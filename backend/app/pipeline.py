from __future__ import annotations

import uuid
import wave
from pathlib import Path
from typing import TYPE_CHECKING

from app import db, storage_backend
from app.config import CLOUD_MODE, STORAGE_DIR

if TYPE_CHECKING:
    # Только для type checker'а — в рантайме vad/live_diarization импортируются
    # лениво внутри LiveCallSession (локальный режим), чтобы не требовать их в
    # CLOUD_MODE (см. _process_call_cloud выше).
    from app.live_diarization import LiveSpeakerClusterer
    from app.vad import SpeechSegment


def _wav_duration_sec(wav_path: str) -> float:
    with wave.open(wav_path, "rb") as f:
        return f.getnframes() / f.getframerate()


def process_call(call_id: str, raw_audio_path: str) -> None:
    if CLOUD_MODE:
        _process_call_cloud(call_id, raw_audio_path)
    else:
        _process_call_local(call_id, raw_audio_path)


def _assign_speaker(segment, turns: list) -> str | None:
    """Спикер с максимальным пересечением по времени с сегментом whisper (локальный режим)."""
    best_speaker = None
    best_overlap = 0.0
    for turn in turns:
        overlap = min(segment.end, turn.end) - max(segment.start, turn.start)
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = turn.speaker
    return best_speaker


def _process_call_local(call_id: str, raw_audio_path: str) -> None:
    # Ленивые импорты: локальный пайплайн не должен требовать faster-whisper/torch/
    # pyannote в облачном режиме (см. _process_call_cloud) — при CLOUD_MODE=true эта
    # функция вообще не вызывается, поэтому импорты сюда безопасны.
    from app.asr import transcribe
    from app.audio_convert import convert_to_wav
    from app.diarization import diarize

    call_dir = STORAGE_DIR / call_id
    wav_path = call_dir / "audio.wav"

    try:
        convert_to_wav(raw_audio_path, str(wav_path))
        duration_sec = _wav_duration_sec(str(wav_path))

        asr_segments = transcribe(str(wav_path))
        speaker_turns = diarize(str(wav_path))

        raw_labels = sorted({t.speaker for t in speaker_turns})
        label_to_id = {label: str(uuid.uuid4()) for label in raw_labels}
        for i, label in enumerate(raw_labels, start=1):
            db.upsert_speaker(label_to_id[label], call_id, f"Спикер {i}")

        for seg in asr_segments:
            if not seg.text:
                continue
            raw_speaker = _assign_speaker(seg, speaker_turns)
            speaker_id = label_to_id.get(raw_speaker) if raw_speaker else None
            db.insert_segment(
                call_id=call_id,
                speaker_id=speaker_id,
                start_ms=int(seg.start * 1000),
                end_ms=int(seg.end * 1000),
                text=seg.text,
            )

        db.mark_call_done(call_id, duration_sec)
    except Exception as e:
        db.mark_call_failed(call_id, str(e))
    finally:
        Path(raw_audio_path).unlink(missing_ok=True)


def process_call_from_storage(call_id: str, storage_raw_ref: str) -> None:
    """Cloud-only: файл уже загружен в Supabase Storage напрямую с браузера по
    signed upload URL (в обход лимита Vercel ~4.5МБ на тело запроса к функции,
    см. docs/tasks.md) — скачиваем во временный файл, обрабатываем как обычно,
    затем убираем сырой объект из Storage (в проде остаётся только audio.wav)."""
    import tempfile

    suffix = Path(storage_raw_ref).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(storage_backend.open_audio_for_read(storage_raw_ref).read())
        raw_path = tmp.name
    try:
        _process_call_cloud(call_id, raw_path)
    finally:
        storage_backend.delete_audio(storage_raw_ref)


def _process_call_cloud(call_id: str, raw_audio_path: str) -> None:
    import tempfile

    from app.audio_convert import convert_to_compressed_mono, convert_to_wav
    from app.groq_transcribe import transcribe_with_speakers

    wav_tmp_path: str | None = None
    compressed_tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_tmp_path = tmp.name
        convert_to_wav(raw_audio_path, wav_tmp_path)
        duration_sec = _wav_duration_sec(wav_tmp_path)

        # Groq режет тело запроса на бесплатном тире ~25МБ — несжатый WAV из
        # convert_to_wav() выше на длинных записях легко превышает лимит, поэтому
        # для самой отправки в Groq используем отдельный сжатый mp3 (см.
        # audio_convert.convert_to_compressed_mono), а WAV остаётся только для
        # хранения в Supabase Storage (проигрывание в браузере).
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            compressed_tmp_path = tmp.name
        convert_to_compressed_mono(raw_audio_path, compressed_tmp_path)

        segments = transcribe_with_speakers(compressed_tmp_path)

        raw_labels = sorted({s.speaker_label for s in segments})
        label_to_id = {label: str(uuid.uuid4()) for label in raw_labels}
        for i, label in enumerate(raw_labels, start=1):
            db.upsert_speaker(label_to_id[label], call_id, f"Спикер {i}")

        for seg in segments:
            db.insert_segment(
                call_id=call_id,
                speaker_id=label_to_id.get(seg.speaker_label),
                start_ms=seg.start_ms,
                end_ms=seg.end_ms,
                text=seg.text,
                is_final=True,
            )

        storage_ref = storage_backend.save_audio(call_id, wav_tmp_path)
        db.save_audio_file(call_id, storage_ref, fmt="wav", size_bytes=Path(wav_tmp_path).stat().st_size)

        db.mark_call_done(call_id, duration_sec)
    except Exception as e:
        db.mark_call_failed(call_id, str(e))
    finally:
        Path(raw_audio_path).unlink(missing_ok=True)
        if wav_tmp_path is not None:
            Path(wav_tmp_path).unlink(missing_ok=True)
        if compressed_tmp_path is not None:
            Path(compressed_tmp_path).unlink(missing_ok=True)


class LiveCallSession:
    """Состояние одного live-звонка (Stage 2): VAD-чанкинг + грубая онлайн-диаризация.

    Черновые метки спикеров ("Спикер N" от live-кластеризации) сохраняются как
    is_final=False — точная замена приходит позже через offline pyannote при
    реконсиляции (Stage 4, ещё не реализована).
    """

    def __init__(self, call_id: str):
        # Ленивые импорты: LiveCallSession создаётся только из /ws/stream/{call_id}
        # в main.py, который сам отказывает в CLOUD_MODE ещё до конструктора — но
        # импорты всё равно держим здесь, а не на верхнем уровне модуля, чтобы сам
        # факт `import app.pipeline` не требовал silero-vad/resemblyzer в облаке.
        from app.live_diarization import LiveSpeakerClusterer
        from app.vad import SpeechChunker

        self.call_id = call_id
        self._chunker = SpeechChunker()
        self._clusterer = LiveSpeakerClusterer()
        self._speaker_ids: dict[int, str] = {}
        self._last_speaker_num: int | None = None
        self.total_ms = 0

        call_dir = STORAGE_DIR / call_id
        call_dir.mkdir(parents=True, exist_ok=True)
        self.wav_path = str(call_dir / "audio.wav")
        self._wav_writer = wave.open(self.wav_path, "wb")
        self._wav_writer.setnchannels(1)
        self._wav_writer.setsampwidth(2)
        self._wav_writer.setframerate(16000)
        self._wav_closed = False

    def _speaker_id_for(self, speaker_num: int) -> str:
        if speaker_num not in self._speaker_ids:
            speaker_id = str(uuid.uuid4())
            self._speaker_ids[speaker_num] = speaker_id
            db.upsert_speaker(speaker_id, self.call_id, f"Спикер {speaker_num}")
        return self._speaker_ids[speaker_num]

    # Клипы короче этого — почти всегда обрывок дыхания/щелчка на границе VAD, а не
    # слово. Whisper на таких галлюцинирует правдоподобный, но случайный текст (см.
    # docs/issues.md), а шумный эмбеддинг такого клипа ещё и может породить лишнего
    # "спикера" в LiveSpeakerClusterer — дешевле отсечь на входе, чем разгребать оба
    # симптома по отдельности.
    MIN_SEGMENT_MS = 300

    def _process_segment(self, seg: SpeechSegment) -> dict | None:
        from app.asr import transcribe_pcm

        self.total_ms = max(self.total_ms, seg.end_ms)
        if seg.end_ms - seg.start_ms < self.MIN_SEGMENT_MS:
            return None
        text = transcribe_pcm(seg.pcm)
        if not text:
            return None
        # continues_previous — сегмент обрублен по max_segment_duration_ms, а не по
        # паузе (см. app/vad.py): это тот же говорящий, что и в прошлом сегменте,
        # переклассификация здесь только внесла бы шум на короткой обрезанной пробе.
        if seg.continues_previous and self._last_speaker_num is not None:
            speaker_num = self._last_speaker_num
        else:
            speaker_num = self._clusterer.assign(seg.pcm)
        self._last_speaker_num = speaker_num
        speaker_id = self._speaker_id_for(speaker_num)
        db.insert_segment(
            call_id=self.call_id,
            speaker_id=speaker_id,
            start_ms=seg.start_ms,
            end_ms=seg.end_ms,
            text=text,
            is_final=False,
        )
        return {
            "type": "segment",
            "speaker": f"Спикер {speaker_num}",
            "start_ms": seg.start_ms,
            "end_ms": seg.end_ms,
            "text": text,
        }

    def feed(self, pcm_bytes: bytes) -> list[dict]:
        self._wav_writer.writeframes(pcm_bytes)
        results = []
        for seg in self._chunker.feed(pcm_bytes):
            msg = self._process_segment(seg)
            if msg:
                results.append(msg)
        return results

    def finalize(self) -> dict | None:
        seg = self._chunker.flush()
        result = self._process_segment(seg) if seg else None
        self._close_wav()
        return result

    def _close_wav(self) -> None:
        if self._wav_closed:
            return
        self._wav_closed = True
        self._wav_writer.close()
        try:
            size_bytes = Path(self.wav_path).stat().st_size
            db.save_audio_file(self.call_id, self.wav_path, fmt="wav", size_bytes=size_bytes)
        except Exception:
            pass

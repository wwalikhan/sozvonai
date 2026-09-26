"""Потоковый VAD-чанкинг (silero-vad) — режет входящий PCM-поток на речевые сегменты
по паузам, не на произвольные тайм-слайсы (см. docs/architecture.md §3.1). CPU — не узкое место."""
from dataclasses import dataclass

import numpy as np
from silero_vad import VADIterator, load_silero_vad

SAMPLE_RATE = 16000
WINDOW_SAMPLES = 512  # фиксированный размер окна, которого требует модель silero-vad при 16kHz
WINDOW_BYTES = WINDOW_SAMPLES * 2  # int16 PCM
PREROLL_WINDOWS = 2  # немного контекста перед началом речи, чтобы не обрезать первый звук

_model = None


def _get_model():
    global _model
    if _model is None:
        _model = load_silero_vad()  # CPU, лёгкая модель
    return _model


@dataclass
class SpeechSegment:
    start_ms: int
    end_ms: int
    pcm: bytes
    continues_previous: bool = False  # обрезан по max_segment_duration_ms, не по паузе —
    # это тот же говорящий, что и в предыдущем сегменте (см. SpeechChunker.feed)


class SpeechChunker:
    """Скармливаешь сырые PCM int16 mono 16kHz байты через feed(), получаешь
    завершённые речевые сегменты (с таймкодами относительно начала потока) по мере готовности."""

    def __init__(
        self,
        threshold: float = 0.5,
        min_silence_duration_ms: int = 500,
        max_segment_duration_ms: int = 5000,
    ):
        self._vad = VADIterator(
            _get_model(),
            threshold=threshold,
            sampling_rate=SAMPLE_RATE,
            min_silence_duration_ms=min_silence_duration_ms,
        )
        self._max_segment_samples = int(max_segment_duration_ms / 1000 * SAMPLE_RATE)
        self._byte_buffer = bytearray()
        self._in_speech = False
        self._segment_chunks: list[bytes] = []
        self._preroll: list[bytes] = []
        self._segment_start_sample = 0
        self._processed_samples = 0
        self._pending_continuation = False

    def feed(self, pcm_bytes: bytes) -> list[SpeechSegment]:
        """Возвращает список завершённых сегментов (обычно 0 или 1 за вызов)."""
        self._byte_buffer.extend(pcm_bytes)
        completed = []

        while len(self._byte_buffer) >= WINDOW_BYTES:
            window_bytes = bytes(self._byte_buffer[:WINDOW_BYTES])
            del self._byte_buffer[:WINDOW_BYTES]

            window_i16 = np.frombuffer(window_bytes, dtype=np.int16)
            window_f32 = window_i16.astype(np.float32) / 32768.0

            event = self._vad(window_f32)
            self._processed_samples += WINDOW_SAMPLES

            if self._in_speech:
                self._segment_chunks.append(window_bytes)
            else:
                self._preroll.append(window_bytes)
                if len(self._preroll) > PREROLL_WINDOWS:
                    self._preroll.pop(0)

            if event and "start" in event and not self._in_speech:
                self._in_speech = True
                self._segment_start_sample = event["start"]
                self._segment_chunks = list(self._preroll) + [window_bytes]

            if event and "end" in event and self._in_speech:
                self._in_speech = False
                completed.append(SpeechSegment(
                    start_ms=int(self._segment_start_sample / SAMPLE_RATE * 1000),
                    end_ms=int(event["end"] / SAMPLE_RATE * 1000),
                    pcm=b"".join(self._segment_chunks),
                    continues_previous=self._pending_continuation,
                ))
                self._segment_chunks = []
                self._preroll = []
                self._pending_continuation = False
            elif self._in_speech and len(self._segment_chunks) * WINDOW_SAMPLES >= self._max_segment_samples:
                # Без принудительной нарезки одна непрерывная реплика без пауз (или
                # быстрая смена говорящих без 500мс тишины между ними) росла бы без
                # ограничений — текст не появлялся бы, пока говорящие вообще не
                # замолчат, и один VAD-сегмент мог бы захватить сразу двух разных
                # спикеров. Режем по максимальной длительности, речь продолжается;
                # continues_previous=True сигналит, что это не новая пауза-граница,
                # а разрыв того же спикера — вызывающий код не должен запускать
                # переклассификацию спикера на этом обрубке (см. app/pipeline.py).
                cut_end_sample = self._processed_samples
                completed.append(SpeechSegment(
                    start_ms=int(self._segment_start_sample / SAMPLE_RATE * 1000),
                    end_ms=int(cut_end_sample / SAMPLE_RATE * 1000),
                    pcm=b"".join(self._segment_chunks),
                    continues_previous=self._pending_continuation,
                ))
                self._segment_start_sample = cut_end_sample
                self._segment_chunks = []
                self._pending_continuation = True

        return completed

    def flush(self) -> SpeechSegment | None:
        """Вызывать при завершении звонка — отдаёт незавершённый хвостовой сегмент, если есть."""
        if self._in_speech and self._segment_chunks:
            pcm = b"".join(self._segment_chunks)
            end_sample = self._segment_start_sample + len(pcm) // 2
            segment = SpeechSegment(
                start_ms=int(self._segment_start_sample / SAMPLE_RATE * 1000),
                end_ms=int(end_sample / SAMPLE_RATE * 1000),
                pcm=pcm,
                continues_previous=self._pending_continuation,
            )
            self._segment_chunks = []
            self._in_speech = False
            return segment
        return None

"""Stage 4: реконсиляция черновой live-диаризации с точной offline-диаризацией.

После завершения live-звонка (Stage 2/3) сегменты транскрипта уже сохранены в БД
с is_final=False и грубыми метками спикеров от LiveSpeakerClusterer. Здесь мы
прогоняем offline pyannote-диаризацию (тот же движок, что и для batch-звонков,
см. app/diarization.py) по полной записи звонка и переносим финальные метки
спикеров на уже существующие сегменты по таймкодам — сам текст не пересчитывается.
"""

import sys
import uuid

from app import db
from app.diarization import SpeakerTurn, diarize


def _assign_speaker_ms(start_ms: int, end_ms: int, turns: list[SpeakerTurn]) -> str | None:
    """Offline-спикер (raw label) с максимальным пересечением по времени с сегментом.

    turns заданы в секундах (SpeakerTurn.start/end), сегмент — в миллисекундах.
    """
    best_speaker = None
    best_overlap = 0.0
    for turn in turns:
        turn_start_ms = turn.start * 1000
        turn_end_ms = turn.end * 1000
        overlap = min(end_ms, turn_end_ms) - max(start_ms, turn_start_ms)
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = turn.speaker
    return best_speaker


def reconcile_live_call(call_id: str, wav_path: str) -> None:
    """Заменяет черновые live-метки спикеров точными offline-метками (pyannote).

    Не бросает исключений наружу: если offline-диаризация не удалась (например,
    HF_TOKEN не задан или запись слишком короткая/пустая для pyannote) — черновые
    метки остаются как есть (is_final=False), это осознанный fallback, не
    критическая ошибка для MVP.
    """
    try:
        speaker_turns = diarize(wav_path)
    except Exception as e:
        print(f"reconciliation failed for {call_id}: {e}", file=sys.stderr)
        return

    old_speakers = db.get_speakers(call_id)
    segments = db.get_segments_raw(call_id)

    raw_labels = sorted({t.speaker for t in speaker_turns})
    label_to_id = {label: str(uuid.uuid4()) for label in raw_labels}
    for i, label in enumerate(raw_labels, start=1):
        db.upsert_speaker(label_to_id[label], call_id, f"Спикер {i}")

    for seg in segments:
        raw_speaker = _assign_speaker_ms(seg["start_ms"], seg["end_ms"], speaker_turns)
        new_speaker_id = label_to_id.get(raw_speaker) if raw_speaker else None
        db.reassign_segment_speaker(seg["id"], new_speaker_id, is_final=True)

    new_speaker_ids = set(label_to_id.values())
    for speaker in old_speakers:
        if speaker["id"] not in new_speaker_ids:
            db.delete_speaker(speaker["id"])

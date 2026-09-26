from dataclasses import dataclass

import numpy as np

from app.gpu import ensure_cuda_dll_path

ensure_cuda_dll_path()

from faster_whisper import WhisperModel

from app.config import (
    ASR_BATCH_BEAM_SIZE,
    ASR_DEVICE,
    ASR_FALLBACK_MODEL_SIZE,
    ASR_LIVE_BEAM_SIZE,
    ASR_MODEL_SIZE,
)

_model: WhisperModel | None = None
_model_device: str | None = None

# Известный артефакт обучающих данных faster-whisper (субтитры с YouTube) — модель
# иногда вместо тишины/шума выдаёт один из этих штампов вместо пустого сегмента.
# Whisper's собственный no_speech_threshold/log_prob_threshold (см. faster_whisper
# defaults) это не всегда ловит: модель "уверена" в этих галлюцинациях (высокий
# avg_logprob), т.к. они заучены, а не угаданы — типичный no_speech-фильтр на такое
# не срабатывает. Список — известные варианты этого конкретного артефакта на
# русском (см. docs/issues.md, запись от 2026-09-25).
_HALLUCINATION_PATTERNS = (
    "редактор субтитров",
    "корректор а.",
    "корректор н.",
    "субтитры сделал",
    "субтитры создал",
    "субтитры добавил",
    "субтитры подготовил",
    "спасибо за просмотр",
    "подписывайтесь на канал",
    "ставьте лайк",
    "приятного просмотра",
    "не забудьте подписаться",
    "продолжение следует",
)


def _is_hallucination(text: str) -> bool:
    normalized = text.lower()
    return any(pattern in normalized for pattern in _HALLUCINATION_PATTERNS)


@dataclass
class AsrSegment:
    start: float
    end: float
    text: str


def _load_model() -> tuple[WhisperModel, str]:
    global _model, _model_device
    if _model is not None:
        return _model, _model_device

    try:
        _model = WhisperModel(ASR_MODEL_SIZE, device=ASR_DEVICE, compute_type="float16" if ASR_DEVICE == "cuda" else "int8")
        _model_device = ASR_DEVICE
    except Exception:
        # откат на CPU с более лёгкой моделью, если GPU недоступен (см. docs/architecture.md §8)
        _model = WhisperModel(ASR_FALLBACK_MODEL_SIZE, device="cpu", compute_type="int8")
        _model_device = "cpu"

    return _model, _model_device


def transcribe(audio_path: str, language: str | None = "ru") -> list[AsrSegment]:
    """language=None включает автоопределение языка Whisper — нужно для видео-инсайтов
    (см. app/video_insights.py), где ролик может быть на любом языке, в отличие от
    деловых звонков (всегда русский, поэтому язык принудительно задан по умолчанию)."""
    model, _ = _load_model()
    segments, _info = model.transcribe(audio_path, language=language, beam_size=ASR_BATCH_BEAM_SIZE)
    return [
        AsrSegment(start=s.start, end=s.end, text=s.text.strip())
        for s in segments
        if not _is_hallucination(s.text)
    ]


def transcribe_pcm(pcm_bytes: bytes, sample_rate: int = 16000) -> str:
    """Транскрибирует один короткий речевой сегмент (из VAD) без чтения с диска — для live-режима.

    Возвращает просто текст (без внутренних таймкодов) — сегмент уже одна реплика,
    таймкоды на уровне звонка берутся из VAD (см. app/vad.py). beam_size ниже, чем
    у batch (см. ASR_LIVE_BEAM_SIZE в config.py) — live вызывается на каждый речевой
    сегмент почти непрерывно на протяжении звонка, что заметно сильнее греет GPU.
    """
    model, _ = _load_model()
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _info = model.transcribe(audio, language="ru", beam_size=ASR_LIVE_BEAM_SIZE)
    texts = [s.text.strip() for s in segments if not _is_hallucination(s.text)]
    return " ".join(texts).strip()

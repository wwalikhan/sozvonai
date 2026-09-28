"""Транскрибация + диаризация через Gemini API — облачная замена faster-whisper+
pyannote.audio (app/asr.py + app/diarization.py) для CLOUD_MODE (см. app/config.py).

Один запрос к Gemini возвращает уже размеченные по спикерам, хронологически
упорядоченные сегменты — в отличие от локального пайплайна, здесь не нужен
отдельный шаг сопоставления ASR-сегментов с окнами диаризации (см.
pipeline._assign_speaker) и не нужна offline-реконсиляция (Stage 4): результат
Gemini сразу финальный.

Известное ограничение: таймкоды сгенерированы моделью, а не через forced
alignment — на длинных записях возможен дрейф. Не решается в этом модуле.
"""

import json
from dataclasses import dataclass

from pydantic import BaseModel

from app.config import GEMINI_API_KEY, GEMINI_MODEL

_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai

        if not GEMINI_API_KEY:
            raise RuntimeError("CLOUD_MODE требует GEMINI_API_KEY")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


@dataclass
class GeminiTranscriptSegment:
    start_ms: int
    end_ms: int
    speaker_label: str
    text: str


class _SegmentSchema(BaseModel):
    speaker: str
    start_sec: float
    end_sec: float
    text: str


class _TranscriptSchema(BaseModel):
    segments: list[_SegmentSchema]


_SPEAKER_PROMPT = """\
Расшифруй эту аудиозапись делового звонка/встречи дословно, реплика за репликой,
и раздели по говорящим (диаризация).

Правила:
- Используй устойчивые метки говорящих "Спикер 1", "Спикер 2" и т.д. — один и тот
  же человек должен иметь одну и ту же метку на протяжении всей записи.
- Сегменты должны идти по порядку, без наложений по времени.
- Расшифровывай дословно, не пересказывай и не суммаризируй.
- start_sec/end_sec — в секундах от начала записи, с точностью до десятых.
- Язык записи: {language_instruction}
"""


def transcribe_with_speakers(audio_path: str, language_hint: str | None = "ru") -> list[GeminiTranscriptSegment]:
    client = _get_client()
    from google.genai import types

    uploaded = client.files.upload(file=audio_path)

    language_instruction = "русский" if language_hint == "ru" else "определи автоматически"
    prompt = _SPEAKER_PROMPT.format(language_instruction=language_instruction)

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[uploaded, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_TranscriptSchema,
        ),
    )

    parsed: _TranscriptSchema = response.parsed or _TranscriptSchema(**json.loads(response.text))
    return [
        GeminiTranscriptSegment(
            start_ms=int(s.start_sec * 1000),
            end_ms=int(s.end_sec * 1000),
            speaker_label=s.speaker,
            text=s.text.strip(),
        )
        for s in parsed.segments
        if s.text.strip()
    ]


_PLAIN_PROMPT = """\
Расшифруй эту аудиозапись дословно, целиком, без разметки по говорящим и без
пересказа. Язык записи: {language_instruction}
"""


def transcribe_plain(audio_path: str, language_hint: str | None = None) -> str:
    """Для video-insights (app/video_insights.py) — без диаризации, только текст."""
    client = _get_client()

    uploaded = client.files.upload(file=audio_path)
    language_instruction = "русский" if language_hint == "ru" else "определи автоматически"
    prompt = _PLAIN_PROMPT.format(language_instruction=language_instruction)

    response = client.models.generate_content(model=GEMINI_MODEL, contents=[uploaded, prompt])
    return (response.text or "").strip()


def summarize_text(system_prompt: str, user_prompt: str) -> str:
    """Для video-insights — суммаризация уже готового транскрипта (замена локальной LLM)."""
    client = _get_client()
    from google.genai import types

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[user_prompt],
        config=types.GenerateContentConfig(system_instruction=system_prompt),
    )
    return (response.text or "").strip()

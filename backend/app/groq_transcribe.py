"""Транскрибация + суммаризация через Groq API — облачная замена faster-whisper+
pyannote.audio (app/asr.py + app/diarization.py) для CLOUD_MODE (см. app/config.py).

Раньше здесь был Gemini (app/gemini_transcribe.py, удалён) — на бесплатном API-ключе
единственная доступная flash-модель (gemini-3.6-flash) оказалась перегружена на
стороне Google (503 "high demand") практически постоянно, а остальные модели либо
сняты с бесплатных ключей (404), либо квота исчерпана (429) — без запасного варианта
внутри самого Gemini. Groq на практике заметно стабильнее на бесплатном тире и даёт
и Whisper (транскрибация), и Llama (суммаризация для video-insights) под одним ключом.

Известное ограничение: Groq Whisper не делает диаризацию (в отличие от Gemini) — все
сегменты в облаке идут под единой меткой "Спикер 1". Восстановить разметку по
спикерам в облаке — отдельная задача на будущее (нужен либо отдельный сервис
диаризации, либо гибридный подход), см. docs/tasks.md.
"""

from dataclasses import dataclass

from app.config import GROQ_API_KEY, GROQ_LLM_MODEL, GROQ_WHISPER_MODEL

_client = None


def _get_client():
    global _client
    if _client is None:
        from groq import Groq

        if not GROQ_API_KEY:
            raise RuntimeError("CLOUD_MODE требует GROQ_API_KEY")
        _client = Groq(api_key=GROQ_API_KEY)
    return _client


@dataclass
class GroqTranscriptSegment:
    start_ms: int
    end_ms: int
    speaker_label: str
    text: str


def _transcribe_verbose(audio_path: str, language_hint: str | None):
    client = _get_client()
    with open(audio_path, "rb") as f:
        return client.audio.transcriptions.create(
            file=(audio_path, f.read()),
            model=GROQ_WHISPER_MODEL,
            response_format="verbose_json",
            language=language_hint or None,
        )


def transcribe_with_speakers(audio_path: str, language_hint: str | None = "ru") -> list[GroqTranscriptSegment]:
    response = _transcribe_verbose(audio_path, language_hint)
    return [
        GroqTranscriptSegment(
            start_ms=int(seg["start"] * 1000),
            end_ms=int(seg["end"] * 1000),
            speaker_label="Спикер 1",
            text=seg["text"].strip(),
        )
        for seg in (response.segments or [])
        if seg["text"].strip()
    ]


def transcribe_plain(audio_path: str, language_hint: str | None = None) -> str:
    """Для video-insights (app/video_insights.py) — без диаризации, только текст."""
    response = _transcribe_verbose(audio_path, language_hint)
    return (response.text or "").strip()


def summarize_text(system_prompt: str, user_prompt: str) -> str:
    """Для video-insights — суммаризация уже готового транскрипта (замена локальной LLM)."""
    client = _get_client()
    response = client.chat.completions.create(
        model=GROQ_LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return (response.choices[0].message.content or "").strip()

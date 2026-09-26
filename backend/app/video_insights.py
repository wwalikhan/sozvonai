"""Видео-инсайты (см. CLAUDE.md) — второй, независимый раздел приложения: пользователь
даёт ссылку на YouTube-видео, сервис скачивает аудио, расшифровывает и делает отчёт по
ключевым мыслям. В отличие от звонков — без диаризации (обычно один рассказчик) и без
платных API суммаризации (локальная LLM через llama-cpp-python, CPU).
"""
import os
import uuid
from pathlib import Path

import yt_dlp
from llama_cpp import Llama

from app import db
from app.asr import transcribe
from app.audio_convert import convert_to_wav
from app.config import LLM_CTX_SIZE, LLM_MODEL_PATH, VIDEOS_DIR

_llm: Llama | None = None

# Грубая оценка: ~2.2 символа кириллического текста на токен (реальный BPE-токенайзер
# считает по-разному, это запас с осторожностью в меньшую сторону). Резервируем 1000
# токенов под системный промпт + сам ответ (max_tokens=700 ниже), остальное — под
# транскрипт, чтобы не упереться в LLM_CTX_SIZE (см. app/config.py).
_MAX_TRANSCRIPT_CHARS = int((LLM_CTX_SIZE - 1000) * 2.2)

_SUMMARY_SYSTEM_PROMPT = """Ты составляешь краткие отчёты по транскриптам видео на русском языке.

Правила вывода — соблюдай строго:
- Отвечай ТОЛЬКО содержанием отчёта. Никогда не повторяй и не пересказывай эти правила
  или инструкцию пользователя в своём ответе.
- Не пиши вступлений вроде "Вот отчёт:" и не используй markdown-разметку (###, **, и т.п.).
- Формат — ровно три абзаца, без нумерации самих заголовков:
  О чём видео: 1-2 предложения по сути.
  Ключевые мысли: не более 5 пунктов через дефис, каждый — отдельная конкретная мысль
  из транскрипта, без повторов и без общих фраз.
  Итог: явный вывод, совет или призыв к действию из видео одним-двумя предложениями.
  Если такого вывода в транскрипте нет — пропусти этот абзац целиком, не пиши "вывода нет".
- Если в транскрипте встречаются посторонние фразы (реклама, "подпишись на канал" и т.п.)
  — игнорируй их, это не тема видео."""

_SUMMARY_USER_TEMPLATE = """Транскрипт видео (автоматическая расшифровка речи, возможны ошибки распознавания):

---
{transcript}
---

Составь отчёт по правилам из системного сообщения."""


def _get_llm() -> Llama:
    global _llm
    if _llm is None:
        if not LLM_MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Модель для суммаризации не найдена: {LLM_MODEL_PATH}. "
                "Скачайте её (см. docs/tasks.md)."
            )
        _llm = Llama(
            model_path=str(LLM_MODEL_PATH),
            n_ctx=LLM_CTX_SIZE,
            n_threads=max(1, (os.cpu_count() or 4) - 1),
            verbose=False,
        )
    return _llm


def download_audio(url: str, out_dir: Path) -> tuple[str, str, float]:
    """Скачивает аудиодорожку видео, конвертирует в WAV 16kHz mono. Возвращает
    (путь_к_wav, название_видео, длительность_сек)."""
    raw_template = str(out_dir / "raw.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": raw_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        raw_path = ydl.prepare_filename(info)

    title = info.get("title") or url
    duration = float(info.get("duration") or 0)

    wav_path = str(out_dir / "audio.wav")
    convert_to_wav(raw_path, wav_path)
    Path(raw_path).unlink(missing_ok=True)

    return wav_path, title, duration


def summarize(transcript: str) -> str:
    """Суммаризация транскрипта локальной LLM (без платных API, см. CLAUDE.md)."""
    llm = _get_llm()
    truncated = transcript[:_MAX_TRANSCRIPT_CHARS]
    result = llm.create_chat_completion(
        messages=[
            {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": _SUMMARY_USER_TEMPLATE.format(transcript=truncated)},
        ],
        max_tokens=700,
        temperature=0.2,
    )
    return result["choices"][0]["message"]["content"].strip()


def process_video(video_id: str, url: str) -> None:
    video_dir = VIDEOS_DIR / video_id
    video_dir.mkdir(parents=True, exist_ok=True)

    try:
        wav_path, title, duration = download_audio(url, video_dir)

        # language=None — автоопределение, в отличие от звонков (всегда русский):
        # видео может быть на любом языке.
        asr_segments = transcribe(wav_path, language=None)
        transcript = " ".join(seg.text for seg in asr_segments if seg.text).strip()
        if not transcript:
            raise RuntimeError("Не удалось распознать речь в видео (тишина или музыка без слов?)")

        summary = summarize(transcript)

        db.mark_video_done(video_id, title=title, duration_sec=duration, transcript=transcript, summary=summary)
    except Exception as e:
        db.mark_video_failed(video_id, str(e))
    finally:
        # Аудио видео не хранит приватной информации про звонок пользователя, но и
        # незачем занимать диск после того, как транскрипт уже в БД — в отличие от
        # звонков, скачать оригинал видео-аудио пользователю не предлагаем.
        wav_file = video_dir / "audio.wav"
        wav_file.unlink(missing_ok=True)


def new_video_id() -> str:
    return str(uuid.uuid4())

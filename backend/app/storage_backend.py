"""Хранение аудиофайлов: локальный диск (dev) или Supabase Storage (CLOUD_MODE).

Тот же принцип, что уже применяется в db.py для SQLite/Postgres — переключение
по флагу, единый интерфейс для вызывающего кода (main.py/pipeline.py/video_insights.py).
Возвращаемая "ссылка на файл" (storage_ref) — локальный путь в dev-режиме,
object key бакета в облаке; вызывающий код не должен интерпретировать её формат.
"""

import shutil
from pathlib import Path
from typing import BinaryIO

from app.config import CLOUD_MODE, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_STORAGE_BUCKET, SUPABASE_URL

_client = None


def _supabase_client():
    global _client
    if _client is None:
        from supabase import create_client

        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            raise RuntimeError("CLOUD_MODE требует SUPABASE_URL и SUPABASE_SERVICE_ROLE_KEY")
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _client


def save_audio(object_prefix: str, local_tmp_path: str, dest_name: str = "audio.wav") -> str:
    """Сохраняет файл из временного пути в постоянное хранилище, возвращает storage_ref."""
    if CLOUD_MODE:
        key = f"{object_prefix}/{dest_name}"
        with open(local_tmp_path, "rb") as f:
            _supabase_client().storage.from_(SUPABASE_STORAGE_BUCKET).upload(
                key, f.read(), {"content-type": "audio/wav", "upsert": "true"}
            )
        return key

    dest_dir = Path(local_tmp_path).parent
    dest_path = dest_dir / dest_name
    if str(Path(local_tmp_path).resolve()) != str(dest_path.resolve()):
        shutil.copyfile(local_tmp_path, dest_path)
    return str(dest_path)


def open_audio_for_read(storage_ref: str) -> BinaryIO:
    import io

    if CLOUD_MODE:
        data = _supabase_client().storage.from_(SUPABASE_STORAGE_BUCKET).download(storage_ref)
        return io.BytesIO(data)
    return open(storage_ref, "rb")


def signed_url_for(storage_ref: str, expires_in: int = 3600) -> str:
    """Только для CLOUD_MODE — временная подписанная ссылка, чтобы не проксировать файл через функцию."""
    result = _supabase_client().storage.from_(SUPABASE_STORAGE_BUCKET).create_signed_url(storage_ref, expires_in)
    return result["signedURL"] if "signedURL" in result else result["signedUrl"]


def delete_audio(storage_ref: str) -> None:
    if CLOUD_MODE:
        _supabase_client().storage.from_(SUPABASE_STORAGE_BUCKET).remove([storage_ref])
        return
    Path(storage_ref).unlink(missing_ok=True)

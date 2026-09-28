import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy import text as sql
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from app.config import DATABASE_URL, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'local',
    title TEXT,
    source TEXT NOT NULL,              -- mic | tab_capture | upload
    status TEXT NOT NULL,              -- processing | done | failed
    error TEXT,
    duration_sec REAL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audio_files (
    call_id TEXT PRIMARY KEY REFERENCES calls(id),
    path TEXT NOT NULL,
    format TEXT NOT NULL,
    size_bytes INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS speakers (
    id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES calls(id),
    display_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcript_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL REFERENCES calls(id),
    speaker_id TEXT REFERENCES speakers(id),
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    text TEXT NOT NULL,
    is_final INTEGER NOT NULL DEFAULT 1
);

-- "Видео-инсайты" — отдельная фича (см. CLAUDE.md), сознательно не переиспользует
-- calls/speakers/transcript_segments: тут нет диаризации (обычно один рассказчик),
-- вместо разметки по спикерам — суммаризация ключевых мыслей локальной LLM.
CREATE TABLE IF NOT EXISTS video_insights (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'local',
    url TEXT NOT NULL,
    title TEXT,
    status TEXT NOT NULL,              -- processing | done | failed
    error TEXT,
    duration_sec REAL,
    transcript TEXT,
    summary TEXT,
    created_at TEXT NOT NULL
);
"""

# Столбцы, добавленные после первого релиза схемы (multi-user/Supabase) — на уже
# существующих локальных sozvonai.db их нет, CREATE TABLE IF NOT EXISTS их не
# добавляет задним числом, поэтому дальше идёт лёгкая ALTER TABLE-миграция.
_LEGACY_COLUMNS = [
    ("calls", "user_id", "TEXT NOT NULL DEFAULT 'local'"),
    ("video_insights", "user_id", "TEXT NOT NULL DEFAULT 'local'"),
]


def _normalize_url(url: str) -> str:
    # Supabase выдаёт connection string как postgres(ql)://... — драйвер psycopg3
    # нужно указать явно, иначе SQLAlchemy пытается найти psycopg2.
    return re.sub(r"^postgres(ql)?://", "postgresql+psycopg://", url, count=1)


def _make_engine() -> Engine:
    if DATABASE_URL:
        return create_engine(_normalize_url(DATABASE_URL), pool_pre_ping=True, pool_size=5, max_overflow=10)

    # NullPool + check_same_thread=False: сохраняет поведение старого кода на
    # sqlite3 (свежее физическое соединение на каждый вызов, никогда не
    # переиспользуется между потоками) — важно, т.к. приложение реально
    # многопоточное (background_tasks, run_in_executor для реконсиляции).
    return create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False}, poolclass=NullPool)


_ENGINE = _make_engine()


@event.listens_for(_ENGINE, "connect")
def _sqlite_foreign_keys(dbapi_conn, _) -> None:
    if _ENGINE.dialect.name == "sqlite":
        dbapi_conn.execute("PRAGMA foreign_keys = ON")


def init_db() -> None:
    if DATABASE_URL:
        # Постгрес-схема живёт только в Supabase-миграциях (apply_migration) —
        # не дублируем её здесь, чтобы не было двух источников истины.
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        for table, column, column_def in _LEGACY_COLUMNS:
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_conn():
    with _ENGINE.begin() as conn:
        yield conn


def create_call(call_id: str, title: str, source: str, user_id: str, status: str = "processing") -> None:
    with get_conn() as conn:
        conn.execute(
            sql(
                "INSERT INTO calls (id, user_id, title, source, status, created_at) "
                "VALUES (:id, :user_id, :title, :source, :status, :created_at)"
            ),
            {
                "id": call_id,
                "user_id": user_id,
                "title": title,
                "source": source,
                "status": status,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )


def save_audio_file(call_id: str, path: str, fmt: str, size_bytes: int) -> None:
    with get_conn() as conn:
        conn.execute(
            sql("INSERT INTO audio_files (call_id, path, format, size_bytes) VALUES (:call_id, :path, :fmt, :size)"),
            {"call_id": call_id, "path": path, "fmt": fmt, "size": size_bytes},
        )


def get_audio_file(call_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            sql("SELECT path, format FROM audio_files WHERE call_id = :call_id"), {"call_id": call_id}
        ).mappings().fetchone()
        return dict(row) if row else None


def mark_call_done(call_id: str, duration_sec: float) -> None:
    with get_conn() as conn:
        conn.execute(
            sql("UPDATE calls SET status = 'done', duration_sec = :duration WHERE id = :id"),
            {"duration": duration_sec, "id": call_id},
        )


def mark_call_failed(call_id: str, error: str) -> None:
    with get_conn() as conn:
        conn.execute(
            sql("UPDATE calls SET status = 'failed', error = :error WHERE id = :id"), {"error": error, "id": call_id}
        )


def upsert_speaker(speaker_id: str, call_id: str, display_name: str) -> None:
    with get_conn() as conn:
        conn.execute(
            sql(
                "INSERT INTO speakers (id, call_id, display_name) VALUES (:id, :call_id, :name) "
                "ON CONFLICT(id) DO NOTHING"
            ),
            {"id": speaker_id, "call_id": call_id, "name": display_name},
        )


def insert_segment(
    call_id: str, speaker_id: str | None, start_ms: int, end_ms: int, text: str, is_final: bool = True
) -> int:
    with get_conn() as conn:
        result = conn.execute(
            sql(
                "INSERT INTO transcript_segments (call_id, speaker_id, start_ms, end_ms, text, is_final) "
                "VALUES (:call_id, :speaker_id, :start_ms, :end_ms, :text, :is_final) RETURNING id"
            ),
            {
                "call_id": call_id,
                "speaker_id": speaker_id,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "text": text,
                "is_final": int(is_final),
            },
        )
        return result.scalar_one()


def mark_call_recording(call_id: str) -> None:
    with get_conn() as conn:
        conn.execute(sql("UPDATE calls SET status = 'recording' WHERE id = :id"), {"id": call_id})


def get_call(call_id: str, user_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            sql("SELECT * FROM calls WHERE id = :id AND user_id = :user_id"), {"id": call_id, "user_id": user_id}
        ).mappings().fetchone()
        return dict(row) if row else None


def list_calls(user_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            sql("SELECT * FROM calls WHERE user_id = :user_id ORDER BY created_at DESC"), {"user_id": user_id}
        ).mappings().fetchall()
        return [dict(r) for r in rows]


def get_segments(call_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            sql(
                "SELECT ts.id, ts.speaker_id, ts.is_final, ts.start_ms, ts.end_ms, ts.text, "
                "s.display_name AS speaker "
                "FROM transcript_segments ts "
                "LEFT JOIN speakers s ON s.id = ts.speaker_id "
                "WHERE ts.call_id = :call_id ORDER BY ts.start_ms"
            ),
            {"call_id": call_id},
        ).mappings().fetchall()
        return [dict(r) for r in rows]


def get_segments_raw(call_id: str) -> list[dict]:
    """Все сегменты звонка без джойна на speakers — для сопоставления по времени
    с offline-диаризацией при реконсиляции (Stage 4)."""
    with get_conn() as conn:
        rows = conn.execute(
            sql(
                "SELECT id, speaker_id, start_ms, end_ms FROM transcript_segments "
                "WHERE call_id = :call_id ORDER BY start_ms"
            ),
            {"call_id": call_id},
        ).mappings().fetchall()
        return [dict(r) for r in rows]


def get_speakers(call_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            sql("SELECT id, call_id, display_name FROM speakers WHERE call_id = :call_id"), {"call_id": call_id}
        ).mappings().fetchall()
        return [dict(r) for r in rows]


def reassign_segment_speaker(segment_id: int, speaker_id: str | None, is_final: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            sql("UPDATE transcript_segments SET speaker_id = :speaker_id, is_final = :is_final WHERE id = :id"),
            {"speaker_id": speaker_id, "is_final": int(is_final), "id": segment_id},
        )


def delete_speaker(speaker_id: str) -> None:
    with get_conn() as conn:
        conn.execute(sql("DELETE FROM speakers WHERE id = :id"), {"id": speaker_id})


def rename_speaker(speaker_id: str, display_name: str) -> None:
    with get_conn() as conn:
        conn.execute(
            sql("UPDATE speakers SET display_name = :name WHERE id = :id"), {"name": display_name, "id": speaker_id}
        )


def delete_call(call_id: str, user_id: str) -> None:
    with get_conn() as conn:
        owned = conn.execute(
            sql("SELECT 1 FROM calls WHERE id = :id AND user_id = :user_id"), {"id": call_id, "user_id": user_id}
        ).fetchone()
        if owned is None:
            return
        conn.execute(sql("DELETE FROM transcript_segments WHERE call_id = :id"), {"id": call_id})
        conn.execute(sql("DELETE FROM speakers WHERE call_id = :id"), {"id": call_id})
        conn.execute(sql("DELETE FROM audio_files WHERE call_id = :id"), {"id": call_id})
        conn.execute(sql("DELETE FROM calls WHERE id = :id"), {"id": call_id})


def create_video(video_id: str, url: str, user_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            sql(
                "INSERT INTO video_insights (id, user_id, url, status, created_at) "
                "VALUES (:id, :user_id, :url, 'processing', :created_at)"
            ),
            {"id": video_id, "user_id": user_id, "url": url, "created_at": datetime.now(timezone.utc).isoformat()},
        )


def mark_video_done(video_id: str, title: str, duration_sec: float, transcript: str, summary: str) -> None:
    with get_conn() as conn:
        conn.execute(
            sql(
                "UPDATE video_insights SET status = 'done', title = :title, duration_sec = :duration, "
                "transcript = :transcript, summary = :summary WHERE id = :id"
            ),
            {"title": title, "duration": duration_sec, "transcript": transcript, "summary": summary, "id": video_id},
        )


def mark_video_failed(video_id: str, error: str) -> None:
    with get_conn() as conn:
        conn.execute(
            sql("UPDATE video_insights SET status = 'failed', error = :error WHERE id = :id"),
            {"error": error, "id": video_id},
        )


def get_video(video_id: str, user_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            sql("SELECT * FROM video_insights WHERE id = :id AND user_id = :user_id"),
            {"id": video_id, "user_id": user_id},
        ).mappings().fetchone()
        return dict(row) if row else None


def list_videos(user_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            sql("SELECT * FROM video_insights WHERE user_id = :user_id ORDER BY created_at DESC"),
            {"user_id": user_id},
        ).mappings().fetchall()
        return [dict(r) for r in rows]

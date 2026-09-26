import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id TEXT PRIMARY KEY,
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


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def create_call(call_id: str, title: str, source: str, status: str = "processing") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO calls (id, title, source, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (call_id, title, source, status, datetime.now(timezone.utc).isoformat()),
        )


def save_audio_file(call_id: str, path: str, fmt: str, size_bytes: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO audio_files (call_id, path, format, size_bytes) VALUES (?, ?, ?, ?)",
            (call_id, path, fmt, size_bytes),
        )


def get_audio_file(call_id: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT path, format FROM audio_files WHERE call_id = ?", (call_id,)
        ).fetchone()


def mark_call_done(call_id: str, duration_sec: float) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE calls SET status = 'done', duration_sec = ? WHERE id = ?",
            (duration_sec, call_id),
        )


def mark_call_failed(call_id: str, error: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE calls SET status = 'failed', error = ? WHERE id = ?",
            (error, call_id),
        )


def upsert_speaker(speaker_id: str, call_id: str, display_name: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO speakers (id, call_id, display_name) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO NOTHING",
            (speaker_id, call_id, display_name),
        )


def insert_segment(
    call_id: str, speaker_id: str | None, start_ms: int, end_ms: int, text: str, is_final: bool = True
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO transcript_segments (call_id, speaker_id, start_ms, end_ms, text, is_final) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (call_id, speaker_id, start_ms, end_ms, text, int(is_final)),
        )
        return cur.lastrowid


def mark_call_recording(call_id: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE calls SET status = 'recording' WHERE id = ?", (call_id,))


def get_call(call_id: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()


def list_calls() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM calls ORDER BY created_at DESC").fetchall()


def get_segments(call_id: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT ts.id, ts.speaker_id, ts.is_final, ts.start_ms, ts.end_ms, ts.text, "
            "s.display_name AS speaker "
            "FROM transcript_segments ts "
            "LEFT JOIN speakers s ON s.id = ts.speaker_id "
            "WHERE ts.call_id = ? ORDER BY ts.start_ms",
            (call_id,),
        ).fetchall()


def get_segments_raw(call_id: str) -> list[sqlite3.Row]:
    """Все сегменты звонка без джойна на speakers — для сопоставления по времени
    с offline-диаризацией при реконсиляции (Stage 4)."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, speaker_id, start_ms, end_ms "
            "FROM transcript_segments WHERE call_id = ? ORDER BY start_ms",
            (call_id,),
        ).fetchall()


def get_speakers(call_id: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, call_id, display_name FROM speakers WHERE call_id = ?",
            (call_id,),
        ).fetchall()


def reassign_segment_speaker(segment_id: int, speaker_id: str | None, is_final: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE transcript_segments SET speaker_id = ?, is_final = ? WHERE id = ?",
            (speaker_id, int(is_final), segment_id),
        )


def delete_speaker(speaker_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM speakers WHERE id = ?", (speaker_id,))


def rename_speaker(speaker_id: str, display_name: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE speakers SET display_name = ? WHERE id = ?",
            (display_name, speaker_id),
        )


def delete_call(call_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM transcript_segments WHERE call_id = ?", (call_id,))
        conn.execute("DELETE FROM speakers WHERE call_id = ?", (call_id,))
        conn.execute("DELETE FROM audio_files WHERE call_id = ?", (call_id,))
        conn.execute("DELETE FROM calls WHERE id = ?", (call_id,))


def create_video(video_id: str, url: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO video_insights (id, url, status, created_at) VALUES (?, ?, 'processing', ?)",
            (video_id, url, datetime.now(timezone.utc).isoformat()),
        )


def mark_video_done(video_id: str, title: str, duration_sec: float, transcript: str, summary: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE video_insights SET status = 'done', title = ?, duration_sec = ?, "
            "transcript = ?, summary = ? WHERE id = ?",
            (title, duration_sec, transcript, summary, video_id),
        )


def mark_video_failed(video_id: str, error: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE video_insights SET status = 'failed', error = ? WHERE id = ?",
            (error, video_id),
        )


def get_video(video_id: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM video_insights WHERE id = ?", (video_id,)).fetchone()


def list_videos() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM video_insights ORDER BY created_at DESC").fetchall()

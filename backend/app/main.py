import asyncio
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import db
from app.config import BACKEND_ROOT, STORAGE_DIR
from app.pipeline import LiveCallSession, process_call
from app.reconciliation import reconcile_live_call
from app.video_insights import new_video_id, process_video

app = FastAPI(title="SozvonAI")
db.init_db()
app.mount("/static", StaticFiles(directory=str(BACKEND_ROOT / "static")), name="static")


@app.get("/")
def index() -> RedirectResponse:
    return RedirectResponse(url="/static/calls.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


class UploadResponse(BaseModel):
    call_id: str
    status: str


@app.post("/calls/upload", response_model=UploadResponse)
async def upload_call(file: UploadFile, background_tasks: BackgroundTasks) -> UploadResponse:
    call_id = str(uuid.uuid4())
    call_dir = STORAGE_DIR / call_id
    call_dir.mkdir(parents=True, exist_ok=True)

    raw_path = call_dir / f"raw{Path(file.filename or 'upload').suffix}"
    with open(raw_path, "wb") as f:
        f.write(await file.read())

    db.create_call(call_id, title=file.filename or call_id, source="upload")
    db.save_audio_file(call_id, str(call_dir / "audio.wav"), fmt="wav", size_bytes=raw_path.stat().st_size)

    background_tasks.add_task(process_call, call_id, str(raw_path))

    return UploadResponse(call_id=call_id, status="processing")


@app.websocket("/ws/stream/{call_id}")
async def stream_call(websocket: WebSocket, call_id: str) -> None:
    """Live-транскрибация с микрофона (Stage 2). Клиент шлёт бинарные фреймы —
    сырой PCM int16 mono 16kHz. Сервер отвечает JSON-сообщениями по мере готовности
    речевых сегментов: {"type": "segment", "speaker", "start_ms", "end_ms", "text"}.
    """
    await websocket.accept()

    source = websocket.query_params.get("source", "mic")
    if source not in ("mic", "tab_capture"):
        source = "mic"

    if db.get_call(call_id) is None:
        db.create_call(call_id, title=call_id, source=source, status="recording")

    session = LiveCallSession(call_id)

    try:
        while True:
            chunk = await websocket.receive_bytes()
            for message in session.feed(chunk):
                await websocket.send_json(message)
    except WebSocketDisconnect:
        pass
    finally:
        tail = session.finalize()
        if tail:
            try:
                await websocket.send_json(tail)
            except Exception:
                pass
        db.mark_call_done(call_id, session.total_ms / 1000)

        # Точная offline-диаризация (pyannote) — синхронная и потенциально долгая
        # операция (Stage 4). Запускаем в отдельном потоке через executor и не
        # дожидаемся результата, чтобы не блокировать закрытие WS-соединения —
        # аналог background_tasks.add_task в HTTP-роуте upload_call, но WS-эндпоинты
        # не принимают BackgroundTasks тем же способом.
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, reconcile_live_call, call_id, session.wav_path)


class SegmentOut(BaseModel):
    id: int
    start_ms: int
    end_ms: int
    text: str
    speaker_id: str | None
    speaker: str | None
    is_final: bool


class SpeakerOut(BaseModel):
    id: str
    display_name: str


class CallOut(BaseModel):
    id: str
    title: str | None
    source: str
    status: str
    error: str | None
    duration_sec: float | None
    created_at: str
    speakers: list[SpeakerOut] = []
    segments: list[SegmentOut] = []


class StoragePathOut(BaseModel):
    path: str


@app.get("/calls/storage-path", response_model=StoragePathOut)
def get_storage_path() -> StoragePathOut:
    return StoragePathOut(path=str(STORAGE_DIR))


@app.post("/calls/storage-path/open")
def open_storage_folder() -> dict[str, str]:
    # Локальное десктоп-приложение (см. CLAUDE.md — деплой пока не выбран, сейчас
    # только localhost на Windows) — открыть папку в проводнике безопасно.
    os.startfile(str(STORAGE_DIR))  # type: ignore[attr-defined]
    return {"status": "ok"}


@app.get("/calls", response_model=list[CallOut])
def list_calls() -> list[CallOut]:
    # Пользователь может удалить папку записи вручную через проводник, минуя
    # DELETE /calls/{id} — тогда строка в базе "осиротевает" (папки уже нет).
    # Подчищаем такие записи прямо здесь, при каждом обновлении списка, чтобы
    # сайт не расходился с диском. Активные звонки (recording/processing) не
    # трогаем — для них папка ещё может не успеть появиться.
    rows = []
    for row in db.list_calls():
        if row["status"] in ("done", "failed") and not (STORAGE_DIR / row["id"]).exists():
            db.delete_call(row["id"])
            continue
        rows.append(row)
    return [CallOut(**dict(row), speakers=[], segments=[]) for row in rows]


@app.get("/calls/{call_id}", response_model=CallOut)
def get_call(call_id: str) -> CallOut:
    row = db.get_call(call_id)
    if row is None:
        raise HTTPException(status_code=404, detail="call not found")

    speakers = [SpeakerOut(**dict(s)) for s in db.get_speakers(call_id)]
    segments = [
        SegmentOut(**{**dict(s), "is_final": bool(s["is_final"])}) for s in db.get_segments(call_id)
    ]
    return CallOut(**dict(row), speakers=speakers, segments=segments)


@app.delete("/calls/{call_id}")
def delete_call(call_id: str) -> dict[str, str]:
    if db.get_call(call_id) is None:
        raise HTTPException(status_code=404, detail="call not found")

    db.delete_call(call_id)

    call_dir = STORAGE_DIR / call_id
    if call_dir.exists():
        shutil.rmtree(call_dir, ignore_errors=True)

    return {"status": "deleted"}


class RenameSpeakerRequest(BaseModel):
    display_name: str


@app.patch("/calls/{call_id}/speakers/{speaker_id}", response_model=SpeakerOut)
def rename_speaker(call_id: str, speaker_id: str, body: RenameSpeakerRequest) -> SpeakerOut:
    speakers = db.get_speakers(call_id)
    if not any(s["id"] == speaker_id for s in speakers):
        raise HTTPException(status_code=404, detail="speaker not found")

    db.rename_speaker(speaker_id, body.display_name)
    return SpeakerOut(id=speaker_id, display_name=body.display_name)


def _format_timecode(ms: int) -> str:
    total_sec = ms // 1000
    return f"{total_sec // 60}:{total_sec % 60:02d}"


def _build_transcript_text(call_id: str) -> str:
    lines = []
    for seg in db.get_segments(call_id):
        speaker = seg["speaker"] or "—"
        lines.append(f"[{_format_timecode(seg['start_ms'])}] {speaker}: {seg['text']}")
    return "\n".join(lines)


def _safe_filename(name: str) -> str:
    return re.sub(r"[^\w\-.]", "_", name)


# Пути системных TTF-шрифтов с поддержкой кириллицы — перебираем по очереди,
# встроенные PDF-шрифты (Helvetica/Arial) кириллицу не поддерживают. Сейчас
# разработка только на Windows (см. CLAUDE.md — платформа деплоя не выбрана),
# но список включает и типичные пути Linux-дистрибутивов на будущее — там, где
# отсутствуют системные Windows-шрифты, обычно есть DejaVu Sans (Debian/Ubuntu)
# либо Liberation Sans (RHEL/Fedora), у обоих полное покрытие кириллицы.
_CYRILLIC_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\calibri.ttf",
    r"C:\Windows\Fonts\tahoma.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
]


def _build_pdf(call_id: str, title: str) -> bytes:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    font_path = next((p for p in _CYRILLIC_FONT_CANDIDATES if Path(p).exists()), None)
    if font_path is None:
        raise HTTPException(
            status_code=500,
            detail="Не найден системный TTF-шрифт с поддержкой кириллицы для генерации PDF",
        )

    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("Body", fname=font_path)
    pdf.set_font("Body", size=14)
    # new_x=LMARGIN/new_y=NEXT — иначе fpdf2 по умолчанию оставляет курсор у правого
    # края после multi_cell(w=0, ...), и следующий вызов падает с "not enough
    # horizontal space" (курсор уже у правого поля страницы).
    pdf.multi_cell(0, 10, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    pdf.set_font("Body", size=11)
    for seg in db.get_segments(call_id):
        speaker = seg["speaker"] or "—"
        line = f"[{_format_timecode(seg['start_ms'])}] {speaker}: {seg['text']}"
        pdf.multi_cell(0, 7, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    return bytes(pdf.output())


@app.get("/calls/{call_id}/audio")
def download_audio(call_id: str) -> FileResponse:
    row = db.get_call(call_id)
    if row is None:
        raise HTTPException(status_code=404, detail="call not found")

    audio_row = db.get_audio_file(call_id)
    if audio_row is None or not Path(audio_row["path"]).exists():
        raise HTTPException(status_code=404, detail="audio file not found")

    filename = _safe_filename(row["title"] or call_id) + f".{audio_row['format']}"
    return FileResponse(audio_row["path"], media_type="audio/wav", filename=filename)


@app.get("/calls/{call_id}/export")
def export_call(call_id: str, format: Literal["txt", "pdf"] = "txt") -> Response:
    row = db.get_call(call_id)
    if row is None:
        raise HTTPException(status_code=404, detail="call not found")

    title = row["title"] or call_id
    filename = _safe_filename(title)

    if format == "txt":
        text = _build_transcript_text(call_id)
        return Response(
            content=text,
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{filename}.txt"'},
        )

    pdf_bytes = _build_pdf(call_id, title)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
    )


# --- Видео-инсайты (см. CLAUDE.md) — отдельный, не связанный со звонками раздел ---

class VideoSubmitRequest(BaseModel):
    url: str


class VideoSubmitResponse(BaseModel):
    id: str
    status: str


class VideoOut(BaseModel):
    id: str
    url: str
    title: str | None
    status: str
    error: str | None
    duration_sec: float | None
    created_at: str
    transcript: str | None = None
    summary: str | None = None


@app.post("/videos", response_model=VideoSubmitResponse)
def submit_video(body: VideoSubmitRequest, background_tasks: BackgroundTasks) -> VideoSubmitResponse:
    url = body.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    video_id = new_video_id()
    db.create_video(video_id, url)
    background_tasks.add_task(process_video, video_id, url)

    return VideoSubmitResponse(id=video_id, status="processing")


@app.get("/videos", response_model=list[VideoOut])
def list_videos() -> list[VideoOut]:
    return [VideoOut(**dict(row)) for row in db.list_videos()]


@app.get("/videos/{video_id}", response_model=VideoOut)
def get_video(video_id: str) -> VideoOut:
    row = db.get_video(video_id)
    if row is None:
        raise HTTPException(status_code=404, detail="video not found")
    return VideoOut(**dict(row))

import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_ROOT / ".env")

STORAGE_DIR = BACKEND_ROOT / "storage" / "calls"
VIDEOS_DIR = BACKEND_ROOT / "storage" / "videos"
DB_PATH = BACKEND_ROOT / "storage" / "sozvonai.db"

HF_TOKEN = os.environ.get("HF_TOKEN")

# Прод-БД (Supabase Postgres) + multi-user auth. Пусто → локальная разработка на
# SQLite без сети и без логина (см. docs/architecture.md). DATABASE_URL — connection
# string из Supabase (Session pooler, порт 5432 — подходит для долгоживущего
# процесса, в отличие от Transaction pooler на 6543, рассчитанного на serverless).
DATABASE_URL = os.environ.get("DATABASE_URL")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
# Publishable/anon-ключ — публичный по дизайну (Supabase Settings → API), безопасно
# отдавать фронтенду через /config для инициализации supabase-js.
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY")
AUTH_ENABLED = bool(DATABASE_URL)

# Облачный режим (деплой на Vercel) — отдельный флаг от AUTH_ENABLED: тот отвечает
# за "какая БД", этот — за "какой ASR-движок". На практике на проде оба всегда
# включены вместе, но смешивать их в один флаг было бы обманчиво (см.
# docs/architecture.md). В облаке нет GPU и достаточно памяти для faster-whisper/
# pyannote — вместо них используется Gemini API (app/gemini_transcribe.py), а
# live-звонки (WebSocket) недоступны — см. CLAUDE.md.
CLOUD_MODE = os.environ.get("CLOUD_MODE", "").lower() in ("1", "true", "yes")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
SUPABASE_STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "call-audio")
# Service-role ключ — только для сервера (никогда не отдаётся фронтенду, в отличие
# от SUPABASE_ANON_KEY). Нужен, чтобы бэкенд писал/читал Storage от имени любого
# пользователя: как и с DATABASE_URL (прямое подключение к Postgres в обход RLS),
# владение проверяется в коде бэкенда (app/auth.py + user_id в запросах), а не
# политиками Storage RLS.
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

# device: "cuda" по умолчанию (на этой машине есть GTX 1650), откат на "cpu"
# конфигурируется через .env при необходимости (см. docs/architecture.md §8)
ASR_DEVICE = os.environ.get("ASR_DEVICE", "cuda")
ASR_MODEL_SIZE = os.environ.get("ASR_MODEL_SIZE", "small")
ASR_FALLBACK_MODEL_SIZE = os.environ.get("ASR_FALLBACK_MODEL_SIZE", "base")
DIARIZATION_DEVICE = os.environ.get("DIARIZATION_DEVICE", "cuda")

# beam_size управляет тем, сколько гипотез перебирает Whisper на каждом шаге
# декодирования — прямо пропорционален GPU-нагрузке. Для batch (один прогон на
# весь файл) держим точность повыше; для live (десятки коротких сегментов за
# звонок, GPU нагружается почти непрерывно и заметно греется на 4GB картах вроде
# GTX 1650) по умолчанию используем greedy-декодирование (beam_size=1) — заметно
# легче для GPU, ценой небольшой потери точности на отдельных репликах.
ASR_BATCH_BEAM_SIZE = int(os.environ.get("ASR_BATCH_BEAM_SIZE", "5"))
ASR_LIVE_BEAM_SIZE = int(os.environ.get("ASR_LIVE_BEAM_SIZE", "1"))

# Видео-инсайты (см. CLAUDE.md) — суммаризация локальной LLM, без платных API.
# Модель — статический GGUF-файл (не в git, качается один раз), CPU-инференс через
# llama-cpp-python: это фоновая batch-задача (не real-time), GPU уже занят
# ASR/диаризацией во время звонков, так что делить с ними VRAM (4GB) смысла нет.
LLM_MODEL_PATH = BACKEND_ROOT / ".tools" / "models" / "qwen2.5-3b-instruct-q4_k_m.gguf"
LLM_CTX_SIZE = int(os.environ.get("LLM_CTX_SIZE", "4096"))


def _find_ffmpeg() -> str | None:
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path

    if sys.platform == "win32":
        winget_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        if winget_root.exists():
            for candidate in winget_root.glob("Gyan.FFmpeg_*/ffmpeg-*-full_build/bin/ffmpeg.exe"):
                return str(candidate)

    if CLOUD_MODE:
        # На Vercel системного ffmpeg нет — используем статический бинарник из
        # чистого Python-пакета (imageio-ffmpeg), без winget/системных пакетов.
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()

    return None


# Может быть None локально, если ffmpeg не найден — audio_convert.py сам решает,
# критично ли это на момент вызова (раньше здесь падал FileNotFoundError уже при
# импорте, что на Linux/Vercel происходило ещё раньше — на os.environ["LOCALAPPDATA"]).
FFMPEG_PATH = _find_ffmpeg()

if not CLOUD_MODE:
    # У Vercel файловая система read-only (кроме /tmp) — в облачном режиме эти
    # директории не нужны и не создаются.
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

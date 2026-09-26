import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_ROOT / ".env")

STORAGE_DIR = BACKEND_ROOT / "storage" / "calls"
VIDEOS_DIR = BACKEND_ROOT / "storage" / "videos"
DB_PATH = BACKEND_ROOT / "storage" / "sozvonai.db"

HF_TOKEN = os.environ.get("HF_TOKEN")

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


def _find_ffmpeg() -> str:
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path

    winget_root = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Packages"
    if winget_root.exists():
        for candidate in winget_root.glob("Gyan.FFmpeg_*/ffmpeg-*-full_build/bin/ffmpeg.exe"):
            return str(candidate)

    raise FileNotFoundError(
        "ffmpeg.exe не найден ни в PATH, ни в стандартном месте установки winget. "
        "Установите: winget install Gyan.FFmpeg"
    )


FFMPEG_PATH = _find_ffmpeg()

STORAGE_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

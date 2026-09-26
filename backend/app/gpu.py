"""Настройка путей к нативным библиотекам (CUDA, ffmpeg-shared) на Windows.

pip-пакеты nvidia-cublas-cu12/nvidia-cudnn-cu12 кладут DLL внутрь venv,
но Windows не ищет их там сама — нужно явно добавить директории в PATH
до первого импорта faster_whisper/ctranslate2.

torchcodec (используется внутри pyannote.audio 4.x) требует "full-shared"
сборку ffmpeg с отдельными DLL (avcodec/avformat/...) — обычный
статический ffmpeg.exe для этого не подходит, поэтому shared-сборка
лежит отдельно в backend/.tools/ffmpeg-*-shared/bin.
"""
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def ensure_cuda_dll_path() -> None:
    if sys.platform != "win32":
        return

    nvidia_root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    dirs = [str(p) for p in nvidia_root.glob("*/bin")] if nvidia_root.exists() else []

    tools_root = BACKEND_ROOT / ".tools"
    dirs += [str(p) for p in tools_root.glob("ffmpeg-*-shared/bin")]

    if not dirs:
        return

    os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")

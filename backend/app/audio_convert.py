import subprocess

from app.config import FFMPEG_PATH


def convert_to_wav(input_path: str, output_path: str) -> None:
    """Конвертирует любой аудио/видео файл в WAV 16kHz mono — формат под faster-whisper/pyannote."""
    if FFMPEG_PATH is None:
        raise FileNotFoundError(
            "ffmpeg не найден ни в PATH, ни в стандартном месте установки winget. "
            "Установите: winget install Gyan.FFmpeg"
        )
    result = subprocess.run(
        [
            FFMPEG_PATH, "-y",
            "-i", input_path,
            "-ar", "16000",
            "-ac", "1",
            "-c:a", "pcm_s16le",
            output_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {result.stderr[-2000:]}")

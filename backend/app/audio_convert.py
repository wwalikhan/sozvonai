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


def convert_to_compressed_mono(input_path: str, output_path: str, bitrate: str = "32k") -> None:
    """Конвертирует в сжатый mp3 16kHz mono — для отправки в Groq (лимит 25МБ на
    бесплатном тире): несжатый WAV из convert_to_wav() для длинных записей легко
    превышает лимит (16kHz/mono/16bit PCM ~= 2МБ/мин), а 32kbps mp3 достаточно для
    распознавания речи и держит файл в разы меньше даже на записях от часа."""
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
            "-c:a", "libmp3lame",
            "-b:a", bitrate,
            output_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg compression failed: {result.stderr[-2000:]}")

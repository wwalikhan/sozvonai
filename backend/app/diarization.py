from dataclasses import dataclass

from app.gpu import ensure_cuda_dll_path

ensure_cuda_dll_path()

import torch
from pyannote.audio import Pipeline

from app.config import DIARIZATION_DEVICE, HF_TOKEN

_pipeline: Pipeline | None = None


@dataclass
class SpeakerTurn:
    start: float
    end: float
    speaker: str  # raw label, e.g. "SPEAKER_00"


def _load_pipeline() -> Pipeline:
    global _pipeline
    if _pipeline is not None:
        return _pipeline

    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN не задан в backend/.env — нужен для загрузки pyannote моделей")

    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", token=HF_TOKEN)
    try:
        pipeline.to(torch.device(DIARIZATION_DEVICE))
    except Exception:
        pipeline.to(torch.device("cpu"))

    _pipeline = pipeline
    return _pipeline


def diarize(audio_path: str) -> list[SpeakerTurn]:
    pipeline = _load_pipeline()
    output = pipeline(audio_path)
    turns = []
    for turn, _, speaker in output.speaker_diarization.itertracks(yield_label=True):
        turns.append(SpeakerTurn(start=turn.start, end=turn.end, speaker=speaker))
    return turns

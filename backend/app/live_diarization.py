"""Грубая live-диаризация: голосовой эмбеддинг на каждом VAD-сегменте + онлайн-кластеризация
против уже встреченных в этом звонке спикеров (см. docs/architecture.md §3.3).

Точность заведомо ограничена — это осознанный компромисс для live-UX. Точная разметка
приходит позже, офлайн, через pyannote (app/diarization.py) при реконсиляции (Stage 4).
"""
import numpy as np
from resemblyzer import VoiceEncoder

_encoder: VoiceEncoder | None = None


def _get_encoder() -> VoiceEncoder:
    global _encoder
    if _encoder is None:
        _encoder = VoiceEncoder("cpu")  # лёгкая модель, CPU не узкое место
    return _encoder


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


class LiveSpeakerClusterer:
    """Один экземпляр на звонок. Хранит эмбеддинги-центроиды уже встреченных спикеров.

    Два порога вместо одного (см. docs/issues.md — ложный "третий спикер" на живых
    звонках): короткие/эмоционально окрашенные реплики дают более шумный эмбеддинг,
    и его сходство с правильным центроидом иногда падает ниже уверенного match_threshold,
    не будучи при этом похожим на нового человека. Новый спикер создаётся только при
    действительно низком сходстве (new_speaker_threshold) — в средней зоне сегмент
    уходит к ближайшему уже известному спикеру.

    Обновление центроида взвешено по длительности сегмента (в секундах), а не считает
    каждый сегмент "за одно": первая реплика говорящего часто короткая и шумная
    (например "Привет, как дела?" на 1-2с) — если давать ей такой же вес, как
    следующим длинным репликам, центроид навсегда застревает на этом шумном
    приближении и переиграть его позже уже нечем (сходство с ним у последующих,
    честных реплик того же голоса будет вечно чуть ниже порога). Длинная реплика —
    более надёжный эмбеддинг, и вправе сильнее сдвигать центроид к себе.
    """

    def __init__(self, match_threshold: float = 0.75, new_speaker_threshold: float = 0.6):
        self.match_threshold = match_threshold
        self.new_speaker_threshold = new_speaker_threshold
        self._centroids: list[np.ndarray] = []  # индекс = "Спикер N" (1-based)
        self._weights: list[float] = []  # суммарная длительность (сек), давшая вклад в центроид

    def assign(self, pcm_bytes: bytes, sample_rate: int = 16000) -> int:
        """Возвращает номер спикера (1-based) для сегмента, обновляя центроиды."""
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        embedding = _get_encoder().embed_utterance(audio)
        duration_sec = len(audio) / sample_rate

        best_idx, best_sim = -1, -1.0
        for i, centroid in enumerate(self._centroids):
            sim = _cosine_sim(embedding, centroid)
            if sim > best_sim:
                best_idx, best_sim = i, sim

        if best_idx >= 0 and best_sim >= self.new_speaker_threshold:
            # относим к ближайшему уже известному голосу (уверенно — match_threshold,
            # либо неуверенно, но явно ближе к нему, чем к новому) и в обоих случаях
            # сдвигаем центроид, взвешивая на длительность реплики
            w = self._weights[best_idx]
            self._centroids[best_idx] = (self._centroids[best_idx] * w + embedding * duration_sec) / (w + duration_sec)
            self._weights[best_idx] += duration_sec
            return best_idx + 1

        self._centroids.append(embedding)
        self._weights.append(duration_sec)
        return len(self._centroids)

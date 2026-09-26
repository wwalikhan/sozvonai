# Инструменты и ресурсы

| Инструмент | Ссылка | Для чего |
|---|---|---|
| FastAPI | https://fastapi.tiangolo.com/ | Backend API сервиса, WebSocket для live-стриминга |
| faster-whisper | https://github.com/SYSTRAN/faster-whisper | Локальная транскрибация звонков без платных API (GPU/CUDA основной путь, CPU int8 — fallback) |
| silero-vad | https://github.com/snakers4/silero-vad | Детекция речи, чанкинг живого аудиопотока |
| pyannote.audio | https://github.com/pyannote/pyannote-audio | Точная офлайн-диаризация спикеров после звонка |
| speechbrain / resemblyzer | https://speechbrain.github.io/ | Голосовые эмбеддинги для грубой live-диаризации |
| ffmpeg | https://ffmpeg.org/ | Извлечение аудиодорожки из видеозаписей звонков |
| SQLite | встроено в Python (`sqlite3`) | Хранение метаданных звонков и транскриптов |
| Python | https://www.python.org/ | Язык разработки backend (3.11, отдельный venv для `backend/`) |

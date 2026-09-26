# Первые шаги

Полный план и обоснование — в `docs/architecture.md`. Реализуем строго по стадиям, каждая проверяется до перехода к следующей.

## Stage 0 — Скелет backend
1. Создать `backend/` (FastAPI, отдельный `.venv` на Python 3.11, `pyproject.toml`).
2. Проверить, что `ffmpeg` доступен в системе (нужен для извлечения звука из видео).
3. Поднять `uvicorn`, эндпоинт `GET /health` для проверки, что сервис живой.

## Stage 1 — Batch-пайплайн (ядро качества, без стриминга) — ✅ реализовано
4. ✅ `faster-whisper` (модель `small`, GPU/CUDA, fallback на CPU int8) — `backend/app/asr.py`.
5. ✅ Эндпоинт `POST /calls/upload` — `backend/app/main.py`.
6. ✅ `pyannote.audio` офлайн-диаризация, сведение с текстом по таймкодам (макс. пересечение по времени) — `backend/app/diarization.py`, `backend/app/pipeline.py`.
7. ✅ SQLite-схема (Call, Speaker, TranscriptSegment, AudioFile) — `backend/app/db.py`.
8. ✅ `GET /calls/{id}` — JSON с сегментами и именами спикеров, `GET /calls` — список.

### Заметки по установке окружения (пригодится при переносе на другую машину)
- `backend/.env` — обязателен `HF_TOKEN` (бесплатный токен HuggingFace). Плюс нужно вручную принять условия трёх gated-моделей на huggingface.co (в своём аккаунте, кнопка "Agree and access repository"): `pyannote/speaker-diarization-3.1`, `pyannote/segmentation-3.0`, `pyannote/speaker-diarization-community-1`.
- `torch` с поддержкой GPU не ставится через обычный `pip install` — PyPI отдаёт CPU-сборку по умолчанию. Нужно переустановить под конкретный тег CUDA (см. `pyproject.toml`, комментарий рядом с зависимостями). Проверить: `python -c "import torch; print(torch.cuda.is_available())"`.
- `pyannote.audio` 4.x использует `torchcodec` для декодирования аудио, а тот требует "full-shared" сборку ffmpeg (отдельные DLL: avcodec/avformat/...) — обычный статический `ffmpeg.exe` (который ставит `winget install Gyan.FFmpeg`) для этого не подходит. Shared-сборка скачана вручную в `backend/.tools/ffmpeg-*-shared/` (см. `backend/app/gpu.py`, не в git).
- CUDA-библиотеки для `faster-whisper` (cuBLAS/cuDNN) ставятся через pip (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`), но Windows не ищет их в venv сама — пути добавляются в `PATH` кодом при старте (`backend/app/gpu.py`, вызывается из `asr.py`/`diarization.py` до импорта соответствующих библиотек).

## Stage 2 — Live-транскрибация с микрофона — ✅ реализовано
9. ✅ VAD-чанкинг (`silero-vad`) — `backend/app/vad.py`, потоковый `SpeechChunker` с таймкодами.
10. ✅ Потоковая ASR по сегментам — `backend/app/asr.py: transcribe_pcm`.
11. ✅ `WS /ws/stream/{call_id}` — `backend/app/main.py`, `backend/app/pipeline.py: LiveCallSession`.
12. ✅ Грубая live-диаризация (эмбеддинги + онлайн-кластеризация) — `backend/app/live_diarization.py` (resemblyzer + косинусная близость к центроидам спикеров).
13. ✅ Минимальный UI — `backend/static/live.html` (кнопка Начать/Остановить, захват микрофона через Web Audio API, текст по спикерам по мере поступления).

**Проверено**: сквозной браузерный тест (Playwright, `--use-file-for-fake-audio-capture`) — 15/15 сегментов транскрибированы и разложены по спикерам, таймкоды верны. На двух разных синтетических голосах live-диаризация дала 14/15 верно (1 ошибка на очень короткой первой реплике — ожидаемое ограничение "черновой" точности, см. `docs/architecture.md` §3.3).

**Осталось перед закрытием Stage 1+2**: прогнать оба пайплайна (batch и live) на реальной записи/живом голосе — синтетический TTS подтвердил механику, не качество на настоящей речи.

### Дополнительные заметки по установке (Stage 2)
- `resemblyzer` тянет `webrtcvad`, который требует компилятор C++ (не ставим тяжёлые Visual Build Tools ради одной зависимости) — вместо этого ставим `webrtcvad-wheels` (готовые wheels) первым, затем `resemblyzer --no-deps`, затем `librosa` отдельно (он тоже нужен `resemblyzer`, но сам не тянет compile-зависимостей).
- Playwright с эмуляцией микрофона (`--use-fake-device-for-media-stream --use-file-for-fake-audio-capture=<path.wav>`) стоит в `backend/.venv` как dev-инструмент для тестирования — не входит в `pyproject.toml` (не нужен в проде).

## Stage 3 — Захват онлайн-звонков — 🟡 механика проверена, смешение звука — нет
14. ✅ Выбор режима звонка (очная встреча / онлайн-звонок) — `backend/static/live.html`.
15. ✅ Захват вкладки/экрана (`getDisplayMedia`) + микс с микрофоном через `GainNode` в тот же PCM16-поток — `backend/static/live.html`. Backend не менялся (принимает тот же формат, что и Stage 2).
16. ✅ Playwright-тест: UI/happy-path без крашей, ошибка "нет звука" корректно блокирует открытие WS, регрессия Stage 2 — всё PASS.

**Stage 3 закрыт**: ручной тест в реальном Chrome (2026-09-25) подтвердил — звук вкладки и микрофон захватываются и транскрибируются из одного смешанного потока; черновая live-диаризация путается на смеси (ожидаемо), но реконсиляция после звонка (Stage 4) корректно разводит на настоящих 2 голосов. См. `docs/status.md`.

**Мелкий долг устранён**: `source` теперь передаётся с фронтенда как query-параметр WS (`mic`/`tab_capture`) и корректно пишется в БД (был баг, см. `docs/issues.md`, запись от 2026-09-25 — можно закрыть).

## Stage 4 — Реконсиляция и полировка — ✅ реализовано и проверено сквозным тестом
17. ✅ Live-сессия пишет полный аудиопоток на диск (`storage/{call_id}/audio.wav`) — `backend/app/pipeline.py: LiveCallSession`.
18. ✅ Реконсиляция черновой live-диаризации с точной offline (pyannote) в фоне после звонка — `backend/app/reconciliation.py: reconcile_live_call()`, запускается из `backend/app/main.py: stream_call` через `run_in_executor`.
19. ✅ Экспорт транскрипта — `GET /calls/{id}/export?format=txt|pdf` — `backend/app/main.py`.
20. ✅ Список звонков + загрузка файла — `backend/static/calls.html`.
21. ✅ Просмотр транскрипта + переименование спикеров (`PATCH /calls/{id}/speakers/{speaker_id}`) — `backend/static/call.html`.
22. ✅ Сквозной браузерный тест (Playwright): upload/live → реконсиляция → список → переименование → экспорт — все сценарии PASS, JSON-контракт между backend и обоими фронтенд-файлами сверен и совпадает полностью.

**Известные некритичные оговорки** (см. `docs/issues.md`):
- PDF-экспорт: кириллица визуально рендерится верно, но не извлекается `pdftotext`/poppler (ToUnicode CMap) — copy/paste и полнотекстовый поиск по PDF ограничены.
- PDF-шрифт зависит от Windows-путей (`_CYRILLIC_FONT_CANDIDATES` в `main.py`) — при деплое на Linux потребуется поправить.
- Реальное смешение мик+вкладка (Stage 3) по-прежнему не проверено живым тестом — ограничение test-инфраструктуры Chromium, нужен ручной прогон.

## Проверка на реальной речи — ✅ выполнено (2026-09-26)
Batch и live пайплайны прогнаны на настоящей русской речи (не TTS), включая сценарий
"голос из динамиков (ИИ-собеседник) + микрофон через одно устройство". Найденные и
исправленные баги live-диаризации — см. `docs/issues.md`, запись от 2026-09-26.

## Stage 5 — "Видео-инсайты" — ✅ реализовано (2026-09-26)
Второй, независимый раздел приложения (см. CLAUDE.md) — ссылка на YouTube → расшифровка
→ отчёт по ключевым мыслям. Не переиспользует модель данных звонков (без диаризации).

1. ✅ Скачивание аудио — `yt-dlp` (`app/video_insights.py: download_audio`), конвертация в WAV через уже существующий `app/audio_convert.py` (переиспользован как есть).
2. ✅ Транскрибация — переиспользован `app/asr.py: transcribe()`, но с `language=None` (автоопределение) вместо принудительного `"ru"` — видео может быть на любом языке, в отличие от звонков (нашлось и исправлено в процессе: тестовое англоязычное видео с принудительным `ru` давало галлюцинированный бессмысленный текст).
3. ✅ Суммаризация — локальная LLM, **Qwen2.5-3B-Instruct** (GGUF, квант `q4_k_m`, ~2.1GB) через `llama-cpp-python`, CPU (не GPU — это фоновая non-realtime задача, GPU и так занят ASR/диаризацией во время звонков, VRAM всего 4GB). Промпт на русском, отчёт: о чём видео / ключевые мысли / итог. `app/video_insights.py: summarize()`.
4. ✅ Модель данных — отдельная таблица `video_insights` (`app/db.py`), не трогает `calls`/`speakers`/`transcript_segments`.
5. ✅ API — `POST /videos` (принимает `{url}`, фоновая обработка), `GET /videos`, `GET /videos/{id}` — `app/main.py`.
6. ✅ UI — `static/videos.html` (список + форма отправки ссылки) и `static/video.html` (отчёт + полный транскрипт + экспорт .txt на клиенте). Ссылка "Видео-инсайты" добавлена в шапку `live.html`/`calls.html`/`call.html`.
7. ✅ Проверено end-to-end через реальный HTTP-запрос (`POST /videos` → фоновая обработка → `GET /videos/{id}`) на реальном YouTube-видео — скачивание, автоопределение языка, суммаризация в отчёт — всё отработало корректно.

### Установка окружения (Stage 5)
- `pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu` — обычный PyPI sdist требует компилятор (CMake+MSVC), этот индекс отдаёт готовый CPU-wheel под Windows/Python 3.11.
- `pip install yt-dlp` — обычная зависимость, без сюрпризов.
- Модель (GGUF, ~2.1GB, не в git) — скачивается один раз:
  ```python
  from huggingface_hub import hf_hub_download
  hf_hub_download(repo_id="Qwen/Qwen2.5-3B-Instruct-GGUF", filename="qwen2.5-3b-instruct-q4_k_m.gguf", local_dir="backend/.tools/models")
  ```
  (`huggingface_hub` уже в зависимостях транзитивно через `pyannote.audio`). Путь ожидается в `app/config.py: LLM_MODEL_PATH`.
- На машине с малым объёмом свободной RAM (здесь — ~1.3GB доступно при обычной нагрузке) модель грузится через mmap (поведение llama.cpp по умолчанию) — не требует держать весь файл резидентно в памяти разом, но первая суммаризация может быть медленнее из-за подкачки с диска.

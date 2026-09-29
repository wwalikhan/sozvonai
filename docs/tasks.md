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

## Деплой на Vercel (облачный режим, Gemini) — ✅ первый прод-деплой живой (2026-09-29)

Финальный деплой создан и прошёл: `sozvonai-walikhan.vercel.app`. `GET /health` → `{"status":"ok"}`,
`GET /config` → `live_enabled: false`, `auth_enabled: true`, статика (`/`, `/static/login.html`,
`/static/calls.html`, `/static/videos.html`) отдаёт 200.

**Как в итоге собрали все 27 файлов** (без `upload_file`/base64 для последних двух —
оказалось не нужно): 25 файлов сослались по `{file, sha, size}` на уже подтверждённые
в sha-кэше Vercel (см. таблицу ниже), а `static/live.html` и `pyproject.toml` передали
прямо в `create_deployment` как инлайн `{file, data, encoding: "utf-8"}` — раз файл целиком
читается одним `Read` без обрезки (это удалось для `live.html`, 706 строк/24229 байт), можно
обойтись без base64 вообще: побайтовая проверка (`Write` копии + `diff`/`sha1sum` против
исходника) подтвердила точное совпадение перед вставкой в вызов.

**Единственная проблема при первом прогоне** — `create_deployment` упал на шаге билда:
```
Failed to run "uv lock --python ...": error: The requested interpreter resolved to
Python 3.12.14, which is incompatible with the project's Python requirement: `==3.11.*`
```
Vercel в этом окружении резолвит Python 3.12, а `backend/pyproject.toml` жёстко требовал
`>=3.11,<3.12`. Исправлено: `requires-python = ">=3.11,<3.13"` (безопасно — облачные базовые
зависимости из `[project.dependencies]` не завязаны на конкретный минор; тяжёлые ML-пакеты
под 3.11 остаются в `[project.optional-dependencies] local`, который в облаке не ставится).
После правки передеплой прошёл с первого раза.

### Осталось (не блокирует, но не проверено)
1. ✅ Сквозной логин через `login.html` на реальном Supabase-проекте на проде — подтверждено пользователем (2026-09-29). Заодно выяснилось и поправлено: Supabase Auth Site URL/Redirect URLs всё ещё указывали на `localhost` из локальной разработки — нужно вручную поправить в Dashboard (Authentication → URL Configuration) на `https://sozvonai-walikhan.vercel.app` (у ассистента нет MCP-доступа к этим настройкам).
2. Загрузить тестовую запись через `/static/calls.html` на проде, убедиться что статус доходит до `done`.
3. Через Supabase MCP (`execute_sql`/`list_storage_buckets`) сверить, что строки и файл записи реально попадают в `calls`/`audio_files`/`speakers`/`transcript_segments` и bucket `call-audio`.

### Найденный и исправленный баг: лимит Vercel на размер тела запроса (2026-09-29)
Загрузка реальной записи (26МБ .m4a) падала на проде с общей ошибкой "не удалось
загрузить файл". Причина — жёсткий платформенный лимит Vercel на тело запроса к
serverless-функции: **~4.5МБ**, не настраивается через `vercel.json` (проверено
напрямую: файл 4МБ проходит до проверки авторизации, 6МБ Vercel обрубает раньше
с `FUNCTION_PAYLOAD_TOO_LARGE`/413).

Исправлено — прямая загрузка с браузера в Supabase Storage по signed URL, в обход
тела запроса к функции:
- `app/storage_backend.py: create_signed_upload_url()` — новая функция.
- `app/pipeline.py: process_call_from_storage()` — скачивает файл из Storage во
  временный файл и обрабатывает как обычно (переиспользует `_process_call_cloud`).
- `app/main.py`: `POST /calls/upload-url` (шаг 1 — создаёт `call`, отдаёт signed
  URL) + `POST /calls/{id}/finalize` (шаг 2 — вызывается после прямой загрузки,
  запускает обработку). Оба — только `CLOUD_MODE` (404 иначе). `GET /config`
  получил новое поле `direct_upload_enabled`.
- `static/js/session.js`: `isDirectUploadEnabled()`.
- `static/calls.html`: кнопка "Загрузить" ветвится — в облаке идёт по новой
  двухшаговой схеме (`upload-url` → `PUT` в Supabase Storage напрямую → `finalize`),
  локально (SQLite, `direct_upload_enabled: false`) — старым способом без изменений.

Задеплоено и проверено на проде (2026-09-29): `GET /config` отдаёт
`direct_upload_enabled: true`, `POST /calls/upload-url` без токена → 401 (не 404,
эндпоинт существует и требует авторизацию). Полный сквозной тест с реальной
загрузкой файла — см. пункт 2 выше, ещё не прогнан.

### Миграция ASR/суммаризации с Gemini на Groq (2026-09-29, код готов, ДЕПЛОЙ ЗАБЛОКИРОВАН)
Реальная загрузка (26МБ .m4a, затем и mp3) после фикса direct-upload стабильно падала
на этапе транскрибации с `503 UNAVAILABLE` от Gemini. Прямая проверка API ключа
показала: `gemini-3.6-flash`/`gemini-flash-latest` — живые 503 (перегрузка на стороне
Google прямо сейчас, не разовая случайность), `gemini-2.0/2.5-flash(-lite)`,
`gemini-2.5-pro`, `gemini-3-pro-preview`, `gemini-1.5-flash` — 404 (сняты с бесплатных
ключей), `gemini-pro-latest` — 429 (квота исчерпана). На бесплатном ключе у Gemini не
осталось рабочего запасного варианта вообще.

По решению пользователя — полный переход на Groq (`app/groq_transcribe.py`, замена
`app/gemini_transcribe.py`, который удалён):
- Whisper (`whisper-large-v3-turbo`) для транскрибации — **без диаризации** (Groq
  Whisper её не делает, в отличие от Gemini): все сегменты в облаке идут под единой
  меткой "Спикер 1". Восстановление разметки по спикерам в облаке — отдельная
  будущая задача.
- `openai/gpt-oss-120b` (через Groq) — суммаризация для video-insights, замена
  `summarize_text`.
- `app/config.py`: `GEMINI_API_KEY`/`GEMINI_MODEL` → `GROQ_API_KEY`,
  `GROQ_WHISPER_MODEL` (default `whisper-large-v3-turbo`), `GROQ_LLM_MODEL` (default
  `openai/gpt-oss-120b`). Доступные модели на бесплатном ключе проверены напрямую
  через `client.models.list()` — не гадать, список меняется.
- `pyproject.toml`: `google-genai` → `groq`. `backend/.env` и Vercel env
  (`prj_36QmOIRilaNsKenJxR6S6QEml20q`) дополнены `GROQ_API_KEY` (upsert).
- Проверено локально (`uv run`): и транскрибация, и суммаризация реально отвечают
  через живой Groq API до деплоя на Vercel.

**Деплой заблокирован багом на стороне Vercel (не в нашем коде), обнаружен 2026-09-29:**
любой `create_deployment` с непустым `vercel.json`-ключом `"functions"` (даже
байт-в-байт идентичным содержимому уже работавшего прод-деплоя, включая просто
`{"app/main.py": {"maxDuration": 300}}` без `excludeFiles`/`fluid`) падает на шаге
сборки с `errorCode: "unused_function"`: `The pattern "app/main.py" defined in
'functions' doesn't match any Serverless Functions inside the 'api' directory.`
Билд падает за 1.5-4 секунды — слишком быстро для реальной установки зависимостей,
похоже на сбой валидации ДО реального билда. Проверено и исключено как причина:
- Разрешение зависимостей (`uv pip compile pyproject.toml --python-version 3.12`) —
  чистое, `groq` резолвится без конфликтов.
- Синтаксис всех изменённых `.py`-файлов (`py_compile`) — ок.
- `rootDirectory`/`framework` на самом Vercel-проекте сброшены в `null` явно — не
  помогло.
- Дословный редеплой уже работавшего набора файлов (все 27 sha от READY-деплоя
  `dpl_HXtRNUs5nNDRXfXHroEsuGKuppTQ`, без единого изменения) — падает с той же
  ошибкой. Это доказывает, что дело не в правках, а в состоянии платформы/проекта.
- Без `vercel.json` вообще (zero-config) деплой проходит (`READY`), но тогда
  FastAPI-приложение монтируется только на буквальный путь `/app/main.py`, а не на
  `/` — `/health`, `/config`, `/` отдают 404.
- `vercel.json` с `"rewrites": [{"source": "/(.*)", "destination": "/app/main.py"}]`
  вместо `"functions"` — деплой проходит, но rewrite НЕ эквивалентен настоящей
  serverless-функции: Vercel отдаёт **сырой исходный код `main.py` как текст**
  вместо выполнения приложения. Опасно — сразу откачено.
- **Аварийный откат уже сделан**: `mcp__vercel__request_promote` на
  `dpl_HXtRNUs5nNDRXfXHroEsuGKuppTQ` — прод сейчас снова на этом READY-деплое
  (старый Gemini-код), `/health`/`/config`/`/` отвечают корректно, исходники не
  протекают. Повторная проверка (`create_deployment` с оригинальным
  `vercel.json.functions`) спустя ~20 минут — та же ошибка, баг не разовый.
- `resourceConfig.functionDefaultTimeout: 300` выставлен на проекте через
  `update_project` как запасной способ задать таймаут функции в обход
  `vercel.json.functions`, если/когда получится задеплоить без него.

**Решено (2026-09-29): подключена git-интеграция, деплой снова живой.**
Пользователь вручную подключил `sozvonai` на Vercel к `wwalikhan/sozvonai` на
GitHub (Settings → Git — `create_git_project` через API не сработал, 403 на scope
"walikhan", как и в прошлой сессии). Первый git-пуш (`5701f1a`) сначала тоже упал
(`rootDirectory` у проекта не был выставлен на `backend`, поэтому Vercel собирал с
корня репо, где нет `app/main.py`/`static/`) — после `update_project(rootDirectory:
"backend")` и повторного пуша (`8071886`) всё ещё падал с тем же `unused_function`.
Помогло: `update_project(framework: "fastapi")` явно (было `null`, автоопределение
почему-то давало сбой именно на связке "framework не задан + функция по
`vercel.json.functions`") + редеплой того же коммита. Итог — **git-путь деплоя
работает и не подвержен тому же багу, что API-путь через `create_deployment` с
inline/sha-файлами** (тот всё ещё не тестировался повторно после фикса — если API-путь
понадобится снова, сначала проверить, не ловит ли он ту же ошибку).

Live-проверка прошла: `/` → 307 на `/static/calls.html`, `/health` →
`{"status":"ok"}`, `/config` → `direct_upload_enabled: true`, `/static/calls.html`
→ 200, `POST /calls/upload-url` без токена → 401 (не 404). Groq-код (см. раздел
выше) теперь в проде — деплой `dpl_F3EaCudZu9cR4tCcMsmobWC1zGBA`.

**На будущее — теперь можно просто пушить в `main`, Vercel задеплоит сам.** Отдельный
`create_deployment` с ручной сборкой файлового списка больше не нужен для обычных
изменений (архивный раздел ниже про заливку 27 файлов по sha — устарел, актуален
только если git-интеграция когда-то отвалится).

**Осталось проверить** (после первого реального Groq-транскрибирования на проде):
1. Загрузить тестовую запись через `/static/calls.html`, убедиться что статус
   доходит до `done` (без диаризации — один "Спикер 1", это ожидаемо).
2. Через Supabase MCP сверить, что строки/файл попадают в `calls`/`audio_files`/
   `speakers`/`transcript_segments` и bucket `call-audio`.

## Архив: процесс заливки файлов (историческая справка, актуально для будущих деплоев с большими/кириллическими файлами)

Контекст и полный план: `C:\Users\Uali_\.claude\plans\eventual-watching-quill.md`.
Код для `CLOUD_MODE` уже написан, закоммичен и запушен (main, коммит `f2517e4`
"Pin working Gemini model and retry 503s..." поверх `5e0f70c`). **Не переписывать
код заново** — доделать только сам деплой.

### Уже сделано
- Рабочая модель Gemini подобрана эмпирически: **`gemini-3.6-flash`** (дефолт в
  `app/config.py`). `gemini-2.5-flash`/`-lite` — 404 (недоступны новым ключам),
  `gemini-3.7/3.8-flash`, `gemini-flash-latest` — стабильно 503 (перегрузка на
  стороне Google). `gemini-3.6-flash` подтверждён end-to-end на реальной записи
  голоса (аудио + JSON-диаризация) — работает ~2 из 3 попыток, поэтому в
  `app/gemini_transcribe.py` добавлен retry (3 попытки, пауза 5с) на `ServerError`.
- `backend/.env` дополнен (не в git): `GEMINI_API_KEY`, `SUPABASE_SERVICE_ROLE_KEY`,
  `SUPABASE_STORAGE_BUCKET=call-audio` — секреты уже на месте, спрашивать
  пользователя заново не нужно.
- На Vercel-проекте `sozvonai` (`prj_36QmOIRilaNsKenJxR6S6QEml20q`) выставлены
  все 8 env-переменных: `CLOUD_MODE=true`, `DATABASE_URL`, `SUPABASE_URL`,
  `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY`,
  `GEMINI_MODEL=gemini-3.6-flash`, `SUPABASE_STORAGE_BUCKET=call-audio`.
- Supabase Storage bucket `call-audio` существует и уже проверен end-to-end
  (upload/download/signed URL/delete) в прошлой сессии.
- `create_git_project` (авто-деплой из GitHub) не сработал ещё в прошлой сессии
  (403 "re-authenticate to scope walikhan") — решили деплоить напрямую через
  `mcp__vercel__create_deployment` с inline-файлами в тот же проект.

### Важный технический вывод (не наступать на те же грабли)
`mcp__vercel__create_deployment` с инлайновыми `{file, data, encoding: "utf-8"}`
**не** кладёт содержимое в sha-кэш Vercel — на него нельзя сослаться позже через
`{file, sha, size}` в другом деплое (проверено многократно, "missing_files").
Единственный способ подготовить файл для финального деплоя по sha-ссылке —
сначала явно залить его через `mcp__vercel__upload_file` (`requestBody` — base64,
`xVercelDigest` — sha1, `contentLength` — размер в байтах), и только потом
ссылаться на него по `{file, sha, size}` в `create_deployment`.
**Base64 обязательно генерировать через Bash (`base64 -w0 <файл>`) прямо перед
вызовом**, не копировать вручную из вывода `Read` — ручная перекопировка больших
base64-блобов один раз уже дала `sha1sum_mismatch`/`Binary arguments must be
valid base64 strings`.

### Прогресс загрузки файлов (26 из 27 подтверждены в sha-хранилище Vercel)
Уже готовы к финальному деплою по `{file, sha, size}` (не перезаливать):
```
app/__init__.py            da39a3ee5e6b4b0d3255bfef95601890afd80709   0
app/asr.py                  90e37a796f02879e4def7cf9be9a7d02098ed0a2  4525
app/audio_convert.py        25a3bad4f7b54b341a0060d4039359e561eb9728  907
app/auth.py                 9163d1345be19314359d7953a371fc0ead12973f  2514
app/diarization.py          d3c109a3d58fe5049f469bb90728ec20406a129c  1204
app/gpu.py                  007cdbf2c0053d638a977c02aea630f692f4e696  1335
app/config.py               59bf4eb2fc21b06c4cd84cc7b5f1e73ba8dd2cca  6383
app/storage_backend.py      3503d4d3cd830c682c70de9526192a9e9e64ec00  2878
app/reconciliation.py       3951b8725b4260f4c2d5fd3866b68a55cabbaba8  3304
app/live_diarization.py     02255a241e7ee3ab74beffcc0e7566393bb59b40  4855
app/db.py                   23ad5454d473c39c9f0f9892bff12b50f8d1868e  12953
app/vad.py                  d9a0c5ed0d398ac0b4c88c2798d937a41b5ad9cb  6526
pyproject.toml               42fd0a11b5e47a5fcbd1dcfde8ecd30fed94f437 2783
vercel.json                  6691d765c275a2282a7b479adf90e9aecd125f5b 213
static/favicon.svg           f1dd5859ad79f2aaa56079a6f8f2ec51606f6298 380
static/js/api.js             0c8f18f77069773ab1412229818ec571df7151fb 1953
static/js/session.js         79afc3a0ce97f3e65b2fde41b3bdb5c774ece7ce 3757
```
(sha1/size всегда можно пересчитать заново: `sha1sum <файл>` + `stat -c%s <файл>`
из `backend/` — если вдруг не совпадёт, значит файл поменялся, перезалить.)

Ещё нужно залить через `upload_file` (`gemini_transcribe.py`, `pipeline.py`, `main.py`, `video_insights.py`,
`login.html`, `videos.html`, `video.html`, `calls.html`, `call.html` подтверждены 2026-09-29). **Важный урок по надёжной заливке файлов с кириллицей** (комментарии на русском
внутри .py — актуально и для будущих переносов, не только для этих 27 файлов): ручной перенос base64
длиннее ~5КБ ненадёжен даже с одноразовой раунд-трип-проверкой — модель может *регенерировать* (не
скопировать) строку заново и почти всегда спотыкается на тех же кириллических фрагментах. Рабочая
процедура:
1. `base64 -w0 <файл> > /tmp/x.b64`.
2. Разбить на куски ~5000 байт: `head -c 5000 x.b64 > x_p1.b64; tail -c +5001 x.b64 > x_p2.b64`.
3. Для каждого куска — Write в scratchpad, затем `tr -d '\n' < <written> | cmp - <часть источника>`.
4. При расхождении — не перепечатывать весь кусок заново (только множит ошибки); найти байт-оффсет
   через `cmp`, взять безопасный уникальный якорь через `grep -bo`, и склеить `head -c <offset> <мой файл>`
   + `tail -c +<offset+1> <исходный файл>` — то есть чинить сам файл на диске байтами из истинного
   источника, а не ретайпом.
5. Когда все куски побайтово совпадают — `cat` их вместе, сверить sha1 с ожидаемым, и только тогда
   один раз вставить получившийся текст в `upload_file`.
```
static/live.html             3cdee38f5f1437a1229054c38f9d6340153ec96f 24229
```

### Следующие шаги
1. Для каждого файла из списка "ещё нужно залить": `base64 -w0 <путь>` в Bash,
   результат целиком передать в `mcp__vercel__upload_file` (`xVercelDigest` = sha1
   из таблицы выше, `contentLength` = размер из таблицы). При таймауте — просто
   повторить тот же вызов ещё раз (наблюдалось, что временами таймаутит без
   видимой причины, повтор обычно проходит).
2. Когда все 27 файлов подтверждены (`upload_file` вернул `{"result":{"urls":[...]}}`),
   один финальный `mcp__vercel__create_deployment` с `target: "production"`,
   `project: "prj_36QmOIRilaNsKenJxR6S6QEml20q"`, `projectSettings.framework:
   "fastapi"` и всеми 27 файлами как `{file, sha, size}` (без `data`).
3. Проверить: `GET /health`, `GET /config` (`live_enabled: false`, `auth_enabled:
   true`) на реальном URL задеплоенного проекта.
4. Залогиниться через `login.html` на реальном Supabase-проекте в браузере —
   этот сквозной браузерный тест ещё ни разу не прогонялся (см. ниже).
5. Загрузить короткую тестовую запись через задеплоенный `calls.html`, убедиться
   что сегменты с спикерами появляются и `GET /calls/{id}` сразу `status: done`.
6. Через Supabase MCP (`execute_sql`/`list_storage_buckets`) проверить, что
   строки реально попали в `calls`/`audio_files`/`speakers`/`transcript_segments`
   и файл лёг в bucket `call-audio`.
7. Только после успешной проверки — обновить `CLAUDE.md`/`docs/status.md` записью
   "Деплой ✅ готово" (сейчас там `❌ не начато` — уже неактуально, но рано ставить
   ✅ до реальной проверки).

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

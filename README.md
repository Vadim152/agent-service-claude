# agent-service

`agent-service` — FastAPI backend для IntelliJ-плагина и HTTP/CLI клиентов.

## Что умеет сервис

- Сканирование и индексация Cucumber step definitions.
- Генерация `.feature` в `jobs-first` режиме.
- Chat control-plane (sessions, status, history, SSE, approvals).
- Memory layer (`MemoryService`) для:
  - feedback по маппингу шагов,
  - generation rules,
  - step templates.
- Split-режим: Control Plane / Execution Plane / Tool Host.

Документация по плагину: `ide-plugin/README.md`.

## Архитектура

### Current (as-is)

Ниже фактическая архитектура по текущей реализации в `src/app`, `src/api`, `src/chat`, `src/infrastructure`, `src/self_healing`.

```mermaid
flowchart LR
  subgraph Clients["Clients"]
    IDE["IDE Plugin"]
    CLI["CLI or HTTP"]
  end

  subgraph CP["Control Plane: agent-service"]
    API["FastAPI API<br/>Jobs, Chat, Steps, Memory, Tools, Feature"]
    CHAT["Chat Runtime<br/>sessions, approvals, SSE"]
    DISP["Job Dispatcher<br/>local or queue"]
    STATE[("Run State Store<br/>memory or postgres")]
  end

  subgraph XP["Execution Plane"]
    Q["Queue optional<br/>local or redis"]
    W["Worker<br/>agent-service-worker"]
    SUP["ExecutionSupervisor"]
    ORCH["Orchestrator Runtime<br/>LangGraph"]
  end

  subgraph TH["Tool Host"]
    THL["Local Tool Host<br/>in-process apply_feature"]
    THR["Remote Tool Host<br/>agent-service-tool-host"]
  end

  subgraph Storage["Storage"]
    ART[(".agent/artifacts<br/>artifact files and incidents")]
    CM[(".agent/chat_memory<br/>sessions and project memory")]
    VDB[(".chroma<br/>Chroma vector store")]
    PG[("Postgres optional")]
    R[("Redis optional")]
  end

  IDE --> API
  CLI --> API

  API --> CHAT
  API --> DISP
  API --> STATE

  DISP -->|local| SUP
  DISP -->|queue| Q
  Q --> W
  W --> SUP
  SUP --> ORCH

  CHAT -->|autotest jobs| SUP
  CHAT -->|save_generated_feature| THL
  CHAT -->|save_generated_feature (remote mode)| THR

  SUP --> ART
  ORCH --> VDB
  CHAT --> CM
  STATE -. postgres mode .-> PG
  Q -. redis backend .-> R
```

Ключевые моменты текущей реализации:

- Внешний стриминг реализован через SSE (`/jobs/{job_id}/events`, `/chat/sessions/{session_id}/stream`), не через WebSocket.
- Approvals/pending permissions встроены в `chat runtime`, отдельного `policy`-сервиса сейчас нет.
- Execution backend переключается между `local` и `queue`; queue backend — `local` или `redis`.
- Tool Host поддерживает `local` и `remote` режимы; remote-сервис сейчас минимальный (`/internal/tools/save-feature`).

### Target (roadmap)

Целевая схема (не все блоки уже реализованы):

```mermaid
flowchart LR
  subgraph Clients["Клиенты"]
    IDE["IDE Plugin"]
    CLI["CLI or HTTP"]
  end

  subgraph CP["Control Plane: один сервис"]
    API["FastAPI API<br/>Runs, Sessions, SSE or WS"]
    Policy["Tooling and Policy<br/>registry, approvals, audit"]
    RunDB[("Postgres<br/>runs, attempts, events, sessions")]
    ArtIdx[("Artifacts index<br/>in DB")]
  end

  subgraph XP["Execution Plane"]
    Q["Queue optional<br/>Redis or RabbitMQ"]
    W["Worker Pool<br/>Executors"]
    Orch["Orchestrator Runtime<br/>LangGraph"]
    RT["Runtime Plugins<br/>testgen, ift, debug, browser, analytics"]
  end

  subgraph Tools["Tool Host: коннекторы"]
    Repo["Repo and code access"]
    Logs["Logs query"]
    Art["Artifacts store"]
    Browser["Playwright runner"]
    Analytics["Analytics query"]
    Patch["Patch proposal and apply"]
  end

  subgraph Storage["Хранилища"]
    OBJ[("S3 or MinIO or FS<br/>artifacts, traces, screenshots")]
    VDB[("Vector store optional")]
    LOGS[("Log backend optional")]
    WARE[("Analytics warehouse optional")]
  end

  IDE --> API
  CLI --> API

  API --> RunDB
  API --> Policy

  API --> Q
  Q --> W
  W --> Orch
  Orch --> RT

  Orch --> Tools
  Tools --> OBJ
  Tools --> VDB
  Tools --> LOGS
  Tools --> WARE

  Patch --> Policy
  Art --> ArtIdx
```

### Дельта между Current и Target

- Реализовано: split-модель `Control Plane / Execution Plane / Tool Host`, `jobs-first`, SSE-стримы, Postgres backend для job state, Redis backend для queue, LangGraph runtime.
- Частично: Tool Host есть, но пока с минимальным internal API; worker есть, но не пул исполнителей.
- Пока отсутствует как выделенный слой: отдельный `Policy` сервис (`registry/approvals/audit`) и `Artifacts index` в БД.
- Пока отсутствует как встроенный backend: RabbitMQ queue backend, WS endpoint-ы, S3/MinIO/log/warehouse интеграции.
- Runtime plugins и расширенный tool-host слой (browser/analytics/log connectors) пока не выделены как самостоятельные модули платформенного уровня.

См. также разделы ниже: `Режимы запуска`, `Split: CP + Worker + Tool Host`, `API Overview`.

## Структура репозитория

- `src/` — backend-код (`app`, `api`, `agents`, `chat`, `memory`, `self_healing`, `infrastructure`).
- `tests/` — pytest тесты.
- `ide-plugin/` — IntelliJ plugin (Kotlin/Gradle).
- `.agent/` — runtime данные (индексы, артефакты).
- `.chroma/` — локальное vector storage (если используется).

## Режимы запуска

### 1. Single-process (по умолчанию)

- `AGENT_SERVICE_STATE_BACKEND=memory`
- `AGENT_SERVICE_EXECUTION_BACKEND=local`
- `AGENT_SERVICE_TOOL_HOST_MODE=local`

`POST /jobs` выполняется внутри процесса API.

### 2. Split (production path)

- Control Plane: `agent-service`
- Execution worker: `agent-service-worker`
- Tool Host: `agent-service-tool-host`

Рекомендуемые backend'ы:
- `AGENT_SERVICE_STATE_BACKEND=postgres`
- `AGENT_SERVICE_EXECUTION_BACKEND=queue`
- `AGENT_SERVICE_QUEUE_BACKEND=redis`
- `AGENT_SERVICE_TOOL_HOST_MODE=remote`

## Требования

- Python `>=3.10`
- PowerShell (или любой shell с эквивалентными командами)

## Установка

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools
python -m pip install -e .
```

## Запуск

### Локально

```powershell
agent-service
```

Альтернатива:

```powershell
$env:PYTHONPATH="src"
python -m app.main
```

### Split: CP + Worker + Tool Host

```powershell
$env:AGENT_SERVICE_STATE_BACKEND='postgres'
$env:AGENT_SERVICE_POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/agent_service'
$env:AGENT_SERVICE_EXECUTION_BACKEND='queue'
$env:AGENT_SERVICE_QUEUE_BACKEND='redis'
$env:AGENT_SERVICE_REDIS_URL='redis://127.0.0.1:6379/0'
$env:AGENT_SERVICE_TOOL_HOST_MODE='remote'
$env:AGENT_SERVICE_TOOL_HOST_URL='http://127.0.0.1:8001'
```

Запуск процессов:

```powershell
agent-service
agent-service-worker
agent-service-tool-host
```

## Health / Readiness

```powershell
curl http://127.0.0.1:8000/health
```

- во время startup: `503`, `status=initializing`
- после инициализации: `200`, `status=ok`

## API Prefix

Все external endpoints публикуются с префиксом `AGENT_SERVICE_API_PREFIX` (по умолчанию `/api/v1`).

Пример: `POST /jobs` -> `POST /api/v1/jobs`.

## API Overview

### Steps API

- `POST /steps/scan-steps`
- `GET /steps/?projectRoot=...`

### Feature API (legacy sync)

- `POST /feature/generate-feature`
- `POST /feature/apply-feature`

### Jobs API

- `POST /jobs`
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/attempts`
- `GET /jobs/{job_id}/result`
- `POST /jobs/{job_id}/cancel`
- `GET /jobs/{job_id}/events` (SSE)

### Chat API

- `POST /chat/sessions`
- `GET /chat/sessions?projectRoot=...`
- `POST /chat/sessions/{session_id}/messages`
- `POST /chat/sessions/{session_id}/tool-decisions`
- `GET /chat/sessions/{session_id}/history`
- `GET /chat/sessions/{session_id}/status`
- `GET /chat/sessions/{session_id}/diff`
- `POST /chat/sessions/{session_id}/commands`
- `GET /chat/sessions/{session_id}/stream` (SSE)

### Tools API

- `POST /tools/find-steps`
- `POST /tools/compose-autotest`
- `POST /tools/explain-unmapped`

### Memory API

- `POST /memory/feedback`
- `GET /memory/rules?projectRoot=...`
- `POST /memory/rules`
- `PATCH /memory/rules/{rule_id}`
- `DELETE /memory/rules/{rule_id}?projectRoot=...`
- `GET /memory/templates?projectRoot=...`
- `POST /memory/templates`
- `PATCH /memory/templates/{template_id}`
- `DELETE /memory/templates/{template_id}?projectRoot=...`
- `POST /memory/resolve-preview`

### LLM API

- `POST /llm/test`

### Tool Host Internal API

- `POST /internal/tools/save-feature` (в `agent-service-tool-host`)

## Memory rules & templates

- Правила и шаблоны хранятся на backend per `projectRoot`.
- `GenerationRule` может задавать:
  - `qualityPolicy`
  - `language`
  - `targetPathTemplate`
  - `applyTemplates`
- `StepTemplate` хранит готовые Gherkin шаги и может триггериться через `triggerRegex`.
- Если срабатывает несколько шаблонов:
  - применяются по `priority`,
  - шаги дедуплицируются,
  - шаги добавляются в начало `scenario.steps`.
- В плагине доступен менеджер через кнопку `Memory` в header ToolWindow.

## Jobs-first поток

1. `POST /jobs` с `projectRoot`, `testCaseText`, параметрами генерации.
2. Статус через `GET /jobs/{job_id}` или `GET /jobs/{job_id}/events`.
3. Результат через `GET /jobs/{job_id}/result`.

Особенности:

- Пока результат не готов, `/jobs/{job_id}/result` возвращает `409`.
- Поддерживается `Idempotency-Key`:
  - тот же ключ + тот же payload -> возвращается существующий `jobId`
  - тот же ключ + другой payload -> `409`

## Ключевые env переменные

### Core

- `AGENT_SERVICE_APP_NAME` (default: `agent-service`)
- `AGENT_SERVICE_API_PREFIX` (default: `/api/v1`)
- `AGENT_SERVICE_HOST` (default: `127.0.0.1`)
- `AGENT_SERVICE_PORT` (default: `8000`)
- `AGENT_SERVICE_LOG_REQUEST_BODIES` (default: `false`)
- `AGENT_SERVICE_STEPS_INDEX_DIR` (default: `.agent/steps_index`)
- `AGENT_SERVICE_ARTIFACTS_DIR` (default: `.agent/artifacts`)

### Split/runtime

- `AGENT_SERVICE_STATE_BACKEND` (`memory|postgres`)
- `AGENT_SERVICE_POSTGRES_DSN`
- `AGENT_SERVICE_EXECUTION_BACKEND` (`local|queue`)
- `AGENT_SERVICE_QUEUE_BACKEND` (`local|redis`)
- `AGENT_SERVICE_QUEUE_NAME`
- `AGENT_SERVICE_REDIS_URL`
- `AGENT_SERVICE_EMBEDDED_EXECUTION_WORKER`
- `AGENT_SERVICE_TOOL_HOST_MODE` (`local|remote`)
- `AGENT_SERVICE_TOOL_HOST_URL`

### Jira source

- `AGENT_SERVICE_JIRA_SOURCE_MODE` (`stub|live|disabled`)
- `AGENT_SERVICE_JIRA_REQUEST_TIMEOUT_S`
- `AGENT_SERVICE_JIRA_DEFAULT_INSTANCE`
- `AGENT_SERVICE_JIRA_VERIFY_SSL`
- `AGENT_SERVICE_JIRA_CA_BUNDLE_FILE`

### LLM / GigaChat / Corp

- `AGENT_SERVICE_LLM_ENDPOINT`
- `AGENT_SERVICE_LLM_API_KEY`
- `AGENT_SERVICE_LLM_MODEL`
- `AGENT_SERVICE_LLM_API_VERSION`
- `GIGACHAT_CLIENT_ID` / `AGENT_SERVICE_GIGACHAT_CLIENT_ID`
- `GIGACHAT_CLIENT_SECRET` / `AGENT_SERVICE_GIGACHAT_CLIENT_SECRET`
- `GIGACHAT_SCOPE` / `AGENT_SERVICE_GIGACHAT_SCOPE`
- `GIGACHAT_AUTH_URL` / `AGENT_SERVICE_GIGACHAT_AUTH_URL`
- `GIGACHAT_API_URL` / `AGENT_SERVICE_GIGACHAT_API_URL`
- `GIGACHAT_VERIFY_SSL` / `AGENT_SERVICE_GIGACHAT_VERIFY_SSL`
- `AGENT_SERVICE_CORP_MODE`
- `AGENT_SERVICE_CORP_PROXY_HOST`
- `AGENT_SERVICE_CORP_PROXY_PATH`
- `AGENT_SERVICE_CORP_MODEL`
- `AGENT_SERVICE_CORP_CERT_FILE`
- `AGENT_SERVICE_CORP_KEY_FILE`
- `AGENT_SERVICE_CORP_CA_BUNDLE_FILE`
- `AGENT_SERVICE_CORP_REQUEST_TIMEOUT_S`
- `AGENT_SERVICE_CORP_RETRY_ATTEMPTS`
- `AGENT_SERVICE_CORP_RETRY_BASE_DELAY_S`
- `AGENT_SERVICE_CORP_RETRY_MAX_DELAY_S`
- `AGENT_SERVICE_CORP_RETRY_JITTER_S`

### Matcher tuning

- `AGENT_SERVICE_MATCH_RETRIEVAL_TOP_K`
- `AGENT_SERVICE_MATCH_CANDIDATE_POOL`
- `AGENT_SERVICE_MATCH_THRESHOLD_EXACT`
- `AGENT_SERVICE_MATCH_THRESHOLD_FUZZY`
- `AGENT_SERVICE_MATCH_MIN_SEQ_FOR_EXACT`
- `AGENT_SERVICE_MATCH_AMBIGUITY_GAP`
- `AGENT_SERVICE_MATCH_LLM_MIN_SCORE`
- `AGENT_SERVICE_MATCH_LLM_MAX_SCORE`
- `AGENT_SERVICE_MATCH_LLM_SHORTLIST`
- `AGENT_SERVICE_MATCH_LLM_MIN_CONFIDENCE`

## Тесты

Полный прогон:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m pytest -p no:cacheprovider
```

Smoke набор:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m pytest -p no:cacheprovider tests/test_jobs_api.py tests/test_chat_api.py tests/test_memory_api.py tests/test_memory_rules_api.py tests/test_job_dispatcher.py tests/test_tool_host_split.py tests/test_startup_readiness.py
```

## Troubleshooting

### `Result is not ready` (`409`) на `/jobs/{job_id}/result`

Job еще не в terminal статусе. Используйте polling `/jobs/{job_id}` или SSE `/jobs/{job_id}/events`.

### `projectRoot is required` (`422`) на `/steps/scan-steps`

Проверьте `projectRoot` в body/query и существование пути на диске.

### `Redis backend requires 'redis' package`

```powershell
python -m pip install redis
```

### `Postgres backend requires 'psycopg' package`

```powershell
python -m pip install "psycopg[binary]"
```

## Безопасность

- Не коммитьте секреты. Используйте `.env` и `AGENT_SERVICE_*`.
- Для корпоративной TLS используйте `*_CA_BUNDLE_FILE` вместо отключения SSL проверки.

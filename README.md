# agent-service

`agent-service` is a FastAPI backend plus an IntelliJ plugin for autotest generation, chat-assisted workflows, and controlled code/apply flows.

## Current Architecture

- Control Plane: FastAPI API with `runs`, `sessions`, `policy`, and `platform/*`
- Execution Plane: local execution or queued worker execution
- Tool Host: local or remote connector service
- State: `memory` or `postgres`
- Queue: `local`, `redis`, or `rabbitmq`
- Artifacts: local filesystem or S3-compatible object storage with a DB index

The public model is `runs-first`, while preserving free-text chat UX. A chat message such as `create autotest SCBC-T123` still creates a `run` with `plugin=testgen`.

## Public API

All external endpoints are published under `AGENT_SERVICE_API_PREFIX` (default: `/api/v1`).

Main groups:

- `POST /runs`
- `GET /runs/{run_id}`
- `GET /runs/{run_id}/attempts`
- `GET /runs/{run_id}/result`
- `POST /runs/{run_id}/cancel`
- `GET /runs/{run_id}/events`
- `GET /runs/{run_id}/artifacts`
- `GET /runs/{run_id}/artifacts/{artifact_id}/content`

- `POST /sessions`
- `GET /sessions`
- `POST /sessions/{session_id}/messages`
- `GET /sessions/{session_id}/history`
- `GET /sessions/{session_id}/status`
- `GET /sessions/{session_id}/diff`
- `POST /sessions/{session_id}/commands`
- `GET /sessions/{session_id}/stream`

- `GET /policy/tools`
- `GET /policy/approvals`
- `POST /policy/approvals/{approval_id}/decision`
- `GET /policy/audit`

- `POST /platform/steps/scan-steps`
- `GET /platform/steps`
- `POST /platform/feature/generate-feature`
- `POST /platform/feature/apply-feature`
- `POST /platform/tools/find-steps`
- `POST /platform/tools/compose-autotest`
- `POST /platform/tools/explain-unmapped`
- `POST /platform/memory/feedback`
- `GET|POST|PATCH|DELETE /platform/memory/rules*`
- `GET|POST|PATCH|DELETE /platform/memory/templates*`
- `POST /platform/memory/resolve-preview`

Internal tool-host API:

- `GET /internal/tools/registry`
- `POST /internal/tools/repo/read`
- `POST /internal/tools/patch/propose`
- `POST /internal/tools/patch/apply`
- `POST /internal/tools/artifacts/put`
- `POST /internal/tools/artifacts/get`

## Main Flows

### Runs-first feature generation

1. `POST /runs` with `plugin=testgen`
2. Wait via `GET /runs/{run_id}/events` or poll `GET /runs/{run_id}`
3. Fetch the result from `GET /runs/{run_id}/result`
4. Resolve artifacts from `GET /runs/{run_id}/artifacts`

If the result is not ready yet, `/runs/{run_id}/result` returns `409`.

### Free-text autotest via sessions

1. Create a session with `POST /sessions`
2. Send a message like `create autotest SCBC-T123`
3. The session runtime detects `testgen` intent and creates a run
4. Progress appears in session SSE and run SSE
5. Approval decisions go through `/policy/approvals/{approval_id}/decision`

## Run Modes

### Single-process

```powershell
$env:AGENT_SERVICE_STATE_BACKEND='memory'
$env:AGENT_SERVICE_EXECUTION_BACKEND='local'
$env:AGENT_SERVICE_TOOL_HOST_MODE='local'
agent-service
```

### Split mode

Example with Postgres + RabbitMQ + remote tool host:

```powershell
$env:AGENT_SERVICE_STATE_BACKEND='postgres'
$env:AGENT_SERVICE_POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/agent_service'
$env:AGENT_SERVICE_EXECUTION_BACKEND='queue'
$env:AGENT_SERVICE_QUEUE_BACKEND='rabbitmq'
$env:AGENT_SERVICE_RABBITMQ_URL='amqp://guest:guest@127.0.0.1:5672/%2f'
$env:AGENT_SERVICE_TOOL_HOST_MODE='remote'
$env:AGENT_SERVICE_TOOL_HOST_URL='http://127.0.0.1:8001'
agent-service
agent-service-worker
agent-service-tool-host
```

Example with S3-compatible artifacts:

```powershell
$env:AGENT_SERVICE_ARTIFACT_STORAGE_BACKEND='s3'
$env:AGENT_SERVICE_ARTIFACT_S3_BUCKET='agent-service-artifacts'
$env:AGENT_SERVICE_ARTIFACT_S3_ENDPOINT_URL='http://127.0.0.1:9000'
$env:AGENT_SERVICE_ARTIFACT_S3_ACCESS_KEY_ID='minioadmin'
$env:AGENT_SERVICE_ARTIFACT_S3_SECRET_ACCESS_KEY='minioadmin'
```

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools
python -m pip install -e .
```

Optional dependencies by backend:

```powershell
python -m pip install redis
python -m pip install pika
python -m pip install "psycopg[binary]"
python -m pip install boto3
```

## Health

```powershell
curl http://127.0.0.1:8000/health
```

- startup: `503`, `status=initializing`
- ready: `200`, `status=ok`

## Local Agent Stack

For local `Agent` runtime development you only need two services:

- `agent-service`
- `claude-code-adapter`

If you installed the repo in editable mode, the adapter can also be started directly with:

```powershell
claude-code-adapter
```

If that command is not available yet, refresh the editable install once:

```powershell
python -m pip install -e .
```

Start both:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-local.ps1
```

Start both and run a quick delegated smoke test:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-local.ps1 -SmokeTest
```

Stop both:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\stop-local.ps1
```

The adapter is configured through `.env` and by default runs Claude Code in headless CLI mode with an embedded Anthropic-compatible gateway:

- `AGENT_SERVICE_AGENT_BACKEND_MODE=http`
- `AGENT_SERVICE_AGENT_ADAPTER_URL=http://127.0.0.1:8011`
- `CLAUDE_CODE_ADAPTER_RUNNER_TYPE=claude_code`
- `CLAUDE_CODE_ADAPTER_BINARY=claude.cmd` on Windows (`claude` on Unix-like hosts)
- `CLAUDE_CODE_ADAPTER_PERMISSION_PROFILE=workspace_write`

For delegated `Agent` runs, the production path is Claude Code headless CLI plus the embedded gateway:

- `claude_code` is the default delegated runner
- `raw_json_runner` remains only for fake/integration tests
- adapter startup runs a Claude Code preflight and surfaces the result at `GET /debug/runtime`

For deterministic local delegated runs, force GigaChat Pro explicitly:

- `CLAUDE_CODE_ADAPTER_MODEL_MODE=override`
- `CLAUDE_CODE_ADAPTER_MODEL_OVERRIDE=gigachat/GigaChat-2`

For delegated `Agent` runs, note:

- Claude Code talks to the adapter's embedded Anthropic-compatible gateway through `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN`
- the gateway authenticates to GigaChat using `GIGACHAT_ACCESS_TOKEN` or `GIGACHAT_CLIENT_ID`/`GIGACHAT_CLIENT_SECRET`
- `agent-service` LLM settings such as `AGENT_SERVICE_LLM_MODEL` do not automatically apply to delegated `Agent`
- startup and smoke checks require:
  - a runnable Claude Code binary
  - support for headless flags such as `-p`, `--output-format`, `--resume`, and `--session-id`
  - embedded gateway readiness
  - working GigaChat auth/token resolution
  - optional explicit model probe when you intentionally request `GET /debug/runtime?includeProbe=true`

The embedded gateway can bootstrap a short-lived `GIGACHAT_ACCESS_TOKEN` when `GIGACHAT_CLIENT_ID` and `GIGACHAT_CLIENT_SECRET` are present.

Config discovery for delegated `Agent` runs is project-aware:

- if `projectRoot/claude-code.json` exists, the adapter exports it as `CLAUDE_CODE_CONFIG`
- if `projectRoot/.claude-code/` exists, the adapter exports it as `CLAUDE_CODE_CONFIG_DIR`
- if you want to force a global config regardless of project root, set:
  - `CLAUDE_CODE_ADAPTER_CONFIG_FILE=<path-to-claude-code.json>`
  - `CLAUDE_CODE_ADAPTER_CONFIG_DIR=<path-to-.claude-code>`

`CLAUDE_CODE_CONFIG_DIR` alone is not a replacement for `claude-code.json`; it is only for the auxiliary `.claude-code` directory structure.

For deterministic local runs, the adapter also isolates `XDG_DATA_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME`, and `XDG_CONFIG_HOME` under `.agent/claude-code-adapter/xdg`. This avoids collisions with your global Claude Code state and prevents `readonly database` failures from leaking into delegated runs.

Runtime diagnostics are available from the adapter itself:

- `GET /health`
- `GET /debug/runtime`
- `GET /internal/anthropic/v1/models`
- `POST /internal/anthropic/v1/messages`
- `POST /internal/anthropic/v1/messages/count_tokens`

`/debug/runtime` reports:

- Claude Code binary resolution and CLI version
- preflight status and blocking issues
- gateway readiness, GigaChat auth readiness, and optional headless model probe status
- permission profile, allowed tools, and resolved model
- active project root, active `claude-code.json`, active `.claude-code` dir
- embedded gateway base URL used by Claude Code child processes

## Key Environment Variables

- `AGENT_SERVICE_API_PREFIX`
- `AGENT_SERVICE_STATE_BACKEND`
- `AGENT_SERVICE_POSTGRES_DSN`
- `AGENT_SERVICE_EXECUTION_BACKEND`
- `AGENT_SERVICE_QUEUE_BACKEND`
- `AGENT_SERVICE_QUEUE_NAME`
- `AGENT_SERVICE_REDIS_URL`
- `AGENT_SERVICE_RABBITMQ_URL`
- `AGENT_SERVICE_WORKER_CONCURRENCY`
- `AGENT_SERVICE_TOOL_HOST_MODE`
- `AGENT_SERVICE_TOOL_HOST_URL`
- `AGENT_SERVICE_ARTIFACT_STORAGE_BACKEND`
- `AGENT_SERVICE_ARTIFACT_S3_BUCKET`
- `AGENT_SERVICE_ARTIFACT_S3_ENDPOINT_URL`
- `AGENT_SERVICE_ARTIFACT_S3_REGION`
- `AGENT_SERVICE_ARTIFACT_S3_ACCESS_KEY_ID`
- `AGENT_SERVICE_ARTIFACT_S3_SECRET_ACCESS_KEY`
- `AGENT_SERVICE_ARTIFACT_S3_SESSION_TOKEN`
- `AGENT_SERVICE_ARTIFACT_S3_KEY_PREFIX`
- `AGENT_SERVICE_ARTIFACT_S3_PRESIGN_EXPIRY_S`
- `AGENT_SERVICE_ARTIFACT_S3_ADDRESSING_STYLE`

## Repository Layout

- `src/app`: startup, config, bootstrap, service entrypoints
- `src/api`: external API routes and schemas
- `src/chat`: session runtime and stores
- `src/policy`: tool registry, approvals, audit
- `src/runtime`: run service
- `src/tool_host`: tool-host service layer and DTOs
- `src/infrastructure`: persistence, queue, artifact/object storage, tool-host client
- `src/self_healing`: execution supervisor and orchestration integration
- `tests`: backend tests
- `ide-plugin`: IntelliJ plugin

Plugin details are in [ide-plugin/README.md](/C:/Users/BaguM/IdeaProjects/agent-service/ide-plugin/README.md). Architecture history is captured in [0001-runs-sessions-policy-platform.md](/C:/Users/BaguM/IdeaProjects/agent-service/docs/adr/0001-runs-sessions-policy-platform.md), and release-level notes are in [CHANGELOG.md](/C:/Users/BaguM/IdeaProjects/agent-service/CHANGELOG.md).

## Tests

Full backend suite:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m pytest -p no:cacheprovider
```

Plugin:

```powershell
.\ide-plugin\gradlew.bat -p ide-plugin compileKotlin --no-daemon
.\ide-plugin\gradlew.bat -p ide-plugin test --no-daemon
```

The plugin Gradle test task uses isolated IDEA sandbox paths, and the current pure unit tests run through JUnit4/Vintage to avoid IntelliJ JUnit5 `ThreadLeakTracker` startup failures in restricted Windows environments.

## Troubleshooting

### `Result is not ready` on `/runs/{run_id}/result`

The run is not terminal yet. Use `/runs/{run_id}` or `/runs/{run_id}/events`.

### `projectRoot is required` on `/platform/steps/scan-steps`

Check `projectRoot` in the request and verify the path exists.

### Queue or storage backend import errors

Install the matching optional package:

- Redis: `redis`
- RabbitMQ: `pika`
- Postgres: `psycopg[binary]`
- S3-compatible storage: `boto3`

## Security

- Do not commit secrets; use `.env` and `AGENT_SERVICE_*`
- Prefer CA bundles over disabling TLS verification

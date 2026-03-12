"""РўРѕС‡РєР° РІС…РѕРґР° РІ РїСЂРёР»РѕР¶РµРЅРёРµ agent-service."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
import warnings
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from agents import create_orchestrator
from api import router as api_router
from app.bootstrap import (
    create_artifact_store,
    create_agent_adapter_client,
    create_chat_state_store,
    create_job_dispatch_components,
    create_policy_store,
    create_run_state_store,
    create_tool_host_client,
)
from app.config import get_settings
from app.logging_config import LOG_LEVEL, get_logger, init_logging
from chat.memory_store import ChatMemoryStore
from chat.runtime import ChatAgentRuntime
from infrastructure.job_worker import JobQueueWorker
from infrastructure.task_registry import TaskRegistry
from policy import PolicyService
from runtime.agent_runtime import AgentRunDriver, AgentSessionRuntime
from runtime.run_service import RunService
from runtime.session_runtime import SessionRuntimeRegistry
from self_healing.supervisor import ExecutionSupervisor

warnings.filterwarnings(
    "ignore",
    message=r"flaml\.automl is not available.*",
    category=UserWarning,
    module="flaml",
)

settings = get_settings()
logger = get_logger(__name__)
orchestrator = None


async def _startup_app(app: FastAPI) -> None:
    """Инициализировать control-plane компоненты приложения."""

    init_logging()
    app.state.is_ready = False
    app.state.init_error = None

    logger.info("[Startup] Инициализация оркестратора")
    global orchestrator
    orchestrator = create_orchestrator(settings)
    app.state.orchestrator = orchestrator

    chat_memory_store = ChatMemoryStore(Path(settings.steps_index_dir).parent / "chat_memory")
    chat_state_store = create_chat_state_store(settings, chat_memory_store)
    run_state_store = create_run_state_store(settings)
    policy_store = create_policy_store(settings)
    artifact_store = create_artifact_store(settings)
    execution_supervisor = ExecutionSupervisor(
        orchestrator=orchestrator,
        run_state_store=run_state_store,
        artifact_store=artifact_store,
    )
    dispatch_components = create_job_dispatch_components(settings)
    tool_host_client = create_tool_host_client(settings)
    agent_adapter_client = create_agent_adapter_client(settings)
    task_registry = TaskRegistry()

    app.state.chat_memory_store = chat_memory_store
    app.state.chat_state_store = chat_state_store
    app.state.run_state_store = run_state_store
    app.state.policy_store = policy_store
    app.state.artifact_store = artifact_store
    app.state.execution_supervisor = execution_supervisor
    app.state.job_dispatcher = dispatch_components.dispatcher
    app.state.job_queue = dispatch_components.queue
    app.state.tool_host_client = tool_host_client
    app.state.agent_adapter_client = agent_adapter_client
    app.state.task_registry = task_registry

    chat_runtime = ChatAgentRuntime(
        memory_store=chat_memory_store,
        state_store=chat_state_store,
        llm_client=getattr(orchestrator, "llm_client", None),
        orchestrator=orchestrator,
        run_state_store=run_state_store,
        execution_supervisor=execution_supervisor,
        tool_host_client=tool_host_client,
    )
    policy_service = PolicyService(
        state_store=chat_state_store,
        store=policy_store,
    )
    chat_runtime.bind_policy_service(policy_service)
    plugin_drivers: dict[str, object] = {}
    runtime_registry = SessionRuntimeRegistry(state_store=chat_state_store)
    runtime_registry.register(chat_runtime)
    agent_runtime = None
    agent_run_driver = None
    if agent_adapter_client is not None:
        agent_run_driver = AgentRunDriver(
            adapter_client=agent_adapter_client,
            run_state_store=run_state_store,
            session_state_store=chat_state_store,
            policy_service=policy_service,
            artifact_store=artifact_store,
            poll_interval_ms=settings.agent_poll_interval_ms,
            max_poll_interval_ms=settings.agent_max_poll_interval_ms,
            event_page_size=settings.agent_event_page_size,
        )
        agent_runtime = AgentSessionRuntime(
            state_store=chat_state_store,
            run_state_store=run_state_store,
            adapter_client=agent_adapter_client,
        )
        runtime_registry.register(agent_runtime)
        plugin_drivers["agent"] = agent_run_driver
    app.state.run_service = RunService(
        run_state_store=run_state_store,
        supervisor=execution_supervisor,
        dispatcher=dispatch_components.dispatcher,
        task_registry=task_registry,
        plugin_drivers=plugin_drivers,
    )
    if agent_runtime is not None:
        agent_runtime.bind_run_service(app.state.run_service)
    policy_service.bind_decision_executor(
        lambda session_id, run_id, approval_id, decision: runtime_registry.resolve_session(session_id).process_tool_decision(
            session_id=session_id,
            run_id=run_id,
            permission_id=approval_id,
            decision=decision,
        )
    )
    policy_service.sync_tools(runtime_registry.all_tools())
    app.state.chat_runtime = chat_runtime
    app.state.agent_runtime = agent_runtime
    app.state.agent_run_driver = agent_run_driver
    app.state.policy_service = policy_service
    app.state.session_runtime_registry = runtime_registry

    if dispatch_components.queue is not None and settings.embedded_execution_worker:
        queue_worker = JobQueueWorker(
            queue=dispatch_components.queue,
            supervisor=execution_supervisor,
            run_state_store=run_state_store,
            concurrency=settings.worker_concurrency,
        )
        app.state.job_queue_worker = queue_worker
        app.state.embedded_worker_task = asyncio.create_task(queue_worker.run_forever())
        logger.info("[Startup] Embedded execution worker started")

    logger.info("[Startup] Оркестратор и control-plane компоненты созданы")


async def _shutdown_app(app: FastAPI) -> None:
    """Освободить фоновые задачи и внешние ресурсы."""

    embedded_worker_task = getattr(app.state, "embedded_worker_task", None)
    if embedded_worker_task is not None:
        embedded_worker_task.cancel()
        try:
            await embedded_worker_task
        except asyncio.CancelledError:
            pass

    embeddings_store = getattr(getattr(app.state, "orchestrator", None), "embeddings_store", None)
    if embeddings_store is not None and hasattr(embeddings_store, "close"):
        embeddings_store.close()

    logger.info("Сервис %s останавливается", settings.app_name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown via ASGI lifespan."""

    try:
        await _startup_app(app)
    except Exception as exc:  # pragma: no cover - ранняя инициализация
        app.state.init_error = f"Ошибка создания оркестратора: {exc}"
        logger.exception("[Startup] Не удалось создать оркестратор")
        yield
        await _shutdown_app(app)
        return

    init_steps = (
        ("Проверка учётных данных внешних сервисов", _validate_external_credentials),
        ("Предзагрузка индекса шагов", _preload_step_indexes),
        ("Прогрев эмбеддингового хранилища", _warm_embeddings_store),
    )

    for description, handler in init_steps:
        logger.info("[Startup] %s", description)
        try:
            handler(app, orchestrator)
            logger.info("[Startup] %s завершена успешно", description)
        except Exception as exc:  # pragma: no cover - ранняя инициализация
            app.state.init_error = f"{description}: {exc}"
            logger.exception("[Startup] Шаг инициализации завершился с ошибкой")
            break
    else:
        app.state.is_ready = True
        logger.info(
            "Сервис %s запущен на %s:%s и готов к работе",
            settings.app_name,
            settings.host,
            settings.port,
        )

    try:
        yield
    finally:
        await _shutdown_app(app)


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.middleware("http")
async def _request_id_middleware(request: Request, call_next):
    request_id = str(request.headers.get("X-Request-Id") or "").strip() or uuid.uuid4().hex
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    return response


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    """Логировать тело запроса при ошибках валидации."""

    raw_body = await request.body()
    body_preview = f"<{len(raw_body)} bytes>"
    if settings.log_request_bodies and raw_body:
        body_preview = raw_body[:512].decode("utf-8", errors="replace")

    logger.warning(
        "[Validation] %s %s: errors=%s | body=%s",
        request.method,
        request.url.path,
        exc.errors(),
        body_preview,
    )
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        response = JSONResponse(status_code=exc.status_code, content=detail)
        response.headers["X-Request-Id"] = getattr(request.state, "request_id", "")
        return response
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})


@app.get("/health", summary="Проверка доступности сервиса")
async def healthcheck() -> dict[str, str]:
    """Простой health-endpoint."""

    is_ready = getattr(app.state, "is_ready", False)
    error = getattr(app.state, "init_error", None)
    status = "ok" if is_ready else "initializing"

    payload = {"status": status, "service": settings.app_name}
    if error:
        payload["error"] = error

    if not is_ready:
        return JSONResponse(status_code=503, content=payload)

    return payload


app.include_router(api_router, prefix=settings.api_prefix)


def _validate_external_credentials(_: FastAPI, orchestrator) -> None:
    llm_client = getattr(orchestrator, "llm_client", None)
    if not llm_client:
        raise RuntimeError("LLM клиент не сконфигурирован и fallback не задан")

    logger.debug("[Startup] Проверка LLM credentials")
    if getattr(llm_client, "corp_mode", False):
        try:
            llm_client.validate_corp_config()  # type: ignore[attr-defined]
        except Exception as exc:
            raise RuntimeError("Corporate proxy settings are not configured correctly") from exc
        return

    has_credentials = bool(
        getattr(llm_client, "credentials", None) or getattr(llm_client, "access_token", None)
    )
    if not has_credentials:
        if not getattr(llm_client, "allow_fallback", False):
            raise RuntimeError("Учётные данные LLM не заданы или недоступны")
        logger.info(
            "[Startup] Учётные данные LLM отсутствуют, используется fallback режим"
        )
        return

    try:
        llm_client._ensure_credentials()  # type: ignore[attr-defined]
    except Exception as exc:
        raise RuntimeError("Учётные данные LLM не заданы или недоступны") from exc


def _preload_step_indexes(_: FastAPI, orchestrator) -> None:
    step_index_store = getattr(orchestrator, "step_index_store", None)
    if not step_index_store:
        logger.warning("[Startup] Хранилище индекса шагов не найдено")
        return

    index_dir: Path | None = getattr(step_index_store, "_index_dir", None)
    if not index_dir:
        logger.warning("[Startup] Каталог индекса шагов не задан")
        return

    index_dir.mkdir(parents=True, exist_ok=True)
    total_steps = 0
    for project_dir in index_dir.iterdir():
        if not project_dir.is_dir():
            continue

        steps_file = project_dir / "steps.json"
        if not steps_file.exists():
            continue

        try:
            data = json.loads(steps_file.read_text(encoding="utf-8"))
            total_steps += len(data.get("steps", []))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[Startup] Не удалось прочитать индекс %s: %s", steps_file, exc)

    logger.info("[Startup] Предзагружено шагов из индекса: %s", total_steps)


def _warm_embeddings_store(_: FastAPI, orchestrator) -> None:
    embeddings_store = getattr(orchestrator, "embeddings_store", None)
    if not embeddings_store:
        logger.warning("[Startup] Эмбеддинговое хранилище не найдено")
        return

    client = getattr(embeddings_store, "_client", None)
    if not client:
        logger.warning("[Startup] Клиент эмбеддингов не инициализирован")
        return

    try:
        collections = client.list_collections()
    except Exception as exc:  # pragma: no cover - внешнее хранилище
        raise RuntimeError("Не удалось инициализировать эмбеддинговое хранилище") from exc

    logger.info("[Startup] Эмбеддинговое хранилище готово, коллекций: %s", len(collections))


def main() -> None:
    """Запустить backend-сервис."""

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level=logging.getLevelName(LOG_LEVEL).lower(),
    )


if __name__ == "__main__":
    main()

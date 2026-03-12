from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.logging_config import init_logging
from claude_code_adapter_app.anthropic_gateway import AnthropicGatewayService
from claude_code_adapter_app.cli_runtime import ClaudeCodeCliRuntime
from claude_code_adapter_app.config import AdapterSettings, get_settings
from claude_code_adapter_app.errors import AdapterApiError, build_error_payload
from claude_code_adapter_app.routes import router as api_router
from claude_code_adapter_app.schemas import AdapterDebugRuntimeResponse
from claude_code_adapter_app.service import ClaudeCodeAdapterService
from claude_code_adapter_app.state_store import ClaudeCodeAdapterStateStore
from claude_code_adapter_app.process_supervisor import ClaudeCodeProcessSupervisor


def create_app(settings: AdapterSettings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    state_store = ClaudeCodeAdapterStateStore.from_settings(resolved)
    gateway_service = AnthropicGatewayService(settings=resolved)
    cli_runtime = ClaudeCodeCliRuntime(settings=resolved, gateway_service=gateway_service)
    supervisor = ClaudeCodeProcessSupervisor(
        settings=resolved,
        state_store=state_store,
        runtime=cli_runtime,
    )
    service = ClaudeCodeAdapterService(
        settings=resolved,
        state_store=state_store,
        process_supervisor=supervisor,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_logging()
        logging.getLogger().setLevel(getattr(logging, resolved.log_level, logging.INFO))
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        if resolved.default_model and not resolved.model_override:
            logging.getLogger(__name__).warning(
                "CLAUDE_CODE_ADAPTER_DEFAULT_MODEL is deprecated; use "
                "CLAUDE_CODE_ADAPTER_MODEL_MODE=override with CLAUDE_CODE_ADAPTER_MODEL_OVERRIDE."
            )
        logging.getLogger(__name__).info(
            "Claude Code adapter model resolution mode: %s",
            resolved.model_resolution_description(),
        )
        restarted = state_store.mark_inflight_runs_failed()
        if restarted:
            logging.getLogger(__name__).warning(
                "Marked %s in-flight Claude Code runs as failed after restart",
                len(restarted),
            )
        preflight = cli_runtime.preflight(force_refresh=True, include_probe=False)
        if bool(preflight.get("ready")):
            logging.getLogger(__name__).info(
                "Claude Code runtime preflight is ready (runner_type=%s, binary=%s, model=%s)",
                resolved.runner_type,
                preflight.get("resolved_binary") or resolved.binary,
                resolved.resolve_forced_model() or "config-managed",
            )
        else:
            logging.getLogger(__name__).warning(
                "Claude Code runtime preflight is blocked: %s",
                "; ".join(str(item.get("message") or "") for item in preflight.get("issues") or []) or "unknown reason",
            )
        try:
            yield
        finally:
            state_store.close()

    app = FastAPI(title="claude-code-adapter", lifespan=lifespan)
    app.state.adapter_settings = resolved
    app.state.anthropic_gateway_service = gateway_service
    app.state.claude_code_cli_runtime = cli_runtime
    app.state.claude_code_adapter_state_store = state_store
    app.state.claude_code_adapter_supervisor = supervisor
    app.state.claude_code_adapter_service = service

    @app.middleware("http")
    async def _request_id_middleware(request: Request, call_next):
        request_id = str(request.headers.get("X-Request-Id") or "").strip() or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    @app.exception_handler(AdapterApiError)
    async def _adapter_error_handler(request: Request, exc: AdapterApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=build_error_payload(
                code=exc.code,
                message=exc.message,
                retryable=exc.retryable,
                request_id=getattr(request.state, "request_id", None),
                details=exc.details,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=build_error_payload(
                code="validation_error",
                message="Request validation failed.",
                retryable=False,
                request_id=getattr(request.state, "request_id", None),
                details={"errors": exc.errors()},
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logging.getLogger(__name__).exception("Unhandled exception in claude-code-adapter", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=build_error_payload(
                code="internal_error",
                message="Unhandled adapter error.",
                retryable=False,
                request_id=getattr(request.state, "request_id", None),
                details={},
            ),
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "claude-code-adapter"}

    @app.get("/debug/runtime", response_model=AdapterDebugRuntimeResponse)
    async def debug_runtime(include_probe: bool = Query(default=False, alias="includeProbe")) -> AdapterDebugRuntimeResponse:
        snapshot = await asyncio.to_thread(cli_runtime.debug_snapshot, include_probe=include_probe)
        return AdapterDebugRuntimeResponse(
            runnerType=resolved.runner_type,
            modelResolution=resolved.model_resolution_description(),
            forcedModel=resolved.resolve_forced_model(),
            configuredBinary=snapshot.get("configured_binary") or resolved.binary,
            resolvedBinary=snapshot.get("resolved_binary"),
            cliVersion=snapshot.get("cli_version"),
            preflightReady=bool(snapshot.get("ready", True)),
            preflightStatus=str(snapshot.get("status") or "skipped"),
            preflightIssues=list(snapshot.get("issues") or []),
            headlessReady=snapshot.get("headless_ready"),
            gatewayReady=bool(snapshot.get("gateway_ready", True)),
            gatewayBaseUrl=str(snapshot.get("gateway_base_url") or resolved.gateway_base_url),
            gigachatAuthReady=bool(snapshot.get("gigachat_auth_ready", False)),
            permissionProfile=str(snapshot.get("permission_profile") or resolved.permission_profile),
            allowedTools=list(snapshot.get("allowed_tools") or resolved.allowed_tools),
            activeProjectRoot=snapshot.get("active_project_root"),
            activeConfigFile=snapshot.get("active_config_file"),
            activeConfigDir=snapshot.get("active_config_dir"),
            resolvedModel=snapshot.get("resolved_model"),
        )

    @app.get("/internal/anthropic/v1/models")
    async def anthropic_models(request: Request) -> dict[str, Any]:
        _authorize_gateway(request)
        return await asyncio.to_thread(gateway_service.list_models)

    @app.post("/internal/anthropic/v1/messages")
    async def anthropic_messages(request: Request) -> dict[str, Any]:
        _authorize_gateway(request)
        payload = await request.json()
        return await asyncio.to_thread(gateway_service.create_message, payload)

    @app.post("/internal/anthropic/v1/messages/count_tokens")
    async def anthropic_count_tokens(request: Request) -> dict[str, Any]:
        _authorize_gateway(request)
        payload = await request.json()
        return await asyncio.to_thread(gateway_service.count_tokens, payload)

    app.include_router(api_router)
    return app


def _authorize_gateway(request: Request) -> None:
    gateway_service: AnthropicGatewayService = request.app.state.anthropic_gateway_service
    gateway_service.authorize(
        authorization=request.headers.get("Authorization"),
        x_api_key=request.headers.get("X-Api-Key"),
    )


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "claude_code_adapter_app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()

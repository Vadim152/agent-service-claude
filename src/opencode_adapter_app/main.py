from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.logging_config import init_logging
from opencode_adapter_app.config import AdapterSettings, get_settings
from opencode_adapter_app.headless_server import OpenCodeHeadlessServer
from opencode_adapter_app.schemas import AdapterDebugRuntimeResponse
from opencode_adapter_app.process_supervisor import OpenCodeProcessSupervisor
from opencode_adapter_app.routes import router as runs_router
from opencode_adapter_app.service import OpenCodeAdapterService
from opencode_adapter_app.state_store import OpenCodeAdapterStateStore


def create_app(settings: AdapterSettings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    state_store = OpenCodeAdapterStateStore(max_events_per_run=resolved.max_events_per_run)
    headless_server = OpenCodeHeadlessServer(settings=resolved)
    supervisor = OpenCodeProcessSupervisor(settings=resolved, state_store=state_store, headless_server=headless_server)
    service = OpenCodeAdapterService(settings=resolved, state_store=state_store, process_supervisor=supervisor)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_logging()
        logging.getLogger().setLevel(getattr(logging, resolved.log_level, logging.INFO))
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        if resolved.default_model and not resolved.model_override:
            logging.getLogger(__name__).warning(
                "OPENCODE_ADAPTER_DEFAULT_MODEL is deprecated; use "
                "OPENCODE_ADAPTER_MODEL_MODE=override with OPENCODE_ADAPTER_MODEL_OVERRIDE."
            )
        logging.getLogger(__name__).info(
            "OpenCode adapter model resolution mode: %s",
            resolved.model_resolution_description(),
        )
        try:
            yield
        finally:
            headless_server.shutdown()

    app = FastAPI(title="opencode-adapter", lifespan=lifespan)
    app.state.adapter_settings = resolved
    app.state.opencode_headless_server = headless_server
    app.state.opencode_adapter_state_store = state_store
    app.state.opencode_adapter_supervisor = supervisor
    app.state.opencode_adapter_service = service

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "opencode-adapter"}

    @app.get("/debug/runtime", response_model=AdapterDebugRuntimeResponse)
    async def debug_runtime() -> AdapterDebugRuntimeResponse:
        snapshot = headless_server.debug_snapshot()
        return AdapterDebugRuntimeResponse(
            runnerType=resolved.runner_type,
            modelResolution=resolved.model_resolution_description(),
            forcedModel=resolved.resolve_forced_model(),
            baseUrl=snapshot["base_url"],
            serverRunning=snapshot["server_running"],
            serverReady=snapshot["server_ready"],
            activeProjectRoot=snapshot.get("active_project_root"),
            activeConfigFile=snapshot.get("active_config_file"),
            activeConfigDir=snapshot.get("active_config_dir"),
            resolvedProviders=snapshot.get("resolved_providers") or [],
            resolvedModel=snapshot.get("resolved_model"),
            rawConfig=snapshot.get("raw_config"),
            configError=snapshot.get("config_error"),
        )

    app.include_router(runs_router)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "opencode_adapter_app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()

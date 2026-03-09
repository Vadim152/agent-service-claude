from __future__ import annotations

from fastapi import APIRouter, Query, Request

from opencode_adapter_app.schemas import (
    AdapterApprovalDecisionRequest,
    AdapterApprovalDecisionResponse,
    AdapterRunCancelResponse,
    AdapterRunCreateRequest,
    AdapterRunCreateResponse,
    AdapterRunEventsResponse,
    AdapterRunStatusResponse,
    AdapterSessionCommandRequest,
    AdapterSessionCommandResponse,
    AdapterSessionDiffResponse,
    AdapterSessionDto,
    AdapterSessionEnsureRequest,
)


router = APIRouter(tags=["opencode-adapter"])
runs_router = APIRouter(prefix="/v1/runs", tags=["opencode-adapter"])
sessions_router = APIRouter(prefix="/v1/sessions", tags=["opencode-adapter"])


def _service(request: Request):
    return request.app.state.opencode_adapter_service


@runs_router.post("", response_model=AdapterRunCreateResponse)
async def create_run(payload: AdapterRunCreateRequest, request: Request) -> AdapterRunCreateResponse:
    return _service(request).create_run(payload)


@runs_router.get("/{backend_run_id}", response_model=AdapterRunStatusResponse)
async def get_run(backend_run_id: str, request: Request) -> AdapterRunStatusResponse:
    return _service(request).get_run(backend_run_id)


@runs_router.get("/{backend_run_id}/events", response_model=AdapterRunEventsResponse)
async def list_events(
    backend_run_id: str,
    request: Request,
    after: int = Query(default=0),
    limit: int = Query(default=200, ge=1, le=5_000),
) -> AdapterRunEventsResponse:
    return _service(request).list_events(backend_run_id, after=after, limit=limit)


@runs_router.post("/{backend_run_id}/cancel", response_model=AdapterRunCancelResponse)
async def cancel_run(backend_run_id: str, request: Request) -> AdapterRunCancelResponse:
    return _service(request).cancel_run(backend_run_id)


@runs_router.post(
    "/{backend_run_id}/approvals/{approval_id}",
    response_model=AdapterApprovalDecisionResponse,
)
async def submit_approval_decision(
    backend_run_id: str,
    approval_id: str,
    payload: AdapterApprovalDecisionRequest,
    request: Request,
) -> AdapterApprovalDecisionResponse:
    return _service(request).submit_approval_decision(backend_run_id, approval_id, payload)


@sessions_router.post("", response_model=AdapterSessionDto)
async def ensure_session(payload: AdapterSessionEnsureRequest, request: Request) -> AdapterSessionDto:
    return _service(request).ensure_session(payload)


@sessions_router.get("/{external_session_id}", response_model=AdapterSessionDto)
async def get_session(external_session_id: str, request: Request) -> AdapterSessionDto:
    return _service(request).get_session(external_session_id)


@sessions_router.post("/{external_session_id}/compact", response_model=AdapterSessionCommandResponse)
async def compact_session(external_session_id: str, request: Request) -> AdapterSessionCommandResponse:
    return _service(request).compact_session(external_session_id)


@sessions_router.get("/{external_session_id}/diff", response_model=AdapterSessionDiffResponse)
async def get_session_diff(external_session_id: str, request: Request) -> AdapterSessionDiffResponse:
    return _service(request).get_session_diff(external_session_id)


@sessions_router.post("/{external_session_id}/commands", response_model=AdapterSessionCommandResponse)
async def execute_session_command(
    external_session_id: str,
    payload: AdapterSessionCommandRequest,
    request: Request,
) -> AdapterSessionCommandResponse:
    return _service(request).execute_session_command(external_session_id, payload)


router.include_router(runs_router)
router.include_router(sessions_router)

"""Session API routes mirroring chat control-plane behavior under /sessions."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from api.chat_schemas import (
    ChatCommandRequest,
    ChatCommandResponse,
    ChatDiffFileDto,
    ChatDiffSummaryDto,
    ChatEventDto,
    ChatHistoryResponse,
    ChatLimitsDto,
    ChatMessageAcceptedResponse,
    ChatMessageDto,
    ChatMessageRequest,
    ChatPendingPermissionDto,
    ChatRiskDto,
    ChatSessionCreateRequest,
    ChatSessionCreateResponse,
    ChatSessionDiffResponse,
    ChatSessionListItemDto,
    ChatSessionsListResponse,
    ChatSessionStatusResponse,
    ChatTokenTotalsDto,
    ChatUsageTotalsDto,
)
from infrastructure.runtime_errors import ChatRuntimeError
from runtime.session_runtime import SessionRuntimeRegistry


router = APIRouter(prefix="/sessions", tags=["sessions"])


def _get_runtime_registry(request: Request) -> SessionRuntimeRegistry | None:
    return getattr(request.app.state, "session_runtime_registry", None)


def _get_runtime(request: Request):
    runtime = getattr(request.app.state, "chat_runtime", None)
    if not runtime:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Session runtime is not initialized",
        )
    return runtime


def _resolve_runtime_for_create(request: Request, runtime_name: str):
    registry = _get_runtime_registry(request)
    if registry is not None:
        return registry.get(runtime_name)
    runtime = _get_runtime(request)
    if runtime_name != "chat":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unsupported session runtime: {runtime_name}")
    return runtime


def _resolve_runtime_for_session(request: Request, session_id: str):
    registry = _get_runtime_registry(request)
    if registry is not None:
        try:
            return registry.resolve_session(session_id)
        except ChatRuntimeError as exc:
            raise _runtime_to_http_error(exc, request) from exc
    return _get_runtime(request)


def _runtime_to_http_error(exc: ChatRuntimeError, request: Request) -> HTTPException:
    if exc.code:
        return HTTPException(
            status_code=exc.status_code or status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "retryable": bool(exc.retryable),
                    "details": dict(exc.details or {}),
                    "requestId": exc.request_id or getattr(request.state, "request_id", None),
                }
            },
        )
    if exc.status_code == 404:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if exc.status_code == 422:
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))


def _parse_iso_datetime(value: str | None) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        return datetime.utcnow()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    return datetime.fromisoformat(raw)


def _build_risk(
    *,
    pending_permissions_count: int,
    files_changed: int,
    lines_changed: int,
    activity: str,
) -> ChatRiskDto:
    reasons: list[str] = []
    high = False
    medium = False

    if pending_permissions_count > 0:
        reasons.append(f"Pending approvals: {pending_permissions_count}")
        medium = True
    if files_changed > 5:
        reasons.append(f"Large file scope: {files_changed} files")
        high = True
    if lines_changed > 200:
        reasons.append(f"Large diff volume: {lines_changed} lines")
        high = True
    if lines_changed > 0 and not high:
        reasons.append(f"Diff lines: {lines_changed}")
        medium = True
    if activity == "error":
        reasons.append("Agent reported an error state")
        high = True

    if high:
        level = "high"
    elif medium:
        level = "medium"
    else:
        level = "low"
        reasons.append("No pending approvals or high-impact changes")

    return ChatRiskDto(level=level, reasons=reasons)


@router.post("", response_model=ChatSessionCreateResponse)
async def create_session(payload: ChatSessionCreateRequest, request: Request) -> ChatSessionCreateResponse:
    runtime = _resolve_runtime_for_create(request, payload.runtime)
    try:
        session = await runtime.create_session(
            project_root=payload.project_root,
            source=payload.source,
            profile=payload.profile,
            reuse_existing=payload.reuse_existing,
            zephyr_auth=payload.zephyr_auth.model_dump(by_alias=True, mode="json") if payload.zephyr_auth else None,
            jira_instance=payload.jira_instance,
        )
    except ChatRuntimeError as exc:
        raise _runtime_to_http_error(exc, request) from exc

    return ChatSessionCreateResponse(
        session_id=str(session["sessionId"]),
        created_at=_parse_iso_datetime(str(session["createdAt"])),
        runtime=str(session.get("runtime", payload.runtime)),
        reused=bool(session.get("reused", False)),
        memory_snapshot=session.get("memorySnapshot", {}),
    )


@router.get("", response_model=ChatSessionsListResponse)
async def list_sessions(request: Request, projectRoot: str, limit: int = 50) -> ChatSessionsListResponse:
    runtime = _resolve_runtime_for_create(request, "chat")
    bounded_limit = max(1, min(limit, 200))
    try:
        payload = await runtime.list_sessions(project_root=projectRoot, limit=bounded_limit)
    except ChatRuntimeError as exc:
        raise _runtime_to_http_error(exc, request) from exc

    items = [
        ChatSessionListItemDto(
            session_id=str(item.get("sessionId", "")),
            project_root=str(item.get("projectRoot", projectRoot)),
            source=str(item.get("source", "ide-plugin")),
            profile=str(item.get("profile", "quick")),
            runtime=str(item.get("runtime", "chat")),
            status=str(item.get("status", "active")),
            activity=str(item.get("activity", "idle")),
            current_action=str(item.get("currentAction", "Idle")),
            created_at=_parse_iso_datetime(str(item.get("createdAt"))),
            updated_at=_parse_iso_datetime(str(item.get("updatedAt"))),
            last_message_preview=str(item["lastMessagePreview"]) if item.get("lastMessagePreview") is not None else None,
            pending_permissions_count=int(item.get("pendingPermissionsCount", 0)),
        )
        for item in payload.get("items", [])
    ]
    return ChatSessionsListResponse(items=items, total=int(payload.get("total", len(items))))


@router.post("/{session_id}/messages", response_model=ChatMessageAcceptedResponse)
async def send_message(
    session_id: str,
    payload: ChatMessageRequest,
    request: Request,
) -> ChatMessageAcceptedResponse:
    runtime = _resolve_runtime_for_session(request, session_id)
    try:
        exists = await runtime.has_session(session_id)
        if not exists:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session not found: {session_id}")
        status_payload = await runtime.get_status(session_id=session_id)
    except ChatRuntimeError as exc:
        raise _runtime_to_http_error(exc, request) from exc

    activity = str(status_payload.get("activity", "idle")).strip().lower()
    if activity in {"busy", "waiting_permission", "retry"}:
        current_action = str(status_payload.get("currentAction", "Processing request")).strip() or "Processing request"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session is {activity}. Wait until it becomes idle before sending a new message. Action: {current_action}",
        )

    run_id = uuid.uuid4().hex

    async def _worker() -> None:
        await runtime.process_message(
            session_id=session_id,
            run_id=run_id,
            message_id=payload.message_id or uuid.uuid4().hex,
            content=payload.content,
        )

    registry = getattr(request.app.state, "task_registry", None)
    if registry is None:
        asyncio.create_task(_worker())
    else:
        registry.create_task(_worker(), source="sessions.message", metadata={"sessionId": session_id, "runId": run_id})

    return ChatMessageAcceptedResponse(session_id=session_id, run_id=run_id, accepted=True)


@router.get("/{session_id}/history", response_model=ChatHistoryResponse)
async def get_history(session_id: str, request: Request, limit: int = 200) -> ChatHistoryResponse:
    runtime = _resolve_runtime_for_session(request, session_id)
    try:
        history = await runtime.get_history(session_id=session_id, limit=limit)
    except ChatRuntimeError as exc:
        raise _runtime_to_http_error(exc, request) from exc

    return ChatHistoryResponse(
        session_id=history["sessionId"],
        project_root=history["projectRoot"],
        source=history.get("source", "ide-plugin"),
        profile=history.get("profile", "quick"),
        runtime=history.get("runtime", "chat"),
        status=history.get("status", "active"),
        messages=[ChatMessageDto.model_validate(item) for item in history.get("messages", [])],
        events=[ChatEventDto.model_validate(item) for item in history.get("events", [])],
        pending_permissions=[ChatPendingPermissionDto.model_validate(item) for item in history.get("pendingPermissions", [])],
        memory_snapshot=history.get("memorySnapshot", {}),
        updated_at=_parse_iso_datetime(history["updatedAt"]),
    )


@router.get("/{session_id}/status", response_model=ChatSessionStatusResponse)
async def get_status(session_id: str, request: Request) -> ChatSessionStatusResponse:
    runtime = _resolve_runtime_for_session(request, session_id)
    try:
        status_payload = await runtime.get_status(session_id=session_id)
        diff_payload = await runtime.get_diff(session_id=session_id)
    except ChatRuntimeError as exc:
        raise _runtime_to_http_error(exc, request) from exc

    summary = diff_payload.get("summary", {})
    pending_permissions_count = int(status_payload.get("pendingPermissionsCount", 0))
    risk = _build_risk(
        pending_permissions_count=pending_permissions_count,
        files_changed=int(summary.get("files", 0)),
        lines_changed=int(summary.get("additions", 0)) + int(summary.get("deletions", 0)),
        activity=str(status_payload.get("activity", "idle")),
    )
    tokens = status_payload.get("totals", {}).get("tokens", {})
    totals = ChatUsageTotalsDto(
        tokens=ChatTokenTotalsDto(
            input=int(tokens.get("input", 0)),
            output=int(tokens.get("output", 0)),
            reasoning=int(tokens.get("reasoning", 0)),
            cache_read=int(tokens.get("cacheRead", 0)),
            cache_write=int(tokens.get("cacheWrite", 0)),
        ),
        cost=float(status_payload.get("totals", {}).get("cost", 0.0)),
    )
    limits_payload = status_payload.get("limits", {})
    limits = ChatLimitsDto(
        context_window=limits_payload.get("contextWindow"),
        used=int(limits_payload.get("used", 0)),
        percent=limits_payload.get("percent"),
    )
    return ChatSessionStatusResponse(
        session_id=status_payload["sessionId"],
        runtime=str(status_payload.get("runtime", "chat")),
        activity=str(status_payload.get("activity", "idle")),
        current_action=str(status_payload.get("currentAction", "Idle")),
        last_event_at=_parse_iso_datetime(str(status_payload.get("lastEventAt"))),
        updated_at=_parse_iso_datetime(str(status_payload.get("updatedAt"))),
        pending_permissions_count=pending_permissions_count,
        active_run_id=status_payload.get("activeRunId"),
        active_run_status=status_payload.get("activeRunStatus"),
        active_run_backend=status_payload.get("activeRunBackend"),
        totals=totals,
        limits=limits,
        last_retry_message=status_payload.get("lastRetryMessage"),
        last_retry_attempt=int(status_payload["lastRetryAttempt"]) if status_payload.get("lastRetryAttempt") is not None else None,
        last_retry_at=_parse_iso_datetime(str(status_payload.get("lastRetryAt"))) if status_payload.get("lastRetryAt") else None,
        risk=risk,
    )


@router.get("/{session_id}/diff", response_model=ChatSessionDiffResponse)
async def get_diff(session_id: str, request: Request) -> ChatSessionDiffResponse:
    runtime = _resolve_runtime_for_session(request, session_id)
    try:
        diff_payload = await runtime.get_diff(session_id=session_id)
        status_payload = await runtime.get_status(session_id=session_id)
    except ChatRuntimeError as exc:
        raise _runtime_to_http_error(exc, request) from exc

    summary = ChatDiffSummaryDto(
        files=int(diff_payload.get("summary", {}).get("files", 0)),
        additions=int(diff_payload.get("summary", {}).get("additions", 0)),
        deletions=int(diff_payload.get("summary", {}).get("deletions", 0)),
    )
    risk = _build_risk(
        pending_permissions_count=int(status_payload.get("pendingPermissionsCount", 0)),
        files_changed=summary.files,
        lines_changed=summary.additions + summary.deletions,
        activity=str(status_payload.get("activity", "idle")),
    )
    return ChatSessionDiffResponse(
        session_id=diff_payload["sessionId"],
        runtime=str(diff_payload.get("runtime", "chat")),
        summary=summary,
        files=[ChatDiffFileDto.model_validate(item) for item in diff_payload.get("files", [])],
        updated_at=_parse_iso_datetime(str(diff_payload.get("updatedAt"))),
        risk=risk,
    )


@router.post("/{session_id}/commands", response_model=ChatCommandResponse)
async def execute_command(session_id: str, payload: ChatCommandRequest, request: Request) -> ChatCommandResponse:
    runtime = _resolve_runtime_for_session(request, session_id)
    try:
        command_result = await runtime.execute_command(session_id=session_id, command=payload.command)
        status_payload = await runtime.get_status(session_id=session_id)
        diff_payload = await runtime.get_diff(session_id=session_id)
    except ChatRuntimeError as exc:
        raise _runtime_to_http_error(exc, request) from exc

    risk = _build_risk(
        pending_permissions_count=int(status_payload.get("pendingPermissionsCount", 0)),
        files_changed=int(diff_payload.get("summary", {}).get("files", 0)),
        lines_changed=int(diff_payload.get("summary", {}).get("additions", 0)) + int(diff_payload.get("summary", {}).get("deletions", 0)),
        activity=str(status_payload.get("activity", "idle")),
    )
    return ChatCommandResponse(
        session_id=command_result.get("sessionId", session_id),
        command=str(command_result.get("command", payload.command)),
        accepted=bool(command_result.get("accepted", True)),
        result=command_result.get("result", {}),
        updated_at=_parse_iso_datetime(str(command_result.get("updatedAt"))),
        risk=risk,
    )


@router.get("/{session_id}/stream")
async def stream_events(session_id: str, request: Request, fromIndex: int = 0):
    runtime = _resolve_runtime_for_session(request, session_id)
    try:
        exists = await runtime.has_session(session_id)
    except ChatRuntimeError as exc:
        raise _runtime_to_http_error(exc, request) from exc
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session not found: {session_id}")

    async def _stream():
        try:
            async for chunk in runtime.stream_events(session_id=session_id, from_index=max(0, fromIndex)):
                if await request.is_disconnected():
                    return
                yield chunk
        except ChatRuntimeError as exc:
            payload = {"eventType": "error", "payload": {"message": str(exc)}}
            yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")

    return StreamingResponse(_stream(), media_type="text/event-stream")

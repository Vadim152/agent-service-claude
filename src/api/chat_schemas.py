"""Pydantic models for chat control-plane API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from api.schemas import ApiBaseModel
from api.schemas import ZephyrAuth


class ChatSessionCreateRequest(ApiBaseModel):
    project_root: str = Field(..., alias="projectRoot")
    source: str = Field(default="ide-plugin")
    profile: str = Field(default="quick")
    runtime: Literal["chat", "agent"] = "chat"
    reuse_existing: bool = Field(default=True, alias="reuseExisting")
    zephyr_auth: ZephyrAuth | None = Field(default=None, alias="zephyrAuth")
    jira_instance: str | None = Field(default=None, alias="jiraInstance")


class ChatSessionCreateResponse(ApiBaseModel):
    session_id: str = Field(..., alias="sessionId")
    created_at: datetime = Field(..., alias="createdAt")
    runtime: Literal["chat", "agent"] = "chat"
    reused: bool = False
    memory_snapshot: dict[str, Any] = Field(default_factory=dict, alias="memorySnapshot")


class ChatSessionListItemDto(ApiBaseModel):
    session_id: str = Field(..., alias="sessionId")
    project_root: str = Field(..., alias="projectRoot")
    source: str = "ide-plugin"
    profile: str = "quick"
    runtime: Literal["chat", "agent"] = "chat"
    status: str = "active"
    activity: str = "idle"
    current_action: str = Field(default="Idle", alias="currentAction")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    last_message_preview: str | None = Field(default=None, alias="lastMessagePreview")
    pending_permissions_count: int = Field(default=0, alias="pendingPermissionsCount")


class ChatSessionsListResponse(ApiBaseModel):
    items: list[ChatSessionListItemDto] = Field(default_factory=list)
    total: int = 0


class ChatMessageRequest(ApiBaseModel):
    message_id: str | None = Field(default=None, alias="messageId")
    role: Literal["user"] = "user"
    content: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class ChatMessageAcceptedResponse(ApiBaseModel):
    session_id: str = Field(..., alias="sessionId")
    run_id: str = Field(..., alias="runId")
    accepted: bool = True


class ChatToolDecisionRequest(ApiBaseModel):
    permission_id: str = Field(..., alias="permissionId")
    decision: Literal["approve_once", "approve_always", "reject"]


class ChatToolDecisionResponse(ApiBaseModel):
    session_id: str = Field(..., alias="sessionId")
    run_id: str = Field(..., alias="runId")
    accepted: bool = True


class ChatMessageDto(ApiBaseModel):
    message_id: str = Field(..., alias="messageId")
    role: str
    content: str
    run_id: str | None = Field(default=None, alias="runId")
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(..., alias="createdAt")


class ChatEventDto(ApiBaseModel):
    event_type: str = Field(..., alias="eventType")
    payload: dict[str, Any]
    created_at: datetime = Field(..., alias="createdAt")
    index: int


class ChatPendingPermissionDto(ApiBaseModel):
    permission_id: str = Field(..., alias="permissionId")
    title: str
    kind: str
    call_id: str | None = Field(default=None, alias="callId")
    message_id: str | None = Field(default=None, alias="messageId")
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(..., alias="createdAt")


class ChatHistoryResponse(ApiBaseModel):
    session_id: str = Field(..., alias="sessionId")
    project_root: str = Field(..., alias="projectRoot")
    source: str
    profile: str
    runtime: Literal["chat", "agent"] = "chat"
    status: str
    messages: list[ChatMessageDto] = Field(default_factory=list)
    events: list[ChatEventDto] = Field(default_factory=list)
    pending_permissions: list[ChatPendingPermissionDto] = Field(
        default_factory=list,
        alias="pendingPermissions",
    )
    memory_snapshot: dict[str, Any] = Field(default_factory=dict, alias="memorySnapshot")
    updated_at: datetime = Field(..., alias="updatedAt")


class ChatTokenTotalsDto(ApiBaseModel):
    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = Field(default=0, alias="cacheRead")
    cache_write: int = Field(default=0, alias="cacheWrite")


class ChatUsageTotalsDto(ApiBaseModel):
    tokens: ChatTokenTotalsDto = Field(default_factory=ChatTokenTotalsDto)
    cost: float = 0.0


class ChatLimitsDto(ApiBaseModel):
    context_window: int | None = Field(default=None, alias="contextWindow")
    used: int = 0
    percent: float | None = None


class ChatRiskDto(ApiBaseModel):
    level: Literal["low", "medium", "high"]
    reasons: list[str] = Field(default_factory=list)


class ChatSessionStatusResponse(ApiBaseModel):
    session_id: str = Field(..., alias="sessionId")
    runtime: Literal["chat", "agent"] = "chat"
    activity: str
    current_action: str = Field(..., alias="currentAction")
    last_event_at: datetime = Field(..., alias="lastEventAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    pending_permissions_count: int = Field(..., alias="pendingPermissionsCount")
    active_run_id: str | None = Field(default=None, alias="activeRunId")
    active_run_status: str | None = Field(default=None, alias="activeRunStatus")
    active_run_backend: str | None = Field(default=None, alias="activeRunBackend")
    totals: ChatUsageTotalsDto = Field(default_factory=ChatUsageTotalsDto)
    limits: ChatLimitsDto = Field(default_factory=ChatLimitsDto)
    last_retry_message: str | None = Field(default=None, alias="lastRetryMessage")
    last_retry_attempt: int | None = Field(default=None, alias="lastRetryAttempt")
    last_retry_at: datetime | None = Field(default=None, alias="lastRetryAt")
    risk: ChatRiskDto


class ChatDiffSummaryDto(ApiBaseModel):
    files: int = 0
    additions: int = 0
    deletions: int = 0


class ChatDiffFileDto(ApiBaseModel):
    file: str
    additions: int = 0
    deletions: int = 0
    before: str = ""
    after: str = ""


class ChatSessionDiffResponse(ApiBaseModel):
    session_id: str = Field(..., alias="sessionId")
    runtime: Literal["chat", "agent"] = "chat"
    summary: ChatDiffSummaryDto = Field(default_factory=ChatDiffSummaryDto)
    files: list[ChatDiffFileDto] = Field(default_factory=list)
    updated_at: datetime = Field(..., alias="updatedAt")
    risk: ChatRiskDto


class ChatCommandRequest(ApiBaseModel):
    command: Literal["compact", "abort", "status", "diff", "help"]


class ChatCommandResponse(ApiBaseModel):
    session_id: str = Field(..., alias="sessionId")
    command: str
    accepted: bool = True
    result: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(..., alias="updatedAt")
    risk: ChatRiskDto


__all__ = [
    "ChatSessionCreateRequest",
    "ChatSessionCreateResponse",
    "ChatSessionListItemDto",
    "ChatSessionsListResponse",
    "ChatMessageRequest",
    "ChatMessageAcceptedResponse",
    "ChatToolDecisionRequest",
    "ChatToolDecisionResponse",
    "ChatMessageDto",
    "ChatEventDto",
    "ChatPendingPermissionDto",
    "ChatHistoryResponse",
    "ChatTokenTotalsDto",
    "ChatUsageTotalsDto",
    "ChatLimitsDto",
    "ChatRiskDto",
    "ChatSessionStatusResponse",
    "ChatDiffSummaryDto",
    "ChatDiffFileDto",
    "ChatSessionDiffResponse",
    "ChatCommandRequest",
    "ChatCommandResponse",
]

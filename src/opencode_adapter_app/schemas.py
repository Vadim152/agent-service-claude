from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from api.schemas import ApiBaseModel


class AdapterRunCreateRequest(ApiBaseModel):
    run_id: str = Field(..., alias="runId")
    session_id: str | None = Field(default=None, alias="sessionId")
    project_root: str = Field(..., alias="projectRoot")
    prompt: str
    source: str | None = None
    profile: str | None = None
    backend_session_id: str | None = Field(default=None, alias="backendSessionId")
    policy_mode: str | None = Field(default=None, alias="policyMode")
    config_profile: str | None = Field(default=None, alias="configProfile")


class AdapterArtifactDto(ApiBaseModel):
    name: str
    media_type: str = Field(default="text/plain", alias="mediaType")
    uri: str | None = None
    content: str | None = None


class AdapterApprovalDto(ApiBaseModel):
    approval_id: str = Field(..., alias="approvalId")
    tool_name: str = Field(..., alias="toolName")
    title: str
    kind: str = "tool"
    risk_level: str = Field(default="high", alias="riskLevel")
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdapterRunStatusResponse(ApiBaseModel):
    backend_run_id: str = Field(..., alias="backendRunId")
    backend_session_id: str | None = Field(default=None, alias="backendSessionId")
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    current_action: str = Field(default="Queued", alias="currentAction")
    result: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    artifacts: list[AdapterArtifactDto] = Field(default_factory=list)
    pending_approvals: list[AdapterApprovalDto] = Field(default_factory=list, alias="pendingApprovals")
    totals: dict[str, Any] | None = None
    limits: dict[str, Any] | None = None
    created_at: datetime = Field(..., alias="createdAt")
    started_at: datetime | None = Field(default=None, alias="startedAt")
    finished_at: datetime | None = Field(default=None, alias="finishedAt")
    exit_code: int | None = Field(default=None, alias="exitCode")
    updated_at: datetime = Field(..., alias="updatedAt")


class AdapterRunCreateResponse(ApiBaseModel):
    backend_run_id: str = Field(..., alias="backendRunId")
    backend_session_id: str | None = Field(default=None, alias="backendSessionId")
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    current_action: str = Field(..., alias="currentAction")
    created_at: datetime = Field(..., alias="createdAt")
    started_at: datetime | None = Field(default=None, alias="startedAt")


class AdapterRunEventDto(ApiBaseModel):
    event_type: str = Field(..., alias="eventType")
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(..., alias="createdAt")
    index: int


class AdapterRunEventsResponse(ApiBaseModel):
    items: list[AdapterRunEventDto] = Field(default_factory=list)
    next_cursor: int = Field(..., alias="nextCursor")


class AdapterRunCancelResponse(ApiBaseModel):
    backend_run_id: str = Field(..., alias="backendRunId")
    status: str
    updated_at: datetime = Field(..., alias="updatedAt")


class AdapterApprovalDecisionRequest(ApiBaseModel):
    decision: Literal["approve", "deny"]


class AdapterApprovalDecisionResponse(ApiBaseModel):
    backend_run_id: str = Field(..., alias="backendRunId")
    approval_id: str = Field(..., alias="approvalId")
    decision: str
    status: str
    updated_at: datetime = Field(..., alias="updatedAt")


class AdapterDebugRuntimeResponse(ApiBaseModel):
    service: str = "opencode-adapter"
    runner_type: str = Field(..., alias="runnerType")
    resolution_mode: str = Field(..., alias="modelResolution")
    forced_model: str | None = Field(default=None, alias="forcedModel")
    base_url: str = Field(..., alias="baseUrl")
    server_running: bool = Field(..., alias="serverRunning")
    server_ready: bool = Field(..., alias="serverReady")
    active_project_root: str | None = Field(default=None, alias="activeProjectRoot")
    active_config_file: str | None = Field(default=None, alias="activeConfigFile")
    active_config_dir: str | None = Field(default=None, alias="activeConfigDir")
    resolved_providers: list[str] = Field(default_factory=list, alias="resolvedProviders")
    resolved_model: str | None = Field(default=None, alias="resolvedModel")
    raw_config: dict[str, Any] | None = Field(default=None, alias="rawConfig")
    config_error: str | None = Field(default=None, alias="configError")

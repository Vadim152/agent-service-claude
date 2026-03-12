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


class AdapterApprovalStatusDto(AdapterApprovalDto):
    status: Literal["pending", "approved", "denied"]
    updated_at: datetime = Field(..., alias="updatedAt")


class AdapterRunStatusResponse(ApiBaseModel):
    backend_run_id: str = Field(..., alias="backendRunId")
    backend_session_id: str | None = Field(default=None, alias="backendSessionId")
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    current_action: str = Field(default="Queued", alias="currentAction")
    result: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    artifacts: list[AdapterArtifactDto] = Field(default_factory=list)
    pending_approvals: list[AdapterApprovalDto] = Field(default_factory=list, alias="pendingApprovals")
    approvals: list[AdapterApprovalStatusDto] = Field(default_factory=list)
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
    has_more: bool = Field(default=False, alias="hasMore")


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


class AdapterSessionEnsureRequest(ApiBaseModel):
    external_session_id: str = Field(..., alias="externalSessionId")
    project_root: str = Field(..., alias="projectRoot")
    source: str | None = None
    profile: str | None = None


class AdapterSessionDto(ApiBaseModel):
    external_session_id: str = Field(..., alias="externalSessionId")
    backend_session_id: str | None = Field(default=None, alias="backendSessionId")
    project_root: str = Field(..., alias="projectRoot")
    last_backend_run_id: str | None = Field(default=None, alias="lastBackendRunId")
    status: str = "idle"
    current_action: str = Field(default="Idle", alias="currentAction")
    last_activity_at: datetime | None = Field(default=None, alias="lastActivityAt")
    last_compaction_at: datetime | None = Field(default=None, alias="lastCompactionAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    last_provider_id: str | None = Field(default=None, alias="lastProviderId")
    last_model_id: str | None = Field(default=None, alias="lastModelId")


class AdapterSessionDiffResponse(ApiBaseModel):
    external_session_id: str = Field(..., alias="externalSessionId")
    backend_session_id: str | None = Field(default=None, alias="backendSessionId")
    summary: dict[str, Any] = Field(default_factory=dict)
    files: list[dict[str, Any]] = Field(default_factory=list)
    stale: bool = False
    updated_at: datetime = Field(..., alias="updatedAt")


class AdapterSessionCommandRequest(ApiBaseModel):
    command: Literal["status", "diff", "compact", "abort", "help"]


class AdapterSessionCommandResponse(ApiBaseModel):
    external_session_id: str = Field(..., alias="externalSessionId")
    command: str
    accepted: bool = True
    result: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(..., alias="updatedAt")


class AdapterDebugRuntimeResponse(ApiBaseModel):
    service: str = "claude-code-adapter"
    runner_type: str = Field(..., alias="runnerType")
    resolution_mode: str = Field(..., alias="modelResolution")
    forced_model: str | None = Field(default=None, alias="forcedModel")
    configured_binary: str = Field(..., alias="configuredBinary")
    resolved_binary: str | None = Field(default=None, alias="resolvedBinary")
    cli_version: str | None = Field(default=None, alias="cliVersion")
    preflight_ready: bool = Field(default=True, alias="preflightReady")
    preflight_status: str = Field(default="skipped", alias="preflightStatus")
    preflight_issues: list[dict[str, Any]] = Field(default_factory=list, alias="preflightIssues")
    headless_ready: bool | None = Field(default=None, alias="headlessReady")
    gateway_ready: bool = Field(default=True, alias="gatewayReady")
    gateway_base_url: str = Field(..., alias="gatewayBaseUrl")
    gigachat_auth_ready: bool = Field(default=False, alias="gigachatAuthReady")
    permission_profile: str = Field(default="workspace_write", alias="permissionProfile")
    allowed_tools: list[str] = Field(default_factory=list, alias="allowedTools")
    active_project_root: str | None = Field(default=None, alias="activeProjectRoot")
    active_config_file: str | None = Field(default=None, alias="activeConfigFile")
    active_config_dir: str | None = Field(default=None, alias="activeConfigDir")
    resolved_model: str | None = Field(default=None, alias="resolvedModel")


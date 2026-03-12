"""Schemas for the new runs control-plane API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from api.schemas import ApiBaseModel, FeatureResultDto, RunAttemptDto


class RunCreateRequest(ApiBaseModel):
    project_root: str = Field(..., alias="projectRoot")
    plugin: Literal["testgen", "ift", "debug", "browser", "analytics", "agent"]
    input: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = Field(default=None, alias="sessionId")
    profile: str = "quick"
    source: str = "api"
    priority: str = "normal"


class RunCreateResponse(ApiBaseModel):
    run_id: str = Field(..., alias="runId")
    status: str
    session_id: str | None = Field(default=None, alias="sessionId")
    plugin: str


class RunStatusResponse(ApiBaseModel):
    run_id: str = Field(..., alias="runId")
    session_id: str | None = Field(default=None, alias="sessionId")
    plugin: str
    runtime: str | None = None
    backend: str | None = None
    status: str
    source: str | None = None
    current_attempt: int = Field(default=0, alias="currentAttempt")
    execution_id: str | None = Field(default=None, alias="executionId")
    backend_run_id: str | None = Field(default=None, alias="backendRunId")
    backend_session_id: str | None = Field(default=None, alias="backendSessionId")
    last_synced_at: datetime | None = Field(default=None, alias="lastSyncedAt")
    incident_uri: str | None = Field(default=None, alias="incidentUri")
    started_at: datetime | None = Field(default=None, alias="startedAt")
    finished_at: datetime | None = Field(default=None, alias="finishedAt")


class RunEventDto(ApiBaseModel):
    event_type: str = Field(..., alias="eventType")
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(..., alias="createdAt")
    index: int


class RunCancelResponse(ApiBaseModel):
    run_id: str = Field(..., alias="runId")
    status: str
    cancel_requested: bool = Field(default=False, alias="cancelRequested")
    effective_status: str | None = Field(default=None, alias="effectiveStatus")


class RunAttemptsResponse(ApiBaseModel):
    run_id: str = Field(..., alias="runId")
    plugin: str
    attempts: list[RunAttemptDto] = Field(default_factory=list)


class RunArtifactDto(ApiBaseModel):
    artifact_id: str | None = Field(default=None, alias="artifactId")
    name: str
    uri: str
    attempt_id: str | None = Field(default=None, alias="attemptId")
    media_type: str | None = Field(default=None, alias="mediaType")
    size: int | None = None
    checksum: str | None = None
    connector_source: str | None = Field(default=None, alias="connectorSource")
    storage_backend: str | None = Field(default=None, alias="storageBackend")
    storage_path: str | None = Field(default=None, alias="storagePath")
    storage_bucket: str | None = Field(default=None, alias="storageBucket")
    storage_key: str | None = Field(default=None, alias="storageKey")
    signed_url: str | None = Field(default=None, alias="signedUrl")
    content: str | None = None


class RunArtifactsResponse(ApiBaseModel):
    run_id: str = Field(..., alias="runId")
    items: list[RunArtifactDto] = Field(default_factory=list)


class RunResultResponse(ApiBaseModel):
    run_id: str = Field(..., alias="runId")
    session_id: str | None = Field(default=None, alias="sessionId")
    plugin: str
    runtime: str | None = None
    backend: str | None = None
    status: str
    source: str | None = None
    backend_run_id: str | None = Field(default=None, alias="backendRunId")
    backend_session_id: str | None = Field(default=None, alias="backendSessionId")
    last_synced_at: datetime | None = Field(default=None, alias="lastSyncedAt")
    incident_uri: str | None = Field(default=None, alias="incidentUri")
    started_at: datetime | None = Field(default=None, alias="startedAt")
    finished_at: datetime | None = Field(default=None, alias="finishedAt")
    output: FeatureResultDto | dict[str, Any] | None = None
    attempts: list[RunAttemptDto] = Field(default_factory=list)
    artifacts: list[RunArtifactDto] = Field(default_factory=list)

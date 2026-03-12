"""Factories for control-plane and execution-plane components."""
from __future__ import annotations

from pathlib import Path

from app.config import Settings
from chat.postgres_state_store import PostgresChatStateStore
from chat.state_store import ChatStateStore
from infrastructure.artifact_index_store import InMemoryArtifactIndexStore, PostgresArtifactIndexStore
from infrastructure.artifact_store import ArtifactStore
from infrastructure.object_storage import LocalObjectStorage, S3ObjectStorage
from infrastructure.job_dispatcher import (
    DispatchComponents,
    JobExecutionDispatcher,
    LocalJobExecutionDispatcher,
    QueueJobExecutionDispatcher,
)
from infrastructure.job_queue import create_job_queue
from infrastructure.postgres_run_state_store import PostgresRunStateStore
from infrastructure.run_state_store import RunStateStore
from infrastructure.tool_host_client import RemoteToolHostClient
from policy import InMemoryPolicyStore, PostgresPolicyStore
from runtime.agent_adapter import HttpClaudeCodeAdapterClient


def create_run_state_store(settings: Settings):
    backend = (settings.state_backend or "memory").strip().lower()
    if backend == "memory":
        return RunStateStore()
    if backend == "postgres":
        return PostgresRunStateStore(dsn=str(settings.postgres_dsn))
    raise ValueError(f"Unsupported state backend: {settings.state_backend}")


def create_chat_state_store(settings: Settings, memory_store):
    backend = (settings.state_backend or "memory").strip().lower()
    if backend == "memory":
        return ChatStateStore(memory_store)
    if backend == "postgres":
        return PostgresChatStateStore(memory_store, dsn=str(settings.postgres_dsn))
    raise ValueError(f"Unsupported state backend: {settings.state_backend}")


def create_artifact_store(settings: Settings) -> ArtifactStore:
    return ArtifactStore(
        Path(settings.artifacts_dir),
        index_store=create_artifact_index_store(settings),
        object_storage=create_object_storage(settings),
    )


def create_artifact_index_store(settings: Settings):
    backend = (settings.state_backend or "memory").strip().lower()
    if backend == "memory":
        return InMemoryArtifactIndexStore()
    if backend == "postgres":
        return PostgresArtifactIndexStore(dsn=str(settings.postgres_dsn))
    raise ValueError(f"Unsupported state backend: {settings.state_backend}")


def create_object_storage(settings: Settings):
    backend = (settings.artifact_storage_backend or "local").strip().lower()
    if backend == "local":
        return LocalObjectStorage(Path(settings.artifacts_dir) / "published")
    if backend == "s3":
        return S3ObjectStorage(
            bucket=str(settings.artifact_s3_bucket),
            endpoint_url=settings.artifact_s3_endpoint_url,
            region=settings.artifact_s3_region,
            access_key_id=settings.artifact_s3_access_key_id,
            secret_access_key=settings.artifact_s3_secret_access_key,
            session_token=settings.artifact_s3_session_token,
            key_prefix=settings.artifact_s3_key_prefix,
            presign_expiry_s=settings.artifact_s3_presign_expiry_s,
            addressing_style=settings.artifact_s3_addressing_style,
        )
    raise ValueError(f"Unsupported artifact storage backend: {settings.artifact_storage_backend}")


def create_policy_store(settings: Settings):
    backend = (settings.state_backend or "memory").strip().lower()
    if backend == "memory":
        return InMemoryPolicyStore()
    if backend == "postgres":
        return PostgresPolicyStore(dsn=str(settings.postgres_dsn))
    raise ValueError(f"Unsupported state backend: {settings.state_backend}")


def create_job_dispatch_components(settings: Settings) -> DispatchComponents:
    execution_mode = (settings.execution_backend or "local").strip().lower()
    if execution_mode == "local":
        return DispatchComponents(dispatcher=LocalJobExecutionDispatcher(), queue=None)

    queue = create_job_queue(
        backend=settings.queue_backend,
        redis_url=settings.redis_url,
        rabbitmq_url=settings.rabbitmq_url,
        queue_name=settings.queue_name,
    )
    dispatcher: JobExecutionDispatcher = QueueJobExecutionDispatcher(queue=queue)
    return DispatchComponents(dispatcher=dispatcher, queue=queue)


def create_tool_host_client(settings: Settings):
    mode = (settings.tool_host_mode or "local").strip().lower()
    if mode == "local":
        return None
    if mode == "remote":
        return RemoteToolHostClient(base_url=str(settings.tool_host_url))
    raise ValueError(f"Unsupported tool host mode: {settings.tool_host_mode}")


def create_agent_adapter_client(settings: Settings):
    mode = (settings.agent_backend_mode or "disabled").strip().lower()
    if mode == "disabled":
        return None
    if mode == "http":
        return HttpClaudeCodeAdapterClient(
            base_url=str(settings.agent_adapter_url),
            timeout_s=float(settings.agent_request_timeout_s),
        )
    raise ValueError(f"Unsupported agent backend mode: {settings.agent_backend_mode}")

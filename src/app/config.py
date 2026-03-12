"""Application settings module."""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar

from dotenv import load_dotenv
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parent.parent.parent
ENV_PATH = ROOT_DIR / ".env"
load_dotenv(ENV_PATH, override=False)


class Settings(BaseSettings):
    """Main service settings."""

    _secret_fields: ClassVar[set[str]] = {
        "llm_api_key",
        "gigachat_client_secret",
        "gigachat_client_id",
        "corp_cert_file",
        "corp_key_file",
        "corp_ca_bundle_file",
    }

    model_config = SettingsConfigDict(
        env_prefix="AGENT_SERVICE_", env_file=ENV_PATH, case_sensitive=False, extra="ignore"
    )

    app_name: str = Field(default="agent-service", description="Service name")
    api_prefix: str = Field(default="/api/v1", description="HTTP API prefix")
    host: str = Field(default="127.0.0.1", description="Bind host")
    port: int = Field(default=8000, description="Bind port")
    log_request_bodies: bool = Field(default=False, description="Enable request body logging for diagnostics")
    steps_index_dir: Path = Field(default=ROOT_DIR / ".agent" / "steps_index", description="Path to steps index")
    artifacts_dir: Path = Field(
        default=ROOT_DIR / ".agent" / "artifacts",
        description="Directory for job artifacts and incidents",
    )
    artifact_storage_backend: str = Field(
        default="local",
        description="Artifact object storage backend: local|s3",
    )
    artifact_s3_bucket: str | None = Field(
        default=None,
        description="S3-compatible bucket used when artifact_storage_backend=s3",
    )
    artifact_s3_endpoint_url: str | None = Field(
        default=None,
        description="S3-compatible endpoint URL used for MinIO or custom object storage",
    )
    artifact_s3_region: str = Field(
        default="us-east-1",
        description="Region used for S3-compatible artifact storage",
    )
    artifact_s3_access_key_id: str | None = Field(
        default=None,
        description="Access key id for S3-compatible artifact storage",
    )
    artifact_s3_secret_access_key: str | None = Field(
        default=None,
        description="Secret access key for S3-compatible artifact storage",
    )
    artifact_s3_session_token: str | None = Field(
        default=None,
        description="Optional session token for S3-compatible artifact storage",
    )
    artifact_s3_key_prefix: str = Field(
        default="agent-service-artifacts",
        description="Object key prefix used for uploaded artifacts",
    )
    artifact_s3_presign_expiry_s: int = Field(
        default=3600,
        description="Presigned URL lifetime for resolved artifact access",
    )
    artifact_s3_addressing_style: str = Field(
        default="path",
        description="S3 addressing style: path|virtual",
    )
    state_backend: str = Field(
        default="memory",
        description="Control-plane state backend: memory|postgres",
    )
    execution_backend: str = Field(
        default="local",
        description="Execution dispatch backend: local|queue",
    )
    queue_backend: str = Field(
        default="local",
        description="Queue backend for execution dispatch: local|redis|rabbitmq",
    )
    queue_name: str = Field(
        default="agent-service:jobs",
        description="Queue name/channel used by execution plane",
    )
    redis_url: str = Field(
        default="redis://127.0.0.1:6379/0",
        description="Redis connection URL for queue backend",
    )
    rabbitmq_url: str = Field(
        default="amqp://guest:guest@127.0.0.1:5672/%2f",
        description="RabbitMQ AMQP URL for queue backend",
    )
    postgres_dsn: str | None = Field(
        default=None,
        description="Postgres DSN used when state_backend=postgres",
    )
    embedded_execution_worker: bool = Field(
        default=False,
        description="Run execution queue worker inside control-plane process",
    )
    worker_concurrency: int = Field(
        default=1,
        description="Number of concurrent queue consumers per worker process",
    )
    tool_host_mode: str = Field(
        default="local",
        description="Tool host mode: local|remote",
    )
    tool_host_url: str | None = Field(
        default=None,
        description="Base URL of remote tool host service when tool_host_mode=remote",
    )
    agent_backend_mode: str = Field(
        default="disabled",
        description="Delegated agent backend mode: disabled|http",
    )
    agent_adapter_url: str | None = Field(
        default=None,
        description="Base URL of the Claude Code adapter API when agent_backend_mode=http",
    )
    agent_request_timeout_s: float = Field(
        default=30.0,
        description="Timeout for delegated agent adapter requests in seconds",
    )
    agent_poll_interval_ms: int = Field(
        default=1_000,
        description="Polling interval for delegated agent runs",
    )
    agent_max_poll_interval_ms: int = Field(
        default=5_000,
        description="Maximum polling interval for delegated agent runs",
    )
    agent_event_page_size: int = Field(
        default=200,
        description="Suggested page size for delegated agent adapter event sync",
    )
    agent_runtime_name: str = Field(
        default="agent",
        description="Runtime name exposed to clients for delegated agent sessions",
    )
    agent_default_profile: str = Field(
        default="agent",
        description="Default profile used for delegated agent sessions",
    )
    jira_source_mode: str = Field(
        default="stub",
        description="Source mode for Jira testcase retrieval: stub|live|disabled",
    )
    jira_request_timeout_s: int = Field(
        default=20,
        description="Timeout for Jira testcase HTTP requests in seconds",
    )
    jira_default_instance: str | None = Field(
        default="https://jira.sberbank.ru",
        description="Default Jira instance URL for testcase retrieval",
    )
    jira_verify_ssl: bool = Field(
        default=True,
        description="Verify SSL certificates for Jira testcase retrieval",
    )
    jira_ca_bundle_file: str | None = Field(
        default=None,
        description="Optional CA bundle path for Jira TLS verification",
    )

    llm_endpoint: str | None = Field(default=None, description="LLM service endpoint")
    llm_api_key: str | None = Field(default=None, description="LLM API key")
    llm_model: str | None = Field(default=None, description="LLM model identifier")
    llm_api_version: str | None = Field(default=None, description="LLM API version")

    gigachat_client_id: str | None = Field(
        default=None,
        description="GigaChat client id",
        validation_alias=AliasChoices("GIGACHAT_CLIENT_ID", "AGENT_SERVICE_GIGACHAT_CLIENT_ID"),
    )
    gigachat_client_secret: str | None = Field(
        default=None,
        description="GigaChat client secret",
        validation_alias=AliasChoices("GIGACHAT_CLIENT_SECRET", "AGENT_SERVICE_GIGACHAT_CLIENT_SECRET"),
    )
    gigachat_scope: str = Field(
        default="GIGACHAT_API_PERS",
        description="OAuth scope for GigaChat",
        validation_alias=AliasChoices("GIGACHAT_SCOPE", "AGENT_SERVICE_GIGACHAT_SCOPE"),
    )
    gigachat_auth_url: str = Field(
        default="https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
        description="GigaChat auth endpoint",
        validation_alias=AliasChoices("GIGACHAT_AUTH_URL", "AGENT_SERVICE_GIGACHAT_AUTH_URL"),
    )
    gigachat_api_url: str = Field(
        default="https://gigachat.devices.sberbank.ru/api/v1",
        description="GigaChat API endpoint",
        validation_alias=AliasChoices("GIGACHAT_API_URL", "AGENT_SERVICE_GIGACHAT_API_URL"),
    )
    gigachat_verify_ssl: bool = Field(
        default=True,
        description="Verify SSL certificates for GigaChat",
        validation_alias=AliasChoices("GIGACHAT_VERIFY_SSL", "AGENT_SERVICE_GIGACHAT_VERIFY_SSL"),
    )
    corp_mode: bool = Field(
        default=False,
        description="Enable corporate proxy mode for chat completions with mTLS",
    )
    corp_proxy_host: str | None = Field(
        default=None,
        description="Corporate proxy host (scheme + host) without endpoint path",
    )
    corp_proxy_path: str = Field(
        default="/sbe-ai-pdlc-integration-code-generator/v1/chat/proxy/completions",
        description="Corporate proxy path for chat completions",
    )
    corp_model: str = Field(
        default="GigaChat-2-Max",
        description="Model name used in corporate proxy mode",
    )
    corp_cert_file: str | None = Field(
        default=None,
        description="Path to client certificate PEM/CRT for corporate proxy mTLS",
    )
    corp_key_file: str | None = Field(
        default=None,
        description="Path to client key file for corporate proxy mTLS",
    )
    corp_ca_bundle_file: str | None = Field(
        default=None,
        description="Optional CA bundle path for corporate TLS verification",
    )
    corp_request_timeout_s: float = Field(
        default=30.0,
        description="Timeout for corporate proxy requests in seconds",
    )
    corp_retry_attempts: int = Field(
        default=3,
        description="Max retry attempts for transient corporate proxy errors",
    )
    corp_retry_base_delay_s: float = Field(
        default=0.5,
        description="Base delay for exponential backoff between corporate proxy retries",
    )
    corp_retry_max_delay_s: float = Field(
        default=4.0,
        description="Maximum delay for corporate proxy retry backoff",
    )
    corp_retry_jitter_s: float = Field(
        default=0.2,
        description="Max random jitter added to each corporate proxy retry delay",
    )
    match_retrieval_top_k: int = Field(
        default=50,
        description="Top-K candidates fetched from embeddings store for step matching",
    )
    match_candidate_pool: int = Field(
        default=30,
        description="Candidate pool size after prefiltering before deterministic ranking",
    )
    match_threshold_exact: float = Field(
        default=0.8,
        description="Threshold for exact step match status",
    )
    match_threshold_fuzzy: float = Field(
        default=0.5,
        description="Threshold for fuzzy step match status",
    )
    match_min_seq_for_exact: float = Field(
        default=0.72,
        description="Minimum sequence score required for exact status",
    )
    match_ambiguity_gap: float = Field(
        default=0.08,
        description="Top1-top2 score gap below which LLM rerank can be invoked",
    )
    match_llm_min_score: float = Field(
        default=0.45,
        description="Lower boundary of score gray-zone for LLM rerank",
    )
    match_llm_max_score: float = Field(
        default=0.82,
        description="Upper boundary of score gray-zone for LLM rerank",
    )
    match_llm_shortlist: int = Field(
        default=5,
        description="Shortlist size passed to LLM during ambiguity rerank",
    )
    match_llm_min_confidence: float = Field(
        default=0.7,
        description="Minimum LLM confidence required to accept reranked candidate",
    )

    @model_validator(mode="after")
    def _validate_corporate_mode(self) -> "Settings":
        if self.corp_proxy_host:
            self.corp_proxy_host = self.corp_proxy_host.strip().rstrip("/")
        self.corp_proxy_path = "/" + self.corp_proxy_path.strip().lstrip("/")
        if self.corp_retry_attempts < 1:
            raise ValueError("corp_retry_attempts must be >= 1")
        if self.corp_retry_base_delay_s < 0:
            raise ValueError("corp_retry_base_delay_s must be >= 0")
        if self.corp_retry_max_delay_s < 0:
            raise ValueError("corp_retry_max_delay_s must be >= 0")
        if self.corp_retry_jitter_s < 0:
            raise ValueError("corp_retry_jitter_s must be >= 0")
        if self.corp_retry_max_delay_s < self.corp_retry_base_delay_s:
            raise ValueError("corp_retry_max_delay_s must be >= corp_retry_base_delay_s")
        if self.match_retrieval_top_k < 1:
            raise ValueError("match_retrieval_top_k must be >= 1")
        if self.match_candidate_pool < 1:
            raise ValueError("match_candidate_pool must be >= 1")
        if self.match_threshold_fuzzy < 0 or self.match_threshold_fuzzy > 1:
            raise ValueError("match_threshold_fuzzy must be in [0, 1]")
        if self.match_threshold_exact < 0 or self.match_threshold_exact > 1:
            raise ValueError("match_threshold_exact must be in [0, 1]")
        if self.match_threshold_exact < self.match_threshold_fuzzy:
            raise ValueError("match_threshold_exact must be >= match_threshold_fuzzy")
        if self.match_min_seq_for_exact < 0 or self.match_min_seq_for_exact > 1:
            raise ValueError("match_min_seq_for_exact must be in [0, 1]")
        if self.match_ambiguity_gap < 0:
            raise ValueError("match_ambiguity_gap must be >= 0")
        if self.match_llm_min_score < 0 or self.match_llm_min_score > 1:
            raise ValueError("match_llm_min_score must be in [0, 1]")
        if self.match_llm_max_score < 0 or self.match_llm_max_score > 1:
            raise ValueError("match_llm_max_score must be in [0, 1]")
        if self.match_llm_max_score < self.match_llm_min_score:
            raise ValueError("match_llm_max_score must be >= match_llm_min_score")
        if self.match_llm_shortlist < 1:
            raise ValueError("match_llm_shortlist must be >= 1")
        if self.match_llm_min_confidence < 0 or self.match_llm_min_confidence > 1:
            raise ValueError("match_llm_min_confidence must be in [0, 1]")
        if self.state_backend not in {"memory", "postgres"}:
            raise ValueError("state_backend must be one of: memory, postgres")
        if self.execution_backend not in {"local", "queue"}:
            raise ValueError("execution_backend must be one of: local, queue")
        if self.queue_backend not in {"local", "redis", "rabbitmq"}:
            raise ValueError("queue_backend must be one of: local, redis, rabbitmq")
        if not self.queue_name.strip():
            raise ValueError("queue_name must not be empty")
        if self.worker_concurrency < 1:
            raise ValueError("worker_concurrency must be >= 1")
        if self.state_backend == "postgres" and not (self.postgres_dsn or "").strip():
            raise ValueError("postgres_dsn is required when state_backend=postgres")
        if self.artifact_storage_backend not in {"local", "s3"}:
            raise ValueError("artifact_storage_backend must be one of: local, s3")
        if self.artifact_s3_presign_expiry_s < 1:
            raise ValueError("artifact_s3_presign_expiry_s must be >= 1")
        if self.artifact_s3_addressing_style not in {"path", "virtual"}:
            raise ValueError("artifact_s3_addressing_style must be one of: path, virtual")
        if self.artifact_storage_backend == "s3" and not (self.artifact_s3_bucket or "").strip():
            raise ValueError("artifact_s3_bucket is required when artifact_storage_backend=s3")
        if self.execution_backend == "queue":
            if self.queue_backend == "redis" and not self.redis_url.strip():
                raise ValueError("redis_url is required when queue_backend=redis")
            if self.queue_backend == "rabbitmq" and not self.rabbitmq_url.strip():
                raise ValueError("rabbitmq_url is required when queue_backend=rabbitmq")
        if self.tool_host_mode not in {"local", "remote"}:
            raise ValueError("tool_host_mode must be one of: local, remote")
        if self.tool_host_mode == "remote" and not (self.tool_host_url or "").strip():
            raise ValueError("tool_host_url is required when tool_host_mode=remote")
        if self.agent_backend_mode not in {"disabled", "http"}:
            raise ValueError("agent_backend_mode must be one of: disabled, http")
        if self.agent_backend_mode == "http" and not (self.agent_adapter_url or "").strip():
            raise ValueError("agent_adapter_url is required when agent_backend_mode=http")
        if self.agent_request_timeout_s <= 0:
            raise ValueError("agent_request_timeout_s must be > 0")
        if self.agent_poll_interval_ms < 100:
            raise ValueError("agent_poll_interval_ms must be >= 100")
        if self.agent_max_poll_interval_ms < self.agent_poll_interval_ms:
            raise ValueError("agent_max_poll_interval_ms must be >= agent_poll_interval_ms")
        if self.agent_event_page_size < 1:
            raise ValueError("agent_event_page_size must be >= 1")
        if not self.agent_runtime_name.strip():
            raise ValueError("agent_runtime_name must not be empty")
        if not self.agent_default_profile.strip():
            raise ValueError("agent_default_profile must not be empty")

        if not self.corp_mode:
            return self

        if not self.corp_proxy_host:
            raise ValueError("corp_proxy_host is required when corp_mode=true")
        if not self.corp_cert_file:
            raise ValueError("corp_cert_file is required when corp_mode=true")
        if not self.corp_key_file:
            raise ValueError("corp_key_file is required when corp_mode=true")

        return self

    def safe_model_dump(self) -> dict[str, Any]:
        """Return settings payload with secrets redacted for logging."""

        payload = self.model_dump()
        for field_name in self._secret_fields:
            if field_name in payload and getattr(self, field_name, None) is not None:
                payload[field_name] = "***"
        return payload


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached app settings."""

    settings = Settings()
    logging.getLogger(__name__).debug("Config loaded: %s", settings.safe_model_dump())
    return settings

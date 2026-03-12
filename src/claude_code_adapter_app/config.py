from __future__ import annotations

import json
import os
import secrets
import sys
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parent.parent.parent
ENV_PATH = ROOT_DIR / ".env"
load_dotenv(ENV_PATH, override=False)

_TOOL_PROFILES = {
    "readonly": ["Read", "Glob", "Grep", "Task", "TaskOutput", "TodoWrite", "Skill"],
    "workspace_write": [
        "Read",
        "Glob",
        "Grep",
        "Task",
        "TaskOutput",
        "TodoWrite",
        "Skill",
        "Edit",
        "Write",
        "NotebookEdit",
    ],
    "full_exec": [
        "Read",
        "Glob",
        "Grep",
        "Task",
        "TaskOutput",
        "TodoWrite",
        "Skill",
        "Edit",
        "Write",
        "NotebookEdit",
        "Bash",
        "WebFetch",
        "WebSearch",
    ],
}


def _default_claude_binary() -> str:
    return "claude.cmd" if sys.platform.startswith("win") else "claude"


def _default_gateway_token() -> str:
    return secrets.token_hex(24)


class AdapterSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLAUDE_CODE_ADAPTER_",
        env_file=ENV_PATH,
        case_sensitive=False,
        extra="ignore",
        protected_namespaces=(),
        populate_by_name=True,
    )

    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8011)
    log_level: str = Field(default="INFO")

    binary: str = Field(default_factory=_default_claude_binary)
    binary_args_json: str | None = Field(default=None)
    runner_type: str = Field(default="claude_code")
    default_agent: str = Field(default="agent")
    model_mode: str = Field(default="config")
    model_override: str | None = Field(default=None)
    default_model: str | None = Field(default=None)
    work_root: Path = Field(default=ROOT_DIR / ".agent" / "claude-code-adapter")
    state_backend: str = Field(default="sqlite")
    state_file: Path | None = Field(default=None)
    session_retention_hours: int = Field(default=720)
    inline_artifact_max_bytes: int = Field(default=65_536)
    graceful_kill_timeout_ms: int = Field(default=3_000)
    run_start_timeout_ms: int = Field(default=10_000)
    max_events_per_run: int = Field(default=5_000)
    config_file: str | None = Field(default=None)
    config_dir: str | None = Field(default=None)
    agent_map_json: str | None = Field(default=None)
    env_allowlist_json: str | None = Field(default=None)
    inherit_parent_env: bool = Field(default=True)
    gateway_token: str = Field(default_factory=_default_gateway_token)
    permission_profile: str = Field(default="workspace_write")
    allowed_tools_json: str | None = Field(default=None)
    permission_mode: str = Field(default="default")
    gigachat_access_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GIGACHAT_ACCESS_TOKEN"),
    )
    gigachat_client_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GIGACHAT_CLIENT_ID", "AGENT_SERVICE_GIGACHAT_CLIENT_ID"),
    )
    gigachat_client_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GIGACHAT_CLIENT_SECRET", "AGENT_SERVICE_GIGACHAT_CLIENT_SECRET"),
    )
    gigachat_scope: str = Field(
        default="GIGACHAT_API_PERS",
        validation_alias=AliasChoices("GIGACHAT_SCOPE", "AGENT_SERVICE_GIGACHAT_SCOPE"),
    )
    gigachat_auth_url: str = Field(
        default="https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
        validation_alias=AliasChoices("GIGACHAT_AUTH_URL", "AGENT_SERVICE_GIGACHAT_AUTH_URL"),
    )
    gigachat_api_url: str = Field(
        default="https://gigachat.devices.sberbank.ru/api/v1",
        validation_alias=AliasChoices("GIGACHAT_API_URL", "AGENT_SERVICE_GIGACHAT_API_URL"),
    )
    gigachat_verify_ssl: bool = Field(
        default=True,
        validation_alias=AliasChoices("GIGACHAT_VERIFY_SSL", "AGENT_SERVICE_GIGACHAT_VERIFY_SSL"),
    )

    @model_validator(mode="after")
    def _validate(self) -> "AdapterSettings":
        self.log_level = self.log_level.upper().strip()
        if self.log_level not in {"DEBUG", "INFO", "WARN", "ERROR"}:
            raise ValueError("log_level must be one of: DEBUG, INFO, WARN, ERROR")
        self.runner_type = self.runner_type.strip().lower()
        if self.runner_type not in {"claude_code", "raw_json_runner"}:
            raise ValueError("runner_type must be one of: claude_code, raw_json_runner")
        self.model_mode = self.model_mode.strip().lower()
        if self.model_mode not in {"config", "override"}:
            raise ValueError("model_mode must be one of: config, override")
        if self.model_override is not None:
            self.model_override = self.model_override.strip() or None
        if self.default_model is not None:
            self.default_model = self.default_model.strip() or None
        if self.model_mode == "override" and not (self.model_override or self.default_model):
            raise ValueError("model_override must be set when model_mode=override")
        self.state_backend = self.state_backend.strip().lower()
        if self.state_backend not in {"sqlite", "memory"}:
            raise ValueError("state_backend must be one of: sqlite, memory")
        self.permission_profile = self.permission_profile.strip().lower()
        if self.permission_profile not in _TOOL_PROFILES:
            raise ValueError(
                "permission_profile must be one of: "
                + ", ".join(sorted(_TOOL_PROFILES))
            )
        self.permission_mode = self.permission_mode.strip()
        if not self.permission_mode:
            raise ValueError("permission_mode must not be empty")
        if self.inline_artifact_max_bytes < 1:
            raise ValueError("inline_artifact_max_bytes must be >= 1")
        if self.graceful_kill_timeout_ms < 1:
            raise ValueError("graceful_kill_timeout_ms must be >= 1")
        if self.max_events_per_run < 1:
            raise ValueError("max_events_per_run must be >= 1")
        if self.session_retention_hours < 1:
            raise ValueError("session_retention_hours must be >= 1")
        if not self.binary.strip():
            raise ValueError("binary must not be empty")
        self.work_root = Path(self.work_root)
        self.work_root.mkdir(parents=True, exist_ok=True)
        if self.state_file is not None:
            self.state_file = Path(self.state_file)
        return self

    @property
    def binary_args(self) -> list[str]:
        return _parse_json_list(self.binary_args_json)

    @property
    def agent_map(self) -> dict[str, str]:
        data = _parse_json_object(self.agent_map_json)
        return {str(key): str(value) for key, value in data.items()}

    @property
    def env_allowlist(self) -> list[str]:
        configured = _parse_json_list(self.env_allowlist_json)
        if configured:
            return configured
        return [
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "COMSPEC",
            "OS",
            "HOME",
            "HOMEDRIVE",
            "HOMEPATH",
            "USERPROFILE",
            "USERNAME",
            "USERDOMAIN",
            "USERDOMAIN_ROAMINGPROFILE",
            "PUBLIC",
            "ALLUSERSPROFILE",
            "PROGRAMDATA",
            "PROGRAMFILES",
            "PROGRAMFILES(X86)",
            "TMP",
            "TEMP",
            "APPDATA",
            "LOCALAPPDATA",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_STATE_HOME",
            "XDG_CACHE_HOME",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
            "ALL_PROXY",
            "SSL_CERT_FILE",
            "REQUESTS_CA_BUNDLE",
            "CURL_CA_BUNDLE",
            "NODE_EXTRA_CA_CERTS",
            "NODE_OPTIONS",
            "GIGACHAT_ACCESS_TOKEN",
            "GIGACHAT_API_URL",
            "GIGACHAT_AUTH_URL",
            "GIGACHAT_SCOPE",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_API_KEY",
            "CLAUDE_CODE_CONFIG_DIR",
            "CLAUDE_CODE_CONFIG",
        ]

    @property
    def xdg_root(self) -> Path:
        root = self.work_root / "xdg"
        root.mkdir(parents=True, exist_ok=True)
        return root

    @property
    def resolved_state_file(self) -> Path | None:
        if self.state_backend == "memory":
            return None
        return Path(self.state_file) if self.state_file is not None else self.work_root / "state.sqlite3"

    @property
    def internal_host(self) -> str:
        host = self.host.strip()
        if host in {"", "0.0.0.0", "::"}:
            return "127.0.0.1"
        return host

    @property
    def internal_http_base_url(self) -> str:
        return f"http://{self.internal_host}:{self.port}"

    @property
    def gateway_base_url(self) -> str:
        return f"{self.internal_http_base_url}/internal/anthropic"

    @property
    def allowed_tools(self) -> list[str]:
        configured = _parse_json_list(self.allowed_tools_json)
        if configured:
            return configured
        return list(_TOOL_PROFILES[self.permission_profile])

    def xdg_env(self) -> dict[str, str]:
        mapping = {
            "XDG_DATA_HOME": self.xdg_root / "data",
            "XDG_STATE_HOME": self.xdg_root / "state",
            "XDG_CACHE_HOME": self.xdg_root / "cache",
            "XDG_CONFIG_HOME": self.xdg_root / "config",
        }
        for path in mapping.values():
            path.mkdir(parents=True, exist_ok=True)
        return {key: str(value) for key, value in mapping.items()}

    def build_child_env(
        self,
        *,
        project_root: str | Path | None = None,
        config_file: str | None = None,
        config_dir: str | None = None,
    ) -> dict[str, str]:
        if self.inherit_parent_env:
            env: dict[str, str] = dict(os.environ)
        else:
            env = {}
            for key in self.env_allowlist:
                value = os.environ.get(key)
                if value is not None:
                    env[key] = value
        env.update(self.xdg_env())
        resolved_config_file = config_file or self.resolve_claude_code_config_file(project_root)
        resolved_config_dir = config_dir or self.resolve_claude_code_config_dir(project_root)
        if resolved_config_file:
            env["CLAUDE_CODE_CONFIG"] = resolved_config_file
        else:
            env.pop("CLAUDE_CODE_CONFIG", None)
        if resolved_config_dir:
            env["CLAUDE_CODE_CONFIG_DIR"] = resolved_config_dir
        else:
            env.pop("CLAUDE_CODE_CONFIG_DIR", None)
        env["ANTHROPIC_BASE_URL"] = self.gateway_base_url
        env["ANTHROPIC_AUTH_TOKEN"] = self.gateway_token
        return env

    def resolve_claude_code_config_file(self, project_root: str | Path | None = None) -> str | None:
        if self.config_file:
            return self.config_file
        if project_root is None:
            return None
        root = Path(project_root)
        candidates = [
            root / "claude-code.json",
            root / ".claude-code" / "claude-code.json",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return None

    def resolve_claude_code_config_dir(self, project_root: str | Path | None = None) -> str | None:
        if self.config_dir:
            return self.config_dir
        if project_root is None:
            return None
        candidate = Path(project_root) / ".claude-code"
        if candidate.is_dir():
            return str(candidate)
        return None

    def resolve_forced_model(self) -> str | None:
        if self.model_override:
            return self.model_override
        if self.default_model:
            return self.default_model
        return None

    def model_resolution_description(self) -> str:
        forced = self.resolve_forced_model()
        if forced:
            source = "CLAUDE_CODE_ADAPTER_MODEL_OVERRIDE"
            if not self.model_override and self.default_model:
                source = "CLAUDE_CODE_ADAPTER_DEFAULT_MODEL"
            return f"override ({forced}) via {source}"
        return "config"


def _parse_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("Expected JSON list")
    return [str(item) for item in data]


def _parse_json_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object")
    return data


@lru_cache(maxsize=1)
def get_settings() -> AdapterSettings:
    settings = AdapterSettings()
    if settings.default_model and not settings.model_override:
        warnings.warn(
            "CLAUDE_CODE_ADAPTER_DEFAULT_MODEL is deprecated; use "
            "CLAUDE_CODE_ADAPTER_MODEL_MODE=override with CLAUDE_CODE_ADAPTER_MODEL_OVERRIDE instead.",
            DeprecationWarning,
            stacklevel=2,
        )
    return settings

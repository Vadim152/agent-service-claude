from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Iterator

import httpx

from opencode_adapter_app.config import AdapterSettings
from opencode_adapter_app.gigachat_auth import GigaChatAuthError, fetch_access_token


LOGGER = logging.getLogger(__name__)


class OpenCodeServerError(RuntimeError):
    pass


class OpenCodeHeadlessServer:
    def __init__(self, *, settings: AdapterSettings) -> None:
        self._settings = settings
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._stdout_handle = None
        self._stderr_handle = None
        self._active_project_root: str | None = None
        self._active_config_file: str | None = None
        self._active_config_dir: str | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self._settings.server_host}:{self._settings.server_port}"

    def debug_snapshot(self) -> dict[str, Any]:
        with self._lock:
            running = self._process is not None and self._process.poll() is None
            ready = running and self._is_ready()
            raw_config: dict[str, Any] | None = None
            config_error: str | None = None
            if ready:
                try:
                    payload = httpx.get(f"{self.base_url}/config", timeout=2.0)
                    payload.raise_for_status()
                    data = payload.json()
                    raw_config = _sanitize_debug_payload(data if isinstance(data, dict) else {"value": data})
                except Exception as exc:
                    config_error = str(exc)
            resolved_providers = _collect_provider_ids(raw_config)
            resolved_model = _extract_resolved_model(raw_config)
            if resolved_model is None:
                forced = self._settings.resolve_forced_model()
                if forced:
                    resolved_model = forced
            return {
                "base_url": self.base_url,
                "server_running": running,
                "server_ready": ready,
                "active_project_root": self._active_project_root,
                "active_config_file": self._active_config_file,
                "active_config_dir": self._active_config_dir,
                "resolved_providers": resolved_providers,
                "resolved_model": resolved_model,
                "raw_config": raw_config,
                "config_error": config_error,
            }

    def ensure_started(self, *, project_root: str | None = None) -> str:
        with self._lock:
            resolved_project_root = str(project_root) if project_root else None
            resolved_config_file = self._settings.resolve_opencode_config_file(resolved_project_root)
            resolved_config_dir = self._settings.resolve_opencode_config_dir(resolved_project_root)
            if (
                self._process is not None
                and self._process.poll() is None
                and self._is_ready()
                and self._active_config_file == resolved_config_file
                and self._active_config_dir == resolved_config_dir
            ):
                return self.base_url
            self._stop_locked()
            self._start_locked(
                project_root=resolved_project_root,
                config_file=resolved_config_file,
                config_dir=resolved_config_dir,
            )
            return self.base_url

    def shutdown(self) -> None:
        with self._lock:
            self._stop_locked()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        timeout_s: float = 30.0,
    ) -> Any:
        project_root = None
        if params and params.get("directory") is not None:
            project_root = str(params["directory"])
        self.ensure_started(project_root=project_root)
        try:
            response = httpx.request(
                method,
                f"{self.base_url}{path}",
                params=params,
                json=json_payload,
                timeout=timeout_s,
            )
            response.raise_for_status()
        except Exception as exc:
            raise OpenCodeServerError(f"OpenCode server request failed: {exc}") from exc
        if not response.content:
            return {}
        return response.json()

    def stream_events(
        self,
        *,
        directory: str,
        stop_event: threading.Event,
    ) -> Iterator[dict[str, Any]]:
        self.ensure_started(project_root=directory)
        timeout = httpx.Timeout(connect=5.0, read=1.0, write=5.0, pool=5.0)
        while not stop_event.is_set():
            try:
                with httpx.stream(
                    "GET",
                    f"{self.base_url}/event",
                    params={"directory": directory},
                    timeout=timeout,
                ) as response:
                    response.raise_for_status()
                    buffer: list[str] = []
                    for raw_line in response.iter_lines():
                        if stop_event.is_set():
                            return
                        line = raw_line.strip()
                        if not line:
                            payload = _decode_sse_chunk(buffer)
                            buffer = []
                            if payload is not None:
                                yield payload
                            continue
                        buffer.append(line)
                    payload = _decode_sse_chunk(buffer)
                    if payload is not None:
                        yield payload
            except httpx.ReadTimeout:
                continue
            except Exception as exc:
                if not stop_event.is_set():
                    raise OpenCodeServerError(f"OpenCode event stream failed: {exc}") from exc

    def _is_ready(self) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/config", timeout=2.0)
            return response.status_code == 200
        except Exception:
            return False

    def _start_locked(
        self,
        *,
        project_root: str | None,
        config_file: str | None,
        config_dir: str | None,
    ) -> None:
        log_dir = self._settings.work_root
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = log_dir / "opencode-serve.stdout.log"
        stderr_path = log_dir / "opencode-serve.stderr.log"
        self._stdout_handle = stdout_path.open("a", encoding="utf-8")
        self._stderr_handle = stderr_path.open("a", encoding="utf-8")
        command = [
            self._settings.binary,
            *self._settings.binary_args,
            "serve",
            "--hostname",
            self._settings.server_host,
            "--port",
            str(self._settings.server_port),
        ]
        if self._settings.print_logs:
            command.append("--print-logs")
        env = self._settings.build_child_env(
            project_root=project_root,
            config_file=config_file,
            config_dir=config_dir,
        )
        self._inject_gigachat_access_token(env)
        LOGGER.info(
            "Starting OpenCode headless server for project_root=%s config_file=%s config_dir=%s",
            project_root,
            config_file,
            config_dir,
        )
        self._process = subprocess.Popen(
            command,
            cwd=project_root or str(self._settings.work_root.parent),
            stdin=subprocess.DEVNULL,
            stdout=self._stdout_handle,
            stderr=self._stderr_handle,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        deadline = time.time() + max(5.0, self._settings.run_start_timeout_ms / 1000.0)
        while time.time() < deadline:
            if self._process.poll() is not None:
                raise OpenCodeServerError("OpenCode headless server exited before becoming ready")
            if self._is_ready():
                self._active_project_root = project_root
                self._active_config_file = config_file
                self._active_config_dir = config_dir
                return
            time.sleep(0.2)
        raise OpenCodeServerError("OpenCode headless server did not become ready in time")

    def _inject_gigachat_access_token(self, env: dict[str, str]) -> None:
        if env.get("GIGACHAT_ACCESS_TOKEN"):
            return
        if not (self._settings.gigachat_client_id and self._settings.gigachat_client_secret):
            return
        try:
            token = fetch_access_token(self._settings)
        except GigaChatAuthError as exc:
            LOGGER.warning("Failed to bootstrap GigaChat access token for OpenCode: %s", exc)
            return
        env["GIGACHAT_ACCESS_TOKEN"] = token
        env.setdefault("GIGACHAT_API_URL", self._settings.gigachat_api_url)
        LOGGER.info("Bootstrapped GigaChat access token for OpenCode provider")

    def _stop_locked(self) -> None:
        process = self._process
        self._process = None
        self._active_project_root = None
        self._active_config_file = None
        self._active_config_dir = None
        if process is not None and process.poll() is None:
            process.terminate()
            deadline = time.time() + (self._settings.graceful_kill_timeout_ms / 1000.0)
            while time.time() < deadline and process.poll() is None:
                time.sleep(0.05)
            if process.poll() is None:
                process.kill()
        for handle_name in ("_stdout_handle", "_stderr_handle"):
            handle = getattr(self, handle_name)
            if handle is not None:
                handle.close()
                setattr(self, handle_name, None)


def _decode_sse_chunk(lines: list[str]) -> dict[str, Any] | None:
    if not lines:
        return None
    data_lines: list[str] = []
    for line in lines:
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if not data_lines:
        return None
    raw = "\n".join(data_lines).strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _collect_provider_ids(payload: Any) -> list[str]:
    providers: set[str] = set()

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            mapping = value.get("providers")
            if isinstance(mapping, dict):
                for key in mapping:
                    providers.add(str(key))
            for key in ("providerID", "providerId", "provider", "id"):
                candidate = value.get(key)
                if key == "id" and str(value.get("type") or "").lower() != "provider":
                    continue
                if isinstance(candidate, str) and candidate.strip():
                    providers.add(candidate.strip())
            for nested in value.values():
                _walk(nested)
        elif isinstance(value, list):
            for item in value:
                _walk(item)

    _walk(payload)
    return sorted(providers)


def _extract_resolved_model(payload: Any) -> str | None:
    candidates: list[str] = []

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            provider_id = value.get("providerID") or value.get("providerId")
            model_id = value.get("modelID") or value.get("modelId")
            if isinstance(provider_id, str) and isinstance(model_id, str):
                provider_id = provider_id.strip()
                model_id = model_id.strip()
                if provider_id and model_id:
                    candidates.append(f"{provider_id}/{model_id}")
            model_value = value.get("model")
            if isinstance(model_value, str) and model_value.strip():
                candidates.append(model_value.strip())
            for nested in value.values():
                _walk(nested)
        elif isinstance(value, list):
            for item in value:
                _walk(item)

    _walk(payload)
    for candidate in candidates:
        if candidate:
            return candidate
    return None


def _sanitize_debug_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, nested in value.items():
            if _is_sensitive_key(key):
                sanitized[str(key)] = "***"
            else:
                sanitized[str(key)] = _sanitize_debug_payload(nested)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_debug_payload(item) for item in value]
    return value


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "").replace("_", "")
    if not normalized:
        return False
    markers = (
        "apikey",
        "accesstoken",
        "refreshtoken",
        "token",
        "secret",
        "password",
        "authorization",
        "clientsecret",
        "bearer",
    )
    return any(marker in normalized for marker in markers)

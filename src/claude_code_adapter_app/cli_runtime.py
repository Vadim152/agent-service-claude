from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from claude_code_adapter_app.anthropic_gateway import AnthropicGatewayService
from claude_code_adapter_app.config import AdapterSettings


class ClaudeCodeCliRuntime:
    def __init__(self, *, settings: AdapterSettings, gateway_service: AnthropicGatewayService) -> None:
        self._settings = settings
        self._gateway_service = gateway_service
        self._lock = RLock()
        self._preflight_cache_key: tuple[Any, ...] | None = None
        self._preflight_snapshot: dict[str, Any] | None = None

    def preflight(
        self,
        *,
        project_root: str | None = None,
        force_refresh: bool = False,
        include_probe: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            resolved_project_root = str(project_root) if project_root else None
            resolved_config_file = self._settings.resolve_claude_code_config_file(resolved_project_root)
            resolved_config_dir = self._settings.resolve_claude_code_config_dir(resolved_project_root)
            cache_key = (
                self._settings.runner_type,
                self._settings.binary,
                tuple(self._settings.binary_args),
                self._settings.resolve_forced_model(),
                resolved_project_root,
                resolved_config_file,
                resolved_config_dir,
                include_probe,
            )
            if not force_refresh and self._preflight_snapshot is not None and self._preflight_cache_key == cache_key:
                return dict(self._preflight_snapshot)
            snapshot = self._preflight_locked(
                project_root=resolved_project_root,
                config_file=resolved_config_file,
                config_dir=resolved_config_dir,
                include_probe=include_probe,
                force_refresh=force_refresh,
            )
            self._preflight_cache_key = cache_key
            self._preflight_snapshot = dict(snapshot)
            return dict(snapshot)

    def debug_snapshot(self, *, project_root: str | None = None, include_probe: bool = False) -> dict[str, Any]:
        snapshot = self.preflight(project_root=project_root, force_refresh=False, include_probe=include_probe)
        snapshot.update(
            {
                "active_project_root": project_root,
                "active_config_file": self._settings.resolve_claude_code_config_file(project_root),
                "active_config_dir": self._settings.resolve_claude_code_config_dir(project_root),
                "permission_profile": self._settings.permission_profile,
                "allowed_tools": list(self._settings.allowed_tools),
            }
        )
        return snapshot

    def build_child_env(self, *, project_root: str | None = None) -> dict[str, str]:
        return self._settings.build_child_env(project_root=project_root)

    def create_backend_session(self, *, external_session_id: str) -> str:
        raw = str(external_session_id or "").strip()
        if raw:
            try:
                return str(uuid.UUID(raw))
            except ValueError:
                pass
        return str(uuid.uuid4())

    def is_valid_backend_session_id(self, value: str | None) -> bool:
        raw = str(value or "").strip()
        if not raw:
            return False
        try:
            uuid.UUID(raw)
        except ValueError:
            return False
        return True

    def build_run_command(
        self,
        *,
        project_root: str,
        prompt: str,
        backend_session_id: str,
        reuse_session: bool,
    ) -> list[str]:
        command = [self._settings.binary, *self._settings.binary_args, "-p", "--verbose", "--output-format", "stream-json"]
        forced_model = self._settings.resolve_forced_model()
        if forced_model:
            command.extend(["--model", forced_model])
        if reuse_session:
            command.extend(["--resume", backend_session_id])
        else:
            command.extend(["--session-id", backend_session_id])
        command.extend(["--permission-mode", self._settings.permission_mode])
        if self._settings.allowed_tools:
            joined = ",".join(self._settings.allowed_tools)
            command.extend(["--tools", joined, "--allowedTools", joined])
        command.extend(["--add-dir", str(project_root), str(prompt)])
        return command

    def _preflight_locked(
        self,
        *,
        project_root: str | None,
        config_file: str | None,
        config_dir: str | None,
        include_probe: bool,
        force_refresh: bool,
    ) -> dict[str, Any]:
        gateway = self._gateway_service.health_snapshot(force_refresh=force_refresh)
        snapshot: dict[str, Any] = {
            "status": "skipped",
            "ready": True,
            "configured_binary": self._settings.binary,
            "resolved_binary": None,
            "cli_version": None,
            "headless_ready": None,
            "gateway_ready": bool(gateway.get("gatewayReady", True)),
            "gateway_base_url": self._settings.gateway_base_url,
            "gigachat_auth_ready": bool(gateway.get("gigachatAuthReady", False)),
            "resolved_model": gateway.get("resolvedModel") or self._settings.resolve_forced_model(),
            "issues": list(gateway.get("issues") or []),
            "checked_at": _utcnow(),
        }
        if self._settings.runner_type != "claude_code":
            return snapshot

        snapshot["status"] = "ready"
        resolved_binary = _resolve_binary(self._settings.binary)
        if resolved_binary:
            snapshot["resolved_binary"] = resolved_binary
        issue = _windows_binary_issue(self._settings.binary, resolved_binary)
        if issue is not None:
            snapshot["issues"].append(issue)
        if not resolved_binary:
            snapshot["issues"].append(
                _issue(
                    "binary_not_found",
                    f"Claude Code binary not found: {self._settings.binary}",
                    configuredBinary=self._settings.binary,
                )
            )
            return _block(snapshot)

        env = self._settings.build_child_env(
            project_root=project_root,
            config_file=config_file,
            config_dir=config_dir,
        )
        version_result = self._run_cli_probe(
            [resolved_binary, *self._settings.binary_args, "-v"],
            env=env,
            cwd=project_root,
            timeout_s=10.0,
        )
        version_output = _non_empty_text(version_result["stdout"]) or _non_empty_text(version_result["stderr"])
        if version_output:
            snapshot["cli_version"] = version_output
        if int(version_result["returncode"]) != 0:
            snapshot["issues"].append(
                _issue(
                    "binary_unusable",
                    "Claude Code binary did not start successfully.",
                    configuredBinary=self._settings.binary,
                    resolvedBinary=resolved_binary,
                    stderr=_trim_output(version_result["stderr"]),
                )
            )
            return _block(snapshot)

        help_result = self._run_cli_probe(
            [resolved_binary, *self._settings.binary_args, "-p", "--help"],
            env=env,
            cwd=project_root,
            timeout_s=10.0,
        )
        missing_flags = _missing_headless_flags(help_result)
        if missing_flags:
            snapshot["issues"].append(
                _issue(
                    "headless_flags_missing",
                    "Installed Claude Code CLI does not expose the required headless flags.",
                    configuredBinary=self._settings.binary,
                    resolvedBinary=resolved_binary,
                    missingFlags=missing_flags,
                    stdout=_trim_output(help_result["stdout"]),
                    stderr=_trim_output(help_result["stderr"]),
                )
            )
            return _block(snapshot)

        if not snapshot["gigachat_auth_ready"]:
            snapshot["issues"].append(
                _issue(
                    "gigachat_auth_failed",
                    "GigaChat authentication is not ready for the embedded Anthropic gateway.",
                    gatewayBaseUrl=self._settings.gateway_base_url,
                )
            )
            return _block(snapshot)

        if include_probe:
            model_probe = self._run_model_probe(
                resolved_binary=resolved_binary,
                env=env,
                cwd=project_root,
            )
            snapshot["headless_ready"] = bool(model_probe["ok"])
            if not model_probe["ok"]:
                snapshot["issues"].append(
                    _issue(
                        "model_probe_failed",
                        str(model_probe["message"] or "Claude Code model probe failed."),
                        forcedModel=self._settings.resolve_forced_model(),
                        stdout=_trim_output(model_probe.get("stdout")),
                        stderr=_trim_output(model_probe.get("stderr")),
                    )
                )
        else:
            snapshot["headless_ready"] = None

        if snapshot["issues"]:
            return _block(snapshot)
        snapshot["status"] = "ready"
        snapshot["ready"] = True
        return snapshot

    def _run_model_probe(
        self,
        *,
        resolved_binary: str,
        env: dict[str, str],
        cwd: str | None,
    ) -> dict[str, Any]:
        command = [
            resolved_binary,
            *self._settings.binary_args,
            "-p",
            "--no-session-persistence",
            "--output-format",
            "json",
        ]
        forced_model = self._settings.resolve_forced_model()
        if forced_model:
            command.extend(["--model", forced_model])
        command.append("Reply with exactly OK")
        probe = self._run_cli_probe(command, env=env, cwd=cwd, timeout_s=30.0)
        payload = _extract_json_payload(probe)
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "message": "Claude Code model probe did not return JSON output.",
                "stdout": probe["stdout"],
                "stderr": probe["stderr"],
            }
        if bool(payload.get("is_error")):
            return {
                "ok": False,
                "message": str(payload.get("result") or "Claude Code model probe returned an error."),
                "stdout": probe["stdout"],
                "stderr": probe["stderr"],
            }
        return {
            "ok": True,
            "message": str(payload.get("result") or "").strip(),
            "stdout": probe["stdout"],
            "stderr": probe["stderr"],
        }

    def _run_cli_probe(
        self,
        command: list[str],
        *,
        env: dict[str, str],
        cwd: str | None,
        timeout_s: float,
    ) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                command,
                cwd=cwd or str(self._settings.work_root.parent),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=timeout_s,
            )
        except FileNotFoundError as exc:
            return {"returncode": -1, "stdout": "", "stderr": str(exc)}
        except subprocess.TimeoutExpired as exc:
            return {
                "returncode": -1,
                "stdout": _coerce_text(exc.stdout),
                "stderr": _coerce_text(exc.stderr) or "Command timed out.",
            }
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout or "",
            "stderr": completed.stderr or "",
        }


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_binary(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_file():
        return str(candidate)
    return shutil.which(raw)


def _windows_binary_issue(configured_binary: str, resolved_binary: str | None) -> dict[str, Any] | None:
    if os.name != "nt":
        return None
    configured_name = Path(str(configured_binary or "").strip()).name.lower()
    resolved_name = Path(str(resolved_binary or "").strip()).name.lower()
    if configured_name == "claude":
        suggestion = shutil.which("claude.cmd")
        details: dict[str, Any] = {"configuredBinary": configured_binary}
        if suggestion:
            details["suggestedBinary"] = suggestion
        return _issue(
            "binary_windows_incompatible",
            "On Windows, set CLAUDE_CODE_ADAPTER_BINARY to `claude.cmd` or an explicit .cmd path.",
            **details,
        )
    if resolved_name.endswith(".ps1"):
        return _issue(
            "binary_windows_incompatible",
            "PowerShell launcher `.ps1` is not supported for subprocess execution. Use `claude.cmd` instead.",
            configuredBinary=configured_binary,
            resolvedBinary=resolved_binary,
        )
    return None


def _missing_headless_flags(result: dict[str, Any]) -> list[str]:
    combined = "\n".join(
        part for part in (str(result.get("stdout") or "").strip(), str(result.get("stderr") or "").strip()) if part
    )
    required_markers = {
        "--output-format": "--output-format",
        "--resume": "--resume",
        "--session-id": "--session-id",
        "--permission-mode": "--permission-mode",
    }
    missing: list[str] = []
    normalized = combined.lower()
    for flag, marker in required_markers.items():
        if marker not in normalized:
            missing.append(flag)
    return missing


def _extract_json_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    for raw in (result.get("stdout"), result.get("stderr")):
        payload = _parse_json_document(raw)
        if isinstance(payload, dict):
            return payload
    return None


def _parse_json_document(raw: Any) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def _non_empty_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _trim_output(value: Any, *, max_chars: int = 400) -> str | None:
    text = _non_empty_text(value)
    if text is None:
        return None
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _issue(code: str, message: str, **details: Any) -> dict[str, Any]:
    clean_details = {str(key): value for key, value in details.items() if value is not None}
    return {"code": code, "message": message, "details": clean_details}


def _block(snapshot: dict[str, Any]) -> dict[str, Any]:
    snapshot["status"] = "blocked"
    snapshot["ready"] = False
    return snapshot

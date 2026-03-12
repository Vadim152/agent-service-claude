from __future__ import annotations

import json
import logging
import subprocess
import threading
from pathlib import Path
from typing import Any, TextIO

from claude_code_adapter_app.cli_runtime import ClaudeCodeCliRuntime
from claude_code_adapter_app.event_parser import TERMINAL_STATUSES, parse_json_line
from claude_code_adapter_app.state_store import ClaudeCodeAdapterStateStore, utcnow
from claude_code_adapter_app.workspace_diff import collect_workspace_diff


LOGGER = logging.getLogger(__name__)


class ClaudeCodeProcessSupervisor:
    def __init__(
        self,
        *,
        settings: Any,
        state_store: ClaudeCodeAdapterStateStore,
        runtime: ClaudeCodeCliRuntime,
    ) -> None:
        self._settings = settings
        self._state_store = state_store
        self._runtime = runtime
        self._lock = threading.RLock()
        self._processes: dict[str, subprocess.Popen[str]] = {}

    @property
    def runtime(self) -> ClaudeCodeCliRuntime:
        return self._runtime

    def start_run(self, run: dict[str, Any]) -> None:
        worker = threading.Thread(
            target=self._run_process,
            args=(dict(run),),
            daemon=True,
            name=f"claude-code-{run['backend_run_id']}",
        )
        worker.start()

    def runtime_preflight(
        self,
        *,
        project_root: str | None = None,
        force_refresh: bool = False,
        include_probe: bool = True,
    ) -> dict[str, Any]:
        return self._runtime.preflight(
            project_root=project_root,
            force_refresh=force_refresh,
            include_probe=include_probe,
        )

    def cancel_run(self, backend_run_id: str) -> dict[str, Any]:
        run = self._state_store.get_run(backend_run_id) or {}
        self._state_store.patch_run(backend_run_id, cancel_requested=True, current_action="Cancelling")
        process = self._get_process(backend_run_id)
        if process is not None and process.poll() is None:
            process.terminate()
        updated = self._state_store.patch_run(
            backend_run_id,
            status="cancelled",
            current_action="Cancelled",
            finished_at=utcnow().isoformat(),
        )
        self._state_store.append_event(backend_run_id, "run.cancelled", {"backendRunId": backend_run_id})
        return updated or self._state_store.get_run(backend_run_id) or {}

    def submit_approval_decision(self, backend_run_id: str, approval_id: str, decision: str) -> dict[str, Any]:
        updated = self._state_store.resolve_approval(backend_run_id, approval_id, decision)
        self._state_store.append_event(
            backend_run_id,
            "approval.decision",
            {"backendRunId": backend_run_id, "approvalId": approval_id, "decision": decision},
        )
        return updated or self._state_store.get_run(backend_run_id) or {}

    def create_backend_session(self, *, project_root: str, external_session_id: str) -> dict[str, Any]:
        _ = project_root
        return {"id": self._runtime.create_backend_session(external_session_id=external_session_id)}

    def fetch_session_diff(self, *, project_root: str, backend_session_id: str) -> dict[str, Any]:
        _ = backend_session_id
        return collect_workspace_diff(project_root)

    def compact_session(
        self,
        *,
        project_root: str,
        backend_session_id: str,
        provider_id: str | None,
        model_id: str | None,
    ) -> dict[str, Any]:
        _ = (project_root, backend_session_id, provider_id, model_id)
        return {"status": "noop"}

    def _run_process(self, run: dict[str, Any]) -> None:
        backend_run_id = str(run["backend_run_id"])
        project_root = str(run["project_root"])
        work_dir = Path(str(run["work_dir"]))
        artifacts_dir = work_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        stream_path = work_dir / "stdout.stream.jsonl"
        stderr_path = work_dir / "stderr.log"
        result_path = work_dir / "result.json"
        meta_path = work_dir / "meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")

        backend_session_id = str(run.get("backend_session_id") or "").strip()
        if not backend_session_id:
            created = self.create_backend_session(
                project_root=project_root,
                external_session_id=str(run.get("external_session_id") or run.get("external_run_id") or ""),
            )
            backend_session_id = str(created.get("id") or "").strip()

        command = self._runtime.build_run_command(
            project_root=project_root,
            prompt=str(run.get("prompt") or ""),
            backend_session_id=backend_session_id,
            reuse_session=bool(str(run.get("backend_session_id") or "").strip()),
        )
        env = self._runtime.build_child_env(project_root=project_root)
        now = utcnow().isoformat()
        self._state_store.patch_run(
            backend_run_id,
            backend_session_id=backend_session_id,
            status="running",
            current_action="Claude Code started",
            started_at=now,
        )
        self._state_store.append_event(
            backend_run_id,
            "run.started",
            {"backendRunId": backend_run_id, "backendSessionId": backend_session_id},
        )

        if str(run.get("external_session_id") or "").strip():
            self._state_store.set_session_mapping(
                external_session_id=str(run["external_session_id"]),
                backend_session_id=backend_session_id,
                project_root=project_root,
                last_backend_run_id=backend_run_id,
            )

        with stream_path.open("w", encoding="utf-8") as stream_handle, stderr_path.open("w", encoding="utf-8") as stderr_handle:
            process = subprocess.Popen(
                command,
                cwd=project_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=stderr_handle,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            self._set_process(backend_run_id, process)
            terminal_payload: dict[str, Any] | None = None
            try:
                if process.stdout is not None:
                    for raw_line in process.stdout:
                        stream_handle.write(raw_line)
                        stream_handle.flush()
                        payload = parse_json_line(raw_line)
                        if not isinstance(payload, dict):
                            continue
                        terminal_payload = self._apply_cli_event(
                            backend_run_id=backend_run_id,
                            backend_session_id=backend_session_id,
                            payload=payload,
                            artifacts_dir=artifacts_dir,
                            result_path=result_path,
                        ) or terminal_payload
                exit_code = process.wait()
            except Exception as exc:
                LOGGER.exception("Claude Code runner crashed for %s", backend_run_id, exc_info=exc)
                exit_code = -1
                current = self._state_store.get_run(backend_run_id) or {}
                if str(current.get("status") or "") not in TERMINAL_STATUSES:
                    self._state_store.patch_run(
                        backend_run_id,
                        status="failed",
                        current_action=str(exc) or exc.__class__.__name__,
                        finished_at=utcnow().isoformat(),
                        exit_code=exit_code,
                    )
                    self._state_store.append_event(
                        backend_run_id,
                        "run.failed",
                        {"backendRunId": backend_run_id, "message": str(exc) or exc.__class__.__name__},
                    )
            finally:
                self._set_process(backend_run_id, None)

        self._finalize_run(
            backend_run_id=backend_run_id,
            backend_session_id=backend_session_id,
            project_root=project_root,
            exit_code=exit_code,
            terminal_payload=terminal_payload,
            stream_path=stream_path,
            stderr_path=stderr_path,
            result_path=result_path,
        )

    def _apply_cli_event(
        self,
        *,
        backend_run_id: str,
        backend_session_id: str,
        payload: dict[str, Any],
        artifacts_dir: Path,
        result_path: Path,
    ) -> dict[str, Any] | None:
        current = self._state_store.get_run(backend_run_id) or {}
        event_type = str(payload.get("type") or "").strip().lower()
        totals = _extract_totals(payload)
        if totals is not None:
            self._state_store.patch_run(backend_run_id, totals=totals)

        if event_type == "system":
            action = str(payload.get("subtype") or "init").replace("-", " ").title()
            self._state_store.patch_run(backend_run_id, current_action=action)
            self._state_store.append_event(
                backend_run_id,
                "run.progress",
                {"backendRunId": backend_run_id, "sessionId": backend_session_id, "message": action},
            )
            return None

        if event_type == "assistant":
            message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
            content = message.get("content") if isinstance(message.get("content"), list) else []
            tool_names = [
                str(block.get("name") or "").strip()
                for block in content
                if isinstance(block, dict) and str(block.get("type") or "").strip() == "tool_use"
            ]
            if tool_names:
                action = f"Tool request: {', '.join(name for name in tool_names if name) or 'tool'}"
            else:
                text = _extract_text_from_content(content)
                action = _truncate_detail(text or "Assistant response updated")
            self._state_store.patch_run(backend_run_id, current_action=action)
            self._state_store.append_event(
                backend_run_id,
                "run.progress",
                {
                    "backendRunId": backend_run_id,
                    "sessionId": backend_session_id,
                    "message": action,
                    "payload": payload,
                },
            )
            return None

        if event_type == "user" and isinstance(payload.get("tool_use_result"), dict):
            tool_result = dict(payload.get("tool_use_result") or {})
            summary = _tool_result_summary(tool_result)
            self._state_store.patch_run(backend_run_id, current_action=summary)
            self._state_store.append_event(
                backend_run_id,
                "run.progress",
                {
                    "backendRunId": backend_run_id,
                    "sessionId": backend_session_id,
                    "message": summary,
                    "payload": payload,
                },
            )
            return None

        if event_type == "result":
            result_text = str(payload.get("result") or "").strip()
            is_error = bool(payload.get("is_error"))
            output = (
                {
                    "error": {
                        "code": "claude_code_error",
                        "message": result_text or "Claude Code failed.",
                    },
                    "sessionId": str(payload.get("session_id") or backend_session_id),
                    "model": str(payload.get("model") or current.get("model") or ""),
                }
                if is_error
                else {
                    "summary": result_text or "Claude Code completed successfully.",
                    "message": result_text or "Claude Code completed successfully.",
                    "sessionId": str(payload.get("session_id") or backend_session_id),
                    "model": str(payload.get("model") or current.get("model") or ""),
                }
            )
            result_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
            self._state_store.patch_run(
                backend_run_id,
                current_action=result_text or ("Completed" if not is_error else "Failed"),
                output=output,
                result=output,
            )
            return payload

        self._state_store.append_event(
            backend_run_id,
            "run.progress",
            {"backendRunId": backend_run_id, "sessionId": backend_session_id, "payload": payload},
        )
        return None

    def _finalize_run(
        self,
        *,
        backend_run_id: str,
        backend_session_id: str,
        project_root: str,
        exit_code: int,
        terminal_payload: dict[str, Any] | None,
        stream_path: Path,
        stderr_path: Path,
        result_path: Path,
    ) -> None:
        current = self._state_store.get_run(backend_run_id) or {}
        artifacts = list(current.get("artifacts") or [])
        for artifact in self._collect_artifacts(stream_path=stream_path, stderr_path=stderr_path, result_path=result_path):
            artifacts = [item for item in artifacts if item.get("name") != artifact["name"]]
            artifacts.append(artifact)
        external_session_id = str(current.get("external_session_id") or "").strip()
        if external_session_id:
            diff = collect_workspace_diff(project_root)
            self._state_store.set_session_diff(
                external_session_id=external_session_id,
                backend_session_id=backend_session_id,
                summary=dict(diff.get("summary") or {"files": 0, "additions": 0, "deletions": 0}),
                files=list(diff.get("files") or []),
                stale=False,
            )
        if str(current.get("status") or "") not in TERMINAL_STATUSES:
            if bool(current.get("cancel_requested")):
                status = "cancelled"
                action = "Cancelled"
                event_type = "run.cancelled"
                output = current.get("output")
            elif int(exit_code) == 0 and terminal_payload is not None:
                status = "succeeded"
                action = str(current.get("current_action") or "Completed")
                event_type = "run.succeeded"
                output = current.get("output")
            elif int(exit_code) == 0:
                status = "succeeded"
                action = "Completed"
                event_type = "run.succeeded"
                output = current.get("output")
            else:
                status = "failed"
                action = "Process exited with error"
                event_type = "run.failed"
                output = current.get("output")
            self._state_store.patch_run(
                backend_run_id,
                status=status,
                current_action=action,
                finished_at=utcnow().isoformat(),
                exit_code=exit_code,
                output=output,
                result=output,
                artifacts=artifacts,
            )
            self._state_store.append_event(
                backend_run_id,
                event_type,
                {
                    "backendRunId": backend_run_id,
                    "sessionId": backend_session_id,
                    "exitCode": exit_code,
                    "message": str((output or {}).get("message") or (output or {}).get("summary") or ""),
                },
            )
        else:
            self._state_store.patch_run(backend_run_id, artifacts=artifacts)

        for artifact in artifacts:
            self._state_store.append_event(
                backend_run_id,
                "run.artifact_published",
                {"backendRunId": backend_run_id, "sessionId": backend_session_id, "artifact": artifact},
            )

    def _collect_artifacts(self, *, stream_path: Path, stderr_path: Path, result_path: Path) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        for path, media_type in (
            (stream_path, "application/x-ndjson"),
            (stderr_path, "text/plain"),
            (result_path, "application/json"),
        ):
            if not path.exists():
                continue
            data = path.read_bytes()
            preview: str | None = None
            if len(data) <= self._settings.inline_artifact_max_bytes and media_type.startswith("text/"):
                preview = data.decode("utf-8", errors="replace")
            if len(data) <= self._settings.inline_artifact_max_bytes and media_type == "application/json":
                preview = data.decode("utf-8", errors="replace")
            artifacts.append(
                {
                    "name": path.name,
                    "mediaType": media_type,
                    "uri": str(path),
                    "content": preview,
                }
            )
        return artifacts

    def _set_process(self, backend_run_id: str, process: subprocess.Popen[str] | None) -> None:
        with self._lock:
            if process is None:
                self._processes.pop(backend_run_id, None)
            else:
                self._processes[backend_run_id] = process

    def _get_process(self, backend_run_id: str) -> subprocess.Popen[str] | None:
        with self._lock:
            return self._processes.get(backend_run_id)


def _extract_text_from_content(content: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if str(block.get("type") or "").strip() != "text":
            continue
        text = str(block.get("text") or "").strip()
        if text:
            chunks.append(text)
    return "\n".join(chunks).strip()


def _extract_totals(payload: dict[str, Any]) -> dict[str, Any] | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        message = payload.get("message")
        if isinstance(message, dict):
            usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    return {
        "tokens": {
            "input": int(usage.get("input_tokens", 0) or 0),
            "output": int(usage.get("output_tokens", 0) or 0),
            "reasoning": 0,
            "cacheRead": int(usage.get("cache_read_input_tokens", 0) or 0),
            "cacheWrite": int(usage.get("cache_creation_input_tokens", 0) or 0),
        },
        "cost": _to_float(payload.get("total_cost_usd"), default=0.0),
    }


def _tool_result_summary(tool_result: dict[str, Any]) -> str:
    stdout = str(tool_result.get("stdout") or "").strip()
    stderr = str(tool_result.get("stderr") or "").strip()
    if stdout:
        return _truncate_detail(f"Tool result: {stdout}")
    if stderr:
        return _truncate_detail(f"Tool error: {stderr}")
    return "Tool completed"


def _truncate_detail(value: str, *, max_len: int = 160) -> str:
    text = value.strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _to_float(value: Any, *, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

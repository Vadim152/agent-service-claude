from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, TextIO

from opencode_adapter_app.config import AdapterSettings
from opencode_adapter_app.event_parser import TERMINAL_STATUSES, classify_event, normalize_status, parse_json_line
from opencode_adapter_app.headless_server import OpenCodeHeadlessServer, OpenCodeServerError
from opencode_adapter_app.state_store import OpenCodeAdapterStateStore, utcnow


LOGGER = logging.getLogger(__name__)


class OpenCodeProcessSupervisor:
    def __init__(
        self,
        *,
        settings: AdapterSettings,
        state_store: OpenCodeAdapterStateStore,
        headless_server: OpenCodeHeadlessServer,
    ) -> None:
        self._settings = settings
        self._state_store = state_store
        self._headless_server = headless_server
        self._lock = threading.RLock()
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._run_stops: dict[str, threading.Event] = {}

    def start_run(self, run: dict[str, Any]) -> None:
        worker = threading.Thread(target=self._run_process, args=(dict(run),), daemon=True, name=f"opencode-{run['backend_run_id']}")
        worker.start()

    def cancel_run(self, backend_run_id: str) -> dict[str, Any]:
        run = self._state_store.get_run(backend_run_id) or {}
        self._state_store.patch_run(backend_run_id, cancel_requested=True, current_action="Cancelling")
        for item in run.get("pending_approvals") or []:
            approval_id = str(item.get("approval_id") or item.get("approvalId") or "").strip()
            if approval_id:
                self._state_store.resolve_approval(backend_run_id, approval_id, "deny")
        if self._settings.runner_type == "raw_json_runner":
            self._cancel_raw_process(backend_run_id)
        else:
            session_id = str(run.get("backend_session_id") or "").strip()
            if session_id:
                try:
                    self._headless_server.request(
                        "POST",
                        f"/session/{session_id}/abort",
                        params={"directory": run.get("project_root")},
                        timeout_s=10.0,
                    )
                except OpenCodeServerError as exc:
                    LOGGER.warning("Failed to abort OpenCode session %s: %s", session_id, exc)
            stop_event = self._run_stops.get(backend_run_id)
            if stop_event is not None:
                stop_event.set()
        updated = self._state_store.patch_run(
            backend_run_id,
            status="cancelled",
            current_action="Cancelled",
            finished_at=utcnow().isoformat(),
        )
        self._state_store.append_event(backend_run_id, "run.cancelled", {"backendRunId": backend_run_id})
        return updated or self._state_store.get_run(backend_run_id) or {}

    def submit_approval_decision(self, backend_run_id: str, approval_id: str, decision: str) -> dict[str, Any]:
        if self._settings.runner_type == "raw_json_runner":
            return self._submit_raw_approval_decision(backend_run_id, approval_id, decision)
        current = self._state_store.get_run(backend_run_id) or {}
        reply = "once" if decision == "approve" else "reject"
        try:
            self._headless_server.request(
                "POST",
                f"/session/{current.get('backend_session_id')}/permissions/{approval_id}",
                params={"directory": current.get("project_root")},
                json_payload={"response": reply},
                timeout_s=10.0,
            )
        except OpenCodeServerError as exc:
            LOGGER.warning("Failed to reply to OpenCode approval %s: %s", approval_id, exc)
        remaining = [
            item
            for item in current.get("pending_approvals") or []
            if str(item.get("approval_id") or item.get("approvalId") or "") != approval_id
        ]
        updated = self._state_store.patch_run(
            backend_run_id,
            pending_approvals=remaining,
            status="running",
            current_action="Approval decision sent",
        )
        self._state_store.append_event(
            backend_run_id,
            "approval.decision",
            {"backendRunId": backend_run_id, "approvalId": approval_id, "decision": decision},
        )
        return updated or self._state_store.get_run(backend_run_id) or {}

    def create_backend_session(self, *, project_root: str, external_session_id: str) -> dict[str, Any]:
        return self._headless_server.request(
            "POST",
            "/session",
            params={"directory": project_root},
            json_payload={"title": f"Adapter session {external_session_id}"},
            timeout_s=30.0,
        )

    def fetch_session_diff(self, *, project_root: str, backend_session_id: str) -> dict[str, Any]:
        response = self._headless_server.request(
            "GET",
            f"/session/{backend_session_id}/diff",
            params={"directory": project_root},
            timeout_s=30.0,
        )
        if isinstance(response, dict):
            return response
        if isinstance(response, list):
            return {"files": response}
        return {"files": []}

    def compact_session(
        self,
        *,
        project_root: str,
        backend_session_id: str,
        provider_id: str | None,
        model_id: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if provider_id and model_id:
            payload["model"] = {"providerID": provider_id, "modelID": model_id}
        response = self._headless_server.request(
            "POST",
            f"/session/{backend_session_id}/summarize",
            params={"directory": project_root},
            json_payload=payload or None,
            timeout_s=300.0,
        )
        return response if isinstance(response, dict) else {}

    def _run_process(self, run: dict[str, Any]) -> None:
        if self._settings.runner_type == "raw_json_runner":
            self._run_raw_json_runner(run)
            return
        self._run_server_backed(run)

    def _run_server_backed(self, run: dict[str, Any]) -> None:
        backend_run_id = str(run["backend_run_id"])
        work_dir = Path(str(run["work_dir"]))
        artifacts_dir = work_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        events_path = work_dir / "events.jsonl"
        result_path = work_dir / "result.json"
        meta_path = work_dir / "meta.json"
        meta_path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")

        stop_event = threading.Event()
        self._run_stops[backend_run_id] = stop_event
        events_handle = events_path.open("a", encoding="utf-8")
        try:
            self._headless_server.ensure_started(project_root=str(run["project_root"]))
            session_id = self._ensure_backend_session(run)
            now = utcnow().isoformat()
            self._state_store.patch_run(
                backend_run_id,
                backend_session_id=session_id,
                status="running",
                current_action="Session ready",
                started_at=now,
            )
            self._state_store.append_event(
                backend_run_id,
                "run.started",
                {"backendRunId": backend_run_id, "backendSessionId": session_id},
            )

            listener = threading.Thread(
                target=self._consume_server_events,
                args=(run, session_id, events_handle, artifacts_dir, stop_event),
                daemon=True,
                name=f"opencode-events-{backend_run_id}",
            )
            listener.start()

            prompt_payload = self._build_prompt_payload(run)
            response, session_id = self._send_prompt_with_retry(
                run=run,
                backend_run_id=backend_run_id,
                session_id=session_id,
                prompt_payload=prompt_payload,
            )
            self._complete_server_run(backend_run_id, session_id, response, artifacts_dir, result_path)
            stop_event.set()
            listener.join(timeout=2.0)
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            current = self._state_store.get_run(backend_run_id) or {}
            if str(current.get("status")) not in TERMINAL_STATUSES:
                self._state_store.patch_run(
                    backend_run_id,
                    status="failed",
                    current_action=message,
                    finished_at=utcnow().isoformat(),
                    exit_code=-1,
                )
                self._state_store.append_event(
                    backend_run_id,
                    "run.failed",
                    {"backendRunId": backend_run_id, "message": message},
                )
        finally:
            stop_event.set()
            self._run_stops.pop(backend_run_id, None)
            events_handle.close()

    def _send_prompt_with_retry(
        self,
        *,
        run: dict[str, Any],
        backend_run_id: str,
        session_id: str,
        prompt_payload: dict[str, Any],
    ) -> tuple[Any, str]:
        response = self._headless_server.request(
            "POST",
            f"/session/{session_id}/message",
            params={"directory": str(run["project_root"])},
            json_payload=prompt_payload,
            timeout_s=300.0,
        )
        if not _is_token_expired_error_response(response):
            return response, session_id

        self._state_store.patch_run(
            backend_run_id,
            current_action="Refreshing GigaChat token and retrying",
            status="running",
            finished_at=None,
        )
        self._state_store.append_event(
            backend_run_id,
            "run.retrying",
            {
                "backendRunId": backend_run_id,
                "sessionId": session_id,
                "message": "401 Token has expired; refreshing token and retrying once",
            },
        )
        self._headless_server.refresh_gigachat_access_token()
        self._headless_server.restart(project_root=str(run["project_root"]))
        run["backend_session_id"] = None
        self._state_store.patch_run(backend_run_id, backend_session_id=None, finished_at=None)
        retried_session_id = self._ensure_backend_session(run)
        self._state_store.patch_run(
            backend_run_id,
            backend_session_id=retried_session_id,
            current_action="Retrying request with refreshed token",
        )
        retried_response = self._headless_server.request(
            "POST",
            f"/session/{retried_session_id}/message",
            params={"directory": str(run["project_root"])},
            json_payload=prompt_payload,
            timeout_s=300.0,
        )
        return retried_response, retried_session_id

    def _ensure_backend_session(self, run: dict[str, Any]) -> str:
        existing = str(run.get("backend_session_id") or "").strip()
        if existing:
            return existing
        payload = self.create_backend_session(
            project_root=str(run["project_root"]),
            external_session_id=str(run.get("external_session_id") or run["external_run_id"]),
        )
        session_id = str(payload.get("id") or "").strip()
        if not session_id:
            raise OpenCodeServerError("OpenCode server did not return a session id")
        external_session_id = str(run.get("external_session_id") or "").strip()
        if external_session_id:
            self._state_store.set_session_mapping(
                external_session_id=external_session_id,
                backend_session_id=session_id,
                project_root=str(run["project_root"]),
                last_backend_run_id=str(run["backend_run_id"]),
            )
        return session_id

    def _build_prompt_payload(self, run: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "agent": self._resolve_agent(run),
            "parts": [{"type": "text", "text": str(run.get("prompt") or "")}],
        }
        model_override = self._settings.resolve_forced_model()
        if model_override:
            LOGGER.info("Forcing OpenCode model for %s: %s", run["backend_run_id"], model_override)
        else:
            LOGGER.info("Using OpenCode config-managed model for %s", run["backend_run_id"])
        model = self._parse_model(model_override)
        if model is not None:
            payload["model"] = model
            external_session_id = str(run.get("external_session_id") or "").strip()
            if external_session_id:
                self._state_store.upsert_session_mapping(
                    external_session_id,
                    last_provider_id=str(model.get("providerID") or "").strip() or None,
                    last_model_id=str(model.get("modelID") or "").strip() or None,
                )
        return payload

    def _consume_server_events(
        self,
        run: dict[str, Any],
        session_id: str,
        events_handle: TextIO,
        artifacts_dir: Path,
        stop_event: threading.Event,
    ) -> None:
        backend_run_id = str(run["backend_run_id"])
        try:
            for item in self._headless_server.stream_events(directory=str(run["project_root"]), stop_event=stop_event):
                payload = item.get("payload") if isinstance(item, dict) else None
                if not isinstance(payload, dict):
                    continue
                if _event_session_id(payload) != session_id:
                    continue
                self._apply_server_event(backend_run_id, session_id, payload, events_handle, artifacts_dir)
                if stop_event.is_set():
                    return
        except OpenCodeServerError as exc:
            LOGGER.warning("OpenCode event stream failed for %s: %s", backend_run_id, exc)

    def _apply_server_event(
        self,
        backend_run_id: str,
        session_id: str,
        payload: dict[str, Any],
        events_handle: TextIO,
        artifacts_dir: Path,
    ) -> None:
        event_type = str(payload.get("type") or "")
        current = self._state_store.get_run(backend_run_id) or {}
        patches: dict[str, Any] = {}
        canonical_event = "run.progress"
        event_payload: dict[str, Any] = {"backendRunId": backend_run_id, "sessionId": session_id, "payload": payload}
        totals, limits = _extract_usage_limits(payload)
        if totals is not None:
            patches["totals"] = totals
        if limits is not None:
            patches["limits"] = limits

        if event_type == "session.status":
            status = ((payload.get("properties") or {}).get("status") or {}).get("type")
            detail = _status_detail_from_event(payload)
            if status == "busy":
                patches["current_action"] = detail or "OpenCode is running"
            elif status == "retry":
                patches["current_action"] = detail or "Retrying"
            elif status == "idle":
                patches["current_action"] = "Idle"
        elif event_type == "message.part.updated":
            detail = _part_detail_from_event(payload)
            if detail:
                patches["current_action"] = detail
        elif event_type == "message.part.delta":
            detail = _part_detail_from_event(payload)
            patches["current_action"] = detail or "Streaming response"
        elif event_type == "message.updated":
            info = (payload.get("properties") or {}).get("info") or {}
            if info.get("role") == "assistant" and info.get("error"):
                error_message = _extract_error_message(info.get("error")) or "OpenCode failed"
                patches.update(
                    {
                        "status": "failed",
                        "current_action": error_message,
                        "finished_at": utcnow().isoformat(),
                    }
                )
                canonical_event = "run.failed"
                event_payload["message"] = error_message
            else:
                patches["current_action"] = "Assistant updated"
        elif event_type == "permission.asked":
            approval = _approval_from_permission(payload.get("properties") or {})
            self._state_store.record_pending_approvals(backend_run_id, [approval])
            patches["pending_approvals"] = self._state_store.list_pending_approvals(backend_run_id)
            patches["current_action"] = "Awaiting approval"
            canonical_event = "run.awaiting_approval"
        elif event_type == "permission.replied":
            properties = payload.get("properties") or {}
            approval_id = str(properties.get("id") or "").strip()
            response = str(properties.get("response") or "").strip().lower()
            if approval_id:
                decision = "approve" if response in {"approve", "approved", "once", "always"} else "deny"
                self._state_store.resolve_approval(backend_run_id, approval_id, decision)
            patches["pending_approvals"] = self._state_store.list_pending_approvals(backend_run_id)
            patches["current_action"] = "Approval replied"
            canonical_event = "approval.decision"
        elif event_type == "session.diff":
            diff = (payload.get("properties") or {}).get("diff") or []
            normalized_files = _normalize_session_diff(diff)
            external_session_id = str(current.get("external_session_id") or "").strip()
            if external_session_id:
                self._state_store.set_session_diff(
                    external_session_id=external_session_id,
                    backend_session_id=session_id,
                    summary=_session_diff_summary(normalized_files),
                    files=normalized_files,
                    stale=False,
                )
            artifact = self._materialize_inline_artifact(
                artifacts_dir,
                "session-diff.json",
                json.dumps(normalized_files, ensure_ascii=False, indent=2),
                "application/json",
            )
            artifacts = list(current.get("artifacts") or [])
            artifacts = [item for item in artifacts if item.get("name") != artifact["name"]]
            artifacts.append(artifact)
            patches["artifacts"] = artifacts
            canonical_event = "run.artifact_published"
            event_payload["artifact"] = artifact
        elif event_type == "session.error":
            error = _extract_error_message((payload.get("properties") or {}).get("error")) or "OpenCode session error"
            patches.update(
                {
                    "status": "failed",
                    "current_action": error,
                    "finished_at": utcnow().isoformat(),
                }
            )
            canonical_event = "run.failed"
            event_payload["message"] = error

        if patches:
            self._state_store.patch_run(backend_run_id, **patches)
        stored_event = self._state_store.append_event(backend_run_id, canonical_event, event_payload)
        events_handle.write(json.dumps(stored_event, ensure_ascii=False) + "\n")
        events_handle.flush()

    def _complete_server_run(
        self,
        backend_run_id: str,
        session_id: str,
        response: Any,
        artifacts_dir: Path,
        result_path: Path,
    ) -> None:
        current = self._state_store.get_run(backend_run_id) or {}
        if str(current.get("status")) in {"failed", "cancelled"}:
            return
        response_error = _extract_response_error(response)
        if response_error is not None:
            error_message = _extract_error_message(response_error) or "OpenCode failed"
            self._state_store.patch_run(
                backend_run_id,
                status="failed",
                current_action=error_message,
                output={"error": response_error},
                result={"error": response_error},
                pending_approvals=[],
                finished_at=utcnow().isoformat(),
                exit_code=-1,
            )
            self._state_store.append_event(
                backend_run_id,
                "run.failed",
                {"backendRunId": backend_run_id, "backendSessionId": session_id, "message": error_message},
            )
            return
        text = _extract_text_output(response)
        result = {
            "summary": text,
            "sessionId": session_id,
            "message": response,
        }
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts = list(current.get("artifacts") or [])
        response_artifact = self._materialize_inline_artifact(
            artifacts_dir,
            "response.json",
            json.dumps(response, ensure_ascii=False, indent=2),
            "application/json",
        )
        artifacts = [item for item in artifacts if item.get("name") != response_artifact["name"]]
        artifacts.append(response_artifact)
        if text:
            text_artifact = self._materialize_inline_artifact(artifacts_dir, "assistant.txt", text, "text/plain")
            artifacts = [item for item in artifacts if item.get("name") != text_artifact["name"]]
            artifacts.append(text_artifact)
        totals, limits = _extract_usage_limits(response)
        self._state_store.patch_run(
            backend_run_id,
            status="succeeded",
            current_action="Completed",
            output=result,
            result=result,
            artifacts=artifacts,
            pending_approvals=[],
            finished_at=utcnow().isoformat(),
            exit_code=0,
            totals=totals,
            limits=limits,
        )
        self._state_store.append_event(
            backend_run_id,
            "run.finished",
            {"backendRunId": backend_run_id, "backendSessionId": session_id, "output": result},
        )

    def _materialize_inline_artifact(self, artifacts_dir: Path, name: str, content: str, media_type: str) -> dict[str, Any]:
        target = artifacts_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        preview = content if len(content.encode("utf-8")) <= self._settings.inline_artifact_max_bytes else None
        return {"name": name, "mediaType": media_type, "uri": str(target), "content": preview}

    def _cancel_raw_process(self, backend_run_id: str) -> None:
        process = self._get_process(backend_run_id)
        if process is not None and process.poll() is None:
            process.terminate()
            deadline = time.time() + (self._settings.graceful_kill_timeout_ms / 1000.0)
            while time.time() < deadline and process.poll() is None:
                time.sleep(0.05)
            if process.poll() is None:
                process.kill()

    def _submit_raw_approval_decision(self, backend_run_id: str, approval_id: str, decision: str) -> dict[str, Any]:
        process = self._get_process(backend_run_id)
        if process is not None and process.stdin is not None and process.poll() is None:
            payload = {"type": "approval", "approvalId": approval_id, "decision": decision}
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()
        current = self._state_store.get_run(backend_run_id) or {}
        self._state_store.resolve_approval(backend_run_id, approval_id, decision)
        remaining = [
            item
            for item in current.get("pending_approvals") or []
            if str(item.get("approval_id") or item.get("approvalId") or "") != approval_id
        ]
        updated = self._state_store.patch_run(
            backend_run_id,
            pending_approvals=remaining,
            status="running",
            current_action="Approval decision sent",
        )
        self._state_store.append_event(
            backend_run_id,
            "approval.decision",
            {"backendRunId": backend_run_id, "approvalId": approval_id, "decision": decision},
        )
        return updated or self._state_store.get_run(backend_run_id) or {}

    def _run_raw_json_runner(self, run: dict[str, Any]) -> None:
        backend_run_id = str(run["backend_run_id"])
        work_dir = Path(str(run["work_dir"]))
        artifacts_dir = work_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = work_dir / "stdout.jsonl"
        stderr_path = work_dir / "stderr.log"
        events_path = work_dir / "events.jsonl"
        result_path = work_dir / "result.json"
        meta_path = work_dir / "meta.json"
        meta_path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")

        command = self._build_raw_command(run)
        env = self._settings.build_child_env()
        try:
            process = subprocess.Popen(
                command,
                cwd=str(Path(str(run["project_root"]))),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
        except Exception as exc:
            self._state_store.patch_run(
                backend_run_id,
                status="failed",
                current_action=f"Failed to start runner: {exc}",
                finished_at=utcnow().isoformat(),
                exit_code=-1,
            )
            self._state_store.append_event(
                backend_run_id,
                "run.failed",
                {"backendRunId": backend_run_id, "message": f"Failed to start runner: {exc}"},
            )
            return

        with self._lock:
            self._processes[backend_run_id] = process
        self._state_store.patch_run(
            backend_run_id,
            status="running",
            current_action="Process started",
            started_at=utcnow().isoformat(),
        )
        self._state_store.append_event(backend_run_id, "run.started", {"backendRunId": backend_run_id, "pid": process.pid})

        stdout_handle = stdout_path.open("a", encoding="utf-8")
        stderr_handle = stderr_path.open("a", encoding="utf-8")
        events_handle = events_path.open("a", encoding="utf-8")

        stdout_thread = threading.Thread(
            target=self._consume_stdout,
            args=(run, process, stdout_handle, events_handle, artifacts_dir, result_path, startup_event := threading.Event()),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=self._consume_stderr,
            args=(backend_run_id, process, stderr_handle),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        startup_timeout_s = max(0.1, self._settings.run_start_timeout_ms / 1000.0)
        if process.poll() is None and not startup_event.wait(timeout=startup_timeout_s):
            self._state_store.patch_run(
                backend_run_id,
                status="failed",
                current_action="Runner startup timeout",
                finished_at=utcnow().isoformat(),
                exit_code=-1,
            )
            self._state_store.append_event(
                backend_run_id,
                "run.failed",
                {
                    "backendRunId": backend_run_id,
                    "message": "Runner did not produce machine-readable output before startup timeout",
                },
            )
            process.terminate()
            deadline = time.time() + (self._settings.graceful_kill_timeout_ms / 1000.0)
            while time.time() < deadline and process.poll() is None:
                time.sleep(0.05)
            if process.poll() is None:
                process.kill()
        exit_code = process.wait()
        stdout_thread.join()
        stderr_thread.join()
        stdout_handle.close()
        stderr_handle.close()
        events_handle.close()
        self._finalize_raw_run(backend_run_id, exit_code, result_path, stderr_path)
        with self._lock:
            self._processes.pop(backend_run_id, None)

    def _consume_stdout(
        self,
        run: dict[str, Any],
        process: subprocess.Popen[str],
        stdout_handle: TextIO,
        events_handle: TextIO,
        artifacts_dir: Path,
        result_path: Path,
        startup_event: threading.Event,
    ) -> None:
        if process.stdout is None:
            return
        for raw_line in process.stdout:
            try:
                stdout_handle.write(raw_line)
                stdout_handle.flush()
            except ValueError:
                return
            payload = parse_json_line(raw_line)
            if payload is None:
                continue
            startup_event.set()
            self._apply_raw_payload(run, payload, events_handle, artifacts_dir, result_path)

    def _consume_stderr(self, backend_run_id: str, process: subprocess.Popen[str], stderr_handle: TextIO) -> None:
        if process.stderr is None:
            return
        for raw_line in process.stderr:
            try:
                stderr_handle.write(raw_line)
                stderr_handle.flush()
            except ValueError:
                return
            if self._settings.print_logs:
                LOGGER.info("[%s stderr] %s", backend_run_id, raw_line.rstrip())

    def _apply_raw_payload(
        self,
        run: dict[str, Any],
        payload: dict[str, Any],
        events_handle: TextIO,
        artifacts_dir: Path,
        result_path: Path,
    ) -> None:
        backend_run_id = str(run["backend_run_id"])
        backend_session_id = str(payload.get("backendSessionId") or payload.get("sessionId") or "").strip() or None
        if backend_session_id:
            self._state_store.patch_run(backend_run_id, backend_session_id=backend_session_id)
            external_session_id = str(run.get("external_session_id") or "").strip()
            if external_session_id:
                self._state_store.set_session_mapping(
                    external_session_id=external_session_id,
                    backend_session_id=backend_session_id,
                    project_root=str(run["project_root"]),
                    last_backend_run_id=backend_run_id,
                )

        event_type = classify_event(payload)
        status = normalize_status(payload.get("status"))
        current_action = str(payload.get("currentAction") or payload.get("message") or event_type)
        patches: dict[str, Any] = {"current_action": current_action}
        totals, limits = _extract_usage_limits(payload)
        if totals is not None:
            patches["totals"] = totals
        if limits is not None:
            patches["limits"] = limits

        approvals = _extract_approvals(payload)
        if approvals:
            self._state_store.record_pending_approvals(backend_run_id, approvals)
            patches["pending_approvals"] = approvals
            patches["status"] = "running"
            event_type = "run.awaiting_approval"
        elif event_type != "run.awaiting_approval":
            patches["pending_approvals"] = []
            if status != "queued" and event_type not in {"run.finished", "run.failed", "run.cancelled"}:
                patches["status"] = status

        result = payload.get("result") or payload.get("output")
        if isinstance(result, dict):
            patches["result"] = result
            patches["output"] = result
            result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        if "diff" in payload:
            normalized_files = _normalize_session_diff(payload.get("diff"))
            external_session_id = str(run.get("external_session_id") or "").strip()
            if external_session_id:
                self._state_store.set_session_diff(
                    external_session_id=external_session_id,
                    backend_session_id=backend_session_id,
                    summary=_session_diff_summary(normalized_files),
                    files=normalized_files,
                    stale=False,
                )

        artifacts = list((self._state_store.get_run(backend_run_id) or {}).get("artifacts") or [])
        for item in _extract_artifacts(payload):
            artifact = self._materialize_artifact(item, artifacts_dir)
            if artifact is not None:
                artifacts = [existing for existing in artifacts if existing.get("name") != artifact["name"]]
                artifacts.append(artifact)
                self._state_store.append_event(
                    backend_run_id,
                    "run.artifact_published",
                    {"backendRunId": backend_run_id, "artifact": artifact},
                )
        patches["artifacts"] = artifacts

        self._state_store.patch_run(backend_run_id, **patches)
        stored_event = self._state_store.append_event(backend_run_id, event_type, {"backendRunId": backend_run_id, **payload})
        events_handle.write(json.dumps(stored_event, ensure_ascii=False) + "\n")
        events_handle.flush()

        if event_type in {"run.finished", "run.failed", "run.cancelled"}:
            # Terminal raw events can arrive before stderr/result artifacts are fully flushed.
            # Final status transition is owned by _finalize_raw_run after process teardown.
            self._state_store.patch_run(
                backend_run_id,
                finished_at=utcnow().isoformat(),
            )

    def _materialize_artifact(self, artifact: dict[str, Any], artifacts_dir: Path) -> dict[str, Any] | None:
        name = str(artifact.get("name") or artifact.get("id") or "").strip()
        if not name:
            return None
        media_type = str(artifact.get("mediaType") or artifact.get("media_type") or "text/plain")
        content = artifact.get("content")
        source_path = str(artifact.get("path") or artifact.get("file") or "").strip()
        uri = str(artifact.get("uri") or "").strip() or None
        preview: str | None = None
        if content is not None:
            target = artifacts_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            text_content = str(content)
            target.write_text(text_content, encoding="utf-8")
            uri = str(target)
            if len(text_content.encode("utf-8")) <= self._settings.inline_artifact_max_bytes:
                preview = text_content
        elif source_path:
            source = Path(source_path)
            if source.exists():
                data = source.read_bytes()
                target = artifacts_dir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                uri = str(target)
                if len(data) <= self._settings.inline_artifact_max_bytes and media_type.startswith("text/"):
                    preview = data.decode("utf-8", errors="replace")
        return {"name": name, "mediaType": media_type, "uri": uri, "content": preview}

    def _finalize_raw_run(self, backend_run_id: str, exit_code: int, result_path: Path, stderr_path: Path) -> None:
        current = self._state_store.get_run(backend_run_id) or {}
        current = self._ensure_stderr_artifact(current, stderr_path, backend_run_id)
        if str(current.get("status")) in TERMINAL_STATUSES:
            self._state_store.patch_run(backend_run_id, exit_code=exit_code, finished_at=current.get("finished_at") or utcnow().isoformat())
            return

        result_payload = current.get("result")
        if result_payload is None and result_path.exists():
            try:
                result_payload = json.loads(result_path.read_text(encoding="utf-8"))
            except Exception:
                result_payload = None

        if current.get("cancel_requested"):
            status = "cancelled"
            event_type = "run.cancelled"
            action = "Cancelled"
        elif exit_code == 0:
            status = "succeeded"
            event_type = "run.finished"
            action = "Completed"
        else:
            status = "failed"
            event_type = "run.failed"
            action = "Process exited with error"

        patches: dict[str, Any] = {
            "status": status,
            "current_action": action,
            "finished_at": utcnow().isoformat(),
            "exit_code": exit_code,
        }
        if isinstance(result_payload, dict):
            patches["result"] = result_payload
            patches["output"] = result_payload
        updated = self._state_store.patch_run(backend_run_id, **patches) or {}
        self._ensure_stderr_artifact(updated, stderr_path, backend_run_id)
        self._state_store.append_event(
            backend_run_id,
            event_type,
            {"backendRunId": backend_run_id, "status": status, "exitCode": exit_code},
        )

    def _ensure_stderr_artifact(self, current: dict[str, Any], stderr_path: Path, backend_run_id: str) -> dict[str, Any]:
        if any(item.get("name") == "stderr.log" for item in current.get("artifacts") or []):
            return current
        if not stderr_path.exists():
            return current
        data = stderr_path.read_bytes()
        artifacts = list(current.get("artifacts") or [])
        artifacts.append(
            {
                "name": "stderr.log",
                "mediaType": "text/plain",
                "uri": str(stderr_path),
                "content": data.decode("utf-8", errors="replace")
                if len(data) <= self._settings.inline_artifact_max_bytes
                else None,
            }
        )
        return self._state_store.patch_run(backend_run_id, artifacts=artifacts) or current

    def _build_raw_command(self, run: dict[str, Any]) -> list[str]:
        command = [self._settings.binary, *self._settings.binary_args]
        profile = str(run.get("profile") or run.get("config_profile") or "").strip()
        agent = self._settings.agent_map.get(profile, profile) or self._settings.default_agent
        backend_session_id = str(run.get("backend_session_id") or "").strip()
        prompt = str(run.get("prompt") or "")
        command.extend(
            [
                "--project-root",
                str(run["project_root"]),
                "--prompt",
                prompt,
                "--agent",
                agent,
            ]
        )
        if backend_session_id:
            command.extend(["--session", backend_session_id])
        model_override = self._settings.resolve_forced_model()
        if model_override:
            command.extend(["--model", model_override])
        return command

    def _resolve_agent(self, run: dict[str, Any]) -> str:
        profile = str(run.get("profile") or run.get("config_profile") or "").strip()
        return self._settings.agent_map.get(profile, profile) or self._settings.default_agent

    def _parse_model(self, value: str | None) -> dict[str, str] | None:
        raw = str(value or "").strip()
        if not raw or "/" not in raw:
            return None
        provider_id, model_id = raw.split("/", 1)
        if not provider_id or not model_id:
            return None
        return {"providerID": provider_id, "modelID": model_id}

    def _get_process(self, backend_run_id: str) -> subprocess.Popen[str] | None:
        with self._lock:
            return self._processes.get(backend_run_id)


def _extract_approvals(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("pendingApprovals") or payload.get("approvals") or []
    if isinstance(values, dict):
        values = [values]
    approvals: list[dict[str, Any]] = []
    for index, item in enumerate(values):
        if not isinstance(item, dict):
            continue
        approval_id = str(item.get("approvalId") or item.get("id") or f"approval-{index + 1}")
        approvals.append(
            {
                "approvalId": approval_id,
                "approval_id": approval_id,
                "toolName": str(item.get("toolName") or item.get("tool") or "opencode.tool"),
                "tool_name": str(item.get("toolName") or item.get("tool") or "opencode.tool"),
                "title": str(item.get("title") or item.get("toolName") or "OpenCode approval"),
                "kind": str(item.get("kind") or "tool"),
                "riskLevel": str(item.get("riskLevel") or "high"),
                "risk_level": str(item.get("riskLevel") or "high"),
                "metadata": dict(item.get("metadata") or {}),
            }
        )
    return approvals


def _extract_artifacts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("artifacts") or payload.get("artifact") or []
    if isinstance(values, dict):
        values = [values]
    return [item for item in values if isinstance(item, dict)]


def _event_session_id(payload: dict[str, Any]) -> str | None:
    properties = payload.get("properties") or {}
    for candidate in (
        properties.get("sessionID"),
        (properties.get("info") or {}).get("sessionID"),
        (properties.get("part") or {}).get("sessionID"),
    ):
        value = str(candidate or "").strip()
        if value:
            return value
    return None


def _approval_from_permission(properties: dict[str, Any]) -> dict[str, Any]:
    approval_id = str(properties.get("id") or "")
    permission = str(properties.get("permission") or "opencode.permission")
    return {
        "approvalId": approval_id,
        "approval_id": approval_id,
        "toolName": permission,
        "tool_name": permission,
        "title": permission,
        "kind": "tool",
        "riskLevel": "high",
        "risk_level": "high",
        "metadata": dict(properties.get("metadata") or {}),
    }


def _normalize_session_diff(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "file": str(item.get("file") or item.get("path") or item.get("name") or ""),
                "additions": int(item.get("additions", 0)),
                "deletions": int(item.get("deletions", 0)),
                "before": str(item.get("before") or ""),
                "after": str(item.get("after") or ""),
            }
        )
    return normalized


def _session_diff_summary(files: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "files": len(files),
        "additions": sum(int(item.get("additions", 0)) for item in files),
        "deletions": sum(int(item.get("deletions", 0)) for item in files),
    }


def _extract_error_message(error: Any) -> str | None:
    if not isinstance(error, dict):
        return None
    data = error.get("data")
    if isinstance(data, dict):
        message = str(data.get("message") or "").strip()
        if message:
            return message
    return str(error.get("name") or "").strip() or None


def _extract_response_error(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict):
        return None
    info = response.get("info")
    if not isinstance(info, dict):
        return None
    error = info.get("error")
    if not isinstance(error, dict):
        return None
    return error


def _extract_usage_limits(payload: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(payload, dict):
        return None, None
    candidates: list[dict[str, Any]] = [payload]
    properties = payload.get("properties")
    if isinstance(properties, dict):
        candidates.append(properties)
        status_block = properties.get("status")
        if isinstance(status_block, dict):
            candidates.append(status_block)
        part = properties.get("part")
        if isinstance(part, dict):
            candidates.append(part)
    message = payload.get("message")
    if isinstance(message, dict):
        candidates.append(message)
        message_info = message.get("info")
        if isinstance(message_info, dict):
            candidates.append(message_info)
        parts = message.get("parts")
        if isinstance(parts, list):
            candidates.extend(item for item in parts if isinstance(item, dict))
    info = payload.get("info")
    if isinstance(info, dict):
        candidates.append(info)
    parts = payload.get("parts")
    if isinstance(parts, list):
        candidates.extend(item for item in parts if isinstance(item, dict))

    totals: dict[str, Any] | None = None
    limits: dict[str, Any] | None = None
    for item in candidates:
        if totals is None:
            totals = _extract_totals(item)
        if limits is None:
            limits = _extract_limits(item)
    return totals, limits


def _extract_totals(payload: dict[str, Any]) -> dict[str, Any] | None:
    totals = payload.get("totals")
    if isinstance(totals, dict):
        return {
            "tokens": _normalize_tokens(totals.get("tokens") if isinstance(totals.get("tokens"), dict) else totals),
            "cost": _to_float(totals.get("cost"), default=0.0),
        }

    usage = payload.get("usage")
    if isinstance(usage, dict):
        return {
            "tokens": _normalize_tokens(usage),
            "cost": _to_float(usage.get("cost"), default=0.0),
        }
    tokens = payload.get("tokens")
    if isinstance(tokens, dict):
        return {
            "tokens": _normalize_tokens(tokens),
            "cost": _to_float(payload.get("cost"), default=0.0),
        }
    return None


def _extract_limits(payload: dict[str, Any]) -> dict[str, Any] | None:
    limits = payload.get("limits")
    if isinstance(limits, dict):
        context_window = _first_int(limits, "contextWindow", "context_window", "window")
        used = _first_int(limits, "used", "usedTokens", "used_tokens", "inputTokens", "totalTokens", default=0)
        percent = _first_float(limits, "percent", "usagePercent", "usage_ratio")
        if percent is None and context_window and context_window > 0:
            percent = round(float(used) / float(context_window), 4)
        return {"contextWindow": context_window, "used": used, "percent": percent}

    usage = payload.get("usage")
    if isinstance(usage, dict):
        context_window = _first_int(payload, "contextWindow", "context_window", "window")
        if context_window is None:
            context_window = _first_int(usage, "contextWindow", "context_window", "window")
        used = _first_int(usage, "totalTokens", "used", "usedTokens", default=0)
        percent = _first_float(usage, "percent", "usagePercent", "usage_ratio")
        if percent is None and context_window and context_window > 0:
            percent = round(float(used) / float(context_window), 4)
        return {"contextWindow": context_window, "used": used, "percent": percent}
    tokens = payload.get("tokens")
    if isinstance(tokens, dict):
        context_window = _first_int(payload, "contextWindow", "context_window", "window")
        used = _first_int(tokens, "total", "totalTokens", default=0)
        percent = _first_float(payload, "percent", "usagePercent", "usage_ratio")
        if percent is None and context_window and context_window > 0:
            percent = round(float(used) / float(context_window), 4)
        return {"contextWindow": context_window, "used": used, "percent": percent}
    return None


def _normalize_tokens(tokens: dict[str, Any] | None) -> dict[str, int]:
    payload = tokens if isinstance(tokens, dict) else {}
    cache = payload.get("cache")
    cache_payload = cache if isinstance(cache, dict) else {}
    return {
        "input": _first_int(payload, "input", "inputTokens", "promptTokens", "prompt_tokens", default=0),
        "output": _first_int(payload, "output", "outputTokens", "completionTokens", "completion_tokens", default=0),
        "reasoning": _first_int(payload, "reasoning", "reasoningTokens", "thinkingTokens", default=0),
        "cacheRead": _first_int(payload, "cacheRead", "cache_read", "cacheReadTokens", default=None)
        or _first_int(cache_payload, "read", "cacheRead", default=0),
        "cacheWrite": _first_int(payload, "cacheWrite", "cache_write", "cacheWriteTokens", default=None)
        or _first_int(cache_payload, "write", "cacheWrite", default=0),
    }


def _status_detail_from_event(payload: dict[str, Any]) -> str:
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        return ""
    status = properties.get("status")
    if not isinstance(status, dict):
        return ""
    message = str(status.get("message") or status.get("detail") or "").strip()
    if message:
        return message
    status_type = str(status.get("type") or "").strip()
    return status_type.title() if status_type else ""


def _part_detail_from_event(payload: dict[str, Any]) -> str:
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        return ""
    part = properties.get("part")
    if not isinstance(part, dict):
        return ""
    part_type = str(part.get("type") or "").strip().lower()
    if part_type == "text":
        text = str(part.get("text") or "").strip()
        return _truncate_detail(f"Streaming: {text}") if text else "Streaming response"
    if part_type == "reasoning":
        text = str(part.get("text") or "").strip()
        return _truncate_detail(f"Reasoning: {text}") if text else "Reasoning"
    if part_type == "tool":
        tool_name = str(part.get("tool") or part.get("name") or "").strip()
        return f"Tool: {tool_name}" if tool_name else "Tool execution"
    if part_type == "step-start":
        snapshot = str(part.get("snapshot") or "").strip()
        return f"Step started ({snapshot[:8]})" if snapshot else "Step started"
    if part_type == "step-finish":
        reason = str(part.get("reason") or "").strip()
        return f"Step finished ({reason})" if reason else "Step finished"
    return part_type.replace("-", " ").title() if part_type else ""


def _truncate_detail(value: str, *, max_len: int = 160) -> str:
    text = value.strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _first_int(payload: dict[str, Any], *keys: str, default: int | None = None) -> int | None:
    for key in keys:
        if key not in payload:
            continue
        value = payload.get(key)
        try:
            if value is None:
                continue
            return int(value)
        except (TypeError, ValueError):
            continue
    return default


def _first_float(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key not in payload:
            continue
        value = payload.get(key)
        try:
            if value is None:
                continue
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _to_float(value: Any, *, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_token_expired_error_response(response: Any) -> bool:
    error = _extract_response_error(response)
    if not isinstance(error, dict):
        return False
    data = error.get("data")
    if isinstance(data, dict):
        status_code = int(data.get("statusCode") or 0)
        message = str(data.get("message") or "").strip().lower()
        response_body = str(data.get("responseBody") or "").strip().lower()
        if status_code == 401 and "token has expired" in (message or response_body):
            return True
        if "token has expired" in message or "token has expired" in response_body:
            return True
    message = _extract_error_message(error)
    return bool(message and "token has expired" in message.lower())


def _extract_text_output(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    parts = response.get("parts") or []
    chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if str(part.get("type") or "") == "text":
            text = str(part.get("text") or "").strip()
            if text:
                chunks.append(text)
    return "\n".join(chunks).strip()

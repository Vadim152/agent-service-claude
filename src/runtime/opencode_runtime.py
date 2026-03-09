"""OpenCode delegated runtime and run driver."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from infrastructure.runtime_errors import ChatRuntimeError
from runtime.opencode_adapter import OpenCodeAdapterError


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


TERMINAL_RUN_STATUSES = {"succeeded", "failed", "needs_attention", "cancelled"}


class OpenCodeRunDriver:
    def __init__(
        self,
        *,
        adapter_client: Any,
        run_state_store: Any,
        session_state_store: Any,
        policy_service: Any | None = None,
        artifact_store: Any | None = None,
        poll_interval_ms: int = 1_000,
        max_poll_interval_ms: int = 5_000,
        event_page_size: int = 200,
    ) -> None:
        self._adapter_client = adapter_client
        self._run_state_store = run_state_store
        self._session_state_store = session_state_store
        self._policy_service = policy_service
        self._artifact_store = artifact_store
        self._poll_interval_s = max(0.1, poll_interval_ms / 1000.0)
        self._max_poll_interval_s = max(self._poll_interval_s, max_poll_interval_ms / 1000.0)
        self._event_page_size = max(1, int(event_page_size))

    async def execute_run(self, run_id: str) -> None:
        run = self._require_run(run_id)
        session_id = str(run.get("session_id") or "").strip() or None
        created = None
        if not str(run.get("backend_run_id") or "").strip():
            created = await asyncio.to_thread(self._adapter_client.create_run, self._build_create_payload(run))
            backend_run_id = str(created.get("backendRunId") or created.get("runId") or created.get("id") or "").strip()
            if not backend_run_id:
                raise RuntimeError("OpenCode adapter did not return backend run id")
            self._run_state_store.patch_job(
                run_id,
                backend="opencode-adapter",
                runtime="opencode",
                backend_run_id=backend_run_id,
                backend_session_id=created.get("backendSessionId") or created.get("sessionId"),
                execution_id=backend_run_id,
                status=self._normalize_status(created.get("status") or "queued"),
                last_synced_at=_utcnow(),
            )
            self._run_state_store.append_event(
                run_id,
                "run.started",
                {"runId": run_id, "backendRunId": backend_run_id},
            )
            self._update_session_run_state(
                session_id=session_id,
                run_id=run_id,
                status=self._normalize_status(created.get("status") or "queued"),
                current_action=str(created.get("currentAction") or "Dispatching OpenCode run"),
                backend_session_id=created.get("backendSessionId") or created.get("sessionId"),
            )

        cancel_forwarded = False
        while True:
            run = self._require_run(run_id)
            backend_run_id = str(run.get("backend_run_id") or "").strip()
            if not backend_run_id:
                raise RuntimeError("OpenCode backend run id is missing")
            if bool(run.get("cancel_requested")) and not cancel_forwarded and str(run.get("status")) not in TERMINAL_RUN_STATUSES:
                await asyncio.to_thread(self._adapter_client.cancel_run, backend_run_id)
                cancel_forwarded = True

            status_payload = created or await asyncio.to_thread(self._adapter_client.get_run, backend_run_id)
            created = None
            await self._sync_status(run_id, status_payload)
            await self._sync_events(run_id)
            current = self._require_run(run_id)
            if str(current.get("status") or "queued") in TERMINAL_RUN_STATUSES:
                await self._sync_session_diff_snapshot(session_id=session_id)
                self._finalize_session(current, status_payload)
                return
            await asyncio.sleep(min(self._poll_interval_s, self._max_poll_interval_s))

    def _build_create_payload(self, run: dict[str, Any]) -> dict[str, Any]:
        input_payload = dict(run.get("input") or {})
        return {
            "runId": run["run_id"],
            "sessionId": run.get("session_id"),
            "projectRoot": run.get("project_root"),
            "prompt": input_payload.get("prompt") or input_payload.get("message") or input_payload.get("content"),
            "source": run.get("source"),
            "profile": run.get("profile"),
            "backendSessionId": run.get("backend_session_id") or input_payload.get("backendSessionId"),
            "policyMode": "external_control_plane",
            "configProfile": input_payload.get("configProfile") or "default",
        }

    async def _sync_status(self, run_id: str, status_payload: dict[str, Any]) -> None:
        run = self._require_run(run_id)
        normalized_status = self._normalize_status(status_payload.get("status") or run.get("status") or "queued")
        backend_session_id = status_payload.get("backendSessionId") or status_payload.get("sessionId") or run.get("backend_session_id")
        current_action = str(status_payload.get("currentAction") or status_payload.get("message") or normalized_status.title())
        session_id = str(run.get("session_id") or "").strip() or None
        existing_session = self._session_state_store.get_session(session_id) if session_id else None
        existing_limits = dict((existing_session or {}).get("limits") or {})
        default_context_window = int(existing_limits.get("contextWindow") or 200_000)
        totals = _extract_status_totals(status_payload) or (existing_session or {}).get("totals")
        limits = _extract_status_limits(status_payload, default_context_window=default_context_window) or (existing_session or {}).get("limits")
        attempts = self._merge_attempt_artifacts(run, status_payload)
        self._run_state_store.patch_job(
            run_id,
            status=normalized_status,
            runtime="opencode",
            backend="opencode-adapter",
            backend_run_id=status_payload.get("backendRunId") or status_payload.get("runId") or run.get("backend_run_id"),
            backend_session_id=backend_session_id,
            execution_id=status_payload.get("backendRunId") or status_payload.get("runId") or run.get("backend_run_id"),
            last_synced_at=_utcnow(),
            finished_at=_utcnow() if normalized_status in TERMINAL_RUN_STATUSES else run.get("finished_at"),
            attempts=attempts,
            result=status_payload.get("output") or status_payload.get("result") or run.get("result"),
        )
        self._update_session_run_state(
            session_id=session_id,
            run_id=run_id,
            status=normalized_status,
            current_action=current_action,
            backend_session_id=backend_session_id,
            totals=totals,
            limits=limits,
        )
        last_progress_action = str(run.get("last_progress_action") or "")
        if session_id and current_action and normalized_status not in TERMINAL_RUN_STATUSES and current_action != last_progress_action:
            self._session_state_store.append_event(
                session_id,
                "opencode.run.progress",
                {
                    "sessionId": session_id,
                    "runId": run_id,
                    "backendRunId": status_payload.get("backendRunId") or status_payload.get("runId") or run.get("backend_run_id"),
                    "message": current_action,
                    "currentAction": current_action,
                },
            )
            self._run_state_store.patch_job(run_id, last_progress_action=current_action)
        pending = status_payload.get("pendingApprovals") or []
        if isinstance(pending, list):
            self._sync_pending_approvals(
                session_id=str(run.get("session_id") or "").strip() or None,
                run_id=run_id,
                backend_run_id=str(status_payload.get("backendRunId") or status_payload.get("runId") or run.get("backend_run_id") or ""),
                items=[item for item in pending if isinstance(item, dict)],
            )

    async def _sync_events(self, run_id: str) -> None:
        run = self._require_run(run_id)
        backend_run_id = str(run.get("backend_run_id") or "").strip()
        if not backend_run_id:
            return
        cursor = run.get("sync_cursor", 0)
        session_id = str(run.get("session_id") or "").strip() or None
        has_more = True
        next_cursor = cursor
        while has_more:
            try:
                payload = await asyncio.to_thread(
                    self._adapter_client.list_events,
                    backend_run_id,
                    after=next_cursor,
                    limit=self._event_page_size,
                )
            except OpenCodeAdapterError as exc:
                if exc.code == "stale_cursor":
                    next_cursor = int((exc.details or {}).get("nextCursor") or next_cursor)
                    self._run_state_store.patch_job(run_id, sync_cursor=next_cursor, last_synced_at=_utcnow())
                    return
                raise
            items = payload.get("items", [])
            if not isinstance(items, list):
                items = []
            next_cursor = payload.get("nextCursor", payload.get("nextIndex", next_cursor))
            has_more = bool(payload.get("hasMore", False))
            for item in items:
                if not isinstance(item, dict):
                    continue
                event_type = str(item.get("eventType") or item.get("type") or "run.progress")
                event_payload = dict(item.get("payload") or {})
                event_payload.setdefault("runId", run_id)
                event_payload.setdefault("backendRunId", backend_run_id)
                self._run_state_store.append_event(run_id, event_type, event_payload)
                if session_id:
                    self._session_state_store.append_event(
                        session_id,
                        f"opencode.{event_type}",
                        {"sessionId": session_id, **event_payload},
                    )
            if not items:
                break
        self._run_state_store.patch_job(run_id, sync_cursor=next_cursor, last_synced_at=_utcnow())

    async def _sync_session_diff_snapshot(self, *, session_id: str | None) -> None:
        if not session_id:
            return
        try:
            payload = await asyncio.to_thread(self._adapter_client.get_session_diff, session_id)
        except Exception:
            return
        summary = dict(payload.get("summary") or {"files": 0, "additions": 0, "deletions": 0})
        files = list(payload.get("files") or [])
        self._session_state_store.update_session(session_id, diff={"summary": summary, "files": files})

    def _merge_attempt_artifacts(self, run: dict[str, Any], status_payload: dict[str, Any]) -> list[dict[str, Any]]:
        attempts = [dict(item) for item in (run.get("attempts") or []) if isinstance(item, dict)]
        if attempts:
            attempt = attempts[-1]
        else:
            attempt = {
                "attempt_id": "opencode-1",
                "attempt_no": 1,
                "status": str(status_payload.get("status") or run.get("status") or "queued"),
                "started_at": run.get("started_at") or _utcnow(),
                "finished_at": None,
                "artifacts": {},
            }
            attempts.append(attempt)
        attempt["status"] = self._normalize_status(status_payload.get("status") or attempt.get("status") or "queued")
        artifacts = dict(attempt.get("artifacts") or {})
        for item in status_payload.get("artifacts") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("id") or f"artifact-{len(artifacts) + 1}")
            uri = self._publish_artifact(item, run_id=str(run["run_id"]), attempt_id=str(attempt["attempt_id"]))
            if uri:
                artifacts[name] = uri
        attempt["artifacts"] = artifacts
        if attempt["status"] in TERMINAL_RUN_STATUSES and not attempt.get("finished_at"):
            attempt["finished_at"] = _utcnow()
        return attempts

    def _publish_artifact(self, artifact: dict[str, Any], *, run_id: str, attempt_id: str) -> str | None:
        if self._artifact_store is not None and artifact.get("content") is not None:
            published = self._artifact_store.publish_text(
                name=str(artifact.get("name") or "artifact.txt"),
                content=str(artifact.get("content") or ""),
                media_type=str(artifact.get("mediaType") or artifact.get("media_type") or "text/plain"),
                connector_source="opencode.artifacts",
                run_id=run_id,
                attempt_id=attempt_id,
            )
            return str(published.get("uri") or "")
        uri = str(artifact.get("uri") or artifact.get("signedUrl") or "").strip()
        return uri or None

    def _sync_pending_approvals(
        self,
        *,
        session_id: str | None,
        run_id: str,
        backend_run_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        if not session_id:
            return
        known = {
            str(item.get("tool_call_id") or "")
            for item in self._session_state_store.list_pending_tool_calls(session_id=session_id)
        }
        for item in items:
            approval_id = str(item.get("approvalId") or item.get("id") or "").strip()
            if not approval_id or approval_id in known:
                continue
            stored = self._session_state_store.set_pending_tool_call(
                session_id,
                tool_call_id=approval_id,
                tool_name=str(item.get("toolName") or item.get("tool") or "opencode.tool"),
                args={"backend_run_id": backend_run_id, "run_id": run_id, **dict(item.get("metadata") or {})},
                risk_level=str(item.get("riskLevel") or "high"),
                requires_confirmation=bool(item.get("requiresConfirmation", True)),
                title=str(item.get("title") or item.get("toolName") or "OpenCode approval"),
                kind=str(item.get("kind") or "tool"),
            )
            if stored is not None and self._policy_service is not None:
                stored["session_id"] = session_id
                self._policy_service.record_approval_requested(stored)
            self._session_state_store.append_event(
                session_id,
                "permission.requested",
                {"sessionId": session_id, "permissionId": approval_id, "runId": run_id},
            )

    def _finalize_session(self, run: dict[str, Any], status_payload: dict[str, Any]) -> None:
        session_id = str(run.get("session_id") or "").strip() or None
        if not session_id:
            return
        status = str(run.get("status") or "failed")
        message = self._build_terminal_message(run, status_payload)
        self._session_state_store.append_message(
            session_id,
            role="assistant" if status == "succeeded" else "system",
            content=message,
            run_id=str(run["run_id"]),
            metadata={"runId": run["run_id"], "backendRunId": run.get("backend_run_id"), "status": status},
        )
        self._clear_pending_approvals(session_id=session_id, backend_run_id=str(run.get("backend_run_id") or ""))
        self._session_state_store.append_event(
            session_id,
            f"run.{status}",
            {"sessionId": session_id, "runId": run["run_id"], "status": status},
        )
        self._session_state_store.update_session(
            session_id,
            activity="idle" if status in {"succeeded", "cancelled"} else "error",
            current_action="Idle" if status in {"succeeded", "cancelled"} else "OpenCode failed",
            active_run_status=status,
            active_run_backend=run.get("backend"),
            backend_session_id=run.get("backend_session_id"),
        )

    def _clear_pending_approvals(self, *, session_id: str, backend_run_id: str) -> None:
        if not backend_run_id:
            return
        for item in self._session_state_store.list_pending_tool_calls(session_id=session_id):
            args = dict(item.get("args") or {})
            if str(args.get("backend_run_id") or "") != backend_run_id:
                continue
            self._session_state_store.pop_pending_tool_call(session_id, str(item.get("tool_call_id") or ""))

    def _update_session_run_state(
        self,
        *,
        session_id: str | None,
        run_id: str,
        status: str,
        current_action: str,
        backend_session_id: str | None,
        totals: dict[str, Any] | None = None,
        limits: dict[str, Any] | None = None,
    ) -> None:
        if not session_id:
            return
        activity = "waiting_permission" if self._session_state_store.list_pending_tool_calls(session_id=session_id) else ("idle" if status in {"succeeded", "cancelled"} else "busy")
        if status == "failed":
            activity = "error"
        changes: dict[str, Any] = {
            "activity": activity,
            "current_action": current_action,
            "active_run_id": run_id,
            "active_run_status": status,
            "active_run_backend": "opencode-adapter",
            "backend_session_id": backend_session_id,
        }
        if totals is not None:
            changes["totals"] = totals
        if limits is not None:
            changes["limits"] = limits
        self._session_state_store.update_session(
            session_id,
            **changes,
        )

    @staticmethod
    def _build_terminal_message(run: dict[str, Any], status_payload: dict[str, Any]) -> str:
        output = status_payload.get("output") or status_payload.get("result") or run.get("result") or {}
        text = ""
        error_text = ""
        fallback_error = ""
        if isinstance(output, dict):
            text = str(output.get("summary") or output.get("message") or output.get("text") or "").strip()
            nested = output.get("message")
            if isinstance(nested, dict):
                error = _extract_structured_error(nested)
                if error:
                    error_text = error
                if not text:
                    text = str(nested.get("text") or "").strip()
        elif output is not None:
            text = str(output).strip()
        fallback_error = str(
            status_payload.get("currentAction")
            or status_payload.get("message")
            or run.get("current_action")
            or run.get("currentAction")
            or ""
        ).strip()
        status = str(run.get("status") or "failed")
        if status == "succeeded":
            return text or "OpenCode run completed successfully."
        if status == "cancelled":
            return text or "OpenCode run was cancelled."
        if fallback_error and fallback_error.lower() not in {"failed", "error", "opencode failed", "opencode run failed"}:
            return error_text or text or fallback_error
        return error_text or text or "OpenCode run failed."

    @staticmethod
    def _normalize_status(value: Any) -> str:
        raw = str(value or "queued").strip().lower()
        mapping = {
            "queued": "queued",
            "pending": "queued",
            "created": "queued",
            "running": "running",
            "in_progress": "running",
            "busy": "running",
            "awaiting_approval": "running",
            "waiting_approval": "running",
            "succeeded": "succeeded",
            "completed": "succeeded",
            "finished": "succeeded",
            "failed": "failed",
            "error": "failed",
            "cancelled": "cancelled",
            "canceled": "cancelled",
        }
        return mapping.get(raw, raw or "queued")

    def _require_run(self, run_id: str) -> dict[str, Any]:
        run = self._run_state_store.get_job(run_id)
        if not run:
            raise RuntimeError(f"Run not found: {run_id}")
        return run


def _extract_structured_error(payload: dict[str, Any]) -> str:
    info = payload.get("info")
    if not isinstance(info, dict):
        return ""
    error = info.get("error")
    if not isinstance(error, dict):
        return ""
    data = error.get("data")
    if isinstance(data, dict):
        message = str(data.get("message") or "").strip()
        if message:
            return message
    fallback = str(error.get("name") or "").strip()
    return fallback


def _extract_status_totals(payload: dict[str, Any]) -> dict[str, Any] | None:
    totals = payload.get("totals")
    if isinstance(totals, dict):
        tokens = totals.get("tokens")
        token_payload = tokens if isinstance(tokens, dict) else totals
        return {
            "tokens": {
                "input": _first_int(token_payload, "input", "inputTokens", "promptTokens", "prompt_tokens", default=0),
                "output": _first_int(token_payload, "output", "outputTokens", "completionTokens", "completion_tokens", default=0),
                "reasoning": _first_int(token_payload, "reasoning", "reasoningTokens", "thinkingTokens", default=0),
                "cacheRead": _first_int(token_payload, "cacheRead", "cache_read", "cacheReadTokens", default=0),
                "cacheWrite": _first_int(token_payload, "cacheWrite", "cache_write", "cacheWriteTokens", default=0),
            },
            "cost": _first_float(totals, "cost", default=0.0),
        }
    usage = payload.get("usage")
    if isinstance(usage, dict):
        return {
            "tokens": {
                "input": _first_int(usage, "input", "inputTokens", "promptTokens", "prompt_tokens", default=0),
                "output": _first_int(usage, "output", "outputTokens", "completionTokens", "completion_tokens", default=0),
                "reasoning": _first_int(usage, "reasoning", "reasoningTokens", "thinkingTokens", default=0),
                "cacheRead": _first_int(usage, "cacheRead", "cache_read", "cacheReadTokens", default=0),
                "cacheWrite": _first_int(usage, "cacheWrite", "cache_write", "cacheWriteTokens", default=0),
            },
            "cost": _first_float(usage, "cost", default=0.0),
        }
    return None


def _extract_status_limits(payload: dict[str, Any], *, default_context_window: int) -> dict[str, Any] | None:
    limits = payload.get("limits")
    if isinstance(limits, dict):
        context_window = _first_int(limits, "contextWindow", "context_window", "window", default=default_context_window)
        used = _first_int(limits, "used", "usedTokens", "used_tokens", "totalTokens", default=0)
        percent = _first_float(limits, "percent", "usagePercent", "usage_ratio", default=None)
        if percent is None and context_window and context_window > 0:
            percent = round(float(used) / float(context_window), 4)
        return {"contextWindow": context_window, "used": used, "percent": percent}
    usage = payload.get("usage")
    if isinstance(usage, dict):
        context_window = _first_int(payload, "contextWindow", "context_window", "window", default=default_context_window)
        used = _first_int(usage, "totalTokens", "used", "usedTokens", default=0)
        percent = _first_float(usage, "percent", "usagePercent", "usage_ratio", default=None)
        if percent is None and context_window and context_window > 0:
            percent = round(float(used) / float(context_window), 4)
        return {"contextWindow": context_window, "used": used, "percent": percent}
    return None


def _first_int(payload: dict[str, Any], *keys: str, default: int | None) -> int | None:
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


def _first_float(payload: dict[str, Any], *keys: str, default: float | None) -> float | None:
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
    return default


def _adapter_error_to_runtime_error(exc: OpenCodeAdapterError) -> ChatRuntimeError:
    return ChatRuntimeError(
        str(exc),
        status_code=exc.status_code or 503,
        code=exc.code,
        retryable=exc.retryable,
        details=exc.details,
        request_id=exc.request_id,
    )


class OpenCodeSessionRuntime:
    name = "opencode"

    def __init__(
        self,
        *,
        state_store: Any,
        run_state_store: Any,
        adapter_client: Any,
        run_service: Any | None = None,
        context_window: int = 200_000,
    ) -> None:
        self.state_store = state_store
        self._run_state_store = run_state_store
        self._adapter_client = adapter_client
        self._run_service = run_service
        self._context_window = context_window
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = RLock()

    def bind_run_service(self, run_service: Any) -> None:
        self._run_service = run_service

    def describe_registered_tools(self) -> list[dict[str, Any]]:
        return []

    async def create_session(
        self,
        *,
        project_root: str,
        source: str,
        profile: str,
        reuse_existing: bool,
        zephyr_auth: dict[str, Any] | None = None,
        jira_instance: str | None = None,
    ) -> dict[str, Any]:
        normalized_project_root = str(Path(project_root).expanduser().resolve())
        payload, reused = self.state_store.create_session(
            project_root=normalized_project_root,
            source=source,
            profile=profile,
            runtime=self.name,
            reuse_existing=reuse_existing,
        )
        self.state_store.update_session(
            payload["session_id"],
            activity="idle",
            current_action="Idle",
            totals={"tokens": {"input": 0, "output": 0, "reasoning": 0, "cacheRead": 0, "cacheWrite": 0}, "cost": 0.0},
            limits={"contextWindow": self._context_window, "used": 0, "percent": 0.0},
            diff={"summary": {"files": 0, "additions": 0, "deletions": 0}, "files": []},
            zephyr_auth=zephyr_auth,
            jira_instance=jira_instance,
            active_run_id=None,
            active_run_status=None,
            active_run_backend="opencode-adapter",
        )
        try:
            adapter_session = await asyncio.to_thread(
                self._adapter_client.ensure_session,
                {
                    "externalSessionId": payload["session_id"],
                    "projectRoot": normalized_project_root,
                    "source": source,
                    "profile": profile,
                },
            )
        except OpenCodeAdapterError as exc:
            self.state_store.update_session(
                payload["session_id"],
                activity="error",
                current_action=str(exc),
            )
            raise _adapter_error_to_runtime_error(exc) from exc
        self.state_store.update_session(
            payload["session_id"],
            backend_session_id=adapter_session.get("backendSessionId"),
            current_action=str(adapter_session.get("currentAction") or "Idle"),
        )
        session = self._require_session(payload["session_id"])
        return {
            "sessionId": session["session_id"],
            "createdAt": session["created_at"],
            "runtime": session.get("runtime", self.name),
            "reused": reused,
            "projectRoot": session["project_root"],
            "source": session["source"],
            "profile": session["profile"],
            "memorySnapshot": session.get("memory_snapshot", {}),
        }

    async def list_sessions(self, *, project_root: str, limit: int = 50) -> dict[str, Any]:
        rows = self.state_store.list_sessions(project_root, limit=limit)
        items = []
        for row in rows:
            messages = row.get("messages", [])
            last_preview = None
            for message in reversed(messages):
                if message.get("role") in {"assistant", "system"}:
                    last_preview = str(message.get("content", ""))[:160]
                    break
            items.append(
                {
                    "sessionId": row["session_id"],
                    "projectRoot": row["project_root"],
                    "source": row.get("source", "ide-plugin"),
                    "profile": row.get("profile", "quick"),
                    "runtime": row.get("runtime", self.name),
                    "status": row.get("status", "active"),
                    "activity": row.get("activity", "idle"),
                    "currentAction": row.get("current_action", "Idle"),
                    "createdAt": row["created_at"],
                    "updatedAt": row["updated_at"],
                    "lastMessagePreview": last_preview,
                    "pendingPermissionsCount": len(row.get("pending_tool_calls", [])),
                }
            )
        return {"items": items, "total": len(items)}

    async def has_session(self, session_id: str) -> bool:
        session = self.state_store.get_session(session_id)
        return session is not None and str(session.get("runtime", self.name)) == self.name

    async def process_message(self, *, session_id: str, run_id: str, message_id: str, content: str) -> None:
        if self._run_service is None:
            raise ChatRuntimeError("OpenCode runtime is not initialized", status_code=503)
        lock = self._session_lock(session_id)
        async with lock:
            session = self._require_session(session_id)
            self.state_store.append_message(session_id, role="user", content=content, run_id=run_id, message_id=message_id)
            self.state_store.append_event(session_id, "message.received", {"sessionId": session_id, "runId": run_id})
            created = self._run_service.create_run(
                plugin="opencode",
                project_root=str(session.get("project_root", "")),
                input_payload={"prompt": content, "messageId": message_id, "backendSessionId": session.get("backend_session_id")},
                session_id=session_id,
                profile=str(session.get("profile", "quick")),
                source=str(session.get("source", "ide-plugin")),
                priority="normal",
                idempotency_key=None,
                requested_run_id=run_id,
            )
            self.state_store.update_session(
                session_id,
                activity="busy",
                current_action="Dispatching OpenCode run",
                active_run_id=created["run_id"],
                active_run_status=created["status"],
                active_run_backend="opencode-adapter",
            )
            self.state_store.append_event(session_id, "opencode.run_created", {"sessionId": session_id, "runId": created["run_id"]})

    async def get_history(self, *, session_id: str, limit: int = 200) -> dict[str, Any]:
        session = self._require_session(session_id)
        history = self.state_store.history(session["session_id"], limit=limit)
        if not history:
            raise ChatRuntimeError(f"Session not found: {session_id}", status_code=404)
        return {
            "sessionId": history["session_id"],
            "projectRoot": history["project_root"],
            "source": history.get("source", "ide-plugin"),
            "profile": history.get("profile", "quick"),
            "runtime": history.get("runtime", self.name),
            "status": history.get("status", "active"),
            "messages": [{"messageId": item["message_id"], "role": item["role"], "content": item["content"], "runId": item.get("run_id"), "metadata": item.get("metadata", {}), "createdAt": item["created_at"]} for item in history.get("messages", [])],
            "events": [{"eventType": event["event_type"], "payload": event["payload"], "createdAt": event["created_at"], "index": event["index"]} for event in history.get("events", [])],
            "pendingPermissions": [{"permissionId": item["tool_call_id"], "title": item.get("title", item["tool_name"]), "kind": item.get("kind", "tool"), "callId": item["tool_call_id"], "messageId": item.get("message_id"), "metadata": {"risk": item.get("risk_level", "read"), **item.get("args", {})}, "createdAt": item["created_at"]} for item in history.get("pending_tool_calls", [])],
            "memorySnapshot": history.get("memory_snapshot", {}),
            "updatedAt": history["updated_at"],
        }

    async def get_status(self, *, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        await self._refresh_session_mapping(session_id=session_id, current_session=session)
        session = self._require_session(session_id)
        events = session.get("events", [])
        last_event = events[-1]["created_at"] if events else session.get("updated_at", _utcnow())
        return {
            "sessionId": session["session_id"],
            "runtime": session.get("runtime", self.name),
            "activity": session.get("activity", "idle"),
            "currentAction": session.get("current_action", "Idle"),
            "lastEventAt": last_event,
            "updatedAt": session.get("updated_at", _utcnow()),
            "pendingPermissionsCount": len(session.get("pending_tool_calls", [])),
            "activeRunId": session.get("active_run_id"),
            "activeRunStatus": session.get("active_run_status"),
            "activeRunBackend": session.get("active_run_backend"),
            "totals": session.get("totals", {"tokens": {"input": 0, "output": 0, "reasoning": 0, "cacheRead": 0, "cacheWrite": 0}, "cost": 0.0}),
            "limits": session.get("limits", {"contextWindow": self._context_window, "used": 0, "percent": 0.0}),
            "lastRetryMessage": session.get("last_retry_message"),
            "lastRetryAttempt": session.get("last_retry_attempt"),
            "lastRetryAt": session.get("last_retry_at"),
        }

    async def get_diff(self, *, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        try:
            payload = await asyncio.to_thread(self._adapter_client.get_session_diff, session_id)
        except OpenCodeAdapterError as exc:
            raise _adapter_error_to_runtime_error(exc) from exc
        diff = {
            "summary": dict(payload.get("summary") or {"files": 0, "additions": 0, "deletions": 0}),
            "files": list(payload.get("files") or []),
        }
        self.state_store.update_session(
            session_id,
            backend_session_id=payload.get("backendSessionId") or session.get("backend_session_id"),
            diff=diff,
        )
        updated = self._require_session(session_id)
        return {
            "sessionId": updated["session_id"],
            "runtime": updated.get("runtime", self.name),
            "summary": diff.get("summary", {"files": 0, "additions": 0, "deletions": 0}),
            "files": diff.get("files", []),
            "updatedAt": payload.get("updatedAt") or updated.get("updated_at", _utcnow()),
        }

    async def execute_command(self, *, session_id: str, command: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        active_run_id = str(session.get("active_run_id") or "").strip() or None
        if command == "abort":
            if not active_run_id or self._run_service is None:
                raise ChatRuntimeError("No active OpenCode run to cancel", status_code=409)
            result = {"ok": True, "cancel": self._run_service.cancel_run(active_run_id)}
        elif command == "compact":
            self.state_store.append_event(session_id, "opencode.compact.started", {"sessionId": session_id})
            try:
                adapter_result = await asyncio.to_thread(self._adapter_client.compact_session, session_id)
            except OpenCodeAdapterError as exc:
                self.state_store.append_event(
                    session_id,
                    "opencode.compact.failed",
                    {"sessionId": session_id, "message": str(exc)},
                )
                raise _adapter_error_to_runtime_error(exc) from exc
            self.state_store.append_event(
                session_id,
                "opencode.compact.succeeded",
                {"sessionId": session_id, "result": adapter_result.get("result") or {}},
            )
            result = dict(adapter_result.get("result") or {})
        elif command in {"status", "diff", "help"}:
            try:
                adapter_result = await asyncio.to_thread(self._adapter_client.execute_session_command, session_id, command)
            except OpenCodeAdapterError as exc:
                raise _adapter_error_to_runtime_error(exc) from exc
            result = dict(adapter_result.get("result") or {})
            if command == "status":
                status_payload = dict(result.get("status") or {})
                if status_payload:
                    self._apply_session_mapping_status(session_id=session_id, payload=status_payload)
            elif command == "diff":
                diff_payload = dict(result.get("diff") or {})
                if diff_payload:
                    self.state_store.update_session(
                        session_id,
                        diff={
                            "summary": dict(diff_payload.get("summary") or {}),
                            "files": list(diff_payload.get("files") or []),
                        },
                    )
        else:
            raise ChatRuntimeError(f"Unsupported command: {command}", status_code=422)
        self.state_store.append_event(session_id, "command.executed", {"sessionId": session_id, "command": command})
        updated = self._require_session(session_id)
        return {"sessionId": session_id, "command": command, "accepted": True, "result": result, "updatedAt": updated.get("updated_at", _utcnow())}

    async def process_tool_decision(self, *, session_id: str, run_id: str, permission_id: str, decision: str) -> None:
        _ = run_id
        pending = self.state_store.get_pending_tool_call(session_id, permission_id)
        if not pending:
            raise ChatRuntimeError(f"Permission not found: {permission_id}", status_code=404)
        backend_run_id = str((pending.get("args") or {}).get("backend_run_id") or "").strip()
        if not backend_run_id:
            raise ChatRuntimeError("OpenCode approval is missing backend run id", status_code=422)
        public_decision = "approve" if decision in {"approve_once", "approve_always"} else "deny"
        try:
            await asyncio.to_thread(self._adapter_client.submit_approval_decision, backend_run_id, permission_id, public_decision)
        except OpenCodeAdapterError as exc:
            raise _adapter_error_to_runtime_error(exc) from exc
        self.state_store.pop_pending_tool_call(session_id, permission_id)
        self.state_store.append_event(session_id, "permission.approved" if public_decision == "approve" else "permission.rejected", {"sessionId": session_id, "permissionId": permission_id})
        session = self._require_session(session_id)
        self.state_store.update_session(session_id, activity="busy" if session.get("active_run_status") not in TERMINAL_RUN_STATUSES else "idle", current_action="Approval decision sent")

    async def stream_events(self, *, session_id: str, from_index: int = 0) -> AsyncIterator[bytes]:
        _ = self._require_session(session_id)
        index = max(0, from_index)
        loop = asyncio.get_running_loop()
        heartbeat_interval_s = 2.0
        last_emit_ts = loop.time()
        while True:
            events, next_index = self.state_store.list_events(session_id, since_index=index)
            if events:
                for event in events:
                    payload = {"eventType": event["event_type"], "payload": event["payload"], "createdAt": event["created_at"], "index": event["index"]}
                    chunk = f"event: {event['event_type']}\n" f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    yield chunk.encode("utf-8")
                last_emit_ts = loop.time()
            else:
                now = loop.time()
                if now - last_emit_ts >= heartbeat_interval_s:
                    payload = {"eventType": "heartbeat", "payload": {"sessionId": session_id}, "createdAt": _utcnow(), "index": next_index}
                    yield f"event: heartbeat\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
                    last_emit_ts = now
            index = next_index
            await asyncio.sleep(0.15)

    async def _refresh_session_mapping(self, *, session_id: str, current_session: dict[str, Any]) -> None:
        active_run_status = str(current_session.get("active_run_status") or "").strip().lower()
        if active_run_status and active_run_status not in {"succeeded", "failed", "cancelled"}:
            return
        try:
            payload = await asyncio.to_thread(self._adapter_client.get_session, session_id)
        except OpenCodeAdapterError:
            return
        self._apply_session_mapping_status(session_id=session_id, payload=payload)

    def _apply_session_mapping_status(self, *, session_id: str, payload: dict[str, Any]) -> None:
        status_value = str(payload.get("status") or "").strip().lower()
        activity = "idle"
        if status_value in {"queued", "running", "busy"}:
            activity = "busy"
        elif status_value in {"failed", "error"}:
            activity = "error"
        if self.state_store.list_pending_tool_calls(session_id=session_id):
            activity = "waiting_permission"
        self.state_store.update_session(
            session_id,
            backend_session_id=payload.get("backendSessionId") or payload.get("backend_session_id"),
            current_action=str(payload.get("currentAction") or payload.get("current_action") or "Idle"),
            activity=activity,
        )

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        with self._locks_guard:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[session_id] = lock
            return lock

    def _require_session(self, session_id: str) -> dict[str, Any]:
        session = self.state_store.get_session(session_id)
        if not session or str(session.get("runtime", self.name)) != self.name:
            raise ChatRuntimeError(f"Session not found: {session_id}", status_code=404)
        return session

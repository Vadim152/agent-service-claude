from __future__ import annotations

import os
from pathlib import Path
from typing import Any
import itertools

from fastapi import HTTPException, status

from opencode_adapter_app.process_supervisor import OpenCodeProcessSupervisor
from opencode_adapter_app.schemas import (
    AdapterApprovalDecisionRequest,
    AdapterApprovalDecisionResponse,
    AdapterRunCancelResponse,
    AdapterRunCreateRequest,
    AdapterRunCreateResponse,
    AdapterRunEventDto,
    AdapterRunEventsResponse,
    AdapterRunStatusResponse,
)
from opencode_adapter_app.state_store import OpenCodeAdapterStateStore, utcnow


class OpenCodeAdapterService:
    def __init__(
        self,
        *,
        settings: Any,
        state_store: OpenCodeAdapterStateStore,
        process_supervisor: OpenCodeProcessSupervisor,
    ) -> None:
        self._settings = settings
        self._state_store = state_store
        self._process_supervisor = process_supervisor
        self._id_counter = itertools.count(1)

    def create_run(self, request: AdapterRunCreateRequest) -> AdapterRunCreateResponse:
        if not request.project_root.strip():
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="projectRoot must not be empty")
        if not request.prompt.strip():
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="prompt must not be empty")

        project_root = str(Path(request.project_root).resolve())
        backend_session_id = request.backend_session_id
        if not backend_session_id and request.session_id:
            session_mapping = self._state_store.get_session_mapping(request.session_id)
            if session_mapping:
                mapped_project_root = str(session_mapping.get("project_root") or "").strip()
                if (
                    str(request.source or "").strip().lower() == "ide-plugin"
                    and mapped_project_root
                    and _normalized_path(mapped_project_root) != _normalized_path(project_root)
                ):
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=(
                            "projectRoot mismatch for existing sessionId: "
                            f"expected {mapped_project_root}, got {project_root}"
                        ),
                    )
                if mapped_project_root and _normalized_path(mapped_project_root) == _normalized_path(project_root):
                    backend_session_id = str(session_mapping.get("backend_session_id") or "").strip() or None

        backend_run_id = f"oc-adapter-{next(self._id_counter)}"
        now = utcnow().isoformat()
        run = {
            "backend_run_id": backend_run_id,
            "external_run_id": request.run_id,
            "external_session_id": request.session_id,
            "backend_session_id": backend_session_id,
            "project_root": project_root,
            "prompt": request.prompt,
            "source": request.source,
            "profile": request.profile,
            "config_profile": request.config_profile,
            "policy_mode": request.policy_mode,
            "status": "queued",
            "current_action": "Queued",
            "result": None,
            "output": None,
            "artifacts": [],
            "pending_approvals": [],
            "cancel_requested": False,
            "exit_code": None,
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "updated_at": now,
            "work_dir": str((self._settings.work_root / "runs" / backend_run_id).resolve()),
        }
        self._state_store.create_run(run)
        self._state_store.append_event(backend_run_id, "run.queued", {"backendRunId": backend_run_id, "runId": request.run_id})
        self._process_supervisor.start_run(run)
        stored = self._require_run(backend_run_id)
        return AdapterRunCreateResponse(
            backendRunId=backend_run_id,
            backendSessionId=stored.get("backend_session_id"),
            status=stored["status"],
            currentAction=stored.get("current_action") or "Queued",
            createdAt=stored["created_at"],
            startedAt=stored.get("started_at"),
        )

    def get_run(self, backend_run_id: str) -> AdapterRunStatusResponse:
        run = self._require_run(backend_run_id)
        return AdapterRunStatusResponse(
            backendRunId=backend_run_id,
            backendSessionId=run.get("backend_session_id"),
            status=run["status"],
            currentAction=run.get("current_action") or "Queued",
            result=run.get("result"),
            output=run.get("output"),
            artifacts=run.get("artifacts") or [],
            pendingApprovals=run.get("pending_approvals") or [],
            totals=run.get("totals"),
            limits=run.get("limits"),
            createdAt=run["created_at"],
            startedAt=run.get("started_at"),
            finishedAt=run.get("finished_at"),
            exitCode=run.get("exit_code"),
            updatedAt=run["updated_at"],
        )

    def list_events(self, backend_run_id: str, after: int) -> AdapterRunEventsResponse:
        self._require_run(backend_run_id)
        events, next_cursor = self._state_store.list_events(backend_run_id, after=after)
        return AdapterRunEventsResponse(
            items=[
                AdapterRunEventDto(
                    eventType=item["event_type"],
                    payload=item["payload"],
                    createdAt=item["created_at"],
                    index=item["index"],
                )
                for item in events
            ],
            nextCursor=next_cursor,
        )

    def cancel_run(self, backend_run_id: str) -> AdapterRunCancelResponse:
        self._require_run(backend_run_id)
        updated = self._process_supervisor.cancel_run(backend_run_id)
        return AdapterRunCancelResponse(
            backendRunId=backend_run_id,
            status=str(updated.get("status") or "running"),
            updatedAt=updated.get("updated_at") or utcnow(),
        )

    def submit_approval_decision(
        self,
        backend_run_id: str,
        approval_id: str,
        request: AdapterApprovalDecisionRequest,
    ) -> AdapterApprovalDecisionResponse:
        run = self._require_run(backend_run_id)
        pending = {
            str(item.get("approval_id") or item.get("approvalId") or "")
            for item in run.get("pending_approvals") or []
        }
        if approval_id not in pending:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Approval not found: {approval_id}")
        updated = self._process_supervisor.submit_approval_decision(backend_run_id, approval_id, request.decision)
        return AdapterApprovalDecisionResponse(
            backendRunId=backend_run_id,
            approvalId=approval_id,
            decision=request.decision,
            status=str(updated.get("status") or "running"),
            updatedAt=updated.get("updated_at") or utcnow(),
        )

    def _require_run(self, backend_run_id: str) -> dict[str, Any]:
        run = self._state_store.get_run(backend_run_id)
        if not run:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run not found: {backend_run_id}")
        return run


def _normalized_path(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))

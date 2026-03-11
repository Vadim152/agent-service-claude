"""Shared run-domain service for `/runs` control-plane APIs."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status


logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunService:
    def __init__(
        self,
        *,
        run_state_store: Any,
        supervisor: Any | None,
        dispatcher: Any | None = None,
        task_registry: Any | None = None,
        plugin_drivers: dict[str, Any] | None = None,
    ) -> None:
        self._run_state_store = run_state_store
        self._supervisor = supervisor
        self._dispatcher = dispatcher
        self._task_registry = task_registry
        self._plugin_drivers = plugin_drivers or {}

    def build_testgen_run_record(
        self,
        *,
        run_id: str,
        project_root: str,
        test_case_text: str,
        plan_id: str | None,
        selected_scenario_id: str | None,
        selected_scenario_candidate_id: str | None,
        accepted_assumption_ids: list[str] | None,
        clarifications: dict[str, Any] | None,
        binding_overrides: list[dict[str, Any]] | None,
        target_path: str | None,
        create_file: bool,
        overwrite_existing: bool,
        language: str | None,
        quality_policy: str,
        zephyr_auth: dict[str, Any] | None,
        jira_instance: str | None,
        profile: str,
        source: str,
        session_id: str | None,
        priority: str | None,
        input_payload: dict[str, Any],
        quality_policy_explicit: bool,
        plugin: str = "testgen",
    ) -> dict[str, Any]:
        if not test_case_text.strip() and not str(plan_id or "").strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="testgen runs require input.testCaseText, input.jiraKey, or input.planId",
            )
        if quality_policy not in {"strict", "balanced", "lenient"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported qualityPolicy: {quality_policy}",
            )

        return {
            "run_id": run_id,
            "status": "queued",
            "plugin": plugin,
            "cancel_requested": False,
            "cancel_requested_at": None,
            "project_root": project_root,
            "test_case_text": test_case_text,
            "plan_id": plan_id,
            "selected_scenario_id": selected_scenario_id,
            "selected_scenario_candidate_id": selected_scenario_candidate_id,
            "accepted_assumption_ids": list(accepted_assumption_ids or []),
            "clarifications": dict(clarifications or {}),
            "binding_overrides": list(binding_overrides or []),
            "target_path": target_path,
            "create_file": create_file,
            "overwrite_existing": overwrite_existing,
            "language": language,
            "quality_policy": quality_policy,
            "quality_policy_explicit": quality_policy_explicit,
            "zephyr_auth": zephyr_auth,
            "jira_instance": jira_instance,
            "profile": profile,
            "source": source,
            "session_id": session_id,
            "priority": priority,
            "input": dict(input_payload),
            "started_at": _utcnow(),
            "updated_at": _utcnow(),
            "attempts": [],
            "result": None,
        }

    def create_run(
        self,
        *,
        plugin: str,
        project_root: str,
        input_payload: dict[str, Any],
        session_id: str | None,
        profile: str,
        source: str,
        priority: str,
        idempotency_key: str | None,
        requested_run_id: str | None = None,
    ) -> dict[str, Any]:
        request_payload = {
            "projectRoot": project_root,
            "plugin": plugin,
            "input": dict(input_payload),
            "sessionId": session_id,
            "profile": profile,
            "source": source,
            "priority": priority,
        }
        payload_fingerprint = hashlib.sha256(
            json.dumps(request_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

        run_id = str(requested_run_id or "").strip() or payload_fingerprint[:8] + "-" + payload_fingerprint[8:16]
        if idempotency_key:
            claimed, existing_run_id = self._run_state_store.claim_idempotency_key(
                idempotency_key,
                fingerprint=payload_fingerprint,
                run_id=run_id,
            )
            if not claimed and existing_run_id:
                existing = self._run_state_store.get_job(existing_run_id)
                if not existing:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Idempotency key is bound to missing run state",
                    )
                return {
                    "run_id": existing_run_id,
                    "status": str(existing.get("status", "queued")),
                    "session_id": existing.get("session_id"),
                    "plugin": str(existing.get("plugin", plugin)),
                }
            if not claimed and not existing_run_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency key reuse with different payload is not allowed",
                )
        elif not requested_run_id:
            run_id = hashlib.sha1((payload_fingerprint + _utcnow()).encode("utf-8")).hexdigest()

        if plugin == "testgen":
            test_case_text = str(
                input_payload.get("testCaseText")
                or input_payload.get("test_case_text")
                or input_payload.get("jiraKey")
                or ""
            ).strip()
            quality_policy = str(input_payload.get("qualityPolicy") or input_payload.get("quality_policy") or "strict")
            job_payload = self.build_testgen_run_record(
                run_id=run_id,
                project_root=project_root,
                test_case_text=test_case_text,
                plan_id=input_payload.get("planId") or input_payload.get("plan_id"),
                selected_scenario_id=input_payload.get("selectedScenarioId") or input_payload.get("selected_scenario_id"),
                selected_scenario_candidate_id=input_payload.get("selectedScenarioCandidateId")
                or input_payload.get("selected_scenario_candidate_id"),
                accepted_assumption_ids=input_payload.get("acceptedAssumptionIds")
                or input_payload.get("accepted_assumption_ids")
                or [],
                clarifications=input_payload.get("clarifications") or {},
                binding_overrides=input_payload.get("bindingOverrides") or input_payload.get("binding_overrides") or [],
                target_path=input_payload.get("targetPath") or input_payload.get("target_path"),
                create_file=bool(input_payload.get("createFile", input_payload.get("create_file", False))),
                overwrite_existing=bool(
                    input_payload.get("overwriteExisting", input_payload.get("overwrite_existing", False))
                ),
                language=input_payload.get("language"),
                quality_policy=quality_policy,
                zephyr_auth=input_payload.get("zephyrAuth") or input_payload.get("zephyr_auth"),
                jira_instance=input_payload.get("jiraInstance") or input_payload.get("jira_instance"),
                profile=profile,
                source=source,
                session_id=session_id,
                priority=priority,
                input_payload=input_payload,
                quality_policy_explicit="qualityPolicy" in input_payload or "quality_policy" in input_payload,
                plugin=plugin,
            )
        elif plugin == "opencode":
            prompt = str(input_payload.get("prompt") or input_payload.get("message") or input_payload.get("content") or "").strip()
            if not prompt:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="opencode runs require input.prompt or input.message",
                )
            job_payload = {
                "run_id": run_id,
                "status": "queued",
                "plugin": plugin,
                "runtime": "opencode",
                "backend": "opencode-adapter",
                "backend_run_id": None,
                "backend_session_id": input_payload.get("backendSessionId") or input_payload.get("backend_session_id"),
                "sync_cursor": 0,
                "last_synced_at": None,
                "cancel_requested": False,
                "cancel_requested_at": None,
                "project_root": project_root,
                "profile": profile,
                "source": source,
                "session_id": session_id,
                "priority": priority,
                "input": dict(input_payload),
                "started_at": _utcnow(),
                "updated_at": _utcnow(),
                "attempts": [],
                "result": None,
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported plugin: {plugin}",
            )
        self._run_state_store.put_job(job_payload)
        self._run_state_store.append_event(
            run_id,
            "run.queued",
            {
                "runId": run_id,
                "plugin": plugin,
                "source": source,
                "sessionId": session_id,
            },
        )
        self.schedule_execution(run_id=run_id, source="runs")
        return {
            "run_id": run_id,
            "status": "queued",
            "session_id": session_id,
            "plugin": plugin,
        }

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self._run_state_store.get_job(run_id)

    def list_events(self, run_id: str, since_index: int) -> tuple[list[dict[str, Any]], int]:
        return self._run_state_store.list_events(run_id, since_index)

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        item = self.get_run(run_id)
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run not found: {run_id}")

        current_status = str(item.get("status", "queued"))
        if current_status in {"succeeded", "failed", "needs_attention", "cancelled"}:
            return {
                "run_id": run_id,
                "status": current_status,
                "cancel_requested": False,
                "effective_status": current_status,
            }

        next_status = "cancelling" if current_status in {"queued", "running", "cancelling"} else "cancelled"
        updated = self._run_state_store.patch_job(
            run_id,
            status=next_status,
            cancel_requested=True,
            cancel_requested_at=_utcnow(),
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run not found: {run_id}")

        self._run_state_store.append_event(
            run_id,
            "run.cancellation_requested",
            {"runId": run_id, "status": next_status},
        )
        return {
            "run_id": run_id,
            "status": next_status,
            "cancel_requested": True,
            "effective_status": next_status,
        }

    def schedule_execution(self, *, run_id: str, source: str) -> None:
        run = self.get_run(run_id)
        if not run:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run not found: {run_id}")
        plugin = str(run.get("plugin", "testgen"))
        plugin_driver = self._plugin_drivers.get(plugin)

        if plugin_driver is None and self._supervisor is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Run control plane is not initialized",
            )

        async def _worker() -> None:
            if plugin_driver is not None:
                await plugin_driver.execute_run(run_id)
                return
            execute = getattr(self._supervisor, "execute_run", None) or self._supervisor.execute_job
            await execute(run_id)

        def _on_error(exc: BaseException) -> None:
            logger.warning("Run execution failed (run_id=%s): %s", run_id, exc)
            self._run_state_store.patch_job(
                run_id,
                status="needs_attention",
                finished_at=_utcnow(),
                incident_uri=None,
            )
            self._run_state_store.append_event(
                run_id,
                "run.worker_failed",
                {"runId": run_id, "status": "needs_attention", "message": str(exc)},
            )

        if plugin_driver is None and self._dispatcher is not None:
            try:
                self._dispatcher.dispatch(
                    run_id=run_id,
                    source=source,
                    supervisor=self._supervisor,
                    run_state_store=self._run_state_store,
                    task_registry=self._task_registry,
                    on_error=_on_error,
                )
                return
            except Exception as exc:
                _on_error(exc)
                return

        if self._task_registry is None:
            import asyncio
            import threading

            if plugin_driver is not None:
                def _run_plugin_driver() -> None:
                    try:
                        asyncio.run(_worker())
                    except Exception as exc:
                        _on_error(exc)

                threading.Thread(target=_run_plugin_driver, daemon=True).start()
                return
            asyncio.create_task(_worker())
            return

        self._task_registry.create_task(
            _worker(),
            source=source,
            metadata={"runId": run_id},
            on_error=_on_error,
        )

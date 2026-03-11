"""ExecutionSupervisor: explicit state machine run -> classify -> remediate -> rerun."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.observability import metrics, traced_span
from infrastructure.artifact_store import ArtifactStore
from infrastructure.run_state_store import RunStateStore
from self_healing.failure_classifier import FailureClassifier
from self_healing.remediation import RemediationPlaybooks


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


logger = logging.getLogger(__name__)


class ExecutionSupervisor:
    def __init__(
        self,
        *,
        orchestrator,
        run_state_store: RunStateStore,
        artifact_store: ArtifactStore,
        max_auto_reruns: int = 2,
        max_total_duration_s: int = 300,
    ) -> None:
        self.orchestrator = orchestrator
        self.run_state_store = run_state_store
        self.artifact_store = artifact_store
        self.classifier = FailureClassifier()
        self.playbooks = RemediationPlaybooks()
        self.max_auto_reruns = max_auto_reruns
        self.max_total_duration_s = max_total_duration_s

    def _limits_for_profile(self, profile: str) -> tuple[int, int]:
        normalized = (profile or "quick").strip().lower()
        if normalized == "strict":
            return max(self.max_auto_reruns, 2), max(self.max_total_duration_s, 300)
        if normalized == "ci":
            return max(self.max_auto_reruns, 3), max(self.max_total_duration_s, 600)
        return min(self.max_auto_reruns, 1), min(self.max_total_duration_s, 180)

    def _is_cancellation_requested(self, run_id: str) -> bool:
        run_record = self.run_state_store.get_job(run_id)
        if not run_record:
            return True
        if bool(run_record.get("cancel_requested")):
            return True
        status = str(run_record.get("status", "")).strip().lower()
        return status in {"cancelling", "cancelled"}

    async def execute_run(self, run_id: str) -> None:
        run_record = self.run_state_store.get_job(run_id)
        if not run_record:
            return
        if self._is_cancellation_requested(run_id):
            self.run_state_store.patch_job(run_id, status="cancelled", finished_at=_utcnow())
            self.run_state_store.append_event(
                run_id,
                "run.cancelled",
                {"runId": run_id, "status": "cancelled", "reason": "cancelled_before_start"},
            )
            return
        metrics.inc("jobs.started")

        profile = str(run_record.get("profile", "quick"))
        max_auto_reruns, max_total_duration_s = self._limits_for_profile(profile)

        execution_id = str(uuid.uuid4())
        self.run_state_store.patch_job(run_id, execution_id=execution_id, status="running")
        self.run_state_store.append_event(
            run_id,
            "run.running",
            {"runId": run_id, "executionId": execution_id, "profile": profile},
        )
        logger.info("Run running", extra={"runId": run_id, "executionId": execution_id, "profile": profile})

        start = asyncio.get_running_loop().time()
        succeeded = False
        cancelled = False
        incident: dict[str, Any] | None = None
        latest_result: dict[str, Any] | None = None

        for attempt_index in range(max_auto_reruns + 1):
            if asyncio.get_running_loop().time() - start > max_total_duration_s:
                incident = self._build_incident(
                    run_record,
                    "",
                    run_id,
                    execution_id,
                    {"category": "unknown", "confidence": 0.0},
                    "Total duration limit exceeded",
                )
                break

            attempt_id = str(uuid.uuid4())
            self.run_state_store.append_attempt(
                run_id,
                {
                    "attempt_id": attempt_id,
                    "attempt_no": attempt_index + 1,
                    "status": "started",
                    "started_at": _utcnow(),
                    "artifacts": {},
                },
            )
            self.run_state_store.append_event(
                run_id,
                "attempt.started",
                {
                    "runId": run_id,
                    "executionId": execution_id,
                    "attemptId": attempt_id,
                    "status": "started",
                },
            )

            if self._is_cancellation_requested(run_id):
                self.run_state_store.patch_attempt(
                    run_id,
                    attempt_id,
                    status="cancelled",
                    finished_at=_utcnow(),
                    artifacts={},
                )
                cancelled = True
                break

            artifacts: dict[str, str] = {}
            try:
                with traced_span("run_test_execution"):
                    result = self.orchestrator.generate_feature(
                        run_record["project_root"],
                        run_record["test_case_text"],
                        run_record.get("target_path"),
                        create_file=bool(run_record.get("create_file")),
                        overwrite_existing=bool(run_record.get("overwrite_existing")),
                        language=run_record.get("language"),
                        quality_policy=run_record.get("quality_policy", "strict"),
                        explicit_quality_policy=bool(run_record.get("quality_policy_explicit", False)),
                        explicit_language=run_record.get("language") is not None,
                        explicit_target_path=run_record.get("target_path") is not None,
                        zephyr_auth=run_record.get("zephyr_auth"),
                        jira_instance=run_record.get("jira_instance"),
                        plan_id=run_record.get("plan_id"),
                        selected_scenario_id=run_record.get("selected_scenario_id"),
                        selected_scenario_candidate_id=run_record.get("selected_scenario_candidate_id"),
                        accepted_assumption_ids=run_record.get("accepted_assumption_ids") or [],
                        clarifications=run_record.get("clarifications") or {},
                        binding_overrides=run_record.get("binding_overrides") or [],
                    )
                latest_result = result
                if self._is_cancellation_requested(run_id):
                    self.run_state_store.patch_attempt(
                        run_id,
                        attempt_id,
                        status="cancelled",
                        finished_at=_utcnow(),
                        artifacts=artifacts,
                    )
                    self.run_state_store.append_event(
                        run_id,
                        "attempt.cancelled",
                        {"runId": run_id, "executionId": execution_id, "attemptId": attempt_id, "status": "cancelled"},
                    )
                    cancelled = True
                    break
                feature_payload = result.get("feature", {})
                unmatched = feature_payload.get("unmappedSteps", [])
                generation_meta = feature_payload.get("meta") or {}
                generation_blocked = bool(generation_meta.get("generationBlocked", False))
                blocking_reason = str(
                    generation_meta.get("blockingReason")
                    or "Generation blocked until required clarifications are provided"
                )
                quality_payload = feature_payload.get("quality") or {}
                quality_passed = True
                if isinstance(quality_payload, dict):
                    quality_passed = bool(quality_payload.get("passed", False))
                if generation_blocked:
                    quality_passed = False
                has_failure = generation_blocked or (len(unmatched) > 0) or (not quality_passed)

                feature_result_artifact = self.artifact_store.publish_json(
                    run_id=run_id,
                    execution_id=execution_id,
                    attempt_id=attempt_id,
                    name="feature-result.json",
                    payload=result,
                )
                artifacts["featureResult"] = str(feature_result_artifact["uri"])
                classifier_input = {
                    "unmatched": str(unmatched),
                    "qualityFailures": str(quality_payload.get("failures", []))
                    if isinstance(quality_payload, dict)
                    else "[]",
                    "artifactUri": artifacts["featureResult"],
                }

                if generation_blocked:
                    classification_payload = {
                        "category": "requirements",
                        "confidence": 1.0,
                        "signals": ["generation_blocked"],
                        "summary": blocking_reason,
                    }
                    self.run_state_store.patch_attempt(
                        run_id,
                        attempt_id,
                        status="failed",
                        finished_at=_utcnow(),
                        classification=classification_payload,
                        artifacts=artifacts,
                    )
                    self.run_state_store.append_event(
                        run_id,
                        "attempt.blocked",
                        {
                            "runId": run_id,
                            "executionId": execution_id,
                            "attemptId": attempt_id,
                            "status": "failed",
                            "classification": classification_payload,
                        },
                    )
                    incident = self._build_incident(
                        run_record,
                        attempt_id,
                        run_id,
                        execution_id,
                        classification_payload,
                        blocking_reason,
                    )
                    break

                if not has_failure:
                    succeeded = True
                    metrics.inc("jobs.succeeded_without_rerun")
                    self.run_state_store.patch_attempt(
                        run_id,
                        attempt_id,
                        status="succeeded",
                        finished_at=_utcnow(),
                        artifacts=artifacts,
                    )
                    self.run_state_store.append_event(
                        run_id,
                        "attempt.succeeded",
                        {"runId": run_id, "executionId": execution_id, "attemptId": attempt_id, "status": "succeeded"},
                    )
                    break

                classification = self.classifier.classify(classifier_input)
                classification_payload = classification.to_dict()
                classification_artifact = self.artifact_store.publish_json(
                    run_id=run_id,
                    execution_id=execution_id,
                    attempt_id=attempt_id,
                    name="failure-classification.json",
                    payload=classification_payload,
                )
                artifacts["failureClassification"] = str(classification_artifact["uri"])
                self.run_state_store.patch_attempt(
                    run_id,
                    attempt_id,
                    status="failed",
                    classification=classification_payload,
                    artifacts=artifacts,
                )
                self.run_state_store.append_event(
                    run_id,
                    "attempt.classified",
                    {
                        "runId": run_id,
                        "executionId": execution_id,
                        "attemptId": attempt_id,
                        "status": "failed",
                        "classification": classification_payload,
                    },
                )

                if classification.confidence < 0.55:
                    metrics.inc("jobs.low_confidence_failures")
                    self.run_state_store.patch_attempt(
                        run_id,
                        attempt_id,
                        status="failed",
                        finished_at=_utcnow(),
                        classification=classification_payload,
                        artifacts=artifacts,
                    )
                    incident = self._build_incident(
                        run_record, attempt_id, run_id, execution_id, classification_payload, "Low confidence"
                    )
                    break

                decision = self.playbooks.decide(classification.category)
                remediation_payload = decision.to_dict()
                apply_result = self.playbooks.apply(decision)
                self.run_state_store.patch_attempt(
                    run_id,
                    attempt_id,
                    status="remediated",
                    classification=classification_payload,
                    remediation=remediation_payload,
                    artifacts=artifacts,
                )
                self.run_state_store.append_event(
                    run_id,
                    "attempt.remediated",
                    {
                        "runId": run_id,
                        "executionId": execution_id,
                        "attemptId": attempt_id,
                        "status": "remediated",
                        "remediation": remediation_payload,
                        "result": apply_result,
                    },
                )
                if not apply_result.get("applied"):
                    self.run_state_store.patch_attempt(
                        run_id,
                        attempt_id,
                        status="failed",
                        finished_at=_utcnow(),
                        classification=classification_payload,
                        remediation=remediation_payload,
                        artifacts=artifacts,
                    )
                    incident = self._build_incident(
                        run_record, attempt_id, run_id, execution_id, classification_payload, decision.notes
                    )
                    break

                self.run_state_store.patch_attempt(
                    run_id,
                    attempt_id,
                    status="rerun_scheduled",
                    finished_at=_utcnow(),
                    classification=classification_payload,
                    remediation=remediation_payload,
                    artifacts=artifacts,
                )
                self.run_state_store.append_event(
                    run_id,
                    "attempt.rerun_scheduled",
                    {
                        "runId": run_id,
                        "executionId": execution_id,
                        "attemptId": attempt_id,
                        "status": "rerun_scheduled",
                    },
                )
                metrics.inc("jobs.rerun_scheduled")
                await asyncio.sleep(0.05)
            except Exception as exc:
                classification_result = self.classifier.classify({"exception": str(exc)})
                classification_payload = classification_result.to_dict()
                self.run_state_store.patch_attempt(
                    run_id,
                    attempt_id,
                    status="failed",
                    finished_at=_utcnow(),
                    classification=classification_payload,
                    artifacts=artifacts,
                )
                incident = self._build_incident(
                    run_record, attempt_id, run_id, execution_id, classification_payload, str(exc)
                )
                break

        if cancelled:
            final_status = "cancelled"
        else:
            final_status = "succeeded" if succeeded else "needs_attention"
        metrics.inc(f"jobs.final_status.{final_status}")
        incident_uri = None
        if incident and final_status != "cancelled":
            incident_artifact = self.artifact_store.publish_incident(
                run_id=run_id,
                execution_id=execution_id,
                payload=incident,
            )
            incident_uri = str(incident_artifact["uri"])
            self.run_state_store.append_event(
                run_id,
                "run.incident",
                {
                    "runId": run_id,
                    "executionId": execution_id,
                    "incident": incident,
                    "incidentUri": incident_uri,
                },
            )

        feature_result = (
            self._build_feature_result(latest_result)
            if latest_result and final_status != "cancelled"
            else None
        )
        self.run_state_store.patch_job(
            run_id,
            status=final_status,
            finished_at=_utcnow(),
            incident_uri=incident_uri,
            result=feature_result,
        )
        if final_status == "cancelled":
            self.run_state_store.append_event(
                run_id,
                "run.cancelled",
                {"runId": run_id, "executionId": execution_id, "status": "cancelled"},
            )
        self.run_state_store.append_event(
            run_id,
            "run.finished",
            {
                "runId": run_id,
                "executionId": execution_id,
                "status": final_status,
                "incidentUri": incident_uri,
                "resultReady": bool(feature_result),
            },
        )

    async def execute_job(self, run_id: str) -> None:
        await self.execute_run(run_id)

    @staticmethod
    def _build_incident(
        run_record: dict[str, Any],
        attempt_id: str,
        run_id: str,
        execution_id: str,
        classification: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        return {
            "runId": run_id,
            "executionId": execution_id,
            "attemptId": attempt_id,
            "source": "execution_supervisor",
            "classification": classification,
            "summary": f"Auto-remediation stopped: {reason}",
            "createdAt": _utcnow(),
            "hypotheses": [
                "Check infrastructure dependencies",
                "Check test data and environment stability",
            ],
        }

    @staticmethod
    def _build_feature_result(result: dict[str, Any]) -> dict[str, Any]:
        feature_payload = result.get("feature", {})
        match_payload = result.get("matchResult", {})
        unmapped_steps_raw = feature_payload.get("unmappedSteps", [])
        unmapped_steps = [
            entry if isinstance(entry, dict) else {"text": str(entry), "reason": "not matched"}
            for entry in unmapped_steps_raw
        ]

        seen_ids: set[str] = set()
        used_steps: list[dict[str, Any]] = []
        for item in match_payload.get("matched", []):
            if not isinstance(item, dict):
                continue
            if item.get("status") == "unmatched":
                continue
            step_definition = item.get("step_definition")
            if not isinstance(step_definition, dict):
                continue
            step_id = str(step_definition.get("id", ""))
            if not step_id or step_id in seen_ids:
                continue
            seen_ids.add(step_id)
            used_steps.append(step_definition)

        return {
            "featureText": feature_payload.get("featureText", ""),
            "unmappedSteps": unmapped_steps,
            "unmapped": list(match_payload.get("unmatched", [])),
            "usedSteps": used_steps,
            "buildStage": feature_payload.get("buildStage"),
            "stepsSummary": feature_payload.get("stepsSummary"),
            "stepDetails": feature_payload.get("stepDetails", []),
            "parameterFillSummary": feature_payload.get("parameterFillSummary", {}),
            "meta": feature_payload.get("meta"),
            "quality": feature_payload.get("quality"),
            "coverageReport": feature_payload.get("coverageReport"),
            "pipeline": result.get("pipeline", []),
            "fileStatus": result.get("fileStatus"),
            "planId": (feature_payload.get("meta") or {}).get("planId"),
            "selectedScenarioId": (feature_payload.get("meta") or {}).get("selectedScenarioId"),
            "selectedScenarioCandidateId": (feature_payload.get("meta") or {}).get("selectedScenarioCandidateId"),
            "generationBlocked": bool((feature_payload.get("meta") or {}).get("generationBlocked", False)),
            "warnings": [
                str(item.get("code"))
                for item in (feature_payload.get("quality") or {}).get("warnings", [])
                if isinstance(item, dict) and item.get("code")
            ],
        }

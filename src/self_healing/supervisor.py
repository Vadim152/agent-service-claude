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

    def _is_cancellation_requested(self, job_id: str) -> bool:
        job = self.run_state_store.get_job(job_id)
        if not job:
            return True
        if bool(job.get("cancel_requested")):
            return True
        status = str(job.get("status", "")).strip().lower()
        return status in {"cancelling", "cancelled"}

    async def execute_job(self, job_id: str) -> None:
        job = self.run_state_store.get_job(job_id)
        if not job:
            return
        if self._is_cancellation_requested(job_id):
            self.run_state_store.patch_job(job_id, status="cancelled", finished_at=_utcnow())
            self.run_state_store.append_event(
                job_id,
                "job.cancelled",
                {"jobId": job_id, "status": "cancelled", "reason": "cancelled_before_start"},
            )
            return
        metrics.inc("jobs.started")

        profile = str(job.get("profile", "quick"))
        max_auto_reruns, max_total_duration_s = self._limits_for_profile(profile)

        run_id = str(uuid.uuid4())
        self.run_state_store.patch_job(job_id, run_id=run_id, status="running")
        self.run_state_store.append_event(
            job_id,
            "job.running",
            {"jobId": job_id, "runId": run_id, "profile": profile},
        )
        logger.info("Job running", extra={"jobId": job_id, "runId": run_id, "profile": profile})

        start = asyncio.get_running_loop().time()
        succeeded = False
        cancelled = False
        incident: dict[str, Any] | None = None
        latest_result: dict[str, Any] | None = None

        for attempt_index in range(max_auto_reruns + 1):
            if asyncio.get_running_loop().time() - start > max_total_duration_s:
                incident = self._build_incident(
                    job,
                    run_id,
                    "",
                    {"category": "unknown", "confidence": 0.0},
                    "Total duration limit exceeded",
                )
                break

            attempt_id = str(uuid.uuid4())
            self.run_state_store.append_attempt(
                job_id,
                {
                    "attempt_id": attempt_id,
                    "attempt_no": attempt_index + 1,
                    "status": "started",
                    "started_at": _utcnow(),
                    "artifacts": {},
                },
            )
            self.run_state_store.append_event(
                job_id,
                "attempt.started",
                {
                    "jobId": job_id,
                    "runId": run_id,
                    "attemptId": attempt_id,
                    "status": "started",
                },
            )

            if self._is_cancellation_requested(job_id):
                self.run_state_store.patch_attempt(
                    job_id,
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
                        job["project_root"],
                        job["test_case_text"],
                        job.get("target_path"),
                        create_file=bool(job.get("create_file")),
                        overwrite_existing=bool(job.get("overwrite_existing")),
                        language=job.get("language"),
                        quality_policy=job.get("quality_policy", "strict"),
                        zephyr_auth=job.get("zephyr_auth"),
                        jira_instance=job.get("jira_instance"),
                    )
                latest_result = result
                if self._is_cancellation_requested(job_id):
                    self.run_state_store.patch_attempt(
                        job_id,
                        attempt_id,
                        status="cancelled",
                        finished_at=_utcnow(),
                        artifacts=artifacts,
                    )
                    self.run_state_store.append_event(
                        job_id,
                        "attempt.cancelled",
                        {"jobId": job_id, "runId": run_id, "attemptId": attempt_id, "status": "cancelled"},
                    )
                    cancelled = True
                    break
                feature_payload = result.get("feature", {})
                unmatched = feature_payload.get("unmappedSteps", [])
                quality_payload = feature_payload.get("quality") or {}
                quality_passed = True
                if isinstance(quality_payload, dict):
                    quality_passed = bool(quality_payload.get("passed", False))
                has_failure = (len(unmatched) > 0) or (not quality_passed)

                feature_result_path = self.artifact_store.write_json(
                    job_id=job_id,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    name="feature-result.json",
                    payload=result,
                )
                artifacts["featureResult"] = feature_result_path
                classifier_input = {
                    "unmatched": str(unmatched),
                    "qualityFailures": str(quality_payload.get("failures", []))
                    if isinstance(quality_payload, dict)
                    else "[]",
                    "artifactUri": feature_result_path,
                }

                if not has_failure:
                    succeeded = True
                    metrics.inc("jobs.succeeded_without_rerun")
                    self.run_state_store.patch_attempt(
                        job_id,
                        attempt_id,
                        status="succeeded",
                        finished_at=_utcnow(),
                        artifacts=artifacts,
                    )
                    self.run_state_store.append_event(
                        job_id,
                        "attempt.succeeded",
                        {"jobId": job_id, "runId": run_id, "attemptId": attempt_id, "status": "succeeded"},
                    )
                    break

                classification = self.classifier.classify(classifier_input)
                classification_payload = classification.to_dict()
                classification_path = self.artifact_store.write_json(
                    job_id=job_id,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    name="failure-classification.json",
                    payload=classification_payload,
                )
                artifacts["failureClassification"] = classification_path
                self.run_state_store.patch_attempt(
                    job_id,
                    attempt_id,
                    status="failed",
                    classification=classification_payload,
                    artifacts=artifacts,
                )
                self.run_state_store.append_event(
                    job_id,
                    "attempt.classified",
                    {
                        "jobId": job_id,
                        "runId": run_id,
                        "attemptId": attempt_id,
                        "status": "failed",
                        "classification": classification_payload,
                    },
                )

                if classification.confidence < 0.55:
                    metrics.inc("jobs.low_confidence_failures")
                    self.run_state_store.patch_attempt(
                        job_id,
                        attempt_id,
                        status="failed",
                        finished_at=_utcnow(),
                        classification=classification_payload,
                        artifacts=artifacts,
                    )
                    incident = self._build_incident(job, run_id, attempt_id, classification_payload, "Low confidence")
                    break

                decision = self.playbooks.decide(classification.category)
                remediation_payload = decision.to_dict()
                apply_result = self.playbooks.apply(decision)
                self.run_state_store.patch_attempt(
                    job_id,
                    attempt_id,
                    status="remediated",
                    classification=classification_payload,
                    remediation=remediation_payload,
                    artifacts=artifacts,
                )
                self.run_state_store.append_event(
                    job_id,
                    "attempt.remediated",
                    {
                        "jobId": job_id,
                        "runId": run_id,
                        "attemptId": attempt_id,
                        "status": "remediated",
                        "remediation": remediation_payload,
                        "result": apply_result,
                    },
                )
                if not apply_result.get("applied"):
                    self.run_state_store.patch_attempt(
                        job_id,
                        attempt_id,
                        status="failed",
                        finished_at=_utcnow(),
                        classification=classification_payload,
                        remediation=remediation_payload,
                        artifacts=artifacts,
                    )
                    incident = self._build_incident(job, run_id, attempt_id, classification_payload, decision.notes)
                    break

                self.run_state_store.patch_attempt(
                    job_id,
                    attempt_id,
                    status="rerun_scheduled",
                    finished_at=_utcnow(),
                    classification=classification_payload,
                    remediation=remediation_payload,
                    artifacts=artifacts,
                )
                self.run_state_store.append_event(
                    job_id,
                    "attempt.rerun_scheduled",
                    {
                        "jobId": job_id,
                        "runId": run_id,
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
                    job_id,
                    attempt_id,
                    status="failed",
                    finished_at=_utcnow(),
                    classification=classification_payload,
                    artifacts=artifacts,
                )
                incident = self._build_incident(job, run_id, attempt_id, classification_payload, str(exc))
                break

        if cancelled:
            final_status = "cancelled"
        else:
            final_status = "succeeded" if succeeded else "needs_attention"
        metrics.inc(f"jobs.final_status.{final_status}")
        incident_uri = None
        if incident and final_status != "cancelled":
            incident_uri = self.artifact_store.write_incident(job_id, incident)
            self.run_state_store.append_event(
                job_id,
                "job.incident",
                {
                    "jobId": job_id,
                    "runId": run_id,
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
            job_id,
            status=final_status,
            finished_at=_utcnow(),
            incident_uri=incident_uri,
            result=feature_result,
        )
        if final_status == "cancelled":
            self.run_state_store.append_event(
                job_id,
                "job.cancelled",
                {"jobId": job_id, "runId": run_id, "status": "cancelled"},
            )
        self.run_state_store.append_event(
            job_id,
            "job.finished",
            {
                "jobId": job_id,
                "runId": run_id,
                "status": final_status,
                "incidentUri": incident_uri,
                "resultReady": bool(feature_result),
            },
        )

    @staticmethod
    def _build_incident(
        job: dict[str, Any], run_id: str, attempt_id: str, classification: dict[str, Any], reason: str
    ) -> dict[str, Any]:
        return {
            "jobId": job["job_id"],
            "runId": run_id,
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
            "pipeline": result.get("pipeline", []),
            "fileStatus": result.get("fileStatus"),
        }

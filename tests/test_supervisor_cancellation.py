from __future__ import annotations

import asyncio
from pathlib import Path

from infrastructure.artifact_store import ArtifactStore
from infrastructure.run_state_store import RunStateStore
from self_healing.supervisor import ExecutionSupervisor


class _CancellingOrchestrator:
    def __init__(self, store: RunStateStore, run_id: str) -> None:
        self._store = store
        self._run_id = run_id

    def generate_feature(self, *_args, **_kwargs):
        self._store.patch_job(self._run_id, status="cancelling", cancel_requested=True)
        return {
            "feature": {"featureText": "Feature: demo", "unmappedSteps": []},
            "matchResult": {"matched": [], "unmatched": []},
        }


class _CapturingOrchestrator:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate_feature(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return {
            "feature": {"featureText": "Feature: done", "unmappedSteps": []},
            "matchResult": {"matched": [], "unmatched": []},
            "pipeline": [],
        }


class _FailingOrchestrator:
    def generate_feature(self, *args, **kwargs):
        _ = (args, kwargs)
        raise RuntimeError("Corporate proxy request failed: 503; body=temporary unavailable")


class _QualityFailingOrchestrator:
    def generate_feature(self, *args, **kwargs):
        _ = (args, kwargs)
        return {
            "feature": {
                "featureText": "Feature: demo\n  Scenario: case\n    Given step",
                "unmappedSteps": [],
                "stepsSummary": {"exact": 1, "fuzzy": 0, "unmatched": 0},
                "quality": {
                    "policy": "strict",
                    "passed": False,
                    "score": 72,
                    "failures": [
                        {
                            "code": "quality_score_too_low",
                            "message": "Overall quality score is below policy threshold",
                            "actual": 72,
                            "expected": ">= 80",
                        }
                    ],
                    "criticIssues": [],
                    "metrics": {
                        "syntaxValid": True,
                        "unmatchedStepsCount": 0,
                        "unmatchedRatio": 0.0,
                        "exactRatio": 1.0,
                        "fuzzyRatio": 0.0,
                        "parameterFillFullRatio": 1.0,
                        "ambiguousCount": 0,
                        "llmRerankedCount": 0,
                        "normalizationSplitCount": 0,
                        "qualityScore": 72,
                    },
                },
            },
            "matchResult": {"matched": [], "unmatched": [], "ambiguousCount": 0},
            "pipeline": [],
        }


class _BlockedGenerationOrchestrator:
    def generate_feature(self, *args, **kwargs):
        _ = (args, kwargs)
        return {
            "feature": {
                "featureText": "",
                "unmappedSteps": [],
                "meta": {
                    "generationBlocked": True,
                    "blockingReason": "Actor and observable outcome must be clarified",
                },
            },
            "matchResult": {"matched": [], "unmatched": []},
            "pipeline": [{"stage": "clarification_gate", "status": "blocked", "details": {}}],
        }


def test_supervisor_respects_cancel_requested_after_attempt(tmp_path: Path) -> None:
    store = RunStateStore()
    run_id = "job-cancel-mid-flight"
    store.put_job(
        {
            "run_id": run_id,
            "status": "queued",
            "cancel_requested": False,
            "project_root": "/tmp/project",
            "test_case_text": "Given user is logged in",
            "target_path": None,
            "create_file": False,
            "overwrite_existing": False,
            "language": None,
            "profile": "quick",
            "source": "tests",
            "started_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "attempts": [],
            "result": None,
        }
    )

    supervisor = ExecutionSupervisor(
        orchestrator=_CancellingOrchestrator(store, run_id),
        run_state_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
    )

    asyncio.run(supervisor.execute_run(run_id))

    item = store.get_job(run_id)
    assert item is not None
    assert item["status"] == "cancelled"
    assert item["result"] is None

    events, _ = store.list_events(run_id)
    event_types = [event["event_type"] for event in events]
    assert "attempt.cancelled" in event_types
    assert "run.cancelled" in event_types


def test_supervisor_passes_jira_context_to_orchestrator(tmp_path: Path) -> None:
    store = RunStateStore()
    run_id = "job-jira-context"
    store.put_job(
        {
            "run_id": run_id,
            "status": "queued",
            "cancel_requested": False,
            "project_root": "/tmp/project",
            "test_case_text": "создай автотест по SCBC-T1",
            "target_path": None,
            "create_file": False,
            "overwrite_existing": False,
            "language": "ru",
            "zephyr_auth": {"authType": "TOKEN", "token": "token-value"},
            "jira_instance": "https://jira.sberbank.ru",
            "profile": "quick",
            "source": "tests",
            "started_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "attempts": [],
            "result": None,
        }
    )
    orchestrator = _CapturingOrchestrator()
    supervisor = ExecutionSupervisor(
        orchestrator=orchestrator,
        run_state_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
    )

    asyncio.run(supervisor.execute_run(run_id))

    assert len(orchestrator.calls) == 1
    kwargs = orchestrator.calls[0]["kwargs"]
    assert kwargs["zephyr_auth"] == {"authType": "TOKEN", "token": "token-value"}
    assert kwargs["jira_instance"] == "https://jira.sberbank.ru"
    item = store.get_job(run_id)
    assert item is not None
    attempts = item.get("attempts", [])
    assert attempts
    assert str(attempts[0]["artifacts"]["featureResult"]).startswith("artifact://")


def test_supervisor_classifies_503_exception_as_infra(tmp_path: Path) -> None:
    store = RunStateStore()
    run_id = "job-503-classification"
    store.put_job(
        {
            "run_id": run_id,
            "status": "queued",
            "cancel_requested": False,
            "project_root": "/tmp/project",
            "test_case_text": "generate autotest",
            "target_path": None,
            "create_file": False,
            "overwrite_existing": False,
            "language": None,
            "profile": "quick",
            "source": "tests",
            "started_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "attempts": [],
            "result": None,
        }
    )

    supervisor = ExecutionSupervisor(
        orchestrator=_FailingOrchestrator(),
        run_state_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
    )
    asyncio.run(supervisor.execute_run(run_id))

    item = store.get_job(run_id)
    assert item is not None
    assert item["status"] == "needs_attention"
    attempts = item.get("attempts", [])
    assert attempts
    classification = attempts[0].get("classification", {})
    assert classification.get("category") == "infra"


def test_supervisor_marks_run_needs_attention_when_quality_gate_fails(tmp_path: Path) -> None:
    store = RunStateStore()
    run_id = "job-quality-gate-fail"
    store.put_job(
        {
            "run_id": run_id,
            "status": "queued",
            "cancel_requested": False,
            "project_root": "/tmp/project",
            "test_case_text": "generate autotest",
            "target_path": None,
            "create_file": False,
            "overwrite_existing": False,
            "language": None,
            "quality_policy": "strict",
            "profile": "quick",
            "source": "tests",
            "started_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "attempts": [],
            "result": None,
        }
    )

    supervisor = ExecutionSupervisor(
        orchestrator=_QualityFailingOrchestrator(),
        run_state_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
    )
    asyncio.run(supervisor.execute_run(run_id))

    item = store.get_job(run_id)
    assert item is not None
    assert item["status"] == "needs_attention"
    assert str(item["incident_uri"]).startswith("artifact://")
    result = item.get("result") or {}
    quality = result.get("quality") or {}
    assert quality.get("passed") is False
    assert quality.get("score") == 72
    attempts = item.get("attempts", [])
    assert attempts
    assert str(attempts[0]["artifacts"]["featureResult"]).startswith("artifact://")
    assert str(attempts[0]["artifacts"]["failureClassification"]).startswith("artifact://")


def test_supervisor_marks_run_needs_attention_when_generation_is_blocked(tmp_path: Path) -> None:
    store = RunStateStore()
    run_id = "job-generation-blocked"
    store.put_job(
        {
            "run_id": run_id,
            "status": "queued",
            "cancel_requested": False,
            "project_root": "/tmp/project",
            "test_case_text": "Open dashboard",
            "target_path": None,
            "create_file": False,
            "overwrite_existing": False,
            "language": None,
            "quality_policy": "strict",
            "profile": "quick",
            "source": "tests",
            "started_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "attempts": [],
            "result": None,
        }
    )

    supervisor = ExecutionSupervisor(
        orchestrator=_BlockedGenerationOrchestrator(),
        run_state_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
    )
    asyncio.run(supervisor.execute_run(run_id))

    item = store.get_job(run_id)
    assert item is not None
    assert item["status"] == "needs_attention"
    result = item.get("result") or {}
    assert result.get("generationBlocked") is True
    attempts = item.get("attempts", [])
    assert attempts
    classification = attempts[0].get("classification", {})
    assert classification.get("category") == "requirements"

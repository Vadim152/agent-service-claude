from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes_runs import router as runs_router
from infrastructure.run_state_store import RunStateStore


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class _NoopSupervisor:
    async def execute_run(self, run_id: str) -> None:  # pragma: no cover - execution is not relevant for API schema checks
        _ = run_id


def _build_app() -> tuple[FastAPI, RunStateStore]:
    app = FastAPI()
    store = RunStateStore()
    app.state.run_state_store = store
    app.state.execution_supervisor = _NoopSupervisor()
    app.include_router(runs_router)
    return app, store


def test_create_run_initializes_result_and_attempts() -> None:
    app, store = _build_app()
    client = TestClient(app)

    response = client.post(
        "/runs",
        json={
            "projectRoot": "/tmp/project",
            "plugin": "testgen",
            "input": {
                "testCaseText": "Given something",
                "jiraInstance": "https://jira.sberbank.ru",
                "zephyrAuth": {"authType": "TOKEN", "token": "secret"},
            },
            "source": "test-suite",
            "profile": "quick",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    item = store.get_job(payload["runId"])
    assert item is not None
    assert item["jira_instance"] == "https://jira.sberbank.ru"
    assert item["zephyr_auth"] == {"authType": "TOKEN", "token": "secret"}
    assert item["quality_policy"] == "strict"
    assert item["result"] is None
    assert item["attempts"] == []


def test_create_run_persists_intent_aware_generation_fields() -> None:
    app, store = _build_app()
    client = TestClient(app)

    response = client.post(
        "/runs",
        json={
            "projectRoot": "/tmp/project",
            "plugin": "testgen",
            "input": {
                "testCaseText": "Given something",
                "planId": "plan-42",
                "selectedScenarioId": "sc-1",
                "selectedScenarioCandidateId": "candidate-1-happy_path",
                "acceptedAssumptionIds": ["assumption-data"],
                "clarifications": {"actor": "user"},
            },
            "source": "test-suite",
            "profile": "quick",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    item = store.get_job(payload["runId"])
    assert item is not None
    assert item["plan_id"] == "plan-42"
    assert item["selected_scenario_id"] == "sc-1"
    assert item["selected_scenario_candidate_id"] == "candidate-1-happy_path"
    assert item["accepted_assumption_ids"] == ["assumption-data"]
    assert item["clarifications"] == {"actor": "user"}


def test_get_run_attempts_returns_attempt_payload() -> None:
    app, store = _build_app()
    client = TestClient(app)

    store.put_job(
        {
            "run_id": "j1",
            "execution_id": "r1",
            "status": "running",
            "source": "test-suite",
            "started_at": _utcnow(),
            "updated_at": _utcnow(),
            "attempts": [
                {
                    "attempt_id": "a1",
                    "status": "failed",
                    "started_at": _utcnow(),
                    "finished_at": _utcnow(),
                    "classification": {"category": "infra", "confidence": 0.8, "signals": [], "summary": "infra"},
                    "artifacts": {"featureResult": "/tmp/result.json"},
                }
            ],
            "result": None,
        }
    )

    response = client.get("/runs/j1/attempts")
    assert response.status_code == 200
    payload = response.json()
    assert payload["runId"] == "j1"
    assert len(payload["attempts"]) == 1
    assert payload["attempts"][0]["attemptId"] == "a1"
    assert payload["attempts"][0]["status"] == "failed"


def test_get_run_result_returns_ready_payload() -> None:
    app, store = _build_app()
    client = TestClient(app)

    store.put_job(
        {
            "run_id": "j2",
            "execution_id": "r2",
            "status": "succeeded",
            "source": "test-suite",
            "incident_uri": None,
            "started_at": _utcnow(),
            "finished_at": _utcnow(),
            "updated_at": _utcnow(),
            "attempts": [],
            "result": {
                "featureText": "Feature: sample",
                "unmappedSteps": [],
                "unmapped": [],
                "usedSteps": [],
                "buildStage": "ok",
                "stepsSummary": {"exact": 1, "fuzzy": 0, "unmatched": 0},
                "quality": {
                    "policy": "strict",
                    "passed": True,
                    "score": 92,
                    "failures": [],
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
                        "qualityScore": 92,
                    },
                },
                "meta": {"language": "en"},
                "pipeline": [],
                "fileStatus": None,
            },
        }
    )

    response = client.get("/runs/j2/result")
    assert response.status_code == 200
    payload = response.json()
    assert payload["runId"] == "j2"
    assert payload["status"] == "succeeded"
    assert payload["output"]["featureText"] == "Feature: sample"
    assert payload["output"]["stepsSummary"]["exact"] == 1
    assert payload["output"]["quality"]["passed"] is True
    assert payload["output"]["quality"]["score"] == 92


def test_get_run_result_returns_409_when_not_ready() -> None:
    app, store = _build_app()
    client = TestClient(app)

    store.put_job(
        {
            "run_id": "j3",
            "status": "running",
            "started_at": _utcnow(),
            "updated_at": _utcnow(),
            "attempts": [],
            "result": None,
        }
    )
    response = client.get("/runs/j3/result")
    assert response.status_code == 409


def test_cancel_run_marks_run_as_cancelling() -> None:
    app, store = _build_app()
    client = TestClient(app)
    store.put_job(
        {
            "run_id": "j4",
            "status": "running",
            "started_at": _utcnow(),
            "updated_at": _utcnow(),
            "attempts": [],
            "result": None,
        }
    )

    response = client.post("/runs/j4/cancel")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "cancelling"
    assert payload["cancelRequested"] is True
    item = store.get_job("j4")
    assert item is not None
    assert item["cancel_requested"] is True
    assert item["status"] == "cancelling"


def test_run_events_store_supports_from_index() -> None:
    _, store = _build_app()
    store.put_job(
        {
            "run_id": "j5",
            "status": "running",
            "started_at": _utcnow(),
            "updated_at": _utcnow(),
            "attempts": [],
            "result": None,
        }
    )
    store.append_event("j5", "event.zero", {"v": 0})
    store.append_event("j5", "event.one", {"v": 1})
    events, next_index = store.list_events("j5", since_index=1)
    assert next_index == 2
    assert len(events) == 1
    assert events[0]["index"] == 1
    assert events[0]["event_type"] == "event.one"


def test_create_run_with_same_idempotency_key_and_payload_returns_existing_run() -> None:
    app, _ = _build_app()
    client = TestClient(app)
    headers = {"Idempotency-Key": "key-123"}
    payload = {
        "projectRoot": "/tmp/project",
        "plugin": "testgen",
        "input": {"testCaseText": "Given something"},
        "source": "test-suite",
        "profile": "quick",
    }

    first = client.post("/runs", json=payload, headers=headers)
    second = client.post("/runs", json=payload, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["runId"] == first.json()["runId"]


def test_create_run_with_same_idempotency_key_and_different_payload_returns_409() -> None:
    app, _ = _build_app()
    client = TestClient(app)
    headers = {"Idempotency-Key": "key-123"}

    first = client.post(
        "/runs",
        json={
            "projectRoot": "/tmp/project",
            "plugin": "testgen",
            "input": {"testCaseText": "Given something"},
            "source": "test-suite",
            "profile": "quick",
        },
        headers=headers,
    )
    second = client.post(
        "/runs",
        json={
            "projectRoot": "/tmp/project",
            "plugin": "testgen",
            "input": {"testCaseText": "Given another thing"},
            "source": "test-suite",
            "profile": "quick",
        },
        headers=headers,
    )

    assert first.status_code == 200
    assert second.status_code == 409


def test_create_run_persists_selected_quality_policy() -> None:
    app, store = _build_app()
    client = TestClient(app)

    response = client.post(
        "/runs",
        json={
            "projectRoot": "/tmp/project",
            "plugin": "testgen",
            "input": {"testCaseText": "Given something", "qualityPolicy": "balanced"},
            "source": "test-suite",
            "profile": "quick",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    item = store.get_job(payload["runId"])
    assert item is not None
    assert item["quality_policy"] == "balanced"

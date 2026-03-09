from __future__ import annotations

import json
import sys
import textwrap
import time
from pathlib import Path

from fastapi.testclient import TestClient

from opencode_adapter_app.config import AdapterSettings
from opencode_adapter_app.main import create_app


def _write_fake_runner(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            """
            import argparse
            import json
            import os
            import pathlib
            import sys
            import time

            parser = argparse.ArgumentParser()
            parser.add_argument("--project-root", required=True)
            parser.add_argument("--prompt", required=True)
            parser.add_argument("--agent", required=True)
            parser.add_argument("--session")
            parser.add_argument("--model")
            args = parser.parse_args()

            prompt = args.prompt
            if "fail" in prompt.lower():
                print(json.dumps({"type": "progress", "status": "running", "message": "started"}), flush=True)
                print("runner failed", file=sys.stderr, flush=True)
                sys.exit(2)

            if "hang" in prompt.lower():
                print("The operation timed out.", file=sys.stderr, flush=True)
                time.sleep(10)
                sys.exit(3)

            session_id = args.session or f"session-{abs(hash(prompt)) % 100000}"
            print(json.dumps({"type": "started", "status": "running", "sessionId": session_id, "currentAction": "Boot"}), flush=True)
            time.sleep(0.05)

            if "approval" in prompt.lower():
                print(
                    json.dumps(
                        {
                            "type": "approval_required",
                            "status": "running",
                            "currentAction": "Awaiting approval",
                            "pendingApprovals": [
                                {
                                    "approvalId": "approve-1",
                                    "toolName": "repo.write",
                                    "title": "Approve repo write",
                                    "riskLevel": "high",
                                }
                            ],
                        }
                    ),
                    flush=True,
                )
                raw = sys.stdin.readline().strip()
                decision = json.loads(raw) if raw else {"decision": "deny"}
                if decision.get("decision") != "approve":
                    print(json.dumps({"type": "cancelled", "status": "cancelled", "message": "Denied"}), flush=True)
                    sys.exit(0)

            artifact_dir = pathlib.Path(args.project_root) / ".fake-opencode"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = artifact_dir / "notes.log"
            artifact_path.write_text(f"log for {prompt}", encoding="utf-8")
            print("stderr trace line", file=sys.stderr, flush=True)
            print(
                json.dumps(
                    {
                        "type": "artifact",
                        "status": "running",
                        "artifacts": [
                            {"name": "notes.log", "path": str(artifact_path), "mediaType": "text/plain"},
                            {"name": "summary.txt", "content": f"summary for {prompt}", "mediaType": "text/plain"},
                        ],
                    }
                ),
                flush=True,
            )
            time.sleep(0.05)
            print(
                json.dumps(
                    {
                        "type": "finished",
                        "status": "succeeded",
                        "sessionId": session_id,
                        "currentAction": "Completed",
                        "totals": {
                            "tokens": {"input": 70, "output": 40, "reasoning": 10, "cacheRead": 0, "cacheWrite": 0},
                            "cost": 0.0,
                        },
                        "limits": {"contextWindow": 200000, "used": 120, "percent": 0.0006},
                        "output": {
                            "summary": f"done: {prompt}",
                            "sessionId": session_id,
                            "model": args.model,
                        },
                    }
                ),
                flush=True,
            )
            """
        ),
        encoding="utf-8",
    )


def _build_settings(tmp_path: Path, *, max_events_per_run: int = 5_000) -> AdapterSettings:
    runner = tmp_path / "fake_runner.py"
    _write_fake_runner(runner)
    return AdapterSettings(
        binary=sys.executable,
        binary_args_json=json.dumps([str(runner)]),
        runner_type="raw_json_runner",
        print_logs=False,
        work_root=tmp_path / "adapter-work",
        max_events_per_run=max_events_per_run,
    )


def _build_client(tmp_path: Path, *, max_events_per_run: int = 5_000) -> TestClient:
    settings = _build_settings(tmp_path, max_events_per_run=max_events_per_run)
    return TestClient(create_app(settings))


def _wait_until(predicate, timeout_s: float = 5.0) -> None:
    started = time.time()
    while time.time() - started < timeout_s:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("Condition was not met before timeout")


def test_create_run_reports_success_and_artifacts(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()

    created = client.post(
        "/v1/runs",
        json={
            "runId": "run-1",
            "sessionId": "session-a",
            "projectRoot": str(project_root),
            "prompt": "hello adapter",
            "profile": "agent",
        },
    )
    assert created.status_code == 200
    backend_run_id = created.json()["backendRunId"]

    def _done() -> bool:
        return client.get(f"/v1/runs/{backend_run_id}").json()["status"] == "succeeded"

    _wait_until(_done)
    payload = client.get(f"/v1/runs/{backend_run_id}").json()
    assert payload["backendSessionId"].startswith("session-")
    assert payload["output"]["summary"] == "done: hello adapter"
    assert payload["output"]["model"] is None
    assert payload["totals"]["tokens"]["input"] == 70
    assert payload["limits"]["percent"] == 0.0006
    names = {item["name"] for item in payload["artifacts"]}
    assert {"notes.log", "summary.txt", "stderr.log"}.issubset(names)

    events = client.get(f"/v1/runs/{backend_run_id}/events", params={"after": 0}).json()
    event_types = [item["eventType"] for item in events["items"]]
    assert "run.started" in event_types
    assert "run.artifact_published" in event_types
    assert "run.finished" in event_types


def test_cancel_run_transitions_to_cancelled(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    created = client.post(
        "/v1/runs",
        json={
            "runId": "run-cancel",
            "projectRoot": str(project_root),
            "prompt": "approval then cancel",
        },
    ).json()
    backend_run_id = created["backendRunId"]

    _wait_until(lambda: client.get(f"/v1/runs/{backend_run_id}").json()["pendingApprovals"])
    cancelled = client.post(f"/v1/runs/{backend_run_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_approval_decision_resumes_run(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    backend_run_id = client.post(
        "/v1/runs",
        json={
            "runId": "run-approval",
            "projectRoot": str(project_root),
            "prompt": "need approval",
            "sessionId": "session-approval",
        },
    ).json()["backendRunId"]

    def _approval_ready() -> bool:
        return bool(client.get(f"/v1/runs/{backend_run_id}").json()["pendingApprovals"])

    _wait_until(_approval_ready)
    approval_id = client.get(f"/v1/runs/{backend_run_id}").json()["pendingApprovals"][0]["approvalId"]
    decision = client.post(
        f"/v1/runs/{backend_run_id}/approvals/{approval_id}",
        json={"decision": "approve"},
    )
    assert decision.status_code == 200

    _wait_until(lambda: client.get(f"/v1/runs/{backend_run_id}").json()["status"] == "succeeded")
    payload = client.get(f"/v1/runs/{backend_run_id}").json()
    assert payload["pendingApprovals"] == []


def test_second_run_reuses_backend_session_id(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    first = client.post(
        "/v1/runs",
        json={
            "runId": "run-first",
            "sessionId": "session-shared",
            "projectRoot": str(project_root),
            "prompt": "first",
        },
    ).json()
    _wait_until(lambda: client.get(f"/v1/runs/{first['backendRunId']}").json()["status"] == "succeeded")
    first_status = client.get(f"/v1/runs/{first['backendRunId']}").json()

    second = client.post(
        "/v1/runs",
        json={
            "runId": "run-second",
            "sessionId": "session-shared",
            "projectRoot": str(project_root),
            "prompt": "second",
        },
    ).json()
    second_status = client.get(f"/v1/runs/{second['backendRunId']}").json()
    assert second_status["backendSessionId"] == first_status["backendSessionId"]


def test_ide_plugin_rejects_project_root_mismatch_for_same_external_session(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    project_root_a = tmp_path / "project-a"
    project_root_b = tmp_path / "project-b"
    project_root_a.mkdir()
    project_root_b.mkdir()

    first = client.post(
        "/v1/runs",
        json={
            "runId": "run-first-mismatch",
            "sessionId": "session-shared",
            "projectRoot": str(project_root_a),
            "prompt": "first",
            "source": "ide-plugin",
        },
    )
    assert first.status_code == 200
    _wait_until(lambda: client.get(f"/v1/runs/{first.json()['backendRunId']}").json()["status"] == "succeeded")

    second = client.post(
        "/v1/runs",
        json={
            "runId": "run-second-mismatch",
            "sessionId": "session-shared",
            "projectRoot": str(project_root_b),
            "prompt": "second",
            "source": "ide-plugin",
        },
    )

    assert second.status_code == 422
    assert "projectRoot mismatch for existing sessionId" in second.json()["error"]["message"]


def test_startup_timeout_marks_run_failed(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    backend_run_id = client.post(
        "/v1/runs",
        json={
            "runId": "run-timeout",
            "projectRoot": str(project_root),
            "prompt": "hang forever",
        },
    ).json()["backendRunId"]

    def _done() -> bool:
        return client.get(f"/v1/runs/{backend_run_id}").json()["status"] == "failed"

    _wait_until(_done, timeout_s=12.0)
    payload = client.get(f"/v1/runs/{backend_run_id}").json()
    assert payload["currentAction"] == "Runner startup timeout"


def test_override_mode_passes_model_to_runner(tmp_path: Path) -> None:
    runner = tmp_path / "fake_runner.py"
    _write_fake_runner(runner)
    settings = AdapterSettings(
        binary=sys.executable,
        binary_args_json=json.dumps([str(runner)]),
        runner_type="raw_json_runner",
        model_mode="override",
        model_override="gigachat/GigaChat-Max",
        print_logs=False,
        work_root=tmp_path / "adapter-work",
    )
    client = TestClient(create_app(settings))
    project_root = tmp_path / "project"
    project_root.mkdir()

    backend_run_id = client.post(
        "/v1/runs",
        json={
            "runId": "run-override",
            "projectRoot": str(project_root),
            "prompt": "hello adapter",
        },
    ).json()["backendRunId"]

    _wait_until(lambda: client.get(f"/v1/runs/{backend_run_id}").json()["status"] == "succeeded")
    payload = client.get(f"/v1/runs/{backend_run_id}").json()
    assert payload["output"]["model"] == "gigachat/GigaChat-Max"


def test_legacy_default_model_is_used_as_override(tmp_path: Path) -> None:
    runner = tmp_path / "fake_runner.py"
    _write_fake_runner(runner)
    settings = AdapterSettings(
        binary=sys.executable,
        binary_args_json=json.dumps([str(runner)]),
        runner_type="raw_json_runner",
        default_model="legacy/provider-model",
        print_logs=False,
        work_root=tmp_path / "adapter-work",
    )
    client = TestClient(create_app(settings))
    project_root = tmp_path / "project"
    project_root.mkdir()

    backend_run_id = client.post(
        "/v1/runs",
        json={
            "runId": "run-legacy",
            "projectRoot": str(project_root),
            "prompt": "hello adapter",
        },
    ).json()["backendRunId"]

    _wait_until(lambda: client.get(f"/v1/runs/{backend_run_id}").json()["status"] == "succeeded")
    payload = client.get(f"/v1/runs/{backend_run_id}").json()
    assert payload["output"]["model"] == "legacy/provider-model"


def test_override_mode_requires_model_override() -> None:
    try:
        AdapterSettings(model_mode="override")
    except ValueError as exc:
        assert "model_override must be set" in str(exc)
    else:
        raise AssertionError("Expected AdapterSettings to reject override mode without model_override")


def test_session_endpoints_reuse_mapping_and_compact_noop(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()

    first = client.post(
        "/v1/sessions",
        json={"externalSessionId": "session-a", "projectRoot": str(project_root), "source": "ide-plugin", "profile": "agent"},
    )
    assert first.status_code == 200
    second = client.post(
        "/v1/sessions",
        json={"externalSessionId": "session-a", "projectRoot": str(project_root), "source": "ide-plugin", "profile": "agent"},
    )
    assert second.status_code == 200
    assert second.json()["backendSessionId"] == first.json()["backendSessionId"]

    diff = client.get("/v1/sessions/session-a/diff")
    assert diff.status_code == 200
    assert diff.json()["summary"]["files"] == 0

    compact = client.post("/v1/sessions/session-a/compact")
    assert compact.status_code == 200
    assert compact.json()["result"]["status"] == "completed"

    compact_again = client.post("/v1/sessions/session-a/compact")
    assert compact_again.status_code == 200
    assert compact_again.json()["result"]["status"] == "noop"


def test_session_compact_conflicts_while_run_is_active(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()

    client.post(
        "/v1/sessions",
        json={"externalSessionId": "session-a", "projectRoot": str(project_root), "source": "ide-plugin", "profile": "agent"},
    )
    created = client.post(
        "/v1/runs",
        json={"runId": "run-active", "sessionId": "session-a", "projectRoot": str(project_root), "prompt": "need approval"},
    )
    backend_run_id = created.json()["backendRunId"]
    _wait_until(lambda: bool(client.get(f"/v1/runs/{backend_run_id}").json()["pendingApprovals"]))

    response = client.post("/v1/sessions/session-a/compact")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_busy"


def test_session_diff_returns_unavailable_when_snapshot_missing(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    client.post(
        "/v1/sessions",
        json={"externalSessionId": "session-a", "projectRoot": str(project_root), "source": "ide-plugin", "profile": "agent"},
    )
    store = client.app.state.opencode_adapter_state_store
    store._conn.execute("DELETE FROM session_diffs WHERE external_session_id = ?", ("session-a",))
    store._conn.commit()

    response = client.get("/v1/sessions/session-a/diff")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "diff_unavailable"


def test_run_events_expose_has_more_and_stale_cursor(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path, max_events_per_run=2)
    app = create_app(settings)
    store = app.state.opencode_adapter_state_store
    now = "2026-03-09T00:00:00+00:00"
    store.create_run(
        {
            "backend_run_id": "manual-run",
            "external_run_id": "manual-run",
            "external_session_id": None,
            "backend_session_id": None,
            "project_root": str(tmp_path / "project"),
            "prompt": "manual",
            "source": "test",
            "profile": "agent",
            "config_profile": "default",
            "policy_mode": None,
            "status": "running",
            "current_action": "Running",
            "result": None,
            "output": None,
            "artifacts": [],
            "pending_approvals": [],
            "cancel_requested": False,
            "exit_code": None,
            "created_at": now,
            "started_at": now,
            "finished_at": None,
            "updated_at": now,
            "work_dir": str(tmp_path / "manual"),
        }
    )
    store.append_event("manual-run", "run.progress", {"step": 1})
    store.append_event("manual-run", "run.progress", {"step": 2})
    store.append_event("manual-run", "run.progress", {"step": 3})
    client = TestClient(app)

    page = client.get("/v1/runs/manual-run/events", params={"after": 1, "limit": 1})
    assert page.status_code == 200
    assert page.json()["hasMore"] is True
    assert page.json()["nextCursor"] == 2

    stale = client.get("/v1/runs/manual-run/events", params={"after": 0, "limit": 10})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_cursor"


def test_get_run_exposes_multiple_pending_approvals(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    app = create_app(settings)
    store = app.state.opencode_adapter_state_store
    now = "2026-03-09T00:00:00+00:00"
    store.create_run(
        {
            "backend_run_id": "approval-run",
            "external_run_id": "approval-run",
            "external_session_id": "session-a",
            "backend_session_id": "session-1",
            "project_root": str(tmp_path / "project"),
            "prompt": "manual",
            "source": "test",
            "profile": "agent",
            "config_profile": "default",
            "policy_mode": None,
            "status": "running",
            "current_action": "Awaiting approval",
            "result": None,
            "output": None,
            "artifacts": [],
            "pending_approvals": [],
            "cancel_requested": False,
            "exit_code": None,
            "created_at": now,
            "started_at": now,
            "finished_at": None,
            "updated_at": now,
            "work_dir": str(tmp_path / "manual"),
        }
    )
    store.record_pending_approvals(
        "approval-run",
        [
            {"approvalId": "approval-1", "toolName": "repo.write", "title": "Write 1", "riskLevel": "high"},
            {"approvalId": "approval-2", "toolName": "repo.write", "title": "Write 2", "riskLevel": "high"},
        ],
    )
    client = TestClient(app)

    response = client.get("/v1/runs/approval-run")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["pendingApprovals"]) == 2
    assert [item["status"] for item in payload["approvals"]] == ["pending", "pending"]


def test_adapter_restart_marks_inflight_runs_failed(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    app = create_app(settings)
    store = app.state.opencode_adapter_state_store
    now = "2026-03-09T00:00:00+00:00"
    store.create_run(
        {
            "backend_run_id": "restart-run",
            "external_run_id": "restart-run",
            "external_session_id": "session-a",
            "backend_session_id": "session-1",
            "project_root": str(tmp_path / "project"),
            "prompt": "manual",
            "source": "test",
            "profile": "agent",
            "config_profile": "default",
            "policy_mode": None,
            "status": "running",
            "current_action": "Running",
            "result": None,
            "output": None,
            "artifacts": [],
            "pending_approvals": [],
            "cancel_requested": False,
            "exit_code": None,
            "created_at": now,
            "started_at": now,
            "finished_at": None,
            "updated_at": now,
            "work_dir": str(tmp_path / "manual"),
        }
    )
    store.close()

    with TestClient(create_app(settings)) as client:
        payload = client.get("/v1/runs/restart-run").json()

        assert payload["status"] == "failed"
        assert payload["output"]["error"]["code"] == "adapter_restarted"


def test_debug_runtime_reports_adapter_snapshot(tmp_path: Path) -> None:
    client = _build_client(tmp_path)

    response = client.get("/debug/runtime")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "opencode-adapter"
    assert payload["runnerType"] == "raw_json_runner"
    assert payload["modelResolution"] == "config"
    assert payload["forcedModel"] is None
    assert payload["serverRunning"] is False
    assert payload["serverReady"] is False
    assert payload["activeConfigFile"] is None
    assert payload["resolvedProviders"] == []
    assert payload["resolvedModel"] is None
    assert payload["rawConfig"] is None

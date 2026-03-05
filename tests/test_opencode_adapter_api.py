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


def _build_client(tmp_path: Path) -> TestClient:
    runner = tmp_path / "fake_runner.py"
    _write_fake_runner(runner)
    settings = AdapterSettings(
        binary=sys.executable,
        binary_args_json=json.dumps([str(runner)]),
        runner_type="raw_json_runner",
        print_logs=False,
        work_root=tmp_path / "adapter-work",
    )
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
    assert "projectRoot mismatch for existing sessionId" in second.json()["detail"]


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

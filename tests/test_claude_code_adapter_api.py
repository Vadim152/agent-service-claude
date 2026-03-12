from __future__ import annotations

import json
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fastapi.testclient import TestClient

from claude_code_adapter_app.config import AdapterSettings
from claude_code_adapter_app.main import create_app


def _write_fake_runner(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            """
            import argparse
            import json
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
            session_id = args.session or f"session-{abs(hash(prompt)) % 100000}"
            print(json.dumps({"type": "started", "status": "running", "sessionId": session_id, "currentAction": "Boot"}), flush=True)
            time.sleep(0.05)

            artifact_dir = pathlib.Path(args.project_root) / ".fake-claude-code"
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


def test_claude_code_adapter_reports_success_and_artifacts(tmp_path: Path) -> None:
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
    assert payload["totals"]["tokens"]["input"] == 70
    assert payload["limits"]["percent"] == 0.0006
    names = {item["name"] for item in payload["artifacts"]}
    assert {"notes.log", "summary.txt", "stderr.log"}.issubset(names)

    events = client.get(f"/v1/runs/{backend_run_id}/events", params={"after": 0}).json()
    event_types = [item["eventType"] for item in events["items"]]
    assert "run.started" in event_types
    assert "run.artifact_published" in event_types
    assert "run.succeeded" in event_types


def test_claude_code_adapter_compact_is_safe_noop_for_raw_runner(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()

    session = client.post(
        "/v1/sessions",
        json={"externalSessionId": "session-a", "projectRoot": str(project_root), "profile": "agent"},
    )
    assert session.status_code == 200

    compact = client.post("/v1/sessions/session-a/compact")
    assert compact.status_code == 200
    assert compact.json()["result"]["status"] == "noop"


def test_claude_code_adapter_health_and_debug_use_new_service_name(tmp_path: Path) -> None:
    client = _build_client(tmp_path)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["service"] == "claude-code-adapter"

    debug = client.get("/debug/runtime")
    assert debug.status_code == 200
    assert debug.json()["service"] == "claude-code-adapter"

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from claude_code_adapter_app.config import AdapterSettings
from claude_code_adapter_app.main import create_app


def _write_fake_claude_cli(path: Path, *, accepted_model: str = "gigachat/GigaChat-2") -> None:
    path.write_text(
        textwrap.dedent(
            f"""
            import json
            import os
            import sys
            from pathlib import Path

            ACCEPTED_MODEL = {json.dumps(accepted_model)}


            def _value(args, flag, default=None):
                if flag not in args:
                    return default
                index = args.index(flag)
                if index + 1 >= len(args):
                    return default
                return args[index + 1]


            def _require_gateway():
                if not os.environ.get("ANTHROPIC_BASE_URL") or not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
                    print(json.dumps({{"type": "result", "is_error": True, "result": "Gateway env is missing"}}))
                    raise SystemExit(1)


            def _state_file(session_id: str) -> Path:
                root = Path(os.environ.get("XDG_STATE_HOME") or ".") / "claude-code-sessions"
                root.mkdir(parents=True, exist_ok=True)
                return root / f"{{session_id}}.json"


            def _load_history(session_id: str) -> list[str]:
                path = _state_file(session_id)
                if not path.exists():
                    return []
                return json.loads(path.read_text(encoding="utf-8"))


            def _save_history(session_id: str, history: list[str]) -> None:
                _state_file(session_id).write_text(json.dumps(history), encoding="utf-8")


            def _print_help():
                print(
                    "Usage: claude [options] [command] [prompt]\\n"
                    "  --output-format <format>\\n"
                    "  --resume [value]\\n"
                    "  --session-id <uuid>\\n"
                    "  --permission-mode <mode>\\n"
                )


            def main() -> int:
                args = sys.argv[1:]
                if args == ["-v"]:
                    print("2.1.74 (Claude Code)")
                    return 0
                if "-p" in args and "--help" in args:
                    _print_help()
                    return 0
                if "-p" not in args:
                    _print_help()
                    return 0

                _require_gateway()

                model = _value(args, "--model", ACCEPTED_MODEL)
                if model != ACCEPTED_MODEL:
                    print(json.dumps({{"type": "result", "is_error": True, "result": f"Unknown model: {{model}}"}}))
                    return 1

                output_format = _value(args, "--output-format", "json")
                no_persist = "--no-session-persistence" in args
                session_id = _value(args, "--resume") or _value(args, "--session-id") or "11111111-1111-4111-8111-111111111111"
                prompt = args[-1] if args else ""
                history = [] if no_persist else _load_history(session_id)
                history.append(prompt)
                if not no_persist:
                    _save_history(session_id, history)

                if output_format == "json":
                    print(
                        json.dumps(
                            {{
                                "type": "result",
                                "subtype": "success",
                                "is_error": False,
                                "result": "OK",
                                "model": model,
                            }}
                        )
                    )
                    return 0

                result_text = f"done: {{' | '.join(history)}}"
                print(
                    json.dumps(
                        {{
                            "type": "system",
                            "subtype": "init",
                            "session_id": session_id,
                            "model": model,
                            "permissionMode": _value(args, "--permission-mode", "default"),
                        }}
                    ),
                    flush=True,
                )
                if "tool" in prompt.lower():
                    print(
                        json.dumps(
                            {{
                                "type": "assistant",
                                "session_id": session_id,
                                "message": {{
                                    "id": "msg_tool",
                                    "type": "message",
                                    "role": "assistant",
                                    "model": model,
                                    "content": [
                                        {{
                                            "type": "tool_use",
                                            "id": "toolu_1",
                                            "name": "Read",
                                            "input": {{"file_path": "README.md"}},
                                        }}
                                    ],
                                    "stop_reason": "tool_use",
                                    "usage": {{"input_tokens": 8, "output_tokens": 4}},
                                }},
                            }}
                        ),
                        flush=True,
                    )
                    print(
                        json.dumps(
                            {{
                                "type": "user",
                                "session_id": session_id,
                                "message": {{
                                    "role": "user",
                                    "content": [
                                        {{
                                            "tool_use_id": "toolu_1",
                                            "type": "tool_result",
                                            "content": "README content",
                                            "is_error": False,
                                        }}
                                    ],
                                }},
                                "tool_use_result": {{
                                    "stdout": "README content",
                                    "stderr": "",
                                    "interrupted": False,
                                    "isImage": False,
                                    "noOutputExpected": False,
                                }},
                            }}
                        ),
                        flush=True,
                    )
                print(
                    json.dumps(
                        {{
                            "type": "assistant",
                            "session_id": session_id,
                            "message": {{
                                "id": "msg_final",
                                "type": "message",
                                "role": "assistant",
                                "model": model,
                                "content": [{{"type": "text", "text": result_text}}],
                                "stop_reason": "end_turn",
                                "usage": {{"input_tokens": 11, "output_tokens": 6}},
                            }},
                        }}
                    ),
                    flush=True,
                )
                print(
                    json.dumps(
                        {{
                            "type": "result",
                            "subtype": "success",
                            "is_error": False,
                            "duration_ms": 5,
                            "result": result_text,
                            "session_id": session_id,
                            "usage": {{
                                "input_tokens": 11,
                                "output_tokens": 6,
                                "cache_creation_input_tokens": 0,
                                "cache_read_input_tokens": 0,
                            }},
                            "total_cost_usd": 0,
                        }}
                    ),
                    flush=True,
                )
                return 0


            if __name__ == "__main__":
                raise SystemExit(main())
            """
        ),
        encoding="utf-8",
    )


def _build_claude_code_settings(tmp_path: Path, *, accepted_model: str = "gigachat/GigaChat-2") -> AdapterSettings:
    cli = tmp_path / "fake_claude_cli.py"
    _write_fake_claude_cli(cli, accepted_model=accepted_model)
    return AdapterSettings(
        binary=sys.executable,
        binary_args_json=json.dumps([str(cli)]),
        runner_type="claude_code",
        model_mode="override",
        model_override=accepted_model,
        permission_profile="workspace_write",
        gigachat_access_token="fake-access-token",
        work_root=tmp_path / "claude-code-work",
        run_start_timeout_ms=15_000,
    )


def _build_client(tmp_path: Path, *, accepted_model: str = "gigachat/GigaChat-2") -> TestClient:
    settings = _build_claude_code_settings(tmp_path, accepted_model=accepted_model)
    return TestClient(create_app(settings))


def _wait_until(predicate, timeout_s: float = 5.0) -> None:
    started = time.time()
    while time.time() - started < timeout_s:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("Condition was not met before timeout")


def test_claude_code_adapter_debug_reports_headless_gateway_fields(tmp_path: Path) -> None:
    with _build_client(tmp_path) as client:
        payload = client.get("/debug/runtime").json()
        assert payload["service"] == "claude-code-adapter"
        assert payload["runnerType"] == "claude_code"
        assert payload["preflightReady"] is True
        assert payload["headlessReady"] is None
        assert payload["gatewayReady"] is True
        assert payload["gigachatAuthReady"] is True
        assert payload["forcedModel"] == "gigachat/GigaChat-2"
        assert payload["resolvedModel"] == "gigachat/GigaChat-2"
        assert payload["permissionProfile"] == "workspace_write"
        assert "Read" in payload["allowedTools"]
        assert "Bash" not in payload["allowedTools"]

        probed = client.get("/debug/runtime", params={"includeProbe": "true"}).json()
        assert probed["preflightReady"] is True
        assert probed["headlessReady"] is True


def test_claude_code_adapter_run_reuses_session_and_publishes_artifacts(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()

    with _build_client(tmp_path) as client:
        session = client.post(
            "/v1/sessions",
            json={"externalSessionId": "session-a", "projectRoot": str(project_root), "profile": "agent"},
        )
        assert session.status_code == 200

        first = client.post(
            "/v1/runs",
            json={
                "runId": "run-1",
                "sessionId": "session-a",
                "projectRoot": str(project_root),
                "prompt": "remember banana",
                "profile": "agent",
            },
        )
        assert first.status_code == 200
        first_backend_run_id = first.json()["backendRunId"]

        _wait_until(lambda: client.get(f"/v1/runs/{first_backend_run_id}").json()["status"] == "succeeded")
        first_status = client.get(f"/v1/runs/{first_backend_run_id}").json()
        assert "remember banana" in first_status["output"]["summary"]

        second = client.post(
            "/v1/runs",
            json={
                "runId": "run-2",
                "sessionId": "session-a",
                "projectRoot": str(project_root),
                "prompt": "use tool to confirm",
                "profile": "agent",
            },
        )
        assert second.status_code == 200
        second_backend_run_id = second.json()["backendRunId"]

        _wait_until(lambda: client.get(f"/v1/runs/{second_backend_run_id}").json()["status"] == "succeeded")
        second_status = client.get(f"/v1/runs/{second_backend_run_id}").json()
        assert second_status["backendSessionId"] == session.json()["backendSessionId"]
        assert "remember banana" in second_status["output"]["summary"]
        assert "use tool to confirm" in second_status["output"]["summary"]

        names = {item["name"] for item in second_status["artifacts"]}
        assert {"stdout.stream.jsonl", "stderr.log", "result.json"}.issubset(names)

        events = client.get(f"/v1/runs/{second_backend_run_id}/events", params={"after": 0}).json()
        event_types = [item["eventType"] for item in events["items"]]
        assert "run.started" in event_types
        assert "run.progress" in event_types
        assert "run.artifact_published" in event_types
        assert "run.succeeded" in event_types


def test_claude_code_adapter_repairs_legacy_non_uuid_backend_session_ids(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()

    with _build_client(tmp_path) as client:
        client.app.state.claude_code_adapter_state_store.upsert_session_mapping(
            "legacy-session",
            backend_session_id="session-53477",
            project_root=str(project_root),
            last_backend_run_id=None,
            status="idle",
            current_action="Idle",
            last_activity_at="2026-03-12T00:00:00Z",
        )

        session = client.post(
            "/v1/sessions",
            json={"externalSessionId": "legacy-session", "projectRoot": str(project_root), "profile": "agent"},
        )
        assert session.status_code == 200
        repaired_session_id = session.json()["backendSessionId"]
        assert repaired_session_id != "session-53477"
        assert len(repaired_session_id) == 36

        run = client.post(
            "/v1/runs",
            json={
                "runId": "run-legacy",
                "sessionId": "legacy-session",
                "projectRoot": str(project_root),
                "prompt": "reply with exactly ok",
                "profile": "agent",
            },
        )
        assert run.status_code == 200
        backend_run_id = run.json()["backendRunId"]

        _wait_until(lambda: client.get(f"/v1/runs/{backend_run_id}").json()["status"] == "succeeded")
        run_status = client.get(f"/v1/runs/{backend_run_id}").json()
        assert run_status["backendSessionId"] == repaired_session_id
        assert "reply with exactly ok" in run_status["output"]["summary"]


def test_claude_code_adapter_diff_and_compact_noop(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is required for diff test")

    project_root = tmp_path / "project"
    project_root.mkdir()
    tracked = project_root / "tracked.txt"
    tracked.write_text("line1\n", encoding="utf-8")

    subprocess.run(["git", "-C", str(project_root), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(project_root), "config", "user.email", "codex@example.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(project_root), "config", "user.name", "Codex"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(project_root), "add", "tracked.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(project_root), "commit", "-m", "init"], check=True, capture_output=True)

    tracked.write_text("line1\nline2\n", encoding="utf-8")

    with _build_client(tmp_path) as client:
        session = client.post(
            "/v1/sessions",
            json={"externalSessionId": "session-a", "projectRoot": str(project_root), "profile": "agent"},
        )
        assert session.status_code == 200

        diff = client.get("/v1/sessions/session-a/diff")
        assert diff.status_code == 200
        payload = diff.json()
        assert payload["summary"]["files"] >= 1
        assert any(item["file"] == "tracked.txt" for item in payload["files"])

        compact = client.post("/v1/sessions/session-a/compact")
        assert compact.status_code == 200
        assert compact.json()["result"]["status"] == "noop"


def test_claude_code_adapter_blocks_when_gateway_auth_is_unavailable(tmp_path: Path) -> None:
    settings = _build_claude_code_settings(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    app = create_app(settings)
    app.state.anthropic_gateway_service.health_snapshot = lambda force_refresh=False: {
        "ready": False,
        "gatewayReady": True,
        "gigachatAuthReady": False,
        "resolvedModel": "gigachat/GigaChat-2",
        "issues": [{"code": "gigachat_auth_failed", "message": "auth missing", "details": {}}],
    }

    with TestClient(app) as client:
        debug = client.get("/debug/runtime")
        assert debug.status_code == 200
        payload = debug.json()
        assert payload["preflightReady"] is False
        issue_codes = {item["code"] for item in payload["preflightIssues"]}
        assert "gigachat_auth_failed" in issue_codes

        session = client.post(
            "/v1/sessions",
            json={"externalSessionId": "session-a", "projectRoot": str(project_root), "profile": "agent"},
        )
        assert session.status_code == 503
        assert session.json()["error"]["code"] == "claude_code_preflight_failed"


def test_internal_anthropic_routes_require_gateway_auth(tmp_path: Path) -> None:
    settings = _build_claude_code_settings(tmp_path)
    app = create_app(settings)
    app.state.anthropic_gateway_service.create_message = lambda payload: {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "gigachat/GigaChat-2",
        "content": [{"type": "text", "text": f"echo: {payload['messages'][0]['content'][0]['text']}"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    app.state.anthropic_gateway_service.count_tokens = lambda payload: {"input_tokens": 7}

    with TestClient(app) as client:
        unauthorized = client.get("/internal/anthropic/v1/models")
        assert unauthorized.status_code == 401

        headers = {"Authorization": f"Bearer {settings.gateway_token}"}
        models = client.get("/internal/anthropic/v1/models", headers=headers)
        assert models.status_code == 200
        assert models.json()["data"][0]["id"] == "gigachat/GigaChat-2"

        message = client.post(
            "/internal/anthropic/v1/messages",
            headers=headers,
            json={
                "model": "gigachat/GigaChat-2",
                "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
            },
        )
        assert message.status_code == 200
        assert message.json()["content"][0]["text"] == "echo: hello"

        tokens = client.post(
            "/internal/anthropic/v1/messages/count_tokens",
            headers=headers,
            json={"messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]},
        )
        assert tokens.status_code == 200
        assert tokens.json()["input_tokens"] == 7

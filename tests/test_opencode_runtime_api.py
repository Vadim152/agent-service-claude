from __future__ import annotations

import tempfile
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes_policy import router as policy_router
from api.routes_runs import router as runs_router
from api.routes_sessions import router as sessions_router
from chat.memory_store import ChatMemoryStore
from chat.runtime import ChatAgentRuntime
from infrastructure.run_state_store import RunStateStore
from policy import InMemoryPolicyStore, PolicyService
from runtime.opencode_adapter import OpenCodeAdapterError
from runtime.opencode_runtime import OpenCodeRunDriver, OpenCodeSessionRuntime
from runtime.run_service import RunService
from runtime.session_runtime import SessionRuntimeRegistry


def _wait_until(assertion, timeout_s: float = 5.0) -> None:
    started = time.time()
    while time.time() - started < timeout_s:
        if assertion():
            return
        time.sleep(0.05)
    raise AssertionError("Condition was not met before timeout")


class _FakeOpenCodeAdapter:
    def __init__(self) -> None:
        self.runs: dict[str, dict[str, object]] = {}
        self.sessions: dict[str, dict[str, object]] = {}

    def ensure_session(self, payload: dict[str, object]) -> dict[str, object]:
        external_session_id = str(payload.get("externalSessionId") or "")
        project_root = str(payload.get("projectRoot") or "")
        existing = self.sessions.get(external_session_id)
        if existing is None:
            existing = {
                "externalSessionId": external_session_id,
                "backendSessionId": f"session-{len(self.sessions) + 1}",
                "projectRoot": project_root,
                "lastBackendRunId": None,
                "status": "idle",
                "currentAction": "Idle",
                "updatedAt": "2026-03-09T00:00:00+00:00",
            }
            self.sessions[external_session_id] = existing
        return dict(existing)

    def get_session(self, external_session_id: str) -> dict[str, object]:
        return dict(self.sessions[external_session_id])

    def compact_session(self, external_session_id: str) -> dict[str, object]:
        session = self.sessions[external_session_id]
        session["currentAction"] = "Idle"
        return {
            "externalSessionId": external_session_id,
            "command": "compact",
            "accepted": True,
            "result": {"status": "completed", "backendSessionId": session["backendSessionId"]},
            "updatedAt": "2026-03-09T00:00:01+00:00",
        }

    def get_session_diff(self, external_session_id: str) -> dict[str, object]:
        session = self.sessions[external_session_id]
        return {
            "externalSessionId": external_session_id,
            "backendSessionId": session["backendSessionId"],
            "summary": {"files": 1, "additions": 3, "deletions": 1},
            "files": [{"file": "src/app/main.py", "additions": 3, "deletions": 1, "before": "", "after": ""}],
            "stale": False,
            "updatedAt": "2026-03-09T00:00:02+00:00",
        }

    def execute_session_command(self, external_session_id: str, command: str) -> dict[str, object]:
        if command == "status":
            return {
                "externalSessionId": external_session_id,
                "command": command,
                "accepted": True,
                "result": {"status": self.get_session(external_session_id)},
                "updatedAt": "2026-03-09T00:00:03+00:00",
            }
        if command == "diff":
            return {
                "externalSessionId": external_session_id,
                "command": command,
                "accepted": True,
                "result": {"diff": self.get_session_diff(external_session_id)},
                "updatedAt": "2026-03-09T00:00:03+00:00",
            }
        if command == "help":
            return {
                "externalSessionId": external_session_id,
                "command": command,
                "accepted": True,
                "result": {"commands": ["status", "diff", "compact", "abort", "help"]},
                "updatedAt": "2026-03-09T00:00:03+00:00",
            }
        raise AssertionError(f"Unsupported fake session command: {command}")

    def create_run(self, payload: dict[str, object]) -> dict[str, object]:
        backend_run_id = f"oc-{len(self.runs) + 1}"
        prompt = str(payload.get("prompt") or "")
        needs_approval = "approval" in prompt.lower()
        session_id = str(payload.get("sessionId") or "")
        backend_session_id = str(payload.get("backendSessionId") or self.sessions[session_id]["backendSessionId"])
        self.runs[backend_run_id] = {
            "backendRunId": backend_run_id,
            "backendSessionId": backend_session_id,
            "externalSessionId": session_id,
            "status": "running",
            "currentAction": "Executing OpenCode run",
            "prompt": prompt,
            "polls": 0,
            "approved": not needs_approval,
            "pendingApprovals": [
                {
                    "approvalId": f"{backend_run_id}-approval",
                    "toolName": "repo.write",
                    "title": "Approve repo write",
                    "riskLevel": "high",
                }
            ]
            if needs_approval
            else [],
            "events": [
                {"eventType": "run.progress", "payload": {"stage": "created"}},
            ],
        }
        self.sessions[session_id]["lastBackendRunId"] = backend_run_id
        self.sessions[session_id]["status"] = "running"
        self.sessions[session_id]["currentAction"] = "Executing OpenCode run"
        return {
            "backendRunId": backend_run_id,
            "backendSessionId": backend_session_id,
            "status": "running",
            "currentAction": "Executing OpenCode run",
        }

    def get_run(self, backend_run_id: str) -> dict[str, object]:
        run = self.runs[backend_run_id]
        run["polls"] = int(run.get("polls", 0)) + 1
        usage_totals = {
            "tokens": {"input": 120, "output": 80, "reasoning": 20, "cacheRead": 0, "cacheWrite": 0},
            "cost": 0.0,
        }
        usage_limits = {"contextWindow": 200000, "used": 220, "percent": 0.0011}
        if bool(run.get("cancelled")):
            self.sessions[str(run["externalSessionId"])]["status"] = "idle"
            self.sessions[str(run["externalSessionId"])]["currentAction"] = "Idle"
            return {
                "backendRunId": backend_run_id,
                "backendSessionId": run["backendSessionId"],
                "status": "cancelled",
                "currentAction": "Cancelled",
                "output": {"summary": "OpenCode run was cancelled."},
                "pendingApprovals": [],
                "totals": usage_totals,
                "limits": usage_limits,
            }
        if run.get("pendingApprovals") and not bool(run.get("approved")):
            self.sessions[str(run["externalSessionId"])]["status"] = "running"
            self.sessions[str(run["externalSessionId"])]["currentAction"] = "Awaiting approval"
            return {
                "backendRunId": backend_run_id,
                "backendSessionId": run["backendSessionId"],
                "status": "running",
                "currentAction": "Awaiting approval",
                "pendingApprovals": list(run.get("pendingApprovals") or []),
                "totals": usage_totals,
                "limits": usage_limits,
            }
        if int(run.get("polls", 0)) >= 2:
            self.sessions[str(run["externalSessionId"])]["status"] = "idle"
            self.sessions[str(run["externalSessionId"])]["currentAction"] = "Idle"
            return {
                "backendRunId": backend_run_id,
                "backendSessionId": run["backendSessionId"],
                "status": "succeeded",
                "currentAction": "Completed",
                "output": {"summary": f"OpenCode finished: {run['prompt']}"},
                "artifacts": [{"name": "stdout.log", "uri": f"https://obj.local/{backend_run_id}/stdout.log"}],
                "pendingApprovals": [],
                "totals": usage_totals,
                "limits": usage_limits,
            }
        return {
            "backendRunId": backend_run_id,
            "backendSessionId": run["backendSessionId"],
            "status": "running",
            "currentAction": "Executing OpenCode run",
            "pendingApprovals": [],
            "totals": usage_totals,
            "limits": usage_limits,
        }

    def list_events(self, backend_run_id: str, *, after: int | str | None, limit: int | None = None) -> dict[str, object]:
        run = self.runs[backend_run_id]
        cursor = int(after or 0)
        items = list(run.get("events") or [])[cursor:]
        if limit is not None:
            items = items[:limit]
        return {"items": items, "nextCursor": cursor + len(items), "hasMore": False}

    def cancel_run(self, backend_run_id: str) -> dict[str, object]:
        self.runs[backend_run_id]["cancelled"] = True
        session_id = str(self.runs[backend_run_id]["externalSessionId"])
        self.sessions[session_id]["status"] = "idle"
        self.sessions[session_id]["currentAction"] = "Idle"
        return {"backendRunId": backend_run_id, "status": "cancelled"}

    def submit_approval_decision(self, backend_run_id: str, approval_id: str, decision: str) -> dict[str, object]:
        run = self.runs[backend_run_id]
        run["approved"] = decision == "approve"
        run["pendingApprovals"] = []
        session_id = str(run["externalSessionId"])
        self.sessions[session_id]["status"] = "running"
        self.sessions[session_id]["currentAction"] = "Executing OpenCode run"
        run.setdefault("events", []).append(
            {"eventType": "approval.decision", "payload": {"approvalId": approval_id, "decision": decision}}
        )
        return {"backendRunId": backend_run_id, "approvalId": approval_id, "decision": decision}


def _build_app(adapter: _FakeOpenCodeAdapter | None = None) -> FastAPI:
    app = FastAPI()
    base = Path(tempfile.mkdtemp(prefix="opencode-runtime-"))
    memory_store = ChatMemoryStore(base)
    run_state_store = RunStateStore()
    adapter = adapter or _FakeOpenCodeAdapter()

    chat_runtime = ChatAgentRuntime(memory_store=memory_store)
    policy_service = PolicyService(state_store=chat_runtime.state_store, store=InMemoryPolicyStore())
    chat_runtime.bind_policy_service(policy_service)

    opencode_runtime = OpenCodeSessionRuntime(
        state_store=chat_runtime.state_store,
        run_state_store=run_state_store,
        adapter_client=adapter,
    )
    opencode_driver = OpenCodeRunDriver(
        adapter_client=adapter,
        run_state_store=run_state_store,
        session_state_store=chat_runtime.state_store,
        policy_service=policy_service,
    )
    registry = SessionRuntimeRegistry(state_store=chat_runtime.state_store)
    registry.register(chat_runtime)
    registry.register(opencode_runtime)

    run_service = RunService(
        run_state_store=run_state_store,
        supervisor=None,
        task_registry=None,
        plugin_drivers={"opencode": opencode_driver},
    )
    opencode_runtime.bind_run_service(run_service)
    policy_service.bind_decision_executor(
        lambda session_id, run_id, approval_id, decision: registry.resolve_session(session_id).process_tool_decision(
            session_id=session_id,
            run_id=run_id,
            permission_id=approval_id,
            decision=decision,
        )
    )
    policy_service.sync_tools(registry.all_tools())

    app.state.chat_runtime = chat_runtime
    app.state.opencode_runtime = opencode_runtime
    app.state.policy_service = policy_service
    app.state.run_state_store = run_state_store
    app.state.run_service = run_service
    app.state.session_runtime_registry = registry
    app.state.opencode_adapter_client = adapter
    app.include_router(sessions_router)
    app.include_router(policy_router)
    app.include_router(runs_router)
    return app


def test_opencode_sessions_are_separate_from_chat_sessions() -> None:
    client = TestClient(_build_app())
    project_root = str(Path(tempfile.mkdtemp(prefix="opencode-shared-root-")).resolve())

    chat_session = client.post("/sessions", json={"projectRoot": project_root, "runtime": "chat"}).json()
    opencode_session = client.post("/sessions", json={"projectRoot": project_root, "runtime": "opencode"}).json()

    assert chat_session["runtime"] == "chat"
    assert opencode_session["runtime"] == "opencode"
    assert chat_session["sessionId"] != opencode_session["sessionId"]

    listing = client.get("/sessions", params={"projectRoot": project_root}).json()
    runtimes = {item["runtime"] for item in listing["items"]}
    assert {"chat", "opencode"}.issubset(runtimes)


def test_opencode_session_normalizes_project_root() -> None:
    client = TestClient(_build_app())
    project_root = Path(tempfile.mkdtemp(prefix="opencode-project-root-")).resolve()
    raw_project_root = str(project_root / "." / ".." / project_root.name)

    session = client.post(
        "/sessions",
        json={"projectRoot": raw_project_root, "runtime": "opencode"},
    )
    assert session.status_code == 200
    session_id = session.json()["sessionId"]

    history = client.get(f"/sessions/{session_id}/history").json()
    assert history["projectRoot"] == str(project_root)


def test_opencode_message_creates_delegated_run_and_updates_history() -> None:
    app = _build_app()
    client = TestClient(app)
    session_id = client.post("/sessions", json={"projectRoot": "/tmp/project", "runtime": "opencode"}).json()["sessionId"]

    response = client.post(f"/sessions/{session_id}/messages", json={"content": "run delegated agent"})
    assert response.status_code == 200

    def _run_completed() -> bool:
        payload = client.get(f"/sessions/{session_id}/status").json()
        return payload.get("activeRunStatus") == "succeeded"

    _wait_until(_run_completed)
    status = client.get(f"/sessions/{session_id}/status").json()
    assert status["runtime"] == "opencode"
    assert status["activeRunBackend"] == "opencode-adapter"
    assert status["totals"]["tokens"]["input"] == 120
    assert status["totals"]["tokens"]["output"] == 80
    assert status["totals"]["tokens"]["reasoning"] == 20
    assert status["limits"]["percent"] == 0.0011
    run_id = status["activeRunId"]

    run_payload = client.get(f"/runs/{run_id}").json()
    assert run_payload["plugin"] == "opencode"
    assert run_payload["runtime"] == "opencode"
    assert run_payload["backend"] == "opencode-adapter"
    assert run_payload["backendRunId"].startswith("oc-")

    history = client.get(f"/sessions/{session_id}/history").json()
    assert history["runtime"] == "opencode"
    assert any("OpenCode finished" in item["content"] for item in history["messages"])
    progress_events = [item for item in history["events"] if item["eventType"] == "opencode.run.progress"]
    assert progress_events
    assert any(str(item.get("payload", {}).get("message", "")).strip() for item in progress_events)


def test_opencode_approval_flow_uses_policy_endpoint() -> None:
    app = _build_app()
    client = TestClient(app)
    session_id = client.post("/sessions", json={"projectRoot": "/tmp/project", "runtime": "opencode"}).json()["sessionId"]

    response = client.post(f"/sessions/{session_id}/messages", json={"content": "run delegated agent with approval"})
    assert response.status_code == 200

    _wait_until(lambda: client.get("/policy/approvals").json()["total"] == 1)
    approval = client.get("/policy/approvals").json()["items"][0]
    decision = client.post(f"/policy/approvals/{approval['approvalId']}/decision", json={"decision": "approve"})
    assert decision.status_code == 200

    _wait_until(lambda: client.get(f"/sessions/{session_id}/status").json().get("activeRunStatus") == "succeeded")
    audit = client.get("/policy/audit", params={"limit": 20}).json()
    event_types = [item["eventType"] for item in audit["items"]]
    assert "permission.requested" in event_types
    assert "permission.approved" in event_types


def test_opencode_create_session_eagerly_ensures_backend_mapping() -> None:
    app = _build_app()
    client = TestClient(app)

    session_id = client.post("/sessions", json={"projectRoot": "/tmp/project", "runtime": "opencode"}).json()["sessionId"]

    adapter = app.state.opencode_adapter_client
    assert session_id in adapter.sessions
    assert adapter.sessions[session_id]["backendSessionId"].startswith("session-")


def test_opencode_compact_command_emits_session_events() -> None:
    app = _build_app()
    client = TestClient(app)
    session_id = client.post("/sessions", json={"projectRoot": "/tmp/project", "runtime": "opencode"}).json()["sessionId"]

    response = client.post(f"/sessions/{session_id}/commands", json={"command": "compact"})

    assert response.status_code == 200
    assert response.json()["result"]["status"] == "completed"
    history = client.get(f"/sessions/{session_id}/history").json()
    event_types = [item["eventType"] for item in history["events"]]
    assert "opencode.compact.started" in event_types
    assert "opencode.compact.succeeded" in event_types


def test_opencode_help_command_includes_full_command_set() -> None:
    client = TestClient(_build_app())
    session_id = client.post("/sessions", json={"projectRoot": "/tmp/project", "runtime": "opencode"}).json()["sessionId"]

    response = client.post(f"/sessions/{session_id}/commands", json={"command": "help"})

    assert response.status_code == 200
    assert response.json()["result"]["commands"] == ["status", "diff", "compact", "abort", "help"]


def test_opencode_structured_adapter_errors_surface_to_session_api() -> None:
    class _BusyAdapter(_FakeOpenCodeAdapter):
        def compact_session(self, external_session_id: str) -> dict[str, object]:
            raise OpenCodeAdapterError(
                "Session is busy and cannot be compacted.",
                status_code=409,
                code="session_busy",
                retryable=False,
            )

    client = TestClient(_build_app(adapter=_BusyAdapter()))
    session_id = client.post("/sessions", json={"projectRoot": "/tmp/project", "runtime": "opencode"}).json()["sessionId"]

    response = client.post(f"/sessions/{session_id}/commands", json={"command": "compact"})

    assert response.status_code == 409
    payload = response.json()["detail"]["error"] if "detail" in response.json() else response.json()["error"]
    assert payload["code"] == "session_busy"
    assert "cannot be compacted" in payload["message"]


def test_opencode_smoke_compact_and_continue_same_backend_session() -> None:
    app = _build_app()
    client = TestClient(app)
    session_id = client.post("/sessions", json={"projectRoot": "/tmp/project", "runtime": "opencode"}).json()["sessionId"]

    first = client.post(f"/sessions/{session_id}/messages", json={"content": "run delegated agent"})
    assert first.status_code == 200
    _wait_until(lambda: client.get(f"/sessions/{session_id}/status").json().get("activeRunStatus") == "succeeded")
    first_run_id = client.get(f"/sessions/{session_id}/status").json()["activeRunId"]
    first_run = client.get(f"/runs/{first_run_id}").json()

    diff = client.get(f"/sessions/{session_id}/diff").json()
    assert diff["summary"]["files"] == 1

    compact = client.post(f"/sessions/{session_id}/commands", json={"command": "compact"})
    assert compact.status_code == 200
    assert compact.json()["result"]["status"] == "completed"

    second = client.post(f"/sessions/{session_id}/messages", json={"content": "run delegated agent again"})
    assert second.status_code == 200
    _wait_until(lambda: client.get(f"/sessions/{session_id}/status").json().get("activeRunStatus") == "succeeded")
    second_run_id = client.get(f"/sessions/{session_id}/status").json()["activeRunId"]
    second_run = client.get(f"/runs/{second_run_id}").json()

    assert second_run["backendSessionId"] == first_run["backendSessionId"]


def test_terminal_message_extracts_structured_error_text() -> None:
    run = {"status": "failed"}
    status_payload = {
        "output": {
            "message": {
                "info": {
                    "error": {
                        "name": "APIError",
                        "data": {"message": "Unauthorized: Token has expired", "statusCode": 401},
                    }
                }
            }
        }
    }

    message = OpenCodeRunDriver._build_terminal_message(run, status_payload)

    assert message == "Unauthorized: Token has expired"


def test_terminal_message_falls_back_to_current_action_error() -> None:
    run = {"status": "failed"}
    status_payload = {"currentAction": "Error: self signed certificate in certificate chain"}

    message = OpenCodeRunDriver._build_terminal_message(run, status_payload)

    assert message == "Error: self signed certificate in certificate chain"

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

    def create_run(self, payload: dict[str, object]) -> dict[str, object]:
        backend_run_id = f"oc-{len(self.runs) + 1}"
        prompt = str(payload.get("prompt") or "")
        needs_approval = "approval" in prompt.lower()
        self.runs[backend_run_id] = {
            "backendRunId": backend_run_id,
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
        return {
            "backendRunId": backend_run_id,
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
            return {
                "backendRunId": backend_run_id,
                "status": "cancelled",
                "currentAction": "Cancelled",
                "output": {"summary": "OpenCode run was cancelled."},
                "pendingApprovals": [],
                "totals": usage_totals,
                "limits": usage_limits,
            }
        if run.get("pendingApprovals") and not bool(run.get("approved")):
            return {
                "backendRunId": backend_run_id,
                "status": "running",
                "currentAction": "Awaiting approval",
                "pendingApprovals": list(run.get("pendingApprovals") or []),
                "totals": usage_totals,
                "limits": usage_limits,
            }
        if int(run.get("polls", 0)) >= 2:
            return {
                "backendRunId": backend_run_id,
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
            "status": "running",
            "currentAction": "Executing OpenCode run",
            "pendingApprovals": [],
            "totals": usage_totals,
            "limits": usage_limits,
        }

    def list_events(self, backend_run_id: str, *, after: int | str | None) -> dict[str, object]:
        run = self.runs[backend_run_id]
        cursor = int(after or 0)
        items = list(run.get("events") or [])[cursor:]
        return {"items": items, "nextCursor": cursor + len(items)}

    def cancel_run(self, backend_run_id: str) -> dict[str, object]:
        self.runs[backend_run_id]["cancelled"] = True
        return {"backendRunId": backend_run_id, "status": "cancelled"}

    def submit_approval_decision(self, backend_run_id: str, approval_id: str, decision: str) -> dict[str, object]:
        run = self.runs[backend_run_id]
        run["approved"] = decision == "approve"
        run["pendingApprovals"] = []
        run.setdefault("events", []).append(
            {"eventType": "approval.decision", "payload": {"approvalId": approval_id, "decision": decision}}
        )
        return {"backendRunId": backend_run_id, "approvalId": approval_id, "decision": decision}


def _build_app() -> FastAPI:
    app = FastAPI()
    base = Path(tempfile.mkdtemp(prefix="opencode-runtime-"))
    memory_store = ChatMemoryStore(base)
    run_state_store = RunStateStore()
    adapter = _FakeOpenCodeAdapter()

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

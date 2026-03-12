from __future__ import annotations

import tempfile
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes_policy import router as policy_router
from api.routes_runs import router as runs_router
from api.routes_sessions import router as sessions_router
from chat.memory_store import ChatMemoryStore
from chat.runtime import ChatAgentRuntime
from infrastructure.run_state_store import RunStateStore
from policy import InMemoryPolicyStore, PolicyService
from runtime.agent_adapter import ClaudeCodeAdapterError
from runtime.agent_runtime import AgentRunDriver, AgentSessionRuntime
from runtime.run_service import RunService
from runtime.session_runtime import SessionRuntimeRegistry


def _wait_until(assertion, timeout_s: float = 5.0) -> None:
    started = time.time()
    while time.time() - started < timeout_s:
        if assertion():
            return
        time.sleep(0.05)
    raise AssertionError("Condition was not met before timeout")


class _FakeClaudeCodeAdapter:
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
                "updatedAt": "2026-03-12T00:00:00+00:00",
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
            "result": {"status": "noop", "backendSessionId": session["backendSessionId"]},
            "updatedAt": "2026-03-12T00:00:01+00:00",
        }

    def get_session_diff(self, external_session_id: str) -> dict[str, object]:
        session = self.sessions[external_session_id]
        return {
            "externalSessionId": external_session_id,
            "backendSessionId": session["backendSessionId"],
            "summary": {"files": 1, "additions": 3, "deletions": 1},
            "files": [{"file": "src/app/main.py", "additions": 3, "deletions": 1, "before": "", "after": ""}],
            "stale": False,
            "updatedAt": "2026-03-12T00:00:02+00:00",
        }

    def execute_session_command(self, external_session_id: str, command: str) -> dict[str, object]:
        if command == "status":
            return {
                "externalSessionId": external_session_id,
                "command": command,
                "accepted": True,
                "result": {"status": self.get_session(external_session_id)},
                "updatedAt": "2026-03-12T00:00:03+00:00",
            }
        if command == "diff":
            return {
                "externalSessionId": external_session_id,
                "command": command,
                "accepted": True,
                "result": {"diff": self.get_session_diff(external_session_id)},
                "updatedAt": "2026-03-12T00:00:03+00:00",
            }
        if command == "help":
            return {
                "externalSessionId": external_session_id,
                "command": command,
                "accepted": True,
                "result": {"commands": ["status", "diff", "compact", "abort", "help"]},
                "updatedAt": "2026-03-12T00:00:03+00:00",
            }
        raise AssertionError(f"Unsupported fake session command: {command}")

    def create_run(self, payload: dict[str, object]) -> dict[str, object]:
        backend_run_id = f"claude-run-{len(self.runs) + 1}"
        prompt = str(payload.get("prompt") or "")
        needs_approval = "approval" in prompt.lower()
        session_id = str(payload.get("sessionId") or "")
        backend_session_id = str(payload.get("backendSessionId") or self.sessions[session_id]["backendSessionId"])
        self.runs[backend_run_id] = {
            "backendRunId": backend_run_id,
            "backendSessionId": backend_session_id,
            "externalSessionId": session_id,
            "status": "running",
            "currentAction": "Executing agent run",
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
            "events": [{"eventType": "run.progress", "payload": {"currentAction": "Scanning files"}}],
        }
        self.sessions[session_id]["lastBackendRunId"] = backend_run_id
        self.sessions[session_id]["status"] = "running"
        self.sessions[session_id]["currentAction"] = "Executing agent run"
        return {
            "backendRunId": backend_run_id,
            "backendSessionId": backend_session_id,
            "status": "running",
            "currentAction": "Executing agent run",
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
                "output": {"summary": "Agent run was cancelled."},
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
                "output": {"summary": f"Agent finished: {run['prompt']}"},
                "artifacts": [{"name": "stdout.log", "uri": f"https://obj.local/{backend_run_id}/stdout.log"}],
                "pendingApprovals": [],
                "totals": usage_totals,
                "limits": usage_limits,
            }
        return {
            "backendRunId": backend_run_id,
            "backendSessionId": run["backendSessionId"],
            "status": "running",
            "currentAction": "Executing agent run",
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
        self.sessions[session_id]["currentAction"] = "Executing agent run"
        run.setdefault("events", []).append(
            {"eventType": "approval.decision", "payload": {"approvalId": approval_id, "decision": decision}}
        )
        return {"backendRunId": backend_run_id, "approvalId": approval_id, "decision": decision}


def _build_app(adapter: _FakeClaudeCodeAdapter | None = None) -> FastAPI:
    app = FastAPI()
    base = Path(tempfile.mkdtemp(prefix="agent-runtime-"))
    memory_store = ChatMemoryStore(base)
    run_state_store = RunStateStore()
    adapter = adapter or _FakeClaudeCodeAdapter()

    chat_runtime = ChatAgentRuntime(memory_store=memory_store)
    policy_service = PolicyService(state_store=chat_runtime.state_store, store=InMemoryPolicyStore())
    chat_runtime.bind_policy_service(policy_service)

    agent_runtime = AgentSessionRuntime(
        state_store=chat_runtime.state_store,
        run_state_store=run_state_store,
        adapter_client=adapter,
    )
    agent_driver = AgentRunDriver(
        adapter_client=adapter,
        run_state_store=run_state_store,
        session_state_store=chat_runtime.state_store,
        policy_service=policy_service,
    )
    registry = SessionRuntimeRegistry(state_store=chat_runtime.state_store)
    registry.register(chat_runtime)
    registry.register(agent_runtime)

    run_service = RunService(
        run_state_store=run_state_store,
        supervisor=None,
        task_registry=None,
        plugin_drivers={"agent": agent_driver},
    )
    agent_runtime.bind_run_service(run_service)
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
    app.state.agent_runtime = agent_runtime
    app.state.policy_service = policy_service
    app.state.run_state_store = run_state_store
    app.state.run_service = run_service
    app.state.session_runtime_registry = registry
    app.state.agent_adapter_client = adapter
    app.include_router(sessions_router)
    app.include_router(policy_router)
    app.include_router(runs_router)
    return app


def test_agent_runtime_rejects_legacy_opencode_contract() -> None:
    client = TestClient(_build_app())

    response = client.post("/sessions", json={"projectRoot": "/tmp/project", "runtime": "opencode"})

    assert response.status_code == 422
    assert "opencode" in response.text


def test_agent_message_creates_delegated_run_and_history_uses_canonical_events() -> None:
    client = TestClient(_build_app())

    session_id = client.post("/sessions", json={"projectRoot": "/tmp/project", "runtime": "agent"}).json()["sessionId"]

    accepted = client.post(
        f"/sessions/{session_id}/messages",
        json={"role": "user", "content": "Please inspect the repo"},
    )
    assert accepted.status_code == 200
    run_id = accepted.json()["runId"]

    _wait_until(lambda: client.get(f"/sessions/{session_id}/status").json()["activeRunStatus"] == "succeeded")

    status_payload = client.get(f"/sessions/{session_id}/status").json()
    assert status_payload["runtime"] == "agent"
    assert status_payload["activeRunBackend"] == "claude_code"

    run_payload = client.get(f"/runs/{run_id}").json()
    assert run_payload["plugin"] == "agent"
    assert run_payload["runtime"] == "agent"
    assert run_payload["backend"] == "claude_code"

    history = client.get(f"/sessions/{session_id}/history").json()
    assert history["runtime"] == "agent"
    assert any("Agent finished" in item["content"] for item in history["messages"])
    assert any(item["eventType"] == "run.progress" for item in history["events"])
    assert all(not item["eventType"].startswith("opencode.") for item in history["events"])


def test_agent_approval_flow_uses_policy_endpoint() -> None:
    client = TestClient(_build_app())
    session_id = client.post("/sessions", json={"projectRoot": "/tmp/project", "runtime": "agent"}).json()["sessionId"]

    accepted = client.post(
        f"/sessions/{session_id}/messages",
        json={"role": "user", "content": "Need approval before changing files"},
    )
    run_id = accepted.json()["runId"]

    def _approval_ready() -> bool:
        payload = client.get(f"/sessions/{session_id}/history").json()
        return bool(payload["pendingPermissions"])

    _wait_until(_approval_ready)
    history = client.get(f"/sessions/{session_id}/history").json()
    approval_id = history["pendingPermissions"][0]["permissionId"]

    decision = client.post(
        f"/policy/approvals/{approval_id}/decision",
        json={"decision": "approve"},
    )
    assert decision.status_code == 200

    _wait_until(lambda: client.get(f"/sessions/{session_id}/status").json()["activeRunStatus"] == "succeeded")
    history = client.get(f"/sessions/{session_id}/history").json()
    assert any(item["eventType"] == "approval.decision" for item in history["events"])


def test_agent_compact_command_keeps_safe_noop_fallback() -> None:
    client = TestClient(_build_app())
    session_id = client.post("/sessions", json={"projectRoot": "/tmp/project", "runtime": "agent"}).json()["sessionId"]

    response = client.post(f"/sessions/{session_id}/commands", json={"command": "compact"})

    assert response.status_code == 200
    assert response.json()["result"]["status"] == "noop"
    history = client.get(f"/sessions/{session_id}/history").json()
    event_types = [item["eventType"] for item in history["events"]]
    assert "session.compact.started" in event_types
    assert "session.compact.succeeded" in event_types


def test_agent_runtime_surfaces_structured_adapter_errors() -> None:
    class _BusyAdapter(_FakeClaudeCodeAdapter):
        def ensure_session(self, payload: dict[str, object]) -> dict[str, object]:
            raise ClaudeCodeAdapterError(
                "Backend session is busy",
                status_code=409,
                code="session_busy",
                retryable=False,
                details={"reason": "already_running"},
            )

    client = TestClient(_build_app(adapter=_BusyAdapter()))

    response = client.post("/sessions", json={"projectRoot": "/tmp/project", "runtime": "agent"})

    assert response.status_code == 409
    payload = response.json()["detail"]["error"]
    assert payload["code"] == "session_busy"
    assert payload["details"]["reason"] == "already_running"

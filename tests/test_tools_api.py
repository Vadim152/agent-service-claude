from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes_tools import router as tools_router


def _build_app() -> FastAPI:
    app = FastAPI()
    app.state.orchestrator = SimpleNamespace(
        find_steps=lambda **kwargs: {"kind": "find_steps", **kwargs},
        compose_autotest=lambda **kwargs: {"kind": "compose_autotest", **kwargs},
        explain_unmapped=lambda match_result: {"kind": "explain_unmapped", "matchResult": match_result},
    )
    app.include_router(tools_router)
    return app


def test_find_steps_tool_endpoint() -> None:
    app = _build_app()
    client = TestClient(app)
    response = client.post(
        "/tools/find-steps",
        json={"projectRoot": "/tmp/project", "query": "login", "topK": 3},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "find_steps"
    assert payload["top_k"] == 3
    assert payload["debug"] is False


def test_find_steps_tool_endpoint_with_debug() -> None:
    app = _build_app()
    client = TestClient(app)
    response = client.post(
        "/tools/find-steps",
        json={"projectRoot": "/tmp/project", "query": "login", "topK": 3, "debug": True},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "find_steps"
    assert payload["debug"] is True


def test_compose_autotest_tool_endpoint() -> None:
    app = _build_app()
    client = TestClient(app)
    response = client.post(
        "/tools/compose-autotest",
        json={"projectRoot": "/tmp/project", "testCaseText": "Given login"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "compose_autotest"
    assert payload["project_root"] == "/tmp/project"
    assert payload["quality_policy"] == "strict"


def test_compose_autotest_tool_endpoint_accepts_quality_policy() -> None:
    app = _build_app()
    client = TestClient(app)
    response = client.post(
        "/tools/compose-autotest",
        json={
            "projectRoot": "/tmp/project",
            "testCaseText": "Given login",
            "qualityPolicy": "balanced",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "compose_autotest"
    assert payload["quality_policy"] == "balanced"

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes_memory import router as memory_router
from memory import MemoryRepository, MemoryService


def _build_app(tmp_path) -> FastAPI:
    app = FastAPI()
    memory_service = MemoryService(MemoryRepository(tmp_path / "learning"))
    app.state.orchestrator = SimpleNamespace(project_learning_store=memory_service)
    app.include_router(memory_router)
    return app


def test_rules_crud_and_preview(tmp_path) -> None:
    app = _build_app(tmp_path)
    client = TestClient(app)

    created_template = client.post(
        "/memory/templates",
        json={
            "projectRoot": "/tmp/project",
            "name": "auth template",
            "triggerRegex": "authoriz",
            "steps": ["Given user is authorized", "When user opens draft"],
        },
    )
    assert created_template.status_code == 200
    template_id = created_template.json()["id"]

    created_rule = client.post(
        "/memory/rules",
        json={
            "projectRoot": "/tmp/project",
            "name": "auth rule",
            "condition": {"textRegex": "authoriz"},
            "actions": {"qualityPolicy": "balanced", "applyTemplates": [template_id]},
        },
    )
    assert created_rule.status_code == 200
    rule_id = created_rule.json()["id"]

    listed = client.get("/memory/rules", params={"projectRoot": "/tmp/project"})
    assert listed.status_code == 200
    assert any(item["id"] == rule_id for item in listed.json()["items"])

    preview = client.post(
        "/memory/resolve-preview",
        json={
            "projectRoot": "/tmp/project",
            "text": "need authorize in system",
            "qualityPolicy": "strict",
        },
    )
    assert preview.status_code == 200
    payload = preview.json()
    assert payload["qualityPolicy"] == "balanced"
    assert payload["appliedRuleIds"] == [rule_id]
    assert payload["appliedTemplateIds"] == [template_id]
    assert payload["templateSteps"]


def test_templates_delete(tmp_path) -> None:
    app = _build_app(tmp_path)
    client = TestClient(app)
    created = client.post(
        "/memory/templates",
        json={
            "projectRoot": "/tmp/project",
            "name": "draft",
            "steps": ["When user opens draft"],
        },
    )
    template_id = created.json()["id"]

    deleted = client.delete(f"/memory/templates/{template_id}", params={"projectRoot": "/tmp/project"})
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    listed = client.get("/memory/templates", params={"projectRoot": "/tmp/project"})
    assert listed.status_code == 200
    assert listed.json()["items"] == []

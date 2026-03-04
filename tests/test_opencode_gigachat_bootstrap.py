from __future__ import annotations

import httpx

from opencode_adapter_app.config import AdapterSettings
from opencode_adapter_app.gigachat_auth import fetch_access_token
from opencode_adapter_app.headless_server import OpenCodeHeadlessServer


def test_fetch_access_token_uses_configured_gigachat_oauth(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_post(url: str, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"access_token": "token-123"}, request=request)

    monkeypatch.setattr(httpx, "post", _fake_post)
    settings = AdapterSettings(
        **{
            "GIGACHAT_CLIENT_ID": "client-id",
            "GIGACHAT_CLIENT_SECRET": "client-secret",
            "GIGACHAT_SCOPE": "GIGACHAT_API_PERS",
            "GIGACHAT_AUTH_URL": "https://gigachat.example/oauth",
            "GIGACHAT_VERIFY_SSL": False,
        }
    )

    token = fetch_access_token(settings)

    assert token == "token-123"
    assert captured["url"] == "https://gigachat.example/oauth"
    kwargs = captured["kwargs"]
    assert kwargs["data"] == {"scope": "GIGACHAT_API_PERS"}
    assert kwargs["verify"] is False
    assert "Authorization" in kwargs["headers"]
    assert kwargs["headers"]["Content-Type"] == "application/x-www-form-urlencoded"


def test_headless_server_bootstraps_token_into_child_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "opencode_adapter_app.headless_server.fetch_access_token",
        lambda _settings: "bootstrap-token",
    )
    settings = AdapterSettings(
        work_root=tmp_path / "adapter-work",
        **{
            "GIGACHAT_CLIENT_ID": "client-id",
            "GIGACHAT_CLIENT_SECRET": "client-secret",
            "GIGACHAT_API_URL": "https://gigachat.example/api/v1",
        }
    )
    server = OpenCodeHeadlessServer(settings=settings)
    env = settings.build_child_env()
    env.pop("GIGACHAT_ACCESS_TOKEN", None)
    env.pop("GIGACHAT_API_URL", None)

    server._inject_gigachat_access_token(env)

    assert env["GIGACHAT_ACCESS_TOKEN"] == "bootstrap-token"
    assert env["GIGACHAT_API_URL"] == "https://gigachat.example/api/v1"


def test_build_child_env_uses_project_opencode_json_as_opencode_config(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "opencode.json").write_text('{"$schema":"https://opencode.ai/config.json"}', encoding="utf-8")
    settings = AdapterSettings(work_root=tmp_path / "adapter-work")

    env = settings.build_child_env(project_root=project_root)

    assert env["OPENCODE_CONFIG"] == str(project_root / "opencode.json")
    assert "OPENCODE_CONFIG_DIR" not in env
    assert env["XDG_DATA_HOME"].endswith("adapter-work\\xdg\\data")
    assert env["XDG_STATE_HOME"].endswith("adapter-work\\xdg\\state")
    assert env["XDG_CACHE_HOME"].endswith("adapter-work\\xdg\\cache")
    assert env["XDG_CONFIG_HOME"].endswith("adapter-work\\xdg\\config")


def test_build_child_env_uses_project_dot_opencode_dir_when_present(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    dot_opencode = project_root / ".opencode"
    dot_opencode.mkdir()
    settings = AdapterSettings(work_root=tmp_path / "adapter-work")

    env = settings.build_child_env(project_root=project_root)

    assert env["OPENCODE_CONFIG_DIR"] == str(dot_opencode)


def test_debug_snapshot_redacts_sensitive_raw_config(monkeypatch, tmp_path) -> None:
    settings = AdapterSettings(work_root=tmp_path / "adapter-work")
    server = OpenCodeHeadlessServer(settings=settings)
    server._process = type("FakeProcess", (), {"poll": lambda self: None})()
    server._active_project_root = str(tmp_path / "project")
    server._active_config_file = str(tmp_path / "project" / "opencode.json")
    monkeypatch.setattr(server, "_is_ready", lambda: True)

    def _fake_get(_url: str, timeout: float = 2.0):
        request = httpx.Request("GET", _url)
        return httpx.Response(
            200,
            json={
                "model": "gigachat/GigaChat-Max",
                "provider": {
                    "gigachat": {
                        "options": {
                            "apiKey": "secret-token-value",
                            "baseURL": "https://gigachat.example/api/v1",
                        }
                    }
                },
            },
            request=request,
        )

    monkeypatch.setattr(httpx, "get", _fake_get)

    snapshot = server.debug_snapshot()

    assert snapshot["resolved_model"] == "gigachat/GigaChat-Max"
    assert snapshot["raw_config"]["provider"]["gigachat"]["options"]["apiKey"] == "***"
    assert snapshot["raw_config"]["provider"]["gigachat"]["options"]["baseURL"] == "https://gigachat.example/api/v1"

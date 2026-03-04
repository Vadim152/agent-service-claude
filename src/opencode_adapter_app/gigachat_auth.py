from __future__ import annotations

from base64 import b64encode
import uuid

import httpx

from opencode_adapter_app.config import AdapterSettings


class GigaChatAuthError(RuntimeError):
    pass


def fetch_access_token(settings: AdapterSettings) -> str:
    client_id = str(settings.gigachat_client_id or "").strip()
    client_secret = str(settings.gigachat_client_secret or "").strip()
    if not client_id or not client_secret:
        raise GigaChatAuthError("GigaChat client credentials are not configured")
    credentials = b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("utf-8")
    headers = {
        "Authorization": f"Basic {credentials}",
        "RqUID": str(uuid.uuid4()),
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    try:
        response = httpx.post(
            settings.gigachat_auth_url,
            headers=headers,
            data={"scope": settings.gigachat_scope},
            timeout=30.0,
            verify=settings.gigachat_verify_ssl,
        )
        response.raise_for_status()
    except Exception as exc:  # pragma: no cover - network failure path
        raise GigaChatAuthError(f"Failed to get GigaChat access token: {exc}") from exc
    try:
        payload = response.json()
    except ValueError as exc:  # pragma: no cover - malformed upstream response
        raise GigaChatAuthError("GigaChat auth endpoint returned non-JSON response") from exc
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise GigaChatAuthError("GigaChat auth endpoint did not return access_token")
    return token

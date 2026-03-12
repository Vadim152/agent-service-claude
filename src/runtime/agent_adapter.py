"""HTTP client for the ClaudeCode wrapper API."""
from __future__ import annotations

from typing import Any

import httpx


class ClaudeCodeAdapterError(RuntimeError):
    """Raised when the Claude Code adapter call fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retryable = retryable
        self.details = details or {}
        self.request_id = request_id


class HttpClaudeCodeAdapterClient:
    def __init__(self, *, base_url: str, timeout_s: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        try:
            response = httpx.request(
                method,
                url,
                json=json_payload,
                params=params,
                timeout=self._timeout_s,
            )
        except Exception as exc:
            raise ClaudeCodeAdapterError(
                f"Claude Code adapter request failed: {exc}",
                status_code=503,
                code="backend_unavailable",
                retryable=True,
            ) from exc

        request_id = str(response.headers.get("X-Request-Id") or "").strip() or None
        try:
            data = response.json() if response.content else {}
        except Exception:
            data = {}

        if response.is_success:
            if not isinstance(data, dict):
                raise ClaudeCodeAdapterError(
                    "Claude Code adapter returned non-object response",
                    status_code=502,
                    code="backend_unavailable",
                    retryable=False,
                    request_id=request_id,
                )
            return data

        error_payload = data.get("error") if isinstance(data, dict) else None
        if isinstance(error_payload, dict):
            raise ClaudeCodeAdapterError(
                str(error_payload.get("message") or f"Claude Code adapter request failed: HTTP {response.status_code}"),
                status_code=response.status_code,
                code=str(error_payload.get("code") or ""),
                retryable=bool(error_payload.get("retryable", False)),
                details=dict(error_payload.get("details") or {}),
                request_id=str(error_payload.get("requestId") or request_id or "").strip() or None,
            )

        raise ClaudeCodeAdapterError(
            response.text or f"Claude Code adapter request failed: HTTP {response.status_code}",
            status_code=response.status_code,
            code="backend_unavailable" if response.status_code >= 500 else None,
            retryable=response.status_code >= 500,
            request_id=request_id,
        )

    def create_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/runs", json_payload=payload)

    def get_run(self, backend_run_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/runs/{backend_run_id}")

    def list_events(self, backend_run_id: str, *, after: int | str | None, limit: int | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if after not in {None, ""}:
            params["after"] = after
        if limit is not None:
            params["limit"] = limit
        return self._request("GET", f"/v1/runs/{backend_run_id}/events", params=params or None)

    def cancel_run(self, backend_run_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/runs/{backend_run_id}/cancel", json_payload={})

    def submit_approval_decision(self, backend_run_id: str, approval_id: str, decision: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/runs/{backend_run_id}/approvals/{approval_id}",
            json_payload={"decision": decision},
        )

    def ensure_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/sessions", json_payload=payload)

    def get_session(self, external_session_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/sessions/{external_session_id}")

    def compact_session(self, external_session_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/sessions/{external_session_id}/compact", json_payload={})

    def get_session_diff(self, external_session_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/sessions/{external_session_id}/diff")

    def execute_session_command(self, external_session_id: str, command: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/sessions/{external_session_id}/commands",
            json_payload={"command": command},
        )


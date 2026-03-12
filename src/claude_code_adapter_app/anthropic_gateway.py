from __future__ import annotations

import json
import secrets
from threading import RLock
from typing import Any

from gigachat import GigaChat

from claude_code_adapter_app.config import AdapterSettings
from claude_code_adapter_app.errors import AdapterApiError
from claude_code_adapter_app.gigachat_auth import GigaChatAuthError, fetch_access_token


class AnthropicGatewayService:
    def __init__(self, *, settings: AdapterSettings) -> None:
        self._settings = settings
        self._lock = RLock()
        self._cached_access_token: str | None = None

    def health_snapshot(self, *, force_refresh: bool = False) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        gigachat_auth_ready = False
        token_source: str | None = None
        try:
            token, token_source = self._resolve_access_token(force_refresh=force_refresh)
            gigachat_auth_ready = bool(token)
        except AdapterApiError as exc:
            issues.append(
                {
                    "code": exc.code,
                    "message": exc.message,
                    "details": dict(exc.details),
                }
            )
        return {
            "ready": not issues,
            "gatewayReady": True,
            "gatewayBaseUrl": self._settings.gateway_base_url,
            "gigachatAuthReady": gigachat_auth_ready,
            "tokenSource": token_source,
            "resolvedModel": self._resolve_model(None),
            "issues": issues,
        }

    def authorize(self, *, authorization: str | None, x_api_key: str | None) -> None:
        expected = self._settings.gateway_token
        if x_api_key and x_api_key == expected:
            return
        if authorization:
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() == "bearer" and token == expected:
                return
        raise AdapterApiError(
            "gateway_unauthorized",
            "Internal gateway authorization failed.",
            status_code=401,
        )

    def list_models(self) -> dict[str, Any]:
        model = self._resolve_model(None)
        return {
            "data": [
                {
                    "id": model,
                    "type": "model",
                    "display_name": model.split("/", 1)[-1],
                    "provider": model.split("/", 1)[0] if "/" in model else "gigachat",
                }
            ]
        }

    def count_tokens(self, payload: dict[str, Any]) -> dict[str, Any]:
        model = self._resolve_model(payload.get("model"))
        gigachat_model = _gigachat_model_name(model)
        inputs = _collect_countable_text(payload)
        if not inputs:
            return {"input_tokens": 0}
        try:
            counts = self._with_client(model=gigachat_model, callback=lambda client: client.tokens_count(inputs, model=gigachat_model))
        except Exception as exc:  # pragma: no cover - upstream/network path
            raise AdapterApiError(
                "gigachat_count_tokens_failed",
                f"Failed to count GigaChat tokens: {exc}",
                status_code=503,
                retryable=True,
            ) from exc
        total = 0
        for item in counts:
            total += int(getattr(item, "tokens", 0) or 0)
        return {"input_tokens": total}

    def create_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        model = self._resolve_model(payload.get("model"))
        gigachat_model = _gigachat_model_name(model)
        chat_payload = _anthropic_to_gigachat(payload, model=gigachat_model)
        try:
            completion = self._with_client(model=gigachat_model, callback=lambda client: client.chat(chat_payload))
        except Exception as exc:  # pragma: no cover - upstream/network path
            raise AdapterApiError(
                "gigachat_chat_failed",
                f"Failed to call GigaChat: {exc}",
                status_code=503,
                retryable=True,
            ) from exc
        return _gigachat_to_anthropic(completion, public_model=model)

    def _with_client(self, *, model: str, callback: Any) -> Any:
        try:
            token, _ = self._resolve_access_token(force_refresh=False)
            client = self._build_client(model=model, token=token)
            return callback(client)
        except Exception as exc:
            if not _looks_like_expired_token_error(exc):
                raise
        token, _ = self._resolve_access_token(force_refresh=True)
        client = self._build_client(model=model, token=token)
        return callback(client)

    def _build_client(self, *, model: str, token: str) -> GigaChat:
        return GigaChat(
            base_url=self._settings.gigachat_api_url,
            auth_url=self._settings.gigachat_auth_url,
            access_token=token,
            model=model,
            verify_ssl_certs=self._settings.gigachat_verify_ssl,
            timeout=60.0,
        )

    def _resolve_access_token(self, *, force_refresh: bool) -> tuple[str, str]:
        explicit = str(self._settings.gigachat_access_token or "").strip()
        if explicit:
            return explicit, "env"
        if not (self._settings.gigachat_client_id and self._settings.gigachat_client_secret):
            raise AdapterApiError(
                "gigachat_auth_missing",
                "GigaChat credentials are not configured.",
                status_code=503,
                retryable=False,
            )
        with self._lock:
            if self._cached_access_token and not force_refresh:
                return self._cached_access_token, "oauth"
            try:
                self._cached_access_token = fetch_access_token(self._settings)
            except GigaChatAuthError as exc:
                raise AdapterApiError(
                    "gigachat_auth_failed",
                    str(exc),
                    status_code=503,
                    retryable=True,
                ) from exc
            return self._cached_access_token, "oauth"

    def _resolve_model(self, requested: Any) -> str:
        forced = str(self._settings.resolve_forced_model() or "").strip()
        if forced:
            return forced
        requested_model = str(requested or "").strip()
        if requested_model:
            return requested_model
        return "gigachat/GigaChat-2"


def _anthropic_to_gigachat(payload: dict[str, Any], *, model: str) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    tool_name_by_id: dict[str, str] = {}

    system_text = _extract_text_blocks(payload.get("system"))
    if system_text:
        messages.append({"role": "system", "content": system_text})

    for raw_message in payload.get("messages") or []:
        if not isinstance(raw_message, dict):
            continue
        role = str(raw_message.get("role") or "").strip().lower()
        content = raw_message.get("content")
        if not isinstance(content, list):
            content = [{"type": "text", "text": str(content or "")}]
        pending_text: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "").strip().lower()
            if block_type == "text":
                text = str(block.get("text") or "").strip()
                if text:
                    pending_text.append(text)
                continue
            if block_type == "tool_use" and role == "assistant":
                if pending_text:
                    messages.append({"role": "assistant", "content": "\n\n".join(pending_text)})
                    pending_text = []
                tool_id = str(block.get("id") or "").strip()
                tool_name = str(block.get("name") or "tool").strip()
                if tool_id:
                    tool_name_by_id[tool_id] = tool_name
                arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
                messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "function_call": {"name": tool_name, "arguments": arguments},
                    }
                )
                continue
            if block_type == "tool_result" and role == "user":
                if pending_text:
                    messages.append({"role": "user", "content": "\n\n".join(pending_text)})
                    pending_text = []
                tool_use_id = str(block.get("tool_use_id") or "").strip()
                tool_name = tool_name_by_id.get(tool_use_id, "tool")
                messages.append(
                    {
                        "role": "function",
                        "content": _tool_result_text(block=block, tool_name=tool_name),
                    }
                )
                continue
        if pending_text:
            normalized_role = role if role in {"user", "assistant", "system"} else "user"
            messages.append({"role": normalized_role, "content": "\n\n".join(pending_text)})

    functions = _anthropic_tools_to_gigachat_functions(payload.get("tools"))
    chat_payload: dict[str, Any] = {"model": model, "messages": messages}
    if functions:
        chat_payload["functions"] = functions
        chat_payload["function_call"] = "auto"
    max_tokens = payload.get("max_tokens")
    if max_tokens is not None:
        try:
            chat_payload["max_tokens"] = int(max_tokens)
        except (TypeError, ValueError):
            pass
    return chat_payload


def _gigachat_to_anthropic(completion: Any, *, public_model: str | None = None) -> dict[str, Any]:
    choices = list(getattr(completion, "choices", []) or [])
    if not choices:
        raise AdapterApiError(
            "gigachat_empty_response",
            "GigaChat returned an empty completion.",
            status_code=503,
            retryable=True,
        )
    choice = choices[0]
    message = getattr(choice, "message", None)
    if message is None:
        raise AdapterApiError(
            "gigachat_empty_message",
            "GigaChat did not return a message payload.",
            status_code=503,
            retryable=True,
        )
    content: list[dict[str, Any]]
    finish_reason = str(getattr(choice, "finish_reason", "") or "").strip().lower()
    function_call = getattr(message, "function_call", None)
    if function_call is not None:
        content = [
            {
                "type": "tool_use",
                "id": f"toolu_{secrets.token_hex(8)}",
                "name": str(getattr(function_call, "name", "") or "tool"),
                "input": dict(getattr(function_call, "arguments", {}) or {}),
            }
        ]
        stop_reason = "tool_use"
    else:
        content = [{"type": "text", "text": str(getattr(message, "content", "") or "")}]
        stop_reason = "max_tokens" if finish_reason == "length" else "end_turn"
    usage = getattr(completion, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    response_model = str(public_model or getattr(completion, "model", "") or "")
    return {
        "id": f"msg_{secrets.token_hex(8)}",
        "type": "message",
        "role": "assistant",
        "model": response_model,
        "content": content,
        "stop_reason": stop_reason,
        "usage": {
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
        },
    }


def _anthropic_tools_to_gigachat_functions(raw_tools: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_tools, list):
        return []
    functions: list[dict[str, Any]] = []
    for raw_tool in raw_tools:
        if not isinstance(raw_tool, dict):
            continue
        name = str(raw_tool.get("name") or "").strip()
        if not name:
            continue
        schema = raw_tool.get("input_schema") if isinstance(raw_tool.get("input_schema"), dict) else {}
        parameters = {"type": str(schema.get("type") or "object")}
        properties = schema.get("properties")
        if isinstance(properties, dict):
            parameters["properties"] = properties
        required = schema.get("required")
        if isinstance(required, list):
            parameters["required"] = [str(item) for item in required]
        functions.append(
            {
                "name": name,
                "description": str(raw_tool.get("description") or "").strip() or None,
                "parameters": parameters,
            }
        )
    return functions


def _tool_result_text(*, block: dict[str, Any], tool_name: str) -> str:
    content = block.get("content")
    if isinstance(content, list):
        text = _extract_text_blocks(content)
    elif isinstance(content, str):
        text = content
    else:
        text = json.dumps(content, ensure_ascii=False) if content is not None else ""
    text = text.strip()
    if bool(block.get("is_error")):
        return f"{tool_name} error: {text or 'Tool failed.'}"
    return f"{tool_name} result: {text or 'Tool completed successfully.'}"


def _collect_countable_text(payload: dict[str, Any]) -> list[str]:
    values: list[str] = []
    system_text = _extract_text_blocks(payload.get("system"))
    if system_text:
        values.append(system_text)
    for message in payload.get("messages") or []:
        if not isinstance(message, dict):
            continue
        text = _extract_text_blocks(message.get("content"))
        if text:
            values.append(text)
    return values


def _extract_text_blocks(raw_blocks: Any) -> str:
    if isinstance(raw_blocks, str):
        return raw_blocks.strip()
    if not isinstance(raw_blocks, list):
        return ""
    parts: list[str] = []
    for block in raw_blocks:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "").strip().lower()
        if block_type == "text":
            text = str(block.get("text") or "").strip()
            if text:
                parts.append(text)
            continue
        if block_type == "tool_result":
            content = block.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
            elif isinstance(content, list):
                nested = _extract_text_blocks(content)
                if nested:
                    parts.append(nested)
            continue
        if block_type == "tool_use":
            try:
                parts.append(json.dumps(block.get("input") or {}, ensure_ascii=False))
            except TypeError:
                parts.append("{}")
    return "\n\n".join(part for part in parts if part).strip()


def _looks_like_expired_token_error(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = ("token has expired", "401", "unauthorized")
    return any(marker in text for marker in markers)


def _gigachat_model_name(value: str) -> str:
    raw = str(value or "").strip()
    if raw.lower().startswith("gigachat/"):
        return raw.split("/", 1)[1]
    return raw

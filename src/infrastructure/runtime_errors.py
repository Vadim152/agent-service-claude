"""Shared runtime-level exceptions."""
from __future__ import annotations

from typing import Any


class ChatRuntimeError(RuntimeError):
    """Raised by chat runtime when request-level processing fails."""

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

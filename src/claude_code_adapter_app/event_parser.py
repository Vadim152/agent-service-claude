from __future__ import annotations

import json
from typing import Any


TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def parse_json_line(raw_line: str) -> dict[str, Any] | None:
    line = raw_line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def normalize_status(value: Any) -> str:
    raw = str(value or "running").strip().lower()
    mapping = {
        "queued": "queued",
        "pending": "queued",
        "running": "running",
        "in_progress": "running",
        "progress": "running",
        "busy": "running",
        "awaiting_approval": "running",
        "waiting_approval": "running",
        "paused": "running",
        "succeeded": "succeeded",
        "completed": "succeeded",
        "finished": "succeeded",
        "failed": "failed",
        "error": "failed",
        "cancelled": "cancelled",
        "canceled": "cancelled",
    }
    return mapping.get(raw, "running")


def classify_event(payload: dict[str, Any]) -> str:
    raw_type = str(payload.get("eventType") or payload.get("type") or "").strip().lower()
    status = normalize_status(payload.get("status"))
    if "approval" in raw_type or payload.get("pendingApproval") or payload.get("approval"):
        return "run.awaiting_approval"
    if "artifact" in raw_type or payload.get("artifact") or payload.get("artifacts"):
        return "run.artifact_published"
    if raw_type in {"run.started", "started"}:
        return "run.started"
    if raw_type in {"run.finished", "finished", "run.succeeded"} or status == "succeeded":
        return "run.succeeded"
    if raw_type in {"run.cancelled", "cancelled"} or status == "cancelled":
        return "run.cancelled"
    if raw_type in {"run.failed", "failed", "error"} or status == "failed":
        return "run.failed"
    return "run.progress"


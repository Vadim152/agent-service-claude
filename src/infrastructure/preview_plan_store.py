"""File-backed storage for generation preview plans."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any


class PreviewPlanStore:
    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir).expanduser().resolve()
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def create_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        plan_id = str(payload.get("planId") or "").strip() or str(uuid.uuid4())
        stored = dict(payload)
        stored["planId"] = plan_id
        self._path(plan_id).write_text(
            json.dumps(stored, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return stored

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        path = self._path(plan_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def update_plan(self, plan_id: str, **changes: Any) -> dict[str, Any] | None:
        payload = self.get_plan(plan_id)
        if payload is None:
            return None
        payload.update({key: value for key, value in changes.items() if value is not None})
        return self.create_plan(payload)

    def delete_plan(self, plan_id: str) -> None:
        path = self._path(plan_id)
        if path.exists():
            path.unlink()

    def _path(self, plan_id: str) -> Path:
        return self._base_dir / f"{plan_id}.json"


__all__ = ["PreviewPlanStore"]

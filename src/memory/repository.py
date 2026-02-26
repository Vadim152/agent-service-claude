from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso8601(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _project_key(project_root: str) -> str:
    normalized = Path(project_root).expanduser().resolve().as_posix().lower()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


class MemoryRepository:
    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, project_root: str) -> Path:
        return self._base_dir / f"{_project_key(project_root)}.json"

    def load(self, project_root: str) -> dict[str, Any]:
        path = self._path(project_root)
        if not path.exists():
            return self._default_payload(project_root)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        defaults = self._default_payload(project_root)
        for key, value in defaults.items():
            payload.setdefault(key, value)
        return payload

    def save(self, project_root: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        data["projectRoot"] = project_root
        data["updatedAt"] = _utcnow()
        path = self._path(project_root)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    def patch(self, project_root: str, **changes: Any) -> dict[str, Any]:
        payload = self.load(project_root)
        for key, value in changes.items():
            if value is None:
                continue
            payload[key] = value
        return self.save(project_root, payload)

    @staticmethod
    def _default_payload(project_root: str) -> dict[str, Any]:
        return {
            "projectRoot": project_root,
            "updatedAt": None,
            "stepBoosts": {},
            "feedback": [],
            "preferences": {},
            "generationRules": [],
            "stepTemplates": [],
        }


__all__ = ["MemoryRepository", "_parse_iso8601"]

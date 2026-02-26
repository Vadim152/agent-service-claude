"""Persistent memory abstraction for chat sessions and project-level context."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_project_root(project_root: str) -> str:
    return Path(project_root).expanduser().resolve().as_posix().lower()


def _project_key(project_root: str) -> str:
    normalized = _normalize_project_root(project_root)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


class ChatMemoryStore:
    """File-backed storage for session snapshots and project memory."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._sessions_dir = self._base_dir / "sessions"
        self._projects_dir = self._base_dir / "projects"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._projects_dir.mkdir(parents=True, exist_ok=True)

    def load_sessions(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for path in self._sessions_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and payload.get("session_id"):
                result.append(payload)
        return result

    def save_session(self, session: dict[str, Any]) -> None:
        session_id = str(session.get("session_id", "")).strip()
        if not session_id:
            return
        payload = dict(session)
        payload["persisted_at"] = _utcnow()
        target = self._sessions_dir / f"{session_id}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def delete_session(self, session_id: str) -> None:
        sid = str(session_id).strip()
        if not sid:
            return
        target = self._sessions_dir / f"{sid}.json"
        try:
            target.unlink(missing_ok=True)
        except OSError:
            return

    def load_project_memory(self, project_root: str) -> dict[str, Any]:
        key = _project_key(project_root)
        path = self._projects_dir / f"{key}.json"
        if not path.exists():
            return {
                "projectRoot": project_root,
                "key": key,
                "updatedAt": None,
                "goals": [],
                "preferences": {},
                "generationRules": [],
                "stepTemplates": [],
                "recentArtifacts": [],
                "summary": None,
            }
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("projectRoot", project_root)
        payload.setdefault("key", key)
        payload.setdefault("goals", [])
        payload.setdefault("preferences", {})
        payload.setdefault("generationRules", [])
        payload.setdefault("stepTemplates", [])
        payload.setdefault("recentArtifacts", [])
        payload.setdefault("summary", None)
        payload.setdefault("updatedAt", None)
        return payload

    def patch_project_memory(self, project_root: str, **changes: Any) -> dict[str, Any]:
        payload = self.load_project_memory(project_root)
        for key, value in changes.items():
            if value is None:
                continue
            payload[key] = value
        payload["updatedAt"] = _utcnow()
        key = _project_key(project_root)
        path = self._projects_dir / f"{key}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

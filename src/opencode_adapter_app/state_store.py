from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return utcnow().isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: str | bytes | None, *, default: Any) -> Any:
    if not value:
        return deepcopy(default)
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return deepcopy(default)


class OpenCodeAdapterStateStore:
    def __init__(
        self,
        *,
        max_events_per_run: int = 5_000,
        backend: str = "sqlite",
        sqlite_path: str | Path | None = None,
        session_retention_hours: int = 720,
    ) -> None:
        self._lock = RLock()
        self._max_events_per_run = max(1, max_events_per_run)
        self._backend = str(backend or "sqlite").strip().lower()
        self._session_retention_hours = max(1, int(session_retention_hours))
        self._sqlite_path = None if self._backend == "memory" else Path(str(sqlite_path)).resolve()
        if self._sqlite_path is not None:
            self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            ":memory:" if self._backend == "memory" else str(self._sqlite_path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._init_schema()
        self._prune_expired_state_locked()

    @classmethod
    def from_settings(cls, settings: Any) -> "OpenCodeAdapterStateStore":
        return cls(
            max_events_per_run=int(settings.max_events_per_run),
            backend=str(settings.state_backend),
            sqlite_path=settings.resolved_state_file,
            session_retention_hours=int(settings.session_retention_hours),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            stored = deepcopy(payload)
            self._write_run_locked(stored)
            self._sync_mapping_from_run_locked(stored)
            return self._hydrate_run_locked(stored)

    def get_run(self, backend_run_id: str) -> dict[str, Any] | None:
        with self._lock:
            payload = self._get_run_payload_locked(backend_run_id)
            if payload is None:
                return None
            return self._hydrate_run_locked(payload)

    def patch_run(self, backend_run_id: str, **changes: Any) -> dict[str, Any] | None:
        with self._lock:
            payload = self._get_run_payload_locked(backend_run_id)
            if payload is None:
                return None
            payload.update(changes)
            payload["updated_at"] = _utcnow_iso()
            self._write_run_locked(payload)
            self._sync_mapping_from_run_locked(payload)
            return self._hydrate_run_locked(payload)

    def append_event(self, backend_run_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            index = self._next_event_index_locked(backend_run_id)
            created_at = _utcnow_iso()
            self._conn.execute(
                """
                INSERT INTO run_events (backend_run_id, idx, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (backend_run_id, index, event_type, _json_dumps(payload), created_at),
            )
            floor_to_keep = max(0, index - self._max_events_per_run + 1)
            self._conn.execute(
                "DELETE FROM run_events WHERE backend_run_id = ? AND idx < ?",
                (backend_run_id, floor_to_keep),
            )
            self._conn.commit()
            return {
                "event_type": event_type,
                "payload": deepcopy(payload),
                "created_at": created_at,
                "index": index,
            }

    def list_events(
        self,
        backend_run_id: str,
        *,
        after: int = 0,
        limit: int | None = None,
    ) -> tuple[list[dict[str, Any]], int, bool, int, bool]:
        with self._lock:
            bounded_limit = max(1, min(int(limit or self._max_events_per_run), self._max_events_per_run))
            next_cursor = self._next_event_index_locked(backend_run_id)
            row = self._conn.execute(
                "SELECT MIN(idx) AS oldest_idx FROM run_events WHERE backend_run_id = ?",
                (backend_run_id,),
            ).fetchone()
            oldest_cursor = int(row["oldest_idx"]) if row and row["oldest_idx"] is not None else next_cursor
            floor = max(0, int(after))
            stale = floor < oldest_cursor and floor < next_cursor
            if stale:
                return [], next_cursor, False, oldest_cursor, True
            rows = self._conn.execute(
                """
                SELECT idx, event_type, payload_json, created_at
                FROM run_events
                WHERE backend_run_id = ? AND idx >= ?
                ORDER BY idx ASC
                LIMIT ?
                """,
                (backend_run_id, floor, bounded_limit + 1),
            ).fetchall()
            has_more = len(rows) > bounded_limit
            materialized = rows[:bounded_limit]
            items = [
                {
                    "event_type": str(item["event_type"]),
                    "payload": _json_loads(item["payload_json"], default={}),
                    "created_at": str(item["created_at"]),
                    "index": int(item["idx"]),
                }
                for item in materialized
            ]
            if items:
                response_cursor = int(items[-1]["index"]) + 1
            else:
                response_cursor = next_cursor
            return items, response_cursor, has_more, oldest_cursor, False

    def record_pending_approvals(self, backend_run_id: str, approvals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._lock:
            stored: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            for raw in approvals:
                normalized = _normalize_approval(raw)
                approval_id = str(normalized.get("approvalId") or "").strip()
                if not approval_id or approval_id in seen_ids:
                    continue
                seen_ids.add(approval_id)
                self._conn.execute(
                    """
                    INSERT INTO approvals (backend_run_id, approval_id, status, payload_json, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(backend_run_id, approval_id) DO UPDATE SET
                        status = excluded.status,
                        payload_json = excluded.payload_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        backend_run_id,
                        approval_id,
                        "pending",
                        _json_dumps(normalized),
                        _utcnow_iso(),
                    ),
                )
                stored.append(normalized)
            self._conn.commit()
            return stored

    def resolve_approval(self, backend_run_id: str, approval_id: str, decision: str) -> dict[str, Any] | None:
        with self._lock:
            current = self.get_approval(backend_run_id, approval_id)
            if current is None:
                return None
            status_value = "approved" if decision == "approve" else "denied"
            payload = dict(current)
            payload["status"] = status_value
            self._conn.execute(
                """
                UPDATE approvals
                SET status = ?, payload_json = ?, updated_at = ?
                WHERE backend_run_id = ? AND approval_id = ?
                """,
                (
                    status_value,
                    _json_dumps(payload),
                    _utcnow_iso(),
                    backend_run_id,
                    approval_id,
                ),
            )
            self._conn.commit()
            return payload

    def get_approval(self, backend_run_id: str, approval_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT payload_json, status, updated_at
                FROM approvals
                WHERE backend_run_id = ? AND approval_id = ?
                """,
                (backend_run_id, approval_id),
            ).fetchone()
            if row is None:
                return None
            payload = _json_loads(row["payload_json"], default={})
            if not isinstance(payload, dict):
                payload = {}
            payload["status"] = str(row["status"])
            payload["updatedAt"] = str(row["updated_at"])
            return payload

    def list_approvals(self, backend_run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT payload_json, status, updated_at
                FROM approvals
                WHERE backend_run_id = ?
                ORDER BY updated_at ASC, approval_id ASC
                """,
                (backend_run_id,),
            ).fetchall()
            items: list[dict[str, Any]] = []
            for row in rows:
                payload = _json_loads(row["payload_json"], default={})
                if not isinstance(payload, dict):
                    payload = {}
                payload["status"] = str(row["status"])
                payload["updatedAt"] = str(row["updated_at"])
                items.append(payload)
            return items

    def list_pending_approvals(self, backend_run_id: str) -> list[dict[str, Any]]:
        return [item for item in self.list_approvals(backend_run_id) if str(item.get("status") or "") == "pending"]

    def set_session_mapping(
        self,
        *,
        external_session_id: str,
        backend_session_id: str,
        project_root: str,
        last_backend_run_id: str,
    ) -> None:
        self.upsert_session_mapping(
            external_session_id,
            backend_session_id=backend_session_id,
            project_root=project_root,
            last_backend_run_id=last_backend_run_id,
        )

    def upsert_session_mapping(self, external_session_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            current = self._get_session_mapping_locked(external_session_id) or {
                "external_session_id": external_session_id,
                "backend_session_id": None,
                "project_root": None,
                "last_backend_run_id": None,
                "status": "idle",
                "current_action": "Idle",
                "last_activity_at": _utcnow_iso(),
                "last_compaction_at": None,
                "updated_at": _utcnow_iso(),
                "last_provider_id": None,
                "last_model_id": None,
            }
            current.update({key: value for key, value in changes.items() if value is not None})
            current["updated_at"] = _utcnow_iso()
            self._conn.execute(
                """
                INSERT INTO session_mappings (
                    external_session_id,
                    backend_session_id,
                    project_root,
                    last_backend_run_id,
                    status,
                    current_action,
                    last_activity_at,
                    last_compaction_at,
                    updated_at,
                    last_provider_id,
                    last_model_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(external_session_id) DO UPDATE SET
                    backend_session_id = excluded.backend_session_id,
                    project_root = excluded.project_root,
                    last_backend_run_id = excluded.last_backend_run_id,
                    status = excluded.status,
                    current_action = excluded.current_action,
                    last_activity_at = excluded.last_activity_at,
                    last_compaction_at = excluded.last_compaction_at,
                    updated_at = excluded.updated_at,
                    last_provider_id = excluded.last_provider_id,
                    last_model_id = excluded.last_model_id
                """,
                (
                    external_session_id,
                    current.get("backend_session_id"),
                    current.get("project_root"),
                    current.get("last_backend_run_id"),
                    current.get("status"),
                    current.get("current_action"),
                    current.get("last_activity_at"),
                    current.get("last_compaction_at"),
                    current.get("updated_at"),
                    current.get("last_provider_id"),
                    current.get("last_model_id"),
                ),
            )
            self._conn.commit()
            return self._get_session_mapping_locked(external_session_id) or current

    def get_session_mapping(self, external_session_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._get_session_mapping_locked(external_session_id)

    def ensure_session_diff(self, *, external_session_id: str, backend_session_id: str | None) -> dict[str, Any]:
        existing = self.get_session_diff(external_session_id)
        if existing is not None:
            return existing
        self.set_session_diff(
            external_session_id=external_session_id,
            backend_session_id=backend_session_id,
            summary={"files": 0, "additions": 0, "deletions": 0},
            files=[],
            stale=False,
        )
        return self.get_session_diff(external_session_id) or {}

    def set_session_diff(
        self,
        *,
        external_session_id: str,
        backend_session_id: str | None,
        summary: dict[str, Any],
        files: list[dict[str, Any]],
        stale: bool,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO session_diffs (
                    external_session_id,
                    backend_session_id,
                    summary_json,
                    files_json,
                    stale,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(external_session_id) DO UPDATE SET
                    backend_session_id = excluded.backend_session_id,
                    summary_json = excluded.summary_json,
                    files_json = excluded.files_json,
                    stale = excluded.stale,
                    updated_at = excluded.updated_at
                """,
                (
                    external_session_id,
                    backend_session_id,
                    _json_dumps(summary),
                    _json_dumps(files),
                    1 if stale else 0,
                    _utcnow_iso(),
                ),
            )
            self._conn.commit()

    def get_session_diff(self, external_session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT backend_session_id, summary_json, files_json, stale, updated_at
                FROM session_diffs
                WHERE external_session_id = ?
                """,
                (external_session_id,),
            ).fetchone()
            if row is None:
                return None
            return {
                "external_session_id": external_session_id,
                "backend_session_id": row["backend_session_id"],
                "summary": _json_loads(row["summary_json"], default={"files": 0, "additions": 0, "deletions": 0}),
                "files": _json_loads(row["files_json"], default=[]),
                "stale": bool(row["stale"]),
                "updated_at": str(row["updated_at"]),
            }

    def find_active_run_for_session(self, external_session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT payload_json
                FROM runs
                WHERE external_session_id = ? AND status IN ('queued', 'running')
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (external_session_id,),
            ).fetchone()
            if row is None:
                return None
            payload = _json_loads(row["payload_json"], default={})
            return self._hydrate_run_locked(payload)

    def has_pending_approvals_for_session(self, external_session_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT 1
                FROM approvals a
                JOIN runs r ON r.backend_run_id = a.backend_run_id
                WHERE r.external_session_id = ? AND a.status = 'pending'
                LIMIT 1
                """,
                (external_session_id,),
            ).fetchone()
            return row is not None

    def mark_inflight_runs_failed(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload_json FROM runs WHERE status IN ('queued', 'running')"
            ).fetchall()
            now = _utcnow_iso()
            updated_ids: list[str] = []
            for row in rows:
                payload = _json_loads(row["payload_json"], default={})
                if not isinstance(payload, dict):
                    continue
                backend_run_id = str(payload.get("backend_run_id") or "").strip()
                if not backend_run_id:
                    continue
                payload.update(
                    {
                        "status": "failed",
                        "current_action": "Adapter restarted",
                        "finished_at": now,
                        "exit_code": -1,
                        "output": {
                            "error": {
                                "code": "adapter_restarted",
                                "message": "Adapter restarted while the run was in progress.",
                            }
                        },
                        "result": {
                            "error": {
                                "code": "adapter_restarted",
                                "message": "Adapter restarted while the run was in progress.",
                            }
                        },
                        "updated_at": now,
                    }
                )
                self._write_run_locked(payload)
                self._sync_mapping_from_run_locked(payload)
                self.append_event(
                    backend_run_id,
                    "run.failed",
                    {
                        "backendRunId": backend_run_id,
                        "message": "Adapter restarted while the run was in progress.",
                        "code": "adapter_restarted",
                    },
                )
                updated_ids.append(backend_run_id)
            return updated_ids

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                backend_run_id TEXT PRIMARY KEY,
                external_session_id TEXT,
                backend_session_id TEXT,
                project_root TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS run_events (
                backend_run_id TEXT NOT NULL,
                idx INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (backend_run_id, idx)
            );

            CREATE TABLE IF NOT EXISTS approvals (
                backend_run_id TEXT NOT NULL,
                approval_id TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (backend_run_id, approval_id)
            );

            CREATE TABLE IF NOT EXISTS session_mappings (
                external_session_id TEXT PRIMARY KEY,
                backend_session_id TEXT,
                project_root TEXT,
                last_backend_run_id TEXT,
                status TEXT,
                current_action TEXT,
                last_activity_at TEXT,
                last_compaction_at TEXT,
                updated_at TEXT NOT NULL,
                last_provider_id TEXT,
                last_model_id TEXT
            );

            CREATE TABLE IF NOT EXISTS session_diffs (
                external_session_id TEXT PRIMARY KEY,
                backend_session_id TEXT,
                summary_json TEXT NOT NULL,
                files_json TEXT NOT NULL,
                stale INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_runs_external_session ON runs (external_session_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_run_events_lookup ON run_events (backend_run_id, idx);
            CREATE INDEX IF NOT EXISTS idx_approvals_lookup ON approvals (backend_run_id, status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_session_mappings_updated ON session_mappings (updated_at);
            CREATE INDEX IF NOT EXISTS idx_session_diffs_updated ON session_diffs (updated_at);
            """
        )
        self._conn.commit()

    def _prune_expired_state_locked(self) -> None:
        cutoff = (utcnow() - timedelta(hours=self._session_retention_hours)).isoformat()
        self._conn.execute("DELETE FROM session_mappings WHERE updated_at < ?", (cutoff,))
        self._conn.execute("DELETE FROM session_diffs WHERE updated_at < ?", (cutoff,))
        self._conn.commit()

    def _get_run_payload_locked(self, backend_run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT payload_json FROM runs WHERE backend_run_id = ?",
            (backend_run_id,),
        ).fetchone()
        if row is None:
            return None
        payload = _json_loads(row["payload_json"], default={})
        return payload if isinstance(payload, dict) else None

    def _write_run_locked(self, payload: dict[str, Any]) -> None:
        backend_run_id = str(payload["backend_run_id"])
        self._conn.execute(
            """
            INSERT INTO runs (
                backend_run_id,
                external_session_id,
                backend_session_id,
                project_root,
                status,
                created_at,
                updated_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(backend_run_id) DO UPDATE SET
                external_session_id = excluded.external_session_id,
                backend_session_id = excluded.backend_session_id,
                project_root = excluded.project_root,
                status = excluded.status,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                payload_json = excluded.payload_json
            """,
            (
                backend_run_id,
                payload.get("external_session_id"),
                payload.get("backend_session_id"),
                payload.get("project_root"),
                payload.get("status"),
                payload.get("created_at") or _utcnow_iso(),
                payload.get("updated_at") or _utcnow_iso(),
                _json_dumps(payload),
            ),
        )
        self._conn.commit()

    def _hydrate_run_locked(self, payload: dict[str, Any]) -> dict[str, Any]:
        materialized = deepcopy(payload)
        backend_run_id = str(materialized.get("backend_run_id") or "").strip()
        approvals = self.list_approvals(backend_run_id) if backend_run_id else []
        materialized["approvals"] = approvals
        materialized["pending_approvals"] = [item for item in approvals if str(item.get("status") or "") == "pending"]
        return materialized

    def _next_event_index_locked(self, backend_run_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(idx), -1) + 1 AS next_idx FROM run_events WHERE backend_run_id = ?",
            (backend_run_id,),
        ).fetchone()
        return int(row["next_idx"]) if row is not None else 0

    def _get_session_mapping_locked(self, external_session_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT
                external_session_id,
                backend_session_id,
                project_root,
                last_backend_run_id,
                status,
                current_action,
                last_activity_at,
                last_compaction_at,
                updated_at,
                last_provider_id,
                last_model_id
            FROM session_mappings
            WHERE external_session_id = ?
            """,
            (external_session_id,),
        ).fetchone()
        if row is None:
            return None
        return {key: row[key] for key in row.keys()}

    def _sync_mapping_from_run_locked(self, payload: dict[str, Any]) -> None:
        external_session_id = str(payload.get("external_session_id") or "").strip()
        if not external_session_id:
            return
        self.upsert_session_mapping(
            external_session_id,
            backend_session_id=str(payload.get("backend_session_id") or "").strip() or None,
            project_root=str(payload.get("project_root") or "").strip() or None,
            last_backend_run_id=str(payload.get("backend_run_id") or "").strip() or None,
            status=str(payload.get("status") or "").strip() or None,
            current_action=str(payload.get("current_action") or "").strip() or None,
            last_activity_at=str(payload.get("updated_at") or _utcnow_iso()),
        )


def _normalize_approval(item: dict[str, Any]) -> dict[str, Any]:
    approval_id = str(item.get("approvalId") or item.get("approval_id") or item.get("id") or "").strip()
    tool_name = str(item.get("toolName") or item.get("tool_name") or item.get("tool") or "opencode.tool").strip()
    risk_level = str(item.get("riskLevel") or item.get("risk_level") or "high").strip() or "high"
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return {
        "approvalId": approval_id,
        "approval_id": approval_id,
        "toolName": tool_name,
        "tool_name": tool_name,
        "title": str(item.get("title") or tool_name or "OpenCode approval"),
        "kind": str(item.get("kind") or "tool"),
        "riskLevel": risk_level,
        "risk_level": risk_level,
        "metadata": deepcopy(metadata),
    }

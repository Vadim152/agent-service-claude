"""Persistence for local `.feature` scenario catalog entries."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from domain.enums import ScenarioType
from domain.models import ScenarioCatalogEntry


class ScenarioIndexStore:
    SCHEMA_VERSION = 1

    def __init__(self, index_dir: str | Path) -> None:
        self._index_dir = Path(index_dir).expanduser().resolve()
        self._index_dir.mkdir(parents=True, exist_ok=True)

    def save_scenarios(self, project_root: str, scenarios: list[ScenarioCatalogEntry]) -> None:
        target_dir = self._project_dir(project_root)
        target_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "updated_at": datetime.utcnow().isoformat(),
            "scenarios": [self._serialize_scenario(item) for item in scenarios],
        }
        (target_dir / "scenarios.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_scenarios(self, project_root: str) -> list[ScenarioCatalogEntry]:
        path = self._project_dir(project_root) / "scenarios.json"
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        raw_items = payload.get("scenarios", []) if isinstance(payload, dict) else []
        if not isinstance(raw_items, list):
            return []
        return [self._deserialize_scenario(item) for item in raw_items if isinstance(item, dict)]

    def clear(self, project_root: str) -> None:
        path = self._project_dir(project_root) / "scenarios.json"
        if path.exists():
            path.unlink()

    def _project_dir(self, project_root: str) -> Path:
        key = hashlib.sha1(Path(project_root).resolve().as_posix().encode()).hexdigest()
        return self._index_dir / key

    @staticmethod
    def _serialize_scenario(entry: ScenarioCatalogEntry) -> dict[str, Any]:
        data = asdict(entry)
        data["scenario_type"] = entry.scenario_type.value
        return data

    @staticmethod
    def _deserialize_scenario(data: dict[str, Any]) -> ScenarioCatalogEntry:
        return ScenarioCatalogEntry(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            feature_path=str(data.get("feature_path") or data.get("featurePath") or ""),
            scenario_name=str(data.get("scenario_name") or data.get("scenarioName") or ""),
            tags=list(data.get("tags", []) or []),
            background_steps=list(data.get("background_steps") or data.get("backgroundSteps") or []),
            steps=list(data.get("steps", []) or []),
            scenario_type=ScenarioType(
                data.get("scenario_type") or data.get("scenarioType") or ScenarioType.STANDARD.value
            ),
            document=data.get("document"),
            description=data.get("description"),
        )


__all__ = ["ScenarioIndexStore"]

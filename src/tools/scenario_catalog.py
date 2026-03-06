"""Scenario catalog extraction from local `.feature` files."""
from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

from domain.enums import ScenarioType, StepIntentType, StepKeyword
from domain.models import ScenarioCatalogEntry
from infrastructure.fs_repo import FsRepository

_FEATURE_LINE_RE = re.compile(r"^\s*(Feature|Функционал)\s*:\s*(?P<name>.+)$", re.IGNORECASE)
_SCENARIO_LINE_RE = re.compile(
    r"^\s*(Scenario|Сценарий|Scenario Outline|Структура сценария)\s*:\s*(?P<name>.+)$",
    re.IGNORECASE,
)
_BACKGROUND_LINE_RE = re.compile(r"^\s*(Background|Предыстория)\s*:\s*$", re.IGNORECASE)
_STEP_LINE_RE = re.compile(
    r"^\s*(Given|When|Then|And|But|Дано|Когда|Тогда|И|Но)\b\s*(?P<text>.+)$",
    re.IGNORECASE,
)
_TAG_LINE_RE = re.compile(r"^\s*@(?P<tags>.+)$")


def extract_scenarios(
    fs_repo: FsRepository,
    patterns: list[str] | None = None,
) -> list[ScenarioCatalogEntry]:
    feature_patterns = patterns or ["**/*.feature"]
    entries: list[ScenarioCatalogEntry] = []
    for relative_path in fs_repo.iter_source_files(feature_patterns):
        pure_path = PurePosixPath(relative_path)
        if pure_path.match("**/build/**") or pure_path.match("**/target/**") or pure_path.match("**/.idea/**"):
            continue
        content = fs_repo.read_text_file(relative_path)
        entries.extend(parse_feature_file(relative_path, content))
    return entries


def parse_feature_file(relative_path: str, content: str) -> list[ScenarioCatalogEntry]:
    lines = content.splitlines()
    feature_name = PurePosixPath(relative_path).stem
    feature_tags: list[str] = []
    pending_tags: list[str] = []
    background_steps: list[str] = []
    background_mode = False
    current_name: str | None = None
    current_steps: list[str] = []
    current_tags: list[str] = []
    entries: list[ScenarioCatalogEntry] = []

    def _flush_current() -> None:
        nonlocal current_name, current_steps, current_tags
        if not current_name:
            current_steps = []
            current_tags = []
            return
        scenario_id = _scenario_id(relative_path, current_name, current_steps)
        steps = [step.strip() for step in current_steps if step.strip()]
        entry = ScenarioCatalogEntry(
            id=scenario_id,
            name=f"{feature_name}: {current_name}",
            feature_path=relative_path,
            scenario_name=current_name,
            tags=list(dict.fromkeys(feature_tags + current_tags)),
            background_steps=list(background_steps),
            steps=steps,
            scenario_type=infer_scenario_type(current_name, steps),
            document=build_scenario_document(
                feature_name=feature_name,
                scenario_name=current_name,
                tags=feature_tags + current_tags,
                background_steps=background_steps,
                steps=steps,
            ),
        )
        entries.append(entry)
        current_name = None
        current_steps = []
        current_tags = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        tag_match = _TAG_LINE_RE.match(stripped)
        if tag_match:
            tags = [item.strip().lstrip("@") for item in tag_match.group("tags").split("@") if item.strip()]
            pending_tags.extend(tags)
            continue

        feature_match = _FEATURE_LINE_RE.match(stripped)
        if feature_match:
            feature_name = feature_match.group("name").strip()
            feature_tags = list(dict.fromkeys(pending_tags))
            pending_tags = []
            background_mode = False
            continue

        if _BACKGROUND_LINE_RE.match(stripped):
            background_mode = True
            pending_tags = []
            continue

        scenario_match = _SCENARIO_LINE_RE.match(stripped)
        if scenario_match:
            _flush_current()
            current_name = scenario_match.group("name").strip()
            current_tags = list(dict.fromkeys(pending_tags))
            pending_tags = []
            background_mode = False
            continue

        step_match = _STEP_LINE_RE.match(stripped)
        if step_match:
            step_text = step_match.group(0).strip()
            if background_mode and current_name is None:
                background_steps.append(step_text)
            elif current_name is not None:
                current_steps.append(step_text)
            continue

        if stripped.startswith("|"):
            if background_mode and current_name is None and background_steps:
                background_steps.append(stripped)
            elif current_name is not None and current_steps:
                current_steps.append(stripped)

    _flush_current()
    return entries


def build_scenario_document(
    *,
    feature_name: str,
    scenario_name: str,
    tags: list[str],
    background_steps: list[str],
    steps: list[str],
) -> str:
    parts = [feature_name, scenario_name]
    if tags:
        parts.extend(tags)
    parts.extend(background_steps)
    parts.extend(steps)
    return "\n".join(part for part in parts if part)


def infer_scenario_type(name: str, steps: list[str]) -> ScenarioType:
    text = " ".join([name] + steps).casefold()
    if any(token in text for token in ("invalid", "ошиб", "error", "некоррект", "validation", "валидац")):
        return ScenarioType.VALIDATION
    if any(token in text for token in ("negative", "негатив", "forbidden", "denied")):
        return ScenarioType.NEGATIVE
    if any(token in text for token in ("navigate", "перех", "откры", "open screen", "screen")):
        return ScenarioType.NAVIGATION
    if any(token in text for token in ("create", "update", "delete", "crud", "созда", "удаля", "измен")):
        return ScenarioType.CRUD
    return ScenarioType.STANDARD


def step_keyword_from_text(text: str) -> StepKeyword:
    match = _STEP_LINE_RE.match(text.strip())
    if not match:
        return StepKeyword.WHEN
    keyword = match.group(0).split(maxsplit=1)[0]
    return StepKeyword.from_string(keyword)


def infer_intent_type(text: str) -> StepIntentType:
    keyword = step_keyword_from_text(text)
    if keyword is StepKeyword.GIVEN:
        return StepIntentType.SETUP
    if keyword is StepKeyword.THEN:
        return StepIntentType.ASSERTION
    lowered = text.casefold()
    if any(token in lowered for token in ("open", "navigate", "перей", "откры")):
        return StepIntentType.NAVIGATION
    return StepIntentType.ACTION


def match_fragments(query_fragments: list[str], scenario: ScenarioCatalogEntry, *, limit: int = 3) -> list[str]:
    scenario_lines = [*scenario.background_steps, *scenario.steps]
    hits: list[str] = []
    seen: set[str] = set()
    for fragment in query_fragments:
        normalized = " ".join(fragment.casefold().split())
        if not normalized:
            continue
        for line in scenario_lines:
            line_normalized = " ".join(line.casefold().split())
            if normalized in line_normalized or line_normalized in normalized:
                marker = line.strip()
                if marker and marker not in seen:
                    hits.append(marker)
                    seen.add(marker)
                break
        if len(hits) >= limit:
            break
    return hits


def _scenario_id(relative_path: str, scenario_name: str, steps: list[str]) -> str:
    digest = hashlib.sha1(
        "\n".join([relative_path, scenario_name, *steps]).encode("utf-8")
    ).hexdigest()
    return digest[:16]


__all__ = [
    "build_scenario_document",
    "extract_scenarios",
    "infer_intent_type",
    "infer_scenario_type",
    "match_fragments",
    "parse_feature_file",
    "step_keyword_from_text",
]

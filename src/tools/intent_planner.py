"""Intent extraction and scenario expansion for weakly specified manual testcases."""
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from domain.enums import ScenarioType

_NEGATIVE_RE = re.compile(
    r"\b(error|invalid|incorrect|required|denied|forbidden|fail|fails|failure|validation|empty|ошибк|неверн|некоррект|обязатель|запрещ|нельзя|пуст)\b",
    re.IGNORECASE,
)
_BOUNDARY_RE = re.compile(
    r"\b(limit|boundary|max|min|length|size|range|date|time|number|numeric|digits|format|длина|размер|границ|макс|мин|диапазон|дата|время|числ|формат)\b",
    re.IGNORECASE,
)
_ROLE_STATE_RE = re.compile(
    r"\b(admin|administrator|manager|operator|guest|anonymous|role|permission|status|state|logged in|logged out|blocked|active|архив|роль|прав|статус|состояни|авториз|неавториз|заблокирован|активн)\b",
    re.IGNORECASE,
)
_FIELD_RE = re.compile(
    r"\b(field|form|input|email|password|code|amount|date|time|name|comment|phone|поле|форма|логин|парол|код|сумм|дат|врем|имя|комментар|телефон)\b",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"\b\d{1,4}[./-]\d{1,2}[./-]\d{1,4}\b")
_NUMBER_RE = re.compile(r"\b-?\d+(?:[.,]\d+)?\b")
_QUOTED_RE = re.compile(r'"([^"]+)"|\'([^\']+)\'|«([^»]+)»')
_WORD_RE = re.compile(r"[A-Za-zА-Яа-я0-9_]+")
_COMMON_ACTORS = {
    "user",
    "client",
    "customer",
    "admin",
    "administrator",
    "manager",
    "operator",
    "agent",
    "system",
    "service",
    "пользователь",
    "клиент",
    "покупатель",
    "администратор",
    "менеджер",
    "оператор",
    "система",
    "сервис",
}
_VERB_HINTS = {
    "open",
    "opens",
    "create",
    "creates",
    "submit",
    "submits",
    "login",
    "log",
    "enter",
    "enters",
    "view",
    "views",
    "select",
    "selects",
    "choose",
    "chooses",
    "fill",
    "fills",
    "save",
    "saves",
    "update",
    "updates",
    "delete",
    "deletes",
    "approve",
    "approves",
    "request",
    "requests",
    "navigate",
    "navigates",
    "open",
    "открывает",
    "создает",
    "создаёт",
    "вводит",
    "заполняет",
    "выбирает",
    "сохраняет",
    "обновляет",
    "удаляет",
    "авторизуется",
    "логинится",
    "переходит",
    "отправляет",
    "запрашивает",
    "подтверждает",
}


def extract_canonical_intent(
    *,
    testcase_text: str,
    scenario: dict[str, Any],
    llm_client: Any | None = None,
) -> dict[str, Any]:
    canonical = scenario.get("canonical") if isinstance(scenario, dict) else {}
    action_steps = [
        str(item.get("text", "")).strip()
        for item in canonical.get("actions", [])
        if isinstance(item, dict) and str(item.get("text", "")).strip()
    ]
    preconditions = [
        str(item.get("text", "")).strip()
        for item in canonical.get("preconditions", [])
        if isinstance(item, dict) and str(item.get("text", "")).strip()
    ]
    expected_results = [
        str(item.get("text", "")).strip()
        for item in canonical.get("expected_results", [])
        if isinstance(item, dict) and str(item.get("text", "")).strip()
    ]
    all_text_fragments = [testcase_text, *preconditions, *action_steps, *expected_results]

    actor = _extract_actor(action_steps, preconditions, testcase_text)
    goal = _extract_goal(action_steps, testcase_text)
    observable_outcomes = _extract_observable_outcomes(expected_results, scenario)
    data_dimensions = _extract_data_dimensions(canonical, all_text_fragments)
    business_rules = _extract_business_rules(all_text_fragments, observable_outcomes)
    sut_area = _extract_sut_area(goal, action_steps, scenario)

    intent = {
        "goal": goal,
        "actor": actor,
        "sutArea": sut_area,
        "preconditions": preconditions,
        "businessRules": business_rules,
        "dataDimensions": data_dimensions,
        "observableOutcomes": observable_outcomes,
        "unknowns": [],
        "assumptions": [],
        "confidence": 0.0,
        "evidenceRefs": [],
    }

    if llm_client and (not actor or not goal or not observable_outcomes):
        intent = _fill_missing_with_llm(
            llm_client=llm_client,
            testcase_text=testcase_text,
            scenario=scenario,
            intent=intent,
        )

    unknowns = []
    if not intent.get("actor"):
        unknowns.append("actor")
    if not intent.get("goal"):
        unknowns.append("primary_action")
    if not intent.get("observableOutcomes"):
        unknowns.append("observable_outcome")
    intent["unknowns"] = unknowns
    intent["assumptions"] = _build_assumptions(intent, scenario)
    intent["confidence"] = _estimate_intent_confidence(intent)
    return intent


def detect_ambiguity_issues(intent: dict[str, Any], scenario: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not str(intent.get("actor") or "").strip():
        issues.append(
            {
                "id": "issue-actor-missing",
                "severity": "blocking",
                "category": "actor_missing",
                "field": "actor",
                "message": "Scenario actor is not explicit in the manual testcase.",
                "question": "Who performs the main scenario?",
                "assumptionId": None,
            }
        )
    if not str(intent.get("goal") or "").strip():
        issues.append(
            {
                "id": "issue-action-missing",
                "severity": "blocking",
                "category": "action_missing",
                "field": "goal",
                "message": "Primary business action is not explicit in the manual testcase.",
                "question": "What is the main user action that should be automated?",
                "assumptionId": None,
            }
        )
    outcomes = [item for item in intent.get("observableOutcomes", []) if str(item).strip()]
    if not outcomes:
        issues.append(
            {
                "id": "issue-outcome-missing",
                "severity": "blocking",
                "category": "observable_outcome_missing",
                "field": "observableOutcomes",
                "message": "The testcase does not describe a user-visible expected result.",
                "question": "What should the system visibly show or return when the scenario succeeds?",
                "assumptionId": None,
            }
        )

    assumptions = intent.get("assumptions", []) if isinstance(intent.get("assumptions"), list) else []
    for assumption in assumptions:
        if not isinstance(assumption, dict):
            continue
        issues.append(
            {
                "id": f"issue-{assumption.get('id')}",
                "severity": "non_blocking",
                "category": str(assumption.get("category") or "assumption"),
                "field": assumption.get("field"),
                "message": str(assumption.get("text") or "").strip(),
                "question": assumption.get("question"),
                "assumptionId": assumption.get("id"),
            }
        )
    return issues


def expand_scenario_candidates(
    *,
    intent: dict[str, Any],
    scenario: dict[str, Any],
    ambiguity_issues: list[dict[str, Any]],
    max_candidates: int = 3,
) -> list[dict[str, Any]]:
    if max_candidates < 1:
        return []

    has_negative = _NEGATIVE_RE.search(_combined_text(intent, scenario)) is not None
    has_boundary = _BOUNDARY_RE.search(_combined_text(intent, scenario)) is not None or bool(intent.get("dataDimensions"))
    has_role_state = _ROLE_STATE_RE.search(_combined_text(intent, scenario)) is not None

    ordered_types = ["happy_path"]
    if has_negative:
        ordered_types.append("negative")
    if has_role_state:
        ordered_types.append("role_state")
    if has_boundary:
        ordered_types.append("boundary_data")

    deduped_types: list[str] = []
    for candidate_type in ordered_types:
        if candidate_type not in deduped_types:
            deduped_types.append(candidate_type)
    deduped_types = deduped_types[:max_candidates]

    primary_type = _select_primary_candidate_type(deduped_types, intent, scenario)
    candidates: list[dict[str, Any]] = []
    for rank, candidate_type in enumerate(deduped_types, start=1):
        candidate_scenario = _build_candidate_scenario(candidate_type, intent, scenario)
        candidates.append(
            {
                "id": f"candidate-{rank}-{candidate_type}",
                "type": candidate_type,
                "rank": rank,
                "title": candidate_scenario.get("name") or f"Candidate {rank}",
                "rationale": _candidate_rationale(candidate_type, intent),
                "recommended": candidate_type == primary_type,
                "confidence": round(_candidate_confidence(candidate_type, intent, ambiguity_issues), 4),
                "expectedOutcomes": list(intent.get("observableOutcomes", []) or []),
                "assumptionIds": [
                    str(item.get("id"))
                    for item in intent.get("assumptions", [])
                    if isinstance(item, dict) and item.get("id")
                ],
                "evidenceRefs": [],
                "steps": [
                    str(item.get("text", "")).strip()
                    for item in candidate_scenario.get("steps", [])
                    if isinstance(item, dict) and str(item.get("text", "")).strip()
                ],
                "backgroundSteps": [
                    str(item.get("text", "")).strip()
                    for item in candidate_scenario.get("preconditions", [])
                    if isinstance(item, dict) and str(item.get("text", "")).strip()
                ],
                "scenario": candidate_scenario,
            }
        )

    if candidates and not any(item.get("recommended") for item in candidates):
        candidates[0]["recommended"] = True
    return candidates


def _extract_actor(action_steps: list[str], preconditions: list[str], testcase_text: str) -> str | None:
    for source in [*action_steps, *preconditions, testcase_text]:
        candidate = str(source or "").strip()
        if not candidate:
            continue
        lowered = candidate.casefold()
        for actor in _COMMON_ACTORS:
            if lowered.startswith(actor.casefold() + " "):
                return actor
        tokens = _WORD_RE.findall(candidate)
        if len(tokens) >= 2 and tokens[0].casefold() in _COMMON_ACTORS:
            return tokens[0]
        if len(tokens) >= 2 and tokens[1].casefold() in _VERB_HINTS and len(tokens[0]) > 2:
            return tokens[0]
    return None


def _extract_goal(action_steps: list[str], testcase_text: str) -> str | None:
    prioritized_verbs = (
        "submit",
        "save",
        "create",
        "update",
        "delete",
        "approve",
        "request",
        "send",
        "отправ",
        "сохран",
        "созда",
        "обнов",
        "удал",
        "подтверж",
        "запрос",
    )
    prioritized_candidates: list[str] = []
    for step in action_steps:
        candidate = _strip_actor(step)
        if candidate:
            prioritized_candidates.append(candidate)
    for candidate in prioritized_candidates:
        lowered = candidate.casefold()
        if any(token in lowered for token in prioritized_verbs):
            return candidate
    if prioritized_candidates:
        return prioritized_candidates[-1]
    for line in str(testcase_text or "").splitlines():
        stripped = line.strip(" -*0123456789.")
        candidate = _strip_actor(stripped)
        if candidate:
            return candidate
    return None


def _extract_sut_area(goal: str | None, action_steps: list[str], scenario: dict[str, Any]) -> str | None:
    names = [str(scenario.get("name") or "").strip(), str(goal or "").strip(), *action_steps]
    for name in names:
        tokens = [token for token in _WORD_RE.findall(name) if len(token) > 3]
        if not tokens:
            continue
        for token in reversed(tokens):
            lowered = token.casefold()
            if lowered in _COMMON_ACTORS or lowered in _VERB_HINTS:
                continue
            return token
    return None


def _extract_observable_outcomes(expected_results: list[str], scenario: dict[str, Any]) -> list[str]:
    outcomes = [item for item in expected_results if item]
    if outcomes:
        return outcomes
    fallback = str(scenario.get("expected_result") or "").strip()
    if not fallback:
        return []
    return [part.strip() for part in re.split(r"[;\n]+", fallback) if part.strip()]


def _extract_data_dimensions(canonical: dict[str, Any], fragments: list[str]) -> list[str]:
    values = [str(item).strip() for item in canonical.get("test_data", []) if str(item).strip()]
    for fragment in fragments:
        text = str(fragment or "")
        for match in _QUOTED_RE.finditer(text):
            raw = next((group for group in match.groups() if group), None)
            if raw:
                values.append(raw.strip())
        values.extend(_DATE_RE.findall(text))
        values.extend(_NUMBER_RE.findall(text))
        if _FIELD_RE.search(text):
            values.append("form_field")
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value).strip()
        if not normalized:
            continue
        marker = normalized.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(normalized)
    return deduped[:10]


def _extract_business_rules(fragments: list[str], observable_outcomes: list[str]) -> list[str]:
    candidates = fragments + observable_outcomes
    rules: list[str] = []
    for fragment in candidates:
        text = str(fragment or "").strip()
        if not text:
            continue
        lowered = text.casefold()
        if any(token in lowered for token in ("must", "should", "cannot", "required", "долж", "нельзя", "обяз")):
            rules.append(text)
        elif _NEGATIVE_RE.search(text):
            rules.append(text)
    return _unique_preserve_order(rules)[:5]


def _build_assumptions(intent: dict[str, Any], scenario: dict[str, Any]) -> list[dict[str, Any]]:
    assumptions: list[dict[str, Any]] = []
    if not list(intent.get("preconditions") or []):
        assumptions.append(
            {
                "id": "assumption-preconditions",
                "text": "Assume the system is in a valid baseline state and the actor has access to the target area.",
                "question": "Should the scenario require any explicit setup or permissions before the main action?",
                "category": "preconditions_missing",
                "field": "preconditions",
            }
        )
    if not list(intent.get("dataDimensions") or []):
        assumptions.append(
            {
                "id": "assumption-data",
                "text": "Assume representative valid test data is sufficient for the primary flow.",
                "question": "Which data values or input partitions are important for this testcase?",
                "category": "data_missing",
                "field": "dataDimensions",
            }
        )
    if not str(intent.get("sutArea") or "").strip():
        assumptions.append(
            {
                "id": "assumption-area",
                "text": "Assume the testcase targets the main page or API area implied by the described action.",
                "question": "Which page, screen, API, or domain area is the primary system under test?",
                "category": "sut_area_missing",
                "field": "sutArea",
            }
        )
    if "template" in " ".join(
        str(item.get("section") or "")
        for item in scenario.get("steps", [])
        if isinstance(item, dict)
    ).casefold():
        assumptions.append(
            {
                "id": "assumption-template",
                "text": "Assume template setup steps from project memory remain valid for this testcase.",
                "question": "Are the injected setup steps from project memory still valid for this scenario?",
                "category": "memory_template",
                "field": "preconditions",
            }
        )
    return assumptions


def _estimate_intent_confidence(intent: dict[str, Any]) -> float:
    score = 0.0
    if intent.get("actor"):
        score += 0.25
    if intent.get("goal"):
        score += 0.3
    if intent.get("observableOutcomes"):
        score += 0.3
    if intent.get("preconditions"):
        score += 0.05
    if intent.get("dataDimensions"):
        score += 0.05
    score += max(0.0, 0.05 - (0.02 * len(intent.get("unknowns", []))))
    return round(max(0.0, min(1.0, score)), 4)


def _fill_missing_with_llm(
    *,
    llm_client: Any,
    testcase_text: str,
    scenario: dict[str, Any],
    intent: dict[str, Any],
) -> dict[str, Any]:
    prompt = (
        "Extract missing intent for a weakly specified testcase. "
        'Return JSON with optional fields: {"actor": "...", "goal": "...", "observableOutcomes": ["..."], "sutArea": "..."}.\n'
        f"Testcase:\n{testcase_text}\n"
        f"Parsed scenario:\n{json.dumps(scenario, ensure_ascii=False)}"
    )
    try:
        response = llm_client.generate(prompt)
        parsed = json.loads(str(response).strip())
    except Exception:
        return intent
    if not isinstance(parsed, dict):
        return intent
    merged = deepcopy(intent)
    if not merged.get("actor") and str(parsed.get("actor") or "").strip():
        merged["actor"] = str(parsed.get("actor")).strip()
    if not merged.get("goal") and str(parsed.get("goal") or "").strip():
        merged["goal"] = str(parsed.get("goal")).strip()
    if not merged.get("sutArea") and str(parsed.get("sutArea") or "").strip():
        merged["sutArea"] = str(parsed.get("sutArea")).strip()
    if not merged.get("observableOutcomes") and isinstance(parsed.get("observableOutcomes"), list):
        merged["observableOutcomes"] = [
            str(item).strip()
            for item in parsed.get("observableOutcomes", [])
            if str(item).strip()
        ]
    return merged


def _select_primary_candidate_type(
    candidate_types: list[str],
    intent: dict[str, Any],
    scenario: dict[str, Any],
) -> str:
    combined = _combined_text(intent, scenario)
    if "negative" in candidate_types and _NEGATIVE_RE.search(combined):
        return "negative"
    if "boundary_data" in candidate_types and _BOUNDARY_RE.search(combined):
        return "boundary_data"
    return candidate_types[0] if candidate_types else "happy_path"


def _build_candidate_scenario(candidate_type: str, intent: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    candidate = deepcopy(scenario)
    base_title = str(intent.get("goal") or scenario.get("name") or "Generated scenario").strip()
    if candidate_type == "negative":
        candidate["name"] = f"Negative path: {base_title}"
        candidate["scenario_type"] = ScenarioType.NEGATIVE.value
        candidate["description"] = "Validation and error handling path inferred from the testcase."
    elif candidate_type == "boundary_data":
        candidate["name"] = f"Boundary data: {base_title}"
        candidate["scenario_type"] = ScenarioType.VALIDATION.value
        candidate["description"] = "Boundary/data variation inferred from testcase inputs."
    elif candidate_type == "role_state":
        candidate["name"] = f"Role or state: {base_title}"
        candidate["scenario_type"] = ScenarioType.NAVIGATION.value
        candidate["description"] = "Role/state-sensitive flow inferred from access or lifecycle cues."
    else:
        candidate["name"] = base_title
        candidate["scenario_type"] = ScenarioType.STANDARD.value
        candidate["description"] = "Primary happy path inferred from the manual testcase."
    candidate["candidateType"] = candidate_type
    return candidate


def _candidate_rationale(candidate_type: str, intent: dict[str, Any]) -> str:
    if candidate_type == "negative":
        return "Chosen because the testcase contains validation/error cues."
    if candidate_type == "boundary_data":
        return "Chosen because the testcase references data values, fields, numbers, or formats."
    if candidate_type == "role_state":
        return "Chosen because the testcase references roles, permissions, or state transitions."
    return f"Primary flow around goal: {intent.get('goal') or 'main action'}."


def _candidate_confidence(candidate_type: str, intent: dict[str, Any], ambiguity_issues: list[dict[str, Any]]) -> float:
    base = float(intent.get("confidence") or 0.0)
    blocking = len([item for item in ambiguity_issues if str(item.get("severity")).casefold() == "blocking"])
    modifier = {
        "happy_path": 0.05,
        "negative": -0.02,
        "boundary_data": -0.03,
        "role_state": -0.04,
    }.get(candidate_type, 0.0)
    return max(0.0, min(1.0, base + modifier - (0.1 * blocking)))


def _strip_actor(text: str) -> str | None:
    tokens = _WORD_RE.findall(str(text or ""))
    if not tokens:
        return None
    if len(tokens) >= 2 and tokens[0].casefold() in _COMMON_ACTORS:
        return str(text).strip()[len(tokens[0]) :].strip()
    return str(text or "").strip() or None


def _combined_text(intent: dict[str, Any], scenario: dict[str, Any]) -> str:
    fragments = [
        str(intent.get("goal") or ""),
        str(intent.get("actor") or ""),
        str(intent.get("sutArea") or ""),
        *[str(item) for item in intent.get("observableOutcomes", [])],
        *[
            str(item.get("text") or "")
            for item in scenario.get("steps", [])
            if isinstance(item, dict)
        ],
    ]
    return "\n".join(fragment for fragment in fragments if fragment)


def _unique_preserve_order(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        normalized = str(item).strip()
        if not normalized:
            continue
        marker = normalized.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        result.append(normalized)
    return result


__all__ = [
    "detect_ambiguity_issues",
    "expand_scenario_candidates",
    "extract_canonical_intent",
]

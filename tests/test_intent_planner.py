from __future__ import annotations

from tools.intent_planner import (
    detect_ambiguity_issues,
    expand_scenario_candidates,
    extract_canonical_intent,
)


def _scenario() -> dict[str, object]:
    return {
        "name": "User submits payment request",
        "expected_result": "Success message is displayed",
        "steps": [
            {"order": 1, "text": "authorized user opens payment form", "section": "precondition"},
            {"order": 2, "text": "user enters amount 100", "section": "step"},
            {"order": 3, "text": "user submits request", "section": "step"},
            {"order": 4, "text": "success message is displayed", "section": "expected_result"},
        ],
        "canonical": {
            "title": "User submits payment request",
            "preconditions": [
                {"order": 1, "text": "authorized user opens payment form"},
            ],
            "actions": [
                {"order": 2, "text": "user enters amount 100"},
                {"order": 3, "text": "user submits request"},
            ],
            "expected_results": [
                {"order": 4, "text": "success message is displayed"},
            ],
            "test_data": ["100"],
            "tags": [],
            "scenario_type": "standard",
            "source": "heuristic",
        },
    }


def test_extract_canonical_intent_restores_actor_goal_and_outcomes() -> None:
    intent = extract_canonical_intent(
        testcase_text="Authorized user enters amount 100 and submits request. Success message is displayed.",
        scenario=_scenario(),
        llm_client=None,
    )

    assert intent["actor"]
    assert "submits" in intent["goal"].lower() or "request" in intent["goal"].lower()
    assert intent["observableOutcomes"] == ["success message is displayed"]
    assert "100" in intent["dataDimensions"]


def test_detect_ambiguity_marks_missing_actor_and_outcome_as_blocking() -> None:
    issues = detect_ambiguity_issues(
        {
            "goal": "submit request",
            "actor": None,
            "observableOutcomes": [],
            "preconditions": [],
            "assumptions": [],
        },
        {"steps": []},
    )

    blocking_categories = {
        item["category"]
        for item in issues
        if item["severity"] == "blocking"
    }
    assert "actor_missing" in blocking_categories
    assert "observable_outcome_missing" in blocking_categories


def test_expand_scenario_candidates_is_deterministic_and_capped_at_three() -> None:
    scenario = _scenario()
    intent = extract_canonical_intent(
        testcase_text="Authorized user enters invalid amount 100 and submits request. Validation error is shown.",
        scenario=scenario,
        llm_client=None,
    )
    intent["observableOutcomes"] = ["Validation error is shown"]
    issues = detect_ambiguity_issues(intent, scenario)

    candidates = expand_scenario_candidates(
        intent=intent,
        scenario=scenario,
        ambiguity_issues=issues,
        max_candidates=3,
    )

    assert 1 <= len(candidates) <= 3
    assert candidates[0]["id"].startswith("candidate-1-")
    assert any(item["type"] == "negative" for item in candidates)

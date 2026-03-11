from __future__ import annotations

from tools.generation_quality import evaluate_generation_quality


def test_quality_report_passes_for_strict_policy_on_clean_output() -> None:
    report = evaluate_generation_quality(
        feature_payload={
            "featureText": "Feature: demo\n  Scenario: happy path\n    Given user is logged in\n",
            "stepsSummary": {"exact": 1, "fuzzy": 0, "unmatched": 0},
            "parameterFillSummary": {"full": 1, "partial": 0, "fallback": 0, "none": 0},
        },
        match_result={"ambiguousCount": 0, "llmRerankedCount": 0},
        scenario={"normalization": {"splitCount": 0}},
        policy="strict",
    )

    assert report["policy"] == "strict"
    assert report["passed"] is True
    assert report["score"] >= 80
    assert report["metrics"]["syntaxValid"] is True
    assert report["metrics"]["unmatchedRatio"] == 0.0


def test_quality_report_fails_when_unmatched_ratio_or_ambiguity_exceed_policy() -> None:
    report = evaluate_generation_quality(
        feature_payload={
            "featureText": "Feature: demo\n  Scenario: noisy\n    Given step one\n",
            "stepsSummary": {"exact": 2, "fuzzy": 0, "unmatched": 2},
            "parameterFillSummary": {"full": 0, "partial": 2, "fallback": 0, "none": 0},
        },
        match_result={"ambiguousCount": 1, "llmRerankedCount": 3},
        scenario={"normalization": {"splitCount": 2}},
        policy="strict",
    )

    assert report["passed"] is False
    failure_codes = {entry["code"] for entry in report["failures"]}
    assert "unmatched_ratio_exceeded" in failure_codes
    assert "ambiguous_count_exceeded" in failure_codes


def test_quality_report_fails_when_feature_syntax_is_invalid() -> None:
    report = evaluate_generation_quality(
        feature_payload={
            "featureText": "broken text without gherkin structure",
            "stepsSummary": {"exact": 0, "fuzzy": 0, "unmatched": 0},
            "parameterFillSummary": {"full": 0, "partial": 0, "fallback": 0, "none": 0},
        },
        match_result={},
        scenario={},
        policy="strict",
    )

    assert report["passed"] is False
    failure_codes = {entry["code"] for entry in report["failures"]}
    assert "syntax_invalid" in failure_codes
    assert "quality_score_too_low" in failure_codes


def test_quality_report_fails_when_blocking_ambiguity_and_new_steps_dominate() -> None:
    report = evaluate_generation_quality(
        feature_payload={
            "featureText": "Feature: demo\n  Scenario: draft\n    When <new_step_needed: submit request>\n",
            "stepsSummary": {"exact": 0, "fuzzy": 0, "unmatched": 2},
            "parameterFillSummary": {"full": 0, "partial": 0, "fallback": 0, "none": 2},
        },
        match_result={
            "matched": [
                {
                    "status": "unmatched",
                    "test_step": {"section": "step", "text": "submit request"},
                    "notes": {"bindingStatus": "new_step_needed"},
                },
                {
                    "status": "unmatched",
                    "test_step": {"section": "expected_result", "text": "success message is shown"},
                    "notes": {"bindingStatus": "manual_review"},
                },
            ],
            "ambiguousCount": 0,
            "llmRerankedCount": 0,
        },
        scenario={"normalization": {"splitCount": 0}},
        canonical_intent={
            "preconditions": [],
            "observableOutcomes": ["success message is shown"],
            "dataDimensions": [],
            "assumptions": [{"id": "a1", "text": "assume happy path"}],
        },
        ambiguity_issues=[
            {"severity": "blocking", "category": "actor_missing"},
        ],
        selected_scenario_candidate={"type": "happy_path"},
        policy="strict",
    )

    failure_codes = {entry["code"] for entry in report["failures"]}
    assert "blocking_ambiguity" in failure_codes
    assert "new_steps_needed_exceeded" in failure_codes
    assert report["coverageReport"]["newStepsNeededCount"] == 1

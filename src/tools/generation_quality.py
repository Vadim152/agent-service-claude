"""Deterministic quality evaluation for generated autotest features."""
from __future__ import annotations

import re
from typing import Any

QUALITY_POLICIES: dict[str, dict[str, float | int]] = {
    "strict": {
        "min_score": 80,
        "max_unmatched_ratio": 0.10,
        "max_ambiguous_count": 0,
    },
    "balanced": {
        "min_score": 70,
        "max_unmatched_ratio": 0.20,
        "max_ambiguous_count": 2,
    },
    "lenient": {
        "min_score": 60,
        "max_unmatched_ratio": 0.35,
        "max_ambiguous_count": 5,
    },
}

_FEATURE_RE = re.compile(r"^\s*(Feature|Функционал)\s*:\s*\S+", re.IGNORECASE)
_SCENARIO_RE = re.compile(r"^\s*(Scenario|Сценарий)\s*:\s*\S+", re.IGNORECASE)
_STEP_RE = re.compile(
    r"^\s*(Given|When|Then|And|But|Допустим|Когда|Тогда|И|Но)\b",
    re.IGNORECASE,
)
_UNMATCHED_MARKER_RE = re.compile(r"<[^>]*unmatched[^>]*>", re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(r"\{[^}]+\}")


def normalize_quality_policy(value: str | None) -> str:
    candidate = str(value or "strict").strip().lower()
    return candidate if candidate in QUALITY_POLICIES else "strict"


def evaluate_generation_quality(
    *,
    feature_payload: dict[str, Any],
    match_result: dict[str, Any],
    scenario: dict[str, Any] | None = None,
    policy: str | None = None,
    canonical_intent: dict[str, Any] | None = None,
    ambiguity_issues: list[dict[str, Any]] | None = None,
    selected_scenario_candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_policy = normalize_quality_policy(policy)
    policy_limits = QUALITY_POLICIES[normalized_policy]

    scenario_payload = scenario or {}
    feature_text = str(feature_payload.get("featureText", "") or "")
    steps_summary = feature_payload.get("stepsSummary") or {}
    fill_summary = feature_payload.get("parameterFillSummary") or {}

    exact = max(0, int(steps_summary.get("exact", 0) or 0))
    fuzzy = max(0, int(steps_summary.get("fuzzy", 0) or 0))
    unmatched = max(0, int(steps_summary.get("unmatched", 0) or 0))
    total_steps = exact + fuzzy + unmatched
    ratio_base = max(1, total_steps)

    unmatched_ratio = unmatched / ratio_base
    exact_ratio = exact / ratio_base
    fuzzy_ratio = fuzzy / ratio_base

    fill_total = max(0, int(sum(int(value or 0) for value in fill_summary.values())))
    fill_full = max(0, int(fill_summary.get("full", 0) or 0))
    parameter_fill_full_ratio = (fill_full / fill_total) if fill_total > 0 else 0.0

    ambiguous_count = max(0, int(match_result.get("ambiguousCount", 0) or 0))
    llm_reranked_count = max(0, int(match_result.get("llmRerankedCount", 0) or 0))
    normalization = scenario_payload.get("normalization") or {}
    normalization_split_count = max(0, int(normalization.get("splitCount", 0) or 0))
    match_entries = [
        item for item in (match_result.get("matched") or []) if isinstance(item, dict)
    ]
    scenario_steps = [
        item for item in (scenario_payload.get("steps") or []) if isinstance(item, dict)
    ]
    expected_steps = [
        item for item in scenario_steps if str(item.get("section") or "").casefold() == "expected_result"
    ]
    matched_expected = [
        item
        for item in match_entries
        if str(((item.get("test_step") or {}).get("section") or "")).casefold() == "expected_result"
        and str(item.get("status") or "").casefold() != "unmatched"
    ]
    assertion_steps = [
        item
        for item in match_entries
        if (
            str((((item.get("step_definition") or {}).get("keyword")) or "")).casefold() == "then"
            or str(item.get("generated_gherkin_line") or "").lstrip().casefold().startswith(("then ", "тогда "))
        )
    ]
    weak_match_count = max(
        0,
        len([item for item in match_entries if str(item.get("status") or "").casefold() == "fuzzy"]),
    )
    expected_result_count = len(expected_steps)
    expected_result_coverage = (
        len(matched_expected) / expected_result_count if expected_result_count > 0 else 1.0
    )
    assertion_count = len(assertion_steps)
    missing_assertion_count = max(0, expected_result_count - assertion_count)
    logical_complete = total_steps > 0 and (expected_result_count == 0 or assertion_count > 0)
    coverage_report = build_coverage_report(
        feature_payload=feature_payload,
        match_result=match_result,
        scenario=scenario_payload,
        canonical_intent=canonical_intent,
        ambiguity_issues=ambiguity_issues,
        selected_scenario_candidate=selected_scenario_candidate,
    )

    syntax_valid, critic_issues = _validate_feature_syntax(feature_text)
    score = _compute_quality_score(
        syntax_valid=syntax_valid,
        unmatched_ratio=unmatched_ratio,
        ambiguous_count=ambiguous_count,
        parameter_fill_full_ratio=parameter_fill_full_ratio,
        llm_reranked_count=llm_reranked_count,
        normalization_split_count=normalization_split_count,
        total_steps=total_steps,
        feature_text=feature_text,
        expected_result_coverage=expected_result_coverage,
        weak_match_count=weak_match_count,
        missing_assertion_count=missing_assertion_count,
        logical_complete=logical_complete,
        oracle_coverage=float(coverage_report.get("oracleCoverage", 1.0)),
        then_coverage=float(coverage_report.get("thenCoverage", 1.0)),
        blocking_ambiguity_count=int(coverage_report.get("blockingIssueCount", 0)),
        assumption_count=int(coverage_report.get("assumptionCount", 0)),
        new_steps_needed_count=int(coverage_report.get("newStepsNeededCount", 0)),
        flake_risk_count=len(coverage_report.get("flakeRiskFlags", [])),
    )

    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not syntax_valid:
        failures.append(
            {
                "code": "syntax_invalid",
                "message": "Generated feature text does not pass deterministic syntax checks",
                "actual": critic_issues,
                "expected": "valid feature/scenario/step structure",
            }
        )

    max_unmatched_ratio = float(policy_limits["max_unmatched_ratio"])
    if unmatched_ratio > max_unmatched_ratio:
        failures.append(
            {
                "code": "unmatched_ratio_exceeded",
                "message": "Too many unmatched steps for selected quality policy",
                "actual": round(unmatched_ratio, 4),
                "expected": f"<= {max_unmatched_ratio}",
            }
        )

    max_ambiguous_count = int(policy_limits["max_ambiguous_count"])
    if ambiguous_count > max_ambiguous_count:
        failures.append(
            {
                "code": "ambiguous_count_exceeded",
                "message": "Too many ambiguous step matches for selected quality policy",
                "actual": ambiguous_count,
                "expected": f"<= {max_ambiguous_count}",
            }
        )

    if int(coverage_report.get("blockingIssueCount", 0)) > 0:
        failures.append(
            {
                "code": "blocking_ambiguity",
                "message": "Critical testcase ambiguity must be resolved before generation is considered safe",
                "actual": int(coverage_report.get("blockingIssueCount", 0)),
                "expected": 0,
            }
        )

    if float(coverage_report.get("oracleCoverage", 1.0)) <= 0.0:
        failures.append(
            {
                "code": "oracle_coverage_missing",
                "message": "Generated scenario lacks explicit observable outcome coverage",
                "actual": float(coverage_report.get("oracleCoverage", 0.0)),
                "expected": "> 0",
            }
        )

    if float(coverage_report.get("thenCoverage", 1.0)) <= 0.0:
        failures.append(
            {
                "code": "then_coverage_missing",
                "message": "Generated scenario does not preserve Then/assertion coverage for the selected intent",
                "actual": float(coverage_report.get("thenCoverage", 0.0)),
                "expected": "> 0",
            }
        )

    new_steps_needed_count = int(coverage_report.get("newStepsNeededCount", 0))
    new_step_failure_threshold = max(1, int((total_steps or 1) * 0.4))
    if new_steps_needed_count >= new_step_failure_threshold:
        failures.append(
            {
                "code": "new_steps_needed_exceeded",
                "message": "Too many unresolved actions require new automation steps for this draft",
                "actual": new_steps_needed_count,
                "expected": f"<= {new_step_failure_threshold}",
            }
        )

    if expected_result_count > 0 and expected_result_coverage < 1.0:
        warnings.append(
            {
                "code": "expected_result_partial",
                "message": "Not all expected-result intents were confidently bound to assertions",
                "actual": round(expected_result_coverage, 4),
                "expected": 1.0,
            }
        )

    if missing_assertion_count > 0:
        warnings.append(
            {
                "code": "missing_then_assertions",
                "message": "Generated scenario has fewer assertion steps than expected results",
                "actual": missing_assertion_count,
                "expected": 0,
            }
        )

    if weak_match_count > 0:
        warnings.append(
            {
                "code": "weak_bindings_present",
                "message": "Some bound steps are fuzzy matches and may need review",
                "actual": weak_match_count,
                "expected": 0,
            }
        )

    if not logical_complete:
        warnings.append(
            {
                "code": "logical_incompleteness",
                "message": "Scenario is missing required assertion coverage for extracted expectations",
                "actual": {
                    "expectedResults": expected_result_count,
                    "assertions": assertion_count,
                },
                "expected": "assertion count should cover expected results",
            }
        )

    if int(coverage_report.get("assumptionCount", 0)) > 0:
        warnings.append(
            {
                "code": "assumptions_present",
                "message": "Generation relied on explicit assumptions that should be reviewed",
                "actual": int(coverage_report.get("assumptionCount", 0)),
                "expected": 0,
            }
        )

    if coverage_report.get("flakeRiskFlags"):
        warnings.append(
            {
                "code": "flake_risk_flags_present",
                "message": "Binding or oracle signals indicate increased flaky-test risk",
                "actual": list(coverage_report.get("flakeRiskFlags", [])),
                "expected": [],
            }
        )

    min_score = int(policy_limits["min_score"])
    if score < min_score:
        failures.append(
            {
                "code": "quality_score_too_low",
                "message": "Overall quality score is below policy threshold",
                "actual": score,
                "expected": f">= {min_score}",
            }
        )

    metrics = {
        "syntaxValid": syntax_valid,
        "unmatchedStepsCount": unmatched,
        "unmatchedRatio": round(unmatched_ratio, 4),
        "exactRatio": round(exact_ratio, 4),
        "fuzzyRatio": round(fuzzy_ratio, 4),
        "parameterFillFullRatio": round(parameter_fill_full_ratio, 4),
        "ambiguousCount": ambiguous_count,
        "llmRerankedCount": llm_reranked_count,
        "normalizationSplitCount": normalization_split_count,
        "expectedResultCount": expected_result_count,
        "expectedResultCoverage": round(expected_result_coverage, 4),
        "assertionCount": assertion_count,
        "missingAssertionCount": missing_assertion_count,
        "weakMatchCount": weak_match_count,
        "logicalCompleteness": logical_complete,
        "qualityScore": score,
        "oracleCoverage": round(float(coverage_report.get("oracleCoverage", 1.0)), 4),
        "preconditionCoverage": round(float(coverage_report.get("preconditionCoverage", 1.0)), 4),
        "dataCoverage": round(float(coverage_report.get("dataCoverage", 1.0)), 4),
        "thenCoverage": round(float(coverage_report.get("thenCoverage", 1.0)), 4),
        "assumptionCount": int(coverage_report.get("assumptionCount", 0)),
        "newStepsNeededCount": int(coverage_report.get("newStepsNeededCount", 0)),
        "traceabilityScore": round(float(coverage_report.get("traceabilityScore", 1.0)), 4),
        "blockingIssueCount": int(coverage_report.get("blockingIssueCount", 0)),
        "flakeRiskFlags": list(coverage_report.get("flakeRiskFlags", [])),
    }
    return {
        "policy": normalized_policy,
        "passed": len(failures) == 0,
        "score": score,
        "failures": failures,
        "warnings": warnings,
        "criticIssues": critic_issues,
        "metrics": metrics,
        "coverageReport": coverage_report,
    }


def build_coverage_report(
    *,
    feature_payload: dict[str, Any],
    match_result: dict[str, Any],
    scenario: dict[str, Any] | None = None,
    canonical_intent: dict[str, Any] | None = None,
    ambiguity_issues: list[dict[str, Any]] | None = None,
    selected_scenario_candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scenario = scenario or {}
    canonical_intent = canonical_intent or {}
    ambiguity_issues = ambiguity_issues or []
    selected_scenario_candidate = selected_scenario_candidate or {}
    match_entries = [
        item for item in (match_result.get("matched") or []) if isinstance(item, dict)
    ]
    feature_text = str(feature_payload.get("featureText", "") or "")
    preconditions = [str(item).strip() for item in canonical_intent.get("preconditions", []) if str(item).strip()]
    observable_outcomes = [
        str(item).strip()
        for item in canonical_intent.get("observableOutcomes", [])
        if str(item).strip()
    ]
    data_dimensions = [
        str(item).strip()
        for item in canonical_intent.get("dataDimensions", [])
        if str(item).strip()
    ]
    assumptions = [
        item
        for item in canonical_intent.get("assumptions", [])
        if isinstance(item, dict) and not bool(item.get("accepted"))
    ]
    blocking_issue_count = len(
        [item for item in ambiguity_issues if str(item.get("severity") or "").casefold() == "blocking"]
    )

    bound_entries = []
    new_steps_needed_count = 0
    manual_review_count = 0
    for item in match_entries:
        status = str(item.get("status") or "").casefold()
        notes = item.get("notes") if isinstance(item.get("notes"), dict) else {}
        binding_status = str(notes.get("bindingStatus") or status or "unmatched").casefold()
        if binding_status == "new_step_needed":
            new_steps_needed_count += 1
        if binding_status == "manual_review":
            manual_review_count += 1
        if status != "unmatched":
            bound_entries.append(item)

    matched_preconditions = [
        item
        for item in bound_entries
        if str(((item.get("test_step") or {}).get("section") or "")).casefold() in {"precondition", "template"}
    ]
    assertion_count = len(
        [
            line
            for line in feature_text.splitlines()
            if line.strip().casefold().startswith(("then ", "тогда "))
        ]
    )
    precondition_coverage = (
        len(matched_preconditions) / len(preconditions)
        if preconditions
        else 1.0
    )
    if observable_outcomes:
        oracle_coverage = 1.0 if assertion_count > 0 else 0.0
        then_coverage = min(1.0, assertion_count / max(1, len(observable_outcomes)))
    else:
        oracle_coverage = 1.0
        then_coverage = 1.0

    if data_dimensions:
        token_hits = 0
        lowered_feature = feature_text.casefold()
        for item in data_dimensions:
            normalized = str(item).strip().casefold()
            if normalized and normalized in lowered_feature:
                token_hits += 1
        data_coverage = min(1.0, token_hits / max(1, len(data_dimensions)))
        if token_hits == 0 and feature_payload.get("parameterFillSummary"):
            fill_summary = feature_payload.get("parameterFillSummary") or {}
            data_coverage = 1.0 if int(fill_summary.get("full", 0) or 0) > 0 else 0.0
    else:
        data_coverage = 1.0

    assumption_count = len(assumptions)
    traceability_score = max(
        0.0,
        min(
            1.0,
            (
                oracle_coverage
                + precondition_coverage
                + data_coverage
                + then_coverage
                + (1.0 if feature_text.strip() else 0.0)
            )
            / 5.0
            - (assumption_count * 0.03)
            - (manual_review_count * 0.05),
        ),
    )
    flake_risk_flags: list[str] = []
    if int(match_result.get("ambiguousCount", 0) or 0) > 0:
        flake_risk_flags.append("ambiguous_bindings")
    if int(match_result.get("llmRerankedCount", 0) or 0) > 0:
        flake_risk_flags.append("llm_reranked_bindings")
    if new_steps_needed_count > 0:
        flake_risk_flags.append("new_steps_needed")
    if manual_review_count > 0:
        flake_risk_flags.append("manual_review_bindings")
    if assumption_count > 2:
        flake_risk_flags.append("high_assumption_count")
    if str(selected_scenario_candidate.get("type") or "").casefold() == "boundary_data":
        flake_risk_flags.append("boundary_variant")
    return {
        "oracleCoverage": round(float(oracle_coverage), 4),
        "preconditionCoverage": round(float(precondition_coverage), 4),
        "dataCoverage": round(float(data_coverage), 4),
        "thenCoverage": round(float(then_coverage), 4),
        "assumptionCount": assumption_count,
        "newStepsNeededCount": new_steps_needed_count,
        "traceabilityScore": round(float(traceability_score), 4),
        "flakeRiskFlags": flake_risk_flags,
        "blockingIssueCount": blocking_issue_count,
    }


def _compute_quality_score(
    *,
    syntax_valid: bool,
    unmatched_ratio: float,
    ambiguous_count: int,
    parameter_fill_full_ratio: float,
    llm_reranked_count: int,
    normalization_split_count: int,
    total_steps: int,
    feature_text: str,
    expected_result_coverage: float,
    weak_match_count: int,
    missing_assertion_count: int,
    logical_complete: bool,
    oracle_coverage: float,
    then_coverage: float,
    blocking_ambiguity_count: int,
    assumption_count: int,
    new_steps_needed_count: int,
    flake_risk_count: int,
) -> int:
    score = 100.0
    if not syntax_valid:
        score -= 70.0
    score -= min(40.0, unmatched_ratio * 100.0 * 0.35)
    score -= min(24.0, float(ambiguous_count) * 8.0)
    score -= min(20.0, (1.0 - parameter_fill_full_ratio) * 20.0)
    score -= min(10.0, float(llm_reranked_count))
    score -= min(6.0, float(normalization_split_count) * 0.5)
    score -= min(12.0, (1.0 - expected_result_coverage) * 20.0)
    score -= min(10.0, float(weak_match_count) * 2.0)
    score -= min(12.0, float(missing_assertion_count) * 4.0)
    score -= min(18.0, (1.0 - oracle_coverage) * 18.0)
    score -= min(14.0, (1.0 - then_coverage) * 14.0)
    score -= min(20.0, float(blocking_ambiguity_count) * 12.0)
    score -= min(10.0, float(assumption_count) * 1.5)
    score -= min(18.0, float(new_steps_needed_count) * 4.0)
    score -= min(12.0, float(flake_risk_count) * 2.0)
    if total_steps == 0:
        score -= 25.0
    if not logical_complete:
        score -= 12.0
    if _PLACEHOLDER_RE.search(feature_text):
        score -= 8.0
    bounded = max(0.0, min(100.0, score))
    return int(round(bounded))


def _validate_feature_syntax(feature_text: str) -> tuple[bool, list[str]]:
    text = feature_text.strip()
    if not text:
        return False, ["feature_text_empty"]

    lines = [line.rstrip() for line in feature_text.splitlines() if line.strip()]
    has_feature = any(_FEATURE_RE.match(line) for line in lines)
    has_scenario = any(_SCENARIO_RE.match(line) for line in lines)
    has_step = any(_STEP_RE.match(line) or line.strip().startswith("|") for line in lines)

    issues: list[str] = []
    if not has_feature:
        issues.append("missing_feature_header")
    if not has_scenario:
        issues.append("missing_scenario_header")
    if not has_step:
        issues.append("missing_gherkin_steps")
    if _UNMATCHED_MARKER_RE.search(feature_text):
        issues.append("contains_unmatched_marker")

    return len(issues) == 0, issues


__all__ = [
    "build_coverage_report",
    "evaluate_generation_quality",
    "normalize_quality_policy",
    "QUALITY_POLICIES",
]

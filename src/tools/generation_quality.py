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
    )

    failures: list[dict[str, Any]] = []
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
        "qualityScore": score,
    }
    return {
        "policy": normalized_policy,
        "passed": len(failures) == 0,
        "score": score,
        "failures": failures,
        "criticIssues": critic_issues,
        "metrics": metrics,
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
) -> int:
    score = 100.0
    if not syntax_valid:
        score -= 70.0
    score -= min(40.0, unmatched_ratio * 100.0 * 0.35)
    score -= min(24.0, float(ambiguous_count) * 8.0)
    score -= min(20.0, (1.0 - parameter_fill_full_ratio) * 20.0)
    score -= min(10.0, float(llm_reranked_count))
    score -= min(6.0, float(normalization_split_count) * 0.5)
    if total_steps == 0:
        score -= 25.0
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


__all__ = ["evaluate_generation_quality", "normalize_quality_policy", "QUALITY_POLICIES"]

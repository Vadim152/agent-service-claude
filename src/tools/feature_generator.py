"""Generation of textual .feature content from domain models."""
from __future__ import annotations

import re
from typing import Any

from domain.enums import MatchStatus, StepKeyword
from domain.models import FeatureFile, FeatureScenario, MatchedStep, Scenario, localize_gherkin_keyword
from tools.testcase_step_normalizer import is_table_row, parse_normalization_section


class FeatureGenerator:
    """Builds FeatureFile and renders final Gherkin text."""

    def __init__(self) -> None:
        self.language: str | None = None

    def build_feature(
        self,
        scenario: Scenario,
        matched_steps: list[MatchedStep],
        language: str | None = None,
    ) -> FeatureFile:
        self.language = language or "ru"
        feature_tags, scenario_tags = self._resolve_feature_and_scenario_tags(scenario.tags)
        feature = FeatureFile(
            name=scenario.name or "Feature",
            description=scenario.description or scenario.expected_result,
            language=self.language,
            tags=feature_tags,
            background_steps=[],
            scenarios=[],
        )

        scenario_steps: list[str] = []
        steps_details: list[dict[str, Any]] = []
        for matched_step in matched_steps:
            rendered, meta = self._render_step(matched_step, self.language)
            scenario_steps.append(rendered)
            binding_status = self._binding_status(matched_step)
            evidence_refs = self._evidence_refs(matched_step)
            step_payload: dict[str, Any] = {
                "originalStep": matched_step.test_step.text,
                "generatedLine": rendered,
                "status": matched_step.status.value,
                "bindingStatus": binding_status,
                "evidenceRefs": evidence_refs,
            }
            if meta:
                step_payload["meta"] = meta
            steps_details.append(step_payload)

        feature_scenario = FeatureScenario(
            name=scenario.name,
            tags=scenario_tags,
            steps=scenario_steps,
            steps_details=steps_details,
            is_outline=False,
            examples=[],
        )
        feature.add_scenario(feature_scenario)
        return feature

    @staticmethod
    def _resolve_feature_and_scenario_tags(tags: list[str]) -> tuple[list[str], list[str]]:
        tms_prefix = "TmsLink="
        tms_value = next(
            (
                str(tag).strip()
                for tag in tags
                if str(tag).strip().lower().startswith(tms_prefix.lower())
                and str(tag).strip()[len(tms_prefix) :]
            ),
            None,
        )
        if not tms_value:
            return list(tags), list(tags)

        testcase_key = tms_value[len(tms_prefix) :]
        return [testcase_key], [tms_value]

    def render_feature(self, feature: FeatureFile) -> str:
        lines: list[str] = []
        if feature.language:
            lines.append(f"# language: {feature.language}")

        if feature.tags:
            lines.append(" ".join(f"@{tag}" for tag in feature.tags))

        feature_keyword = localize_gherkin_keyword("Feature", feature.language)
        lines.append(f"{feature_keyword}: {feature.name}")

        if feature.description:
            lines.append("")
            lines.append(feature.description)

        if feature.background_steps:
            lines.append("")
            background_keyword = localize_gherkin_keyword("Background", feature.language)
            lines.append(f"  {background_keyword}:")
            for step in feature.background_steps:
                lines.append(f"    {step}")

        for scenario in feature.scenarios:
            lines.append("")
            if scenario.tags:
                lines.append(" ".join(f"@{tag}" for tag in scenario.tags))
            scenario_keyword = localize_gherkin_keyword("Scenario", feature.language)
            lines.append(f"  {scenario_keyword}: {scenario.name}")
            for step in scenario.steps:
                lines.append(f"    {step}")

        return "\n".join(lines).rstrip() + "\n"

    def _render_step(
        self,
        matched_step: MatchedStep,
        language: str | None,
    ) -> tuple[str, dict[str, Any]]:
        binding_status = self._binding_status(matched_step)
        evidence_refs = self._evidence_refs(matched_step)
        if is_table_row(matched_step.test_step.text):
            line = matched_step.test_step.text.strip()
            return line, self._with_normalization_meta(
                {
                    "substitutionType": "table_row",
                    "renderSource": "table_row",
                    "bindingStatus": binding_status,
                    "evidenceRefs": evidence_refs,
                },
                matched_step,
            )

        if matched_step.generated_gherkin_line:
            return (
                self._localize_generated_line(matched_step.generated_gherkin_line, language),
                self._with_normalization_meta(
                    {
                        "substitutionType": "generated",
                        "renderSource": "generated_line",
                        "bindingStatus": binding_status,
                        "evidenceRefs": evidence_refs,
                    },
                    matched_step,
                ),
            )

        if matched_step.resolved_step_text:
            keyword = self._select_keyword(matched_step, language)
            line = f"{keyword} {matched_step.resolved_step_text}".strip()
            meta: dict[str, Any] = {
                "substitutionType": "resolved",
                "renderSource": "definition_pattern",
                "bindingStatus": binding_status,
                "evidenceRefs": evidence_refs,
            }
            if matched_step.parameter_fill_meta:
                fill_meta = dict(matched_step.parameter_fill_meta)
                meta["parameterFill"] = fill_meta
                status = fill_meta.get("status")
                if status:
                    meta["parameterFillStatus"] = status
                source = str(fill_meta.get("source") or "").casefold()
                if source == "regex_strict":
                    meta["renderSource"] = "definition_regex"
            if matched_step.matched_parameters:
                meta["matchedParameters"] = matched_step.matched_parameters
            return line, self._with_normalization_meta(meta, matched_step)

        if matched_step.status is MatchStatus.UNMATCHED or not matched_step.step_definition:
            reason = None
            if isinstance(matched_step.notes, dict):
                reason = matched_step.notes.get("reason")
            marker = reason or binding_status or "unmatched"
            line = f"{StepKeyword.WHEN.as_text(language)} <{marker}: {matched_step.test_step.text}>"
            meta: dict[str, Any] = {
                "substitutionType": "unmatched",
                "renderSource": "unmatched",
                "bindingStatus": binding_status,
                "evidenceRefs": evidence_refs,
            }
            if reason:
                meta["reason"] = reason
            return line, self._with_normalization_meta(meta, matched_step)

        rendered, meta = self._build_gherkin_line(matched_step, language)
        meta["bindingStatus"] = binding_status
        meta["evidenceRefs"] = evidence_refs
        return rendered, self._with_normalization_meta(meta, matched_step)

    def _localize_generated_line(self, line: str, language: str | None) -> str:
        match = re.match(r"^\s*(\S+)(\s+.*)?$", line)
        if not match:
            return line

        keyword = match.group(1)
        rest = match.group(2) or ""
        try:
            normalized_keyword = StepKeyword.from_string(keyword).as_text(language)
        except ValueError:
            return line

        return f"{normalized_keyword}{rest}"

    def _select_keyword(self, matched_step: MatchedStep, language: str | None) -> str:
        definition = matched_step.step_definition
        if definition and isinstance(definition.keyword, StepKeyword):
            return definition.keyword.as_text(language)
        return StepKeyword.WHEN.as_text(language)

    def _build_gherkin_line(
        self,
        matched_step: MatchedStep,
        language: str | None,
    ) -> tuple[str, dict[str, Any]]:
        definition = matched_step.step_definition
        if not definition:
            return "", {"substitutionType": "unmatched"}

        keyword = self._select_keyword(matched_step, language)
        pattern = definition.pattern
        regex = definition.regex

        filled_pattern = pattern
        try:
            match = re.search(regex, matched_step.test_step.text)
        except re.error:
            match = None

        substitution_type = "pattern"
        if match:
            groups = match.groups()
            placeholders = re.findall(r"\{[^}]+\}", pattern)
            if groups and placeholders:
                for placeholder, value in zip(placeholders, groups):
                    filled_pattern = filled_pattern.replace(placeholder, value, 1)
            elif groups:
                filled_pattern = " ".join([pattern] + list(groups))
            substitution_type = "regex"

        rendered = f"{keyword} {filled_pattern}" if filled_pattern else keyword
        meta = {"substitutionType": substitution_type}
        meta["parameterFillStatus"] = "partial" if self._has_placeholders(filled_pattern) else "full"
        return rendered, meta

    @staticmethod
    def _has_placeholders(text: str) -> bool:
        return bool(re.search(r"\{[^}]+\}", text))

    @staticmethod
    def _with_normalization_meta(
        meta: dict[str, Any],
        matched_step: MatchedStep,
    ) -> dict[str, Any]:
        section_meta = parse_normalization_section(matched_step.test_step.section)
        if not section_meta:
            return meta

        enriched = dict(meta)
        normalized_from = section_meta.get("normalizedFrom")
        strategy = section_meta.get("normalizationStrategy")
        if normalized_from:
            enriched["normalizedFrom"] = normalized_from
        if strategy:
            enriched["normalizationStrategy"] = strategy
        return enriched

    @staticmethod
    def _binding_status(matched_step: MatchedStep) -> str:
        if isinstance(matched_step.notes, dict):
            status = str(matched_step.notes.get("bindingStatus") or "").strip()
            if status:
                return status
        return matched_step.status.value

    @staticmethod
    def _evidence_refs(matched_step: MatchedStep) -> list[str]:
        if isinstance(matched_step.notes, dict):
            values = matched_step.notes.get("evidenceRefs")
            if isinstance(values, list):
                return [str(item) for item in values if str(item).strip()]
        return []

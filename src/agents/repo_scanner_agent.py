"""Agent for repository scan and step index updates."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import ZipFile

from domain.enums import StepIntentType, StepKeyword, StepPatternType
from domain.models import ScenarioCatalogEntry, StepDefinition, StepImplementation
from infrastructure.embeddings_store import EmbeddingsStore
from infrastructure.fs_repo import FsRepository
from infrastructure.llm_client import LLMClient
from infrastructure.scenario_index_store import ScenarioIndexStore
from infrastructure.step_index_store import StepIndexStore
from tools.cucumber_expression import cucumber_expression_to_regex
from tools.scenario_catalog import extract_scenarios
from tools.step_extractor import StepExtractor

logger = logging.getLogger(__name__)

DEFAULT_PROJECT_FILE_PATTERNS = [
    "**/*Steps.java",
    "**/*Steps.kt",
    "**/*Steps.groovy",
    "**/*Steps.py",
    "**/*StepDefinitions.java",
    "**/*StepDefinitions.kt",
    "**/*StepDefinitions.groovy",
    "**/*StepDefinitions.py",
    "**/*StepDefinition.java",
    "**/*StepDefinition.kt",
    "**/*StepDefinition.groovy",
    "**/*StepDefinition.py",
]

DEFAULT_EXTERNAL_FILE_PATTERNS = [
    "**/*.java",
    "**/*.kt",
    "**/*.groovy",
    "**/*.py",
]


class RepoScannerAgent:
    """Encapsulates source scan and step index refresh."""

    def __init__(
        self,
        step_index_store: StepIndexStore,
        embeddings_store: EmbeddingsStore,
        scenario_index_store: ScenarioIndexStore | None = None,
        llm_client: LLMClient | None = None,
        file_patterns: list[str] | None = None,
        external_file_patterns: list[str] | None = None,
        feature_patterns: list[str] | None = None,
    ) -> None:
        self.step_index_store = step_index_store
        self.embeddings_store = embeddings_store
        self.scenario_index_store = scenario_index_store
        self.llm_client = llm_client
        self.file_patterns = file_patterns or list(DEFAULT_PROJECT_FILE_PATTERNS)
        self.external_file_patterns = external_file_patterns or list(DEFAULT_EXTERNAL_FILE_PATTERNS)
        self.feature_patterns = feature_patterns or ["**/*.feature"]

    def scan_repository(
        self,
        project_root: str,
        additional_roots: list[str] | None = None,
    ) -> dict[str, Any]:
        """Scans one project and optional dependency roots, then rebuilds index."""

        logger.info("[RepoScannerAgent] Scan started: %s", project_root)
        roots = self._build_scan_roots(project_root, additional_roots)
        steps: list[StepDefinition] = []
        for root in roots:
            steps.extend(self._extract_steps_from_root(project_root, root))
        steps = self._deduplicate_steps(steps)
        scenarios = self._extract_scenarios(project_root)
        self._apply_scenario_metadata(steps, scenarios)

        logger.debug("[RepoScannerAgent] Steps found: %s", len(steps))

        if self.llm_client:
            for step in steps:
                self._enrich_step_with_llm(step)

        self.step_index_store.save_steps(project_root, steps)
        self.embeddings_store.index_steps(project_root, steps)
        if self.scenario_index_store:
            self.scenario_index_store.save_scenarios(project_root, scenarios)
        if hasattr(self.embeddings_store, "index_scenarios"):
            self.embeddings_store.index_scenarios(project_root, scenarios)

        updated_at = datetime.now(tz=timezone.utc).isoformat()
        result = {
            "projectRoot": project_root,
            "stepsCount": len(steps),
            "scenariosCount": len(scenarios),
            "updatedAt": updated_at,
            "sampleSteps": steps[:50],
            "sampleScenarios": scenarios[:20],
        }
        logger.info("[RepoScannerAgent] Scan completed %s. Steps: %s", project_root, len(steps))
        return result

    def _build_scan_roots(self, project_root: str, additional_roots: list[str] | None) -> list[str]:
        primary = Path(project_root).expanduser().resolve()
        ordered_unique: dict[str, None] = {str(primary): None}
        for item in additional_roots or []:
            value = str(item).strip()
            if not value:
                continue
            candidate = Path(value).expanduser().resolve()
            ordered_unique[str(candidate)] = None
        return list(ordered_unique.keys())

    def _extract_steps_from_root(self, project_root: str, root: str) -> list[StepDefinition]:
        root_path = Path(root).expanduser().resolve()
        project_path = Path(project_root).expanduser().resolve()
        is_primary_root = root_path == project_path
        file_patterns = self.file_patterns if is_primary_root else self.external_file_patterns

        if root_path.is_dir():
            extractor = StepExtractor(FsRepository(str(root_path)), file_patterns)
            steps = extractor.extract_steps()
            if not is_primary_root:
                self._prefix_external_steps(steps, str(root_path))
            return steps

        if root_path.is_file() and root_path.suffix.lower() == ".jar":
            steps = self._extract_steps_from_archive(root_path, file_patterns)
            self._prefix_external_steps(steps, str(root_path))
            return steps

        logger.debug("[RepoScannerAgent] Skip unsupported scan root: %s", root)
        return []

    def _extract_steps_from_archive(
        self,
        archive_path: Path,
        file_patterns: list[str],
    ) -> list[StepDefinition]:
        steps: list[StepDefinition] = []
        saw_supported_source_file = False
        with ZipFile(archive_path) as archive:
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                relative_path = entry.filename
                if not self._matches_pattern(relative_path, file_patterns):
                    continue
                saw_supported_source_file = True

                try:
                    content = archive.read(entry).decode("utf-8", errors="replace")
                except Exception:
                    logger.debug(
                        "[RepoScannerAgent] Failed reading archive entry %s from %s",
                        relative_path,
                        archive_path,
                    )
                    continue

                annotations = list(StepExtractor._iter_annotations(content.splitlines()))
                for annotation in annotations:
                    pattern_type = StepExtractor._detect_pattern_type(annotation.pattern)
                    regex = (
                        cucumber_expression_to_regex(annotation.pattern)
                        if pattern_type is StepPatternType.CUCUMBER_EXPRESSION
                        else annotation.pattern
                    )
                    step_id = f"{relative_path}:{annotation.line_number}"
                    steps.append(
                        StepDefinition(
                            id=step_id,
                            keyword=annotation.keyword,
                            pattern=annotation.pattern,
                            regex=regex,
                            code_ref=step_id,
                            pattern_type=pattern_type,
                            parameters=StepExtractor._extract_parameters(
                                annotation.pattern,
                                pattern_type,
                                annotation.method_parameters,
                            ),
                            tags=[],
                            language=None,
                            implementation=StepImplementation(
                                file=f"{archive_path.name}!/{relative_path}",
                                line=annotation.line_number,
                                class_name=annotation.class_name,
                                method_name=annotation.method_name,
                            ),
                        )
                    )
        if not saw_supported_source_file:
            logger.info(
                "[RepoScannerAgent] Skip archive without attached sources: %s",
                archive_path,
            )
        return steps

    def _extract_scenarios(self, project_root: str) -> list[ScenarioCatalogEntry]:
        project_path = Path(project_root).expanduser().resolve()
        fs_repo = FsRepository(str(project_path))
        return extract_scenarios(fs_repo, self.feature_patterns)

    def _matches_pattern(self, relative_path: str, file_patterns: list[str]) -> bool:
        normalized = relative_path.replace("\\", "/")
        pure = PurePosixPath(normalized)
        return any(pure.match(pattern) for pattern in file_patterns)

    @staticmethod
    def _prefix_external_steps(steps: list[StepDefinition], source_root: str) -> None:
        source_hash = hashlib.sha1(source_root.encode("utf-8")).hexdigest()[:10]
        prefix = f"dep[{source_hash}]"
        for step in steps:
            step.id = f"{prefix}:{step.id}"
            step.code_ref = f"{prefix}:{step.code_ref}"
            if step.implementation and step.implementation.file:
                step.implementation.file = f"{prefix}:{step.implementation.file}"

    @staticmethod
    def _deduplicate_steps(steps: list[StepDefinition]) -> list[StepDefinition]:
        deduped: list[StepDefinition] = []
        seen: set[tuple[str, str, str, int | None, str | None]] = set()
        for step in steps:
            impl = step.implementation
            signature = (
                step.keyword.value,
                step.pattern,
                impl.file if impl else "",
                impl.line if impl else None,
                impl.method_name if impl else None,
            )
            if signature in seen:
                continue
            seen.add(signature)
            deduped.append(step)
        return deduped

    def _apply_scenario_metadata(
        self,
        steps: list[StepDefinition],
        scenarios: list[ScenarioCatalogEntry],
    ) -> None:
        for step in steps:
            if step.step_type is None:
                step.step_type = self._infer_step_type(step)
            step.usage_count = 0
            step.linked_scenario_ids = []
            step.sample_scenario_refs = []
            if step.aliases is None:
                step.aliases = []

        linked_ids: dict[str, set[str]] = {step.id: set() for step in steps}
        sample_refs: dict[str, list[str]] = {step.id: [] for step in steps}
        aliases: dict[str, list[str]] = {step.id: list(step.aliases) for step in steps}

        for scenario in scenarios:
            scenario_ref = f"{scenario.feature_path}:{scenario.scenario_name}"
            for raw_line in [*scenario.background_steps, *scenario.steps]:
                matched_step = self._match_step_for_catalog(raw_line, steps)
                if matched_step is None:
                    continue
                matched_step.usage_count += 1
                linked_ids[matched_step.id].add(scenario.id)
                if scenario_ref not in sample_refs[matched_step.id]:
                    sample_refs[matched_step.id].append(scenario_ref)
                alias = self._scenario_alias_candidate(raw_line, matched_step)
                if alias and alias not in aliases[matched_step.id]:
                    aliases[matched_step.id].append(alias)

        for step in steps:
            step.linked_scenario_ids = sorted(linked_ids.get(step.id, set()))
            step.sample_scenario_refs = sample_refs.get(step.id, [])[:5]
            step.aliases = aliases.get(step.id, [])[:10]

    @staticmethod
    def _infer_step_type(step: StepDefinition) -> StepIntentType:
        if step.keyword is StepKeyword.GIVEN:
            return StepIntentType.SETUP
        if step.keyword is StepKeyword.THEN:
            return StepIntentType.ASSERTION
        lowered = step.pattern.casefold()
        if any(token in lowered for token in ("open", "navigate", "перей", "откры")):
            return StepIntentType.NAVIGATION
        return StepIntentType.ACTION

    def _match_step_for_catalog(
        self,
        raw_line: str,
        steps: list[StepDefinition],
    ) -> StepDefinition | None:
        keyword, line_text = self._split_gherkin_line(raw_line)
        normalized_line = self._normalize_step_text(line_text)
        if not normalized_line:
            return None

        fallback: StepDefinition | None = None
        fallback_score = 0
        for step in steps:
            if keyword is not None and keyword not in {StepKeyword.AND, StepKeyword.BUT}:
                if step.keyword not in {keyword, StepKeyword.AND, StepKeyword.BUT}:
                    continue
            if self._line_matches_step(step, line_text):
                return step
            score = self._token_overlap_score(
                normalized_line,
                self._normalize_step_text(step.pattern),
            )
            if score > fallback_score:
                fallback = step
                fallback_score = score
        return fallback if fallback_score >= 0.8 else None

    def _line_matches_step(self, step: StepDefinition, line_text: str) -> bool:
        regex = step.regex or (
            cucumber_expression_to_regex(step.pattern)
            if step.pattern_type is StepPatternType.CUCUMBER_EXPRESSION
            else step.pattern
        )
        try:
            if regex and re.search(regex, line_text):
                return True
        except re.error:
            pass
        return self._normalize_step_text(step.pattern) == self._normalize_step_text(line_text)

    def _scenario_alias_candidate(self, raw_line: str, step: StepDefinition) -> str | None:
        _, line_text = self._split_gherkin_line(raw_line)
        normalized_line = self._normalize_step_text(line_text)
        normalized_pattern = self._normalize_step_text(step.pattern)
        if not normalized_line or normalized_line == normalized_pattern:
            return None
        return line_text.strip()

    @staticmethod
    def _split_gherkin_line(line: str) -> tuple[StepKeyword | None, str]:
        match = re.match(
            r"^\s*(Given|When|Then|And|But|Дано|Когда|Тогда|И|Но)\b\s*(?P<text>.+)$",
            line.strip(),
            flags=re.IGNORECASE,
        )
        if not match:
            return None, line.strip()
        keyword_raw = match.group(1)
        return StepKeyword.from_string(keyword_raw), match.group("text").strip()

    @staticmethod
    def _normalize_step_text(text: str) -> str:
        return " ".join(re.findall(r"\w+", (text or "").casefold()))

    @staticmethod
    def _token_overlap_score(left: str, right: str) -> float:
        left_tokens = {token for token in left.split() if token}
        right_tokens = {token for token in right.split() if token}
        if not left_tokens or not right_tokens:
            return 0.0
        union = len(left_tokens | right_tokens)
        if union == 0:
            return 0.0
        return len(left_tokens & right_tokens) / union

    def _enrich_step_with_llm(self, step: StepDefinition) -> None:
        """Adds compact summary/examples with LLM where available."""

        summary_prompt = (
            "Сформулируй краткое назначение cucumber-шага на основе аннотации."
            " Верни одно предложение без лишних слов.\n"
            f"Ключевое слово: {step.keyword.value}.\n"
            f"Паттерн шага: {step.pattern}.\n"
            f"Тип паттерна: {step.pattern_type.value}.\n"
            f"Параметры: {', '.join(param.name for param in step.parameters) or 'нет'}."
        )

        examples_prompt = (
            "Приведи 2-3 строки Gherkin, подходящие под аннотацию шага"
            " (без номеров и лишних комментариев)."
            f" Используй язык шага: {step.language or 'как в исходнике'}.\n"
            f"Ключевое слово: {step.keyword.value}. Паттерн: {step.pattern}."
        )

        try:
            raw_summary = self.llm_client.generate(summary_prompt)
            step.summary = (raw_summary or "").strip() or step.summary
            step.doc_summary = step.summary
        except Exception as exc:  # pragma: no cover
            logger.warning("[RepoScannerAgent] Failed to fetch summary from LLM: %s", exc)

        try:
            raw_examples = self.llm_client.generate(examples_prompt)
            parsed_examples = self._parse_examples(raw_examples)
            if parsed_examples:
                step.examples = parsed_examples
        except Exception as exc:  # pragma: no cover
            logger.warning("[RepoScannerAgent] Failed to fetch examples from LLM: %s", exc)

    @staticmethod
    def _parse_examples(raw: str) -> list[str]:
        """Extracts examples from LLM output."""

        if not raw:
            return []

        cleaned = raw.replace("\r", "\n")
        lines = [line.strip(" -•\t") for line in cleaned.splitlines()]
        examples = [line for line in lines if line]

        if len(examples) == 1:
            try:
                data = json.loads(examples[0])
                if isinstance(data, list):
                    return [str(item).strip() for item in data if str(item).strip()]
            except json.JSONDecodeError:
                pass

        return examples


__all__ = ["RepoScannerAgent"]

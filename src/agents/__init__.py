"""Агенты и фабрики для работы с сервисом."""
from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from domain.enums import MatchStatus, StepKeyword, StepPatternType
from domain.models import (
    FeatureFile,
    FeatureScenario,
    MatchedStep,
    Scenario,
    StepDefinition,
    StepImplementation,
    StepParameter,
    TestStep,
)
from infrastructure.embeddings_store import EmbeddingsStore
from infrastructure.gigachat_adapter import GigaChatAdapter
from memory import MemoryRepository, MemoryService
from infrastructure.step_index_store import StepIndexStore
from integrations.jira_testcase_provider import JiraTestcaseProvider
from tools.cucumber_expression import cucumber_expression_to_regex
from tools.feature_generator import FeatureGenerator
from tools.step_matcher import StepMatcherConfig

logger = logging.getLogger(__name__)


def _serialize_step_definition(step: StepDefinition) -> dict[str, Any]:
    data = asdict(step)
    data["keyword"] = step.keyword.value
    data["pattern_type"] = step.pattern_type.value
    return data


def _serialize_test_step(test_step: TestStep) -> dict[str, Any]:
    return asdict(test_step)


def _serialize_scenario(scenario: Scenario) -> dict[str, Any]:
    return {
        "name": scenario.name,
        "description": scenario.description,
        "preconditions": [_serialize_test_step(step) for step in scenario.preconditions],
        "steps": [_serialize_test_step(step) for step in scenario.steps],
        "expected_result": scenario.expected_result,
        "tags": list(scenario.tags),
    }


def _serialize_matched_step(matched: MatchedStep) -> dict[str, Any]:
    return {
        "test_step": _serialize_test_step(matched.test_step),
        "status": matched.status.value,
        "step_definition": _serialize_step_definition(matched.step_definition)
        if matched.step_definition
        else None,
        "confidence": matched.confidence,
        "generated_gherkin_line": matched.generated_gherkin_line,
        "resolved_step_text": matched.resolved_step_text,
        "matched_parameters": matched.matched_parameters,
        "parameter_fill_meta": matched.parameter_fill_meta,
        "notes": matched.notes,
    }


def _deserialize_test_step(data: dict[str, Any]) -> TestStep:
    return TestStep(
        order=int(data.get("order", 0)),
        text=data.get("text", ""),
        section=data.get("section"),
    )


def _deserialize_scenario(data: dict[str, Any]) -> Scenario:
    return Scenario(
        name=data.get("name", ""),
        description=data.get("description"),
        preconditions=[_deserialize_test_step(step) for step in data.get("preconditions", [])],
        steps=[_deserialize_test_step(step) for step in data.get("steps", [])],
        expected_result=data.get("expected_result"),
        tags=list(data.get("tags", []) or []),
    )


def _deserialize_step_definition(data: dict[str, Any]) -> StepDefinition:
    pattern = data.get("pattern", "")
    pattern_type = StepPatternType(
        data.get("pattern_type", StepPatternType.CUCUMBER_EXPRESSION.value)
    )
    regex = data.get("regex")
    if not regex:
        regex = (
            cucumber_expression_to_regex(pattern)
            if pattern_type is StepPatternType.CUCUMBER_EXPRESSION
            else pattern
        )
    return StepDefinition(
        id=data.get("id", ""),
        keyword=StepKeyword(data.get("keyword", StepKeyword.GIVEN.value)),
        pattern=pattern,
        regex=regex,
        code_ref=data.get("code_ref", ""),
        pattern_type=pattern_type,
        parameters=[StepParameter(**param) for param in data.get("parameters", [])],
        tags=list(data.get("tags", []) or []),
        language=data.get("language"),
        implementation=StepImplementation(**data["implementation"])
        if data.get("implementation")
        else None,
        summary=data.get("summary"),
        examples=list(data.get("examples", []) or []),
    )


def _deserialize_matched_step(data: dict[str, Any]) -> MatchedStep:
    return MatchedStep(
        test_step=_deserialize_test_step(data.get("test_step", {})),
        status=MatchStatus(data.get("status", MatchStatus.UNMATCHED.value)),
        step_definition=_deserialize_step_definition(data["step_definition"])
        if data.get("step_definition")
        else None,
        confidence=data.get("confidence"),
        generated_gherkin_line=data.get("generated_gherkin_line"),
        resolved_step_text=data.get("resolved_step_text"),
        matched_parameters=list(data.get("matched_parameters", []) or []),
        parameter_fill_meta=data.get("parameter_fill_meta"),
        notes=data.get("notes"),
    )


def _serialize_feature(feature: FeatureFile, rendered_text: str | None = None) -> dict[str, Any]:
    return {
        "name": feature.name,
        "description": feature.description,
        "language": feature.language,
        "tags": list(feature.tags),
        "background_steps": list(feature.background_steps),
        "scenarios": [asdict(scenario) for scenario in feature.scenarios],
        "rendered": rendered_text,
    }


def create_orchestrator(settings: Settings | None = None):
    """Создаёт собранный оркестратор со всеми зависимостями."""

    from agents.feature_builder_agent import FeatureBuilderAgent
    from agents.orchestrator import Orchestrator
    from agents.repo_scanner_agent import RepoScannerAgent
    from agents.step_matcher_agent import StepMatcherAgent
    from agents.testcase_parser_agent import TestcaseParserAgent

    resolved_settings = settings or get_settings()
    corp_proxy_url = None
    if resolved_settings.corp_mode:
        corp_proxy_url = (
            f"{resolved_settings.corp_proxy_host}{resolved_settings.corp_proxy_path}"
            if resolved_settings.corp_proxy_host
            else None
        )
    credentials_provided = bool(
        resolved_settings.corp_mode
        and resolved_settings.corp_proxy_host
        and resolved_settings.corp_cert_file
        and resolved_settings.corp_key_file
    ) or bool(
        resolved_settings.llm_api_key
        or (
            resolved_settings.gigachat_client_id and resolved_settings.gigachat_client_secret
        )
    )
    llm_client = GigaChatAdapter(
        base_url=resolved_settings.gigachat_api_url,
        auth_url=resolved_settings.gigachat_auth_url,
        credentials=resolved_settings.llm_api_key,
        client_id=resolved_settings.gigachat_client_id,
        client_secret=resolved_settings.gigachat_client_secret,
        model_name=resolved_settings.corp_model if resolved_settings.corp_mode else (resolved_settings.llm_model or "GigaChat"),
        scope=resolved_settings.gigachat_scope,
        verify_ssl_certs=resolved_settings.gigachat_verify_ssl,
        allow_fallback=not credentials_provided,
        corp_mode=resolved_settings.corp_mode,
        corp_proxy_url=corp_proxy_url,
        cert_file=resolved_settings.corp_cert_file,
        key_file=resolved_settings.corp_key_file,
        ca_bundle_file=resolved_settings.corp_ca_bundle_file,
        request_timeout_s=resolved_settings.corp_request_timeout_s,
        corp_retry_attempts=resolved_settings.corp_retry_attempts,
        corp_retry_base_delay_s=resolved_settings.corp_retry_base_delay_s,
        corp_retry_max_delay_s=resolved_settings.corp_retry_max_delay_s,
        corp_retry_jitter_s=resolved_settings.corp_retry_jitter_s,
    )
    step_index_store = StepIndexStore(resolved_settings.steps_index_dir)
    embeddings_store = EmbeddingsStore()
    memory_service = MemoryService(
        MemoryRepository(Path(resolved_settings.steps_index_dir).parent / "learning_memory")
    )

    logger.debug("LLMClient и хранилища инициализированы")

    agent_llm_client = llm_client if credentials_provided else None

    repo_scanner = RepoScannerAgent(step_index_store, embeddings_store, agent_llm_client)
    testcase_parser = TestcaseParserAgent(agent_llm_client)
    step_matcher = StepMatcherAgent(
        step_index_store,
        embeddings_store,
        agent_llm_client,
        project_learning_store=memory_service,
        matcher_config=StepMatcherConfig(
            retrieval_top_k=resolved_settings.match_retrieval_top_k,
            candidate_pool=resolved_settings.match_candidate_pool,
            threshold_exact=resolved_settings.match_threshold_exact,
            threshold_fuzzy=resolved_settings.match_threshold_fuzzy,
            min_seq_for_exact=resolved_settings.match_min_seq_for_exact,
            ambiguity_gap=resolved_settings.match_ambiguity_gap,
            llm_min_score=resolved_settings.match_llm_min_score,
            llm_max_score=resolved_settings.match_llm_max_score,
            llm_shortlist=resolved_settings.match_llm_shortlist,
            llm_min_confidence=resolved_settings.match_llm_min_confidence,
        ),
    )
    feature_generator = FeatureBuilderAgent(agent_llm_client)
    jira_testcase_provider = JiraTestcaseProvider(resolved_settings)

    orchestrator = Orchestrator(
        repo_scanner_agent=repo_scanner,
        testcase_parser_agent=testcase_parser,
        step_matcher_agent=step_matcher,
        feature_builder_agent=feature_generator,
        step_index_store=step_index_store,
        embeddings_store=embeddings_store,
        project_learning_store=memory_service,
        llm_client=llm_client,
        jira_testcase_provider=jira_testcase_provider,
    )
    return orchestrator


__all__ = [
    "create_orchestrator",
    "_serialize_feature",
    "_serialize_matched_step",
    "_serialize_scenario",
    "_serialize_step_definition",
    "_serialize_test_step",
    "_deserialize_matched_step",
    "_deserialize_scenario",
    "_deserialize_step_definition",
    "_deserialize_test_step",
]

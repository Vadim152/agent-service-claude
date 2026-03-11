"""Pydantic-СЃС…РµРјС‹ Р·Р°РїСЂРѕСЃРѕРІ Рё РѕС‚РІРµС‚РѕРІ РґР»СЏ HTTP API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from domain.enums import ScenarioType, StepIntentType, StepKeyword, StepPatternType


def _to_camel(value: str) -> str:
    """РџСЂРµРѕР±СЂР°Р·СѓРµС‚ snake_case РІ camelCase РґР»СЏ JSON."""

    parts = value.split("_")
    return parts[0] + "".join(word.capitalize() for word in parts[1:])


class ApiBaseModel(BaseModel):
    """Р‘Р°Р·РѕРІР°СЏ РјРѕРґРµР»СЊ РґР»СЏ API СЃРѕ СЃС‚РёР»РµРј camelCase Рё populate_by_name."""

    model_config = ConfigDict(
        alias_generator=_to_camel, populate_by_name=True, from_attributes=True
    )


class StepParameterDto(ApiBaseModel):
    """РЎС‚СЂСѓРєС‚СѓСЂРёСЂРѕРІР°РЅРЅРѕРµ РѕРїРёСЃР°РЅРёРµ РїР°СЂР°РјРµС‚СЂР° С€Р°РіР°."""

    name: str = Field(..., description="РРјСЏ РїР°СЂР°РјРµС‚СЂР° РёР· СЃРёРіРЅР°С‚СѓСЂС‹ С€Р°РіР°")
    type: str | None = Field(
        default=None, description="РўРёРї РїР°СЂР°РјРµС‚СЂР° (РЅР°РїСЂРёРјРµСЂ, string/int/object)"
    )
    placeholder: str | None = Field(
        default=None,
        description="РСЃС…РѕРґРЅС‹Р№ placeholder РёР»Рё СЂРµРіСѓР»СЏСЂРЅРѕРµ РІС‹СЂР°Р¶РµРЅРёРµ РёР· РїР°С‚С‚РµСЂРЅР°",
    )


class StepImplementationDto(ApiBaseModel):
    """РРЅС„РѕСЂРјР°С†РёСЏ РѕР± РёСЃС…РѕРґРЅРѕРј С„Р°Р№Р»Рµ Рё РјРµС‚РѕРґРµ, СЂРµР°Р»РёР·СѓСЋС‰РµРј С€Р°Рі."""

    file: str | None = Field(default=None, description="РџСѓС‚СЊ Рє С„Р°Р№Р»Сѓ СЃ СЂРµР°Р»РёР·Р°С†РёРµР№")
    line: int | None = Field(default=None, description="РќРѕРјРµСЂ СЃС‚СЂРѕРєРё Р°РЅРЅРѕС‚Р°С†РёРё С€Р°РіР°")
    class_name: str | None = Field(
        default=None, alias="className", description="РРјСЏ РєР»Р°СЃСЃР°, РµСЃР»Рё РїСЂРёРјРµРЅРёРјРѕ"
    )
    method_name: str | None = Field(
        default=None, alias="methodName", description="РРјСЏ РјРµС‚РѕРґР°, РµСЃР»Рё РїСЂРёРјРµРЅРёРјРѕ"
    )


class StepDefinitionDto(ApiBaseModel):
    """РЈРїСЂРѕС‰С‘РЅРЅРѕРµ РїСЂРµРґСЃС‚Р°РІР»РµРЅРёРµ StepDefinition РґР»СЏ РѕС‚РґР°С‡Рё РІ API."""

    id: str = Field(..., description="РЈРЅРёРєР°Р»СЊРЅС‹Р№ РёРґРµРЅС‚РёС„РёРєР°С‚РѕСЂ С€Р°РіР°")
    keyword: StepKeyword = Field(
        ..., description="РљР»СЋС‡РµРІРѕРµ СЃР»РѕРІРѕ С€Р°РіР° (Given/When/Then/And/But)"
    )
    pattern: str = Field(..., description="РџР°С‚С‚РµСЂРЅ С€Р°РіР° РёР· Р°РЅРЅРѕС‚Р°С†РёРё")
    pattern_type: StepPatternType = Field(
        default=StepPatternType.CUCUMBER_EXPRESSION,
        alias="patternType",
        description="РўРёРї РїР°С‚С‚РµСЂРЅР°: cucumberExpression РёР»Рё regularExpression",
    )
    regex: str | None = Field(
        default=None,
        description="Р РµРіСѓР»СЏСЂРЅРѕРµ РІС‹СЂР°Р¶РµРЅРёРµ С€Р°РіР°, РµСЃР»Рё РѕРЅРѕ РµСЃС‚СЊ РІ РёСЃС…РѕРґРЅРёРєРµ",
    )
    code_ref: str = Field(..., alias="codeRef", description="РЎСЃС‹Р»РєР° РЅР° РёСЃС…РѕРґРЅС‹Р№ РєРѕРґ")
    parameters: list[StepParameterDto] = Field(
        default_factory=list,
        description="РЎРїРёСЃРѕРє РїР°СЂР°РјРµС‚СЂРѕРІ С€Р°РіР° СЃ С‚РёРїР°РјРё Рё РїР»РµР№СЃС…РѕР»РґРµСЂР°РјРё",
    )
    tags: list[str] | None = Field(
        default=None, description="РўРµРіРё С€Р°РіР° РёР· РёСЃС…РѕРґРЅРёРєР°, РµСЃР»Рё РµСЃС‚СЊ"
    )
    language: str | None = Field(
        default=None, description="РЇР·С‹Рє С€Р°РіР° РІ РёСЃС…РѕРґРЅРёРєРµ (ru/en Рё С‚.Рґ.)"
    )
    implementation: StepImplementationDto | None = Field(
        default=None,
        description="РџРѕРґСЂРѕР±РЅРѕСЃС‚Рё Рѕ С„Р°Р№Р»Рµ, СЃС‚СЂРѕРєРµ Рё РјРµС‚РѕРґРµ, СЂРµР°Р»РёР·СѓСЋС‰РµРј С€Р°Рі",
    )
    summary: str | None = Field(
        default=None, description="РљСЂР°С‚РєРѕРµ РѕРїРёСЃР°РЅРёРµ С€Р°РіР° РёР· РґРѕРєСѓРјРµРЅС‚Р°С†РёРё"
    )
    doc_summary: str | None = Field(
        default=None,
        alias="docSummary",
        description="Р РµР·СЋРјРµ С€Р°РіР°, РѕР±РѕРіР°С‰РµРЅРЅРѕРµ LLM РёР»Рё РґРѕРєСѓРјРµРЅС‚Р°С†РёРµР№",
    )
    examples: list[str] = Field(
        default_factory=list,
        description="РџСЂРёРјРµСЂС‹ РёСЃРїРѕР»СЊР·РѕРІР°РЅРёСЏ С€Р°РіР° РёР· РєРѕРјРјРµРЅС‚Р°СЂРёРµРІ РёР»Рё РґРѕРєСѓРјРµРЅС‚Р°С†РёРё",
    )
    step_type: StepIntentType | None = Field(default=None, alias="stepType")
    usage_count: int = Field(default=0, alias="usageCount")
    linked_scenario_ids: list[str] = Field(default_factory=list, alias="linkedScenarioIds")
    sample_scenario_refs: list[str] = Field(default_factory=list, alias="sampleScenarioRefs")
    aliases: list[str] = Field(default_factory=list)
    domain: str | None = None


class CanonicalStepDto(ApiBaseModel):
    order: int
    text: str
    intent_type: StepIntentType = Field(..., alias="intentType")
    source: str
    origin: str
    confidence: float = 1.0
    normalized_from: str | None = Field(default=None, alias="normalizedFrom")
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalTestCaseDto(ApiBaseModel):
    title: str
    preconditions: list[CanonicalStepDto] = Field(default_factory=list)
    actions: list[CanonicalStepDto] = Field(default_factory=list)
    expected_results: list[CanonicalStepDto] = Field(default_factory=list, alias="expectedResults")
    test_data: list[str] = Field(default_factory=list, alias="testData")
    tags: list[str] = Field(default_factory=list)
    scenario_type: ScenarioType = Field(default=ScenarioType.STANDARD, alias="scenarioType")
    source: str | None = None


class BindingCandidateDto(ApiBaseModel):
    step_id: str = Field(..., alias="stepId")
    step_text: str = Field(..., alias="stepText")
    status: str
    confidence: float
    reason: str | None = None
    source: str | None = None


class BindingOverrideDto(ApiBaseModel):
    order: int | None = None
    text: str | None = None
    step_id: str = Field(..., alias="stepId")


class GenerationPlanItemDto(ApiBaseModel):
    order: int
    text: str
    intent_type: StepIntentType = Field(..., alias="intentType")
    section: str
    keyword: StepKeyword
    binding_candidates: list[BindingCandidateDto] = Field(default_factory=list, alias="bindingCandidates")
    selected_step_id: str | None = Field(default=None, alias="selectedStepId")
    selected_confidence: float | None = Field(default=None, alias="selectedConfidence")
    warning: str | None = None


class ScenarioCatalogDto(ApiBaseModel):
    id: str
    name: str
    feature_path: str = Field(..., alias="featurePath")
    scenario_name: str = Field(..., alias="scenarioName")
    tags: list[str] = Field(default_factory=list)
    background_steps: list[str] = Field(default_factory=list, alias="backgroundSteps")
    steps: list[str] = Field(default_factory=list)
    scenario_type: ScenarioType = Field(default=ScenarioType.STANDARD, alias="scenarioType")
    document: str | None = None
    description: str | None = None


class SimilarScenarioDto(ApiBaseModel):
    scenario_id: str = Field(..., alias="scenarioId")
    name: str
    feature_path: str = Field(..., alias="featurePath")
    score: float
    matched_fragments: list[str] = Field(default_factory=list, alias="matchedFragments")
    background_steps: list[str] = Field(default_factory=list, alias="backgroundSteps")
    steps: list[str] = Field(default_factory=list)
    recommended: bool = False


class GenerationPlanDto(ApiBaseModel):
    plan_id: str | None = Field(default=None, alias="planId")
    source: str = "retrieval_driven"
    recommended_scenario_id: str | None = Field(default=None, alias="recommendedScenarioId")
    selected_scenario_id: str | None = Field(default=None, alias="selectedScenarioId")
    candidate_background: list[str] = Field(default_factory=list, alias="candidateBackground")
    items: list[GenerationPlanItemDto] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    draft_feature_text: str = Field(default="", alias="draftFeatureText")


class AssumptionDto(ApiBaseModel):
    id: str
    text: str
    question: str | None = None
    category: str | None = None
    field: str | None = None


class AmbiguityIssueDto(ApiBaseModel):
    id: str
    severity: Literal["blocking", "non_blocking"]
    category: str
    field: str | None = None
    message: str
    question: str | None = None
    assumption_id: str | None = Field(default=None, alias="assumptionId")


class CanonicalIntentDto(ApiBaseModel):
    goal: str | None = None
    actor: str | None = None
    sut_area: str | None = Field(default=None, alias="sutArea")
    preconditions: list[str] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list, alias="businessRules")
    data_dimensions: list[str] = Field(default_factory=list, alias="dataDimensions")
    observable_outcomes: list[str] = Field(default_factory=list, alias="observableOutcomes")
    unknowns: list[str] = Field(default_factory=list)
    assumptions: list[AssumptionDto] = Field(default_factory=list)
    confidence: float = 0.0
    evidence_refs: list[str] = Field(default_factory=list, alias="evidenceRefs")


class ScenarioCandidateDto(ApiBaseModel):
    id: str
    type: str
    rank: int
    title: str
    rationale: str
    recommended: bool = False
    confidence: float = 0.0
    expected_outcomes: list[str] = Field(default_factory=list, alias="expectedOutcomes")
    assumption_ids: list[str] = Field(default_factory=list, alias="assumptionIds")
    evidence_refs: list[str] = Field(default_factory=list, alias="evidenceRefs")
    steps: list[str] = Field(default_factory=list)
    background_steps: list[str] = Field(default_factory=list, alias="backgroundSteps")


class EvidenceHitDto(ApiBaseModel):
    id: str
    source: str
    title: str
    score: float = 0.0
    details: dict[str, Any] = Field(default_factory=dict)


class EvidenceSummaryDto(ApiBaseModel):
    scenarios: list[EvidenceHitDto] = Field(default_factory=list)
    steps: list[EvidenceHitDto] = Field(default_factory=list)
    review_signals: list[EvidenceHitDto] = Field(default_factory=list, alias="reviewSignals")


class CoverageReportDto(ApiBaseModel):
    oracle_coverage: float = Field(default=0.0, alias="oracleCoverage")
    precondition_coverage: float = Field(default=0.0, alias="preconditionCoverage")
    data_coverage: float = Field(default=0.0, alias="dataCoverage")
    then_coverage: float = Field(default=0.0, alias="thenCoverage")
    assumption_count: int = Field(default=0, alias="assumptionCount")
    new_steps_needed_count: int = Field(default=0, alias="newStepsNeededCount")
    traceability_score: float = Field(default=0.0, alias="traceabilityScore")
    flake_risk_flags: list[str] = Field(default_factory=list, alias="flakeRiskFlags")
    blocking_issue_count: int = Field(default=0, alias="blockingIssueCount")


class UnmappedStepDto(ApiBaseModel):
    """РЁР°Рі С‚РµСЃС‚РєРµР№СЃР°, РєРѕС‚РѕСЂС‹Р№ РЅРµ СѓРґР°Р»РѕСЃСЊ СЃРѕРїРѕСЃС‚Р°РІРёС‚СЊ СЃ cucumber-С€Р°РіРѕРј."""

    text: str = Field(..., description="РўРµРєСЃС‚ РёСЃС…РѕРґРЅРѕРіРѕ С€Р°РіР° С‚РµСЃС‚РєРµР№СЃР°")
    reason: str | None = Field(
        default=None, description="РџСЂРёС‡РёРЅР° РѕС‚СЃСѓС‚СЃС‚РІРёСЏ СЃРѕРїРѕСЃС‚Р°РІР»РµРЅРёСЏ"
    )


class StepsSummaryDto(ApiBaseModel):
    """РљСЂР°С‚РєР°СЏ СЃС‚Р°С‚РёСЃС‚РёРєР° РїРѕ СЂРµР·СѓР»СЊС‚Р°С‚Р°Рј СЃРѕРїРѕСЃС‚Р°РІР»РµРЅРёСЏ С€Р°РіРѕРІ."""

    exact: int = Field(default=0, description="РљРѕР»РёС‡РµСЃС‚РІРѕ С‚РѕС‡РЅС‹С… СЃРѕРІРїР°РґРµРЅРёР№")
    fuzzy: int = Field(default=0, description="РљРѕР»РёС‡РµСЃС‚РІРѕ РЅРµСЃС‚СЂРѕРіРёС… СЃРѕРІРїР°РґРµРЅРёР№")
    unmatched: int = Field(default=0, description="РљРѕР»РёС‡РµСЃС‚РІРѕ С€Р°РіРѕРІ Р±РµР· СЃРѕРїРѕСЃС‚Р°РІР»РµРЅРёСЏ")


class QualityFailureDto(ApiBaseModel):
    code: str
    message: str
    actual: Any = None
    expected: Any = None


class QualityMetricsDto(ApiBaseModel):
    syntax_valid: bool = Field(default=False, alias="syntaxValid")
    unmatched_steps_count: int = Field(default=0, alias="unmatchedStepsCount")
    unmatched_ratio: float = Field(default=0.0, alias="unmatchedRatio")
    exact_ratio: float = Field(default=0.0, alias="exactRatio")
    fuzzy_ratio: float = Field(default=0.0, alias="fuzzyRatio")
    parameter_fill_full_ratio: float = Field(default=0.0, alias="parameterFillFullRatio")
    ambiguous_count: int = Field(default=0, alias="ambiguousCount")
    llm_reranked_count: int = Field(default=0, alias="llmRerankedCount")
    normalization_split_count: int = Field(default=0, alias="normalizationSplitCount")
    expected_result_count: int = Field(default=0, alias="expectedResultCount")
    expected_result_coverage: float = Field(default=0.0, alias="expectedResultCoverage")
    assertion_count: int = Field(default=0, alias="assertionCount")
    missing_assertion_count: int = Field(default=0, alias="missingAssertionCount")
    weak_match_count: int = Field(default=0, alias="weakMatchCount")
    logical_completeness: bool = Field(default=False, alias="logicalCompleteness")
    quality_score: int = Field(default=0, alias="qualityScore")
    oracle_coverage: float = Field(default=0.0, alias="oracleCoverage")
    precondition_coverage: float = Field(default=0.0, alias="preconditionCoverage")
    data_coverage: float = Field(default=0.0, alias="dataCoverage")
    then_coverage: float = Field(default=0.0, alias="thenCoverage")
    assumption_count: int = Field(default=0, alias="assumptionCount")
    new_steps_needed_count: int = Field(default=0, alias="newStepsNeededCount")
    traceability_score: float = Field(default=0.0, alias="traceabilityScore")
    blocking_issue_count: int = Field(default=0, alias="blockingIssueCount")
    flake_risk_flags: list[str] = Field(default_factory=list, alias="flakeRiskFlags")


class QualityReportDto(ApiBaseModel):
    policy: str = Field(default="strict")
    passed: bool = Field(default=False)
    score: int = Field(default=0)
    failures: list[QualityFailureDto] = Field(default_factory=list)
    warnings: list[QualityFailureDto] = Field(default_factory=list)
    critic_issues: list[str] = Field(default_factory=list, alias="criticIssues")
    metrics: QualityMetricsDto = Field(default_factory=QualityMetricsDto)
    coverage_report: CoverageReportDto | None = Field(default=None, alias="coverageReport")


class PipelineStepDto(ApiBaseModel):
    """РћРїРёСЃР°РЅРёРµ С€Р°РіР° РїР°Р№РїР»Р°Р№РЅР° РіРµРЅРµСЂР°С†РёРё feature."""

    stage: str = Field(..., description="РќР°Р·РІР°РЅРёРµ СЌС‚Р°РїР°")
    status: str = Field(..., description="РЎС‚Р°С‚СѓСЃ РІС‹РїРѕР»РЅРµРЅРёСЏ СЌС‚Р°РїР°")
    details: dict[str, Any] | None = Field(
        default=None, description="Р”РѕРїРѕР»РЅРёС‚РµР»СЊРЅС‹Рµ РґРµС‚Р°Р»Рё РѕР± СЌС‚Р°Рїe"
    )


class StepDetailDto(ApiBaseModel):
    """Р”РµС‚Р°Р»Рё РїРѕ РѕС‚РґРµР»СЊРЅРѕРјСѓ С€Р°РіСѓ РІ feature."""

    original_step: str = Field(..., alias="originalStep")
    generated_line: str = Field(..., alias="generatedLine")
    status: str
    binding_status: str | None = Field(default=None, alias="bindingStatus")
    evidence_refs: list[str] = Field(default_factory=list, alias="evidenceRefs")
    meta: dict[str, Any] | None = None


class ScanStepsRequest(ApiBaseModel):
    """Р—Р°РїСЂРѕСЃ РЅР° СЃРєР°РЅРёСЂРѕРІР°РЅРёРµ РїСЂРѕРµРєС‚Р° РґР»СЏ РїРѕСЃС‚СЂРѕРµРЅРёСЏ РёРЅРґРµРєСЃР° С€Р°РіРѕРІ."""

    project_root: str = Field(..., alias="projectRoot", description="РџСѓС‚СЊ Рє РїСЂРѕРµРєС‚Сѓ")
    additional_roots: list[str] = Field(
        default_factory=list,
        alias="additionalRoots",
        description="Дополнительные корни для сканирования шагов (каталоги или source jars)",
    )
    provided_steps: list[StepDefinitionDto] = Field(
        default_factory=list,
        alias="providedSteps",
        description="Шаги, собранные plugin-side из binary library roots",
    )


class ScanStepsResponse(ApiBaseModel):
    """РћС‚РІРµС‚ СЃРѕ СЃС‚Р°С‚РёСЃС‚РёРєРѕР№ РїРѕСЃР»Рµ СЃРєР°РЅРёСЂРѕРІР°РЅРёСЏ С€Р°РіРѕРІ."""

    project_root: str = Field(..., alias="projectRoot", description="РџСѓС‚СЊ Рє РїСЂРѕРµРєС‚Сѓ")
    steps_count: int = Field(..., alias="stepsCount", description="РљРѕР»РёС‡РµСЃС‚РІРѕ С€Р°РіРѕРІ")
    scenarios_count: int = Field(default=0, alias="scenariosCount")
    updated_at: datetime = Field(..., alias="updatedAt", description="Р’СЂРµРјСЏ РѕР±РЅРѕРІР»РµРЅРёСЏ")
    sample_steps: list[StepDefinitionDto] | None = Field(
        default=None,
        alias="sampleSteps",
        description="РџРµСЂРІС‹Рµ РЅР°Р№РґРµРЅРЅС‹Рµ С€Р°РіРё РґР»СЏ РїСЂРµРґРїСЂРѕСЃРјРѕС‚СЂР°",
    )
    sample_scenarios: list[ScenarioCatalogDto] = Field(
        default_factory=list,
        alias="sampleScenarios",
    )
    unmapped_steps: list[UnmappedStepDto] = Field(
        default_factory=list,
        alias="unmappedSteps",
        description="РЁР°РіРё С‚РµСЃС‚РєРµР№СЃР° Р±РµР· СЃРѕРїРѕСЃС‚Р°РІР»РµРЅРёСЏ",
    )


class GenerateFeatureOptions(ApiBaseModel):
    """РћРїС†РёРё СѓРїСЂР°РІР»РµРЅРёСЏ РіРµРЅРµСЂР°С†РёРµР№ Рё СЃРѕС…СЂР°РЅРµРЅРёРµРј .feature С„Р°Р№Р»Р°."""

    create_file: bool = Field(
        default=False, alias="createFile", description="РЎРѕР·РґР°РІР°С‚СЊ Р»Рё С„Р°Р№Р» РЅР° РґРёСЃРєРµ"
    )
    overwrite_existing: bool = Field(
        default=False,
        alias="overwriteExisting",
        description="РџРµСЂРµР·Р°РїРёСЃС‹РІР°С‚СЊ СЃСѓС‰РµСЃС‚РІСѓСЋС‰РёР№ С„Р°Р№Р»",
    )
    language: str | None = Field(
        default=None, description="Р–РµР»Р°РµРјС‹Р№ СЏР·С‹Рє Gherkin (ru/en)"
    )


class ZephyrAuthType(str, Enum):
    """РўРёРї Р°РІС‚РѕСЂРёР·Р°С†РёРё РґР»СЏ Jira/Zephyr."""

    TOKEN = "TOKEN"
    LOGIN_PASSWORD = "LOGIN_PASSWORD"


class ZephyrAuth(ApiBaseModel):
    """Р”Р°РЅРЅС‹Рµ Р°РІС‚РѕСЂРёР·Р°С†РёРё РґР»СЏ РїРѕР»СѓС‡РµРЅРёСЏ С‚РµСЃС‚РєРµР№СЃР° РёР· Jira/Zephyr."""

    auth_type: ZephyrAuthType = Field(..., alias="authType", description="РўРёРї Р°РІС‚РѕСЂРёР·Р°С†РёРё")
    token: str | None = Field(default=None, description="Token Jira/Zephyr")
    login: str | None = Field(default=None, description="Login Jira/Zephyr")
    password: str | None = Field(default=None, description="Password Jira/Zephyr")


class GenerateFeatureRequest(ApiBaseModel):
    """Р—Р°РїСЂРѕСЃ РЅР° РіРµРЅРµСЂР°С†РёСЋ .feature РЅР° РѕСЃРЅРѕРІРµ С‚РµСЃС‚РєРµР№СЃР°."""

    project_root: str = Field(..., alias="projectRoot", description="РџСѓС‚СЊ Рє РїСЂРѕРµРєС‚Сѓ")
    test_case_text: str = Field(
        ..., alias="testCaseText", description="РўРµРєСЃС‚ С‚РµСЃС‚РєРµР№СЃР°, РІСЃС‚Р°РІР»РµРЅРЅС‹Р№ РїРѕР»СЊР·РѕРІР°С‚РµР»РµРј"
    )
    target_path: str | None = Field(
        default=None,
        alias="targetPath",
        description="РџСѓС‚СЊ Рє С†РµР»РµРІРѕРјСѓ .feature РѕС‚РЅРѕСЃРёС‚РµР»СЊРЅРѕ projectRoot",
    )
    options: GenerateFeatureOptions | None = Field(
        default=None, description="РћРїС†РёРё РіРµРЅРµСЂР°С†РёРё Рё СЃРѕС…СЂР°РЅРµРЅРёСЏ С„Р°Р№Р»Р°"
    )
    zephyr_auth: ZephyrAuth | None = Field(
        default=None,
        alias="zephyrAuth",
        description="Р”Р°РЅРЅС‹Рµ Р°РІС‚РѕСЂРёР·Р°С†РёРё Jira/Zephyr РґР»СЏ РїРѕР»СѓС‡РµРЅРёСЏ С‚РµСЃС‚РєРµР№СЃР°",
    )
    jira_instance: str | None = Field(
        default=None,
        alias="jiraInstance",
        description="Jira base URL (e.g. https://jira.sberbank.ru)",
    )
    quality_policy: Literal["strict", "balanced", "lenient"] = Field(
        default="strict",
        alias="qualityPolicy",
        description="Policy for deterministic quality gate over generated feature",
    )
    plan_id: str | None = Field(default=None, alias="planId")
    selected_scenario_id: str | None = Field(default=None, alias="selectedScenarioId")
    selected_scenario_candidate_id: str | None = Field(
        default=None,
        alias="selectedScenarioCandidateId",
    )
    accepted_assumption_ids: list[str] = Field(default_factory=list, alias="acceptedAssumptionIds")
    clarifications: dict[str, str] = Field(default_factory=dict)
    binding_overrides: list[BindingOverrideDto] = Field(default_factory=list, alias="bindingOverrides")


class GenerateFeatureResponse(ApiBaseModel):
    """РћС‚РІРµС‚ СЃ СЂРµР·СѓР»СЊС‚Р°С‚Р°РјРё РіРµРЅРµСЂР°С†РёРё .feature С„Р°Р№Р»Р°."""

    feature_text: str = Field(..., alias="featureText", description="РЎРіРµРЅРµСЂРёСЂРѕРІР°РЅРЅС‹Р№ С‚РµРєСЃС‚")
    unmapped_steps: list[UnmappedStepDto] = Field(
        ..., alias="unmappedSteps", description="РЁР°РіРё Р±РµР· СЃРѕРїРѕСЃС‚Р°РІР»РµРЅРёСЏ"
    )
    unmapped: list[str] = Field(
        default_factory=list, description="РќРµ СЃРѕРїРѕСЃС‚Р°РІР»РµРЅРЅС‹Рµ С€Р°РіРё РёР· РјР°С‚С‡РµСЂР°"
    )
    used_steps: list[StepDefinitionDto] = Field(
        ..., alias="usedSteps", description="РЁР°РіРё С„СЂРµР№РјРІРѕСЂРєР°, РёСЃРїРѕР»СЊР·РѕРІР°РЅРЅС‹Рµ РІ feature"
    )
    build_stage: str | None = Field(
        default=None, alias="buildStage", description="Р­С‚Р°Рї СЃР±РѕСЂРєРё feature"
    )
    steps_summary: StepsSummaryDto | None = Field(
        default=None, alias="stepsSummary", description="РЎРІРѕРґРєР° РїРѕ СЃС‚Р°С‚СѓСЃР°Рј С€Р°РіРѕРІ"
    )
    meta: dict[str, Any] | None = Field(
        default=None, description="Р”РѕРїРѕР»РЅРёС‚РµР»СЊРЅС‹Рµ РјРµС‚Р°РґР°РЅРЅС‹Рµ Рѕ feature"
    )
    pipeline: list[PipelineStepDto] = Field(
        default_factory=list,
        description="РџРѕСЃР»РµРґРѕРІР°С‚РµР»СЊРЅРѕСЃС‚СЊ СЌС‚Р°РїРѕРІ РїРѕСЃС‚СЂРѕРµРЅРёСЏ feature",
    )
    step_details: list[StepDetailDto] = Field(
        default_factory=list,
        alias="stepDetails",
        description="Р”РµС‚Р°Р»Рё РіРµРЅРµСЂР°С†РёРё РїРѕ РєР°Р¶РґРѕРјСѓ С€Р°РіСѓ",
    )
    parameter_fill_summary: dict[str, int] = Field(
        default_factory=dict,
        alias="parameterFillSummary",
        description="РЎРІРѕРґРєР° РєР°С‡РµСЃС‚РІР° Р·Р°РїРѕР»РЅРµРЅРёСЏ РїР°СЂР°РјРµС‚СЂРѕРІ",
    )
    quality: QualityReportDto | None = Field(
        default=None,
        description="Deterministic quality evaluation and gate result",
    )
    plan_id: str | None = Field(default=None, alias="planId")
    selected_scenario_id: str | None = Field(default=None, alias="selectedScenarioId")
    selected_scenario_candidate_id: str | None = Field(default=None, alias="selectedScenarioCandidateId")
    coverage_report: CoverageReportDto | None = Field(default=None, alias="coverageReport")
    generation_blocked: bool = Field(default=False, alias="generationBlocked")
    warnings: list[str] = Field(default_factory=list)


class GenerationPreviewRequest(ApiBaseModel):
    project_root: str = Field(..., alias="projectRoot")
    test_case_text: str = Field(..., alias="testCaseText")
    language: str | None = None
    quality_policy: Literal["strict", "balanced", "lenient"] = Field(
        default="strict",
        alias="qualityPolicy",
    )
    selected_scenario_id: str | None = Field(default=None, alias="selectedScenarioId")
    selected_scenario_candidate_id: str | None = Field(
        default=None,
        alias="selectedScenarioCandidateId",
    )
    accepted_assumption_ids: list[str] = Field(default_factory=list, alias="acceptedAssumptionIds")
    clarifications: dict[str, str] = Field(default_factory=dict)
    binding_overrides: list[BindingOverrideDto] = Field(default_factory=list, alias="bindingOverrides")


class GenerationPreviewResponse(ApiBaseModel):
    plan_id: str | None = Field(default=None, alias="planId")
    canonical_test_case: CanonicalTestCaseDto | None = Field(default=None, alias="canonicalTestCase")
    similar_scenarios: list[SimilarScenarioDto] = Field(default_factory=list, alias="similarScenarios")
    generation_plan: GenerationPlanDto = Field(default_factory=GenerationPlanDto, alias="generationPlan")
    draft_feature_text: str = Field(default="", alias="draftFeatureText")
    quality: QualityReportDto | None = None
    canonical_intent: CanonicalIntentDto | None = Field(default=None, alias="canonicalIntent")
    ambiguity_issues: list[AmbiguityIssueDto] = Field(default_factory=list, alias="ambiguityIssues")
    scenario_candidates: list[ScenarioCandidateDto] = Field(default_factory=list, alias="scenarioCandidates")
    evidence_summary: EvidenceSummaryDto | None = Field(default=None, alias="evidenceSummary")
    coverage_report: CoverageReportDto | None = Field(default=None, alias="coverageReport")
    selected_scenario_candidate_id: str | None = Field(default=None, alias="selectedScenarioCandidateId")
    generation_blocked: bool = Field(default=False, alias="generationBlocked")
    warnings: list[str] = Field(default_factory=list)
    memory_preview: dict[str, Any] | None = Field(default=None, alias="memoryPreview")


class ApplyFeatureRequest(ApiBaseModel):
    """Р—Р°РїСЂРѕСЃ РЅР° СЃРѕС…СЂР°РЅРµРЅРёРµ .feature С„Р°Р№Р»Р° РІ СЂРµРїРѕР·РёС‚РѕСЂРёРё."""

    project_root: str = Field(..., alias="projectRoot", description="РџСѓС‚СЊ Рє РїСЂРѕРµРєС‚Сѓ")
    target_path: str = Field(
        ..., alias="targetPath", description="Р¦РµР»РµРІРѕР№ РїСѓС‚СЊ .feature РѕС‚РЅРѕСЃРёС‚РµР»СЊРЅРѕ РїСЂРѕРµРєС‚Р°"
    )
    feature_text: str = Field(..., alias="featureText", description="РЎРѕРґРµСЂР¶РёРјРѕРµ С„Р°Р№Р»Р°")
    overwrite_existing: bool = Field(
        default=False,
        alias="overwriteExisting",
        description="РџРµСЂРµР·Р°РїРёСЃС‹РІР°С‚СЊ СЃСѓС‰РµСЃС‚РІСѓСЋС‰РёР№ С„Р°Р№Р»",
    )


class ApplyFeatureResponse(ApiBaseModel):
    """РћС‚РІРµС‚ РїРѕСЃР»Рµ РїРѕРїС‹С‚РєРё Р·Р°РїРёСЃРё .feature С„Р°Р№Р»Р°."""

    project_root: str = Field(..., alias="projectRoot", description="РџСѓС‚СЊ Рє РїСЂРѕРµРєС‚Сѓ")
    target_path: str = Field(
        ..., alias="targetPath", description="Р¦РµР»РµРІРѕР№ РїСѓС‚СЊ .feature РѕС‚РЅРѕСЃРёС‚РµР»СЊРЅРѕ РїСЂРѕРµРєС‚Р°"
    )
    status: str = Field(..., description="РЎС‚Р°С‚СѓСЃ РѕРїРµСЂР°С†РёРё: created/overwritten/skipped")
    message: str | None = Field(default=None, description="Р”РѕРїРѕР»РЅРёС‚РµР»СЊРЅРѕРµ РїРѕСЏСЃРЅРµРЅРёРµ")


class ReviewLearningRequest(ApiBaseModel):
    project_root: str = Field(..., alias="projectRoot")
    plan_id: str | None = Field(default=None, alias="planId")
    target_path: str = Field(..., alias="targetPath")
    original_feature_text: str = Field(..., alias="originalFeatureText")
    edited_feature_text: str = Field(..., alias="editedFeatureText")
    overwrite_existing: bool = Field(default=False, alias="overwriteExisting")
    selected_scenario_id: str | None = Field(default=None, alias="selectedScenarioId")
    selected_scenario_candidate_id: str | None = Field(
        default=None,
        alias="selectedScenarioCandidateId",
    )
    accepted_step_ids: list[str] = Field(default_factory=list, alias="acceptedStepIds")
    rejected_step_ids: list[str] = Field(default_factory=list, alias="rejectedStepIds")
    accepted_assumption_ids: list[str] = Field(default_factory=list, alias="acceptedAssumptionIds")
    rejected_candidate_ids: list[str] = Field(default_factory=list, alias="rejectedCandidateIds")
    binding_decisions: list[dict[str, Any]] = Field(default_factory=list, alias="bindingDecisions")
    confirmed_clarifications: dict[str, str] = Field(default_factory=dict, alias="confirmedClarifications")
    binding_overrides: list[BindingOverrideDto] = Field(default_factory=list, alias="bindingOverrides")


class ReviewLearningResultDto(ApiBaseModel):
    rewrite_rules_saved: int = Field(default=0, alias="rewriteRulesSaved")
    alias_candidates_saved: int = Field(default=0, alias="aliasCandidatesSaved")
    selected_scenario_id: str | None = Field(default=None, alias="selectedScenarioId")
    selected_scenario_candidate_id: str | None = Field(
        default=None,
        alias="selectedScenarioCandidateId",
    )
    memory_updated_at: datetime | None = Field(default=None, alias="memoryUpdatedAt")


class ReviewLearningResponse(ApiBaseModel):
    plan_id: str | None = Field(default=None, alias="planId")
    file_status: ApplyFeatureResponse = Field(..., alias="fileStatus")
    quality: QualityReportDto | None = None
    learning: ReviewLearningResultDto = Field(default_factory=ReviewLearningResultDto)


class FailureClassificationDto(ApiBaseModel):
    category: str
    confidence: float
    signals: list[str] = Field(default_factory=list)
    summary: str | None = None


class RemediationActionDto(ApiBaseModel):
    action: str
    strategy: str
    safe: bool = True
    notes: str | None = None


class IncidentReportDto(ApiBaseModel):
    run_id: str = Field(..., alias="runId")
    attempt_id: str = Field(..., alias="attemptId")
    source: str
    summary: str
    hypotheses: list[str] = Field(default_factory=list)


class RunAttemptDto(ApiBaseModel):
    attempt_id: str = Field(..., alias="attemptId")
    status: str
    started_at: datetime | None = Field(default=None, alias="startedAt")
    finished_at: datetime | None = Field(default=None, alias="finishedAt")
    classification: FailureClassificationDto | None = None
    remediation: RemediationActionDto | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)


class FeatureResultDto(ApiBaseModel):
    feature_text: str = Field(default="", alias="featureText")
    unmapped_steps: list[UnmappedStepDto] = Field(default_factory=list, alias="unmappedSteps")
    unmapped: list[str] = Field(default_factory=list)
    used_steps: list[StepDefinitionDto] = Field(default_factory=list, alias="usedSteps")
    build_stage: str | None = Field(default=None, alias="buildStage")
    steps_summary: StepsSummaryDto | None = Field(default=None, alias="stepsSummary")
    meta: dict[str, Any] | None = None
    pipeline: list[PipelineStepDto] = Field(default_factory=list)
    step_details: list[StepDetailDto] = Field(default_factory=list, alias="stepDetails")
    parameter_fill_summary: dict[str, int] = Field(
        default_factory=dict, alias="parameterFillSummary"
    )
    file_status: dict[str, Any] | None = Field(default=None, alias="fileStatus")
    quality: QualityReportDto | None = Field(default=None)
    plan_id: str | None = Field(default=None, alias="planId")
    selected_scenario_id: str | None = Field(default=None, alias="selectedScenarioId")
    selected_scenario_candidate_id: str | None = Field(default=None, alias="selectedScenarioCandidateId")
    coverage_report: CoverageReportDto | None = Field(default=None, alias="coverageReport")
    generation_blocked: bool = Field(default=False, alias="generationBlocked")
    warnings: list[str] = Field(default_factory=list)


class LlmTestRequest(ApiBaseModel):
    """Р—Р°РїСЂРѕСЃ РЅР° С‚РµСЃС‚РѕРІС‹Р№ РІС‹Р·РѕРІ LLM."""

    prompt: str = Field(
        default="Ping from agent-service: please confirm connectivity.",
        description="РџСЂРѕРјРїС‚, РєРѕС‚РѕСЂС‹Р№ Р±СѓРґРµС‚ РѕС‚РїСЂР°РІР»РµРЅ РІ LLM",
    )


class LlmTestResponse(ApiBaseModel):
    """РћС‚РІРµС‚ РЅР° С‚РµСЃС‚РѕРІС‹Р№ РІС‹Р·РѕРІ LLM."""

    prompt: str = Field(..., description="РћС‚РїСЂР°РІР»РµРЅРЅС‹Р№ РїСЂРѕРјРїС‚")
    reply: str = Field(..., description="РћС‚РІРµС‚ LLM РЅР° С‚РµСЃС‚РѕРІС‹Р№ Р·Р°РїСЂРѕСЃ")
    provider: str | None = Field(default=None, description="РРјСЏ РїСЂРѕРІР°Р№РґРµСЂР° LLM")
    model: str | None = Field(default=None, description="РСЃРїРѕР»СЊР·СѓРµРјР°СЏ РјРѕРґРµР»СЊ LLM")


class MemoryFeedbackRequest(ApiBaseModel):
    project_root: str = Field(..., alias="projectRoot")
    step_id: str = Field(..., alias="stepId")
    accepted: bool
    note: str | None = None
    preference_key: str | None = Field(default=None, alias="preferenceKey")
    preference_value: Any = Field(default=None, alias="preferenceValue")


class MemoryFeedbackResponse(ApiBaseModel):
    project_root: str = Field(..., alias="projectRoot")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")
    step_boosts: dict[str, float] = Field(default_factory=dict, alias="stepBoosts")
    feedback_count: int = Field(default=0, alias="feedbackCount")


class GenerationRuleConditionDto(ApiBaseModel):
    jira_key_pattern: str | None = Field(default=None, alias="jiraKeyPattern")
    language_in: list[Literal["ru", "en"]] = Field(default_factory=list, alias="languageIn")
    quality_policy_in: list[Literal["strict", "balanced", "lenient"]] = Field(
        default_factory=list,
        alias="qualityPolicyIn",
    )
    text_regex: str | None = Field(default=None, alias="textRegex")


class GenerationRuleActionsDto(ApiBaseModel):
    quality_policy: Literal["strict", "balanced", "lenient"] | None = Field(
        default=None,
        alias="qualityPolicy",
    )
    language: Literal["ru", "en"] | None = None
    target_path_template: str | None = Field(default=None, alias="targetPathTemplate")
    apply_templates: list[str] = Field(default_factory=list, alias="applyTemplates")


class GenerationRuleDto(ApiBaseModel):
    id: str
    name: str
    enabled: bool = True
    priority: int = 100
    source: str = "api"
    condition: GenerationRuleConditionDto = Field(default_factory=GenerationRuleConditionDto)
    actions: GenerationRuleActionsDto = Field(default_factory=GenerationRuleActionsDto)
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")


class GenerationRuleCreateRequest(ApiBaseModel):
    project_root: str = Field(..., alias="projectRoot")
    name: str
    enabled: bool = True
    priority: int = 100
    source: str = "api"
    condition: GenerationRuleConditionDto = Field(default_factory=GenerationRuleConditionDto)
    actions: GenerationRuleActionsDto = Field(default_factory=GenerationRuleActionsDto)


class GenerationRulePatchRequest(ApiBaseModel):
    project_root: str = Field(..., alias="projectRoot")
    name: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    condition: GenerationRuleConditionDto | None = None
    actions: GenerationRuleActionsDto | None = None


class GenerationRuleListResponse(ApiBaseModel):
    project_root: str = Field(..., alias="projectRoot")
    items: list[GenerationRuleDto] = Field(default_factory=list)


class StepTemplateDto(ApiBaseModel):
    id: str
    name: str
    enabled: bool = True
    priority: int = 100
    source: str = "api"
    trigger_regex: str | None = Field(default=None, alias="triggerRegex")
    steps: list[str] = Field(default_factory=list)
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")


class StepTemplateCreateRequest(ApiBaseModel):
    project_root: str = Field(..., alias="projectRoot")
    name: str
    enabled: bool = True
    priority: int = 100
    source: str = "api"
    trigger_regex: str | None = Field(default=None, alias="triggerRegex")
    steps: list[str] = Field(default_factory=list)


class StepTemplatePatchRequest(ApiBaseModel):
    project_root: str = Field(..., alias="projectRoot")
    name: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    trigger_regex: str | None = Field(default=None, alias="triggerRegex")
    steps: list[str] | None = None


class StepTemplateListResponse(ApiBaseModel):
    project_root: str = Field(..., alias="projectRoot")
    items: list[StepTemplateDto] = Field(default_factory=list)


class GenerationResolvePreviewRequest(ApiBaseModel):
    project_root: str = Field(..., alias="projectRoot")
    text: str
    jira_key: str | None = Field(default=None, alias="jiraKey")
    language: Literal["ru", "en"] | None = None
    quality_policy: Literal["strict", "balanced", "lenient"] | None = Field(
        default=None,
        alias="qualityPolicy",
    )


class GenerationResolvePreviewResponse(ApiBaseModel):
    project_root: str = Field(..., alias="projectRoot")
    quality_policy: Literal["strict", "balanced", "lenient"] | None = Field(
        default=None,
        alias="qualityPolicy",
    )
    language: Literal["ru", "en"] | None = None
    target_path: str | None = Field(default=None, alias="targetPath")
    applied_rule_ids: list[str] = Field(default_factory=list, alias="appliedRuleIds")
    applied_template_ids: list[str] = Field(default_factory=list, alias="appliedTemplateIds")
    template_steps: list[str] = Field(default_factory=list, alias="templateSteps")

__all__ = [
    "AmbiguityIssueDto",
    "ApplyFeatureRequest",
    "ApplyFeatureResponse",
    "AssumptionDto",
    "BindingCandidateDto",
    "BindingOverrideDto",
    "CanonicalIntentDto",
    "CanonicalStepDto",
    "CanonicalTestCaseDto",
    "CoverageReportDto",
    "EvidenceHitDto",
    "EvidenceSummaryDto",
    "LlmTestRequest",
    "LlmTestResponse",
    "MemoryFeedbackRequest",
    "MemoryFeedbackResponse",
    "GenerationPlanDto",
    "GenerationPlanItemDto",
    "GenerationPreviewRequest",
    "GenerationPreviewResponse",
    "GenerationRuleConditionDto",
    "GenerationRuleActionsDto",
    "GenerationRuleDto",
    "GenerationRuleCreateRequest",
    "GenerationRulePatchRequest",
    "GenerationRuleListResponse",
    "StepTemplateDto",
    "StepTemplateCreateRequest",
    "StepTemplatePatchRequest",
    "StepTemplateListResponse",
    "GenerationResolvePreviewRequest",
    "GenerationResolvePreviewResponse",
    "GenerateFeatureOptions",
    "GenerateFeatureRequest",
    "GenerateFeatureResponse",
    "FailureClassificationDto",
    "FeatureResultDto",
    "ReviewLearningRequest",
    "ReviewLearningResponse",
    "ReviewLearningResultDto",
    "RemediationActionDto",
    "IncidentReportDto",
    "RunAttemptDto",
    "PipelineStepDto",
    "QualityFailureDto",
    "QualityMetricsDto",
    "QualityReportDto",
    "ScenarioCandidateDto",
    "StepDetailDto",
    "ScanStepsRequest",
    "ScanStepsResponse",
    "StepImplementationDto",
    "StepDefinitionDto",
    "StepParameterDto",
    "StepsSummaryDto",
    "ScenarioCatalogDto",
    "SimilarScenarioDto",
    "UnmappedStepDto",
]


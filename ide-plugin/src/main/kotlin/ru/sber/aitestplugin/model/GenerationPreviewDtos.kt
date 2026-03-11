package ru.sber.aitestplugin.model

import java.time.Instant

data class BindingOverrideDto(
    val order: Int? = null,
    val text: String? = null,
    val stepId: String
)

data class BindingCandidateDto(
    val stepId: String,
    val stepText: String,
    val status: String,
    val confidence: Double,
    val reason: String? = null,
    val source: String? = null
)

data class CanonicalStepDto(
    val order: Int,
    val text: String,
    val intentType: String,
    val source: String,
    val origin: String,
    val confidence: Double = 1.0,
    val normalizedFrom: String? = null,
    val metadata: Map<String, Any?> = emptyMap()
)

data class CanonicalTestCaseDto(
    val title: String,
    val preconditions: List<CanonicalStepDto> = emptyList(),
    val actions: List<CanonicalStepDto> = emptyList(),
    val expectedResults: List<CanonicalStepDto> = emptyList(),
    val testData: List<String> = emptyList(),
    val tags: List<String> = emptyList(),
    val scenarioType: String = "standard",
    val source: String? = null
)

data class SimilarScenarioDto(
    val scenarioId: String,
    val name: String,
    val featurePath: String,
    val score: Double,
    val matchedFragments: List<String> = emptyList(),
    val backgroundSteps: List<String> = emptyList(),
    val steps: List<String> = emptyList(),
    val recommended: Boolean = false
)

data class GenerationPlanItemDto(
    val order: Int,
    val text: String,
    val intentType: String,
    val section: String,
    val keyword: String,
    val bindingCandidates: List<BindingCandidateDto> = emptyList(),
    val selectedStepId: String? = null,
    val selectedConfidence: Double? = null,
    val warning: String? = null
)

data class GenerationPlanDto(
    val planId: String? = null,
    val source: String = "intent_aware",
    val recommendedScenarioId: String? = null,
    val selectedScenarioId: String? = null,
    val candidateBackground: List<String> = emptyList(),
    val items: List<GenerationPlanItemDto> = emptyList(),
    val warnings: List<String> = emptyList(),
    val confidence: Double = 0.0,
    val draftFeatureText: String = ""
)

data class AssumptionDto(
    val id: String,
    val text: String,
    val question: String? = null,
    val category: String? = null,
    val field: String? = null,
    val accepted: Boolean = false
)

data class AmbiguityIssueDto(
    val id: String,
    val severity: String,
    val category: String,
    val field: String? = null,
    val message: String,
    val question: String? = null,
    val assumptionId: String? = null
)

data class CanonicalIntentDto(
    val goal: String? = null,
    val actor: String? = null,
    val sutArea: String? = null,
    val preconditions: List<String> = emptyList(),
    val businessRules: List<String> = emptyList(),
    val dataDimensions: List<String> = emptyList(),
    val observableOutcomes: List<String> = emptyList(),
    val unknowns: List<String> = emptyList(),
    val assumptions: List<AssumptionDto> = emptyList(),
    val confidence: Double = 0.0,
    val evidenceRefs: List<String> = emptyList()
)

data class ScenarioCandidateDto(
    val id: String,
    val type: String,
    val rank: Int,
    val title: String,
    val rationale: String,
    val recommended: Boolean = false,
    val confidence: Double = 0.0,
    val expectedOutcomes: List<String> = emptyList(),
    val assumptionIds: List<String> = emptyList(),
    val evidenceRefs: List<String> = emptyList(),
    val steps: List<String> = emptyList(),
    val backgroundSteps: List<String> = emptyList()
)

data class EvidenceHitDto(
    val id: String,
    val source: String,
    val title: String,
    val score: Double = 0.0,
    val details: Map<String, Any?> = emptyMap()
)

data class EvidenceSummaryDto(
    val scenarios: List<EvidenceHitDto> = emptyList(),
    val steps: List<EvidenceHitDto> = emptyList(),
    val reviewSignals: List<EvidenceHitDto> = emptyList()
)

data class CoverageReportDto(
    val oracleCoverage: Double = 0.0,
    val preconditionCoverage: Double = 0.0,
    val dataCoverage: Double = 0.0,
    val thenCoverage: Double = 0.0,
    val assumptionCount: Int = 0,
    val newStepsNeededCount: Int = 0,
    val traceabilityScore: Double = 0.0,
    val flakeRiskFlags: List<String> = emptyList(),
    val blockingIssueCount: Int = 0
)

data class GenerationPreviewRequestDto(
    val projectRoot: String,
    val testCaseText: String,
    val language: String? = null,
    val qualityPolicy: String = "strict",
    val selectedScenarioId: String? = null,
    val selectedScenarioCandidateId: String? = null,
    val acceptedAssumptionIds: List<String> = emptyList(),
    val clarifications: Map<String, String> = emptyMap(),
    val bindingOverrides: List<BindingOverrideDto> = emptyList()
)

data class GenerationPreviewResponseDto(
    val planId: String? = null,
    val canonicalTestCase: CanonicalTestCaseDto? = null,
    val similarScenarios: List<SimilarScenarioDto> = emptyList(),
    val generationPlan: GenerationPlanDto = GenerationPlanDto(),
    val draftFeatureText: String = "",
    val quality: QualityReportDto? = null,
    val canonicalIntent: CanonicalIntentDto? = null,
    val ambiguityIssues: List<AmbiguityIssueDto> = emptyList(),
    val scenarioCandidates: List<ScenarioCandidateDto> = emptyList(),
    val evidenceSummary: EvidenceSummaryDto? = null,
    val coverageReport: CoverageReportDto? = null,
    val selectedScenarioCandidateId: String? = null,
    val generationBlocked: Boolean = false,
    val warnings: List<String> = emptyList(),
    val memoryPreview: Map<String, Any?>? = null
)

data class ReviewLearningRequestDto(
    val projectRoot: String,
    val planId: String? = null,
    val targetPath: String,
    val originalFeatureText: String,
    val editedFeatureText: String,
    val overwriteExisting: Boolean = false,
    val selectedScenarioId: String? = null,
    val selectedScenarioCandidateId: String? = null,
    val acceptedStepIds: List<String> = emptyList(),
    val rejectedStepIds: List<String> = emptyList(),
    val acceptedAssumptionIds: List<String> = emptyList(),
    val rejectedCandidateIds: List<String> = emptyList(),
    val bindingDecisions: List<Map<String, Any?>> = emptyList(),
    val confirmedClarifications: Map<String, String> = emptyMap(),
    val bindingOverrides: List<BindingOverrideDto> = emptyList()
)

data class ReviewLearningResultDto(
    val rewriteRulesSaved: Int = 0,
    val aliasCandidatesSaved: Int = 0,
    val selectedScenarioId: String? = null,
    val selectedScenarioCandidateId: String? = null,
    val memoryUpdatedAt: Instant? = null
)

data class ReviewLearningResponseDto(
    val planId: String? = null,
    val fileStatus: ApplyFeatureResponseDto,
    val quality: QualityReportDto? = null,
    val learning: ReviewLearningResultDto = ReviewLearningResultDto()
)

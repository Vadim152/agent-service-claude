package ru.sber.aitestplugin.model

data class FeatureResultDto(
    val featureText: String = "",
    val unmappedSteps: List<UnmappedStepDto> = emptyList(),
    val unmapped: List<String> = emptyList(),
    val usedSteps: List<StepDefinitionDto> = emptyList(),
    val buildStage: String? = null,
    val stepsSummary: StepsSummaryDto? = null,
    val meta: Map<String, Any?>? = null,
    val pipeline: List<Map<String, Any?>> = emptyList(),
    val stepDetails: List<Map<String, Any?>> = emptyList(),
    val parameterFillSummary: Map<String, Int> = emptyMap(),
    val fileStatus: Map<String, Any?>? = null,
    val quality: QualityReportDto? = null,
    val planId: String? = null,
    val selectedScenarioId: String? = null,
    val selectedScenarioCandidateId: String? = null,
    val coverageReport: CoverageReportDto? = null,
    val generationBlocked: Boolean = false,
    val warnings: List<String> = emptyList()
)

data class StepsSummaryDto(
    val exact: Int = 0,
    val fuzzy: Int = 0,
    val unmatched: Int = 0
)

data class QualityFailureDto(
    val code: String,
    val message: String,
    val actual: Any? = null,
    val expected: Any? = null
)

data class QualityMetricsDto(
    val syntaxValid: Boolean = false,
    val unmatchedStepsCount: Int = 0,
    val unmatchedRatio: Double = 0.0,
    val exactRatio: Double = 0.0,
    val fuzzyRatio: Double = 0.0,
    val parameterFillFullRatio: Double = 0.0,
    val ambiguousCount: Int = 0,
    val llmRerankedCount: Int = 0,
    val normalizationSplitCount: Int = 0,
    val expectedResultCount: Int = 0,
    val expectedResultCoverage: Double = 0.0,
    val assertionCount: Int = 0,
    val missingAssertionCount: Int = 0,
    val weakMatchCount: Int = 0,
    val logicalCompleteness: Boolean = false,
    val qualityScore: Int = 0,
    val oracleCoverage: Double = 0.0,
    val preconditionCoverage: Double = 0.0,
    val dataCoverage: Double = 0.0,
    val thenCoverage: Double = 0.0,
    val assumptionCount: Int = 0,
    val newStepsNeededCount: Int = 0,
    val traceabilityScore: Double = 0.0,
    val blockingIssueCount: Int = 0,
    val flakeRiskFlags: List<String> = emptyList()
)

data class QualityReportDto(
    val policy: String = "strict",
    val passed: Boolean = false,
    val score: Int = 0,
    val failures: List<QualityFailureDto> = emptyList(),
    val warnings: List<QualityFailureDto> = emptyList(),
    val criticIssues: List<String> = emptyList(),
    val metrics: QualityMetricsDto = QualityMetricsDto(),
    val coverageReport: CoverageReportDto? = null
)

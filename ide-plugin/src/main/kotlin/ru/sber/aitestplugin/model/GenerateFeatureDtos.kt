package ru.sber.aitestplugin.model

/**
 * Опции генерации .feature файла.
 */
data class GenerateFeatureOptionsDto(
    val createFile: Boolean = false,
    val overwriteExisting: Boolean = false,
    val language: String? = null
)

/**
 * Запрос на генерацию feature из текстового тесткейса.
 */
data class GenerateFeatureRequestDto(
    val projectRoot: String,
    val testCaseText: String,
    val targetPath: String? = null,
    val options: GenerateFeatureOptionsDto? = null,
    val zephyrAuth: ZephyrAuthDto? = null,
    val qualityPolicy: String = "strict"
)

/** Ответ на генерацию feature. */
data class GenerateFeatureResponseDto(
    val featureText: String,
    val unmappedSteps: List<UnmappedStepDto> = emptyList(),
    val usedSteps: List<StepDefinitionDto> = emptyList(),
    val meta: Map<String, Any?>? = emptyMap(),
    val stepDetails: List<Map<String, Any?>> = emptyList(),
    val parameterFillSummary: Map<String, Int> = emptyMap(),
    val quality: QualityReportDto? = null
)

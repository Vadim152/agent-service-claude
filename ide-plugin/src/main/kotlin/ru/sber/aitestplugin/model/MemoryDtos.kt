package ru.sber.aitestplugin.model

import java.time.Instant

data class GenerationRuleConditionDto(
    val jiraKeyPattern: String? = null,
    val languageIn: List<String> = emptyList(),
    val qualityPolicyIn: List<String> = emptyList(),
    val textRegex: String? = null
)

data class GenerationRuleActionsDto(
    val qualityPolicy: String? = null,
    val language: String? = null,
    val targetPathTemplate: String? = null,
    val applyTemplates: List<String> = emptyList()
)

data class GenerationRuleDto(
    val id: String,
    val name: String,
    val enabled: Boolean = true,
    val priority: Int = 100,
    val source: String = "api",
    val condition: GenerationRuleConditionDto = GenerationRuleConditionDto(),
    val actions: GenerationRuleActionsDto = GenerationRuleActionsDto(),
    val createdAt: Instant? = null,
    val updatedAt: Instant? = null
)

data class GenerationRuleCreateRequestDto(
    val projectRoot: String,
    val name: String,
    val enabled: Boolean = true,
    val priority: Int = 100,
    val source: String = "ide-plugin",
    val condition: GenerationRuleConditionDto = GenerationRuleConditionDto(),
    val actions: GenerationRuleActionsDto = GenerationRuleActionsDto()
)

data class GenerationRulePatchRequestDto(
    val projectRoot: String,
    val name: String? = null,
    val enabled: Boolean? = null,
    val priority: Int? = null,
    val condition: GenerationRuleConditionDto? = null,
    val actions: GenerationRuleActionsDto? = null
)

data class GenerationRuleListResponseDto(
    val projectRoot: String,
    val items: List<GenerationRuleDto> = emptyList()
)

data class StepTemplateDto(
    val id: String,
    val name: String,
    val enabled: Boolean = true,
    val priority: Int = 100,
    val source: String = "api",
    val triggerRegex: String? = null,
    val steps: List<String> = emptyList(),
    val createdAt: Instant? = null,
    val updatedAt: Instant? = null
)

data class StepTemplateCreateRequestDto(
    val projectRoot: String,
    val name: String,
    val enabled: Boolean = true,
    val priority: Int = 100,
    val source: String = "ide-plugin",
    val triggerRegex: String? = null,
    val steps: List<String> = emptyList()
)

data class StepTemplatePatchRequestDto(
    val projectRoot: String,
    val name: String? = null,
    val enabled: Boolean? = null,
    val priority: Int? = null,
    val triggerRegex: String? = null,
    val steps: List<String>? = null
)

data class StepTemplateListResponseDto(
    val projectRoot: String,
    val items: List<StepTemplateDto> = emptyList()
)

data class DeleteMemoryItemResponseDto(
    val deleted: Boolean = false,
    val ruleId: String? = null,
    val templateId: String? = null
)

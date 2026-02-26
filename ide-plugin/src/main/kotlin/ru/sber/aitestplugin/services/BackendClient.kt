package ru.sber.aitestplugin.services

import ru.sber.aitestplugin.model.ApplyFeatureRequestDto
import ru.sber.aitestplugin.model.ApplyFeatureResponseDto
import ru.sber.aitestplugin.model.ChatHistoryResponseDto
import ru.sber.aitestplugin.model.ChatCommandRequestDto
import ru.sber.aitestplugin.model.ChatCommandResponseDto
import ru.sber.aitestplugin.model.ChatMessageAcceptedResponseDto
import ru.sber.aitestplugin.model.ChatMessageRequestDto
import ru.sber.aitestplugin.model.ChatSessionCreateRequestDto
import ru.sber.aitestplugin.model.ChatSessionCreateResponseDto
import ru.sber.aitestplugin.model.ChatSessionDiffResponseDto
import ru.sber.aitestplugin.model.ChatSessionsListResponseDto
import ru.sber.aitestplugin.model.ChatSessionStatusResponseDto
import ru.sber.aitestplugin.model.ChatToolDecisionRequestDto
import ru.sber.aitestplugin.model.ChatToolDecisionResponseDto
import ru.sber.aitestplugin.model.GenerateFeatureRequestDto
import ru.sber.aitestplugin.model.GenerateFeatureResponseDto
import ru.sber.aitestplugin.model.JobCreateRequestDto
import ru.sber.aitestplugin.model.JobCreateResponseDto
import ru.sber.aitestplugin.model.JobResultResponseDto
import ru.sber.aitestplugin.model.JobStatusResponseDto
import ru.sber.aitestplugin.model.DeleteMemoryItemResponseDto
import ru.sber.aitestplugin.model.GenerationRuleCreateRequestDto
import ru.sber.aitestplugin.model.GenerationRuleDto
import ru.sber.aitestplugin.model.GenerationRuleListResponseDto
import ru.sber.aitestplugin.model.GenerationRulePatchRequestDto
import ru.sber.aitestplugin.model.ScanStepsResponseDto
import ru.sber.aitestplugin.model.StepDefinitionDto
import ru.sber.aitestplugin.model.StepTemplateCreateRequestDto
import ru.sber.aitestplugin.model.StepTemplateDto
import ru.sber.aitestplugin.model.StepTemplateListResponseDto
import ru.sber.aitestplugin.model.StepTemplatePatchRequestDto

/**
 * Абстракция клиента, обращающегося к backend-сервису agent-service.
 * Методы предполагают выполнение в фоновых задачах, чтобы не блокировать UI.
 */
interface BackendClient {
    fun scanSteps(projectRoot: String, additionalRoots: List<String> = emptyList()): ScanStepsResponseDto

    fun listSteps(projectRoot: String): List<StepDefinitionDto>

    fun generateFeature(request: GenerateFeatureRequestDto): GenerateFeatureResponseDto

    fun createJob(request: JobCreateRequestDto): JobCreateResponseDto

    fun getJob(jobId: String): JobStatusResponseDto

    fun getJobResult(jobId: String): JobResultResponseDto

    fun applyFeature(request: ApplyFeatureRequestDto): ApplyFeatureResponseDto

    fun createChatSession(request: ChatSessionCreateRequestDto): ChatSessionCreateResponseDto

    fun listChatSessions(projectRoot: String, limit: Int = 50): ChatSessionsListResponseDto

    fun sendChatMessage(sessionId: String, request: ChatMessageRequestDto): ChatMessageAcceptedResponseDto

    fun getChatHistory(sessionId: String): ChatHistoryResponseDto

    fun getChatStatus(sessionId: String): ChatSessionStatusResponseDto

    fun getChatDiff(sessionId: String): ChatSessionDiffResponseDto

    fun executeChatCommand(sessionId: String, request: ChatCommandRequestDto): ChatCommandResponseDto

    fun submitChatToolDecision(sessionId: String, request: ChatToolDecisionRequestDto): ChatToolDecisionResponseDto

    fun listGenerationRules(projectRoot: String): GenerationRuleListResponseDto

    fun createGenerationRule(request: GenerationRuleCreateRequestDto): GenerationRuleDto

    fun updateGenerationRule(ruleId: String, request: GenerationRulePatchRequestDto): GenerationRuleDto

    fun deleteGenerationRule(ruleId: String, projectRoot: String): DeleteMemoryItemResponseDto

    fun listStepTemplates(projectRoot: String): StepTemplateListResponseDto

    fun createStepTemplate(request: StepTemplateCreateRequestDto): StepTemplateDto

    fun updateStepTemplate(templateId: String, request: StepTemplatePatchRequestDto): StepTemplateDto

    fun deleteStepTemplate(templateId: String, projectRoot: String): DeleteMemoryItemResponseDto
}

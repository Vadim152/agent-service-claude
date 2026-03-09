package ru.sber.aitestplugin.services

import com.fasterxml.jackson.databind.DeserializationFeature
import com.fasterxml.jackson.databind.SerializationFeature
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import com.fasterxml.jackson.module.kotlin.readValue
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import com.intellij.openapi.project.ProjectManager
import ru.sber.aitestplugin.config.AiTestPluginSettings
import ru.sber.aitestplugin.config.AiTestPluginSettingsService
import ru.sber.aitestplugin.config.toZephyrAuthDto
import ru.sber.aitestplugin.config.toZephyrAuthHeaders
import ru.sber.aitestplugin.config.toJiraInstanceUrl
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
import ru.sber.aitestplugin.model.GenerationPreviewRequestDto
import ru.sber.aitestplugin.model.GenerationPreviewResponseDto
import ru.sber.aitestplugin.model.DeleteMemoryItemResponseDto
import ru.sber.aitestplugin.model.GenerationRuleCreateRequestDto
import ru.sber.aitestplugin.model.GenerationRuleDto
import ru.sber.aitestplugin.model.GenerationRuleListResponseDto
import ru.sber.aitestplugin.model.GenerationRulePatchRequestDto
import ru.sber.aitestplugin.model.GenerationResolvePreviewRequestDto
import ru.sber.aitestplugin.model.GenerationResolvePreviewResponseDto
import ru.sber.aitestplugin.model.ReviewLearningRequestDto
import ru.sber.aitestplugin.model.ReviewLearningResponseDto
import ru.sber.aitestplugin.model.ScanStepsRequestDto
import ru.sber.aitestplugin.model.ScanStepsResponseDto
import ru.sber.aitestplugin.model.StepDefinitionDto
import ru.sber.aitestplugin.model.StepTemplateCreateRequestDto
import ru.sber.aitestplugin.model.StepTemplateDto
import ru.sber.aitestplugin.model.StepTemplateListResponseDto
import ru.sber.aitestplugin.model.StepTemplatePatchRequestDto
import ru.sber.aitestplugin.model.RunCreateRequestDto
import ru.sber.aitestplugin.model.RunCreateResponseDto
import ru.sber.aitestplugin.model.RunArtifactsResponseDto
import ru.sber.aitestplugin.model.RunEventResponseDto
import ru.sber.aitestplugin.model.RunResultResponseDto
import ru.sber.aitestplugin.model.RunStatusResponseDto
import java.net.URI
import java.net.URLEncoder
import com.fasterxml.jackson.databind.JsonNode
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.nio.charset.StandardCharsets
import java.time.Duration
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/**
 * Реализация BackendClient, использующая HTTP вызовы к агенту.
 */
class HttpBackendClient(
    private val project: Project? = null,
    private val settingsProvider: () -> AiTestPluginSettings = {
        val resolvedProject = project
            ?: ProjectManager.getInstance().openProjects.firstOrNull()
            ?: ProjectManager.getInstance().defaultProject
        AiTestPluginSettingsService.getInstance(resolvedProject).settings
    }
) : BackendClient {
    private val terminalRunStatuses = setOf("succeeded", "failed", "needs_attention", "cancelled")
    private val terminalRunEvents = setOf("run.finished", "run.cancelled", "run.worker_failed")

    private val logger = Logger.getInstance(HttpBackendClient::class.java)

    private val mapper = jacksonObjectMapper()
        .registerModule(JavaTimeModule())
        .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
        .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
    private val clientsByTimeoutMs = ConcurrentHashMap<Int, OkHttpClient>()

    override fun scanSteps(projectRoot: String, additionalRoots: List<String>): ScanStepsResponseDto {
        val request = ScanStepsRequestDto(projectRoot = projectRoot, additionalRoots = additionalRoots)
        val encodedProjectRoot = URLEncoder.encode(projectRoot, StandardCharsets.UTF_8)
        return post("/platform/steps/scan-steps?projectRoot=$encodedProjectRoot", request)
    }

    override fun listSteps(projectRoot: String): List<StepDefinitionDto> {
        val encodedProjectRoot = URLEncoder.encode(projectRoot, StandardCharsets.UTF_8)
        return get("/platform/steps/?projectRoot=$encodedProjectRoot")
    }

    override fun generateFeature(request: GenerateFeatureRequestDto): GenerateFeatureResponseDto {
        val settings = settingsProvider()
        val zephyrAuth = settings.toZephyrAuthDto()
        val sanitizedRequest = request.copy(
            projectRoot = request.projectRoot.trim(),
            testCaseText = request.testCaseText.trim(),
            zephyrAuth = request.zephyrAuth ?: zephyrAuth
        )

        if (sanitizedRequest.projectRoot.isBlank()) {
            throw BackendException("Project root must not be empty")
        }

        if (sanitizedRequest.testCaseText.isBlank()) {
            throw BackendException("Test case text must not be empty")
        }

        return post(
            "/platform/feature/generate-feature",
            sanitizedRequest,
            timeoutMs = settings.generateFeatureTimeoutMs,
            headers = settings.toZephyrAuthHeaders()
        )
    }

    override fun createRun(request: RunCreateRequestDto): RunCreateResponseDto =
        createRun(request, idempotencyKey = UUID.randomUUID().toString())

    fun createRun(request: RunCreateRequestDto, idempotencyKey: String?): RunCreateResponseDto =
        run {
            val sanitizedRequest = request.copy(projectRoot = request.projectRoot.trim())
            if (sanitizedRequest.projectRoot.isBlank()) {
                throw BackendException("Project root must not be empty")
            }

            val headers = if (idempotencyKey.isNullOrBlank()) {
                emptyMap()
            } else {
                mapOf("Idempotency-Key" to idempotencyKey.trim())
            }
            post("/runs", sanitizedRequest, headers = headers)
        }

    override fun getRun(runId: String): RunStatusResponseDto =
        get("/runs/$runId")

    override fun getRunResult(runId: String): RunResultResponseDto =
        get("/runs/$runId/result")

    override fun listRunArtifacts(runId: String): RunArtifactsResponseDto =
        get("/runs/$runId/artifacts")

    override fun getRunArtifactContent(runId: String, artifactId: String): String {
        val encodedArtifactId = URLEncoder.encode(artifactId, StandardCharsets.UTF_8)
        return getRaw("/runs/$runId/artifacts/$encodedArtifactId/content")
    }

    fun awaitTerminalRunStatus(runId: String, timeoutMs: Int = 60_000): RunStatusResponseDto {
        val sseStatus = tryAwaitTerminalStatusViaEvents(runId, timeoutMs)
        if (sseStatus != null) {
            return sseStatus
        }

        val pollIntervalMs = 500L
        val attempts = (timeoutMs / pollIntervalMs.toInt()).coerceAtLeast(1)
        repeat(attempts) {
            val status = getRun(runId)
            if (status.status in terminalRunStatuses) {
                return status
            }
            Thread.sleep(pollIntervalMs)
        }
        return getRun(runId)
    }

    override fun applyFeature(request: ApplyFeatureRequestDto): ApplyFeatureResponseDto =
        post("/platform/feature/apply-feature", request)

    override fun previewGenerationPlan(
        request: GenerationPreviewRequestDto
    ): GenerationPreviewResponseDto = post("/platform/feature/preview-generation", request)

    override fun reviewApplyFeature(
        request: ReviewLearningRequestDto
    ): ReviewLearningResponseDto = post("/platform/feature/review-apply", request)

    override fun createChatSession(request: ChatSessionCreateRequestDto): ChatSessionCreateResponseDto =
        post("/sessions", request)

    override fun listChatSessions(projectRoot: String, limit: Int): ChatSessionsListResponseDto {
        val encodedProjectRoot = URLEncoder.encode(projectRoot, StandardCharsets.UTF_8)
        val boundedLimit = limit.coerceIn(1, 200)
        return get("/sessions?projectRoot=$encodedProjectRoot&limit=$boundedLimit")
    }

    override fun sendChatMessage(sessionId: String, request: ChatMessageRequestDto): ChatMessageAcceptedResponseDto {
        val settings = settingsProvider()
        return post("/sessions/$sessionId/messages", request, timeoutMs = settings.chatSendTimeoutMs)
    }

    override fun getChatHistory(sessionId: String): ChatHistoryResponseDto =
        get("/sessions/$sessionId/history")

    override fun getChatStatus(sessionId: String): ChatSessionStatusResponseDto =
        get("/sessions/$sessionId/status")

    override fun getChatDiff(sessionId: String): ChatSessionDiffResponseDto =
        get("/sessions/$sessionId/diff")

    override fun executeChatCommand(
        sessionId: String,
        request: ChatCommandRequestDto
    ): ChatCommandResponseDto = post("/sessions/$sessionId/commands", request)

    override fun submitChatToolDecision(
        sessionId: String,
        request: ChatToolDecisionRequestDto
    ): ChatToolDecisionResponseDto = post(
        "/policy/approvals/${request.permissionId}/decision",
        mapOf("decision" to if (request.decision.lowercase() == "reject") "deny" else "approve")
    )

    override fun listGenerationRules(projectRoot: String): GenerationRuleListResponseDto {
        val encodedProjectRoot = URLEncoder.encode(projectRoot, StandardCharsets.UTF_8)
        return get("/platform/memory/rules?projectRoot=$encodedProjectRoot")
    }

    override fun createGenerationRule(request: GenerationRuleCreateRequestDto): GenerationRuleDto =
        post("/platform/memory/rules", request)

    override fun updateGenerationRule(ruleId: String, request: GenerationRulePatchRequestDto): GenerationRuleDto =
        patch("/platform/memory/rules/$ruleId", request)

    override fun deleteGenerationRule(ruleId: String, projectRoot: String): DeleteMemoryItemResponseDto {
        val encodedProjectRoot = URLEncoder.encode(projectRoot, StandardCharsets.UTF_8)
        return delete("/platform/memory/rules/$ruleId?projectRoot=$encodedProjectRoot")
    }

    override fun listStepTemplates(projectRoot: String): StepTemplateListResponseDto {
        val encodedProjectRoot = URLEncoder.encode(projectRoot, StandardCharsets.UTF_8)
        return get("/platform/memory/templates?projectRoot=$encodedProjectRoot")
    }

    override fun createStepTemplate(request: StepTemplateCreateRequestDto): StepTemplateDto =
        post("/platform/memory/templates", request)

    override fun updateStepTemplate(templateId: String, request: StepTemplatePatchRequestDto): StepTemplateDto =
        patch("/platform/memory/templates/$templateId", request)

    override fun deleteStepTemplate(templateId: String, projectRoot: String): DeleteMemoryItemResponseDto {
        val encodedProjectRoot = URLEncoder.encode(projectRoot, StandardCharsets.UTF_8)
        return delete("/platform/memory/templates/$templateId?projectRoot=$encodedProjectRoot")
    }

    override fun resolveGenerationPreview(
        request: GenerationResolvePreviewRequestDto
    ): GenerationResolvePreviewResponseDto = post("/platform/memory/resolve-preview", request)

    private inline fun <reified T : Any> post(
        path: String,
        payload: Any,
        timeoutMs: Int? = null,
        headers: Map<String, String> = emptyMap()
    ): T {
        val settings = settingsProvider()
        val url = "${settings.backendUrl.trimEnd('/')}$path"
        val effectiveTimeoutMs = timeoutMs ?: settings.requestTimeoutMs
        val client = getHttpClient(effectiveTimeoutMs)

        val body = mapper.writeValueAsString(payload)
        val bodyBytes = body.toByteArray(StandardCharsets.UTF_8)
        val contentType = "application/json"
        val bodyLength = bodyBytes.size
        val requestBody = bodyBytes.toRequestBody(contentType.toMediaType())
        val requestBuilder = Request.Builder()
            .url(URI.create(url).toURL())
            .header("Content-Type", contentType)
            .header("X-Body-Length", bodyLength.toString())
            .post(requestBody)
        headers.forEach { (key, value) ->
            requestBuilder.header(key, value)
        }
        val request = requestBuilder.build()

        if (logger.isDebugEnabled) {
            val preview = body.take(500)
            logger.debug(
                "Sending POST to $url with Content-Type=$contentType, body size=$bodyLength bytes, preview=\"$preview\""
            )
        }

        val response = try {
            client.newCall(request).execute()
        } catch (ex: Exception) {
            if (logger.isDebugEnabled) {
                logger.debug(
                    "Failed to send POST to $url with body size=${bodyBytes.size} bytes",
                    ex
                )
            }
            throw BackendException("Failed to call $url: ${ex.message}", ex)
        }

        response.use { httpResponse ->
            val responseBody = httpResponse.body?.string().orEmpty()
            if (!httpResponse.isSuccessful) {
                if (logger.isDebugEnabled) {
                    logger.debug(
                        "Received non-2xx from $url: status=${httpResponse.code}, headers=${httpResponse.headers}, body=\"$responseBody\""
                    )
                }
                val message = parseBackendError(responseBody, httpResponse.code).also {
                    if (httpResponse.code == 422 && logger.isDebugEnabled) {
                        logger.debug("Received 422 from $url for payload: $body")
                    }
                }
                throw BackendException("Backend $url responded with ${httpResponse.code}: $message")
            }

            return try {
                mapper.readValue(responseBody)
            } catch (ex: Exception) {
                throw BackendException("Failed to parse response from $url: ${ex.message}", ex)
            }
        }
    }

    private inline fun <reified T : Any> patch(
        path: String,
        payload: Any,
        timeoutMs: Int? = null
    ): T {
        val settings = settingsProvider()
        val url = "${settings.backendUrl.trimEnd('/')}$path"
        val effectiveTimeoutMs = timeoutMs ?: settings.requestTimeoutMs
        val client = getHttpClient(effectiveTimeoutMs)

        val body = mapper.writeValueAsString(payload)
        val requestBody = body.toByteArray(StandardCharsets.UTF_8).toRequestBody("application/json".toMediaType())
        val request = Request.Builder()
            .url(URI.create(url).toURL())
            .header("Content-Type", "application/json")
            .patch(requestBody)
            .build()

        val response = try {
            client.newCall(request).execute()
        } catch (ex: Exception) {
            throw BackendException("Failed to call $url: ${ex.message}", ex)
        }

        response.use { httpResponse ->
            val responseBody = httpResponse.body?.string().orEmpty()
            if (!httpResponse.isSuccessful) {
                val message = parseBackendError(responseBody, httpResponse.code)
                throw BackendException("Backend $url responded with ${httpResponse.code}: $message")
            }
            return try {
                mapper.readValue(responseBody)
            } catch (ex: Exception) {
                throw BackendException("Failed to parse response from $url: ${ex.message}", ex)
            }
        }
    }

    private inline fun <reified T : Any> delete(
        path: String,
        timeoutMs: Int? = null
    ): T {
        val settings = settingsProvider()
        val url = "${settings.backendUrl.trimEnd('/')}$path"
        val effectiveTimeoutMs = timeoutMs ?: settings.requestTimeoutMs
        val client = getHttpClient(effectiveTimeoutMs)

        val request = Request.Builder()
            .url(URI.create(url).toURL())
            .delete()
            .build()

        val response = try {
            client.newCall(request).execute()
        } catch (ex: Exception) {
            throw BackendException("Failed to call $url: ${ex.message}", ex)
        }

        response.use { httpResponse ->
            val responseBody = httpResponse.body?.string().orEmpty()
            if (!httpResponse.isSuccessful) {
                val message = parseBackendError(responseBody, httpResponse.code)
                throw BackendException("Backend $url responded with ${httpResponse.code}: $message")
            }
            return try {
                mapper.readValue(responseBody)
            } catch (ex: Exception) {
                throw BackendException("Failed to parse response from $url: ${ex.message}", ex)
            }
        }
    }

    private inline fun <reified T : Any> get(
        path: String,
        timeoutMs: Int? = null
    ): T {
        val settings = settingsProvider()
        val url = "${settings.backendUrl.trimEnd('/')}$path"
        val effectiveTimeoutMs = timeoutMs ?: settings.requestTimeoutMs
        val client = getHttpClient(effectiveTimeoutMs)

        val request = Request.Builder()
            .url(URI.create(url).toURL())
            .get()
            .build()

        val response = try {
            client.newCall(request).execute()
        } catch (ex: Exception) {
            if (logger.isDebugEnabled) {
                logger.debug("Failed to send GET to $url", ex)
            }
            throw BackendException("Failed to call $url: ${ex.message}", ex)
        }

        response.use { httpResponse ->
            val responseBody = httpResponse.body?.string().orEmpty()
            if (!httpResponse.isSuccessful) {
                if (logger.isDebugEnabled) {
                    logger.debug(
                        "Received non-2xx from $url: status=${httpResponse.code}, headers=${httpResponse.headers}, body=\"$responseBody\""
                    )
                }
                val message = parseBackendError(responseBody, httpResponse.code)
                throw BackendException("Backend $url responded with ${httpResponse.code}: $message")
            }

            return try {
                mapper.readValue(responseBody)
            } catch (ex: Exception) {
                throw BackendException("Failed to parse response from $url: ${ex.message}", ex)
            }
        }
    }

    private fun parseValidationError(body: String): String {
        if (body.isBlank()) return "Validation failed with empty response"

        return try {
            val root = mapper.readValue<JsonNode>(body)
            val detail = root.get("detail")
            when {
                detail == null -> body
                detail.isTextual -> detail.asText()
                detail.isArray -> detail.joinToString("; ") { node ->
                    val path = node.get("loc")?.joinToString(".") { it.asText() }
                    val message = node.get("msg")?.asText() ?: node.get("type")?.asText()
                    listOfNotNull(path, message).joinToString(": ")
                }.ifBlank { body }
                else -> detail.toString()
            }
        } catch (_: Exception) {
            body
        }
    }

    private fun getHttpClient(timeoutMs: Int): OkHttpClient {
        val boundedTimeout = timeoutMs.coerceAtLeast(1)
        return clientsByTimeoutMs.computeIfAbsent(boundedTimeout) { timeout ->
            val duration = Duration.ofMillis(timeout.toLong())
            OkHttpClient.Builder()
                .callTimeout(duration)
                .connectTimeout(duration)
                .readTimeout(duration)
                .build()
        }
    }

    private fun getRaw(
        path: String,
        timeoutMs: Int? = null
    ): String {
        val settings = settingsProvider()
        val url = "${settings.backendUrl.trimEnd('/')}$path"
        val effectiveTimeoutMs = timeoutMs ?: settings.requestTimeoutMs
        val client = getHttpClient(effectiveTimeoutMs)

        val request = Request.Builder()
            .url(URI.create(url).toURL())
            .get()
            .build()

        val response = try {
            client.newCall(request).execute()
        } catch (ex: Exception) {
            throw BackendException("Failed to call $url: ${ex.message}", ex)
        }

        response.use { httpResponse ->
            val responseBody = httpResponse.body?.string().orEmpty()
            if (!httpResponse.isSuccessful) {
                val message = parseBackendError(responseBody, httpResponse.code)
                throw BackendException("Backend $url responded with ${httpResponse.code}: $message")
            }
            return responseBody
        }
    }

    private fun parseBackendError(body: String, statusCode: Int): String {
        if (body.isBlank()) return "HTTP $statusCode"

        return try {
            val root = mapper.readValue<JsonNode>(body)
            root.get("error")?.get("message")?.takeIf { it.isTextual }?.asText()
                ?: if (statusCode == 422) parseValidationError(body) else {
                    val detail = root.get("detail")
                    when {
                        detail == null -> body
                        detail.isTextual -> detail.asText()
                        detail.isObject -> detail.get("error")?.get("message")?.takeIf { it.isTextual }?.asText()
                            ?: detail.toString()
                        else -> detail.toString()
                    }
                }
        } catch (_: Exception) {
            body
        }
    }

    private fun tryAwaitTerminalStatusViaEvents(runId: String, timeoutMs: Int): RunStatusResponseDto? {
        val settings = settingsProvider()
        val encodedRunId = URLEncoder.encode(runId, StandardCharsets.UTF_8)
        val url = "${settings.backendUrl.trimEnd('/')}/runs/$encodedRunId/events?fromIndex=0"
        val client = getHttpClient(timeoutMs)
        val request = Request.Builder()
            .url(URI.create(url).toURL())
            .header("Accept", "text/event-stream")
            .get()
            .build()

        val startedAtMs = System.currentTimeMillis()
        val response = try {
            client.newCall(request).execute()
        } catch (ex: Exception) {
            logger.info("SSE stream unavailable for $runId, fallback to polling: ${ex.message}")
            return null
        }

        response.use { httpResponse ->
            if (!httpResponse.isSuccessful) {
                logger.info("SSE stream responded ${httpResponse.code} for $runId, fallback to polling")
                return null
            }

            val body = httpResponse.body ?: return null
            val source = body.source()
            var currentEventType: String? = null
            var currentData = StringBuilder()

            while (!source.exhausted()) {
                if (System.currentTimeMillis() - startedAtMs > timeoutMs) {
                    return null
                }

                val line = source.readUtf8Line() ?: continue
                if (line.isBlank()) {
                    if (currentData.isNotEmpty()) {
                        val rawData = currentData.toString().trim()
                        val eventType = currentEventType ?: ""
                        if (eventType in terminalRunEvents) {
                            try {
                                val event = mapper.readValue<RunEventResponseDto>(rawData)
                                val status = event.payload["status"]?.toString()?.trim()?.lowercase()
                                if (!status.isNullOrEmpty() && status in terminalRunStatuses) {
                                    return getRun(runId)
                                }
                            } catch (_: Exception) {
                                return getRun(runId)
                            }
                        }
                    }
                    currentEventType = null
                    currentData = StringBuilder()
                    continue
                }

                if (line.startsWith("event:")) {
                    currentEventType = line.removePrefix("event:").trim()
                    continue
                }
                if (line.startsWith("data:")) {
                    if (currentData.isNotEmpty()) {
                        currentData.append('\n')
                    }
                    currentData.append(line.removePrefix("data:").trim())
                }
            }
        }
        return null
    }
}

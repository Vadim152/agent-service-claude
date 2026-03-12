package ru.sber.aitestplugin.ui.toolwindow

import ru.sber.aitestplugin.model.ChatEventDto
import java.time.Instant

internal object AgentEventLogFormatter {
    private val ACTIVE_ACTIVITIES = setOf("busy", "retry", "waiting_permission")

    internal enum class TimelineKind {
        USER,
        AGENT_EVENT,
        ASSISTANT,
        SYSTEM
    }

    internal data class TimelineItem(
        val kind: TimelineKind,
        val text: String,
        val createdAt: Instant,
        val stableKey: String
    )

    internal enum class ExecutionPhase(val title: String) {
        THINKING("Thinking"),
        WORKING("Working"),
        RUNNING("Running"),
        APPLYING_CHANGES("Applying changes")
    }

    private enum class EventCategory(val title: String) {
        STATUS("Status"),
        STEP("Step"),
        CHANGE("Change"),
        COMMAND("Command"),
        APPROVAL("Approval")
    }

    fun buildAgentEventLines(events: List<ChatEventDto>, maxLines: Int): List<TimelineItem> {
        val compact = events
            .sortedWith(compareBy<ChatEventDto> { it.createdAt }.thenBy { it.index })
            .mapNotNull { toEventLine(it) }
            .fold(mutableListOf<CompactEvent>()) { acc, line ->
                val previous = acc.lastOrNull()
                if (previous != null && previous.category == line.category && previous.title == line.title) {
                    acc[acc.lastIndex] = previous.copy(count = previous.count + 1)
                } else {
                    acc.add(line)
                }
                acc
            }
            .takeLast(maxLines)
        return compact.map { compactLine ->
            val title = if (compactLine.count > 1) "${compactLine.title} (x${compactLine.count})" else compactLine.title
            TimelineItem(
                kind = TimelineKind.AGENT_EVENT,
                text = "[${compactLine.category.title}] $title",
                createdAt = compactLine.createdAt,
                stableKey = compactLine.stableKey
            )
        }
    }

    fun mergeConversationAndEvents(messages: List<TimelineItem>, events: List<TimelineItem>): List<TimelineItem> =
        (messages + events).sortedWith(
            compareBy<TimelineItem> { it.createdAt }
                .thenBy { orderWeight(it.kind) }
                .thenBy { it.stableKey }
        )

    fun formatPhaseProgress(activity: String, currentAction: String, retryMessage: String? = null): String? {
        val normalizedActivity = activity.lowercase()
        if (normalizedActivity !in ACTIVE_ACTIVITIES) return null
        val detail = when (normalizedActivity) {
            "retry" -> retryMessage?.trim().orEmpty().ifBlank { currentAction.trim() }
            else -> currentAction.trim()
        }
        val fallbackDetail = when (normalizedActivity) {
            "waiting_permission" -> "Awaiting approval"
            "retry" -> "Retrying"
            else -> "Working"
        }
        val effectiveDetail = detail.ifBlank { fallbackDetail }
        val phase = classifyPhase(effectiveDetail)
        return "${phase.title}: $effectiveDetail"
    }

    fun classifyPhase(detail: String): ExecutionPhase {
        val normalized = detail.lowercase()
        if (normalized.isBlank()) return ExecutionPhase.WORKING
        if (containsAny(normalized, APPLYING_KEYWORDS)) return ExecutionPhase.APPLYING_CHANGES
        if (containsAny(normalized, RUNNING_KEYWORDS)) return ExecutionPhase.RUNNING
        if (containsAny(normalized, THINKING_KEYWORDS)) return ExecutionPhase.THINKING
        if (containsAny(normalized, WORKING_KEYWORDS)) return ExecutionPhase.WORKING
        return ExecutionPhase.WORKING
    }

    private fun orderWeight(kind: TimelineKind): Int = when (kind) {
        TimelineKind.USER -> 0
        TimelineKind.AGENT_EVENT -> 1
        TimelineKind.ASSISTANT -> 2
        TimelineKind.SYSTEM -> 3
    }

    private fun toEventLine(event: ChatEventDto): CompactEvent? {
        val normalized = event.eventType.lowercase()
        val detail = resolveDetail(event.payload)
        return when (normalized) {
            "heartbeat" -> null
            "message.received",
            "run.created",
            "run.started",
            "run.queued",
            "run.succeeded" -> null
            "run.retrying" ->
                compact(EventCategory.STEP, if (detail.isNotBlank()) detail else "Retrying", event)
            "run.awaiting_approval", "permission.requested" ->
                compact(EventCategory.APPROVAL, "Approval required", event)
            "approval.decision", "permission.approved", "permission.rejected" ->
                compact(EventCategory.APPROVAL, "Approval decision sent", event)
            "run.artifact_published" ->
                compact(EventCategory.CHANGE, artifactMessage(event.payload), event)
            "session.compact.started" ->
                compact(EventCategory.COMMAND, "Compacting session", event)
            "session.compact.succeeded" ->
                compact(EventCategory.COMMAND, "Session compacted", event)
            "session.compact.failed" ->
                compact(EventCategory.STATUS, if (detail.isNotBlank()) "Compaction failed: $detail" else "Compaction failed", event)
            "command.executed" ->
                compact(EventCategory.COMMAND, commandMessage(event.payload), event)
            "run.progress" ->
                compact(EventCategory.STEP, if (detail.isNotBlank()) detail else "Working", event)
            "run.failed" ->
                compact(EventCategory.STATUS, if (detail.isNotBlank()) "Failed: $detail" else "Failed", event)
            "run.cancelled" ->
                compact(EventCategory.STATUS, "Cancelled", event)
            else -> null
        }
    }

    private fun compact(category: EventCategory, title: String, event: ChatEventDto): CompactEvent = CompactEvent(
        category = category,
        title = title,
        createdAt = event.createdAt,
        stableKey = "trace:${event.index}:${event.eventType}"
    )

    private fun artifactMessage(payload: Map<String, Any?>): String {
        val artifact = payload["artifact"] as? Map<*, *>
        val artifactName = artifact?.get("name")?.toString()?.trim().orEmpty()
        return if (artifactName.isNotBlank()) "Updated $artifactName" else "Published changes"
    }

    private fun commandMessage(payload: Map<String, Any?>): String {
        val command = payload["command"]?.toString()?.trim().orEmpty()
        return if (command.isNotBlank()) "/$command" else "Tool command executed"
    }

    private fun resolveDetail(payload: Map<String, Any?>): String {
        val message = payload["message"]?.toString()?.trim().orEmpty()
        val currentAction = payload["currentAction"]?.toString()?.trim().orEmpty()
        val nestedPayload = payload["payload"] as? Map<*, *>
        val nestedType = nestedPayload?.get("type")?.toString()?.trim().orEmpty()
        val nestedProperties = nestedPayload?.get("properties") as? Map<*, *>
        val nestedStatus = nestedProperties?.get("status") as? Map<*, *>
        val nestedStatusMessage = nestedStatus?.get("message")?.toString()?.trim().orEmpty()
        val nestedPart = nestedProperties?.get("part") as? Map<*, *>
        val nestedPartType = nestedPart?.get("type")?.toString()?.trim().orEmpty()
        val nestedPartText = nestedPart?.get("text")?.toString()?.trim().orEmpty()
        val nestedInfo = nestedPayload?.get("info") as? Map<*, *>
        val nestedError = nestedInfo?.get("error") as? Map<*, *>
        val nestedErrorData = nestedError?.get("data") as? Map<*, *>
        val nestedErrorMessage = nestedErrorData?.get("message")?.toString()?.trim().orEmpty()
        return when {
            nestedErrorMessage.isNotBlank() -> nestedErrorMessage
            nestedPartText.isNotBlank() -> nestedPartText
            nestedStatusMessage.isNotBlank() -> nestedStatusMessage
            message.isNotBlank() -> message
            currentAction.isNotBlank() -> currentAction
            nestedPartType.isNotBlank() -> nestedPartType
            else -> nestedType
        }
    }

    private data class CompactEvent(
        val category: EventCategory,
        val title: String,
        val createdAt: Instant,
        val stableKey: String,
        val count: Int = 1
    )

    private fun containsAny(value: String, markers: Set<String>): Boolean = markers.any { value.contains(it) }

    private val THINKING_KEYWORDS = setOf(
        "thinking", "think", "scan", "scanning", "read", "reading", "analyz", "search", "inspect", "index"
    )
    private val RUNNING_KEYWORDS = setOf(
        "running", "run ", "execute", "executing", "test", "pytest", "gradle", "lint", "command", "tool"
    )
    private val APPLYING_KEYWORDS = setOf(
        "apply", "applying", "patch", "edit", "write", "saving", "save", "diff", "artifact", "change", "updated"
    )
    private val WORKING_KEYWORDS = setOf(
        "work", "retry", "approval", "awaiting", "streaming", "processing"
    )
}

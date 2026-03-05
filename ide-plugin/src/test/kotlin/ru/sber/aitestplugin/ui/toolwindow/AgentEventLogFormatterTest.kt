package ru.sber.aitestplugin.ui.toolwindow

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import ru.sber.aitestplugin.model.ChatEventDto
import java.time.Instant

class AgentEventLogFormatterTest {

    @Test
    fun `buildAgentEventLines hides lifecycle noise and maps operational categories`() {
        val now = Instant.parse("2026-03-05T10:00:00Z")
        val events = listOf(
            ChatEventDto("message.received", emptyMap(), now.minusSeconds(2), -1),
            ChatEventDto("opencode.run.started", emptyMap(), now, 0),
            ChatEventDto("opencode.run.progress", mapOf("currentAction" to "Scanning files"), now.plusSeconds(1), 1),
            ChatEventDto("opencode.run.artifact_published", mapOf("artifact" to mapOf("name" to "session-diff.json")), now.plusSeconds(2), 2),
            ChatEventDto("command.executed", mapOf("command" to "diff"), now.plusSeconds(3), 3),
            ChatEventDto("permission.requested", emptyMap(), now.plusSeconds(4), 4),
            ChatEventDto("run.succeeded", emptyMap(), now.plusSeconds(5), 5),
            ChatEventDto("run.failed", mapOf("message" to "Boom"), now.plusSeconds(6), 6),
        )

        val lines = AgentEventLogFormatter.buildAgentEventLines(events, maxLines = 20)
        val text = lines.map { it.text }

        assertTrue(text.any { it.startsWith("[Step] Scanning files") })
        assertTrue(text.any { it.startsWith("[Change] Updated session-diff.json") })
        assertTrue(text.any { it.startsWith("[Command] /diff") })
        assertTrue(text.any { it.startsWith("[Approval] Approval required") })
        assertTrue(text.any { it.startsWith("[Status] Failed: Boom") })
        assertTrue(text.none { it.contains("Request accepted", ignoreCase = true) })
        assertTrue(text.none { it.contains("Run started", ignoreCase = true) })
        assertTrue(text.none { it.contains("succeeded", ignoreCase = true) })
    }

    @Test
    fun `buildAgentEventLines compacts repeated events`() {
        val now = Instant.parse("2026-03-05T10:00:00Z")
        val events = listOf(
            ChatEventDto("opencode.run.progress", mapOf("currentAction" to "Reading project"), now, 0),
            ChatEventDto("opencode.run.progress", mapOf("currentAction" to "Reading project"), now.plusSeconds(1), 1),
        )

        val lines = AgentEventLogFormatter.buildAgentEventLines(events, maxLines = 20)

        assertEquals(1, lines.size)
        assertEquals("[Step] Reading project (x2)", lines[0].text)
    }

    @Test
    fun `mergeConversationAndEvents puts event between user and assistant on same time`() {
        val now = Instant.parse("2026-03-05T10:00:00Z")
        val messages = listOf(
            AgentEventLogFormatter.TimelineItem(
                kind = AgentEventLogFormatter.TimelineKind.USER,
                text = "Please run agent",
                createdAt = now,
                stableKey = "m-user"
            ),
            AgentEventLogFormatter.TimelineItem(
                kind = AgentEventLogFormatter.TimelineKind.ASSISTANT,
                text = "Done",
                createdAt = now,
                stableKey = "m-assistant"
            )
        )
        val events = listOf(
            AgentEventLogFormatter.TimelineItem(
                kind = AgentEventLogFormatter.TimelineKind.AGENT_EVENT,
                text = "[Status] Run started",
                createdAt = now,
                stableKey = "e-1"
            )
        )

        val merged = AgentEventLogFormatter.mergeConversationAndEvents(messages, events)

        assertEquals(
            listOf(
                AgentEventLogFormatter.TimelineKind.USER,
                AgentEventLogFormatter.TimelineKind.AGENT_EVENT,
                AgentEventLogFormatter.TimelineKind.ASSISTANT
            ),
            merged.map { it.kind }
        )
    }

    @Test
    fun `classifyPhase maps common actions to stable phases`() {
        assertEquals(
            AgentEventLogFormatter.ExecutionPhase.THINKING,
            AgentEventLogFormatter.classifyPhase("Scanning project files")
        )
        assertEquals(
            AgentEventLogFormatter.ExecutionPhase.RUNNING,
            AgentEventLogFormatter.classifyPhase("Executing tests")
        )
        assertEquals(
            AgentEventLogFormatter.ExecutionPhase.APPLYING_CHANGES,
            AgentEventLogFormatter.classifyPhase("Applying patch to src/App.kt")
        )
        assertEquals(
            AgentEventLogFormatter.ExecutionPhase.WORKING,
            AgentEventLogFormatter.classifyPhase("Awaiting approval")
        )
    }

    @Test
    fun `formatPhaseProgress returns phase and detail only for active activities`() {
        assertEquals(
            "Thinking: Scanning files",
            AgentEventLogFormatter.formatPhaseProgress("busy", "Scanning files")
        )
        assertEquals(
            "Running: Executing tests",
            AgentEventLogFormatter.formatPhaseProgress("retry", "Working", "Executing tests")
        )
        assertEquals(
            "Working: Awaiting approval",
            AgentEventLogFormatter.formatPhaseProgress("waiting_permission", "")
        )
        assertEquals(
            null,
            AgentEventLogFormatter.formatPhaseProgress("idle", "Idle")
        )
    }
}

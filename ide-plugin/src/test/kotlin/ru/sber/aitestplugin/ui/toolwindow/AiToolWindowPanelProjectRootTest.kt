package ru.sber.aitestplugin.ui.toolwindow

import org.junit.Assert.assertEquals
import org.junit.Test

class AiToolWindowPanelProjectRootTest {

    @Test
    fun `agent runtime uses ide base path`() {
        val result = resolveRuntimeProjectRootValue(
            runtime = "agent",
            projectBasePath = "C:/repo/current-project"
        )

        assertEquals("C:/repo/current-project", result)
    }

    @Test
    fun `chat runtime keeps ide base path`() {
        val result = resolveRuntimeProjectRootValue(
            runtime = "chat",
            projectBasePath = "C:/repo/current-project"
        )

        assertEquals("C:/repo/current-project", result)
    }

    @Test
    fun `returns empty when ide base path is missing`() {
        val result = resolveRuntimeProjectRootValue(
            runtime = "agent",
            projectBasePath = null
        )

        assertEquals("", result)
    }

    @Test
    fun `status line includes agent context and tokens`() {
        val result = buildStatusLabelText(
            runtimeText = "Agent",
            activityText = "Готов",
            connectionText = "подключено",
            details = null,
            contextPercent = 37,
            tokenTotal = 1420
        )

        assertEquals("Agent | Готов | подключено | Контекст 37% | Токены 1420", result)
    }

    @Test
    fun `status line skips agent metrics for non agent`() {
        val result = buildStatusLabelText(
            runtimeText = "Chat",
            activityText = "Готов",
            connectionText = "подключено",
            details = "ok",
            contextPercent = null,
            tokenTotal = null
        )

        assertEquals("Chat | Готов | подключено | ok", result)
    }

    @Test
    fun `context percent falls back to used and context window`() {
        val result = resolveContextPercent(
            percent = null,
            used = 1000,
            contextWindow = 200000
        )

        assertEquals(0, result)
    }

    @Test
    fun `cli token total uses input output and reasoning only`() {
        val result = agentTokenTotal(
            input = 120,
            output = 80,
            reasoning = 40
        )

        assertEquals(240, result)
    }
}

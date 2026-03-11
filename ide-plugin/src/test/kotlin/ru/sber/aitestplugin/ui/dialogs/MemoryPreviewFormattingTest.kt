package ru.sber.aitestplugin.ui.dialogs

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import ru.sber.aitestplugin.model.GenerationResolvePreviewResponseDto

class MemoryPreviewFormattingTest {

    @Test
    fun `reports when memory preview is empty`() {
        val preview = GenerationResolvePreviewResponseDto(projectRoot = "C:/repo")

        assertEquals("Memory rules did not match this testcase.", buildMemoryPreviewStatus(preview))
        assertTrue(formatMemoryPreview(preview).contains("Matched rules: 0"))
        assertTrue(formatMemoryPreview(preview).contains("No template steps will be injected."))
    }

    @Test
    fun `renders matched memory preview details`() {
        val preview = GenerationResolvePreviewResponseDto(
            projectRoot = "C:/repo",
            qualityPolicy = "balanced",
            language = "ru",
            targetPath = "src/test/resources/features/auth.feature",
            appliedRuleIds = listOf("rule-1", "rule-2"),
            appliedTemplateIds = listOf("tpl-1"),
            templateSteps = listOf("Given user is authorized", "When user opens draft")
        )

        val rendered = formatMemoryPreview(preview)

        assertEquals("Memory rules will be applied automatically.", buildMemoryPreviewStatus(preview))
        assertTrue(rendered.contains("Matched rules: 2"))
        assertTrue(rendered.contains("Matched templates: 1"))
        assertTrue(rendered.contains("Resolved quality policy: balanced"))
        assertTrue(rendered.contains("Recommended path: src/test/resources/features/auth.feature"))
        assertTrue(rendered.contains("1. Given user is authorized"))
    }
}

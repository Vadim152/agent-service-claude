package ru.sber.aitestplugin.ui.dialogs

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import ru.sber.aitestplugin.model.GenerationResolvePreviewResponseDto

class MemoryPreviewFormattingTest {

    @Test
    fun `reports when memory preview is empty`() {
        val preview = GenerationResolvePreviewResponseDto(projectRoot = "C:/repo")

        assertEquals("Правила памяти не сработали для этого тест-кейса.", buildMemoryPreviewStatus(preview))
        assertTrue(formatMemoryPreview(preview).contains("Совпавших правил: 0"))
        assertTrue(formatMemoryPreview(preview).contains("Шаблонные шаги не будут добавлены."))
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

        assertEquals("Правила памяти будут применены автоматически.", buildMemoryPreviewStatus(preview))
        assertTrue(rendered.contains("Совпавших правил: 2"))
        assertTrue(rendered.contains("Совпавших шаблонов: 1"))
        assertTrue(rendered.contains("Итоговая quality policy: balanced"))
        assertTrue(rendered.contains("Рекомендуемый путь: src/test/resources/features/auth.feature"))
        assertTrue(rendered.contains("1. Given user is authorized"))
    }
}

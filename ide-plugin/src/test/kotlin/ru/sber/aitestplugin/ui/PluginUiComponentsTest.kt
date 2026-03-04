package ru.sber.aitestplugin.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import ru.sber.aitestplugin.ui.components.EmptyStatePanel
import ru.sber.aitestplugin.ui.components.SectionCard
import ru.sber.aitestplugin.ui.components.StatusBadge
import javax.swing.JLabel
import javax.swing.JPanel

class PluginUiComponentsTest {

    @Test
    fun `status badge updates label text`() {
        val badge = StatusBadge("Подключение", false)

        badge.update("Подключено", true)

        val labels = badge.components.filterIsInstance<JLabel>()
        assertTrue(labels.any { it.text == "Подключено" })
    }

    @Test
    fun `section card keeps provided content`() {
        val content = JPanel()
        val card = SectionCard("Заголовок", "Комментарий", content)

        assertEquals(content, card.getComponent(1))
    }

    @Test
    fun `empty state panel is transparent`() {
        val panel = EmptyStatePanel("Пусто", "Пока данных нет")

        assertFalse(panel.isOpaque)
    }
}

package ru.sber.aitestplugin.ui.components

import com.intellij.util.ui.JBUI
import ru.sber.aitestplugin.ui.theme.PluginUiTheme
import java.awt.BorderLayout
import javax.swing.JLabel
import javax.swing.JPanel

class EmptyStatePanel(title: String, description: String? = null) : JPanel(BorderLayout(0, JBUI.scale(4))) {
    init {
        isOpaque = false
        border = JBUI.Borders.empty(16)
        add(JLabel(title).apply {
            font = font.deriveFont(font.style or java.awt.Font.BOLD)
            foreground = PluginUiTheme.primaryText
        }, BorderLayout.NORTH)
        if (!description.isNullOrBlank()) {
            add(JLabel(description).apply {
                foreground = PluginUiTheme.secondaryText
            }, BorderLayout.CENTER)
        }
    }
}

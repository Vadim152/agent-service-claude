package ru.sber.aitestplugin.ui.components

import com.intellij.util.ui.JBUI
import ru.sber.aitestplugin.ui.theme.PluginUiTheme
import ru.sber.aitestplugin.ui.theme.PluginUiTokens
import java.awt.BorderLayout
import javax.swing.BorderFactory
import javax.swing.JComponent
import javax.swing.JLabel
import javax.swing.JPanel

class SectionCard(
    title: String,
    comment: String? = null,
    content: JComponent
) : JPanel(BorderLayout(0, PluginUiTokens.blockGap)) {
    init {
        isOpaque = true
        background = PluginUiTheme.containerBackground
        border = JBUI.Borders.compound(
            BorderFactory.createLineBorder(PluginUiTheme.containerBorder, 1, true),
            JBUI.Borders.empty(PluginUiTokens.sectionInsets)
        )

        add(
            JPanel(BorderLayout(0, PluginUiTokens.blockGap / 2)).apply {
                isOpaque = false
                add(JLabel(title).apply {
                    font = font.deriveFont(font.style or java.awt.Font.BOLD)
                    foreground = PluginUiTheme.primaryText
                }, BorderLayout.NORTH)
                if (!comment.isNullOrBlank()) {
                    add(JLabel(comment).apply {
                        foreground = PluginUiTheme.secondaryText
                    }, BorderLayout.CENTER)
                }
            },
            BorderLayout.NORTH
        )
        add(content, BorderLayout.CENTER)
    }
}

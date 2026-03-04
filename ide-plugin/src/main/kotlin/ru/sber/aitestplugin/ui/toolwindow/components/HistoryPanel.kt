package ru.sber.aitestplugin.ui.toolwindow.components

import com.intellij.ui.components.JBLabel
import com.intellij.ui.components.JBList
import com.intellij.ui.components.JBScrollPane
import com.intellij.util.ui.JBUI
import ru.sber.aitestplugin.ui.UiStrings
import ru.sber.aitestplugin.ui.theme.PluginUiTheme
import java.awt.BorderLayout
import javax.swing.BorderFactory
import javax.swing.JButton
import javax.swing.JPanel

class HistoryPanel(
    historyList: JBList<*>,
    onBack: () -> Unit,
    onOpenSelected: () -> Unit
) : JPanel(BorderLayout(0, JBUI.scale(8))) {
    init {
        isOpaque = false
        add(
            JPanel(BorderLayout()).apply {
                isOpaque = false
                add(toolbarButton(UiStrings.back, onBack), BorderLayout.WEST)
                add(JBLabel(UiStrings.history).apply {
                    foreground = PluginUiTheme.primaryText
                }, BorderLayout.CENTER)
            },
            BorderLayout.NORTH
        )
        add(JBScrollPane(historyList).apply {
            border = JBUI.Borders.compound(
                BorderFactory.createLineBorder(PluginUiTheme.containerBorder, 1, true),
                JBUI.Borders.empty(2)
            )
            viewport.background = PluginUiTheme.containerBackground
        }, BorderLayout.CENTER)
        add(
            JPanel(BorderLayout()).apply {
                isOpaque = false
                add(toolbarButton(UiStrings.openChat, onOpenSelected), BorderLayout.EAST)
            },
            BorderLayout.SOUTH
        )
    }

    private fun toolbarButton(text: String, action: () -> Unit): JButton = JButton(text).apply {
        foreground = PluginUiTheme.primaryText
        background = PluginUiTheme.controlBackground
        border = BorderFactory.createLineBorder(PluginUiTheme.controlBorder, 1, true)
        isContentAreaFilled = true
        isFocusPainted = false
        addActionListener { action() }
    }
}

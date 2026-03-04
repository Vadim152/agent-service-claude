package ru.sber.aitestplugin.ui.toolwindow.components

import com.intellij.icons.AllIcons
import com.intellij.util.ui.JBUI
import ru.sber.aitestplugin.ui.theme.PluginUiTheme
import java.awt.BorderLayout
import java.awt.Cursor
import java.awt.FlowLayout
import java.awt.Font
import javax.swing.BorderFactory
import javax.swing.JButton
import javax.swing.JComponent
import javax.swing.JLabel
import javax.swing.JPanel

class ToolWindowHeaderPanel(
    title: String,
    statusComponent: JComponent,
    onNewSession: () -> Unit,
    onShowHistory: () -> Unit,
    onOpenSettings: () -> Unit
) : JPanel(BorderLayout()) {
    init {
        isOpaque = false
        border = JBUI.Borders.empty(0, 2, 8, 2)
        add(JLabel(title).apply {
            font = font.deriveFont(Font.BOLD, 16f)
            foreground = PluginUiTheme.primaryText
        }, BorderLayout.WEST)
        add(
            JPanel(FlowLayout(FlowLayout.RIGHT, 6, 0)).apply {
                isOpaque = false
                add(statusComponent)
                add(toolbarButton("+", onNewSession))
                add(toolbarButton("История", onShowHistory))
                add(JButton(AllIcons.General.Settings).apply {
                    toolTipText = "Настройки"
                    cursor = Cursor.getPredefinedCursor(Cursor.HAND_CURSOR)
                    foreground = PluginUiTheme.primaryText
                    background = PluginUiTheme.controlBackground
                    border = BorderFactory.createLineBorder(PluginUiTheme.controlBorder, 1, true)
                    isContentAreaFilled = true
                    isFocusPainted = false
                    addActionListener { onOpenSettings() }
                })
            },
            BorderLayout.EAST
        )
    }

    private fun toolbarButton(text: String, action: () -> Unit): JButton = JButton(text).apply {
        cursor = Cursor.getPredefinedCursor(Cursor.HAND_CURSOR)
        foreground = PluginUiTheme.primaryText
        background = PluginUiTheme.controlBackground
        border = BorderFactory.createLineBorder(PluginUiTheme.controlBorder, 1, true)
        isContentAreaFilled = true
        isFocusPainted = false
        putClientProperty("JButton.buttonType", "roundRect")
        addActionListener { action() }
    }
}

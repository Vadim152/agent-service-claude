package ru.sber.aitestplugin.ui.toolwindow.components

import com.intellij.ui.components.JBScrollPane
import com.intellij.ui.components.JBTextArea
import com.intellij.util.ui.JBUI
import ru.sber.aitestplugin.ui.theme.PluginUiTheme
import java.awt.BorderLayout
import java.awt.Dimension
import java.awt.FlowLayout
import javax.swing.BorderFactory
import javax.swing.JButton
import javax.swing.JComboBox
import javax.swing.JComponent
import javax.swing.JPanel

class ChatComposerPanel(
    runtimeSelector: JComboBox<*>,
    inputArea: JBTextArea,
    sendButton: JButton,
    statusComponent: JComponent
) : JPanel(BorderLayout()) {
    init {
        isOpaque = false
        border = JBUI.Borders.emptyTop(8)
        add(
            JPanel(BorderLayout()).apply {
                isOpaque = true
                background = PluginUiTheme.inputBackground
                border = JBUI.Borders.compound(
                    BorderFactory.createLineBorder(PluginUiTheme.containerBorder, 1, true),
                    JBUI.Borders.empty(8, 8, 8, 6)
                )
                add(
                    JPanel(FlowLayout(FlowLayout.LEFT, 0, 0)).apply {
                        isOpaque = false
                        border = JBUI.Borders.emptyBottom(6)
                        add(runtimeSelector)
                    },
                    BorderLayout.NORTH
                )
                add(JBScrollPane(inputArea).apply {
                    border = JBUI.Borders.empty()
                    background = PluginUiTheme.inputBackground
                    viewport.background = PluginUiTheme.inputBackground
                    preferredSize = Dimension(120, JBUI.scale(110))
                }, BorderLayout.CENTER)
                add(
                    JPanel(BorderLayout()).apply {
                        isOpaque = false
                        border = JBUI.Borders.emptyTop(6)
                        add(JPanel(FlowLayout(FlowLayout.RIGHT, 0, 0)).apply {
                            isOpaque = false
                            add(sendButton)
                        }, BorderLayout.EAST)
                    },
                    BorderLayout.SOUTH
                )
            },
            BorderLayout.CENTER
        )
        add(statusComponent, BorderLayout.SOUTH)
    }
}

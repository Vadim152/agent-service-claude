package ru.sber.aitestplugin.ui.components

import com.intellij.util.ui.JBUI
import ru.sber.aitestplugin.ui.theme.PluginUiTheme
import java.awt.FlowLayout
import javax.swing.JLabel
import javax.swing.JPanel

class StatusBadge(text: String, online: Boolean) : JPanel(FlowLayout(FlowLayout.LEFT, JBUI.scale(6), 0)) {
    private val label = JLabel(text)

    init {
        isOpaque = false
        add(JLabel(PluginUiTheme.statusIcon(online)))
        label.foreground = PluginUiTheme.statusColor(online)
        add(label)
    }

    fun update(text: String, online: Boolean) {
        removeAll()
        add(JLabel(PluginUiTheme.statusIcon(online)))
        label.text = text
        label.foreground = PluginUiTheme.statusColor(online)
        add(label)
        revalidate()
        repaint()
    }
}

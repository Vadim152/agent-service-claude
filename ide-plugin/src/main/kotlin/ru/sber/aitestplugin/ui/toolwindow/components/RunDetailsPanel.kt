package ru.sber.aitestplugin.ui.toolwindow.components

import com.intellij.ui.components.JBTabbedPane
import com.intellij.util.ui.JBUI
import ru.sber.aitestplugin.ui.UiStrings
import java.awt.BorderLayout
import javax.swing.JComponent
import javax.swing.JPanel

class RunDetailsPanel(
    infoPanel: JComponent,
    eventsPanel: JComponent,
    artifactsPanel: JComponent,
    approvalsPanel: JComponent
) : JPanel(BorderLayout(0, JBUI.scale(6))) {
    private val tabs = JBTabbedPane().apply {
        addTab(UiStrings.infoTab, infoPanel)
        addTab(UiStrings.eventsTab, eventsPanel)
        addTab(UiStrings.artifactsTab, artifactsPanel)
    }

    init {
        isOpaque = false
        add(tabs, BorderLayout.CENTER)
        add(approvalsPanel, BorderLayout.SOUTH)
    }
}

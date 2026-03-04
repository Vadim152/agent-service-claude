package ru.sber.aitestplugin.ui.theme

import com.intellij.util.ui.JBUI
import java.awt.Dimension

object PluginUiTokens {
    val panelInsets = JBUI.insets(12)
    val sectionInsets = JBUI.insets(12, 12, 12, 12)
    val compactInsets = JBUI.insets(8)
    val blockGap = JBUI.scale(8)
    val contentGap = JBUI.scale(12)
    val largeGap = JBUI.scale(16)
    val cardArc = JBUI.scale(14)
    val buttonArc = JBUI.scale(10)
    val toolWindowMinTimelineHeight = JBUI.scale(360)
    val detailsPreviewSize = Dimension(JBUI.scale(420), JBUI.scale(360))
}

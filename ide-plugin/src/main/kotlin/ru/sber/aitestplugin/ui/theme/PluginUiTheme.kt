package ru.sber.aitestplugin.ui.theme

import com.intellij.icons.AllIcons
import com.intellij.ui.JBColor
import java.awt.Color
import javax.swing.Icon

object PluginUiTheme {
    val panelBackground: Color = JBColor.PanelBackground
    val containerBackground: Color = JBColor(
        Color(0xFA, 0xFB, 0xFC),
        Color(0x2B, 0x2D, 0x30)
    )
    val containerBorder: Color = JBColor(
        Color(0xD8, 0xDE, 0xE6),
        Color(0x4A, 0x4F, 0x57)
    )
    val controlBackground: Color = JBColor(
        Color(0xF2, 0xF5, 0xF8),
        Color(0x3A, 0x3D, 0x42)
    )
    val inputBackground: Color = JBColor(
        Color(0xFF, 0xFF, 0xFF),
        Color(0x31, 0x33, 0x36)
    )
    val accentBackground: Color = JBColor(
        Color(0xE8, 0xF1, 0xFF),
        Color(0x2D, 0x42, 0x5D)
    )
    val accentForeground: Color = JBColor(
        Color(0x0B, 0x5C, 0xAD),
        Color(0x9E, 0xC6, 0xFF)
    )
    val primaryText: Color = JBColor.foreground()
    val secondaryText: Color = JBColor.GRAY
    val systemText: Color = JBColor(
        Color(0xA6, 0x2D, 0x2D),
        Color(0xF0, 0x8C, 0x8C)
    )
    val controlBorder: Color = containerBorder
    val sendButtonBackground: Color = accentForeground
    val stopButtonBackground: Color = JBColor(
        Color(0xB5, 0x2B, 0x2B),
        Color(0xD6, 0x71, 0x71)
    )
    val userBubble: Color = JBColor(
        Color(0xF2, 0xF7, 0xFF),
        Color(0x3A, 0x4A, 0x5D)
    )
    val userBubbleBorder: Color = JBColor(
        Color(0xCC, 0xDA, 0xEA),
        Color(0x5B, 0x6F, 0x84)
    )
    val progressBubble: Color = JBColor(
        Color(0xF4, 0xF5, 0xF7),
        Color(0x3A, 0x3D, 0x42)
    )

    fun statusColor(online: Boolean): Color = if (online) {
        JBColor(Color(0x1F, 0x8F, 0x5A), Color(0x69, 0xC5, 0x89))
    } else {
        JBColor(Color(0xA6, 0x52, 0x2A), Color(0xE0, 0x9B, 0x77))
    }

    fun statusIcon(online: Boolean): Icon = if (online) {
        AllIcons.General.InspectionsOK
    } else {
        AllIcons.General.Warning
    }
}

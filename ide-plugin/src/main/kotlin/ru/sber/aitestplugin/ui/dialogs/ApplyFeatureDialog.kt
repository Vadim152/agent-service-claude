package ru.sber.aitestplugin.ui.dialogs

import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.DialogWrapper
import com.intellij.ui.dsl.builder.AlignX
import com.intellij.ui.dsl.builder.panel
import ru.sber.aitestplugin.ui.UiStrings
import javax.swing.JComponent

class ApplyFeatureDialog(project: Project, defaults: ApplyFeatureDialogOptions) : DialogWrapper(project) {
    private val targetPathField = com.intellij.ui.components.JBTextField(defaults.targetPath ?: "")
    private val createFileCheckbox = com.intellij.ui.components.JBCheckBox(UiStrings.dialogCreateFile, defaults.createFile)
    private val overwriteCheckbox = com.intellij.ui.components.JBCheckBox(UiStrings.dialogOverwriteFile, defaults.overwriteExisting)

    init {
        title = UiStrings.applyFeatureTitle
        init()
    }

    override fun createCenterPanel(): JComponent = buildApplyFeatureFormPanel(
        targetPathField = targetPathField,
        createFileCheckbox = createFileCheckbox,
        overwriteCheckbox = overwriteCheckbox,
    )

    fun targetPath(): String? = targetPathField.text.trim().takeIf { it.isNotEmpty() }

    fun shouldCreateFile(): Boolean = createFileCheckbox.isSelected

    fun shouldOverwriteExisting(): Boolean = overwriteCheckbox.isSelected

    fun selectedOptions(): ApplyFeatureDialogOptions = ApplyFeatureDialogOptions(
        targetPath = targetPath(),
        createFile = shouldCreateFile(),
        overwriteExisting = shouldOverwriteExisting(),
    )
}

internal fun buildApplyFeatureFormPanel(
    targetPathField: com.intellij.ui.components.JBTextField,
    createFileCheckbox: com.intellij.ui.components.JBCheckBox,
    overwriteCheckbox: com.intellij.ui.components.JBCheckBox,
): JComponent = panel {
    row(UiStrings.dialogTargetPath) {
        cell(targetPathField).resizableColumn().align(AlignX.FILL)
    }
    row {
        comment(UiStrings.dialogTargetPathComment)
    }
    row {
        cell(createFileCheckbox)
    }
    row {
        cell(overwriteCheckbox)
    }
}

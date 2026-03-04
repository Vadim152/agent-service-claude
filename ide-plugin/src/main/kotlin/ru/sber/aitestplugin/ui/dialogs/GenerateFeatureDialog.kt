package ru.sber.aitestplugin.ui.dialogs

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.DialogWrapper
import com.intellij.ui.JBColor
import com.intellij.ui.components.JBCheckBox
import com.intellij.ui.components.JBScrollPane
import com.intellij.ui.components.JBTextArea
import com.intellij.ui.components.JBTextField
import com.intellij.ui.dsl.builder.Align
import com.intellij.ui.dsl.builder.AlignX
import com.intellij.ui.dsl.builder.panel
import com.intellij.util.ui.JBUI
import ru.sber.aitestplugin.model.GenerationResolvePreviewRequestDto
import ru.sber.aitestplugin.model.GenerationResolvePreviewResponseDto
import ru.sber.aitestplugin.services.BackendClient
import ru.sber.aitestplugin.ui.UiStrings
import javax.swing.JButton
import javax.swing.JComponent

class GenerateFeatureDialog(
    project: Project,
    defaults: GenerateFeatureDialogOptions,
    private val backendClient: BackendClient,
    private val projectRoot: String,
    private val testCaseText: String
) : DialogWrapper(project) {
    private val targetPathField = JBTextField(defaults.targetPath ?: "")
    private val createFileCheckbox = JBCheckBox(UiStrings.dialogCreateFile, defaults.createFile)
    private val overwriteCheckbox = JBCheckBox(UiStrings.dialogOverwriteFile, defaults.overwriteExisting)
    private val defaultLanguage = defaults.language
    private val memoryStatusLabel = javax.swing.JLabel(UiStrings.dialogLoadingPreview)
    private val memoryPreviewArea = JBTextArea().apply {
        isEditable = false
        lineWrap = true
        wrapStyleWord = true
        border = JBUI.Borders.empty(8)
        background = JBColor.PanelBackground
        foreground = JBColor.foreground()
        rows = 8
    }
    private val refreshPreviewButton = JButton(UiStrings.dialogRefreshPreview)
    private var latestPreview: GenerationResolvePreviewResponseDto? = null

    init {
        title = UiStrings.generateFeatureTitle
        refreshPreviewButton.addActionListener { loadMemoryPreviewAsync() }
        init()
        loadMemoryPreviewAsync()
    }

    override fun createCenterPanel(): JComponent = buildGenerateFeatureFormPanel(
        targetPathField = targetPathField,
        createFileCheckbox = createFileCheckbox,
        overwriteCheckbox = overwriteCheckbox,
        memoryStatusLabel = memoryStatusLabel,
        memoryPreviewArea = memoryPreviewArea,
        refreshPreviewButton = refreshPreviewButton,
    )

    fun targetPath(): String? = targetPathField.text.trim().takeIf { it.isNotEmpty() }

    fun shouldCreateFile(): Boolean = createFileCheckbox.isSelected

    fun shouldOverwriteExisting(): Boolean = overwriteCheckbox.isSelected

    fun selectedOptions(): GenerateFeatureDialogOptions = GenerateFeatureDialogOptions(
        targetPath = targetPath(),
        createFile = shouldCreateFile(),
        overwriteExisting = shouldOverwriteExisting(),
        language = defaultLanguage,
    )

    private fun loadMemoryPreviewAsync() {
        memoryStatusLabel.text = UiStrings.dialogLoadingPreview
        memoryPreviewArea.text = ""
        refreshPreviewButton.isEnabled = false
        ApplicationManager.getApplication().executeOnPooledThread {
            val request = GenerationResolvePreviewRequestDto(
                projectRoot = projectRoot,
                text = testCaseText,
                language = defaultLanguage,
                qualityPolicy = DEFAULT_QUALITY_POLICY,
            )
            runCatching { backendClient.resolveGenerationPreview(request) }
                .onSuccess { preview ->
                    ApplicationManager.getApplication().invokeLater {
                        refreshPreviewButton.isEnabled = true
                        latestPreview = preview
                        if (targetPathField.text.trim().isEmpty() && !preview.targetPath.isNullOrBlank()) {
                            targetPathField.text = preview.targetPath
                        }
                        memoryStatusLabel.text = buildMemoryPreviewStatus(preview)
                        memoryPreviewArea.text = formatMemoryPreview(preview)
                    }
                }
                .onFailure { ex ->
                    ApplicationManager.getApplication().invokeLater {
                        refreshPreviewButton.isEnabled = true
                        latestPreview = null
                        memoryStatusLabel.text = UiStrings.dialogPreviewUnavailable
                        memoryPreviewArea.text = ex.message?.trim().takeUnless { it.isNullOrBlank() }
                            ?: "Backend не вернул данные предпросмотра. Генерацию можно продолжить без них."
                    }
                }
        }
    }

    companion object {
        private const val DEFAULT_QUALITY_POLICY = "strict"
    }
}

internal fun buildGenerateFeatureFormPanel(
    targetPathField: JBTextField,
    createFileCheckbox: JBCheckBox,
    overwriteCheckbox: JBCheckBox,
    memoryStatusLabel: javax.swing.JLabel,
    memoryPreviewArea: JBTextArea,
    refreshPreviewButton: JButton,
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
    group(UiStrings.dialogMemoryPreview) {
        row {
            cell(memoryStatusLabel)
        }
        row {
            cell(JBScrollPane(memoryPreviewArea))
                .resizableColumn()
                .align(Align.FILL)
        }
        row {
            cell(refreshPreviewButton)
        }
    }
}

internal fun buildMemoryPreviewStatus(preview: GenerationResolvePreviewResponseDto): String {
    if (
        preview.appliedRuleIds.isEmpty() &&
        preview.appliedTemplateIds.isEmpty() &&
        preview.templateSteps.isEmpty() &&
        preview.targetPath.isNullOrBlank() &&
        preview.qualityPolicy.isNullOrBlank() &&
        preview.language.isNullOrBlank()
    ) {
        return "Правила памяти не сработали для этого тест-кейса."
    }
    return "Правила памяти будут применены автоматически."
}

internal fun formatMemoryPreview(preview: GenerationResolvePreviewResponseDto): String {
    val lines = mutableListOf<String>()
    lines += "Совпавших правил: ${preview.appliedRuleIds.size}"
    lines += "Совпавших шаблонов: ${preview.appliedTemplateIds.size}"
    lines += "Шагов для вставки: ${preview.templateSteps.size}"
    preview.qualityPolicy?.takeIf { it.isNotBlank() }?.let { lines += "Итоговая quality policy: $it" }
    preview.language?.takeIf { it.isNotBlank() }?.let { lines += "Итоговый язык: $it" }
    preview.targetPath?.takeIf { it.isNotBlank() }?.let { lines += "Рекомендуемый путь: $it" }
    if (preview.templateSteps.isNotEmpty()) {
        lines += ""
        lines += "Будут добавлены шаги:"
        preview.templateSteps.forEachIndexed { index, step ->
            lines += "${index + 1}. $step"
        }
    } else {
        lines += ""
        lines += "Шаблонные шаги не будут добавлены."
    }
    return lines.joinToString("\n")
}

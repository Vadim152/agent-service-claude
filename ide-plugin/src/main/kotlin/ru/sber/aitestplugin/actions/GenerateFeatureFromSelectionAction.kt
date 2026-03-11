package ru.sber.aitestplugin.actions

import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.actionSystem.CommonDataKeys
import com.intellij.notification.NotificationGroupManager
import com.intellij.notification.NotificationType
import com.intellij.openapi.editor.colors.EditorColorsManager
import com.intellij.openapi.editor.impl.DocumentMarkupModel
import com.intellij.openapi.editor.markup.EffectType
import com.intellij.openapi.editor.markup.HighlighterLayer
import com.intellij.openapi.editor.markup.HighlighterTargetArea
import com.intellij.openapi.editor.markup.TextAttributes
import com.intellij.openapi.fileEditor.FileDocumentManager
import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.progress.ProgressManager
import com.intellij.openapi.progress.Task
import com.intellij.openapi.progress.ProgressIndicator
import com.intellij.openapi.project.Project
import com.intellij.openapi.util.Key
import com.intellij.openapi.vfs.LocalFileSystem
import com.intellij.openapi.vfs.VirtualFile
import com.intellij.testFramework.LightVirtualFile
import com.intellij.ui.JBColor
import ru.sber.aitestplugin.config.AiTestPluginSettingsService
import ru.sber.aitestplugin.config.zephyrAuthValidationError
import ru.sber.aitestplugin.model.GenerateFeatureOptionsDto
import ru.sber.aitestplugin.model.FEATURE_REVIEW_METADATA_KEY
import ru.sber.aitestplugin.model.FeatureReviewMetadata
import ru.sber.aitestplugin.model.FeatureResultDto
import ru.sber.aitestplugin.model.QualityReportDto
import ru.sber.aitestplugin.model.RunCreateRequestDto
import ru.sber.aitestplugin.model.UnmappedStepDto
import ru.sber.aitestplugin.services.HttpBackendClient
import ru.sber.aitestplugin.ui.dialogs.FeatureDialogStateStorage
import ru.sber.aitestplugin.ui.dialogs.GenerateFeatureDialog
import ru.sber.aitestplugin.ui.toolwindow.ToolWindowIds
import java.awt.Color
import java.nio.file.Path
import java.nio.file.Paths
import javax.swing.JOptionPane

/**
 * Действие, генерирующее .feature из выделенного текста тесткейса.
 */
class GenerateFeatureFromSelectionAction : AnAction() {
    private val unmappedHighlightKey = Key.create<Boolean>("ru.sber.aitestplugin.unmapped.step")

    override fun actionPerformed(e: AnActionEvent) {
        val editor = e.getData(CommonDataKeys.EDITOR)
        val project = e.project
        if (editor == null || project == null) {
            JOptionPane.showMessageDialog(null, "Выделите текст тесткейса")
            return
        }
        val selectionModel = editor.selectionModel
        val selectedText = selectionModel.selectedText?.trim() ?: run {
            JOptionPane.showMessageDialog(null, "Выделите текст тесткейса")
            return
        }
        if (selectedText.isBlank()) {
            JOptionPane.showMessageDialog(null, "Выделите текст тесткейса")
            return
        }
        val projectRoot = project.basePath?.trim().orEmpty()
        if (projectRoot.isBlank()) {
            JOptionPane.showMessageDialog(null, "Не удалось определить корень проекта")
            return
        }

        val backendClient = HttpBackendClient(project)

        val stateStorage = FeatureDialogStateStorage(AiTestPluginSettingsService.getInstance(project).settings)
        val dialog = GenerateFeatureDialog(
            project = project,
            defaults = stateStorage.loadGenerateOptions(),
            backendClient = backendClient,
            projectRoot = projectRoot,
            testCaseText = selectedText
        )
        if (!dialog.showAndGet()) {
            return
        }
        if (dialog.generationBlocked()) {
            notify(project, "Feature generation is blocked by critical ambiguity in the testcase preview", NotificationType.WARNING)
            return
        }

        val dialogOptions = dialog.selectedOptions()
        stateStorage.saveGenerateOptions(dialogOptions)

        val settings = AiTestPluginSettingsService.getInstance(project).settings
        val authError = settings.zephyrAuthValidationError()
        if (authError != null) {
            JOptionPane.showMessageDialog(null, authError)
            return
        }

        val options = GenerateFeatureOptionsDto(
            createFile = dialogOptions.createFile,
            overwriteExisting = dialogOptions.overwriteExisting
        )

        ProgressManager.getInstance().run(object : Task.Backgroundable(project, "Generating feature", true) {
            private var featureText: String = ""
            private var resultTargetPath: String? = dialogOptions.targetPath
            private var unmappedSteps: List<UnmappedStepDto> = emptyList()
            private var fileStatus: Map<String, Any?>? = null
            private var quality: QualityReportDto? = null
            private var featureResult: FeatureResultDto? = null
            private var planId: String? = dialog.planId()
            private var selectedScenarioId: String? = dialog.selectedScenarioId()
            private var selectedScenarioCandidateId: String? = dialog.selectedScenarioCandidateId()
            private var acceptedAssumptionIds: List<String> = dialog.acceptedAssumptionIds()
            private var confirmedClarifications: Map<String, String> = dialog.confirmedClarifications()

            override fun run(indicator: ProgressIndicator) {
                indicator.text = "Creating run..."
                val run = backendClient.createRun(
                    RunCreateRequestDto(
                        projectRoot = projectRoot,
                        plugin = "testgen",
                        input = buildRunInputPayload(
                            selectedText = selectedText,
                            dialogOptions = dialogOptions,
                            planId = planId,
                            selectedScenarioId = selectedScenarioId,
                            selectedScenarioCandidateId = selectedScenarioCandidateId,
                            acceptedAssumptionIds = acceptedAssumptionIds,
                            confirmedClarifications = confirmedClarifications,
                        ),
                        profile = "quick",
                        source = "ide-plugin"
                    )
                )

                indicator.text = "Waiting for run..."
                val finalStatus = backendClient.awaitTerminalRunStatus(run.runId, timeoutMs = 60_000).status
                if (finalStatus == "cancelled") {
                    throw IllegalStateException("Run was cancelled")
                }

                indicator.text = "Fetching result..."
                val result = backendClient.getRunResult(run.runId)
                val feature = result.output ?: throw IllegalStateException("Run completed without feature result")
                featureResult = feature
                featureText = feature.featureText
                if (featureText.isBlank()) {
                    throw IllegalStateException("Generated feature is empty")
                }
                unmappedSteps = feature.unmappedSteps
                fileStatus = feature.fileStatus
                quality = feature.quality
                val targetFromFileStatus = feature.fileStatus?.get("targetPath")?.toString()
                resultTargetPath = targetFromFileStatus ?: resultTargetPath
            }

            override fun onSuccess() {
                val file = resolveFeatureFile(projectRoot, resultTargetPath, featureText, fileStatus)
                file.putUserData(
                    FEATURE_REVIEW_METADATA_KEY,
                    FeatureReviewMetadata(
                        projectRoot = projectRoot,
                        targetPath = dialogOptions.targetPath,
                        overwriteExisting = dialogOptions.overwriteExisting,
                        planId = planId ?: featureResult?.planId,
                        selectedScenarioId = selectedScenarioId ?: featureResult?.selectedScenarioId,
                        selectedScenarioCandidateId = selectedScenarioCandidateId ?: featureResult?.selectedScenarioCandidateId,
                        acceptedAssumptionIds = acceptedAssumptionIds,
                        confirmedClarifications = confirmedClarifications,
                        originalFeatureText = featureText
                    )
                )
                FileEditorManager.getInstance(project).openFile(file, true)
                highlightUnmappedSteps(project, file, unmappedSteps)
                updateToolWindowUnmapped(project, unmappedSteps)
                val fileStatusCode = fileStatus?.get("status")?.toString().orEmpty()
                val notificationType = if (fileStatusCode == "rejected_outside_project") {
                    NotificationType.ERROR
                } else if (quality?.passed == false) {
                    NotificationType.WARNING
                } else {
                    NotificationType.INFORMATION
                }
                notify(
                    project,
                    buildNotificationMessage(unmappedSteps, fileStatus, quality, result = featureResult),
                    notificationType
                )
            }

            override fun onThrowable(error: Throwable) {
                val message = error.message ?: "Unexpected error"
                notify(project, "Feature generation failed: $message", NotificationType.ERROR)
            }
        })
    }

    private fun resolveFeatureFile(
        projectRoot: String,
        targetPath: String?,
        featureText: String,
        fileStatus: Map<String, Any?>?
    ): VirtualFile {
        val status = fileStatus?.get("status")?.toString()?.lowercase()
        if (status !in setOf("created", "overwritten")) {
            val previewName = targetPath?.substringAfterLast('/', "generated.feature")
                ?.substringAfterLast('\\', "generated.feature")
                ?: "generated.feature"
            return LightVirtualFile(previewName, featureText)
        }
        val normalizedTarget = targetPath?.takeIf { it.isNotBlank() }
        val filePath = normalizedTarget?.let { toAbsolutePath(projectRoot, it) }

        if (filePath != null) {
            val existing = LocalFileSystem.getInstance().refreshAndFindFileByPath(filePath.toString())
            if (existing != null) {
                return existing
            }
        }

        val previewName = filePath?.fileName?.toString() ?: "generated.feature"
        return LightVirtualFile(previewName, featureText)
    }

    private fun toAbsolutePath(projectRoot: String, targetPath: String): Path {
        val path = Paths.get(targetPath)
        return if (path.isAbsolute) path else Paths.get(projectRoot).resolve(path).normalize()
    }

    private fun highlightUnmappedSteps(project: Project, virtualFile: VirtualFile, unmappedSteps: List<UnmappedStepDto>) {
        if (unmappedSteps.isEmpty()) return

        val document = FileDocumentManager.getInstance().getDocument(virtualFile) ?: return
        val markupModel = DocumentMarkupModel.forDocument(document, project, true)

        markupModel.allHighlighters
            .filter { it.getUserData(unmappedHighlightKey) == true }
            .forEach { markupModel.removeHighlighter(it) }

        val textAttributes = buildUnmappedAttributes()

        unmappedSteps.forEach { step ->
            val text = step.text
            var startIndex = document.text.indexOf(text)
            while (startIndex >= 0) {
                val endIndex = startIndex + text.length
                val highlighter = markupModel.addRangeHighlighter(
                    startIndex,
                    endIndex,
                    HighlighterLayer.WARNING,
                    textAttributes,
                    HighlighterTargetArea.EXACT_RANGE
                )
                highlighter.setErrorStripeMarkColor(JBColor.RED)
                highlighter.errorStripeTooltip = step.reason ?: "Unmapped step"
                highlighter.putUserData(unmappedHighlightKey, true)

                startIndex = document.text.indexOf(text, endIndex)
            }
        }
    }

    private fun buildUnmappedAttributes(): TextAttributes {
        val globalScheme = EditorColorsManager.getInstance().globalScheme
        return TextAttributes()
            .also {
                it.foregroundColor = JBColor.RED
                it.effectColor = JBColor(Color(0xD1, 0x39, 0x39), Color(0xD1, 0x39, 0x39))
                it.effectType = EffectType.WAVE_UNDERSCORE
                it.backgroundColor = globalScheme.defaultBackground
            }
    }

    private fun notify(project: Project, message: String, type: NotificationType) {
        NotificationGroupManager.getInstance()
            .getNotificationGroup("Агентум")
            .createNotification(message, type)
            .notify(project)
    }

    private fun buildNotificationMessage(
        unmappedSteps: List<UnmappedStepDto>,
        fileStatus: Map<String, Any?>?,
        quality: QualityReportDto?,
        result: FeatureResultDto?
    ): String {
        val base = "Feature generated${if (unmappedSteps.isNotEmpty()) ": ${unmappedSteps.size} unmapped steps" else ""}"
        val status = fileStatus?.get("status")?.toString()
        if (status == "rejected_outside_project") {
            val target = fileStatus["targetPath"]?.toString().orEmpty()
            return "Feature generated, but saving outside current project is blocked: $target"
        }
        val qualitySuffix = quality?.let { " (quality: ${it.score}, gate=${if (it.passed) "pass" else "fail"})" } ?: ""
        val memorySuffix = result?.let(::buildMemorySummaryFromPipeline)?.let { " ($it)" } ?: ""
        val filePart = if (status.isNullOrBlank()) base else "$base (file: $status)"
        return "$filePart$qualitySuffix$memorySuffix"
    }

    private fun updateToolWindowUnmapped(project: Project, unmappedSteps: List<UnmappedStepDto>) {
        val toolWindow = ToolWindowIds.findToolWindow(project)
        val panel = toolWindow?.contentManager?.contents
            ?.mapNotNull { it.component as? ru.sber.aitestplugin.ui.toolwindow.AiToolWindowPanel }
            ?.firstOrNull()
        panel?.showUnmappedSteps(unmappedSteps)
    }
}

internal fun buildRunInputPayload(
    selectedText: String,
    dialogOptions: ru.sber.aitestplugin.ui.dialogs.GenerateFeatureDialogOptions,
    planId: String?,
    selectedScenarioId: String?,
    selectedScenarioCandidateId: String?,
    acceptedAssumptionIds: List<String>,
    confirmedClarifications: Map<String, String>,
): Map<String, Any?> = mapOf(
    "testCaseText" to selectedText,
    "targetPath" to dialogOptions.targetPath,
    "createFile" to false,
    "overwriteExisting" to false,
    "language" to dialogOptions.language,
    "planId" to planId,
    "selectedScenarioId" to selectedScenarioId,
    "selectedScenarioCandidateId" to selectedScenarioCandidateId,
    "acceptedAssumptionIds" to acceptedAssumptionIds,
    "clarifications" to confirmedClarifications,
)

internal fun buildMemorySummaryFromPipeline(result: FeatureResultDto): String? {
    val stage = result.pipeline.firstOrNull { it["stage"]?.toString() == "memory_rules" } ?: return null
    val details = stage["details"] as? Map<*, *> ?: return null
    val appliedRules = (details["appliedRuleIds"] as? List<*>)?.size ?: 0
    val appliedTemplates = (details["appliedTemplateIds"] as? List<*>)?.size ?: 0
    val templateStepsAdded = when (val value = details["templateStepsAdded"]) {
        is Number -> value.toInt()
        is String -> value.toIntOrNull() ?: 0
        else -> 0
    }
    if (appliedRules == 0 && appliedTemplates == 0 && templateStepsAdded == 0) {
        return null
    }
    return "memory: rules=$appliedRules, templates=$appliedTemplates, injectedSteps=$templateStepsAdded"
}




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
import ru.sber.aitestplugin.model.JobCreateRequestDto
import ru.sber.aitestplugin.model.QualityReportDto
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
        val dialog = GenerateFeatureDialog(project, stateStorage.loadGenerateOptions())
        if (!dialog.showAndGet()) {
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

            override fun run(indicator: ProgressIndicator) {
                indicator.text = "Creating job..."
                val job = backendClient.createJob(
                    JobCreateRequestDto(
                        projectRoot = projectRoot,
                        testCaseText = selectedText,
                        targetPath = dialogOptions.targetPath,
                        profile = "quick",
                        createFile = options.createFile,
                        overwriteExisting = options.overwriteExisting
                    )
                )

                indicator.text = "Waiting for job..."
                val finalStatus = backendClient.awaitTerminalJobStatus(job.jobId, timeoutMs = 60_000).status
                if (finalStatus == "cancelled") {
                    throw IllegalStateException("Job was cancelled")
                }

                indicator.text = "Fetching result..."
                val result = backendClient.getJobResult(job.jobId)
                val feature = result.feature ?: throw IllegalStateException("Job completed without feature result")
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
                val file = resolveFeatureFile(projectRoot, resultTargetPath, featureText)
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
                    buildNotificationMessage(unmappedSteps, fileStatus, quality),
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
        featureText: String
    ): VirtualFile {
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
        quality: QualityReportDto?
    ): String {
        val base = "Feature generated${if (unmappedSteps.isNotEmpty()) ": ${unmappedSteps.size} unmapped steps" else ""}"
        val status = fileStatus?.get("status")?.toString()
        if (status == "rejected_outside_project") {
            val target = fileStatus["targetPath"]?.toString().orEmpty()
            return "Feature generated, but saving outside current project is blocked: $target"
        }
        val qualitySuffix = quality?.let { " (quality: ${it.score}, gate=${if (it.passed) "pass" else "fail"})" } ?: ""
        val filePart = if (status.isNullOrBlank()) base else "$base (file: $status)"
        return "$filePart$qualitySuffix"
    }

    private fun updateToolWindowUnmapped(project: Project, unmappedSteps: List<UnmappedStepDto>) {
        val toolWindow = ToolWindowIds.findToolWindow(project)
        val panel = toolWindow?.contentManager?.contents
            ?.mapNotNull { it.component as? ru.sber.aitestplugin.ui.toolwindow.AiToolWindowPanel }
            ?.firstOrNull()
        panel?.showUnmappedSteps(unmappedSteps)
    }
}




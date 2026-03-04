package ru.sber.aitestplugin.config

import com.intellij.icons.AllIcons
import com.intellij.notification.NotificationGroupManager
import com.intellij.notification.NotificationType
import com.intellij.openapi.options.Configurable
import com.intellij.openapi.progress.ProgressIndicator
import com.intellij.openapi.progress.ProgressManager
import com.intellij.openapi.progress.Task
import com.intellij.openapi.project.Project
import com.intellij.openapi.project.ProjectManager
import com.intellij.openapi.ui.Messages
import com.intellij.ui.ColoredListCellRenderer
import com.intellij.ui.JBSplitter
import com.intellij.ui.JBColor
import com.intellij.ui.SimpleTextAttributes
import com.intellij.ui.components.JBList
import com.intellij.ui.components.JBPasswordField
import com.intellij.ui.components.JBScrollPane
import com.intellij.ui.components.JBTextField
import com.intellij.ui.dsl.builder.AlignX
import com.intellij.ui.dsl.builder.panel
import com.intellij.util.ui.JBUI
import ru.sber.aitestplugin.model.StepDefinitionDto
import ru.sber.aitestplugin.model.UnmappedStepDto
import ru.sber.aitestplugin.services.BackendClient
import ru.sber.aitestplugin.services.HttpBackendClient
import ru.sber.aitestplugin.ui.UiStrings
import ru.sber.aitestplugin.ui.components.SectionCard
import ru.sber.aitestplugin.ui.dialogs.MemoryManagerDialog
import ru.sber.aitestplugin.ui.theme.PluginUiTheme
import ru.sber.aitestplugin.ui.theme.PluginUiTokens
import ru.sber.aitestplugin.util.StepScanRootsResolver
import java.awt.BorderLayout
import java.awt.Component
import java.awt.FlowLayout
import java.net.HttpURLConnection
import java.net.URL
import java.util.Base64
import javax.swing.Box
import javax.swing.BoxLayout
import javax.swing.ButtonGroup
import javax.swing.JButton
import javax.swing.JComponent
import javax.swing.JComboBox
import javax.swing.JEditorPane
import javax.swing.JLabel
import javax.swing.JPanel
import javax.swing.JRadioButton

class AiTestPluginSettingsConfigurable(
    project: Project? = null,
    backendClient: BackendClient? = null
) : Configurable {
    private val project: Project = project ?: ProjectManager.getInstance().defaultProject
    private val settingsService = AiTestPluginSettingsService.getInstance(this.project)
    private val backendClient: BackendClient = backendClient ?: HttpBackendClient(this.project)

    private val projectRootField = JBTextField()
    private val scanButton = JButton(UiStrings.settingsScanButton, AllIcons.Actions.Search).apply {
        foreground = PluginUiTheme.accentForeground
        background = PluginUiTheme.accentBackground
        isOpaque = true
    }
    private val stepsList = JBList<StepDefinitionDto>()
    private val statusLabel = JLabel(UiStrings.settingsIndexMissing, AllIcons.General.Information, JLabel.LEADING)

    private val zephyrJiraLabel = JLabel(UiStrings.settingsJiraInstance)
    private val zephyrJiraInstanceCombo = JComboBox(jiraInstanceOptions.keys.toTypedArray())
    private val zephyrTokenRadio = JRadioButton(UiStrings.settingsAuthToken, true)
    private val zephyrLoginRadio = JRadioButton(UiStrings.settingsAuthLoginPassword)
    private val zephyrTokenLabel = JLabel(UiStrings.settingsJiraToken)
    private val zephyrTokenField = JBPasswordField()
    private val zephyrLoginLabel = JLabel(UiStrings.settingsLogin)
    private val zephyrLoginField = JBTextField()
    private val zephyrPasswordLabel = JLabel(UiStrings.settingsPassword)
    private val zephyrPasswordField = JBPasswordField()
    private val addJiraProjectButton = JButton(UiStrings.settingsAddJiraProject)
    private val verifySettingsButton = JButton(UiStrings.settingsVerify)
    private val memoryButton = JButton(UiStrings.settingsOpenMemory)
    private val jiraProjectsPanel = JPanel()
    private val jiraProjects: MutableList<String> = mutableListOf()

    private val rootPanel = JPanel(BorderLayout(0, PluginUiTokens.contentGap))

    constructor(project: Project) : this(project, HttpBackendClient(project))

    override fun getDisplayName(): String = UiStrings.settingsTitle

    override fun createComponent(): JComponent {
        if (rootPanel.componentCount == 0) {
            buildUi()
        }
        return rootPanel
    }

    override fun isModified(): Boolean {
        val saved = settingsService.settings
        val currentAuthType = if (zephyrTokenRadio.isSelected) ZephyrAuthType.TOKEN else ZephyrAuthType.LOGIN_PASSWORD
        val currentToken = String(zephyrTokenField.password).trim().ifEmpty { null }
        val currentLogin = zephyrLoginField.text.trim().ifEmpty { null }
        val currentPassword = String(zephyrPasswordField.password).trim().ifEmpty { null }
        val currentJiraInstanceUrl = resolveJiraInstanceUrl(zephyrJiraInstanceCombo.selectedItem?.toString().orEmpty())
        val savedJiraInstanceUrl = resolveJiraInstanceUrl(saved.zephyrJiraInstance)
        return projectRootField.text.trim() != (saved.scanProjectRoot ?: "") ||
            currentAuthType != saved.zephyrAuthType ||
            currentToken != saved.zephyrToken ||
            currentLogin != saved.zephyrLogin ||
            currentPassword != saved.zephyrPassword ||
            currentJiraInstanceUrl != savedJiraInstanceUrl ||
            jiraProjects != saved.zephyrProjects
    }

    override fun apply() {
        settingsService.settings.scanProjectRoot = projectRootField.text.trim().ifEmpty { null }
        settingsService.settings.zephyrAuthType =
            if (zephyrTokenRadio.isSelected) ZephyrAuthType.TOKEN else ZephyrAuthType.LOGIN_PASSWORD
        settingsService.settings.zephyrToken = String(zephyrTokenField.password).trim().ifEmpty { null }
        settingsService.settings.zephyrLogin = zephyrLoginField.text.trim().ifEmpty { null }
        settingsService.settings.zephyrPassword = String(zephyrPasswordField.password).trim().ifEmpty { null }
        settingsService.settings.zephyrJiraInstance =
            resolveJiraInstanceUrl(zephyrJiraInstanceCombo.selectedItem?.toString().orEmpty()).orEmpty()
        settingsService.settings.zephyrProjects = jiraProjects.toMutableList()
    }

    override fun reset() {
        val saved = settingsService.settings
        if (rootPanel.componentCount == 0) {
            buildUi()
        }
        projectRootField.text = saved.scanProjectRoot ?: project.basePath.orEmpty()
        zephyrTokenField.text = saved.zephyrToken.orEmpty()
        zephyrLoginField.text = saved.zephyrLogin.orEmpty()
        zephyrPasswordField.text = saved.zephyrPassword.orEmpty()
        val savedJiraLabel = resolveJiraInstanceLabel(saved.zephyrJiraInstance)
        zephyrJiraInstanceCombo.setSelectedItem(savedJiraLabel)
        jiraProjects.clear()
        jiraProjects.addAll(saved.zephyrProjects)
        refreshJiraProjects()
        if (saved.zephyrAuthType == ZephyrAuthType.TOKEN) {
            zephyrTokenRadio.isSelected = true
        } else {
            zephyrLoginRadio.isSelected = true
        }
        updateZephyrAuthUi()
        loadIndexedSteps(projectRootField.text.trim())
    }

    private fun buildUi() {
        rootPanel.background = PluginUiTheme.panelBackground
        stepsList.emptyText.text = UiStrings.settingsStepsEmpty
        configureStepRenderer(stepsList)

        val leftContent = JPanel().apply {
            layout = BoxLayout(this, BoxLayout.Y_AXIS)
            isOpaque = false
            border = JBUI.Borders.empty(PluginUiTokens.panelInsets)
            add(SectionCard(UiStrings.settingsScanSection, UiStrings.settingsScanComment, buildScanControls()))
            add(Box.createVerticalStrut(PluginUiTokens.contentGap))
            add(SectionCard(UiStrings.settingsZephyrSection, UiStrings.settingsZephyrComment, buildZephyrControls()))
            add(Box.createVerticalStrut(PluginUiTokens.contentGap))
            add(SectionCard(UiStrings.settingsMemorySection, UiStrings.settingsMemoryComment, buildMemoryControls()))
            add(Box.createVerticalGlue())
        }

        val stepsPanel = SectionCard(
            UiStrings.settingsIndexedSteps,
            UiStrings.settingsScanComment,
            JBScrollPane(stepsList).apply { border = JBUI.Borders.empty() }
        )

        val mainSplitter = JBSplitter(true, 0.64f).apply {
            firstComponent = JBScrollPane(leftContent).apply {
                border = JBUI.Borders.empty()
                horizontalScrollBarPolicy = JBScrollPane.HORIZONTAL_SCROLLBAR_NEVER
            }
            secondComponent = JPanel(BorderLayout()).apply {
                isOpaque = false
                border = JBUI.Borders.empty(PluginUiTokens.panelInsets)
                add(stepsPanel, BorderLayout.CENTER)
            }
        }

        rootPanel.add(mainSplitter, BorderLayout.CENTER)
        rootPanel.add(statusLabel.apply {
            border = JBUI.Borders.empty(0, 12, 12, 12)
        }, BorderLayout.SOUTH)

        scanButton.addActionListener { runScanSteps() }
        ButtonGroup().apply {
            add(zephyrTokenRadio)
            add(zephyrLoginRadio)
        }
        zephyrTokenRadio.addActionListener { updateZephyrAuthUi() }
        zephyrLoginRadio.addActionListener { updateZephyrAuthUi() }
        addJiraProjectButton.addActionListener { promptAddJiraProject() }
        verifySettingsButton.addActionListener { verifyJiraProjectAvailability() }
        memoryButton.addActionListener { openMemoryManager() }

        jiraProjectsPanel.layout = BoxLayout(jiraProjectsPanel, BoxLayout.Y_AXIS)
        jiraProjectsPanel.isOpaque = false
        refreshJiraProjects()
        updateZephyrAuthUi()
    }

    private fun buildScanControls(): JPanel = panel {
        row(UiStrings.settingsProjectRoot) {
            cell(projectRootField).align(AlignX.FILL).resizableColumn()
            cell(scanButton)
        }
    }

    private fun buildZephyrControls(): JPanel {
        val authPanel = JPanel(FlowLayout(FlowLayout.LEFT, PluginUiTokens.blockGap, 0)).apply {
            isOpaque = false
            add(zephyrTokenRadio)
            add(zephyrLoginRadio)
        }
        return panel {
            row {
                cell(zephyrJiraLabel)
                cell(zephyrJiraInstanceCombo).resizableColumn().align(AlignX.FILL)
            }
            row(UiStrings.settingsAuthType) {
                cell(authPanel).align(AlignX.FILL)
            }
            row {
                cell(zephyrTokenLabel)
                cell(zephyrTokenField).resizableColumn().align(AlignX.FILL)
            }
            row {
                cell(zephyrLoginLabel)
                cell(zephyrLoginField).resizableColumn().align(AlignX.FILL)
            }
            row {
                cell(zephyrPasswordLabel)
                cell(zephyrPasswordField).resizableColumn().align(AlignX.FILL)
            }
            row(UiStrings.settingsProjects) {
                cell(jiraProjectsPanel).align(AlignX.FILL).resizableColumn()
            }
            row {
                cell(addJiraProjectButton)
                cell(verifySettingsButton)
            }
        }
    }

    private fun buildMemoryControls(): JPanel = panel {
        row {
            cell(memoryButton)
        }
    }

    private fun loadIndexedSteps(projectRoot: String) {
        if (projectRoot.isBlank()) return

        statusLabel.icon = AllIcons.General.BalloonInformation
        statusLabel.text = UiStrings.settingsLoadingIndex

        ProgressManager.getInstance().run(object : Task.Backgroundable(project, UiStrings.settingsLoadingIndex, true) {
            private var responseSteps = emptyList<StepDefinitionDto>()
            private var statusMessage: String = ""

            override fun run(indicator: ProgressIndicator) {
                indicator.text = "Обращение к сервису..."
                responseSteps = backendClient.listSteps(projectRoot)
                statusMessage = if (responseSteps.isEmpty()) {
                    "Сохранённые шаги не найдены"
                } else {
                    "Найдено ${responseSteps.size} шагов • Загружено из индекса"
                }
            }

            override fun onSuccess() {
                stepsList.setListData(responseSteps.toTypedArray())
                statusLabel.icon = AllIcons.General.InspectionsOK
                statusLabel.text = statusMessage
            }

            override fun onThrowable(error: Throwable) {
                val message = error.message ?: "Непредвиденная ошибка"
                statusLabel.icon = AllIcons.General.Warning
                statusLabel.text = "Не удалось загрузить индекс: $message"
                notify(message, NotificationType.WARNING)
            }
        })
    }

    private fun resolveMemoryProjectRoot(): String {
        return resolveMemoryProjectRootValue(
            preferredRoot = projectRootField.text.trim(),
            scanProjectRoot = settingsService.settings.scanProjectRoot,
            projectBasePath = project.basePath
        )
    }

    private fun openMemoryManager() {
        val projectRoot = resolveMemoryProjectRoot()
        if (projectRoot.isBlank()) {
            Messages.showWarningDialog(
                project,
                "Не удалось определить корень проекта. Укажите путь в разделе сканирования.",
                UiStrings.settingsMemorySection
            )
            return
        }
        MemoryManagerDialog(project, backendClient, projectRoot).show()
    }

    private fun updateZephyrAuthUi() {
        val tokenSelected = zephyrTokenRadio.isSelected
        setZephyrFieldState(zephyrTokenLabel, zephyrTokenField, tokenSelected)
        setZephyrFieldState(zephyrLoginLabel, zephyrLoginField, !tokenSelected)
        setZephyrFieldState(zephyrPasswordLabel, zephyrPasswordField, !tokenSelected)
        rootPanel.revalidate()
        rootPanel.repaint()
    }

    private fun promptAddJiraProject() {
        val projectKey = Messages.showInputDialog(
            rootPanel,
            "Введите ключ Jira-проекта",
            UiStrings.settingsAddJiraProject,
            Messages.getQuestionIcon()
        )?.trim().orEmpty()
        if (projectKey.isBlank()) return
        if (jiraProjects.contains(projectKey)) {
            notify("Проект уже добавлен", NotificationType.WARNING)
            return
        }
        jiraProjects.add(projectKey)
        refreshJiraProjects()
    }

    private fun refreshJiraProjects() {
        jiraProjectsPanel.removeAll()
        if (jiraProjects.isEmpty()) {
            jiraProjectsPanel.add(JLabel("Список проектов пуст").apply {
                foreground = PluginUiTheme.secondaryText
                alignmentX = Component.LEFT_ALIGNMENT
            })
        } else {
            jiraProjects.forEach { project ->
                createProjectRow(project).also {
                    it.alignmentX = Component.LEFT_ALIGNMENT
                    jiraProjectsPanel.add(it)
                }
                jiraProjectsPanel.add(Box.createVerticalStrut(PluginUiTokens.blockGap))
            }
        }
        jiraProjectsPanel.revalidate()
        jiraProjectsPanel.repaint()
    }

    private fun createProjectRow(projectKey: String): JPanel = JPanel(BorderLayout(PluginUiTokens.blockGap, 0)).apply {
        isOpaque = false
        add(JEditorPane().apply {
            contentType = "text/plain"
            text = projectKey
            isEditable = false
            background = PluginUiTheme.inputBackground
            border = JBUI.Borders.empty(6, 8)
        }, BorderLayout.CENTER)
        add(JButton("Удалить").apply {
            addActionListener {
                jiraProjects.remove(projectKey)
                refreshJiraProjects()
            }
        }, BorderLayout.EAST)
    }

    private fun verifyJiraProjectAvailability() {
        val jiraInstanceName = zephyrJiraInstanceCombo.selectedItem?.toString().orEmpty()
        val jiraBaseUrl = jiraInstanceOptions[jiraInstanceName]
        if (jiraBaseUrl.isNullOrBlank()) {
            notify("Не выбран Jira-инстанс", NotificationType.WARNING)
            return
        }
        val projectKey = jiraProjects.firstOrNull()?.trim().orEmpty()
        if (projectKey.isBlank()) {
            notify("Добавьте Jira-проект для проверки", NotificationType.WARNING)
            return
        }
        val tokenSelected = zephyrTokenRadio.isSelected
        val token = String(zephyrTokenField.password).trim()
        val login = zephyrLoginField.text.trim()
        val password = String(zephyrPasswordField.password).trim()
        if (tokenSelected && token.isBlank()) {
            notify("Укажите токен Jira", NotificationType.WARNING)
            return
        }
        if (!tokenSelected && (login.isBlank() || password.isBlank())) {
            notify("Укажите логин и пароль Jira", NotificationType.WARNING)
            return
        }

        ProgressManager.getInstance().run(object : Task.Backgroundable(project, "Проверка Jira-проекта", true) {
            private var statusMessage: String = ""

            override fun run(indicator: ProgressIndicator) {
                indicator.text = "Проверяем доступность проекта..."
                val settings = settingsService.settings
                val requestUrl = "${jiraBaseUrl.trimEnd('/')}/rest/api/2/project/${projectKey.trim()}/"
                val connection = (URL(requestUrl).openConnection() as HttpURLConnection).apply {
                    requestMethod = "GET"
                    connectTimeout = settings.requestTimeoutMs
                    readTimeout = settings.requestTimeoutMs
                    if (tokenSelected) {
                        setRequestProperty("Authorization", "Bearer $token")
                    } else {
                        val credentials = "$login:$password"
                        val encoded = Base64.getEncoder().encodeToString(credentials.toByteArray(Charsets.UTF_8))
                        setRequestProperty("Authorization", "Basic $encoded")
                    }
                }
                try {
                    val responseCode = connection.responseCode
                    if (responseCode !in 200..299) {
                        val errorBody = connection.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                        val message = errorBody.takeIf { it.isNotBlank() } ?: "HTTP $responseCode"
                        throw IllegalStateException("Jira ответила $responseCode: $message")
                    }
                } finally {
                    connection.disconnect()
                }
                statusMessage = "Проект $projectKey доступен"
            }

            override fun onSuccess() {
                notify(statusMessage, NotificationType.INFORMATION)
            }

            override fun onThrowable(error: Throwable) {
                val message = error.message ?: "Непредвиденная ошибка"
                notify("Проверка не удалась: $message", NotificationType.ERROR)
            }
        })
    }

    private fun setZephyrFieldState(label: JLabel, field: JComponent, isVisible: Boolean) {
        label.isVisible = isVisible
        field.isVisible = isVisible
        label.isEnabled = isVisible
        field.isEnabled = isVisible
    }

    private fun runScanSteps() {
        val projectRoot = projectRootField.text.trim()
            .ifEmpty { settingsService.settings.scanProjectRoot.orEmpty() }
            .ifEmpty { project.basePath.orEmpty() }
        if (projectRoot.isBlank()) {
            statusLabel.icon = AllIcons.General.Warning
            statusLabel.text = "Путь к проекту не указан"
            notify("Укажите путь к корню проекта", NotificationType.WARNING)
            return
        }
        settingsService.settings.scanProjectRoot = projectRoot

        statusLabel.icon = AllIcons.General.BalloonInformation
        statusLabel.text = "Идёт сканирование проекта..."

        ProgressManager.getInstance().run(object : Task.Backgroundable(project, "Сканирование шагов Cucumber", true) {
            private var responseSteps = emptyList<StepDefinitionDto>()
            private var responseUnmapped = emptyList<UnmappedStepDto>()
            private var statusMessage: String = ""

            override fun run(indicator: ProgressIndicator) {
                indicator.text = "Обращение к сервису..."
                val additionalRoots = StepScanRootsResolver.resolveAdditionalRoots(project, projectRoot)
                val response = backendClient.scanSteps(projectRoot, additionalRoots)
                responseSteps = response.sampleSteps.orEmpty()
                responseUnmapped = response.unmappedSteps
                val unmappedMessage = if (responseUnmapped.isEmpty()) "" else ", несопоставленных: ${responseUnmapped.size}"
                statusMessage = "Найдено ${response.stepsCount} шагов$unmappedMessage • Обновлено ${response.updatedAt}"
            }

            override fun onSuccess() {
                stepsList.setListData(responseSteps.toTypedArray())
                statusLabel.icon = AllIcons.General.InspectionsOK
                statusLabel.text = statusMessage
            }

            override fun onThrowable(error: Throwable) {
                val message = error.message ?: "Непредвиденная ошибка"
                statusLabel.icon = AllIcons.General.Error
                statusLabel.text = "Сканирование не удалось: $message"
                notify(message, NotificationType.ERROR)
            }
        })
    }

    private fun configureStepRenderer(list: JBList<StepDefinitionDto>) {
        list.cellRenderer = object : ColoredListCellRenderer<StepDefinitionDto>() {
            override fun customizeCellRenderer(
                list: javax.swing.JList<out StepDefinitionDto>,
                value: StepDefinitionDto?,
                index: Int,
                selected: Boolean,
                hasFocus: Boolean,
            ) {
                if (value == null) return
                val keywordAttributes = SimpleTextAttributes(SimpleTextAttributes.STYLE_UNDERLINE, JBColor(0x0B874B, 0x7DE390))
                append(value.keyword, keywordAttributes)
                append(" ${value.pattern}")
                val params = value.parameters.orEmpty()
                if (params.isNotEmpty()) {
                    val signature = params.joinToString(", ") { param ->
                        if (param.type.isNullOrBlank()) param.name else "${param.name}:${param.type}"
                    }
                    append(" [$signature]", SimpleTextAttributes.GRAYED_ATTRIBUTES)
                }
                value.summary?.takeIf { it.isNotBlank() }?.let {
                    append(" | $it", SimpleTextAttributes.GRAYED_ATTRIBUTES)
                }
            }
        }
    }

    private fun notify(message: String, type: NotificationType) {
        NotificationGroupManager.getInstance()
            .getNotificationGroup(UiStrings.pluginName)
            .createNotification(message, type)
            .notify(project)
    }

    private fun resolveJiraInstanceUrl(label: String): String? = jiraInstanceOptions[label] ?: label.takeIf { it.isNotBlank() }

    private fun resolveJiraInstanceLabel(url: String): String {
        return jiraInstanceOptions.entries.firstOrNull { it.value == url }?.key ?: url.ifBlank { jiraInstanceOptions.keys.first() }
    }

    companion object {
        private val jiraInstanceOptions = mapOf(
            "Sigma" to "https://jira.sberbank.ru"
        )
    }
}

internal fun resolveMemoryProjectRootValue(
    preferredRoot: String,
    scanProjectRoot: String?,
    projectBasePath: String?
): String {
    return preferredRoot.trim()
        .ifEmpty { scanProjectRoot.orEmpty().trim() }
        .ifEmpty { projectBasePath.orEmpty().trim() }
}

package ru.sber.aitestplugin.ui.dialogs

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.DialogWrapper
import com.intellij.openapi.ui.Messages
import com.intellij.ui.ColoredListCellRenderer
import com.intellij.ui.JBColor
import com.intellij.ui.SimpleTextAttributes
import com.intellij.ui.components.JBList
import com.intellij.ui.components.JBScrollPane
import com.intellij.ui.components.JBTextArea
import com.intellij.ui.components.JBTextField
import com.intellij.ui.dsl.builder.Align
import com.intellij.ui.dsl.builder.AlignX
import com.intellij.ui.dsl.builder.panel
import com.intellij.util.ui.JBUI
import ru.sber.aitestplugin.model.GenerationRuleActionsDto
import ru.sber.aitestplugin.model.GenerationRuleConditionDto
import ru.sber.aitestplugin.model.GenerationRuleCreateRequestDto
import ru.sber.aitestplugin.model.GenerationRuleDto
import ru.sber.aitestplugin.model.GenerationRulePatchRequestDto
import ru.sber.aitestplugin.model.StepTemplateCreateRequestDto
import ru.sber.aitestplugin.model.StepTemplateDto
import ru.sber.aitestplugin.model.StepTemplatePatchRequestDto
import ru.sber.aitestplugin.services.BackendClient
import ru.sber.aitestplugin.ui.UiStrings
import ru.sber.aitestplugin.ui.components.EmptyStatePanel
import ru.sber.aitestplugin.ui.components.SectionCard
import ru.sber.aitestplugin.ui.theme.PluginUiTheme
import ru.sber.aitestplugin.ui.theme.PluginUiTokens
import java.awt.BorderLayout
import java.awt.Dimension
import java.awt.FlowLayout
import javax.swing.BorderFactory
import javax.swing.DefaultListModel
import javax.swing.JButton
import javax.swing.JCheckBox
import javax.swing.JComboBox
import javax.swing.JComponent
import javax.swing.JList
import javax.swing.JPanel
import javax.swing.JSpinner
import javax.swing.JSplitPane
import javax.swing.JTabbedPane
import javax.swing.ListSelectionModel
import javax.swing.SpinnerNumberModel
import javax.swing.event.ListSelectionEvent

class MemoryManagerDialog(
    private val project: Project,
    private val backendClient: BackendClient,
    private val projectRoot: String
) : DialogWrapper(project, true) {
    private val rulesModel = DefaultListModel<GenerationRuleDto>()
    private val rulesList = JBList(rulesModel)
    private val templatesModel = DefaultListModel<StepTemplateDto>()
    private val templatesList = JBList(templatesModel)
    private val rulesDetailsArea = createDetailsArea()
    private val templatesDetailsArea = createDetailsArea()
    private val rulesStatusArea = createStatusArea()
    private val templatesStatusArea = createStatusArea()
    private val contentPanel = MemoryManagerContentPanel(
        projectRoot = projectRoot,
        rulesList = rulesList,
        templatesList = templatesList,
        rulesDetailsArea = rulesDetailsArea,
        templatesDetailsArea = templatesDetailsArea,
        rulesStatusArea = rulesStatusArea,
        templatesStatusArea = templatesStatusArea,
        onRefreshRules = ::loadRulesAsync,
        onAddRule = ::addRule,
        onEditRule = ::editRule,
        onDeleteRule = ::deleteRule,
        onRefreshTemplates = ::loadTemplatesAsync,
        onAddTemplate = ::addTemplate,
        onEditTemplate = ::editTemplate,
        onDeleteTemplate = ::deleteTemplate,
    )

    init {
        title = UiStrings.memoryProjectTitle
        init()
        configureLists()
        loadAllAsync()
    }

    override fun createCenterPanel(): JComponent = contentPanel

    private fun configureLists() {
        rulesList.emptyText.text = "Правила для этого корня проекта пока не сохранены."
        templatesList.emptyText.text = "Шаблоны для этого корня проекта пока не сохранены."
        rulesList.selectionMode = ListSelectionModel.SINGLE_SELECTION
        templatesList.selectionMode = ListSelectionModel.SINGLE_SELECTION

        rulesList.cellRenderer = object : ColoredListCellRenderer<GenerationRuleDto>() {
            override fun customizeCellRenderer(
                list: JList<out GenerationRuleDto>,
                value: GenerationRuleDto?,
                index: Int,
                selected: Boolean,
                hasFocus: Boolean
            ) {
                if (value == null) return
                append(value.name, SimpleTextAttributes.REGULAR_BOLD_ATTRIBUTES)
                append("  p=${value.priority}", SimpleTextAttributes.GRAYED_ATTRIBUTES)
                append("  ${if (value.enabled) "вкл" else "выкл"}", SimpleTextAttributes.GRAYED_ATTRIBUTES)
                val regex = value.condition.textRegex?.takeIf { it.isNotBlank() } ?: "без regex"
                append("  match=$regex", SimpleTextAttributes.GRAYED_ATTRIBUTES)
                append("  templates=${value.actions.applyTemplates.size}", SimpleTextAttributes.GRAYED_ATTRIBUTES)
            }
        }
        templatesList.cellRenderer = object : ColoredListCellRenderer<StepTemplateDto>() {
            override fun customizeCellRenderer(
                list: JList<out StepTemplateDto>,
                value: StepTemplateDto?,
                index: Int,
                selected: Boolean,
                hasFocus: Boolean
            ) {
                if (value == null) return
                append(value.name, SimpleTextAttributes.REGULAR_BOLD_ATTRIBUTES)
                append("  p=${value.priority}", SimpleTextAttributes.GRAYED_ATTRIBUTES)
                append("  ${if (value.enabled) "вкл" else "выкл"}", SimpleTextAttributes.GRAYED_ATTRIBUTES)
                append("  steps=${value.steps.size}", SimpleTextAttributes.GRAYED_ATTRIBUTES)
                val regex = value.triggerRegex?.takeIf { it.isNotBlank() } ?: "manual"
                append("  trigger=$regex", SimpleTextAttributes.GRAYED_ATTRIBUTES)
            }
        }

        rulesList.addListSelectionListener { event: ListSelectionEvent ->
            if (!event.valueIsAdjusting) {
                rulesDetailsArea.text = rulesList.selectedValue?.let(::formatRuleDetails)
                    ?: "Выберите правило, чтобы посмотреть условия и действия."
            }
        }
        templatesList.addListSelectionListener { event: ListSelectionEvent ->
            if (!event.valueIsAdjusting) {
                templatesDetailsArea.text = templatesList.selectedValue?.let(::formatTemplateDetails)
                    ?: "Выберите шаблон, чтобы посмотреть его шаги."
            }
        }

        rulesDetailsArea.text = "Выберите правило, чтобы посмотреть условия и действия."
        templatesDetailsArea.text = "Выберите шаблон, чтобы посмотреть его шаги."
        rulesStatusArea.text = buildProjectContextText("Правила")
        templatesStatusArea.text = buildProjectContextText("Шаблоны")
    }

    private fun loadAllAsync() {
        loadRulesAsync()
        loadTemplatesAsync()
    }

    private fun loadRulesAsync() {
        rulesStatusArea.text = "Загрузка правил для:\n$projectRoot"
        ApplicationManager.getApplication().executeOnPooledThread {
            runCatching { backendClient.listGenerationRules(projectRoot) }
                .onSuccess { response ->
                    ApplicationManager.getApplication().invokeLater {
                        rulesModel.clear()
                        response.items.forEach { rulesModel.addElement(it) }
                        rulesStatusArea.text = if (response.items.isEmpty()) {
                            buildProjectContextText("Правила") + "\n\nСохранённых правил пока нет."
                        } else {
                            buildProjectContextText("Правила") + "\n\nЗагружено правил: ${response.items.size}."
                        }
                    }
                }
                .onFailure { ex ->
                    ApplicationManager.getApplication().invokeLater {
                        rulesStatusArea.text = buildProjectContextText("Правила") +
                            "\n\nНе удалось загрузить правила:\n${ex.message ?: "Неизвестная ошибка"}"
                    }
                }
        }
    }

    private fun loadTemplatesAsync() {
        templatesStatusArea.text = "Загрузка шаблонов для:\n$projectRoot"
        ApplicationManager.getApplication().executeOnPooledThread {
            runCatching { backendClient.listStepTemplates(projectRoot) }
                .onSuccess { response ->
                    ApplicationManager.getApplication().invokeLater {
                        templatesModel.clear()
                        response.items.forEach { templatesModel.addElement(it) }
                        templatesStatusArea.text = if (response.items.isEmpty()) {
                            buildProjectContextText("Шаблоны") + "\n\nСохранённых шаблонов пока нет."
                        } else {
                            buildProjectContextText("Шаблоны") + "\n\nЗагружено шаблонов: ${response.items.size}."
                        }
                    }
                }
                .onFailure { ex ->
                    ApplicationManager.getApplication().invokeLater {
                        templatesStatusArea.text = buildProjectContextText("Шаблоны") +
                            "\n\nНе удалось загрузить шаблоны:\n${ex.message ?: "Неизвестная ошибка"}"
                    }
                }
        }
    }
    private fun addRule() {
        val formData = RuleEditorDialog(project, templatesModel.toItemList()).showAndGetResult() ?: return
        val request = GenerationRuleCreateRequestDto(
            projectRoot = projectRoot,
            name = formData.name,
            enabled = formData.enabled,
            priority = formData.priority,
            condition = GenerationRuleConditionDto(textRegex = formData.textRegex),
            actions = GenerationRuleActionsDto(
                qualityPolicy = formData.qualityPolicy,
                language = formData.language,
                targetPathTemplate = formData.targetPathTemplate,
                applyTemplates = formData.templateIds
            )
        )
        ApplicationManager.getApplication().executeOnPooledThread {
            runCatching { backendClient.createGenerationRule(request) }
                .onSuccess {
                    loadRulesAsync()
                    loadTemplatesAsync()
                }
                .onFailure { showError("Не удалось создать правило", it) }
        }
    }

    private fun editRule() {
        val selected = rulesList.selectedValue ?: return
        val formData = RuleEditorDialog(project, templatesModel.toItemList(), selected).showAndGetResult() ?: return
        val request = GenerationRulePatchRequestDto(
            projectRoot = projectRoot,
            name = formData.name,
            enabled = formData.enabled,
            priority = formData.priority,
            condition = GenerationRuleConditionDto(textRegex = formData.textRegex),
            actions = GenerationRuleActionsDto(
                qualityPolicy = formData.qualityPolicy,
                language = formData.language,
                targetPathTemplate = formData.targetPathTemplate,
                applyTemplates = formData.templateIds
            )
        )
        ApplicationManager.getApplication().executeOnPooledThread {
            runCatching { backendClient.updateGenerationRule(selected.id, request) }
                .onSuccess {
                    loadRulesAsync()
                    loadTemplatesAsync()
                }
                .onFailure { showError("Не удалось обновить правило", it) }
        }
    }

    private fun deleteRule() {
        val selected = rulesList.selectedValue ?: return
        val confirmed = Messages.showYesNoDialog(
            project,
            "Удалить правило '${selected.name}'?",
            "Удаление правила",
            Messages.getQuestionIcon()
        )
        if (confirmed != Messages.YES) return
        ApplicationManager.getApplication().executeOnPooledThread {
            runCatching { backendClient.deleteGenerationRule(selected.id, projectRoot) }
                .onSuccess { loadRulesAsync() }
                .onFailure { showError("Не удалось удалить правило", it) }
        }
    }

    private fun addTemplate() {
        val formData = TemplateEditorDialog(project).showAndGetResult() ?: return
        val request = StepTemplateCreateRequestDto(
            projectRoot = projectRoot,
            name = formData.name,
            enabled = formData.enabled,
            priority = formData.priority,
            triggerRegex = formData.triggerRegex,
            steps = formData.steps
        )
        ApplicationManager.getApplication().executeOnPooledThread {
            runCatching { backendClient.createStepTemplate(request) }
                .onSuccess { loadTemplatesAsync() }
                .onFailure { showError("Не удалось создать шаблон", it) }
        }
    }

    private fun editTemplate() {
        val selected = templatesList.selectedValue ?: return
        val formData = TemplateEditorDialog(project, selected).showAndGetResult() ?: return
        val request = StepTemplatePatchRequestDto(
            projectRoot = projectRoot,
            name = formData.name,
            enabled = formData.enabled,
            priority = formData.priority,
            triggerRegex = formData.triggerRegex,
            steps = formData.steps
        )
        ApplicationManager.getApplication().executeOnPooledThread {
            runCatching { backendClient.updateStepTemplate(selected.id, request) }
                .onSuccess { loadTemplatesAsync() }
                .onFailure { showError("Не удалось обновить шаблон", it) }
        }
    }

    private fun deleteTemplate() {
        val selected = templatesList.selectedValue ?: return
        val linkedRuleNames = rulesModel.toItemList()
            .filter { it.actions.applyTemplates.contains(selected.id) }
            .map { it.name }
        val warning = if (linkedRuleNames.isEmpty()) {
            "Удалить шаблон '${selected.name}'?"
        } else {
            "Удалить шаблон '${selected.name}'?\n\nОн используется в правилах: ${linkedRuleNames.joinToString(", ")}."
        }
        val confirmed = Messages.showYesNoDialog(
            project,
            warning,
            "Удаление шаблона",
            Messages.getWarningIcon()
        )
        if (confirmed != Messages.YES) return
        ApplicationManager.getApplication().executeOnPooledThread {
            runCatching { backendClient.deleteStepTemplate(selected.id, projectRoot) }
                .onSuccess {
                    loadTemplatesAsync()
                    loadRulesAsync()
                }
                .onFailure { showError("Не удалось удалить шаблон", it) }
        }
    }

    private fun buildProjectContextText(kind: String): String =
        "$kind для корня проекта:\n$projectRoot"

    private fun showError(title: String, throwable: Throwable) {
        ApplicationManager.getApplication().invokeLater {
            Messages.showErrorDialog(project, throwable.message ?: title, title)
        }
    }

    private fun formatRuleDetails(rule: GenerationRuleDto): String {
        val condition = rule.condition
        val actions = rule.actions
        return buildString {
            appendLine("Правило: ${rule.name}")
            appendLine("Id: ${rule.id}")
            appendLine("Включено: ${if (rule.enabled) "да" else "нет"}")
            appendLine("Приоритет: ${rule.priority}")
            appendLine("Источник: ${rule.source}")
            appendLine()
            appendLine("Условия")
            appendLine("- textRegex: ${condition.textRegex ?: "<none>"}")
            appendLine("- jiraKeyPattern: ${condition.jiraKeyPattern ?: "<none>"}")
            appendLine("- languageIn: ${condition.languageIn.joinToString().ifBlank { "<none>" }}")
            appendLine("- qualityPolicyIn: ${condition.qualityPolicyIn.joinToString().ifBlank { "<none>" }}")
            appendLine()
            appendLine("Действия")
            appendLine("- qualityPolicy: ${actions.qualityPolicy ?: "<none>"}")
            appendLine("- language: ${actions.language ?: "<none>"}")
            appendLine("- targetPathTemplate: ${actions.targetPathTemplate ?: "<none>"}")
            appendLine("- applyTemplates: ${resolveTemplateNames(actions.applyTemplates)}")
        }
    }

    private fun formatTemplateDetails(template: StepTemplateDto): String = buildString {
        appendLine("Шаблон: ${template.name}")
        appendLine("Id: ${template.id}")
        appendLine("Включено: ${if (template.enabled) "да" else "нет"}")
        appendLine("Приоритет: ${template.priority}")
        appendLine("Источник: ${template.source}")
        appendLine("Trigger regex: ${template.triggerRegex ?: "<none>"}")
        appendLine()
        appendLine("Шаги")
        template.steps.forEachIndexed { index, step ->
            appendLine("${index + 1}. $step")
        }
    }

    private fun resolveTemplateNames(templateIds: List<String>): String {
        if (templateIds.isEmpty()) return "<none>"
        val templatesById = templatesModel.toItemList().associateBy { it.id }
        return templateIds.joinToString { templateId ->
            templatesById[templateId]?.name ?: templateId
        }
    }
}

internal class MemoryManagerContentPanel(
    projectRoot: String,
    rulesList: JBList<GenerationRuleDto>,
    templatesList: JBList<StepTemplateDto>,
    rulesDetailsArea: JBTextArea,
    templatesDetailsArea: JBTextArea,
    rulesStatusArea: JBTextArea,
    templatesStatusArea: JBTextArea,
    onRefreshRules: () -> Unit,
    onAddRule: () -> Unit,
    onEditRule: () -> Unit,
    onDeleteRule: () -> Unit,
    onRefreshTemplates: () -> Unit,
    onAddTemplate: () -> Unit,
    onEditTemplate: () -> Unit,
    onDeleteTemplate: () -> Unit,
) : JPanel(BorderLayout(0, PluginUiTokens.contentGap)) {
    init {
        preferredSize = Dimension(1080, 720)
        border = JBUI.Borders.empty(PluginUiTokens.panelInsets)
        background = PluginUiTheme.panelBackground
        isOpaque = true

        add(buildProjectSummary(projectRoot), BorderLayout.NORTH)
        add(buildTabs(
            rulesList = rulesList,
            templatesList = templatesList,
            rulesDetailsArea = rulesDetailsArea,
            templatesDetailsArea = templatesDetailsArea,
            rulesStatusArea = rulesStatusArea,
            templatesStatusArea = templatesStatusArea,
            onRefreshRules = onRefreshRules,
            onAddRule = onAddRule,
            onEditRule = onEditRule,
            onDeleteRule = onDeleteRule,
            onRefreshTemplates = onRefreshTemplates,
            onAddTemplate = onAddTemplate,
            onEditTemplate = onEditTemplate,
            onDeleteTemplate = onDeleteTemplate,
        ), BorderLayout.CENTER)
    }

    private fun buildProjectSummary(projectRoot: String): JComponent = SectionCard(
        title = "Контекст памяти",
        comment = "Правила и шаблоны применяются backend-сервисом при генерации feature-файлов.",
        content = panel {
            row("Корень проекта") {
                textField().applyToComponent {
                    text = projectRoot
                    isEditable = false
                    background = PluginUiTheme.inputBackground
                }.resizableColumn().align(AlignX.FILL)
            }
        }
    )

    private fun buildTabs(
        rulesList: JBList<GenerationRuleDto>,
        templatesList: JBList<StepTemplateDto>,
        rulesDetailsArea: JBTextArea,
        templatesDetailsArea: JBTextArea,
        rulesStatusArea: JBTextArea,
        templatesStatusArea: JBTextArea,
        onRefreshRules: () -> Unit,
        onAddRule: () -> Unit,
        onEditRule: () -> Unit,
        onDeleteRule: () -> Unit,
        onRefreshTemplates: () -> Unit,
        onAddTemplate: () -> Unit,
        onEditTemplate: () -> Unit,
        onDeleteTemplate: () -> Unit,
    ): JComponent = JTabbedPane().apply {
        addTab(UiStrings.memoryRulesTab, buildMemoryTab(
            title = "Правила генерации",
            comment = "Условия и действия, которые автоматически влияют на quality policy, язык и путь генерации.",
            listTitle = "Список правил",
            listComment = "Выберите правило слева, чтобы посмотреть его структуру и связанные шаблоны.",
            list = rulesList,
            detailsTitle = "Детали правила",
            detailsComment = "Состав условия, вычисленные действия и привязанные шаблоны.",
            detailsArea = rulesDetailsArea,
            statusTitle = "Состояние загрузки",
            statusArea = rulesStatusArea,
            emptyState = EmptyStatePanel("Правил пока нет", "Добавьте первое правило, чтобы зафиксировать память проекта."),
            toolbarActions = listOf(
                toolbarButton(UiStrings.memoryRefresh, onRefreshRules),
                toolbarButton(UiStrings.memoryAdd, onAddRule),
                toolbarButton(UiStrings.memoryEdit, onEditRule),
                toolbarButton(UiStrings.memoryDelete, onDeleteRule),
            )
        ))
        addTab(UiStrings.memoryTemplatesTab, buildMemoryTab(
            title = "Шаблоны шагов",
            comment = "Повторно используемые шаги, которые можно вызывать вручную или привязывать к правилам.",
            listTitle = "Список шаблонов",
            listComment = "Храните короткие и целевые шаблоны; длинные сценарии сложнее поддерживать.",
            list = templatesList,
            detailsTitle = "Детали шаблона",
            detailsComment = "Просмотр шагов, триггера и служебных полей выбранного шаблона.",
            detailsArea = templatesDetailsArea,
            statusTitle = "Состояние загрузки",
            statusArea = templatesStatusArea,
            emptyState = EmptyStatePanel("Шаблонов пока нет", "Добавьте шаблон, чтобы переиспользовать шаги в нескольких правилах."),
            toolbarActions = listOf(
                toolbarButton(UiStrings.memoryRefresh, onRefreshTemplates),
                toolbarButton(UiStrings.memoryAdd, onAddTemplate),
                toolbarButton(UiStrings.memoryEdit, onEditTemplate),
                toolbarButton(UiStrings.memoryDelete, onDeleteTemplate),
            )
        ))
    }

    private fun buildMemoryTab(
        title: String,
        comment: String,
        listTitle: String,
        listComment: String,
        list: JBList<*>,
        detailsTitle: String,
        detailsComment: String,
        detailsArea: JBTextArea,
        statusTitle: String,
        statusArea: JBTextArea,
        emptyState: JComponent,
        toolbarActions: List<JButton>,
    ): JComponent = JPanel(BorderLayout(0, PluginUiTokens.contentGap)).apply {
        isOpaque = false
        border = JBUI.Borders.emptyTop(4)
        add(SectionCard(title, comment, buildToolbar(toolbarActions)), BorderLayout.NORTH)
        add(
            JSplitPane(
                JSplitPane.HORIZONTAL_SPLIT,
                SectionCard(listTitle, listComment, buildListSection(list, emptyState)),
                JPanel(BorderLayout(0, PluginUiTokens.contentGap)).apply {
                    isOpaque = false
                    add(
                        SectionCard(detailsTitle, detailsComment, JBScrollPane(detailsArea).apply {
                            border = JBUI.Borders.empty()
                            preferredSize = PluginUiTokens.detailsPreviewSize
                            viewport.background = PluginUiTheme.inputBackground
                        }),
                        BorderLayout.CENTER
                    )
                    add(
                        SectionCard(
                            statusTitle,
                            "Последний ответ backend и контекст выбранного project root.",
                            JBScrollPane(statusArea).apply {
                                border = JBUI.Borders.empty()
                                preferredSize = Dimension(420, JBUI.scale(160))
                                viewport.background = PluginUiTheme.inputBackground
                            }
                        ),
                        BorderLayout.SOUTH
                    )
                }
            ).apply {
                border = JBUI.Borders.empty()
                resizeWeight = 0.42
                dividerSize = JBUI.scale(10)
                isOpaque = false
            },
            BorderLayout.CENTER
        )
    }

    private fun buildToolbar(actions: List<JButton>): JComponent = JPanel(FlowLayout(FlowLayout.LEFT, JBUI.scale(8), 0)).apply {
        isOpaque = false
        actions.forEach(::add)
    }

    private fun buildListSection(list: JBList<*>, emptyState: JComponent): JComponent = JPanel(BorderLayout(0, PluginUiTokens.blockGap)).apply {
        isOpaque = false
        add(JBScrollPane(list).apply {
            border = BorderFactory.createLineBorder(PluginUiTheme.containerBorder, 1, true)
            preferredSize = Dimension(420, 420)
        }, BorderLayout.CENTER)
        add(emptyState, BorderLayout.SOUTH)
    }
}

private class TemplateEditorDialog(
    project: Project,
    existing: StepTemplateDto? = null,
) : DialogWrapper(project, true) {
    private val nameField = JBTextField(existing?.name.orEmpty())
    private val enabledCheckbox = JCheckBox("Включён", existing?.enabled ?: true)
    private val prioritySpinner = JSpinner(SpinnerNumberModel(existing?.priority ?: 100, 0, 10000, 1))
    private val triggerField = JBTextField(existing?.triggerRegex.orEmpty())
    private val stepsArea = JBTextArea(existing?.steps?.joinToString("\n").orEmpty()).apply {
        lineWrap = true
        wrapStyleWord = true
        rows = 10
        background = PluginUiTheme.inputBackground
        foreground = PluginUiTheme.primaryText
        border = JBUI.Borders.empty(8)
    }
    private var resultData: TemplateFormData? = null

    init {
        title = if (existing == null) "Новый шаблон" else "Редактирование шаблона"
        init()
    }

    override fun createCenterPanel(): JComponent = panel {
        row("Название") {
            cell(nameField).resizableColumn().align(AlignX.FILL)
        }
        row {
            cell(enabledCheckbox)
        }
        row("Приоритет") {
            cell(prioritySpinner)
        }
        row("Trigger regex") {
            cell(triggerField).resizableColumn().align(AlignX.FILL)
        }
        row("Шаги") {
            cell(JBScrollPane(stepsArea).apply {
                preferredSize = Dimension(420, 220)
                border = BorderFactory.createLineBorder(PluginUiTheme.containerBorder, 1, true)
                viewport.background = PluginUiTheme.inputBackground
            }).resizableColumn().align(Align.FILL)
        }
        row {
            comment("Один шаг на строку. Пустые строки будут отброшены.")
        }
    }

    override fun doOKAction() {
        val name = nameField.text.trim()
        val steps = stepsArea.text.lines().map(String::trim).filter(String::isNotBlank)
        when {
            name.isBlank() -> {
                setErrorText("Название шаблона не должно быть пустым.")
                return
            }
            steps.isEmpty() -> {
                setErrorText("Шаблон должен содержать хотя бы один шаг.")
                return
            }
            else -> setErrorText(null)
        }
        resultData = TemplateFormData(
            name = name,
            enabled = enabledCheckbox.isSelected,
            priority = (prioritySpinner.value as Number).toInt(),
            triggerRegex = triggerField.text.trim().ifBlank { null },
            steps = steps
        )
        super.doOKAction()
    }

    fun showAndGetResult(): TemplateFormData? = if (showAndGet()) resultData else null
}

private class RuleEditorDialog(
    project: Project,
    templates: List<StepTemplateDto>,
    existing: GenerationRuleDto? = null,
) : DialogWrapper(project, true) {
    private val nameField = JBTextField(existing?.name.orEmpty())
    private val enabledCheckbox = JCheckBox("Включено", existing?.enabled ?: true)
    private val prioritySpinner = JSpinner(SpinnerNumberModel(existing?.priority ?: 100, 0, 10000, 1))
    private val textRegexField = JBTextField(existing?.condition?.textRegex.orEmpty())
    private val targetPathField = JBTextField(existing?.actions?.targetPathTemplate.orEmpty())
    private val qualityCombo = JComboBox(arrayOf("", "strict", "balanced", "lenient")).apply {
        selectedItem = existing?.actions?.qualityPolicy.orEmpty()
    }
    private val languageCombo = JComboBox(arrayOf("", "ru", "en")).apply {
        selectedItem = existing?.actions?.language.orEmpty()
    }
    private val templateList = JBList(DefaultListModel<StepTemplateDto>().apply {
        templates.forEach(::addElement)
    }).apply {
        selectionMode = ListSelectionModel.MULTIPLE_INTERVAL_SELECTION
        visibleRowCount = 8
    }
    private var resultData: RuleFormData? = null

    init {
        val selectedTemplateIds = existing?.actions?.applyTemplates.orEmpty().toSet()
        val selectedIndices = templates.mapIndexedNotNull { index, template ->
            index.takeIf { template.id in selectedTemplateIds }
        }.toIntArray()
        if (selectedIndices.isNotEmpty()) {
            templateList.selectedIndices = selectedIndices
        }
        title = if (existing == null) "Новое правило" else "Редактирование правила"
        init()
    }

    override fun createCenterPanel(): JComponent = panel {
        row("Название") {
            cell(nameField).resizableColumn().align(AlignX.FILL)
        }
        row {
            cell(enabledCheckbox)
        }
        row("Приоритет") {
            cell(prioritySpinner)
        }
        row("Text regex") {
            cell(textRegexField).resizableColumn().align(AlignX.FILL)
        }
        row("Quality policy") {
            cell(qualityCombo)
        }
        row("Язык") {
            cell(languageCombo)
        }
        row("Target path template") {
            cell(targetPathField).resizableColumn().align(AlignX.FILL)
        }
        row("Шаблоны") {
            cell(JBScrollPane(templateList).apply {
                preferredSize = Dimension(360, 180)
                border = BorderFactory.createLineBorder(PluginUiTheme.containerBorder, 1, true)
                viewport.background = JBColor.PanelBackground
            }).resizableColumn().align(Align.FILL)
        }
        row {
            comment("Правило должно менять хотя бы одно свойство генерации или ссылаться на шаблон.")
        }
    }

    override fun doOKAction() {
        val templateIds = templateList.selectedValuesList.map { it.id }
        val name = nameField.text.trim()
        val textRegex = textRegexField.text.trim().ifBlank { null }
        val quality = qualityCombo.selectedItem?.toString()?.trim().orEmpty().ifBlank { null }
        val language = languageCombo.selectedItem?.toString()?.trim().orEmpty().ifBlank { null }
        val targetPathTemplate = targetPathField.text.trim().ifBlank { null }
        when {
            name.isBlank() -> {
                setErrorText("Название правила не должно быть пустым.")
                return
            }
            textRegex == null && quality == null && language == null && targetPathTemplate == null && templateIds.isEmpty() -> {
                setErrorText("Правило должно задавать хотя бы одно условие или действие.")
                return
            }
            else -> setErrorText(null)
        }
        resultData = RuleFormData(
            name = name,
            enabled = enabledCheckbox.isSelected,
            priority = (prioritySpinner.value as Number).toInt(),
            textRegex = textRegex,
            qualityPolicy = quality,
            language = language,
            targetPathTemplate = targetPathTemplate,
            templateIds = templateIds
        )
        super.doOKAction()
    }

    fun showAndGetResult(): RuleFormData? = if (showAndGet()) resultData else null
}

private fun toolbarButton(text: String, action: () -> Unit): JButton = JButton(text).apply {
    background = PluginUiTheme.controlBackground
    foreground = PluginUiTheme.primaryText
    border = BorderFactory.createLineBorder(PluginUiTheme.controlBorder, 1, true)
    isFocusPainted = false
    isContentAreaFilled = true
    putClientProperty("JButton.buttonType", "roundRect")
    addActionListener { action() }
}

private fun createDetailsArea(): JBTextArea = JBTextArea().apply {
    isEditable = false
    lineWrap = true
    wrapStyleWord = true
    background = PluginUiTheme.inputBackground
    foreground = PluginUiTheme.primaryText
    border = JBUI.Borders.empty(10)
}

private fun createStatusArea(): JBTextArea = JBTextArea().apply {
    isEditable = false
    lineWrap = true
    wrapStyleWord = true
    background = PluginUiTheme.inputBackground
    foreground = PluginUiTheme.secondaryText
    border = JBUI.Borders.empty(10)
}

private data class TemplateFormData(
    val name: String,
    val enabled: Boolean,
    val priority: Int,
    val triggerRegex: String?,
    val steps: List<String>
)

private data class RuleFormData(
    val name: String,
    val enabled: Boolean,
    val priority: Int,
    val textRegex: String?,
    val qualityPolicy: String?,
    val language: String?,
    val targetPathTemplate: String?,
    val templateIds: List<String>
)

private fun <T> DefaultListModel<T>.toItemList(): List<T> = (0 until size()).map(::getElementAt)


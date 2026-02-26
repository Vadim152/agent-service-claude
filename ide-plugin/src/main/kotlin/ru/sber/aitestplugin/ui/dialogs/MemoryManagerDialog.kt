package ru.sber.aitestplugin.ui.dialogs

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.DialogWrapper
import com.intellij.openapi.ui.Messages
import com.intellij.ui.components.JBList
import com.intellij.ui.components.JBScrollPane
import com.intellij.util.ui.JBUI
import ru.sber.aitestplugin.model.GenerationRuleActionsDto
import ru.sber.aitestplugin.model.GenerationRuleConditionDto
import ru.sber.aitestplugin.model.GenerationRuleCreateRequestDto
import ru.sber.aitestplugin.model.GenerationRuleDto
import ru.sber.aitestplugin.model.StepTemplateCreateRequestDto
import ru.sber.aitestplugin.model.StepTemplateDto
import ru.sber.aitestplugin.services.BackendClient
import java.awt.BorderLayout
import java.awt.Dimension
import java.awt.GridLayout
import javax.swing.DefaultListModel
import javax.swing.JButton
import javax.swing.JComponent
import javax.swing.JPanel
import javax.swing.JTabbedPane

class MemoryManagerDialog(
    private val project: Project,
    private val backendClient: BackendClient,
    private val projectRoot: String
) : DialogWrapper(project, true) {
    private val rulesModel = DefaultListModel<GenerationRuleDto>()
    private val rulesList = JBList(rulesModel)
    private val templatesModel = DefaultListModel<StepTemplateDto>()
    private val templatesList = JBList(templatesModel)

    init {
        title = "Memory Rules & Templates"
        init()
        loadAllAsync()
    }

    override fun createCenterPanel(): JComponent {
        val tabs = JTabbedPane()
        tabs.addTab("Rules", buildRulesPanel())
        tabs.addTab("Templates", buildTemplatesPanel())

        return JPanel(BorderLayout()).apply {
            preferredSize = Dimension(780, 520)
            border = JBUI.Borders.empty(8)
            add(tabs, BorderLayout.CENTER)
        }
    }

    private fun buildRulesPanel(): JPanel = JPanel(BorderLayout()).apply {
        add(JBScrollPane(rulesList), BorderLayout.CENTER)
        add(
            JPanel(GridLayout(1, 0, 8, 0)).apply {
                border = JBUI.Borders.emptyTop(8)
                add(JButton("Refresh").apply { addActionListener { loadRulesAsync() } })
                add(JButton("Add").apply { addActionListener { addRule() } })
                add(JButton("Delete").apply { addActionListener { deleteRule() } })
            },
            BorderLayout.SOUTH
        )
    }

    private fun buildTemplatesPanel(): JPanel = JPanel(BorderLayout()).apply {
        add(JBScrollPane(templatesList), BorderLayout.CENTER)
        add(
            JPanel(GridLayout(1, 0, 8, 0)).apply {
                border = JBUI.Borders.emptyTop(8)
                add(JButton("Refresh").apply { addActionListener { loadTemplatesAsync() } })
                add(JButton("Add").apply { addActionListener { addTemplate() } })
                add(JButton("Delete").apply { addActionListener { deleteTemplate() } })
            },
            BorderLayout.SOUTH
        )
    }

    private fun loadAllAsync() {
        loadRulesAsync()
        loadTemplatesAsync()
    }

    private fun loadRulesAsync() {
        ApplicationManager.getApplication().executeOnPooledThread {
            runCatching { backendClient.listGenerationRules(projectRoot) }
                .onSuccess { response ->
                    ApplicationManager.getApplication().invokeLater {
                        rulesModel.clear()
                        response.items.forEach { rulesModel.addElement(it) }
                    }
                }
                .onFailure { ex ->
                    ApplicationManager.getApplication().invokeLater {
                        Messages.showErrorDialog(project, ex.message ?: "Failed to load rules", "Memory")
                    }
                }
        }
    }

    private fun loadTemplatesAsync() {
        ApplicationManager.getApplication().executeOnPooledThread {
            runCatching { backendClient.listStepTemplates(projectRoot) }
                .onSuccess { response ->
                    ApplicationManager.getApplication().invokeLater {
                        templatesModel.clear()
                        response.items.forEach { templatesModel.addElement(it) }
                    }
                }
                .onFailure { ex ->
                    ApplicationManager.getApplication().invokeLater {
                        Messages.showErrorDialog(project, ex.message ?: "Failed to load templates", "Memory")
                    }
                }
        }
    }

    private fun addRule() {
        val name = Messages.showInputDialog(project, "Rule name", "Add Rule", null)?.trim().orEmpty()
        if (name.isBlank()) return
        val textRegex = Messages.showInputDialog(project, "Optional text regex", "Add Rule", null)?.trim().orEmpty()
        val templateIdsRaw = Messages.showInputDialog(project, "Apply template IDs (comma-separated)", "Add Rule", null)?.trim().orEmpty()
        val templateIds = templateIdsRaw.split(',').map { it.trim() }.filter { it.isNotBlank() }

        val request = GenerationRuleCreateRequestDto(
            projectRoot = projectRoot,
            name = name,
            condition = GenerationRuleConditionDto(textRegex = textRegex.ifBlank { null }),
            actions = GenerationRuleActionsDto(applyTemplates = templateIds)
        )

        ApplicationManager.getApplication().executeOnPooledThread {
            runCatching { backendClient.createGenerationRule(request) }
                .onSuccess { loadRulesAsync() }
                .onFailure { ex ->
                    ApplicationManager.getApplication().invokeLater {
                        Messages.showErrorDialog(project, ex.message ?: "Failed to add rule", "Memory")
                    }
                }
        }
    }

    private fun deleteRule() {
        val selected = rulesList.selectedValue ?: return
        ApplicationManager.getApplication().executeOnPooledThread {
            runCatching { backendClient.deleteGenerationRule(selected.id, projectRoot) }
                .onSuccess { loadRulesAsync() }
                .onFailure { ex ->
                    ApplicationManager.getApplication().invokeLater {
                        Messages.showErrorDialog(project, ex.message ?: "Failed to delete rule", "Memory")
                    }
                }
        }
    }

    private fun addTemplate() {
        val name = Messages.showInputDialog(project, "Template name", "Add Template", null)?.trim().orEmpty()
        if (name.isBlank()) return
        val triggerRegex = Messages.showInputDialog(project, "Optional trigger regex", "Add Template", null)?.trim().orEmpty()
        val stepsRaw = Messages.showMultilineInputDialog(
            project,
            "Template steps (one per line)",
            "Add Template",
            "Given user is authorized\nWhen user opens draft",
            null,
            null
        )?.trim().orEmpty()
        val steps = stepsRaw.lines().map { it.trim() }.filter { it.isNotBlank() }
        if (steps.isEmpty()) {
            Messages.showWarningDialog(project, "Template must contain at least one step", "Memory")
            return
        }

        val request = StepTemplateCreateRequestDto(
            projectRoot = projectRoot,
            name = name,
            triggerRegex = triggerRegex.ifBlank { null },
            steps = steps
        )

        ApplicationManager.getApplication().executeOnPooledThread {
            runCatching { backendClient.createStepTemplate(request) }
                .onSuccess { loadTemplatesAsync() }
                .onFailure { ex ->
                    ApplicationManager.getApplication().invokeLater {
                        Messages.showErrorDialog(project, ex.message ?: "Failed to add template", "Memory")
                    }
                }
        }
    }

    private fun deleteTemplate() {
        val selected = templatesList.selectedValue ?: return
        ApplicationManager.getApplication().executeOnPooledThread {
            runCatching { backendClient.deleteStepTemplate(selected.id, projectRoot) }
                .onSuccess { loadTemplatesAsync() }
                .onFailure { ex ->
                    ApplicationManager.getApplication().invokeLater {
                        Messages.showErrorDialog(project, ex.message ?: "Failed to delete template", "Memory")
                    }
                }
        }
    }
}

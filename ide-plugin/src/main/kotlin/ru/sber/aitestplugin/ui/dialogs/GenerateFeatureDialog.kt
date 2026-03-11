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
import ru.sber.aitestplugin.model.AssumptionDto
import ru.sber.aitestplugin.model.GenerationPreviewRequestDto
import ru.sber.aitestplugin.model.GenerationPreviewResponseDto
import ru.sber.aitestplugin.model.GenerationResolvePreviewResponseDto
import ru.sber.aitestplugin.model.ScenarioCandidateDto
import ru.sber.aitestplugin.model.SimilarScenarioDto
import ru.sber.aitestplugin.services.BackendClient
import ru.sber.aitestplugin.ui.UiStrings
import java.awt.CardLayout
import javax.swing.AbstractAction
import javax.swing.Action
import javax.swing.BoxLayout
import javax.swing.JButton
import javax.swing.JComboBox
import javax.swing.JComponent
import javax.swing.JLabel
import javax.swing.JPanel
import javax.swing.event.DocumentEvent
import javax.swing.event.DocumentListener

private enum class WizardStep {
    CLARIFY,
    REVIEW,
}

class GenerateFeatureDialog(
    project: Project,
    defaults: GenerateFeatureDialogOptions,
    private val backendClient: BackendClient,
    private val projectRoot: String,
    private val testCaseText: String
) : DialogWrapper(project) {
    private val targetPathField = JBTextField(defaults.targetPath ?: "")
    private val createFileCheckbox = JBCheckBox(UiStrings.dialogCreateFile, false)
    private val overwriteCheckbox = JBCheckBox(UiStrings.dialogOverwriteFile, defaults.overwriteExisting)
    private val defaultLanguage = defaults.language

    private val wizardHeaderLabel = JLabel("Step 1 of 2: Clarify intent")
    private val clarifyStatusLabel = JLabel(UiStrings.dialogLoadingPreview)
    private val reviewStatusLabel = JLabel("Review will be available after required clarifications are provided.")

    private val actorField = JBTextField()
    private val goalField = JBTextField()
    private val expectedOutcomeArea = newEditorArea(rows = 3)
    private val preconditionsArea = newEditorArea(rows = 3)
    private val dataDimensionsArea = newEditorArea(rows = 3)
    private val clarifyPreviewArea = newEditorArea(rows = 12, editable = false)
    private val reviewPreviewArea = newEditorArea(rows = 16, editable = false)

    private val scenarioSelector = JComboBox<String>()
    private val assumptionsPanel = JPanel().apply {
        layout = BoxLayout(this, BoxLayout.Y_AXIS)
        isOpaque = false
        border = JBUI.Borders.empty(4, 0)
    }
    private val clarifyRefreshButton = JButton("Refresh analysis")
    private val reviewRefreshButton = JButton(UiStrings.dialogRefreshPreview)

    private val wizardLayout = CardLayout()
    private val wizardPanel = JPanel(wizardLayout)
    private val clarifyStepPanel: JComponent
    private val reviewStepPanel: JComponent

    private val backAction = object : AbstractAction("Back") {
        override fun actionPerformed(event: java.awt.event.ActionEvent?) {
            switchStep(WizardStep.CLARIFY)
        }
    }

    private var currentStep = WizardStep.CLARIFY
    private var previewLoading = false
    private var updatingScenarioSelector = false

    private var latestPreview: GenerationPreviewResponseDto? = null
    private var similarScenarios: List<SimilarScenarioDto> = emptyList()
    private var scenarioCandidates: List<ScenarioCandidateDto> = emptyList()
    private val assumptionCheckboxes = linkedMapOf<String, JBCheckBox>()

    init {
        title = UiStrings.generateFeatureTitle
        createFileCheckbox.isEnabled = false
        createFileCheckbox.toolTipText = "Draft is created in the editor. Save to disk via Apply after review."

        scenarioSelector.isEnabled = false
        scenarioSelector.addActionListener {
            if (updatingScenarioSelector) {
                return@addActionListener
            }
            refreshReviewPreview()
            if (currentStep == WizardStep.REVIEW && !previewLoading && scenarioCandidates.isNotEmpty()) {
                loadPreviewAsync(advanceToReview = true)
            }
        }
        clarifyRefreshButton.addActionListener {
            loadPreviewAsync(advanceToReview = false)
        }
        reviewRefreshButton.addActionListener {
            loadPreviewAsync(advanceToReview = currentStep == WizardStep.REVIEW)
        }

        attachMandatoryFieldListener(actorField)
        attachMandatoryFieldListener(goalField)
        attachMandatoryFieldListener(expectedOutcomeArea)

        clarifyStepPanel = buildClarifyStepPanel(
            actorField = actorField,
            goalField = goalField,
            expectedOutcomeArea = expectedOutcomeArea,
            preconditionsArea = preconditionsArea,
            dataDimensionsArea = dataDimensionsArea,
            clarifyStatusLabel = clarifyStatusLabel,
            clarifyPreviewArea = clarifyPreviewArea,
            refreshPreviewButton = clarifyRefreshButton,
        )
        reviewStepPanel = buildReviewStepPanel(
            reviewStatusLabel = reviewStatusLabel,
            scenarioSelector = scenarioSelector,
            assumptionsPanel = assumptionsPanel,
            reviewPreviewArea = reviewPreviewArea,
            refreshPreviewButton = reviewRefreshButton,
        )

        wizardPanel.add(clarifyStepPanel, WizardStep.CLARIFY.name)
        wizardPanel.add(reviewStepPanel, WizardStep.REVIEW.name)

        init()
        switchStep(WizardStep.CLARIFY)
        loadPreviewAsync()
    }

    override fun createCenterPanel(): JComponent = buildGenerateFeatureWizardPanel(
        targetPathField = targetPathField,
        createFileCheckbox = createFileCheckbox,
        overwriteCheckbox = overwriteCheckbox,
        wizardHeaderLabel = wizardHeaderLabel,
        wizardPanel = wizardPanel,
    )

    override fun createLeftSideActions(): Array<Action> = arrayOf(backAction)

    override fun doOKAction() {
        when (currentStep) {
            WizardStep.CLARIFY -> {
                if (!isClarificationComplete(actorField.text, goalField.text, expectedOutcomeArea.text)) {
                    clarifyStatusLabel.text = "Actor, goal, and expected outcome are required before review."
                    updateActionState()
                    return
                }
                loadPreviewAsync(advanceToReview = true)
            }

            WizardStep.REVIEW -> {
                if (generationBlocked()) {
                    reviewStatusLabel.text = "Generation is blocked until critical ambiguity is resolved."
                    updateActionState()
                    return
                }
                super.doOKAction()
            }
        }
    }

    fun targetPath(): String? = targetPathField.text.trim().takeIf { it.isNotEmpty() }

    fun shouldCreateFile(): Boolean = false

    fun shouldOverwriteExisting(): Boolean = overwriteCheckbox.isSelected

    fun selectedOptions(): GenerateFeatureDialogOptions = GenerateFeatureDialogOptions(
        targetPath = targetPath(),
        createFile = false,
        overwriteExisting = shouldOverwriteExisting(),
        language = defaultLanguage,
    )

    fun selectedScenarioId(): String? =
        selectedBaseScenario()?.scenarioId ?: latestPreview?.generationPlan?.selectedScenarioId

    fun selectedScenarioCandidateId(): String? =
        selectedScenarioCandidate()?.id ?: latestPreview?.selectedScenarioCandidateId

    fun acceptedAssumptionIds(): List<String> = collectAcceptedAssumptionIds(
        latestPreview = latestPreview,
        selectedCandidate = selectedScenarioCandidate(),
        assumptionCheckboxes = assumptionCheckboxes,
    )

    fun confirmedClarifications(): Map<String, String> = buildClarificationPayload(
        actor = actorField.text,
        goal = goalField.text,
        expectedOutcome = expectedOutcomeArea.text,
        preconditions = preconditionsArea.text,
        dataDimensions = dataDimensionsArea.text,
    )

    fun generationBlocked(): Boolean = latestPreview?.generationBlocked == true

    fun planId(): String? = latestPreview?.planId

    private fun selectedBaseScenario(): SimilarScenarioDto? =
        similarScenarios.firstOrNull { it.recommended } ?: similarScenarios.firstOrNull()

    private fun selectedScenarioCandidate(): ScenarioCandidateDto? {
        val index = scenarioSelector.selectedIndex
        return if (index in scenarioCandidates.indices) scenarioCandidates[index] else null
    }

    private fun switchStep(step: WizardStep) {
        currentStep = step
        wizardLayout.show(wizardPanel, step.name)
        wizardHeaderLabel.text = when (step) {
            WizardStep.CLARIFY -> "Step 1 of 2: Clarify intent"
            WizardStep.REVIEW -> "Step 2 of 2: Review candidates"
        }
        updateActionState()
    }

    private fun updateActionState() {
        isOKActionEnabled = when (currentStep) {
            WizardStep.CLARIFY -> !previewLoading && isClarificationComplete(
                actorField.text,
                goalField.text,
                expectedOutcomeArea.text,
            )

            WizardStep.REVIEW -> !previewLoading && !generationBlocked()
        }
        setOKButtonText(if (currentStep == WizardStep.CLARIFY) "Next" else "Generate draft")
        backAction.isEnabled = currentStep == WizardStep.REVIEW && !previewLoading
        clarifyRefreshButton.isEnabled = !previewLoading
        reviewRefreshButton.isEnabled = !previewLoading
    }

    private fun attachMandatoryFieldListener(component: javax.swing.text.JTextComponent) {
        component.document.addDocumentListener(object : DocumentListener {
            override fun insertUpdate(event: DocumentEvent?) = updateActionState()
            override fun removeUpdate(event: DocumentEvent?) = updateActionState()
            override fun changedUpdate(event: DocumentEvent?) = updateActionState()
        })
    }

    private fun loadPreviewAsync(advanceToReview: Boolean = false) {
        previewLoading = true
        clarifyStatusLabel.text = UiStrings.dialogLoadingPreview
        if (currentStep == WizardStep.REVIEW) {
            reviewStatusLabel.text = UiStrings.dialogLoadingPreview
        }
        updateActionState()

        ApplicationManager.getApplication().executeOnPooledThread {
            val request = GenerationPreviewRequestDto(
                projectRoot = projectRoot,
                testCaseText = testCaseText,
                language = defaultLanguage,
                qualityPolicy = DEFAULT_QUALITY_POLICY,
                selectedScenarioCandidateId = selectedScenarioCandidateId(),
                acceptedAssumptionIds = acceptedAssumptionIds(),
                clarifications = confirmedClarifications(),
            )
            runCatching { backendClient.previewGenerationPlan(request) }
                .onSuccess { preview ->
                    ApplicationManager.getApplication().invokeLater {
                        applyPreview(preview, advanceToReview)
                    }
                }
                .onFailure { ex ->
                    ApplicationManager.getApplication().invokeLater {
                        previewLoading = false
                        latestPreview = null
                        similarScenarios = emptyList()
                        scenarioCandidates = emptyList()
                        assumptionCheckboxes.clear()
                        assumptionsPanel.removeAll()
                        assumptionsPanel.revalidate()
                        assumptionsPanel.repaint()
                        clarifyStatusLabel.text = UiStrings.dialogPreviewUnavailable
                        reviewStatusLabel.text = UiStrings.dialogPreviewUnavailable
                        val fallback = ex.message?.trim().takeUnless { it.isNullOrBlank() }
                            ?: "Backend did not return a generation preview. Review draft generation is unavailable."
                        clarifyPreviewArea.text = fallback
                        reviewPreviewArea.text = fallback
                        if (currentStep == WizardStep.REVIEW) {
                            switchStep(WizardStep.CLARIFY)
                        }
                        updateActionState()
                    }
                }
        }
    }

    private fun applyPreview(preview: GenerationPreviewResponseDto, advanceToReview: Boolean) {
        previewLoading = false
        latestPreview = preview
        similarScenarios = preview.similarScenarios
        scenarioCandidates = preview.scenarioCandidates
        prefillClarificationFields(preview)

        if (targetPathField.text.trim().isEmpty()) {
            val memoryPath = preview.memoryPreview?.get("targetPath")?.toString()
            if (!memoryPath.isNullOrBlank()) {
                targetPathField.text = memoryPath
            }
        }

        populateScenarioSelector(preview)
        populateAssumptionCheckboxes(preview)

        clarifyStatusLabel.text = buildClarificationStatus(preview)
        clarifyPreviewArea.text = formatClarificationPreview(preview)
        refreshReviewPreview()

        if (advanceToReview && !preview.generationBlocked) {
            switchStep(WizardStep.REVIEW)
        } else if (preview.generationBlocked && currentStep == WizardStep.REVIEW) {
            switchStep(WizardStep.CLARIFY)
        } else {
            updateActionState()
        }
    }

    private fun prefillClarificationFields(preview: GenerationPreviewResponseDto) {
        val intent = preview.canonicalIntent ?: return
        if (actorField.text.trim().isEmpty()) {
            actorField.text = intent.actor.orEmpty()
        }
        if (goalField.text.trim().isEmpty()) {
            goalField.text = intent.goal.orEmpty()
        }
        if (expectedOutcomeArea.text.trim().isEmpty()) {
            expectedOutcomeArea.text = intent.observableOutcomes.joinToString("\n")
        }
        if (preconditionsArea.text.trim().isEmpty()) {
            preconditionsArea.text = intent.preconditions.joinToString("\n")
        }
        if (dataDimensionsArea.text.trim().isEmpty()) {
            dataDimensionsArea.text = intent.dataDimensions.joinToString("\n")
        }
    }

    private fun populateScenarioSelector(preview: GenerationPreviewResponseDto) {
        updatingScenarioSelector = true
        try {
            scenarioSelector.removeAllItems()
            preview.scenarioCandidates.forEach { item ->
                val marker = if (item.recommended) "Recommended" else "Candidate"
                scenarioSelector.addItem("$marker: ${item.title} (${String.format("%.2f", item.confidence)})")
            }
            val selectedIndex = preview.scenarioCandidates.indexOfFirst {
                it.id == preview.selectedScenarioCandidateId || it.recommended
            }.takeIf { it >= 0 } ?: 0
            if (preview.scenarioCandidates.isNotEmpty()) {
                scenarioSelector.selectedIndex = selectedIndex
                scenarioSelector.isEnabled = !preview.generationBlocked
            } else {
                scenarioSelector.isEnabled = false
            }
        } finally {
            updatingScenarioSelector = false
        }
    }

    private fun populateAssumptionCheckboxes(preview: GenerationPreviewResponseDto) {
        val previousSelection = assumptionCheckboxes.mapValues { it.value.isSelected }
        assumptionCheckboxes.clear()
        assumptionsPanel.removeAll()

        val nonBlockingAssumptionIds = preview.ambiguityIssues
            .filter { it.severity != "blocking" }
            .mapNotNull { it.assumptionId }
            .toSet()
        val assumptions = preview.canonicalIntent?.assumptions.orEmpty()
            .filter { nonBlockingAssumptionIds.isEmpty() || it.id in nonBlockingAssumptionIds }

        if (assumptions.isEmpty()) {
            assumptionsPanel.add(JLabel("No non-blocking assumptions."))
        } else {
            assumptions.forEach { assumption ->
                val selected = previousSelection[assumption.id] ?: true
                val checkbox = JBCheckBox(buildAssumptionLabel(assumption), selected)
                checkbox.isOpaque = false
                checkbox.addActionListener { refreshReviewPreview() }
                assumptionCheckboxes[assumption.id] = checkbox
                assumptionsPanel.add(checkbox)
            }
        }
        assumptionsPanel.revalidate()
        assumptionsPanel.repaint()
    }

    private fun refreshReviewPreview() {
        reviewStatusLabel.text = buildGenerationPreviewStatus(
            latestPreview,
            selectedScenarioCandidate(),
            selectedBaseScenario(),
        )
        latestPreview?.let { preview ->
            reviewPreviewArea.text = formatGenerationPreview(
                preview,
                selectedScenarioCandidate(),
                selectedBaseScenario(),
                acceptedAssumptionIds(),
            )
        } ?: run {
            reviewPreviewArea.text = "Review preview is unavailable."
        }
        updateActionState()
    }

    companion object {
        private const val DEFAULT_QUALITY_POLICY = "strict"

        private fun newEditorArea(rows: Int, editable: Boolean = true): JBTextArea =
            JBTextArea().apply {
                isEditable = editable
                lineWrap = true
                wrapStyleWord = true
                border = JBUI.Borders.empty(8)
                background = JBColor.PanelBackground
                foreground = JBColor.foreground()
                this.rows = rows
            }
    }
}

internal fun buildGenerateFeatureWizardPanel(
    targetPathField: JBTextField,
    createFileCheckbox: JBCheckBox,
    overwriteCheckbox: JBCheckBox,
    wizardHeaderLabel: JLabel,
    wizardPanel: JPanel,
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
    row {
        cell(wizardHeaderLabel)
    }
    row {
        cell(wizardPanel).resizableColumn().align(Align.FILL)
    }
}

internal fun buildGenerateFeatureFormPanel(
    targetPathField: JBTextField,
    createFileCheckbox: JBCheckBox,
    overwriteCheckbox: JBCheckBox,
    memoryStatusLabel: JLabel,
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
    group("Preview") {
        row {
            cell(memoryStatusLabel)
        }
        row {
            cell(JBScrollPane(memoryPreviewArea)).resizableColumn().align(Align.FILL)
        }
        row {
            cell(refreshPreviewButton)
        }
    }
}

internal fun buildClarifyStepPanel(
    actorField: JBTextField,
    goalField: JBTextField,
    expectedOutcomeArea: JBTextArea,
    preconditionsArea: JBTextArea,
    dataDimensionsArea: JBTextArea,
    clarifyStatusLabel: JLabel,
    clarifyPreviewArea: JBTextArea,
    refreshPreviewButton: JButton,
): JComponent = panel {
    group("Clarify intent") {
        row("Actor") {
            cell(actorField).resizableColumn().align(AlignX.FILL)
        }
        row("Goal / primary action") {
            cell(goalField).resizableColumn().align(AlignX.FILL)
        }
        row("Expected outcome") {
            cell(JBScrollPane(expectedOutcomeArea)).resizableColumn().align(Align.FILL)
        }
        row("Preconditions") {
            cell(JBScrollPane(preconditionsArea)).resizableColumn().align(Align.FILL)
        }
        row("Test data / business constraints") {
            cell(JBScrollPane(dataDimensionsArea)).resizableColumn().align(Align.FILL)
        }
    }
    group("Analysis") {
        row {
            cell(clarifyStatusLabel)
        }
        row {
            cell(JBScrollPane(clarifyPreviewArea)).resizableColumn().align(Align.FILL)
        }
        row {
            cell(refreshPreviewButton)
        }
    }
}

internal fun buildReviewStepPanel(
    reviewStatusLabel: JLabel,
    scenarioSelector: JComboBox<String>,
    assumptionsPanel: JPanel,
    reviewPreviewArea: JBTextArea,
    refreshPreviewButton: JButton,
): JComponent = panel {
    group("Review candidates") {
        row {
            cell(reviewStatusLabel)
        }
        row("Scenario candidate") {
            cell(scenarioSelector).resizableColumn().align(AlignX.FILL)
        }
        row("Assumptions") {
            cell(JBScrollPane(assumptionsPanel)).resizableColumn().align(Align.FILL)
        }
        row {
            cell(JBScrollPane(reviewPreviewArea)).resizableColumn().align(Align.FILL)
        }
        row {
            cell(refreshPreviewButton)
        }
    }
}

internal fun isClarificationComplete(actor: String, goal: String, expectedOutcome: String): Boolean =
    actor.trim().isNotEmpty() && goal.trim().isNotEmpty() && expectedOutcome.trim().isNotEmpty()

internal fun buildClarificationPayload(
    actor: String,
    goal: String,
    expectedOutcome: String,
    preconditions: String,
    dataDimensions: String,
): Map<String, String> = buildMap {
    actor.trim().takeIf { it.isNotEmpty() }?.let { put("actor", it) }
    goal.trim().takeIf { it.isNotEmpty() }?.let { put("goal", it) }
    expectedOutcome.trim().takeIf { it.isNotEmpty() }?.let { put("observableOutcomes", it) }
    preconditions.trim().takeIf { it.isNotEmpty() }?.let { put("preconditions", it) }
    dataDimensions.trim().takeIf { it.isNotEmpty() }?.let { put("dataDimensions", it) }
}

internal fun collectAcceptedAssumptionIds(
    latestPreview: GenerationPreviewResponseDto?,
    selectedCandidate: ScenarioCandidateDto?,
    assumptionCheckboxes: Map<String, JBCheckBox>,
): List<String> {
    if (assumptionCheckboxes.isNotEmpty()) {
        return assumptionCheckboxes.entries
            .filter { it.value.isSelected }
            .map { it.key }
    }
    return latestPreview?.canonicalIntent?.assumptions
        ?.filter { it.accepted || it.id in (selectedCandidate?.assumptionIds ?: emptyList()) }
        ?.map { it.id }
        ?: emptyList()
}

internal fun buildClarificationStatus(preview: GenerationPreviewResponseDto?): String {
    if (preview == null) {
        return "Clarification preview is unavailable."
    }
    val blockingIssues = preview.ambiguityIssues.count { it.severity == "blocking" }
    val mode = if (preview.generationBlocked) "Clarification required" else "Clarification resolved"
    return "$mode; blocking issues: $blockingIssues"
}

internal fun formatClarificationPreview(preview: GenerationPreviewResponseDto): String {
    val lines = mutableListOf<String>()
    preview.canonicalIntent?.let { intent ->
        lines += "Intent"
        lines += "Actor: ${intent.actor ?: "-"}"
        lines += "Goal: ${intent.goal ?: "-"}"
        lines += "Observable outcomes: ${intent.observableOutcomes.joinToString(", ").ifBlank { "-" }}"
        if (intent.preconditions.isNotEmpty()) {
            lines += "Preconditions: ${intent.preconditions.joinToString(" | ")}"
        }
        if (intent.dataDimensions.isNotEmpty()) {
            lines += "Data dimensions: ${intent.dataDimensions.joinToString(" | ")}"
        }
        lines += ""
    }

    lines += "Questions / assumptions"
    if (preview.ambiguityIssues.isEmpty()) {
        lines += "No ambiguity issues detected."
    } else {
        preview.ambiguityIssues.forEach { issue ->
            lines += "- [${issue.severity}] ${issue.message}"
            issue.question?.takeIf { it.isNotBlank() }?.let { lines += "  question: $it" }
        }
    }

    if (preview.generationBlocked) {
        lines += ""
        lines += "Draft generation is blocked until all blocking issues are resolved."
    }
    return lines.joinToString("\n")
}

internal fun buildGenerationPreviewStatus(
    preview: GenerationPreviewResponseDto?,
    selectedCandidate: ScenarioCandidateDto?,
    selectedBaseScenario: SimilarScenarioDto?
): String {
    if (preview == null) {
        return "Preview is unavailable."
    }
    val candidatePart = selectedCandidate?.title ?: "no scenario candidate"
    val basePart = selectedBaseScenario?.name ?: "no base scenario"
    val warnings = preview.warnings.size
    val blocked = if (preview.generationBlocked) "; blocked" else ""
    return "Plan ${preview.planId ?: "-"}; candidate: $candidatePart; base: $basePart; warnings: $warnings$blocked"
}

internal fun formatGenerationPreview(
    preview: GenerationPreviewResponseDto,
    selectedCandidate: ScenarioCandidateDto?,
    selectedBaseScenario: SimilarScenarioDto?,
    acceptedAssumptionIds: List<String> = emptyList(),
): String {
    val lines = mutableListOf<String>()
    preview.canonicalTestCase?.let { canonical ->
        lines += "Canonical testcase"
        lines += "Title: ${canonical.title}"
        lines += "Preconditions: ${canonical.preconditions.size}"
        lines += "Actions: ${canonical.actions.size}"
        lines += "Expected results: ${canonical.expectedResults.size}"
        if (canonical.testData.isNotEmpty()) {
            lines += "Test data: ${canonical.testData.joinToString(", ")}"
        }
        lines += ""
    }

    preview.canonicalIntent?.let { intent ->
        lines += "Intent"
        lines += "Actor: ${intent.actor ?: "-"}"
        lines += "Goal: ${intent.goal ?: "-"}"
        lines += "SUT area: ${intent.sutArea ?: "-"}"
        lines += "Observable outcomes: ${intent.observableOutcomes.joinToString(", ").ifBlank { "-" }}"
        lines += "Confidence: ${String.format("%.2f", intent.confidence)}"
        lines += ""
    }

    lines += "Questions / assumptions"
    if (preview.ambiguityIssues.isEmpty()) {
        lines += "No ambiguity issues detected."
    } else {
        preview.ambiguityIssues.forEach { issue ->
            lines += "- [${issue.severity}] ${issue.message}"
            issue.question?.takeIf { it.isNotBlank() }?.let { lines += "  question: $it" }
        }
    }
    val acceptedAssumptions = preview.canonicalIntent?.assumptions.orEmpty()
        .filter { it.id in acceptedAssumptionIds }
    if (acceptedAssumptions.isNotEmpty()) {
        lines += "Accepted assumptions: ${acceptedAssumptions.joinToString { it.text }}"
    }

    lines += ""
    lines += "Scenario candidates"
    if (preview.scenarioCandidates.isEmpty()) {
        lines += "No scenario candidates were derived."
    } else {
        preview.scenarioCandidates.forEach { item ->
            val marker = if (item.id == selectedCandidate?.id) {
                "selected"
            } else if (item.recommended) {
                "recommended"
            } else {
                "candidate"
            }
            lines += "- [$marker] ${item.title} (${item.type}, confidence=${String.format("%.2f", item.confidence)})"
            lines += "  rationale: ${item.rationale}"
        }
    }

    lines += ""
    lines += "Similar scenarios"
    if (preview.similarScenarios.isEmpty()) {
        lines += "No local .feature scenarios matched."
    } else {
        preview.similarScenarios.forEachIndexed { index, item ->
            val marker = if (item.recommended) "recommended" else "candidate"
            lines += "${index + 1}. [$marker] ${item.name} (${String.format("%.2f", item.score)})"
            if (item.matchedFragments.isNotEmpty()) {
                lines += "   matched: ${item.matchedFragments.joinToString(" | ")}"
            }
        }
    }

    lines += ""
    lines += "Generation plan"
    lines += "Selected candidate: ${selectedCandidate?.title ?: preview.selectedScenarioCandidateId ?: "-"}"
    lines += "Selected base scenario: ${selectedBaseScenario?.name ?: preview.generationPlan.selectedScenarioId ?: "-"}"
    lines += "Candidate background steps: ${preview.generationPlan.candidateBackground.size}"
    lines += "Planned steps: ${preview.generationPlan.items.size}"
    if (preview.generationPlan.items.isNotEmpty()) {
        preview.generationPlan.items.forEach { item ->
            val selected = item.selectedStepId ?: "unmatched"
            val confidence = item.selectedConfidence?.let { String.format("%.2f", it) } ?: "-"
            lines += "${item.order}. [${item.intentType}] ${item.text}"
            lines += "   selected: $selected (confidence: $confidence)"
        }
    }

    preview.evidenceSummary?.let { evidence ->
        lines += ""
        lines += "Evidence"
        if (evidence.scenarios.isEmpty() && evidence.steps.isEmpty() && evidence.reviewSignals.isEmpty()) {
            lines += "No evidence was found."
        } else {
            evidence.scenarios.forEach { lines += "- scenario: ${it.title} (${String.format("%.2f", it.score)})" }
            evidence.steps.forEach { lines += "- step: ${it.title} (${String.format("%.2f", it.score)})" }
            evidence.reviewSignals.forEach { lines += "- learning: ${it.title} (${String.format("%.2f", it.score)})" }
        }
    }

    if (preview.warnings.isNotEmpty()) {
        lines += ""
        lines += "Warnings"
        preview.warnings.forEach { warning ->
            lines += "- $warning"
        }
    }

    if (preview.quality != null) {
        lines += ""
        lines += "Quality gate"
        lines += "Score: ${preview.quality.score}"
        lines += "Passed: ${preview.quality.passed}"
        preview.coverageReport?.let {
            lines += "Coverage: oracle=${String.format("%.2f", it.oracleCoverage)}, then=${String.format("%.2f", it.thenCoverage)}, traceability=${String.format("%.2f", it.traceabilityScore)}"
            if (it.flakeRiskFlags.isNotEmpty()) {
                lines += "Risks: ${it.flakeRiskFlags.joinToString(", ")}"
            }
        }
        if (preview.quality.failures.isNotEmpty()) {
            lines += "Failures: ${preview.quality.failures.joinToString { it.code }}"
        }
        if (preview.quality.warnings.isNotEmpty()) {
            lines += "Quality warnings: ${preview.quality.warnings.joinToString { it.code }}"
        }
    }

    return lines.joinToString("\n")
}

private fun buildAssumptionLabel(assumption: AssumptionDto): String {
    val question = assumption.question?.takeIf { it.isNotBlank() }
    return if (question != null) {
        "${assumption.text} ($question)"
    } else {
        assumption.text
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
        return "Memory rules did not match this testcase."
    }
    return "Memory rules will be applied automatically."
}

internal fun formatMemoryPreview(preview: GenerationResolvePreviewResponseDto): String {
    val lines = mutableListOf<String>()
    lines += "Matched rules: ${preview.appliedRuleIds.size}"
    lines += "Matched templates: ${preview.appliedTemplateIds.size}"
    lines += "Injected steps: ${preview.templateSteps.size}"
    preview.qualityPolicy?.takeIf { it.isNotBlank() }?.let { lines += "Resolved quality policy: $it" }
    preview.language?.takeIf { it.isNotBlank() }?.let { lines += "Resolved language: $it" }
    preview.targetPath?.takeIf { it.isNotBlank() }?.let { lines += "Recommended path: $it" }
    if (preview.templateSteps.isNotEmpty()) {
        lines += ""
        lines += "Injected steps:"
        preview.templateSteps.forEachIndexed { index, step ->
            lines += "${index + 1}. $step"
        }
    } else {
        lines += ""
        lines += "No template steps will be injected."
    }
    return lines.joinToString("\n")
}

package ru.sber.aitestplugin.ui.dialogs

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import com.intellij.ui.components.JBCheckBox
import ru.sber.aitestplugin.actions.buildReviewLearningRequest
import ru.sber.aitestplugin.actions.buildRunInputPayload
import ru.sber.aitestplugin.model.AssumptionDto
import ru.sber.aitestplugin.model.CanonicalIntentDto
import ru.sber.aitestplugin.model.FeatureReviewMetadata
import ru.sber.aitestplugin.model.GenerationPlanDto
import ru.sber.aitestplugin.model.GenerationPreviewResponseDto
import ru.sber.aitestplugin.model.ScenarioCandidateDto

class GenerateFeatureWizardTest {

    @Test
    fun `clarification step requires actor goal and expected outcome`() {
        assertFalse(isClarificationComplete("", "open dashboard", "dashboard is displayed"))
        assertFalse(isClarificationComplete("user", "", "dashboard is displayed"))
        assertFalse(isClarificationComplete("user", "open dashboard", ""))
        assertTrue(isClarificationComplete("user", "open dashboard", "dashboard is displayed"))
    }

    @Test
    fun `builds clarification payload from wizard fields`() {
        val payload = buildClarificationPayload(
            actor = "user",
            goal = "open dashboard",
            expectedOutcome = "dashboard is displayed",
            preconditions = "user is logged in",
            dataDimensions = "",
        )

        assertEquals("user", payload["actor"])
        assertEquals("open dashboard", payload["goal"])
        assertEquals("dashboard is displayed", payload["observableOutcomes"])
        assertEquals("user is logged in", payload["preconditions"])
        assertFalse(payload.containsKey("dataDimensions"))
    }

    @Test
    fun `collects accepted assumptions from selected checkboxes`() {
        val checkboxA = JBCheckBox("A", true)
        val checkboxB = JBCheckBox("B", false)

        val accepted = collectAcceptedAssumptionIds(
            latestPreview = null,
            selectedCandidate = null,
            assumptionCheckboxes = linkedMapOf(
                "assumption-a" to checkboxA,
                "assumption-b" to checkboxB,
            ),
        )

        assertEquals(listOf("assumption-a"), accepted)
    }

    @Test
    fun `run payload includes selected candidate assumptions and clarifications`() {
        val payload = buildRunInputPayload(
            selectedText = "Open dashboard",
            dialogOptions = GenerateFeatureDialogOptions(
                targetPath = "generated/dashboard.feature",
                createFile = false,
                overwriteExisting = false,
                language = "en",
            ),
            planId = "plan-1",
            selectedScenarioId = "scenario-1",
            selectedScenarioCandidateId = "candidate-1",
            acceptedAssumptionIds = listOf("assumption-a"),
            confirmedClarifications = mapOf("actor" to "user"),
        )

        assertEquals("candidate-1", payload["selectedScenarioCandidateId"])
        assertEquals(listOf("assumption-a"), payload["acceptedAssumptionIds"])
        assertEquals(mapOf("actor" to "user"), payload["clarifications"])
    }

    @Test
    fun `review learning request preserves confirmed clarifications`() {
        val request = buildReviewLearningRequest(
            reviewMetadata = FeatureReviewMetadata(
                projectRoot = "C:/repo",
                targetPath = "generated/dashboard.feature",
                overwriteExisting = true,
                planId = "plan-1",
                selectedScenarioId = "scenario-1",
                selectedScenarioCandidateId = "candidate-1",
                acceptedAssumptionIds = listOf("assumption-a"),
                confirmedClarifications = mapOf("goal" to "open dashboard"),
                originalFeatureText = "Feature: Dashboard",
            ),
            targetPath = "generated/dashboard.feature",
            featureText = "Feature: Dashboard",
            overwriteExisting = true,
        )

        assertEquals("candidate-1", request.selectedScenarioCandidateId)
        assertEquals(mapOf("goal" to "open dashboard"), request.confirmedClarifications)
    }

    @Test
    fun `falls back to preview assumptions when no checkbox model exists`() {
        val preview = GenerationPreviewResponseDto(
            generationPlan = GenerationPlanDto(),
            canonicalIntent = CanonicalIntentDto(
                assumptions = listOf(
                    AssumptionDto(id = "assumption-a", text = "Use default filter", accepted = true),
                    AssumptionDto(id = "assumption-b", text = "Use admin role", accepted = false),
                ),
            ),
        )
        val candidate = ScenarioCandidateDto(
            id = "candidate-1",
            type = "happy_path",
            rank = 1,
            title = "Open dashboard",
            rationale = "Primary flow",
            assumptionIds = listOf("assumption-b"),
        )

        val accepted = collectAcceptedAssumptionIds(
            latestPreview = preview,
            selectedCandidate = candidate,
            assumptionCheckboxes = emptyMap(),
        )

        assertEquals(listOf("assumption-a", "assumption-b"), accepted)
    }
}

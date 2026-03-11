from __future__ import annotations

from agents.feature_builder_agent import FeatureBuilderAgent
from agents.orchestrator import Orchestrator
from agents.repo_scanner_agent import RepoScannerAgent
from agents.step_matcher_agent import StepMatcherAgent
from agents.testcase_parser_agent import TestcaseParserAgent
from infrastructure.embeddings_store import EmbeddingsStore
from infrastructure.preview_plan_store import PreviewPlanStore
from infrastructure.scenario_index_store import ScenarioIndexStore
from infrastructure.step_index_store import StepIndexStore
from memory import MemoryRepository, MemoryService


def _steps_source() -> str:
    return """
        package steps

        class LoginSteps {
            @Given("user is logged in")
            fun login() {}

            @When("user opens dashboard")
            fun openDashboard() {}

            @Then("dashboard is displayed")
            fun assertDashboard() {}
        }
    """.strip()


def _feature_source() -> str:
    return """
        Feature: Dashboard access

          Background:
            Given user is logged in

          Scenario: Open dashboard
            When user opens dashboard
            Then dashboard is displayed
    """.strip()


def _build_orchestrator(tmp_path, project_root):
    index_root = tmp_path / "index"
    step_store = StepIndexStore(index_root / "steps")
    scenario_store = ScenarioIndexStore(index_root / "scenarios")
    preview_store = PreviewPlanStore(index_root / "preview")
    embeddings_store = EmbeddingsStore(index_root / "chroma")
    memory_service = MemoryService(MemoryRepository(index_root / "memory"))

    orchestrator = Orchestrator(
        repo_scanner_agent=RepoScannerAgent(
            step_store,
            embeddings_store,
            scenario_index_store=scenario_store,
            file_patterns=["**/*.kt"],
        ),
        testcase_parser_agent=TestcaseParserAgent(),
        step_matcher_agent=StepMatcherAgent(
            step_store,
            embeddings_store,
            project_learning_store=memory_service,
        ),
        feature_builder_agent=FeatureBuilderAgent(),
        step_index_store=step_store,
        embeddings_store=embeddings_store,
        scenario_index_store=scenario_store,
        preview_plan_store=preview_store,
        project_learning_store=memory_service,
    )
    orchestrator.scan_steps(str(project_root))
    return orchestrator, embeddings_store, memory_service


def test_preview_generation_plan_returns_similar_scenarios_and_background(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "LoginSteps.kt").write_text(_steps_source(), encoding="utf-8")
    (project_root / "dashboard.feature").write_text(_feature_source(), encoding="utf-8")

    orchestrator, embeddings_store, _memory_service = _build_orchestrator(tmp_path, project_root)
    try:
        preview = orchestrator.preview_generation_plan(
            project_root=str(project_root),
            testcase_text="""
                Preconditions:
                1. user is logged in
                Steps:
                2. user opens dashboard
                Expected result:
                dashboard is displayed
            """.strip(),
            language="en",
            quality_policy="strict",
        )
    finally:
        embeddings_store.close()

    assert preview["planId"]
    assert preview["similarScenarios"]
    assert preview["similarScenarios"][0]["recommended"] is True
    assert preview["canonicalIntent"]["goal"]
    assert preview["scenarioCandidates"]
    assert preview["selectedScenarioCandidateId"] == preview["scenarioCandidates"][0]["id"]
    assert preview["coverageReport"]["traceabilityScore"] > 0.0
    assert preview["generationPlan"]["candidateBackground"] == ["Given user is logged in"]
    assert len(preview["generationPlan"]["items"]) >= 2
    assert preview["quality"]["metrics"]["expectedResultCoverage"] >= 1.0


def test_preview_generation_plan_blocks_until_clarifications_are_provided(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "LoginSteps.kt").write_text(_steps_source(), encoding="utf-8")
    (project_root / "dashboard.feature").write_text(_feature_source(), encoding="utf-8")

    orchestrator, embeddings_store, _memory_service = _build_orchestrator(tmp_path, project_root)
    try:
        preview = orchestrator.preview_generation_plan(
            project_root=str(project_root),
            testcase_text="Open dashboard",
            language="en",
            quality_policy="strict",
        )
    finally:
        embeddings_store.close()

    assert preview["generationBlocked"] is True
    assert preview["draftFeatureText"] == ""
    assert preview["generationPlan"]["items"] == []
    assert preview["quality"] is None
    assert preview["coverageReport"] is None
    assert preview["scenarioCandidates"] == []
    assert preview["evidenceSummary"] is None
    assert any(item["severity"] == "blocking" for item in preview["ambiguityIssues"])


def test_preview_generation_plan_returns_full_preview_after_clarifications(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "LoginSteps.kt").write_text(_steps_source(), encoding="utf-8")
    (project_root / "dashboard.feature").write_text(_feature_source(), encoding="utf-8")

    orchestrator, embeddings_store, _memory_service = _build_orchestrator(tmp_path, project_root)
    try:
        preview = orchestrator.preview_generation_plan(
            project_root=str(project_root),
            testcase_text="user opens dashboard",
            language="en",
            quality_policy="strict",
            clarifications={
                "actor": "user",
                "goal": "open dashboard",
                "observableOutcomes": "dashboard is displayed",
            },
        )
    finally:
        embeddings_store.close()

    assert preview["generationBlocked"] is False
    assert preview["scenarioCandidates"]
    assert preview["generationPlan"]["items"]
    assert preview["draftFeatureText"]
    assert preview["quality"] is not None


def test_review_and_apply_feature_persists_learning_memory(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "LoginSteps.kt").write_text(_steps_source(), encoding="utf-8")
    (project_root / "dashboard.feature").write_text(_feature_source(), encoding="utf-8")

    orchestrator, embeddings_store, memory_service = _build_orchestrator(tmp_path, project_root)
    try:
        preview = orchestrator.preview_generation_plan(
            project_root=str(project_root),
            testcase_text="1. user opens dashboard\n2. dashboard is displayed",
            language="en",
            quality_policy="strict",
        )
        draft = preview["draftFeatureText"]
        edited = draft.replace("Then dashboard is displayed", "Then dashboard is displayed")
        result = orchestrator.review_and_apply_feature(
            project_root=str(project_root),
            plan_id=preview["planId"],
            target_path="generated/dashboard.feature",
            original_feature_text=draft,
            edited_feature_text=edited,
            overwrite_existing=True,
            selected_scenario_id=preview["generationPlan"]["selectedScenarioId"],
            selected_scenario_candidate_id=preview["selectedScenarioCandidateId"],
            accepted_assumption_ids=["assumption-1"],
            confirmed_clarifications={"actor": "user", "goal": "open dashboard"},
            accepted_step_ids=[
                item["selectedStepId"]
                for item in preview["generationPlan"]["items"]
                if item.get("selectedStepId")
            ],
        )
    finally:
        embeddings_store.close()

    assert result["fileStatus"]["status"] == "created"
    saved_payload = memory_service.load_project_memory(str(project_root))
    assert saved_payload["reviewHistory"]
    assert saved_payload["reviewHistory"][-1]["confirmedClarifications"]["actor"] == "user"
    assert saved_payload["reviewHistory"][-1]["acceptedAssumptionIds"] == ["assumption-1"]
    assert saved_payload["reviewHistory"][-1]["selectedScenarioCandidateId"] == preview["selectedScenarioCandidateId"]
    assert saved_payload["reviewHistory"][-1]["finalThenLines"] == ["Then dashboard is displayed"]
    assert saved_payload["scenarioPreferences"]
    assert (project_root / "generated" / "dashboard.feature").exists()


def test_generate_feature_uses_selected_scenario_candidate(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "LoginSteps.kt").write_text(_steps_source(), encoding="utf-8")
    (project_root / "dashboard.feature").write_text(_feature_source(), encoding="utf-8")

    orchestrator, embeddings_store, _memory_service = _build_orchestrator(tmp_path, project_root)
    try:
        preview = orchestrator.preview_generation_plan(
            project_root=str(project_root),
            testcase_text="""
                Preconditions:
                1. user is logged in
                Steps:
                2. user opens dashboard with invalid filter
                Expected result:
                validation error is displayed
            """.strip(),
            language="en",
            quality_policy="strict",
        )
        chosen_candidate = next(
            item for item in preview["scenarioCandidates"]
            if item["type"] == "negative"
        )
        generated = orchestrator.generate_feature(
            project_root=str(project_root),
            testcase_text="",
            plan_id=preview["planId"],
            selected_scenario_candidate_id=chosen_candidate["id"],
            quality_policy="strict",
        )
    finally:
        embeddings_store.close()

    feature_meta = generated["feature"]["meta"]
    assert feature_meta["selectedScenarioCandidateId"] == chosen_candidate["id"]
    assert generated["feature"]["featureText"].startswith("# language: en\nFeature: Negative path:")


def test_generate_feature_returns_blocked_result_without_required_clarifications(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "LoginSteps.kt").write_text(_steps_source(), encoding="utf-8")
    (project_root / "dashboard.feature").write_text(_feature_source(), encoding="utf-8")

    orchestrator, embeddings_store, _memory_service = _build_orchestrator(tmp_path, project_root)
    try:
        generated = orchestrator.generate_feature(
            project_root=str(project_root),
            testcase_text="Open dashboard",
            quality_policy="strict",
        )
    finally:
        embeddings_store.close()

    assert generated["feature"]["featureText"] == ""
    assert generated["feature"]["meta"]["generationBlocked"] is True
    assert generated["pipeline"][0]["stage"] == "clarification_gate"


def test_repair_pass_adds_missing_then_coverage_once(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "LoginSteps.kt").write_text(_steps_source(), encoding="utf-8")
    (project_root / "dashboard.feature").write_text(_feature_source(), encoding="utf-8")

    orchestrator, embeddings_store, _memory_service = _build_orchestrator(tmp_path, project_root)
    try:
        state = {
            "project_root": str(project_root),
            "testcase_text": "Open dashboard",
            "target_path": None,
            "create_file": False,
            "overwrite_existing": False,
            "language": "en",
            "quality_policy": "strict",
            "selected_scenario_id": None,
            "selected_scenario_candidate_id": "candidate-1-happy_path",
            "selected_scenario_candidate": {
                "id": "candidate-1-happy_path",
                "type": "happy_path",
                "expectedOutcomes": ["dashboard is displayed"],
            },
            "parsed_scenario": {
                "name": "Open dashboard",
                "steps": [
                    {"order": 1, "text": "user opens dashboard", "section": "action"},
                    {"order": 2, "text": "dashboard is displayed", "section": "expected_result"},
                ],
            },
            "scenario": {
                "name": "Open dashboard",
                "steps": [
                    {"order": 1, "text": "user opens dashboard", "section": "action"},
                ],
            },
            "canonical_intent": {
                "actor": "user",
                "goal": "open dashboard",
                "observableOutcomes": ["dashboard is displayed"],
                "preconditions": [],
                "dataDimensions": [],
                "assumptions": [],
            },
            "ambiguity_issues": [],
            "quality_report": {
                "passed": False,
                "failures": [
                    {"code": "oracle_coverage_missing"},
                    {"code": "then_coverage_missing"},
                ],
                "coverageReport": {
                    "oracleCoverage": 0.0,
                    "thenCoverage": 0.0,
                    "newStepsNeededCount": 0,
                },
            },
        }
        repaired = orchestrator._maybe_run_repair_pass(  # noqa: SLF001
            {
                "project_root": str(project_root),
                "create_file": False,
                "overwrite_existing": False,
                "target_path": None,
            },
            state,
        )
    finally:
        embeddings_store.close()

    assert repaired["feature"]["featureText"]
    assert repaired["quality_report"]["coverageReport"]["oracleCoverage"] > 0.0
    assert repaired["quality_report"]["coverageReport"]["thenCoverage"] > 0.0
    assert sum(1 for item in repaired["pipeline"] if item.get("stage") == "repair_pass") == 1

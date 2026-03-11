from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes_generate import router as generate_router


class _OrchestratorStub:
    def preview_generation_plan(self, **kwargs):  # noqa: ANN003
        _ = kwargs
        return {
            "planId": "plan-1",
            "canonicalTestCase": {
                "title": "Sample",
                "preconditions": [],
                "actions": [{"order": 1, "text": "open dashboard", "intentType": "action", "source": "heuristic", "origin": "actions", "confidence": 1.0, "normalizedFrom": "open dashboard", "metadata": {}}],
                "expectedResults": [],
                "testData": [],
                "tags": [],
                "scenarioType": "standard",
                "source": "heuristic",
            },
            "canonicalIntent": {
                "goal": "open dashboard",
                "actor": "user",
                "sutArea": "dashboard",
                "preconditions": ["user is logged in"],
                "businessRules": [],
                "dataDimensions": [],
                "observableOutcomes": ["dashboard is displayed"],
                "unknowns": [],
                "assumptions": [],
                "confidence": 0.92,
                "evidenceRefs": [],
            },
            "ambiguityIssues": [],
            "scenarioCandidates": [
                {
                    "id": "candidate-1-happy_path",
                    "type": "happy_path",
                    "rank": 1,
                    "title": "Open dashboard",
                    "rationale": "Primary flow around goal: open dashboard.",
                    "recommended": True,
                    "confidence": 0.9,
                    "expectedOutcomes": ["dashboard is displayed"],
                    "assumptionIds": [],
                    "evidenceRefs": [],
                    "steps": ["user opens dashboard"],
                    "backgroundSteps": ["user is logged in"],
                }
            ],
            "similarScenarios": [
                {
                    "scenarioId": "sc-1",
                    "name": "Feature: Open dashboard",
                    "featurePath": "dashboard.feature",
                    "score": 0.91,
                    "matchedFragments": ["When user opens dashboard"],
                    "backgroundSteps": [],
                    "steps": ["When user opens dashboard"],
                    "recommended": True,
                }
            ],
            "evidenceSummary": {
                "scenarios": [
                    {
                        "id": "sc-1",
                        "source": "scenario_index",
                        "title": "Feature: Open dashboard",
                        "score": 0.91,
                        "details": {"featurePath": "dashboard.feature"},
                    }
                ],
                "steps": [],
                "reviewSignals": [],
            },
            "coverageReport": {
                "oracleCoverage": 1.0,
                "preconditionCoverage": 1.0,
                "dataCoverage": 1.0,
                "thenCoverage": 1.0,
                "assumptionCount": 0,
                "newStepsNeededCount": 0,
                "traceabilityScore": 0.96,
                "flakeRiskFlags": [],
                "blockingIssueCount": 0,
            },
            "selectedScenarioCandidateId": "candidate-1-happy_path",
            "generationBlocked": False,
            "generationPlan": {
                "planId": "plan-1",
                "source": "intent_aware",
                "recommendedScenarioId": "sc-1",
                "selectedScenarioId": "sc-1",
                "candidateBackground": [],
                "items": [
                    {
                        "order": 1,
                        "text": "open dashboard",
                        "intentType": "action",
                        "section": "step",
                        "keyword": "When",
                        "bindingCandidates": [],
                        "selectedStepId": None,
                        "selectedConfidence": 0.0,
                        "warning": None,
                    }
                ],
                "warnings": [],
                "confidence": 0.8,
                "draftFeatureText": "Feature: Sample\n",
            },
            "draftFeatureText": "Feature: Sample\n",
            "quality": None,
            "warnings": [],
            "memoryPreview": {"targetPath": "generated/sample.feature"},
        }

    def review_and_apply_feature(self, **kwargs):  # noqa: ANN003
        _ = kwargs
        return {
            "planId": "plan-1",
            "fileStatus": {
                "projectRoot": "/tmp/project",
                "targetPath": "generated/sample.feature",
                "status": "created",
                "message": None,
            },
            "quality": {
                "policy": "strict",
                "passed": True,
                "score": 90,
                "failures": [],
                "warnings": [],
                "criticIssues": [],
                "metrics": {
                    "syntaxValid": True,
                    "unmatchedStepsCount": 0,
                    "unmatchedRatio": 0.0,
                    "exactRatio": 1.0,
                    "fuzzyRatio": 0.0,
                    "parameterFillFullRatio": 1.0,
                    "ambiguousCount": 0,
                    "llmRerankedCount": 0,
                    "normalizationSplitCount": 0,
                    "expectedResultCount": 0,
                    "expectedResultCoverage": 1.0,
                    "assertionCount": 0,
                    "missingAssertionCount": 0,
                    "weakMatchCount": 0,
                    "logicalCompleteness": True,
                    "qualityScore": 90,
                },
            },
            "learning": {
                "rewriteRulesSaved": 1,
                "aliasCandidatesSaved": 1,
                "selectedScenarioId": "sc-1",
                "selectedScenarioCandidateId": "candidate-1-happy_path",
                "memoryUpdatedAt": None,
            },
        }

    def generate_feature(self, *args, **kwargs):  # noqa: ANN002, ANN003
        _ = (args, kwargs)
        return {
            "feature": {
                "featureText": "",
                "unmappedSteps": [],
                "meta": {
                    "generationBlocked": True,
                    "blockingReason": "Actor and expected outcome must be clarified",
                    "canonicalIntent": {
                        "goal": "open dashboard",
                        "actor": None,
                        "observableOutcomes": [],
                    },
                    "ambiguityIssues": [
                        {
                            "id": "issue-actor",
                            "severity": "blocking",
                            "category": "actor",
                            "field": "actor",
                            "message": "Actor is required",
                        }
                    ],
                },
            },
            "matchResult": {"matched": [], "unmatched": []},
            "pipeline": [{"stage": "clarification_gate", "status": "blocked", "details": {}}],
            "fileStatus": None,
        }


def _build_app() -> FastAPI:
    app = FastAPI()
    app.state.orchestrator = _OrchestratorStub()
    app.include_router(generate_router)
    return app


def test_preview_generation_endpoint_returns_plan_payload() -> None:
    client = TestClient(_build_app())

    response = client.post(
        "/platform/feature/preview-generation",
        json={
            "projectRoot": "/tmp/project",
            "testCaseText": "1. open dashboard",
            "qualityPolicy": "strict",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["planId"] == "plan-1"
    assert payload["generationPlan"]["selectedScenarioId"] == "sc-1"
    assert payload["selectedScenarioCandidateId"] == "candidate-1-happy_path"
    assert payload["canonicalIntent"]["actor"] == "user"
    assert payload["coverageReport"]["traceabilityScore"] == 0.96
    assert payload["similarScenarios"][0]["recommended"] is True


def test_review_apply_endpoint_returns_learning_payload() -> None:
    client = TestClient(_build_app())

    response = client.post(
        "/platform/feature/review-apply",
        json={
            "projectRoot": "/tmp/project",
            "planId": "plan-1",
            "targetPath": "generated/sample.feature",
            "originalFeatureText": "Feature: Sample\n",
            "editedFeatureText": "Feature: Sample\n",
            "overwriteExisting": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["fileStatus"]["status"] == "created"
    assert payload["learning"]["rewriteRulesSaved"] == 1
    assert payload["learning"]["selectedScenarioCandidateId"] == "candidate-1-happy_path"


def test_generate_endpoint_returns_422_when_generation_is_blocked(tmp_path) -> None:
    client = TestClient(_build_app())
    project_root = tmp_path / "project"
    project_root.mkdir()

    response = client.post(
        "/platform/feature/generate-feature",
        json={
            "projectRoot": str(project_root),
            "testCaseText": "Open dashboard",
            "qualityPolicy": "strict",
        },
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["detail"]["message"] == "Actor and expected outcome must be clarified"
    assert payload["detail"]["ambiguityIssues"][0]["field"] == "actor"

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.orchestrator import Orchestrator


class _RepoScannerStub:
    def scan_repository(self, _project_root: str) -> dict[str, Any]:
        return {}


class _ParserStub:
    def parse_testcase(self, _testcase_text: str) -> dict[str, Any]:
        return {
            "name": "scenario",
            "steps": [{"order": 1, "text": "unknown step"}],
            "normalization": {"splitCount": 0},
        }


class _StepMatcherStub:
    def match_testcase_steps(self, _project_root: str, _scenario: dict[str, Any]) -> dict[str, Any]:
        return {
            "matched": [],
            "unmatched": ["unknown step"],
            "needsScan": False,
            "indexStatus": "ready",
            "ambiguousCount": 0,
            "llmRerankedCount": 0,
        }


class _FeatureBuilderStub:
    def build_feature_from_matches(
        self,
        _scenario_dict: dict[str, Any],
        _matched_steps_dicts: list[dict[str, Any]],
        language: str | None = None,  # noqa: ARG002
    ) -> dict[str, Any]:
        return {
            "featureText": "Feature: generated\n  Scenario: sample\n    When <unmatched: unknown step>\n",
            "unmappedSteps": ["unknown step"],
            "stepsSummary": {"exact": 0, "fuzzy": 0, "unmatched": 1},
            "parameterFillSummary": {"full": 0, "partial": 0, "fallback": 0, "none": 1},
            "meta": {"language": "ru"},
        }


class _StepIndexStub:
    pass


class _EmbeddingsStub:
    def get_top_k(self, _project_root: str, _query: str, *, top_k: int = 5):  # noqa: ARG002
        return []


def _make_orchestrator() -> Orchestrator:
    return Orchestrator(
        repo_scanner_agent=_RepoScannerStub(),
        testcase_parser_agent=_ParserStub(),
        step_matcher_agent=_StepMatcherStub(),
        feature_builder_agent=_FeatureBuilderStub(),
        step_index_store=_StepIndexStub(),
        embeddings_store=_EmbeddingsStub(),
    )


def test_generate_feature_skips_file_write_when_quality_gate_fails(tmp_path: Path) -> None:
    orchestrator = _make_orchestrator()
    project_root = tmp_path / "project"
    project_root.mkdir()

    result = orchestrator.generate_feature(
        project_root=str(project_root),
        testcase_text="Given unknown testcase",
        target_path="features/generated.feature",
        create_file=True,
        overwrite_existing=False,
        quality_policy="strict",
    )

    feature_payload = result["feature"]
    assert feature_payload["quality"]["passed"] is False
    assert result["fileStatus"]["status"] == "skipped_quality_gate"
    assert not (project_root / "features" / "generated.feature").exists()

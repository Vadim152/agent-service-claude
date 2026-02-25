"""Orchestrator facade for steps scan and feature generation workflows."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, TypedDict

from langgraph.graph import END, StateGraph

from agents.feature_builder_agent import FeatureBuilderAgent
from agents.repo_scanner_agent import RepoScannerAgent
from agents.step_matcher_agent import StepMatcherAgent
from agents.testcase_parser_agent import TestcaseParserAgent
from infrastructure.embeddings_store import EmbeddingsStore
from infrastructure.fs_repo import FsRepository
from infrastructure.llm_client import LLMClient
from infrastructure.project_learning_store import ProjectLearningStore
from infrastructure.step_index_store import StepIndexStore
from integrations.jira_testcase_normalizer import normalize_jira_testcase
from integrations.jira_testcase_provider import JiraTestcaseProvider, extract_jira_testcase_key
from self_healing.capabilities import CapabilityRegistry
from tools.generation_quality import evaluate_generation_quality, normalize_quality_policy

logger = logging.getLogger(__name__)


class ScanState(TypedDict):
    project_root: str
    additional_roots: list[str]
    result: dict[str, Any]


class FeatureGenerationState(TypedDict, total=False):
    project_root: str
    testcase_text: str
    zephyr_auth: dict[str, Any] | None
    jira_instance: str | None
    target_path: str | None
    create_file: bool
    overwrite_existing: bool
    language: str | None
    quality_policy: str
    resolved_testcase_source: str | None
    resolved_testcase_key: str | None
    normalization_report: dict[str, Any] | None
    scenario: dict[str, Any]
    match_result: dict[str, Any]
    feature: dict[str, Any]
    quality_report: dict[str, Any]
    file_status: dict[str, Any] | None
    pipeline: list[dict[str, Any]]


class Orchestrator:
    """Coordinates domain agents and exposes capability-style methods."""

    def __init__(
        self,
        repo_scanner_agent: RepoScannerAgent,
        testcase_parser_agent: TestcaseParserAgent,
        step_matcher_agent: StepMatcherAgent,
        feature_builder_agent: FeatureBuilderAgent,
        step_index_store: StepIndexStore,
        embeddings_store: EmbeddingsStore,
        project_learning_store: ProjectLearningStore | None = None,
        llm_client: LLMClient | None = None,
        jira_testcase_provider: JiraTestcaseProvider | None = None,
    ) -> None:
        self.repo_scanner_agent = repo_scanner_agent
        self.testcase_parser_agent = testcase_parser_agent
        self.step_matcher_agent = step_matcher_agent
        self.feature_builder_agent = feature_builder_agent
        self.step_index_store = step_index_store
        self.embeddings_store = embeddings_store
        self.project_learning_store = project_learning_store
        self.llm_client = llm_client
        self.jira_testcase_provider = jira_testcase_provider or JiraTestcaseProvider()

        self.capability_registry = CapabilityRegistry()
        self._register_default_capabilities()
        self._scan_graph = self._build_scan_graph()
        self._feature_graph = self._build_feature_graph()

    def _register_default_capabilities(self) -> None:
        self.capability_registry.register("scan_steps", self.scan_steps)
        self.capability_registry.register("find_steps", self.find_steps)
        self.capability_registry.register("parse_testcase", self.testcase_parser_agent.parse_testcase)
        self.capability_registry.register("match_steps", self.step_matcher_agent.match_testcase_steps)
        self.capability_registry.register("build_feature", self.feature_builder_agent.build_feature_from_matches)
        self.capability_registry.register("compose_autotest", self.compose_autotest)
        self.capability_registry.register("explain_unmapped", self.explain_unmapped)
        self.capability_registry.register("apply_feature", self.apply_feature)
        self.capability_registry.register("run_test_execution", self.generate_feature)
        self.capability_registry.register("collect_run_artifacts", lambda *_args, **_kwargs: {})
        self.capability_registry.register("classify_failure", lambda *_args, **_kwargs: {})
        self.capability_registry.register("apply_remediation", lambda *_args, **_kwargs: {})
        self.capability_registry.register("rerun_with_strategy", self.generate_feature)
        self.capability_registry.register("incident_report_builder", lambda *_args, **_kwargs: {})

    def _build_scan_graph(self):
        graph = StateGraph(ScanState)
        graph.add_node("scan_repository", self._scan_repository_node())
        graph.set_entry_point("scan_repository")
        graph.add_edge("scan_repository", END)
        return graph.compile()

    def _build_feature_graph(self):
        graph = StateGraph(FeatureGenerationState)
        graph.add_node("resolve_testcase_source", self._resolve_testcase_source_node())
        graph.add_node("parse_testcase", self._parse_testcase_node())
        graph.add_node("match_steps", self._match_steps_node())
        graph.add_node("build_feature", self._build_feature_node())
        graph.add_node("assemble_pipeline", self._assemble_pipeline_node())
        graph.add_node("evaluate_quality", self._evaluate_quality_node())
        graph.add_node("apply_feature", self._apply_feature_node())
        graph.add_node("skip_apply", self._skip_apply_node())
        graph.set_entry_point("resolve_testcase_source")
        graph.add_edge("resolve_testcase_source", "parse_testcase")
        graph.add_edge("parse_testcase", "match_steps")
        graph.add_edge("match_steps", "build_feature")
        graph.add_edge("build_feature", "assemble_pipeline")
        graph.add_edge("assemble_pipeline", "evaluate_quality")
        graph.add_conditional_edges(
            "evaluate_quality",
            self._should_apply_feature,
            {"apply_feature": "apply_feature", "skip_apply": "skip_apply"},
        )
        graph.add_edge("apply_feature", END)
        graph.add_edge("skip_apply", END)
        return graph.compile()

    def _resolve_testcase_source_node(self) -> Callable[[FeatureGenerationState], dict[str, Any]]:
        def _node(state: FeatureGenerationState) -> dict[str, Any]:
            raw_text = state.get("testcase_text", "")
            key = extract_jira_testcase_key(raw_text)
            if not key:
                return {
                    "resolved_testcase_source": "raw_text",
                    "resolved_testcase_key": None,
                    "normalization_report": None,
                }

            try:
                payload = self.jira_testcase_provider.fetch_testcase(
                    key,
                    auth=state.get("zephyr_auth"),
                    jira_instance=state.get("jira_instance"),
                )
                normalized, normalization_report = normalize_jira_testcase(
                    payload,
                    llm_client=None,
                )
                if not normalized.strip():
                    raise RuntimeError(f"normalized testcase is empty for {key}")
            except Exception as exc:
                raise RuntimeError(
                    f"Jira testcase key detected but retrieval failed: {exc}"
                ) from exc

            source = "jira_stub_fixed" if key.upper() == "SCBC-T1" else "jira_live"
            logger.info("[Orchestrator] Resolved testcase key %s from %s", key, source)
            return {
                "testcase_text": normalized,
                "resolved_testcase_source": source,
                "resolved_testcase_key": key,
                "normalization_report": normalization_report,
            }

        return _node

    def _scan_repository_node(self) -> Callable[[ScanState], dict[str, Any]]:
        def _node(state: ScanState) -> dict[str, Any]:
            try:
                result = self.repo_scanner_agent.scan_repository(
                    state["project_root"],
                    additional_roots=state.get("additional_roots", []),
                )
            except TypeError:
                result = self.repo_scanner_agent.scan_repository(state["project_root"])
            return {"result": result}

        return _node

    def _parse_testcase_node(self) -> Callable[[FeatureGenerationState], dict[str, Any]]:
        def _node(state: FeatureGenerationState) -> dict[str, Any]:
            scenario_dict = self.testcase_parser_agent.parse_testcase(state["testcase_text"])
            resolved_key = state.get("resolved_testcase_key")
            if resolved_key:
                scenario_dict["tags"] = [f"TmsLink={str(resolved_key).strip()}"]
            return {"scenario": scenario_dict}

        return _node

    def _match_steps_node(self) -> Callable[[FeatureGenerationState], dict[str, Any]]:
        def _node(state: FeatureGenerationState) -> dict[str, Any]:
            matched = self.step_matcher_agent.match_testcase_steps(
                state["project_root"], state["scenario"]
            )
            return {"match_result": matched}

        return _node

    def _build_feature_node(self) -> Callable[[FeatureGenerationState], dict[str, Any]]:
        def _node(state: FeatureGenerationState) -> dict[str, Any]:
            feature_result = self.feature_builder_agent.build_feature_from_matches(
                state["scenario"],
                state.get("match_result", {}).get("matched", []),
                language=state.get("language"),
            )
            return {"feature": feature_result}

        return _node

    def _assemble_pipeline_node(self) -> Callable[[FeatureGenerationState], dict[str, Any]]:
        def _node(state: FeatureGenerationState) -> dict[str, Any]:
            normalization_details = self._compose_normalization_details(
                state.get("normalization_report"),
                state.get("scenario", {}).get("normalization"),
            )
            pipeline = [
                {
                    "stage": "source_resolve",
                    "status": state.get("resolved_testcase_source") or "raw_text",
                    "details": {
                        "jiraKey": state.get("resolved_testcase_key"),
                    },
                },
                {
                    "stage": "normalization",
                    "status": "ok",
                    "details": normalization_details,
                },
                {
                    "stage": "parse",
                    "status": "ok",
                    "details": {
                        "source": state.get("scenario", {}).get("source"),
                        "llmParseUsed": bool(
                            state.get("scenario", {})
                            .get("normalization", {})
                            .get("llmParseUsed")
                        ),
                        "steps": len(state.get("scenario", {}).get("steps", [])),
                    },
                },
                {
                    "stage": "match",
                    "status": "needs_scan"
                    if state.get("match_result", {}).get("needsScan")
                    else "ok",
                    "details": {
                        "matched": len(state.get("match_result", {}).get("matched", [])),
                        "unmatched": len(state.get("match_result", {}).get("unmatched", [])),
                        "indexStatus": state.get("match_result", {}).get("indexStatus", "unknown"),
                        "exactDefinitionMatches": int(
                            state.get("match_result", {}).get("exactDefinitionMatches", 0)
                        ),
                        "sourceTextFallbackUsed": int(
                            state.get("match_result", {}).get("sourceTextFallbackUsed", 0)
                        ),
                        "llmRerankedCount": int(
                            state.get("match_result", {}).get("llmRerankedCount", 0)
                        ),
                        "ambiguousCount": int(
                            state.get("match_result", {}).get("ambiguousCount", 0)
                        ),
                    },
                },
                {
                    "stage": "feature_build",
                    "status": state.get("feature", {}).get("buildStage") or "ok",
                    "details": {
                        "stepsSummary": state.get("feature", {}).get("stepsSummary"),
                        "language": state.get("feature", {}).get("meta", {}).get("language"),
                    },
                },
                {
                    "stage": "parameter_fill",
                    "status": "ok",
                    "details": state.get("feature", {}).get("parameterFillSummary", {}),
                },
            ]
            return {"pipeline": pipeline}

        return _node

    @staticmethod
    def _compose_normalization_details(
        source_report: dict[str, Any] | None,
        parser_report: dict[str, Any] | None,
    ) -> dict[str, Any]:
        source_report = source_report or {}
        parser_report = parser_report or {}
        input_steps = source_report.get("inputSteps")
        if input_steps is None:
            input_steps = parser_report.get("inputSteps", 0)
        normalized_steps = parser_report.get("normalizedSteps")
        if normalized_steps is None:
            normalized_steps = source_report.get("normalizedSteps", 0)

        split_count = int(source_report.get("splitCount", 0)) + int(
            parser_report.get("splitCount", 0)
        )
        llm_fallback_used = bool(source_report.get("llmFallbackUsed")) or bool(
            parser_report.get("llmFallbackUsed")
        )
        details = {
            "inputSteps": input_steps,
            "normalizedSteps": normalized_steps,
            "splitCount": split_count,
            "llmFallbackUsed": llm_fallback_used,
            "llmParseUsed": bool(parser_report.get("llmParseUsed")),
        }
        return details

    def _apply_feature_node(self) -> Callable[[FeatureGenerationState], dict[str, Any]]:
        def _node(state: FeatureGenerationState) -> dict[str, Any]:
            file_status: dict[str, Any] | None = None
            if state.get("target_path"):
                feature_text = state.get("feature", {}).get("featureText", "")
                file_status = self.apply_feature(
                    state["project_root"],
                    state["target_path"],
                    feature_text,
                    overwrite_existing=state.get("overwrite_existing", False),
                )
            return {"file_status": file_status}

        return _node

    def _skip_apply_node(self) -> Callable[[FeatureGenerationState], dict[str, Any]]:
        def _node(state: FeatureGenerationState) -> dict[str, Any]:
            if not (state.get("create_file") and state.get("target_path")):
                return {"file_status": None}
            quality = state.get("quality_report") or state.get("feature", {}).get("quality") or {}
            if bool(quality.get("passed", False)):
                return {"file_status": None}
            return {
                "file_status": {
                    "projectRoot": state.get("project_root"),
                    "targetPath": state.get("target_path"),
                    "status": "skipped_quality_gate",
                    "message": "Quality gate failed; feature was not written to disk",
                }
            }

        return _node

    def _evaluate_quality_node(self) -> Callable[[FeatureGenerationState], dict[str, Any]]:
        def _node(state: FeatureGenerationState) -> dict[str, Any]:
            feature_payload = dict(state.get("feature", {}))
            quality_report = evaluate_generation_quality(
                feature_payload=feature_payload,
                match_result=state.get("match_result", {}),
                scenario=state.get("scenario", {}),
                policy=state.get("quality_policy", "strict"),
            )
            feature_payload["quality"] = quality_report
            pipeline = list(state.get("pipeline", []))
            pipeline.append(
                {
                    "stage": "quality_gate",
                    "status": "passed" if quality_report.get("passed") else "failed",
                    "details": {
                        "policy": quality_report.get("policy"),
                        "score": quality_report.get("score"),
                        "failures": [
                            item.get("code")
                            for item in quality_report.get("failures", [])
                            if isinstance(item, dict)
                        ],
                    },
                }
            )
            return {
                "feature": feature_payload,
                "quality_report": quality_report,
                "pipeline": pipeline,
            }

        return _node

    @staticmethod
    def _should_apply_feature(state: FeatureGenerationState) -> str:
        quality = state.get("quality_report") or state.get("feature", {}).get("quality") or {}
        if (
            state.get("create_file")
            and state.get("target_path")
            and bool(quality.get("passed", False))
        ):
            return "apply_feature"
        return "skip_apply"

    def scan_steps(
        self,
        project_root: str,
        additional_roots: list[str] | None = None,
    ) -> dict[str, Any]:
        logger.info("[Orchestrator] Start steps scan: %s", project_root)
        state = self._scan_graph.invoke(
            {
                "project_root": project_root,
                "additional_roots": list(additional_roots or []),
            }
        )
        result = state["result"]
        logger.info("[Orchestrator] Steps scan done: %s", result)
        return result

    def find_steps(
        self,
        project_root: str,
        query: str,
        *,
        top_k: int = 5,
        debug: bool = False,
    ) -> dict[str, Any]:
        candidates = self.embeddings_store.get_top_k(project_root, query, top_k=top_k)
        payload = {
            "projectRoot": project_root,
            "query": query,
            "items": [
                {
                    "step": item.pattern,
                    "stepId": item.id,
                    "keyword": item.keyword.value,
                    "score": score,
                    "codeRef": item.code_ref,
                }
                for item, score in candidates
            ],
        }
        if debug:
            payload["debug"] = {
                "candidateCount": len(candidates),
                "topK": top_k,
            }
        return payload

    def compose_autotest(
        self,
        project_root: str,
        testcase_text: str,
        *,
        language: str | None = None,
        quality_policy: str | None = None,
    ) -> dict[str, Any]:
        return self.generate_feature(
            project_root=project_root,
            testcase_text=testcase_text,
            target_path=None,
            create_file=False,
            overwrite_existing=False,
            language=language,
            quality_policy=quality_policy,
        )

    @staticmethod
    def explain_unmapped(match_result: dict[str, Any]) -> dict[str, Any]:
        unmatched = list(match_result.get("unmatched", []))
        return {
            "count": len(unmatched),
            "items": [
                {
                    "step": text,
                    "reason": "no indexed step matched with acceptable confidence",
                }
                for text in unmatched
            ],
        }

    def generate_feature(
        self,
        project_root: str,
        testcase_text: str,
        target_path: str | None = None,
        *,
        create_file: bool = False,
        overwrite_existing: bool = False,
        language: str | None = None,
        zephyr_auth: dict[str, Any] | None = None,
        jira_instance: str | None = None,
        quality_policy: str | None = None,
    ) -> dict[str, Any]:
        logger.info("[Orchestrator] Generate feature for project %s", project_root)
        state = self._feature_graph.invoke(
            {
                "project_root": project_root,
                "testcase_text": testcase_text,
                "zephyr_auth": zephyr_auth,
                "jira_instance": jira_instance,
                "target_path": target_path,
                "create_file": create_file,
                "overwrite_existing": overwrite_existing,
                "language": language,
                "quality_policy": normalize_quality_policy(quality_policy),
            }
        )
        scenario_dict = state.get("scenario", {})
        match_result = state.get("match_result", {})
        feature_result = state.get("feature", {})
        pipeline = state.get("pipeline", [])
        file_status = state.get("file_status")

        logger.info(
            "[Orchestrator] Feature generation done. Unmapped=%s",
            len(feature_result.get("unmappedSteps", [])),
        )
        return {
            "projectRoot": project_root,
            "scenario": scenario_dict,
            "matchResult": match_result,
            "feature": feature_result,
            "pipeline": pipeline,
            "fileStatus": file_status,
        }

    def apply_feature(
        self,
        project_root: str,
        target_path: str,
        feature_text: str,
        *,
        overwrite_existing: bool = False,
    ) -> dict[str, Any]:
        logger.info("[Orchestrator] Persist feature %s in %s", target_path, project_root)
        fs_repo = FsRepository(project_root)
        project_root_path = Path(project_root).expanduser().resolve()
        candidate_path = Path(target_path).expanduser()
        resolved_path = (
            candidate_path.resolve()
            if candidate_path.is_absolute()
            else (project_root_path / candidate_path).resolve()
        )
        try:
            normalized_path = resolved_path.relative_to(project_root_path).as_posix()
        except ValueError:
            return {
                "projectRoot": project_root,
                "targetPath": target_path,
                "status": "rejected_outside_project",
                "message": "Target path is outside project root",
            }
        exists = fs_repo.exists(normalized_path)

        if exists and not overwrite_existing:
            return {
                "projectRoot": project_root,
                "targetPath": target_path,
                "status": "skipped",
                "message": "File already exists and overwrite is disabled",
            }

        fs_repo.write_text_file(normalized_path, feature_text, create_dirs=True)
        status = "overwritten" if exists else "created"
        return {
            "projectRoot": project_root,
            "targetPath": normalized_path,
            "status": status,
            "message": None,
        }


__all__ = ["Orchestrator"]

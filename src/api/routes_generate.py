"""Роуты, связанные с генерацией и применением .feature файлов."""

from __future__ import annotations

import logging
import json
from pathlib import Path
from typing import Iterable

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from agents.orchestrator import Orchestrator
from app.config import get_settings
from api.schemas import (
    ApplyFeatureRequest,
    ApplyFeatureResponse,
    GenerateFeatureRequest,
    GenerateFeatureResponse,
    GenerationPreviewRequest,
    GenerationPreviewResponse,
    PipelineStepDto,
    QualityReportDto,
    ReviewLearningRequest,
    ReviewLearningResponse,
    ReviewLearningResultDto,
    StepDetailDto,
    StepsSummaryDto,
    StepDefinitionDto,
    UnmappedStepDto,
)
from domain.enums import MatchStatus
from domain.models import MatchedStep

router = APIRouter(prefix="/platform/feature", tags=["platform-feature"])
logger = logging.getLogger(__name__)
settings = get_settings()


def _get_orchestrator(request: Request) -> Orchestrator:
    orchestrator: Orchestrator | None = getattr(request.app.state, "orchestrator", None)
    if not orchestrator:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Orchestrator is not initialized",
        )
    return orchestrator


def _dedup_used_steps(matched: Iterable[MatchedStep | dict[str, object]]) -> list[StepDefinitionDto]:
    seen: dict[str, StepDefinitionDto] = {}
    for entry in matched:
        if isinstance(entry, dict):
            step_def = entry.get("step_definition")
            status_value = entry.get("status")
        else:
            step_def = entry.step_definition
            status_value = entry.status.value

        if not step_def or status_value == MatchStatus.UNMATCHED.value:
            continue

        dto = (
            StepDefinitionDto.model_validate(step_def, from_attributes=True)
            if not isinstance(step_def, dict)
            else StepDefinitionDto.model_validate(step_def)
        )
        if dto.id in seen:
            continue

        seen[dto.id] = dto
    return list(seen.values())


@router.post(
    "/preview-generation",
    response_model=GenerationPreviewResponse,
    summary="Preview retrieval-driven generation plan before feature creation",
)
async def preview_generation(
    request_model: GenerationPreviewRequest,
    request: Request,
) -> GenerationPreviewResponse:
    orchestrator = _get_orchestrator(request)
    result = orchestrator.preview_generation_plan(
        project_root=request_model.project_root,
        testcase_text=request_model.test_case_text,
        language=request_model.language,
        quality_policy=request_model.quality_policy,
        selected_scenario_id=request_model.selected_scenario_id,
        selected_scenario_candidate_id=request_model.selected_scenario_candidate_id,
        accepted_assumption_ids=request_model.accepted_assumption_ids,
        clarifications=request_model.clarifications,
        binding_overrides=[
            item.model_dump(by_alias=True, mode="json") for item in request_model.binding_overrides
        ],
    )
    return GenerationPreviewResponse.model_validate(result)


@router.post(
    "/generate-feature",
    response_model=GenerateFeatureResponse,
    summary="Сгенерировать Gherkin-файл по тесткейсу",
)
async def generate_feature(request: Request) -> GenerateFeatureResponse:
    """Генерирует .feature текст и, опционально, сохраняет его на диск."""

    raw_body = await request.body()
    content_length = request.headers.get("content-length")
    content_type = request.headers.get("content-type")
    body_len = len(raw_body) if raw_body else 0
    if settings.log_request_bodies and raw_body:
        body_preview = raw_body.decode("utf-8", errors="replace")[:500]
        body_hex_preview = raw_body[:128].hex()
    else:
        body_preview = f"<{body_len} bytes>"
        body_hex_preview = "<disabled>"

    logger.debug(
        (
            "API: generate-feature raw body; client=%s, method=%s %s, content-length=%s, "
            "read_len=%s, hex_preview=%s, utf8_preview=%r"
        ),
        request.client,
        request.method,
        request.url.path,
        content_length,
        body_len,
        body_hex_preview,
        body_preview,
    )

    if not raw_body:
        logger.debug("API: request headers snapshot=%s", dict(request.headers))
        if content_length not in (None, str(body_len)):
            logger.debug(
                "API: Content-Length mismatch: header=%s, read=%s", content_length, body_len
            )

        logger.warning(
            (
                "API: пустое тело запроса (len=%s, content-length=%s, content-type=%s, "
                "body=%r)"
            ),
            body_len,
            content_length,
            content_type,
            body_preview,
        )
        mismatch_note = (
            "; Content-Length differs from read body"
            if content_length not in (None, str(body_len))
            else ""
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Request body is empty; ensure Content-Type: application/json and non-empty payload"
                f" (read {body_len} bytes, content-length={content_length}{mismatch_note})"
            ),
        )

    raw_payload_dict: dict[str, object] = {}
    try:
        loaded = json.loads(raw_body.decode("utf-8"))
        if isinstance(loaded, dict):
            raw_payload_dict = loaded
    except Exception:
        raw_payload_dict = {}

    try:
        request_model = GenerateFeatureRequest.model_validate_json(raw_body)
    except ValidationError as exc:  # pragma: no cover - форма валидируется FastAPI
        logger.warning("API: generate-feature validation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=jsonable_encoder(exc.errors()),
        ) from exc

    if content_length not in (None, str(body_len)):
        logger.info(
            "API: Body read length differs from Content-Length: header=%s, read=%s",
            content_length,
            body_len,
        )

    logger.info(
        (
            "API: generate-feature payload accepted (len=%s, content-type=%s, content-length=%s,"
            " testCaseText_len=%s, targetPath=%s, options=%s)"
        ),
        body_len,
        content_type,
        content_length,
        len(request_model.test_case_text or ""),
        request_model.target_path,
        request_model.options,
    )

    if not (request_model.test_case_text or "").strip():
        logger.warning(
            "API: testCaseText пустой или состоит из пробелов; возможно перепутаны поля UI? targetPath=%s, options=%s",  # noqa: E501
            request_model.target_path,
            request_model.options,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "testCaseText is empty; ensure the UI sends the original test case text, "
                "not the generated feature body"
            ),
        )

    orchestrator = _get_orchestrator(request)
    project_root = request_model.project_root
    path_obj = Path(project_root).expanduser()
    if not path_obj.exists():
        logger.warning("Проект не найден: %s", project_root)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project root not found: {project_root}",
        )

    options = request_model.options or None
    logger.info(
        "API: генерация feature (len=%s) для %s", len(request_model.test_case_text), project_root
    )
    result = orchestrator.generate_feature(
        project_root,
        request_model.test_case_text,
        request_model.target_path,
        create_file=bool(options.create_file) if options else False,
        overwrite_existing=bool(options.overwrite_existing) if options else False,
        language=options.language if options else None,
        zephyr_auth=request_model.zephyr_auth.model_dump(by_alias=True, mode="json")
        if request_model.zephyr_auth
        else None,
        jira_instance=request_model.jira_instance,
        quality_policy=request_model.quality_policy,
        explicit_quality_policy="qualityPolicy" in raw_payload_dict,
        explicit_language=bool(options and options.language is not None),
        explicit_target_path=request_model.target_path is not None,
        plan_id=request_model.plan_id,
        selected_scenario_id=request_model.selected_scenario_id,
        selected_scenario_candidate_id=request_model.selected_scenario_candidate_id,
        accepted_assumption_ids=request_model.accepted_assumption_ids,
        clarifications=request_model.clarifications,
        binding_overrides=[
            item.model_dump(by_alias=True, mode="json") for item in request_model.binding_overrides
        ],
    )
    generation_meta = result.get("feature", {}).get("meta") or {}
    if bool(generation_meta.get("generationBlocked", False)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": generation_meta.get("blockingReason")
                or "Generation blocked until required clarifications are provided",
                "canonicalIntent": generation_meta.get("canonicalIntent"),
                "ambiguityIssues": generation_meta.get("ambiguityIssues") or [],
            },
        )

    feature_payload = result.get("feature", {})
    match_payload = result.get("matchResult", {})
    pipeline_raw = result.get("pipeline") or []
    feature_text = feature_payload.get("featureText", "")
    unmapped_steps = [
        UnmappedStepDto(text=step_text, reason="not matched")
        for step_text in feature_payload.get("unmappedSteps", [])
    ]
    used_steps = _dedup_used_steps(match_payload.get("matched", []))
    step_details_raw = feature_payload.get("stepDetails") or []
    step_details = []
    for entry in step_details_raw:
        try:
            step_details.append(StepDetailDto.model_validate(entry))
        except Exception:
            continue
    steps_summary_raw = feature_payload.get("stepsSummary") or {}
    steps_summary = StepsSummaryDto(
        exact=steps_summary_raw.get("exact", 0),
        fuzzy=steps_summary_raw.get("fuzzy", 0),
        unmatched=steps_summary_raw.get("unmatched", 0),
    )
    quality = None
    quality_payload = feature_payload.get("quality")
    if isinstance(quality_payload, dict):
        quality = QualityReportDto.model_validate(quality_payload)

    logger.info(
        "API: генерация завершена, unmapped=%s, used_steps=%s",
        len(unmapped_steps),
        len(used_steps),
    )
    pipeline = [PipelineStepDto.model_validate(entry) for entry in pipeline_raw]
    return GenerateFeatureResponse(
        feature_text=feature_text,
        unmapped_steps=unmapped_steps,
        unmapped=match_payload.get("unmatched", []),
        used_steps=used_steps,
        build_stage=feature_payload.get("buildStage"),
        steps_summary=steps_summary,
        meta=feature_payload.get("meta"),
        pipeline=pipeline,
        step_details=step_details,
        parameter_fill_summary=feature_payload.get("parameterFillSummary") or {},
        quality=quality,
        plan_id=((feature_payload.get("meta") or {}).get("planId")),
        selected_scenario_id=((feature_payload.get("meta") or {}).get("selectedScenarioId")),
        selected_scenario_candidate_id=((feature_payload.get("meta") or {}).get("selectedScenarioCandidateId")),
        coverage_report=quality_payload.get("coverageReport") if isinstance(quality_payload, dict) else None,
        generation_blocked=bool((feature_payload.get("meta") or {}).get("generationBlocked", False)),
        warnings=[
            str(item.get("code"))
            for item in (quality_payload.get("warnings", []) if isinstance(quality_payload, dict) else [])
            if isinstance(item, dict) and item.get("code")
        ],
    )


@router.post(
    "/apply-feature",
    response_model=ApplyFeatureResponse,
    summary="Сохранить .feature файл на диске",
)
async def apply_feature(request_model: ApplyFeatureRequest, request: Request) -> ApplyFeatureResponse:
    """Записывает переданный .feature текст в проект."""

    orchestrator = _get_orchestrator(request)
    project_root = request_model.project_root
    path_obj = Path(project_root).expanduser()
    if not path_obj.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project root not found: {project_root}",
        )

    logger.info(
        "API: сохранение feature для %s -> %s", project_root, request_model.target_path
    )
    result = orchestrator.apply_feature(
        project_root,
        request_model.target_path,
        request_model.feature_text,
        overwrite_existing=request_model.overwrite_existing,
    )

    status_value = result.get("status", "created")
    message_value = result.get("message")
    logger.info(
        "API: сохранение завершено %s, статус=%s", request_model.target_path, status_value
    )
    return ApplyFeatureResponse(
        project_root=result.get("projectRoot", project_root),
        target_path=result.get("targetPath", request_model.target_path),
        status=status_value,
        message=message_value,
    )


@router.post(
    "/review-apply",
    response_model=ReviewLearningResponse,
    summary="Capture review edits, save learning, and apply feature safely",
)
async def review_apply_feature(
    request_model: ReviewLearningRequest,
    request: Request,
) -> ReviewLearningResponse:
    orchestrator = _get_orchestrator(request)
    result = orchestrator.review_and_apply_feature(
        project_root=request_model.project_root,
        plan_id=request_model.plan_id,
        target_path=request_model.target_path,
        original_feature_text=request_model.original_feature_text,
        edited_feature_text=request_model.edited_feature_text,
        overwrite_existing=request_model.overwrite_existing,
        selected_scenario_id=request_model.selected_scenario_id,
        selected_scenario_candidate_id=request_model.selected_scenario_candidate_id,
        accepted_step_ids=request_model.accepted_step_ids,
        rejected_step_ids=request_model.rejected_step_ids,
        accepted_assumption_ids=request_model.accepted_assumption_ids,
        rejected_candidate_ids=request_model.rejected_candidate_ids,
        binding_decisions=request_model.binding_decisions,
        confirmed_clarifications=request_model.confirmed_clarifications,
        binding_overrides=[
            item.model_dump(by_alias=True, mode="json") for item in request_model.binding_overrides
        ],
    )
    file_status = result.get("fileStatus") or {}
    quality = result.get("quality")
    learning = result.get("learning") or {}
    return ReviewLearningResponse(
        plan_id=result.get("planId"),
        file_status=ApplyFeatureResponse(
            project_root=file_status.get("projectRoot", request_model.project_root),
            target_path=file_status.get("targetPath", request_model.target_path),
            status=file_status.get("status", "created"),
            message=file_status.get("message"),
        ),
        quality=QualityReportDto.model_validate(quality) if isinstance(quality, dict) else None,
        learning=ReviewLearningResultDto.model_validate(learning),
    )

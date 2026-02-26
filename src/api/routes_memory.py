"""Project memory and feedback routes for learning loop."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from api.schemas import (
    GenerationResolvePreviewRequest,
    GenerationResolvePreviewResponse,
    GenerationRuleCreateRequest,
    GenerationRuleDto,
    GenerationRuleListResponse,
    GenerationRulePatchRequest,
    MemoryFeedbackRequest,
    MemoryFeedbackResponse,
    StepTemplateCreateRequest,
    StepTemplateDto,
    StepTemplateListResponse,
    StepTemplatePatchRequest,
)
from memory.service import MemoryService

router = APIRouter(prefix="/memory", tags=["memory"])


def _get_learning_store(request: Request) -> MemoryService:
    orchestrator = getattr(request.app.state, "orchestrator", None)
    store = getattr(orchestrator, "project_learning_store", None) if orchestrator else None
    if not store:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Project learning store is not initialized",
        )
    return store


@router.post("/feedback", response_model=MemoryFeedbackResponse)
async def submit_feedback(payload: MemoryFeedbackRequest, request: Request) -> MemoryFeedbackResponse:
    store = _get_learning_store(request)
    updated = store.record_feedback(
        project_root=payload.project_root,
        step_id=payload.step_id,
        accepted=payload.accepted,
        note=payload.note,
        preference_key=payload.preference_key,
        preference_value=payload.preference_value,
    )
    return MemoryFeedbackResponse(
        project_root=payload.project_root,
        updated_at=updated.get("updatedAt"),
        step_boosts=store.get_step_boosts(payload.project_root),
        feedback_count=len(updated.get("feedback", [])),
    )


@router.get("/rules", response_model=GenerationRuleListResponse)
async def list_rules(projectRoot: str, request: Request) -> GenerationRuleListResponse:
    store = _get_learning_store(request)
    return GenerationRuleListResponse(
        project_root=projectRoot,
        items=[GenerationRuleDto.model_validate(item) for item in store.list_generation_rules(projectRoot)],
    )


@router.post("/rules", response_model=GenerationRuleDto)
async def create_rule(payload: GenerationRuleCreateRequest, request: Request) -> GenerationRuleDto:
    store = _get_learning_store(request)
    body = payload.model_dump(by_alias=True, mode="json")
    body.pop("projectRoot", None)
    try:
        created = store.add_generation_rule(
            payload.project_root,
            body,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return GenerationRuleDto.model_validate(created)


@router.patch("/rules/{rule_id}", response_model=GenerationRuleDto)
async def patch_rule(
    rule_id: str,
    payload: GenerationRulePatchRequest,
    request: Request,
) -> GenerationRuleDto:
    store = _get_learning_store(request)
    body = payload.model_dump(exclude_none=True, by_alias=True, mode="json")
    body.pop("projectRoot", None)
    try:
        updated = store.update_generation_rule(
            payload.project_root,
            rule_id,
            body,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Rule not found: {rule_id}")
    return GenerationRuleDto.model_validate(updated)


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str, projectRoot: str, request: Request) -> dict[str, object]:
    store = _get_learning_store(request)
    deleted = store.delete_generation_rule(projectRoot, rule_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Rule not found: {rule_id}")
    return {"deleted": True, "ruleId": rule_id}


@router.get("/templates", response_model=StepTemplateListResponse)
async def list_templates(projectRoot: str, request: Request) -> StepTemplateListResponse:
    store = _get_learning_store(request)
    return StepTemplateListResponse(
        project_root=projectRoot,
        items=[StepTemplateDto.model_validate(item) for item in store.list_step_templates(projectRoot)],
    )


@router.post("/templates", response_model=StepTemplateDto)
async def create_template(payload: StepTemplateCreateRequest, request: Request) -> StepTemplateDto:
    store = _get_learning_store(request)
    body = payload.model_dump(by_alias=True, mode="json")
    body.pop("projectRoot", None)
    try:
        created = store.add_step_template(
            payload.project_root,
            body,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return StepTemplateDto.model_validate(created)


@router.patch("/templates/{template_id}", response_model=StepTemplateDto)
async def patch_template(
    template_id: str,
    payload: StepTemplatePatchRequest,
    request: Request,
) -> StepTemplateDto:
    store = _get_learning_store(request)
    body = payload.model_dump(exclude_none=True, by_alias=True, mode="json")
    body.pop("projectRoot", None)
    try:
        updated = store.update_step_template(
            payload.project_root,
            template_id,
            body,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Template not found: {template_id}")
    return StepTemplateDto.model_validate(updated)


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str, projectRoot: str, request: Request) -> dict[str, object]:
    store = _get_learning_store(request)
    deleted = store.delete_step_template(projectRoot, template_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Template not found: {template_id}")
    return {"deleted": True, "templateId": template_id}


@router.post("/resolve-preview", response_model=GenerationResolvePreviewResponse)
async def resolve_preview(
    payload: GenerationResolvePreviewRequest,
    request: Request,
) -> GenerationResolvePreviewResponse:
    store = _get_learning_store(request)
    resolved = store.resolve_generation_preferences(
        project_root=payload.project_root,
        text=payload.text,
        jira_key=payload.jira_key,
        language=payload.language,
        quality_policy=payload.quality_policy,
    )
    return GenerationResolvePreviewResponse(
        project_root=payload.project_root,
        quality_policy=resolved.get("qualityPolicy"),
        language=resolved.get("language"),
        target_path=resolved.get("targetPath"),
        applied_rule_ids=list(resolved.get("appliedRuleIds", [])),
        applied_template_ids=list(resolved.get("appliedTemplateIds", [])),
        template_steps=list(resolved.get("templateSteps", [])),
    )

"""Skill/tool style endpoints for step retrieval and autotest composition."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field

from api.schemas import ApiBaseModel

router = APIRouter(prefix="/tools", tags=["tools"])


class FindStepsRequest(ApiBaseModel):
    project_root: str = Field(..., alias="projectRoot")
    query: str
    top_k: int = Field(default=5, alias="topK")
    debug: bool = Field(default=False)


class ComposeAutotestRequest(ApiBaseModel):
    project_root: str = Field(..., alias="projectRoot")
    testcase_text: str = Field(..., alias="testCaseText")
    language: str | None = None
    quality_policy: str = Field(default="strict", alias="qualityPolicy")


class ExplainUnmappedRequest(ApiBaseModel):
    match_result: dict = Field(..., alias="matchResult")


def _get_orchestrator(request: Request):
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if not orchestrator:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Orchestrator is not initialized",
        )
    return orchestrator


@router.post("/find-steps")
async def find_steps(payload: FindStepsRequest, request: Request) -> dict:
    orchestrator = _get_orchestrator(request)
    return orchestrator.find_steps(
        project_root=payload.project_root,
        query=payload.query,
        top_k=max(1, min(payload.top_k, 50)),
        debug=payload.debug,
    )


@router.post("/compose-autotest")
async def compose_autotest(payload: ComposeAutotestRequest, request: Request) -> dict:
    orchestrator = _get_orchestrator(request)
    return orchestrator.compose_autotest(
        project_root=payload.project_root,
        testcase_text=payload.testcase_text,
        language=payload.language,
        quality_policy=payload.quality_policy,
    )


@router.post("/explain-unmapped")
async def explain_unmapped(payload: ExplainUnmappedRequest, request: Request) -> dict:
    orchestrator = _get_orchestrator(request)
    return orchestrator.explain_unmapped(payload.match_result)

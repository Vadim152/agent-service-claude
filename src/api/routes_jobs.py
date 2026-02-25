"""Job API: control-plane endpoints and SSE stream."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from api.schemas import (
    JobAttemptsResponse,
    JobCancelResponse,
    JobCreateRequest,
    JobCreateResponse,
    JobEventDto,
    JobFeatureResultDto,
    JobResultResponse,
    JobStatusResponse,
    RunAttemptDto,
)


router = APIRouter(prefix="/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _schedule_job_execution(request: Request, *, job_id: str, supervisor, run_state_store) -> None:
    async def _worker() -> None:
        await supervisor.execute_job(job_id)

    def _on_error(exc: BaseException) -> None:
        logger.warning("Job execution failed (job_id=%s): %s", job_id, exc)
        run_state_store.patch_job(
            job_id,
            status="needs_attention",
            finished_at=_utcnow(),
            incident_uri=None,
        )
        run_state_store.append_event(
            job_id,
            "job.worker_failed",
            {"jobId": job_id, "status": "needs_attention", "message": str(exc)},
        )

    task_registry = getattr(request.app.state, "task_registry", None)
    dispatcher = getattr(request.app.state, "job_dispatcher", None)
    if dispatcher is not None:
        try:
            dispatcher.dispatch(
                job_id=job_id,
                source="jobs",
                supervisor=supervisor,
                run_state_store=run_state_store,
                task_registry=task_registry,
                on_error=_on_error,
            )
            return
        except Exception as exc:
            _on_error(exc)
            return

    if task_registry is None:
        asyncio.create_task(_worker())
        return

    task_registry.create_task(
        _worker(),
        source="jobs",
        metadata={"jobId": job_id},
        on_error=_on_error,
    )


@router.post("", response_model=JobCreateResponse)
async def create_job(payload: JobCreateRequest, request: Request) -> JobCreateResponse:
    supervisor = getattr(request.app.state, "execution_supervisor", None)
    run_state_store = getattr(request.app.state, "run_state_store", None)
    if not supervisor or not run_state_store:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Job control plane is not initialized")

    idempotency_key = request.headers.get("Idempotency-Key")
    payload_fingerprint = hashlib.sha256(
        json.dumps(
            payload.model_dump(by_alias=True, mode="json"),
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    job_id = str(uuid.uuid4())
    if idempotency_key:
        claimed, existing_job_id = run_state_store.claim_idempotency_key(
            idempotency_key,
            fingerprint=payload_fingerprint,
            job_id=job_id,
        )
        if not claimed and existing_job_id:
            existing = run_state_store.get_job(existing_job_id)
            if not existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency key is bound to missing job state",
                )
            return JobCreateResponse(
                job_id=existing_job_id,
                status=str(existing.get("status", "queued")),
            )
        if not claimed and not existing_job_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency key reuse with different payload is not allowed",
            )
    zephyr_auth = (
        payload.zephyr_auth.model_dump(by_alias=True, mode="json")
        if payload.zephyr_auth
        else None
    )
    run_state_store.put_job(
        {
            "job_id": job_id,
            "status": "queued",
            "cancel_requested": False,
            "cancel_requested_at": None,
            "project_root": payload.project_root,
            "test_case_text": payload.test_case_text,
            "target_path": payload.target_path,
            "create_file": payload.create_file,
            "overwrite_existing": payload.overwrite_existing,
            "language": payload.language,
            "quality_policy": payload.quality_policy,
            "zephyr_auth": zephyr_auth,
            "jira_instance": payload.jira_instance,
            "profile": payload.profile,
            "source": payload.source,
            "started_at": _utcnow(),
            "updated_at": _utcnow(),
            "attempts": [],
            "result": None,
        }
    )
    run_state_store.append_event(job_id, "job.queued", {"jobId": job_id, "source": payload.source})
    _schedule_job_execution(request, job_id=job_id, supervisor=supervisor, run_state_store=run_state_store)
    return JobCreateResponse(job_id=job_id, status="queued")


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str, request: Request) -> JobStatusResponse:
    run_state_store = getattr(request.app.state, "run_state_store", None)
    if not run_state_store:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Job control plane is not initialized")

    item = run_state_store.get_job(job_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job not found: {job_id}")

    return JobStatusResponse(
        job_id=job_id,
        run_id=item.get("run_id"),
        status=item.get("status", "queued"),
        source=item.get("source"),
        incident_uri=item.get("incident_uri"),
        started_at=datetime.fromisoformat(item["started_at"]) if item.get("started_at") else None,
        finished_at=datetime.fromisoformat(item["finished_at"]) if item.get("finished_at") else None,
    )


@router.get("/{job_id}/attempts", response_model=JobAttemptsResponse)
async def get_job_attempts(job_id: str, request: Request) -> JobAttemptsResponse:
    run_state_store = getattr(request.app.state, "run_state_store", None)
    if not run_state_store:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Job control plane is not initialized")

    item = run_state_store.get_job(job_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job not found: {job_id}")

    attempts = [RunAttemptDto.model_validate(attempt) for attempt in item.get("attempts", [])]
    return JobAttemptsResponse(job_id=job_id, run_id=item.get("run_id"), attempts=attempts)


@router.get("/{job_id}/result", response_model=JobResultResponse)
async def get_job_result(job_id: str, request: Request) -> JobResultResponse:
    run_state_store = getattr(request.app.state, "run_state_store", None)
    if not run_state_store:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Job control plane is not initialized")

    item = run_state_store.get_job(job_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job not found: {job_id}")

    feature_payload = item.get("result")
    if feature_payload is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Result is not ready for job: {job_id}",
        )

    attempts = [RunAttemptDto.model_validate(attempt) for attempt in item.get("attempts", [])]
    return JobResultResponse(
        job_id=job_id,
        run_id=item.get("run_id"),
        status=item.get("status", "queued"),
        source=item.get("source"),
        incident_uri=item.get("incident_uri"),
        started_at=datetime.fromisoformat(item["started_at"]) if item.get("started_at") else None,
        finished_at=datetime.fromisoformat(item["finished_at"]) if item.get("finished_at") else None,
        feature=JobFeatureResultDto.model_validate(feature_payload),
        attempts=attempts,
    )


@router.post("/{job_id}/cancel", response_model=JobCancelResponse)
async def cancel_job(job_id: str, request: Request) -> JobCancelResponse:
    run_state_store = getattr(request.app.state, "run_state_store", None)
    if not run_state_store:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Job control plane is not initialized")

    item = run_state_store.get_job(job_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job not found: {job_id}")

    current_status = str(item.get("status", "queued"))
    if current_status in {"succeeded", "failed", "needs_attention", "cancelled"}:
        return JobCancelResponse(
            job_id=job_id,
            status=current_status,
            cancel_requested=False,
            effective_status=current_status,
        )

    next_status = "cancelling" if current_status in {"queued", "running", "cancelling"} else "cancelled"
    updated = run_state_store.patch_job(
        job_id,
        status=next_status,
        cancel_requested=True,
        cancel_requested_at=_utcnow(),
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job not found: {job_id}")

    run_state_store.append_event(
        job_id,
        "job.cancellation_requested",
        {"jobId": job_id, "status": next_status},
    )
    return JobCancelResponse(
        job_id=job_id,
        status=next_status,
        cancel_requested=True,
        effective_status=next_status,
    )


@router.get("/{job_id}/events")
async def stream_job_events(job_id: str, request: Request, fromIndex: int = 0):
    run_state_store = getattr(request.app.state, "run_state_store", None)
    if not run_state_store:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Job control plane is not initialized")

    if not run_state_store.get_job(job_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job not found: {job_id}")

    async def event_stream():
        idx = max(0, fromIndex)
        loop = asyncio.get_running_loop()
        heartbeat_interval_s = 2.0
        last_emit_ts = loop.time()
        while True:
            if await request.is_disconnected():
                return
            events, idx = run_state_store.list_events(job_id, idx)
            if not events:
                now = loop.time()
                if now - last_emit_ts >= heartbeat_interval_s:
                    payload = JobEventDto(
                        event_type="heartbeat",
                        payload={"jobId": job_id},
                        created_at=datetime.now(timezone.utc),
                        index=idx,
                    ).model_dump(by_alias=True, mode="json")
                    yield f"event: heartbeat\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    last_emit_ts = now
                await asyncio.sleep(0.2)
                continue
            for event in events:
                payload = JobEventDto(
                    event_type=event["event_type"],
                    payload=event["payload"],
                    created_at=datetime.fromisoformat(event["created_at"]),
                    index=event["index"],
                ).model_dump(by_alias=True, mode="json")
                yield f"event: {payload['eventType']}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                last_emit_ts = loop.time()

    return StreamingResponse(event_stream(), media_type="text/event-stream")

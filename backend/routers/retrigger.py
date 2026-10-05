"""
Retrigger (re-ingestion) router.

Endpoints for managing retrigger projects (S3 bucket + DynamoDB table pairs
for a customer's invoice pipeline) and running the re-ingestion pipeline
against a batch of invoice numbers.

Execution model: creating a job invokes ProcessorFunction asynchronously
(same pattern as /run-test and /intake) — this Lambda (ApiFunction) never
runs the pipeline itself, since batch sleeps between S3 copy batches can
run well past API Gateway's request timeout.
"""

import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from routers.auth import get_current_user
from tools.retrigger_tools import (
    list_retrigger_projects,
    get_retrigger_project,
    create_retrigger_project,
    update_retrigger_project,
    delete_retrigger_project,
    list_retrigger_jobs,
    get_retrigger_job,
    save_retrigger_job,
    update_job_step,
    update_retrigger_job,
    append_job_log,
)
from models.retrigger import (
    RetriggerProjectCreate,
    RetriggerProjectUpdate,
    default_steps,
    BULK_ACTIONS,
)
from services.bulk_fetch_pipeline import steps_for_action

router = APIRouter(prefix="/retrigger", tags=["retrigger"])


# ── Projects ───────────────────────────────────────────────────────────────────

@router.get("/projects")
def list_projects(_user=Depends(get_current_user)):
    return list_retrigger_projects()


@router.get("/projects/{project_id}")
def get_project(project_id: str, _user=Depends(get_current_user)):
    project = get_retrigger_project(project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{project_id}' not found")
    return project


@router.post("/projects", status_code=status.HTTP_201_CREATED)
def create_project(body: RetriggerProjectCreate, _user=Depends(get_current_user)):
    name = body.project_name.strip()
    bucket = body.s3_bucket.strip()
    table = body.dynamodb_table.strip()
    if not name or not bucket or not table:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="project_name, s3_bucket, and dynamodb_table are required.",
        )
    return create_retrigger_project({
        "project_name": name,
        "s3_bucket": bucket,
        "dynamodb_table": table,
        "aws_region": body.aws_region or "us-east-1",
        "batch_size": body.batch_size,
        "batch_sleep_secs": body.batch_sleep_secs,
        "destination_bucket": (body.destination_bucket or "").strip(),
        "cloudwatch_log_group": (body.cloudwatch_log_group or "").strip(),
        "payload_filename": (body.payload_filename or "api_payload.json").strip()
        or "api_payload.json",
        "log_lookback_seconds": body.log_lookback_seconds or 604800,
    })


@router.put("/projects/{project_id}")
def edit_project(project_id: str, body: RetriggerProjectUpdate, _user=Depends(get_current_user)):
    updates = body.model_dump(exclude_unset=True)
    updated = update_retrigger_project(project_id, updates)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{project_id}' not found")
    return updated


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_project(project_id: str, _user=Depends(get_current_user)):
    if not delete_retrigger_project(project_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{project_id}' not found")


# ── Jobs ───────────────────────────────────────────────────────────────────────

class RetriggerJobCreateBody(BaseModel):
    project_id: str
    invoice_numbers: list[str]
    folder_name: str
    action: str = "retrigger"  # retrigger | fetch_payload | fetch_pdf | fetch_logs


def _project_cfg(project: dict) -> dict:
    return {
        "project_name": project.get("project_name") or "",
        "s3_bucket": project["s3_bucket"],
        "dynamodb_table": project["dynamodb_table"],
        "aws_region": project.get("aws_region") or "us-east-1",
        "batch_size": project.get("batch_size", 10),
        "batch_sleep_secs": project.get("batch_sleep_secs", 45),
        "destination_bucket": project.get("destination_bucket") or "",
        "cloudwatch_log_group": project.get("cloudwatch_log_group") or "",
        "payload_filename": project.get("payload_filename") or "api_payload.json",
        "log_lookback_seconds": project.get("log_lookback_seconds") or 604800,
    }


def _invoke_processor(payload: dict) -> None:
    """Fire-and-forget invoke of ProcessorFunction — mirrors agent_runner.py."""
    processor_arn = os.environ.get("PROCESSOR_FUNCTION_ARN")
    if not processor_arn:
        import threading
        from services.retrigger_pipeline import run_retrigger_pipeline, run_fetch_records
        from services.bulk_fetch_pipeline import run_bulk_fetch_pipeline

        mode = payload.get("mode")
        if mode == "retrigger":
            target = run_retrigger_pipeline
            args = (payload["job_id"], payload["invoice_numbers"], payload["cfg"])
        elif mode == "retrigger_resume":
            target = run_retrigger_pipeline
            args = (
                payload["job_id"],
                payload["invoice_numbers"],
                payload["cfg"],
                payload.get("resume_from_step"),
            )
        elif mode == "retrigger_fetch_records":
            target = run_fetch_records
            args = (payload["job_id"], payload["invoice_numbers"], payload["cfg"])
        elif mode == "bulk_fetch":
            target = run_bulk_fetch_pipeline
            args = (
                payload["job_id"],
                payload["invoice_numbers"],
                payload["cfg"],
                payload.get("action"),
            )
        else:
            return
        threading.Thread(target=target, args=args, daemon=True).start()
        return

    import json
    import boto3
    try:
        boto3.client("lambda", region_name=os.environ.get("AWS_REGION", "us-east-1")).invoke(
            FunctionName=processor_arn,
            InvocationType="Event",
            Payload=json.dumps(payload).encode(),
        )
    except Exception as exc:
        print(f"[BulkOps] Failed to invoke processor Lambda: {exc}")


@router.get("/jobs")
def list_jobs(project_id: str | None = None, limit: int = 50, _user=Depends(get_current_user)):
    return list_retrigger_jobs(project_id=project_id, limit=limit)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, _user=Depends(get_current_user)):
    job = get_retrigger_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.post("/jobs", status_code=status.HTTP_201_CREATED)
def create_job(body: RetriggerJobCreateBody, _user=Depends(get_current_user)):
    project = get_retrigger_project(body.project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{body.project_id}' not found")

    action = (body.action or "retrigger").strip().lower()
    if action not in BULK_ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"action must be one of: {', '.join(BULK_ACTIONS)}",
        )

    invoices = list(dict.fromkeys(i.strip() for i in body.invoice_numbers if i.strip()))
    if not invoices:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="At least one invoice number is required.")

    folder_name = body.folder_name.strip()
    if not folder_name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Folder name is required.")

    if action == "fetch_logs" and not (project.get("cloudwatch_log_group") or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Project is missing cloudwatch_log_group (Batch or Lambda log group) for Fetch Logs.",
        )

    now = datetime.now(tz=timezone.utc).isoformat()
    job_id = f"bulk-{uuid.uuid4().hex[:10]}"
    steps = default_steps() if action == "retrigger" else steps_for_action(action)
    job = {
        "job_id": job_id,
        "project_id": project["project_id"],
        "project_name": project["project_name"],
        "folder_name": folder_name,
        "action": action,
        "invoice_numbers": invoices,
        "status": "pending",
        "steps": steps,
        "logs": [],
        "pk_records": [],
        "artifacts": [],
        "progress": {"completed_pks": [], "completed_message_ids": []},
        "paused_at_step": None,
        "created_at": now,
        "updated_at": now,
        "error": None,
        "summary": None,
        "output_dir": None,
    }
    save_retrigger_job(job)

    cfg = _project_cfg(project)
    if action == "retrigger":
        _invoke_processor({"mode": "retrigger", "job_id": job_id, "invoice_numbers": invoices, "cfg": cfg})
    else:
        _invoke_processor({
            "mode": "bulk_fetch",
            "job_id": job_id,
            "invoice_numbers": invoices,
            "cfg": cfg,
            "action": action,
        })

    return job


@router.get("/jobs/{job_id}/artifacts/{filename}")
def download_artifact(job_id: str, filename: str, _user=Depends(get_current_user)):
    """Download one saved artifact file from a completed bulk-fetch job."""
    from fastapi.responses import FileResponse
    from pathlib import Path

    job = get_retrigger_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    safe = Path(filename).name
    for art in job.get("artifacts") or []:
        if art.get("filename") != safe or art.get("status") != "OK":
            continue
        path = art.get("local_path")
        if path and Path(path).is_file():
            return FileResponse(path, filename=safe)
        break
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact file not found on disk")


@router.post("/jobs/{job_id}/fetch-records", status_code=status.HTTP_202_ACCEPTED)
def trigger_fetch_records(job_id: str, _user=Depends(get_current_user)):
    """Manually re-scan DynamoDB for this job's invoice numbers."""
    job = get_retrigger_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    project = get_retrigger_project(job["project_id"])
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    update_job_step(job_id, "fetch_records", "running")

    cfg = _project_cfg(project)
    _invoke_processor({
        "mode": "retrigger_fetch_records",
        "job_id": job_id,
        "invoice_numbers": job["invoice_numbers"],
        "cfg": cfg,
    })

    return get_retrigger_job(job_id)


@router.post("/jobs/{job_id}/pause", status_code=status.HTTP_200_OK)
def pause_job(job_id: str, _user=Depends(get_current_user)):
    """Pause a running retrigger job."""
    job = get_retrigger_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    if job.get("action", "retrigger") != "retrigger":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pause is only supported for Retrigger jobs")

    if job["status"] != "running":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Job is not running (current status: {job['status']})")

    current_step = None
    for step_name, step_data in job.get("steps", {}).items():
        if step_data.get("status") == "running":
            current_step = step_name
            break

    update_retrigger_job(job_id, {
        "status": "paused",
        "paused_at_step": current_step,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    })

    append_job_log(job_id, "info", f"Job paused at step: {current_step or 'unknown'}")

    return get_retrigger_job(job_id)


@router.post("/jobs/{job_id}/resume", status_code=status.HTTP_202_ACCEPTED)
def resume_job(job_id: str, _user=Depends(get_current_user)):
    """Resume a paused retrigger job."""
    job = get_retrigger_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    if job["status"] != "paused":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Job is not paused (current status: {job['status']})")

    project = get_retrigger_project(job["project_id"])
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    cfg = _project_cfg(project)

    _invoke_processor({
        "mode": "retrigger_resume",
        "job_id": job_id,
        "invoice_numbers": job["invoice_numbers"],
        "cfg": cfg,
        "resume_from_step": job.get("paused_at_step")
    })

    update_retrigger_job(job_id, {
        "status": "running",
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    })

    append_job_log(job_id, "info", f"Job resumed from step: {job.get('paused_at_step') or 'beginning'}")

    return get_retrigger_job(job_id)

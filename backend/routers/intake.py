"""
POST /api/intake
Public webhook endpoint called by the Invoice Processor Lambda after every run.

Authentication: X-Intake-Key header (shared secret, NOT a JWT token).
Response:       202 Accepted immediately — validation runs in a background thread.
Lambda must:    fire-and-forget with a 5-second timeout so it doesn't block.
"""

import threading
from fastapi import APIRouter, Header, HTTPException, status

from config import settings
from models.intake import IntakePayload
from agents.intake_processor import process_intake
from tools.mongodb_tools import save_job, update_job
from datetime import datetime, timezone
import uuid

router = APIRouter(tags=["intake"])


def _verify_key(x_intake_key: str | None) -> None:
    if not x_intake_key or x_intake_key != settings.INTAKE_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Intake-Key header",
        )


def _run_in_background(job_id: str, intake_dict: dict) -> None:
    try:
        result = process_intake(intake_dict)

        if "error" in result:
            update_job(job_id, {
                "status": "failed",
                "error": result["error"],
                "completed_at": datetime.now(tz=timezone.utc).isoformat(),
            })
        else:
            update_job(job_id, {
                "status": "complete",
                "result_id":     result.get("result_id"),
                "overall_score": result.get("overall_score"),
                "test_status":   result.get("status"),
                "project_id":    result.get("project_id"),
                "completed_at":  datetime.now(tz=timezone.utc).isoformat(),
            })
    except Exception as exc:
        print(f"[Intake] Background job {job_id} failed: {exc}")
        update_job(job_id, {
            "status": "failed",
            "error": str(exc),
            "completed_at": datetime.now(tz=timezone.utc).isoformat(),
        })


@router.post("/intake", status_code=status.HTTP_202_ACCEPTED)
def intake(
    body: IntakePayload,
    x_intake_key: str | None = Header(default=None),
):
    """
    Receive invoice data pushed by Lambda. Validates and stores the result
    in the background — returns immediately so Lambda is not blocked.

    Required header:  X-Intake-Key: <shared secret from INTAKE_API_KEY env var>
    """
    _verify_key(x_intake_key)

    job_id = f"intake-{uuid.uuid4().hex[:8]}"
    now    = datetime.now(tz=timezone.utc).isoformat()

    # Persist a job document so the UI can poll if needed
    save_job({
        "job_id":         job_id,
        "source":         "lambda_push",
        "project_id":     body.project_id,
        "invoice_number": body.invoice_number,
        "status":         "running",
        "created_at":     now,
    })

    thread = threading.Thread(
        target=_run_in_background,
        args=(job_id, body.model_dump()),
        daemon=True,
    )
    thread.start()

    return {
        "accepted": True,
        "job_id":   job_id,
        "message":  "Validation started. Poll /api/jobs/{job_id}/status for result.",
    }


@router.get("/intake/health")
def intake_health():
    """Quick liveness check Lambda can use to verify the tunnel is up."""
    return {"status": "ok", "endpoint": "/api/intake"}

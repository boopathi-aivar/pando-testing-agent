"""
Agent Runner
Starts a test job in a background thread, tracks per-step progress in MongoDB,
and writes the final result when the orchestrator finishes.
"""

import os
import json
import uuid
import threading
from datetime import datetime, timezone

from agents.orchestrator import run_test
from tools.dynamodb_tools import save_job, get_job, update_job

STEP_NAMES = [
    "Collecting inputs from S3",
    "Querying CloudWatch logs",
    "Validating payload fields",
    "Computing scores and suggestions",
    "Saving result to database",
]


def _update_step(job_id: str, step_index: int, status: str) -> None:
    job = get_job(job_id)
    if not job:
        return
    steps = job.get("steps", [])
    if step_index < len(steps):
        steps[step_index]["status"] = status
        if status == "complete":
            steps[step_index]["completed_at"] = datetime.now(tz=timezone.utc).isoformat()
        update_job(job_id, {"steps": steps})


def _run_background(job_id: str, project_id: str, invoice_number: str | None) -> None:
    """Executed in a daemon thread. Updates job steps in real-time."""
    try:
        # Steps 0-1: mark running before agent starts
        _update_step(job_id, 0, "running")
        update_job(job_id, {"status": "running"})

        # Step 0 completes when input collector returns (orchestrator drives timing)
        # We advance steps optimistically; the orchestrator logs mirror the progress
        import time

        def _advance(idx: int):
            _update_step(job_id, idx - 1, "complete")
            _update_step(job_id, idx,     "running")

        # Stagger step transitions so the UI shows real progression
        def _step_ticker():
            for i in range(1, len(STEP_NAMES)):
                time.sleep(3)
                _advance(i)

        ticker = threading.Thread(target=_step_ticker, daemon=True)
        ticker.start()

        result = run_test(project_id, invoice_number)

        # Mark all steps complete
        for i in range(len(STEP_NAMES)):
            _update_step(job_id, i, "complete")

        if "error" in result:
            update_job(job_id, {
                "status": "failed",
                "error": result["error"],
                "completed_at": datetime.now(tz=timezone.utc).isoformat(),
            })
        else:
            update_job(job_id, {
                "status": "complete",
                "result_id":    result.get("result_id"),
                "overall_score": result.get("overall_score"),
                "test_status":  result.get("status"),
                "completed_at": datetime.now(tz=timezone.utc).isoformat(),
            })

    except Exception as exc:
        print(f"[AgentRunner] Job {job_id} failed: {exc}")
        update_job(job_id, {
            "status": "failed",
            "error": str(exc),
            "completed_at": datetime.now(tz=timezone.utc).isoformat(),
        })


def _invoke_processor_lambda(payload: dict) -> None:
    """
    Invoke the processor Lambda asynchronously when running in AWS.
    PROCESSOR_FUNCTION_ARN is injected by SAM template.
    """
    import boto3
    processor_arn = os.environ.get("PROCESSOR_FUNCTION_ARN")
    if not processor_arn:
        return
    try:
        boto3.client("lambda", region_name=os.environ.get("AWS_REGION", "us-east-1")).invoke(
            FunctionName=processor_arn,
            InvocationType="Event",  # fire-and-forget, returns immediately
            Payload=json.dumps(payload).encode(),
        )
        print(f"[AgentRunner] Processor Lambda invoked for job {payload.get('job_id')}")
    except Exception as e:
        print(f"[AgentRunner] Failed to invoke processor Lambda: {e}")


def start_test_run(project_id: str, invoice_number: str | None = None) -> str:
    """
    Create a job document in MongoDB, dispatch the AI pipeline,
    and return the job_id immediately so the caller can poll for status.

    In AWS Lambda  → invokes PandoProcessorFunction asynchronously via boto3.
    Locally        → spawns a background daemon thread.
    """
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    now = datetime.now(tz=timezone.utc).isoformat()

    job = {
        "job_id":         job_id,
        "project_id":     project_id,
        "invoice_number": invoice_number,
        "status":         "running",
        "created_at":     now,
        "steps":          [{"name": name, "status": "pending"} for name in STEP_NAMES],
        "result_id":      None,
        "overall_score":  None,
        "test_status":    None,
        "error":          None,
    }
    save_job(job)

    if os.environ.get("PROCESSOR_FUNCTION_ARN"):
        # Running in AWS Lambda — use async Lambda invocation
        _invoke_processor_lambda({
            "mode":           "run_test",
            "job_id":         job_id,
            "project_id":     project_id,
            "invoice_number": invoice_number,
        })
    else:
        # Running locally — use background thread
        thread = threading.Thread(
            target=_run_background,
            args=(job_id, project_id, invoice_number),
            daemon=True,
        )
        thread.start()

    return job_id

"""
AWS Lambda entry points.

api_handler       — wraps FastAPI via Mangum for HTTP API Gateway (timeout: 30s)
processor_handler — runs the full AI pipeline asynchronously  (timeout: 900s)

Both functions share the same container image but use different handlers,
configured in template.yaml via ImageConfig.Command.
"""

import json
from datetime import datetime, timezone
from mangum import Mangum
from main import app

# ── HTTP handler — all /api/* routes ──────────────────────────────────────────
api_handler = Mangum(app, lifespan="off")


# ── Async processor handler ───────────────────────────────────────────────────

def processor_handler(event: dict, context) -> dict:
    """
    Long-running AI pipeline worker.
    Invoked with InvocationType='Event' (fire-and-forget) by api_handler.

    Event shapes:
      Run-test (pull mode):
        { "mode": "run_test", "job_id": "...", "project_id": "...", "invoice_number": "..." }

      Intake (push mode from Lambda):
        { "mode": "intake", "job_id": "...", "intake_data": { <IntakePayload dict> } }
    """
    from tools.dynamodb_tools import update_job

    mode   = event.get("mode")
    job_id = event.get("job_id", "unknown")
    now    = datetime.now(tz=timezone.utc).isoformat()

    print(f"[ProcessorHandler] Starting job={job_id} mode={mode}")

    try:
        if mode == "run_test":
            from agents.orchestrator import run_test
            result = run_test(
                event["project_id"],
                event.get("invoice_number"),
            )
            if "error" in result:
                update_job(job_id, {
                    "status": "failed",
                    "error": result["error"],
                    "completed_at": now,
                })
            else:
                update_job(job_id, {
                    "status":        "complete",
                    "result_id":     result.get("result_id"),
                    "overall_score": result.get("overall_score"),
                    "test_status":   result.get("status"),
                    "completed_at":  now,
                })

        elif mode == "intake":
            from agents.intake_processor import process_intake
            result = process_intake(event["intake_data"])
            if "error" in result:
                update_job(job_id, {
                    "status": "failed",
                    "error": result["error"],
                    "completed_at": now,
                })
            else:
                update_job(job_id, {
                    "status":        "complete",
                    "result_id":     result.get("result_id"),
                    "overall_score": result.get("overall_score"),
                    "test_status":   result.get("status"),
                    "project_id":    result.get("project_id"),
                    "completed_at":  now,
                })

        else:
            print(f"[ProcessorHandler] Unknown mode: {mode}")

    except Exception as exc:
        print(f"[ProcessorHandler] Job {job_id} failed: {exc}")
        update_job(job_id, {
            "status":       "failed",
            "error":        str(exc),
            "completed_at": now,
        })

    return {"statusCode": 200}

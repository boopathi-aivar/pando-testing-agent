"""
Shape of the JSON body that the Invoice Processor Lambda POSTs to /api/intake.

Lambda must send ALL fields it has. project_id OR s3_bucket is required for
the backend to route the result to the correct project.
"""

from pydantic import BaseModel
from typing import Optional


class IntakePayload(BaseModel):
    # ── Project routing (at least one required) ───────────────────────────────
    project_id: Optional[str] = None   # exact match — fastest
    s3_bucket:  Optional[str] = None   # matched against project file_slots (exact then fuzzy)
    log_group:  Optional[str] = None   # fallback: matched against cloudwatch_log_group

    # ── Invoice data ──────────────────────────────────────────────────────────
    invoice_number: str                # e.g. "INV-2024-0091"
    payload: dict                      # full JSON object Lambda extracted from the invoice

    # ── LLM context (collected from CloudWatch / Lambda internals) ────────────
    llm_response: Optional[dict]  = None   # raw JSON the LLM returned
    prompt:       Optional[str]   = None   # prompt text that was sent to the LLM

    # ── Execution metadata ────────────────────────────────────────────────────
    execution_duration_ms: int           = 0
    cold_start:            bool          = False
    errors:                list          = []
    warnings:              list          = []
    api_status:            Optional[int] = None   # HTTP status returned by the Lambda API call

"""
Pydantic models for the Retrigger (re-ingestion) feature.

A RetriggerProject names the S3 bucket + DynamoDB table pair that a
customer's invoice-processing pipeline uses. It does NOT store AWS
credentials — cross-account access reuses the same Secrets Manager-backed
session already configured for S3/CloudWatch access (see config.py's
make_source_aws_session()).

A RetriggerJob tracks one re-ingestion pipeline run for a batch of invoice
numbers against a given project.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Retrigger Project ─────────────────────────────────────────────────────────

class RetriggerProjectCreate(BaseModel):
    project_name: str
    s3_bucket: str
    dynamodb_table: str
    aws_region: str = "us-east-1"
    batch_size: int = 10
    batch_sleep_secs: int = 45


class RetriggerProjectUpdate(BaseModel):
    project_name: Optional[str] = None
    s3_bucket: Optional[str] = None
    dynamodb_table: Optional[str] = None
    aws_region: Optional[str] = None
    batch_size: Optional[int] = None
    batch_sleep_secs: Optional[int] = None


class RetriggerProject(BaseModel):
    project_id: str
    project_name: str
    s3_bucket: str
    dynamodb_table: str
    aws_region: str = "us-east-1"
    batch_size: int = 10
    batch_sleep_secs: int = 45
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ── Retrigger Job ──────────────────────────────────────────────────────────────

class RetriggerJobCreate(BaseModel):
    project_id: str
    invoice_numbers: list[str]
    folder_name: str  # NEW: folder name for organizing PK records


STEP_NAMES = ["fetch_pks", "delete_records", "reingest_files"]


def default_steps() -> dict[str, Any]:
    return {name: {"status": "pending"} for name in STEP_NAMES}


class LogEntry(BaseModel):
    ts: str
    level: str = "info"
    msg: str


class RetriggerJob(BaseModel):
    job_id: str
    project_id: str
    project_name: str
    folder_name: str  # NEW: folder name for organizing this batch
    invoice_numbers: list[str]
    status: str = "pending"  # pending | running | paused | completed | failed
    steps: dict[str, Any] = Field(default_factory=default_steps)
    logs: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())
    error: Optional[str] = None
    summary: Optional[dict[str, Any]] = None
    records: Optional[list[dict[str, Any]]] = None  # populated by fetch_records step
    # NEW: Store PK records within the job
    pk_records: list[dict[str, Any]] = Field(default_factory=list)
    # Format: [{"invoice_number": "xxx", "pk": "EMAIL#xxx", "message_id": "xxx", "sk": "METADATA", "status": "pending"}]
    # NEW: Pause/Resume support
    paused_at_step: Optional[str] = None
    progress: Optional[dict[str, Any]] = None  # {completed_pks: [...], completed_message_ids: [...]}

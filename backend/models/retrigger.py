"""
Pydantic models for the Bulk Ops feature (formerly Retrigger-only).

A project names the S3 bucket + DynamoDB table pair that a customer's
invoice-processing pipeline uses. Optional fields support bulk fetch
actions (payload / PDF / CloudWatch-or-Batch logs).

Credentials are NOT stored — cross-account access reuses Secrets Manager
sessions (see config.make_source_aws_session()).
"""

from typing import Any, Optional

from pydantic import BaseModel, Field


BULK_ACTIONS = ("retrigger", "fetch_payload", "fetch_pdf", "fetch_logs")


class RetriggerProjectCreate(BaseModel):
    project_name: str
    s3_bucket: str
    dynamodb_table: str
    aws_region: str = "us-east-1"
    batch_size: int = 10
    batch_sleep_secs: int = 45
    destination_bucket: str = ""
    cloudwatch_log_group: str = ""
    payload_filename: str = "api_payload.json"
    log_lookback_seconds: int = 604800


class RetriggerProjectUpdate(BaseModel):
    project_name: Optional[str] = None
    s3_bucket: Optional[str] = None
    dynamodb_table: Optional[str] = None
    aws_region: Optional[str] = None
    batch_size: Optional[int] = None
    batch_sleep_secs: Optional[int] = None
    destination_bucket: Optional[str] = None
    cloudwatch_log_group: Optional[str] = None
    payload_filename: Optional[str] = None
    log_lookback_seconds: Optional[int] = None


class RetriggerProject(BaseModel):
    project_id: str
    project_name: str
    s3_bucket: str
    dynamodb_table: str
    aws_region: str = "us-east-1"
    batch_size: int = 10
    batch_sleep_secs: int = 45
    destination_bucket: str = ""
    cloudwatch_log_group: str = ""
    payload_filename: str = "api_payload.json"
    log_lookback_seconds: int = 604800
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class RetriggerJobCreate(BaseModel):
    project_id: str
    invoice_numbers: list[str]
    folder_name: str
    action: str = "retrigger"


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
    folder_name: str
    action: str = "retrigger"
    invoice_numbers: list[str]
    status: str = "pending"
    steps: dict[str, Any] = Field(default_factory=default_steps)
    logs: list[dict[str, Any]] = Field(default_factory=list)
    pk_records: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    progress: dict[str, Any] = Field(default_factory=dict)
    paused_at_step: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    error: Optional[str] = None
    summary: Optional[dict[str, Any]] = None
    output_dir: Optional[str] = None

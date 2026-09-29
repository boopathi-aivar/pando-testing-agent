"""
DynamoDB helpers for the Retrigger (re-ingestion) feature.

Mirrors the style of tools/dynamodb_tools.py — plain functions used directly
by the router and pipeline service (no Strands @tool decorations needed here,
since this feature isn't driven by an LLM agent).
"""

import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from database import tbl_retrigger_projects, tbl_retrigger_jobs
from tools.dynamodb_tools import _to_dynamo, _from_dynamo

_JOB_TTL_SECONDS = 30 * 24 * 3600  # keep job history for 30 days


# ── Projects ───────────────────────────────────────────────────────────────────

def list_retrigger_projects() -> list[dict]:
    resp = tbl_retrigger_projects().scan()
    items = [_from_dynamo(i) for i in resp.get("Items", [])]
    items.sort(key=lambda d: (d.get("created_at") or ""), reverse=True)
    return items


def get_retrigger_project(project_id: str) -> dict | None:
    resp = tbl_retrigger_projects().get_item(Key={"project_id": project_id})
    item = resp.get("Item")
    return _from_dynamo(item) if item else None


def create_retrigger_project(data: dict) -> dict:
    now = datetime.now(tz=timezone.utc).isoformat()
    project = {
        "project_id": uuid.uuid4().hex,
        "created_at": now,
        "updated_at": now,
        **data,
    }
    tbl_retrigger_projects().put_item(Item=_to_dynamo(project))
    return project


def update_retrigger_project(project_id: str, updates: dict) -> dict | None:
    existing = get_retrigger_project(project_id)
    if not existing:
        return None
    merged = {**existing, **{k: v for k, v in updates.items() if v is not None}}
    merged["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
    tbl_retrigger_projects().put_item(Item=_to_dynamo(merged))
    return merged


def delete_retrigger_project(project_id: str) -> bool:
    resp = tbl_retrigger_projects().delete_item(
        Key={"project_id": project_id},
        ReturnValues="ALL_OLD",
    )
    return bool(resp.get("Attributes"))


# ── Jobs ───────────────────────────────────────────────────────────────────────

def list_retrigger_jobs(project_id: str | None = None, limit: int = 50) -> list[dict]:
    resp = tbl_retrigger_jobs().scan()
    items = [_from_dynamo(i) for i in resp.get("Items", [])]
    if project_id:
        items = [i for i in items if i.get("project_id") == project_id]
    items.sort(key=lambda d: (d.get("created_at") or ""), reverse=True)
    return items[:limit]


def get_retrigger_job(job_id: str) -> dict | None:
    resp = tbl_retrigger_jobs().get_item(Key={"job_id": job_id})
    item = resp.get("Item")
    return _from_dynamo(item) if item else None


def save_retrigger_job(job: dict) -> str:
    item = dict(job)
    item["ttl"] = int(time.time()) + _JOB_TTL_SECONDS
    tbl_retrigger_jobs().put_item(Item=_to_dynamo(item))
    return job["job_id"]


def update_retrigger_job(job_id: str, updates: dict) -> None:
    """Partially update a retrigger job document (top-level attributes only)."""
    if not updates:
        return
    safe = _to_dynamo(updates)
    set_expr = ", ".join(f"#f{i} = :v{i}" for i, _ in enumerate(safe))
    names  = {f"#f{i}": k for i, k in enumerate(safe)}
    values = {f":v{i}": v for i, (_, v) in enumerate(safe.items())}
    tbl_retrigger_jobs().update_item(
        Key={"job_id": job_id},
        UpdateExpression=f"SET {set_expr}",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def append_job_log(job_id: str, level: str, msg: str) -> None:
    job = get_retrigger_job(job_id)
    if not job:
        return
    logs = list(job.get("logs") or [])
    logs.append({"ts": datetime.now(tz=timezone.utc).isoformat(), "level": level, "msg": msg})
    update_retrigger_job(job_id, {
        "logs": logs,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    })


def update_job_step(job_id: str, step: str, status: str, data: dict | None = None) -> None:
    job = get_retrigger_job(job_id)
    if not job:
        return
    steps = dict(job.get("steps") or {})
    steps[step] = {
        "status": status,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        **(data or {}),
    }
    update_retrigger_job(job_id, {
        "steps": steps,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    })


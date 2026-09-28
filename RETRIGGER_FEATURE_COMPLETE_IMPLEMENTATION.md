# Retrigger Feature - Complete Implementation Guide

This document provides a complete specification for implementing the **Retrigger (Re-ingestion)** feature in the Invoice Testing Agent application. This feature allows users to re-run invoice ingestion pipelines for specific invoice numbers.

## Table of Contents
1. [Feature Overview](#feature-overview)
2. [Backend Implementation](#backend-implementation)
3. [Frontend Implementation](#frontend-implementation)
4. [Integration Points](#integration-points)
5. [Testing Guide](#testing-guide)

---

## Feature Overview

### Purpose
The Retrigger feature recreates S3 events that originally triggered invoice ingestion, allowing users to:
- Re-process specific invoices after fixing processing issues
- Update invoice data when business rules change
- Clean and re-ingest data without manual file uploads

### Architecture
The feature follows a 3-step pipeline:
1. **Fetch PKs** - Scan DynamoDB to find partition keys for invoice numbers
2. **Delete Records** - Remove stale DynamoDB records
3. **Re-ingest Files** - S3 CopyObject triggers new processing events
4. **Fetch Records** (optional) - Retrieve freshly processed data

### Key Components
- **Retrigger Projects**: Configuration for S3 bucket + DynamoDB table pairs
- **Retrigger Jobs**: Tracked pipeline executions with logs and progress
- **Async Processing**: Jobs run in Lambda with DynamoDB-based status tracking

---

## Backend Implementation

### 1. Database Schema (`backend/database.py`)

Add table definitions and helper functions:

```python
# Add after existing table definitions (around line 23-24)
_RETRIGGER_PROJECTS_TABLE = os.getenv("RETRIGGER_PROJECTS_TABLE", "pando-retrigger-projects")
_RETRIGGER_JOBS_TABLE     = os.getenv("RETRIGGER_JOBS_TABLE",     "pando-retrigger-jobs")

# Add table accessor functions (around line 52-57)
def tbl_retrigger_projects():
    return _get_resource().Table(_RETRIGGER_PROJECTS_TABLE)


def tbl_retrigger_jobs():
    return _get_resource().Table(_RETRIGGER_JOBS_TABLE)

# Add to check_connection() print statements (around line 68-69)
    print(f"  Retrig projects : {_RETRIGGER_PROJECTS_TABLE}")
    print(f"  Retrig jobs     : {_RETRIGGER_JOBS_TABLE}")

# Add table creation in ensure_tables() (around line 164-194)
    # ── pando-retrigger-projects ──────────────────────────────────────────────
    if _RETRIGGER_PROJECTS_TABLE not in existing:
        client.create_table(
            TableName=_RETRIGGER_PROJECTS_TABLE,
            KeySchema=[{"AttributeName": "project_id", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "project_id", "AttributeType": "S"},
            ],
        )
        print(f"[DynamoDB] Created table: {_RETRIGGER_PROJECTS_TABLE}")

    # ── pando-retrigger-jobs ───────────────────────────────────────────────────
    if _RETRIGGER_JOBS_TABLE not in existing:
        client.create_table(
            TableName=_RETRIGGER_JOBS_TABLE,
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "job_id", "AttributeType": "S"},
            ],
        )
        client.update_time_to_live(
            TableName=_RETRIGGER_JOBS_TABLE,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
        )
        print(f"[DynamoDB] Created table: {_RETRIGGER_JOBS_TABLE} (TTL on 'ttl' attribute)")
```

### 2. Pydantic Models (`backend/models/retrigger.py`)

Create new file with complete content:

```python
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
    invoice_numbers: list[str]
    status: str = "pending"  # pending | running | completed | failed
    steps: dict[str, Any] = Field(default_factory=default_steps)
    logs: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())
    error: Optional[str] = None
    summary: Optional[dict[str, Any]] = None
    records: Optional[list[dict[str, Any]]] = None  # populated by fetch_records step
```

### 3. DynamoDB Tools (`backend/tools/retrigger_tools.py`)

Create new file with complete content:

```python
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
```

### 4. Pipeline Service (`backend/services/retrigger_pipeline.py`)

Create new file - this is the core business logic (400+ lines). Key functions:
- `run_retrigger_pipeline()` - Main pipeline orchestrator
- `run_fetch_records()` - On-demand record fetcher
- `_step_fetch_pks()` - Scans DynamoDB for invoice PKs
- `_step_delete_records()` - Deletes stale records
- `_step_reingest_files()` - Triggers S3 events via CopyObject

**Full file content**: [See the complete file in the codebase - 420 lines]

Key implementation notes:
- Uses `make_source_aws_session()` from `config.py` for cross-account access
- Batches S3 operations with configurable sleep intervals
- Updates DynamoDB job status after each step
- Handles errors gracefully with detailed logging

### 5. FastAPI Router (`backend/routers/retrigger.py`)

Create new file with complete content:

```python
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
from models.retrigger import (
    RetriggerProjectCreate,
    RetriggerProjectUpdate,
    default_steps,
)
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
)

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


def _invoke_processor(payload: dict) -> None:
    """Fire-and-forget invoke of ProcessorFunction — mirrors agent_runner.py."""
    processor_arn = os.environ.get("PROCESSOR_FUNCTION_ARN")
    if not processor_arn:
        # Local dev — run inline in a background thread instead of blocking the request
        import threading
        from services.retrigger_pipeline import run_retrigger_pipeline, run_fetch_records
        mode = payload.get("mode")
        if mode == "retrigger":
            target = run_retrigger_pipeline
            args = (payload["job_id"], payload["invoice_numbers"], payload["cfg"])
        elif mode == "retrigger_fetch_records":
            target = run_fetch_records
            args = (payload["job_id"], payload["invoice_numbers"], payload["cfg"])
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
        print(f"[Retrigger] Failed to invoke processor Lambda: {exc}")


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

    invoices = list(dict.fromkeys(i.strip() for i in body.invoice_numbers if i.strip()))
    if not invoices:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="At least one invoice number is required.")

    now = datetime.now(tz=timezone.utc).isoformat()
    job_id = f"retrig-{uuid.uuid4().hex[:10]}"
    job = {
        "job_id": job_id,
        "project_id": project["project_id"],
        "project_name": project["project_name"],
        "invoice_numbers": invoices,
        "status": "pending",
        "steps": default_steps(),
        "logs": [],
        "created_at": now,
        "updated_at": now,
        "error": None,
        "summary": None,
    }
    save_retrigger_job(job)

    cfg = {
        "s3_bucket": project["s3_bucket"],
        "dynamodb_table": project["dynamodb_table"],
        "aws_region": project.get("aws_region") or "us-east-1",
        "batch_size": project.get("batch_size", 10),
        "batch_sleep_secs": project.get("batch_sleep_secs", 45),
    }
    _invoke_processor({"mode": "retrigger", "job_id": job_id, "invoice_numbers": invoices, "cfg": cfg})

    return job


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

    cfg = {
        "s3_bucket": project["s3_bucket"],
        "dynamodb_table": project["dynamodb_table"],
        "aws_region": project.get("aws_region") or "us-east-1",
        "batch_size": project.get("batch_size", 10),
        "batch_sleep_secs": project.get("batch_sleep_secs", 45),
    }
    _invoke_processor({
        "mode": "retrigger_fetch_records",
        "job_id": job_id,
        "invoice_numbers": job["invoice_numbers"],
        "cfg": cfg,
    })

    return get_retrigger_job(job_id)
```

### 6. Lambda Handler Integration (`backend/lambda_handler.py`)

Add retrigger handling to the `processor_handler()` function:

```python
# Add to the docstring (around line 37-41)
      Retrigger (re-ingestion pipeline):
        { "mode": "retrigger", "job_id": "...", "invoice_numbers": [...], "cfg": {...} }

      Retrigger fetch-records (on-demand re-scan):
        { "mode": "retrigger_fetch_records", "job_id": "...", "invoice_numbers": [...], "cfg": {...} }

# Add after the existing mode handlers (around line 111-120)
        elif mode == "retrigger":
            # Own try/except + its own DynamoDB table (retrigger-jobs, not
            # jobs) — don't fall through to the generic update_job() below.
            from services.retrigger_pipeline import run_retrigger_pipeline
            run_retrigger_pipeline(job_id, event["invoice_numbers"], event["cfg"])

        elif mode == "retrigger_fetch_records":
            from services.retrigger_pipeline import run_fetch_records
            run_fetch_records(job_id, event["invoice_numbers"], event["cfg"])

# Update the exception handler (around line 126)
        if mode not in ("retrigger", "retrigger_fetch_records"):
            update_job(job_id, {
                "status":       "failed",
                # ... rest of error handling
```

### 7. Main App Integration (`backend/main.py`)

Add the retrigger router to the FastAPI app:

```python
# Add to imports (around line 9)
from routers import auth, projects, results, jobs, intake, dashboard, docprojects, retrigger

# Add router (around line 73)
app.include_router(retrigger.router, prefix="/api")
```

---

## Frontend Implementation

### 1. API Client (`frontend/src/api/client.js`)

Add retrigger API functions:

```javascript
// ─── Retrigger ─────────────────────────────────────────────────────────────────
export async function getRetriggerProjects() {
  return request('/retrigger/projects')
}

export async function getRetriggerProject(id) {
  return request(`/retrigger/projects/${id}`)
}

export async function createRetriggerProject(data) {
  return request('/retrigger/projects', { method: 'POST', body: JSON.stringify(data) })
}

export async function updateRetriggerProject(id, data) {
  return request(`/retrigger/projects/${id}`, { method: 'PUT', body: JSON.stringify(data) })
}

export async function deleteRetriggerProject(id) {
  const token = getToken()
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${API_BASE}/api/retrigger/projects/${id}`, { method: 'DELETE', headers })
  if (res.status === 401) { clearAuth(); window.location.href = '/login'; throw new Error('Session expired') }
  if (!res.ok) { const b = await res.json().catch(() => ({})); throw new Error(b.detail ?? `Delete failed: ${res.status}`) }
}

export async function getRetriggerJobs(projectId) {
  const q = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
  return request(`/retrigger/jobs${q}`)
}

export async function getRetriggerJob(jobId) {
  return request(`/retrigger/jobs/${jobId}`)
}

export async function createRetriggerJob(projectId, invoiceNumbers) {
  return request('/retrigger/jobs', {
    method: 'POST',
    body: JSON.stringify({ project_id: projectId, invoice_numbers: invoiceNumbers }),
  })
}

export async function triggerRetriggerFetchRecords(jobId) {
  return request(`/retrigger/jobs/${jobId}/fetch-records`, { method: 'POST' })
}
```

### 2. Retrigger Main Page (`frontend/src/pages/Retrigger.jsx`)

**Full file content**: [See complete 200+ line component in codebase]

Key features:
- Project selector with inline project management
- Multi-line invoice number textarea
- Recent jobs list with live status badges
- Form validation and error handling
- Navigation to job detail page

### 3. Job Detail Page (`frontend/src/pages/RetriggerJobDetail.jsx`)

**Full file content**: [See complete 250+ line component in codebase]

Key features:
- Real-time job status polling (2s intervals)
- Step-by-step progress tracker
- Live log streaming with auto-scroll
- Summary cards with metrics
- Fetch records button for completed jobs
- JSON record viewer toggle

### 4. Components

#### `frontend/src/components/retrigger/StatusBadge.jsx`

```javascript
const CONFIG = {
  pending:   { label: 'Pending',   cls: 'bg-background text-text-muted border-border',                 dot: 'bg-text-muted' },
  running:   { label: 'Running',   cls: 'bg-aivar-purple-50 text-[#6C5CE7] border-aivar-purple-200',    dot: 'bg-[#6C5CE7]' },
  completed: { label: 'Completed', cls: 'bg-pando-green-50 text-pando-green-600 border-pando-green-200', dot: 'bg-pando-green-600' },
  failed:    { label: 'Failed',    cls: 'bg-danger-bg text-danger border-danger/20',                    dot: 'bg-danger' },
}

export default function StatusBadge({ status, size = 'md' }) {
  const { label, cls, dot } = CONFIG[status] ?? CONFIG.pending
  const isRunning = status === 'running'
  const padding = size === 'sm' ? 'px-2 py-0.5 text-[11px]' : 'px-2.5 py-1 text-xs'
  const dotSize = size === 'sm' ? 'w-1.5 h-1.5' : 'w-2 h-2'

  return (
    <span className={`inline-flex items-center gap-1.5 font-semibold rounded-full border ${cls} ${padding}`}>
      <span className={`inline-block rounded-full flex-shrink-0 ${dot} ${dotSize} ${isRunning ? 'animate-pulse' : ''}`} />
      {label}
    </span>
  )
}
```

#### `frontend/src/components/retrigger/StepTracker.jsx`

**Full file content**: [See complete component with icons and progress visualization]

Features:
- Visual progress with icons (pending, running, completed, failed)
- Vertical connector lines between steps
- Step metadata display (counts, errors)
- Color-coded status indicators

#### `frontend/src/components/retrigger/RetriggerProjectModal.jsx`

**Full file content**: [See complete modal form component]

Features:
- Create/edit project modal
- Form validation
- AWS region selector
- Batch size and sleep configuration
- Error display

### 5. Routing (`frontend/src/App.jsx`)

Add routes and imports:

```javascript
// Add imports
import Retrigger from './pages/Retrigger'
import RetriggerJobDetail from './pages/RetriggerJobDetail'

// Add routes (in the Routes component)
<Route path="/retrigger" element={<PrivateRoute><Layout title="Retrigger"><Retrigger /></Layout></PrivateRoute>} />
<Route path="/retrigger/jobs/:jobId" element={<PrivateRoute><Layout title="Retrigger Job"><RetriggerJobDetail /></Layout></PrivateRoute>} />
```

### 6. Navigation (`frontend/src/components/layout/Sidebar.jsx`)

Add Retrigger to navigation:

```javascript
// Add import
import { RefreshCw } from 'lucide-react'

// Add to navItems array
const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard', end: true },
  { to: '/projects', icon: FolderOpen, label: 'Projects' },
  // ... other items
  { to: '/retrigger', icon: RefreshCw, label: 'Retrigger' },
  { to: '/documentation', icon: BookOpen, label: 'Documentation' },
  { to: '/settings', icon: Settings, label: 'Settings' },
]
```

---

## Integration Points

### 1. Environment Variables

Add to `backend/.env`:
```bash
RETRIGGER_PROJECTS_TABLE=pando-retrigger-projects
RETRIGGER_JOBS_TABLE=pando-retrigger-jobs

# Source account credentials for cross-account S3/DynamoDB access
SOURCE_ACCOUNT_SECRET_NAME=invoice-testing-agent/source-account-credentials
SOURCE_ACCOUNT_REGION=us-east-1

# Or use direct credentials (local dev only)
SOURCE_AWS_ACCESS_KEY_ID=
SOURCE_AWS_SECRET_ACCESS_KEY=
SOURCE_AWS_SESSION_TOKEN=
```

### 2. AWS Permissions

The Lambda execution role needs:
- `dynamodb:PutItem`, `dynamodb:GetItem`, `dynamodb:Scan`, `dynamodb:Query`, `dynamodb:DeleteItem` on retrigger tables
- `s3:GetObject`, `s3:CopyObject` on source buckets
- `dynamodb:*` on customer DynamoDB tables (cross-account)
- `secretsmanager:GetSecretValue` for cross-account credentials

### 3. SAM Template Updates (`template.yaml`)

Add environment variables to Lambda functions:
```yaml
Environment:
  Variables:
    RETRIGGER_PROJECTS_TABLE: pando-retrigger-projects
    RETRIGGER_JOBS_TABLE: pando-retrigger-jobs
    SOURCE_ACCOUNT_SECRET_NAME: invoice-testing-agent/source-account-credentials
    SOURCE_ACCOUNT_REGION: us-east-1
```

Add DynamoDB table resources if needed.

---

## Testing Guide

### Backend Testing

1. **Start backend locally**:
```bash
cd backend
source .venv/bin/activate
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

2. **Test endpoints** (use Swagger UI at `http://localhost:8000/docs`):
   - POST `/api/retrigger/projects` - Create a project
   - GET `/api/retrigger/projects` - List projects
   - POST `/api/retrigger/jobs` - Start a retrigger job
   - GET `/api/retrigger/jobs/{job_id}` - Monitor job progress
   - POST `/api/retrigger/jobs/{job_id}/fetch-records` - Fetch records

### Frontend Testing

1. **Start frontend**:
```bash
cd frontend
npm run dev
```

2. **Test UI flows**:
   - Navigate to `/retrigger`
   - Create a new project
   - Submit invoice numbers
   - Watch job progress in real-time
   - Click "Fetch Records" after completion
   - View JSON output

### Integration Testing

1. **End-to-end flow**:
   - Create retrigger project pointing to real S3/DynamoDB
   - Submit known invoice numbers
   - Verify DynamoDB records are deleted
   - Verify S3 CopyObject events are triggered
   - Confirm Lambda reprocessing occurs
   - Fetch and verify updated records

### Error Scenarios

Test these edge cases:
- Invalid invoice numbers (not found)
- Network failures during pipeline
- Cross-account permission errors
- DynamoDB throttling
- S3 access errors

---

## Implementation Checklist

### Backend
- [ ] Update `database.py` with table definitions
- [ ] Create `models/retrigger.py`
- [ ] Create `tools/retrigger_tools.py`
- [ ] Create `services/retrigger_pipeline.py`
- [ ] Create `routers/retrigger.py`
- [ ] Update `lambda_handler.py` with retrigger modes
- [ ] Update `main.py` to include retrigger router
- [ ] Add environment variables to `.env`
- [ ] Test all endpoints

### Frontend
- [ ] Update `api/client.js` with retrigger functions
- [ ] Create `pages/Retrigger.jsx`
- [ ] Create `pages/RetriggerJobDetail.jsx`
- [ ] Create `components/retrigger/StatusBadge.jsx`
- [ ] Create `components/retrigger/StepTracker.jsx`
- [ ] Create `components/retrigger/RetriggerProjectModal.jsx`
- [ ] Update `App.jsx` with routes
- [ ] Update `Sidebar.jsx` with navigation
- [ ] Test UI flows

### Infrastructure
- [ ] Configure AWS credentials/secrets
- [ ] Set up cross-account permissions
- [ ] Update SAM template
- [ ] Test Lambda invocations
- [ ] Verify DynamoDB tables are created

---

## Notes

- **Cross-account access**: The feature reuses the existing `make_source_aws_session()` pattern - no new credential management needed
- **Local dev**: Jobs run in background threads instead of Lambda invokes
- **Polling**: Frontend polls every 2 seconds while jobs are active
- **TTL**: Job records auto-expire after 30 days
- **Batch processing**: Configurable batch size and sleep to avoid rate limits

---

## Support

For issues or questions:
1. Check CloudWatch logs for backend errors
2. Check browser console for frontend errors
3. Verify AWS permissions and credentials
4. Confirm DynamoDB tables exist and are accessible

---

**Version**: 1.0
**Last Updated**: 2026-09-22
**Author**: Kiro AI Agent

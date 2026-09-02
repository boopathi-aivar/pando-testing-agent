"""
DynamoDB tool functions for Strands agents and FastAPI routers.
@tool-decorated functions are callable by Strands agents.
Plain functions are used directly by routers and the orchestrator.
"""

import json
import time
from datetime import datetime, timezone
from decimal import Decimal

from boto3.dynamodb.conditions import Key, Attr
from strands import tool
from database import tbl_projects, tbl_results, tbl_jobs


# ── DynamoDB ↔ JSON serialization ────────────────────────────────────────────

def _to_dynamo(obj):
    """Recursively convert Python types to DynamoDB-safe types (Decimal for floats)."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_dynamo(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_dynamo(i) for i in obj]
    return obj


def _from_dynamo(obj):
    """Recursively convert DynamoDB Decimal back to float/int."""
    if isinstance(obj, Decimal):
        f = float(obj)
        return int(f) if f == int(f) else f
    if isinstance(obj, dict):
        return {k: _from_dynamo(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_from_dynamo(i) for i in obj]
    return obj


# ── Strands @tools (called by agents) ────────────────────────────────────────

@tool
def get_project_config(project_id: str) -> str:
    """
    Fetch project configuration from DynamoDB.
    Returns the project document as a JSON string, or an error JSON if not found.
    project_id: unique identifier for the project (e.g. 'ge-freight')
    """
    resp = tbl_projects().get_item(Key={"project_id": project_id})
    item = resp.get("Item")
    if not item:
        return json.dumps({"error": f"Project '{project_id}' not found"})
    return json.dumps(_from_dynamo(item))


@tool
def save_test_result(project_id: str, result_json: str) -> str:
    """
    Persist a completed TestResult document to the pando-results DynamoDB table.
    project_id: the project this result belongs to
    result_json: complete TestResult serialised as a JSON string
    Returns the result_id of the saved document.
    """
    result = json.loads(result_json)
    result["created_at"] = datetime.now(tz=timezone.utc).isoformat()
    if not result.get("timestamp"):
        result["timestamp"] = result["created_at"]
    tbl_results().put_item(Item=_to_dynamo(result))
    return result.get("result_id", "unknown")


@tool
def update_project_last_tested(project_id: str, score: float, status: str) -> str:
    """
    Update the project document with the latest test score and timestamp.
    Called by the orchestrator after every completed test run.
    project_id: unique project identifier
    score: overall score as a float 0-100
    status: 'passed' | 'warning' | 'failed'
    """
    tbl_projects().update_item(
        Key={"project_id": project_id},
        UpdateExpression="SET last_tested = :ts, last_score = :sc, #st = :sv",
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={
            ":ts": datetime.now(tz=timezone.utc).isoformat(),
            ":sc": Decimal(str(score)),
            ":sv": "configured",
        },
    )
    return "updated"


# ── Direct helpers (used by routers and orchestrator) ─────────────────────────

def save_project(project_data: dict) -> str:
    """Upsert a project config into DynamoDB. Returns project_id."""
    tbl_projects().put_item(Item=_to_dynamo(project_data))
    return project_data["project_id"]


def get_results_for_project(
    project_id: str,
    invoice_filter: str = None,
    status_filter: str = None,
    carrier_filter: str = None,
    limit: int = 50,
) -> list:
    """Query test results for a project with optional filters, sorted by timestamp desc."""
    resp = tbl_results().query(
        IndexName="project_id-index",
        KeyConditionExpression=Key("project_id").eq(project_id),
        ScanIndexForward=False,  # descending by timestamp
        Limit=200,               # fetch more then filter in Python
    )
    items = [_from_dynamo(i) for i in resp.get("Items", [])]

    if invoice_filter:
        inv_lower = invoice_filter.lower()
        items = [i for i in items if inv_lower in (i.get("invoice_number") or "").lower()]
    if status_filter and status_filter != "all":
        items = [i for i in items if i.get("status") == status_filter]
    if carrier_filter and carrier_filter != "all":
        items = [i for i in items if i.get("vendor_name") == carrier_filter]

    return items[:limit]


def get_result_by_id(result_id: str) -> dict:
    """Fetch a single test result by result_id."""
    resp = tbl_results().get_item(Key={"result_id": result_id})
    item = resp.get("Item")
    return _from_dynamo(item) if item else {}


def save_job(job: dict) -> str:
    """Upsert a job document into DynamoDB. Returns job_id. Sets 1-hour TTL."""
    item = dict(job)
    item["ttl"] = int(time.time()) + 3600  # expire after 1 hour
    tbl_jobs().put_item(Item=_to_dynamo(item))
    return job["job_id"]


def get_job(job_id: str) -> dict:
    """Fetch a job document by job_id."""
    resp = tbl_jobs().get_item(Key={"job_id": job_id})
    item = resp.get("Item")
    return _from_dynamo(item) if item else {}


def update_job(job_id: str, updates: dict) -> None:
    """Partially update a job document."""
    if not updates:
        return
    safe = _to_dynamo(updates)
    set_expr = ", ".join(f"#f{i} = :v{i}" for i, k in enumerate(safe))
    names  = {f"#f{i}": k for i, k in enumerate(safe)}
    values = {f":v{i}": v for i, (k, v) in enumerate(safe.items())}
    tbl_jobs().update_item(
        Key={"job_id": job_id},
        UpdateExpression=f"SET {set_expr}",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )

"""
MongoDB tool functions for Strands agents and FastAPI routers.
@tool-decorated functions are callable by Strands agents.
Plain functions are used directly by routers and the orchestrator.
"""

import json
from datetime import datetime, timezone
from bson import ObjectId
from strands import tool
from database import col_projects, col_results, col_jobs


# ── Serialization ─────────────────────────────────────────────────────────────

def _serialize(doc: dict) -> dict:
    """Convert ObjectId / datetime fields to JSON-safe types."""
    if not doc:
        return {}
    out = {}
    for k, v in doc.items():
        if k == "_id":
            out["_id"] = str(v)
        elif isinstance(v, (ObjectId,)):
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, dict):
            out[k] = _serialize(v)
        elif isinstance(v, list):
            out[k] = [_serialize(i) if isinstance(i, dict) else i for i in v]
        else:
            out[k] = v
    return out


# ── Strands @tools (called by agents) ────────────────────────────────────────

@tool
def get_project_config(project_id: str) -> str:
    """
    Fetch project configuration from MongoDB.
    Returns the project document as a JSON string, or an error JSON if not found.
    project_id: unique identifier for the project (e.g. 'ge-freight')
    """
    doc = col_projects().find_one({"project_id": project_id}, {"_id": 0})
    if not doc:
        return json.dumps({"error": f"Project '{project_id}' not found"})
    return json.dumps(_serialize(doc))


@tool
def save_test_result(project_id: str, result_json: str) -> str:
    """
    Persist a completed TestResult document to the test_results MongoDB collection.
    project_id: the project this result belongs to
    result_json: complete TestResult serialised as a JSON string
    Returns the result_id of the saved document.
    """
    result = json.loads(result_json)
    result["created_at"] = datetime.now(tz=timezone.utc)
    if isinstance(result.get("timestamp"), str):
        try:
            result["timestamp"] = datetime.fromisoformat(result["timestamp"])
        except Exception:
            pass
    col_results().replace_one(
        {"result_id": result["result_id"]},
        result,
        upsert=True,
    )
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
    col_projects().update_one(
        {"project_id": project_id},
        {"$set": {
            "last_tested": datetime.now(tz=timezone.utc).isoformat(),
            "last_score": score,
            "status": "configured",
        }},
    )
    return "updated"


# ── Direct helpers (used by routers and orchestrator) ─────────────────────────

def save_project(project_data: dict) -> str:
    """Upsert a project config into MongoDB. Returns project_id."""
    col_projects().update_one(
        {"project_id": project_data["project_id"]},
        {"$set": project_data},
        upsert=True,
    )
    return project_data["project_id"]


def get_results_for_project(
    project_id: str,
    invoice_filter: str = None,
    status_filter: str = None,
    carrier_filter: str = None,
    limit: int = 50,
) -> list:
    """Query test results for a project with optional filters."""
    query: dict = {"project_id": project_id}
    if invoice_filter:
        query["invoice_number"] = {"$regex": invoice_filter, "$options": "i"}
    if status_filter and status_filter != "all":
        query["status"] = status_filter
    if carrier_filter and carrier_filter != "all":
        query["vendor_name"] = carrier_filter
    cursor = col_results().find(query, {"_id": 0}).sort("timestamp", -1).limit(limit)
    return [_serialize(doc) for doc in cursor]


def get_result_by_id(result_id: str) -> dict:
    """Fetch a single test result by result_id."""
    doc = col_results().find_one({"result_id": result_id}, {"_id": 0})
    return _serialize(doc) if doc else {}


def save_job(job: dict) -> str:
    """Upsert a job document into MongoDB. Returns job_id."""
    col_jobs().update_one(
        {"job_id": job["job_id"]},
        {"$set": job},
        upsert=True,
    )
    return job["job_id"]


def get_job(job_id: str) -> dict:
    """Fetch a job document by job_id."""
    doc = col_jobs().find_one({"job_id": job_id}, {"_id": 0})
    return _serialize(doc) if doc else {}


def update_job(job_id: str, updates: dict) -> None:
    """Partially update a job document."""
    col_jobs().update_one({"job_id": job_id}, {"$set": updates})

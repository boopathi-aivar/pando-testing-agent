from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

from database import tbl_projects
from tools.dynamodb_tools import get_job, _from_dynamo
from agent_runner import start_test_run
from routers.auth import get_current_user

router = APIRouter(tags=["jobs"])

STEP_NAMES = [
    "Collecting inputs from S3",
    "Querying CloudWatch logs",
    "Validating payload fields",
    "Computing scores and suggestions",
    "Saving result to database",
]


@router.post("/projects/{project_id}/run-test", status_code=status.HTTP_202_ACCEPTED)
def run_test(
    project_id: str,
    invoice_number: Optional[str] = None,
    _user=Depends(get_current_user),
):
    """Start a test run for the given project. Returns job_id to poll for status."""
    resp = tbl_projects().get_item(Key={"project_id": project_id})
    if not resp.get("Item"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Project '{project_id}' not found")

    job_id = start_test_run(project_id, invoice_number)
    return {"job_id": job_id}


@router.get("/jobs/{job_id}/status")
def job_status(job_id: str, _user=Depends(get_current_user)):
    """Poll a job for its current status and step progress."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job

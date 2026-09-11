from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional

from database import tbl_projects
from tools.dynamodb_tools import get_job, get_result_by_id
from agent_runner import start_test_run, start_retest
from routers.auth import get_current_user

router = APIRouter(tags=["jobs"])


class RunTestRequest(BaseModel):
    invoice_number: Optional[str] = None


@router.post("/projects/{project_id}/run-test", status_code=status.HTTP_202_ACCEPTED)
def run_test(
    project_id: str,
    body: RunTestRequest = RunTestRequest(),
    _user=Depends(get_current_user),
):
    """Start a test run for the given project. Returns job_id to poll for status."""
    resp = tbl_projects().get_item(Key={"project_id": project_id})
    if not resp.get("Item"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Project '{project_id}' not found")

    invoice_number = (body.invoice_number or "").strip() or None
    job_id = start_test_run(project_id, invoice_number)
    return {"job_id": job_id}


@router.post("/projects/{project_id}/results/{result_id}/retest", status_code=status.HTTP_202_ACCEPTED)
def retest_result(
    project_id: str,
    result_id: str,
    _user=Depends(get_current_user),
):
    """
    Re-score a stored invoice result from its raw_payload (and PDF if available).
    Use this for a specific invoice instead of a CloudWatch log pull.
    """
    stored = get_result_by_id(result_id)
    if not stored or stored.get("project_id") != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Result '{result_id}' not found")
    if not stored.get("raw_payload"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="This result has no stored payload to retest")
    job_id = start_retest(result_id, project_id=project_id)
    return {"job_id": job_id}


@router.get("/jobs/{job_id}/status")
def job_status(job_id: str, _user=Depends(get_current_user)):
    """Poll a job for its current status and step progress."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job

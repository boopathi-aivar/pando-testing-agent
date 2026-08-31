from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from models.result import TestResult
from tools.mongodb_tools import get_results_for_project, get_result_by_id
from database import col_results
from routers.auth import get_current_user

router = APIRouter(tags=["results"])


@router.get("/projects/{project_id}/results", response_model=list[TestResult])
def list_results(
    project_id: str,
    invoice: Optional[str] = Query(None, description="Filter by invoice number substring"),
    status: Optional[str]  = Query(None, description="passed | warning | failed"),
    carrier: Optional[str] = Query(None, description="Filter by vendor_name (carrier)"),
    _user=Depends(get_current_user),
):
    return get_results_for_project(
        project_id,
        invoice_filter=invoice,
        status_filter=status,
        carrier_filter=carrier,
    )


@router.get("/projects/{project_id}/carriers")
def list_carriers(project_id: str, _user=Depends(get_current_user)):
    """Return the distinct vendor_name values for a project's results."""
    pipeline = [
        {"$match": {"project_id": project_id, "vendor_name": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$vendor_name"}},
        {"$sort": {"_id": 1}},
    ]
    carriers = [doc["_id"] for doc in col_results().aggregate(pipeline)]
    return {"carriers": carriers}


@router.get("/results/{result_id}")
def get_result(result_id: str, _user=Depends(get_current_user)):
    """Return a single test result by result_id."""
    result = get_result_by_id(result_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Result '{result_id}' not found")
    return result

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from models.result import TestResult
from database import tbl_projects
from tools.dynamodb_tools import get_results_for_project, get_result_by_id, delete_result, _from_dynamo
from routers.auth import get_current_user
from services.field_compare import apply_comparison

router = APIRouter(tags=["results"])


def _mandatory_fields_for(project_id: str) -> list:
    if not project_id:
        return []
    resp = tbl_projects().get_item(Key={"project_id": project_id})
    item = resp.get("Item")
    if not item:
        return []
    return _from_dynamo(item).get("mandatory_fields") or []


def _for_display(item: dict, mandatory_fields: list | None = None) -> dict:
    """
    Re-classify stored rows so older results pick up date matching and
    stop treating 'no ground truth' as Correct. API-error rows are left as-is.
    Score uses required fields only.
    """
    api_status = item.get("api_status")
    try:
        if api_status is not None and int(api_status) >= 400:
            return item
    except (TypeError, ValueError):
        pass
    fields = mandatory_fields if mandatory_fields is not None else _mandatory_fields_for(item.get("project_id") or "")
    apply_comparison(
        item,
        payload=item.get("raw_payload") or {},
        mandatory_fields=fields,
    )
    return item


@router.get("/projects/{project_id}/results", response_model=list[TestResult])
def list_results(
    project_id: str,
    invoice: Optional[str] = Query(None, description="Filter by invoice number substring"),
    status: Optional[str]  = Query(None, description="passed | warning | failed | unscored"),
    carrier: Optional[str] = Query(None, description="Filter by vendor_name (carrier)"),
    _user=Depends(get_current_user),
):
    raw = get_results_for_project(
        project_id,
        invoice_filter=invoice,
        status_filter=None,
        carrier_filter=carrier,
    )
    mandatory = _mandatory_fields_for(project_id)
    # Skip documents that still fail validation so one bad DynamoDB item
    # cannot 500 the entire results page. Re-classify before status filter so
    # "unscored" (no PDF) is not stuck under stored "failed".
    safe: list[TestResult] = []
    for item in raw:
        try:
            displayed = _for_display(item, mandatory)
            if status and status != "all" and (displayed.get("status") or "") != status:
                continue
            safe.append(TestResult.model_validate(displayed))
        except Exception as exc:
            print(f"[results] Skipping invalid result {item.get('result_id')}: {exc}")
    return safe


@router.get("/projects/{project_id}/carriers")
def list_carriers(project_id: str, _user=Depends(get_current_user)):
    """Return the distinct vendor_name values for a project's results."""
    results = get_results_for_project(project_id, limit=500)
    seen = set()
    carriers = []
    for r in results:
        name = r.get("vendor_name")
        if name and name not in seen:
            seen.add(name)
            carriers.append(name)
    carriers.sort()
    return {"carriers": carriers}


@router.get("/results/{result_id}")
def get_result(result_id: str, _user=Depends(get_current_user)):
    """Return a single test result by result_id."""
    result = get_result_by_id(result_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Result '{result_id}' not found")
    return _for_display(result)


@router.delete("/projects/{project_id}/results/{result_id}")
def remove_result(project_id: str, result_id: str, _user=Depends(get_current_user)):
    """Delete a single stored invoice result for this project."""
    stored = get_result_by_id(result_id)
    if not stored or stored.get("project_id") != project_id:
        raise HTTPException(status_code=404, detail=f"Result '{result_id}' not found")
    delete_result(result_id)
    return {"ok": True, "result_id": result_id}

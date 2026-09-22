"""Observability API — /api/observability/{project_id}/..."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from observability.registry import list_projects
from routers.auth import get_current_user
from services import observability_logs

router = APIRouter(tags=["observability"])


@router.get("/projects")
def get_observability_projects(_user=Depends(get_current_user)):
    return {"projects": list_projects()}


@router.get("/{project_id}/invoices")
def get_invoices(
    project_id: str,
    status: Optional[str] = Query(default=None),
    carrier: Optional[str] = Query(default=None),
    invoice_number: Optional[str] = Query(
        default=None,
        description="Case-insensitive substring match on invoice_number",
    ),
    created_after: Optional[str] = Query(default=None),
    created_before: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    _user=Depends(get_current_user),
):
    try:
        return observability_logs.list_invoices(
            project_id,
            status=status,
            carrier=carrier,
            invoice_number=invoice_number,
            created_after=created_after,
            created_before=created_before,
            page=page,
            page_size=page_size,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DynamoDB error: {str(e)}") from e


@router.get("/{project_id}/invoices/{email_id}/{attachment_id}")
def get_invoice_detail(
    project_id: str,
    email_id: str,
    attachment_id: str,
    _user=Depends(get_current_user),
):
    try:
        detail = observability_logs.get_invoice_detail(project_id, email_id, attachment_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DynamoDB error: {str(e)}") from e

    if not detail:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return detail


@router.get("/{project_id}/stats")
def get_stats(
    project_id: str,
    status: Optional[str] = Query(default=None),
    carrier: Optional[str] = Query(default=None),
    invoice_number: Optional[str] = Query(default=None),
    created_after: Optional[str] = Query(default=None),
    created_before: Optional[str] = Query(default=None),
    _user=Depends(get_current_user),
):
    try:
        return observability_logs.get_stats(
            project_id,
            status=status,
            carrier=carrier,
            invoice_number=invoice_number,
            created_after=created_after,
            created_before=created_before,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DynamoDB error: {str(e)}") from e


@router.get("/{project_id}/statuses")
def get_statuses(project_id: str, _user=Depends(get_current_user)):
    try:
        return observability_logs.get_statuses(project_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DynamoDB error: {str(e)}") from e


@router.post("/{project_id}/invoices/{email_id}/retrigger")
def retrigger_invoice(
    project_id: str,
    email_id: str,
    _user=Depends(get_current_user),
):
    """
    Re-trigger all attachments for a given email:
      - Deletes ALL DynamoDB rows under pk=EMAIL#{email_id}
      - Re-PUTs the original S3 object (same key, no suffix) to fire the Lambda pipeline again
    """
    try:
        return observability_logs.retrigger_invoice(project_id, email_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

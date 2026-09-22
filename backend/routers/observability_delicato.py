"""Observability API for the Delicato invoice-processing project."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from routers.auth import get_current_user
from services import delicato_logs

router = APIRouter(tags=["observability-delicato"])


@router.get("/invoices")
def get_invoices(
    status: Optional[str] = Query(default=None),
    carrier: Optional[str] = Query(default=None),
    invoice_number: Optional[str] = Query(default=None),
    created_after: Optional[str] = Query(
        default=None,
        description="ISO date/datetime — include invoices created at or after this time",
    ),
    created_before: Optional[str] = Query(
        default=None,
        description="ISO date/datetime — include invoices created at or before this time",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    _user=Depends(get_current_user),
):
    try:
        return delicato_logs.list_invoices(
            status=status,
            carrier=carrier,
            invoice_number=invoice_number,
            created_after=created_after,
            created_before=created_before,
            page=page,
            page_size=page_size,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DynamoDB error: {str(e)}") from e


@router.get("/invoices/{email_id}/{attachment_id}")
def get_invoice_detail(
    email_id: str,
    attachment_id: str,
    _user=Depends(get_current_user),
):
    try:
        detail = delicato_logs.get_invoice_detail(email_id, attachment_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DynamoDB error: {str(e)}") from e

    if not detail:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return detail


@router.get("/stats")
def get_stats(
    status: Optional[str] = Query(default=None),
    carrier: Optional[str] = Query(default=None),
    invoice_number: Optional[str] = Query(default=None),
    created_after: Optional[str] = Query(default=None),
    created_before: Optional[str] = Query(default=None),
    _user=Depends(get_current_user),
):
    try:
        return delicato_logs.get_stats(
            status=status,
            carrier=carrier,
            invoice_number=invoice_number,
            created_after=created_after,
            created_before=created_before,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DynamoDB error: {str(e)}") from e


@router.get("/statuses")
def get_statuses(_user=Depends(get_current_user)):
    try:
        return delicato_logs.get_statuses()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DynamoDB error: {str(e)}") from e

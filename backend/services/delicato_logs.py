"""
Backward-compatible Delicato Observability helpers.

Prefer services.observability_logs with project_id=\"delicato\".
"""

from services import observability_logs

PROJECT_ID = "delicato"


def warm_cache():
    return observability_logs.warm_cache(PROJECT_ID)


def list_invoices(**kwargs):
    return observability_logs.list_invoices(PROJECT_ID, **kwargs)


def get_invoice_detail(email_id: str, attachment_id: str):
    return observability_logs.get_invoice_detail(PROJECT_ID, email_id, attachment_id)


def get_stats(**kwargs):
    return observability_logs.get_stats(PROJECT_ID, **kwargs)


def get_statuses():
    return observability_logs.get_statuses(PROJECT_ID)

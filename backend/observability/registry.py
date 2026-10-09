"""Observability project registry — account + DynamoDB table per project."""

from __future__ import annotations

from dataclasses import dataclass

from config import (
    make_aws_session,
    make_observability_ddb_session,
    settings,
    _session_from_secret,
)
import boto3


@dataclass(frozen=True)
class ObservabilityProject:
    id: str
    name: str
    account: str  # "ops" | "meta"
    table_name: str
    region: str
    # Primary Lambda log group used by "View CloudWatch log" deep-links.
    cloudwatch_log_group: str = ""


# Invoice-processor / orchestrator log groups.
# These live in the source/ops AWS account (see CLOUDWATCH_CONSOLE_ACCOUNT_ID),
# NOT in the "production" console account users often keep open.
_DEFAULT_LOG_GROUPS: dict[str, str] = {
    "delicato": "/aws/lambda/pando-delicato-orchestrator",
    "ge": "/aws/lambda/pando-general-electronics-invoice-processor-temp",
    # JnJ Batch processors log under the shared /aws/batch/job group
    # (streams like jj-invoice-definition/default/...).
    "jnj": "/aws/batch/job",
    "otter": "/aws/lambda/pando-otter-invoice-processor",
    "ghent": "/aws/lambda/pando-ghent-invoice_processing_temp",
    "west-marine": "/aws/lambda/pando-west-marine-orchestrator",
    "viking": "/aws/lambda/pando-viking-invoice-processing",
    "unilever": "/aws/lambda/Pando-Unilever-Invoice",
    "unilever-excel": "/aws/lambda/pando-unilever-excel-processor",
    "meta": "/aws/lambda/pando-meta-invoice",
}

# Secondary groups to search when the primary has no matching events.
_FALLBACK_LOG_GROUPS: dict[str, list[str]] = {
    "jnj": [
        "/aws/lambda/jj-BatchTriggerLambda",
        "/aws/lambda/pando-JJ-invoice-processing",
    ],
}

# Account where invoice Lambda CloudWatch log groups actually exist.
CLOUDWATCH_CONSOLE_ACCOUNT_ID = "354602095398"


def _meta_session() -> boto3.Session:
    """Session for Meta (Account B) Observability DynamoDB."""
    if not settings.META_DDB_SECRET_NAME:
        return make_aws_session()
    return _session_from_secret(
        settings.META_DDB_SECRET_NAME,
        secrets_region=settings.META_DDB_SECRET_REGION,
        session_region=settings.META_DDB_REGION,
    )


def session_for_account(account: str) -> boto3.Session:
    if account == "meta":
        return _meta_session()
    if account == "ops":
        return make_observability_ddb_session()
    return make_aws_session()


def _ops(
    project_id: str,
    name: str,
    table_name: str,
    cloudwatch_log_group: str = "",
) -> ObservabilityProject:
    return ObservabilityProject(
        id=project_id,
        name=name,
        account="ops",
        table_name=table_name,
        region=settings.OBSERVABILITY_DDB_REGION or settings.DELICATO_AWS_REGION,
        cloudwatch_log_group=cloudwatch_log_group
        or _DEFAULT_LOG_GROUPS.get(project_id, ""),
    )


def _build_projects() -> dict[str, ObservabilityProject]:
    return {
        "delicato": _ops("delicato", "Delicato", settings.DELICATO_LOG_TABLE),
        "ge": _ops("ge", "GE", settings.GE_LOG_TABLE),
        "jnj": _ops("jnj", "JnJ", settings.JNJ_LOG_TABLE),
        "otter": _ops("otter", "Otter", settings.OTTER_LOG_TABLE),
        "ghent": _ops("ghent", "Ghent", settings.GHENT_LOG_TABLE),
        "west-marine": _ops(
            "west-marine", "West Marine", settings.WEST_MARINE_LOG_TABLE
        ),
        "viking": _ops("viking", "Viking", settings.VIKING_LOG_TABLE),
        "unilever": _ops("unilever", "Unilever Logs", settings.UNILEVER_LOG_TABLE),
        "unilever-excel": _ops(
            "unilever-excel",
            "Unilever Excel Demo",
            settings.UNILEVER_EXCEL_LOG_TABLE,
        ),
        "meta": ObservabilityProject(
            id="meta",
            name="Meta",
            account="meta",
            table_name=settings.META_LOG_TABLE,
            region=settings.META_DDB_REGION,
            cloudwatch_log_group=_DEFAULT_LOG_GROUPS.get("meta", ""),
        ),
    }


OBSERVABILITY_PROJECTS = _build_projects()


def get_project(project_id: str) -> ObservabilityProject | None:
    # Prefer user-configured views (may override a builtin id).
    try:
        from services.observability_views import get_view, view_as_registry_project

        view = get_view(project_id)
        if view and view.get("table_name"):
            return view_as_registry_project(view)
    except Exception:
        pass
    built_in = OBSERVABILITY_PROJECTS.get(project_id)
    if built_in and built_in.table_name:
        return built_in
    return built_in


def log_groups_for_project(project_id: str) -> list[str]:
    """Primary + fallback CloudWatch log groups for a project (deduped)."""
    project = get_project(project_id)
    groups: list[str] = []
    if project and project.cloudwatch_log_group:
        groups.append(project.cloudwatch_log_group)
    for g in _FALLBACK_LOG_GROUPS.get(project_id, []):
        if g and g not in groups:
            groups.append(g)
    return groups


def list_projects() -> list[dict]:
    """Hub list — only user-configurable views (hardcoded projects are seeded once into DB)."""
    try:
        from services.observability_views import list_views

        out = []
        for v in list_views():
            if not v.get("table_name"):
                continue
            out.append(
                {
                    "id": v["view_id"],
                    "name": v.get("name") or v["view_id"],
                    "account": v.get("account") or "ops",
                    "table": v.get("table_name"),
                    "description": v.get("description") or "",
                    "source": "configured",
                    "ui_mode": v.get("ui_mode") or "dynamic",
                    "selected_fields": v.get("selected_fields") or [],
                    "action_view_pdf": bool(v.get("action_view_pdf", True)),
                    "action_cloudwatch": bool(v.get("action_cloudwatch", True)),
                    "action_retrigger": bool(v.get("action_retrigger", True)),
                }
            )
        out.sort(key=lambda x: (x.get("name") or x["id"]).lower())
        return out
    except Exception:
        return []

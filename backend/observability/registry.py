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
    "jnj": "/aws/lambda/pando-JJ-invoice-processing",
    "otter": "/aws/lambda/pando-otter-invoice-processor",
    "ghent": "/aws/lambda/pando-ghent-invoice_processing_temp",
    "west-marine": "/aws/lambda/pando-west-marine-orchestrator",
    "viking": "/aws/lambda/pando-viking-invoice-processing",
    "unilever": "/aws/lambda/Pando-Unilever-Invoice",
    "unilever-excel": "/aws/lambda/pando-unilever-excel-processor",
    "meta": "/aws/lambda/pando-meta-invoice",
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
    return OBSERVABILITY_PROJECTS.get(project_id)


def list_projects() -> list[dict]:
    return [
        {"id": p.id, "name": p.name, "account": p.account, "table": p.table_name}
        for p in OBSERVABILITY_PROJECTS.values()
        if p.table_name
    ]

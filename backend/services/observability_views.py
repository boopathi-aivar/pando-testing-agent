"""
Configurable Observability views.

User supplies a DynamoDB table name (same ops/meta credentials as today).
We sample items to discover fields, let the user pick columns + action toggles,
and persist the view config in the app DynamoDB account.
"""

from __future__ import annotations

import re
import time
import uuid
from decimal import Decimal
from typing import Any, Optional

from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import HTTPException

from config import settings
from database import tbl_observability_views
from observability.registry import session_for_account

# Virtual checklist entries (not DynamoDB attributes) — checked by default.
ACTION_FIELD_DEFS = [
    {
        "id": "action_view_pdf",
        "label": "View PDF",
        "kind": "action",
        "default_checked": True,
    },
    {
        "id": "action_cloudwatch",
        "label": "View CloudWatch log",
        "kind": "action",
        "default_checked": True,
    },
    {
        "id": "action_retrigger",
        "label": "Re-trigger",
        "kind": "action",
        "default_checked": True,
    },
]

# Prefer these as initially selected when present in the sample.
_PREFERRED_LIST_FIELDS = (
    "invoice_number",
    "carrier_name",
    "status",
    "invoice_date",
    "created_at_iso",
    "created_at",
    "filename",
    "error",
)


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return s or f"view-{uuid.uuid4().hex[:8]}"


def _type_label(val: Any) -> str:
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "bool"
    if isinstance(val, (int, float, Decimal)):
        return "number"
    if isinstance(val, dict):
        return "map"
    if isinstance(val, (list, set, tuple)):
        return "list"
    return "string"


def _collect_fields(items: list[dict]) -> list[dict]:
    """Union of top-level attribute names from sample items."""
    seen: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for key, val in item.items():
            if key not in seen:
                seen[key] = _type_label(val)
    # Stable-ish order: preferred first, then alpha
    preferred = [k for k in _PREFERRED_LIST_FIELDS if k in seen]
    rest = sorted(k for k in seen if k not in preferred)
    return [{"id": k, "label": k, "kind": "data", "type": seen[k]} for k in preferred + rest]


def _ddb_table(table_name: str, account: str = "ops"):
    region = (
        settings.META_DDB_REGION
        if account == "meta"
        else (settings.OBSERVABILITY_DDB_REGION or settings.DELICATO_AWS_REGION)
    )
    session = session_for_account(account if account in ("ops", "meta") else "ops")
    return session.resource(
        "dynamodb",
        region_name=region,
        config=Config(max_pool_connections=8),
    ).Table(table_name)


def discover_schema(
    table_name: str,
    account: str = "ops",
    sample_limit: int = 25,
) -> dict:
    table_name = (table_name or "").strip()
    if not table_name:
        raise HTTPException(status_code=400, detail="table_name is required")
    account = (account or "ops").strip().lower()
    if account not in ("ops", "meta"):
        raise HTTPException(status_code=400, detail="account must be 'ops' or 'meta'")

    table = _ddb_table(table_name, account)
    items: list[dict] = []
    try:
        # Prefer attachment-shaped rows when present (current Pando schema).
        from boto3.dynamodb.conditions import Attr

        resp = table.scan(
            Limit=sample_limit,
            FilterExpression=Attr("sk").begins_with("ATTACHMENT#"),
        )
        items = resp.get("Items") or []
        if not items:
            resp = table.scan(Limit=sample_limit)
            items = resp.get("Items") or []
    except ClientError as exc:
        code = (exc.response.get("Error") or {}).get("Code", "")
        msg = (exc.response.get("Error") or {}).get("Message", str(exc))
        if code in ("ResourceNotFoundException",):
            raise HTTPException(
                status_code=404,
                detail=f"Table '{table_name}' not found in account '{account}'",
            ) from exc
        raise HTTPException(status_code=500, detail=f"DynamoDB error: {msg}") from exc

    data_fields = _collect_fields(items)
    suggested = [f["id"] for f in data_fields if f["id"] in _PREFERRED_LIST_FIELDS]
    if not suggested and data_fields:
        suggested = [f["id"] for f in data_fields[:6]]

    return {
        "table_name": table_name,
        "account": account,
        "sample_count": len(items),
        "fields": data_fields,
        "action_fields": ACTION_FIELD_DEFS,
        "suggested_selected_fields": suggested,
        "suggested_actions": {
            "action_view_pdf": True,
            "action_cloudwatch": True,
            "action_retrigger": True,
        },
        "note": (
            "DynamoDB has no fixed schema; fields are discovered from sample items. "
            "Re-run discover if new attributes appear later."
            if items
            else "No items sampled — checklist may be empty. Paste sample data later or re-try."
        ),
    }


def _serialize(item: dict) -> dict:
    out = dict(item)
    # Ensure JSON-friendly
    for k, v in list(out.items()):
        if isinstance(v, Decimal):
            out[k] = int(v) if v == int(v) else float(v)
        elif isinstance(v, set):
            out[k] = list(v)
    return out


_SEED_MARKER_ID = "__obs_views_seeded_v1__"

def list_views() -> list[dict]:
    resp = tbl_observability_views().scan()
    items = [
        _serialize(i)
        for i in (resp.get("Items") or [])
        if not str(i.get("view_id") or "").startswith("__")
    ]
    items.sort(key=lambda x: (x.get("name") or x.get("view_id") or "").lower())
    return items


_CLEAR_LEGACY_MARKER_ID = "__obs_views_cleared_legacy_v1__"

# Former hardcoded hub ids — removed once so the hub starts empty / user-driven.
_LEGACY_VIEW_IDS = (
    "delicato",
    "ge",
    "jnj",
    "otter",
    "ghent",
    "west-marine",
    "viking",
    "unilever",
    "unilever-excel",
    "meta",
)


def seed_views_from_registry_once() -> int:
    """
    No longer copies registry projects into the hub.
    Only writes a seed marker so older builds that expected a migration stay noop.
    """
    table = tbl_observability_views()
    marker = table.get_item(Key={"view_id": _SEED_MARKER_ID}).get("Item")
    if marker:
        return 0
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    table.put_item(
        Item={
            "view_id": _SEED_MARKER_ID,
            "seeded_at": now,
            "created_count": 0,
            "note": "Hub is user-configured only; registry projects are not auto-added.",
        }
    )
    return 0


def clear_legacy_seeded_views_once() -> int:
    """
    One-time: remove formerly auto-seeded project cards (Delicato, Meta, …).
    Keeps any other user-created views. Does not re-run after the clear marker exists.
    """
    table = tbl_observability_views()
    marker = table.get_item(Key={"view_id": _CLEAR_LEGACY_MARKER_ID}).get("Item")
    if marker:
        return 0

    removed = 0
    for view_id in _LEGACY_VIEW_IDS:
        if get_view(view_id):
            delete_view(view_id)
            removed += 1

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    table.put_item(
        Item={
            "view_id": _CLEAR_LEGACY_MARKER_ID,
            "cleared_at": now,
            "removed_count": removed,
        }
    )
    # Also ensure the old seed marker exists so nothing re-seeds.
    if not table.get_item(Key={"view_id": _SEED_MARKER_ID}).get("Item"):
        table.put_item(
            Item={
                "view_id": _SEED_MARKER_ID,
                "seeded_at": now,
                "created_count": 0,
            }
        )
    print(f"[Observability] Cleared {removed} legacy seeded view(s).")
    return removed


def get_view(view_id: str) -> dict | None:
    resp = tbl_observability_views().get_item(Key={"view_id": view_id})
    item = resp.get("Item")
    return _serialize(item) if item else None


def create_view(body) -> dict:
    name = body.name.strip()
    table_name = body.table_name.strip()
    account = (body.account or "ops").strip().lower()
    if account not in ("ops", "meta"):
        raise HTTPException(status_code=400, detail="account must be 'ops' or 'meta'")

    view_id = (body.view_id or "").strip() or _slugify(name)
    existing = get_view(view_id)
    if existing:
        view_id = f"{view_id}-{uuid.uuid4().hex[:6]}"

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    item = {
        "view_id": view_id,
        "name": name,
        "description": (body.description or "").strip(),
        "table_name": table_name,
        "account": account,
        "selected_fields": list(body.selected_fields or []),
        "discovered_fields": list(body.discovered_fields or []),
        "action_view_pdf": bool(body.action_view_pdf),
        "action_cloudwatch": bool(body.action_cloudwatch),
        "action_retrigger": bool(body.action_retrigger),
        "created_at": now,
        "updated_at": now,
        "ui_mode": "dynamic",
    }
    tbl_observability_views().put_item(Item=item)
    return _serialize(item)


def update_view(view_id: str, body) -> dict:
    current = get_view(view_id)
    if not current:
        raise HTTPException(status_code=404, detail=f"View '{view_id}' not found")

    data = body.model_dump(exclude_unset=True)
    if "account" in data and data["account"] is not None:
        account = str(data["account"]).strip().lower()
        if account not in ("ops", "meta"):
            raise HTTPException(status_code=400, detail="account must be 'ops' or 'meta'")
        data["account"] = account
    if "table_name" in data and data["table_name"] is not None:
        data["table_name"] = str(data["table_name"]).strip()
    for key, val in data.items():
        if val is not None:
            current[key] = val
    current["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tbl_observability_views().put_item(Item=current)
    return _serialize(current)


def delete_view(view_id: str) -> None:
    tbl_observability_views().delete_item(Key={"view_id": view_id})


def view_as_registry_project(view: dict):
    """Build an ObservabilityProject-compatible object for cache/scan."""
    from observability.registry import ObservabilityProject, _DEFAULT_LOG_GROUPS

    account = view.get("account") or "ops"
    region = (
        settings.META_DDB_REGION
        if account == "meta"
        else (settings.OBSERVABILITY_DDB_REGION or settings.DELICATO_AWS_REGION)
    )
    view_id = view["view_id"]
    cw = (view.get("cloudwatch_log_group") or "").strip() or _DEFAULT_LOG_GROUPS.get(
        view_id, ""
    )
    return ObservabilityProject(
        id=view_id,
        name=view.get("name") or view_id,
        account=account,
        table_name=view.get("table_name") or "",
        region=region,
        cloudwatch_log_group=cw,
    )

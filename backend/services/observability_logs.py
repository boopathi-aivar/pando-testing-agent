"""
Multi-project Observability log service.

Per-project DynamoDB table + Secrets Manager session (ops vs meta accounts).
Shared scan/cache/list/stats logic for invoice attachment rows.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config
from fastapi import HTTPException

from config import settings
from observability.registry import get_project, list_projects, session_for_account

log = logging.getLogger("observability_logs")

_TOTAL_SEGMENTS = settings.DELICATO_SCAN_SEGMENTS
_MAX_WORKERS = settings.DELICATO_SCAN_SEGMENTS
_CACHE_TTL_SECONDS = settings.DELICATO_CACHE_TTL_SECONDS

# List/cache only — exclude huge api_payload / extracted_fields / transaction_steps.
_LIST_PROJECTION = (
    "pk, sk, email_id, attachment_id, filename, #st, carrier_name, "
    "invoice_number, invoice_date, #md, s3_path, created_at, created_at_iso, "
    "updated_at, updated_at_iso, completed_at_iso, #err, stage_validation, "
    "confidence_score, textract_classification_result, "
    "image_model_classification_result, api_response, #tp"
)
_LIST_EXPR_NAMES = {
    "#st": "status",
    "#md": "mode",
    "#err": "error",
    "#tp": "type",
}

# project_id -> { lock, table, items, timestamp }
_project_state: dict[str, dict] = {}
_state_guard = threading.Lock()


def _state(project_id: str) -> dict:
    with _state_guard:
        if project_id not in _project_state:
            _project_state[project_id] = {
                "lock": threading.Lock(),
                "table": None,
                "items": [],
                "timestamp": 0.0,
            }
        return _project_state[project_id]


def require_project(project_id: str):
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Unknown observability project: {project_id}")
    if not project.table_name:
        raise HTTPException(
            status_code=503,
            detail=f"Observability project '{project_id}' has no table configured "
            f"(set META_LOG_TABLE / DELICATO_LOG_TABLE)",
        )
    return project


def _get_table(project_id: str):
    project = require_project(project_id)
    st = _state(project_id)
    if st["table"] is None:
        boto_cfg = Config(max_pool_connections=max(_TOTAL_SEGMENTS, 1))
        session = session_for_account(project.account)
        dynamodb = session.resource(
            "dynamodb",
            region_name=project.region,
            config=boto_cfg,
        )
        st["table"] = dynamodb.Table(project.table_name)
        log.info(
            "Observability table ready project=%s account=%s table=%s region=%s",
            project.id,
            project.account,
            project.table_name,
            project.region,
        )
    return st["table"]


def _list_kwargs_base() -> dict:
    return {
        "ProjectionExpression": _LIST_PROJECTION,
        "ExpressionAttributeNames": _LIST_EXPR_NAMES,
    }


def _scan_segment(project_id: str, segment: int) -> list[dict]:
    table = _get_table(project_id)
    items: list[dict] = []
    kwargs: dict = {
        **_list_kwargs_base(),
        "Segment": segment,
        "TotalSegments": _TOTAL_SEGMENTS,
        "FilterExpression": Attr("sk").begins_with("ATTACHMENT#"),
    }
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        last = resp.get("LastEvaluatedKey")
        if not last:
            break
        kwargs["ExclusiveStartKey"] = last
    return items


def _parallel_scan(project_id: str) -> list[dict]:
    all_items: list[dict] = []
    t0 = time.time()
    log.info("Parallel scan start project=%s segments=%d", project_id, _TOTAL_SEGMENTS)

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
        futures = {
            executor.submit(_scan_segment, project_id, seg): seg
            for seg in range(_TOTAL_SEGMENTS)
        }
        for future in as_completed(futures):
            seg = futures[future]
            try:
                all_items.extend(future.result())
            except Exception as exc:
                log.error("Segment %d failed project=%s: %s", seg, project_id, exc)

    log.info(
        "Parallel scan done project=%s — %d items in %.2fs",
        project_id,
        len(all_items),
        time.time() - t0,
    )
    return all_items


def _query_gsi(project_id: str) -> list[dict]:
    """Query optional list GSI (typically type + created_at_iso)."""
    table = _get_table(project_id)
    index = settings.OBSERVABILITY_LIST_GSI
    pk_attr = settings.OBSERVABILITY_LIST_GSI_PK
    pk_value = settings.OBSERVABILITY_LIST_GSI_PK_VALUE

    items: list[dict] = []
    t0 = time.time()
    log.info(
        "GSI query start project=%s index=%s %s=%s",
        project_id,
        index,
        pk_attr,
        pk_value,
    )

    kwargs: dict = {
        "IndexName": index,
        "KeyConditionExpression": Key(pk_attr).eq(pk_value),
        "ProjectionExpression": _LIST_PROJECTION,
        "ExpressionAttributeNames": dict(_LIST_EXPR_NAMES),
        "ScanIndexForward": False,
    }

    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        last = resp.get("LastEvaluatedKey")
        if not last:
            break
        kwargs["ExclusiveStartKey"] = last

    log.info(
        "GSI query done project=%s — %d items in %.2fs",
        project_id,
        len(items),
        time.time() - t0,
    )
    return items


def _fetch_attachment_items(project_id: str) -> list[dict]:
    """Prefer GSI Query when configured; otherwise projected parallel Scan."""
    if settings.OBSERVABILITY_LIST_GSI:
        try:
            return _query_gsi(project_id)
        except Exception as exc:
            log.error(
                "GSI query failed project=%s index=%s — falling back to scan: %s",
                project_id,
                settings.OBSERVABILITY_LIST_GSI,
                exc,
            )
    return _parallel_scan(project_id)


def get_cached_items(project_id: str, force: bool = False) -> list[dict]:
    require_project(project_id)
    st = _state(project_id)
    now = time.time()
    with st["lock"]:
        if force or (now - st["timestamp"]) > _CACHE_TTL_SECONDS:
            log.info("Cache miss project=%s — fetching attachment list", project_id)
            items = _fetch_attachment_items(project_id)
            st["items"] = items
            st["timestamp"] = time.time()
            if not items:
                log.warning(
                    "Observability fetch returned 0 ATTACHMENT items for project=%s — "
                    "check table/GSI/region or report schema mismatch to project owners "
                    "(expected sk begins_with ATTACHMENT# or type=Attachment)",
                    project_id,
                )
        else:
            log.info(
                "Cache hit project=%s — %d items, age=%.1fs",
                project_id,
                len(st["items"]),
                now - st["timestamp"],
            )
        return list(st["items"])


def warm_cache(project_id: Optional[str] = None) -> None:
    ids = [project_id] if project_id else [p["id"] for p in list_projects()]
    errors: list[str] = []
    for pid in ids:
        try:
            log.info("Warming Observability cache project=%s...", pid)
            get_cached_items(pid, force=True)
            log.info("Observability cache ready project=%s", pid)
        except Exception as exc:
            log.error("Cache warm failed project=%s: %s", pid, exc)
            errors.append(f"{pid}: {exc}")
    if errors and project_id:
        raise RuntimeError("; ".join(errors))
    if errors:
        log.warning("Some Observability caches failed to warm: %s", "; ".join(errors))


def _safe_str(val) -> str:
    if val is None:
        return ""
    if isinstance(val, dict):
        return json.dumps(val, default=str)
    if isinstance(val, Decimal):
        return str(int(val)) if val == int(val) else str(val)
    return str(val)


def _format_ts(val) -> str:
    if not val:
        return ""
    try:
        n = float(val)
        if n > 1e10:
            n = n / 1000
        return datetime.utcfromtimestamp(n).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        return str(val)


def sanitize(obj):
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, Decimal):
        return int(obj) if obj == int(obj) else float(obj)
    if isinstance(obj, set):
        return list(obj)
    return obj


def parse_attachment(item: dict) -> dict:
    error = item.get("error") or {}
    if isinstance(error, str):
        try:
            error = json.loads(error)
        except Exception:
            error = {"message": error, "error_code": ""}

    stage_val = item.get("stage_validation") or {}
    missing_fields = stage_val.get("missing_fields") or []

    return {
        "email_id": _safe_str(item.get("email_id")),
        "attachment_id": _safe_str(item.get("attachment_id")),
        "filename": _safe_str(item.get("filename")),
        "status": _safe_str(item.get("status")),
        "carrier_name": _safe_str(item.get("carrier_name")),
        "invoice_number": _safe_str(item.get("invoice_number")),
        "invoice_date": _safe_str(item.get("invoice_date")),
        "mode": _safe_str(item.get("mode")),
        "s3_path": _safe_str(item.get("s3_path")),
        "created_at": _format_ts(item.get("created_at")),
        "created_at_iso": _safe_str(item.get("created_at_iso")),
        "updated_at": _format_ts(item.get("updated_at")),
        "updated_at_iso": _safe_str(item.get("updated_at_iso")),
        "completed_at_iso": _safe_str(item.get("completed_at_iso")),
        "error_code": _safe_str(error.get("error_code")),
        "error_message": _safe_str(error.get("message")),
        "missing_fields": missing_fields,
        "confidence_score": _safe_str(item.get("confidence_score")),
        "textract_carrier": _safe_str(item.get("textract_classification_result")),
        "vision_carrier": _safe_str(item.get("image_model_classification_result")),
        "api_status_code": _safe_str(
            (sanitize(item.get("api_response") or {})).get("status_code")
        ),
        "api_success": _safe_str(
            (sanitize(item.get("api_response") or {})).get("success")
        ),
    }


def _parse_created_dt(item: dict) -> datetime | None:
    iso = (item.get("created_at_iso") or "").strip()
    if iso:
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass

    raw = item.get("created_at")
    if raw in (None, ""):
        return None
    try:
        n = float(str(raw).replace(",", ""))
        if n > 1e12:
            n = n / 1000.0
        return datetime.fromtimestamp(n, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _parse_query_dt(value: Optional[str], *, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    try:
        if len(raw) <= 10 and "T" not in raw:
            dt = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
            if end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
            return dt
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _apply_filters(
    items: list[dict],
    *,
    status: Optional[str] = None,
    carrier: Optional[str] = None,
    invoice_number: Optional[str] = None,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
) -> list[dict]:
    if status:
        items = [i for i in items if i["status"] == status]
    if carrier:
        cl = carrier.lower().strip()
        items = [i for i in items if i["carrier_name"].lower().strip() == cl]
    if invoice_number:
        needle = invoice_number.lower().strip()
        items = [
            i for i in items
            if needle in (i.get("invoice_number") or "").lower()
        ]

    after_dt = _parse_query_dt(created_after)
    before_dt = _parse_query_dt(created_before, end_of_day=True)
    if after_dt or before_dt:
        filtered = []
        for i in items:
            created = _parse_created_dt(i)
            if created is None:
                continue
            if after_dt and created < after_dt:
                continue
            if before_dt and created > before_dt:
                continue
            filtered.append(i)
        items = filtered
    return items


def list_invoices(
    project_id: str,
    status: Optional[str] = None,
    carrier: Optional[str] = None,
    invoice_number: Optional[str] = None,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    raw = get_cached_items(project_id)
    items = _apply_filters(
        [parse_attachment(r) for r in raw],
        status=status,
        carrier=carrier,
        invoice_number=invoice_number,
        created_after=created_after,
        created_before=created_before,
    )
    items.sort(
        key=lambda i: i.get("created_at_iso") or i.get("updated_at_iso") or "",
        reverse=True,
    )

    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, -(-total // page_size)),
        "items": page_items,
    }


def get_invoice_detail(project_id: str, email_id: str, attachment_id: str) -> dict | None:
    require_project(project_id)
    resp = _get_table(project_id).get_item(
        Key={
            "pk": f"EMAIL#{email_id}",
            "sk": f"ATTACHMENT#{attachment_id}",
        }
    )
    item = resp.get("Item")
    if not item:
        return None

    base = parse_attachment(item)
    base["stage_clustering"] = sanitize(item.get("stage_clustering") or {})
    base["stage_textract"] = sanitize(item.get("stage_textract") or {})
    base["stage_llm_extraction"] = sanitize(item.get("stage_llm_extraction") or {})
    base["stage_validation"] = sanitize(item.get("stage_validation") or {})
    base["transaction_steps"] = sanitize(item.get("transaction_steps") or [])

    raw_payload = item.get("api_payload")
    if raw_payload:
        try:
            base["api_payload"] = json.loads(raw_payload)
        except Exception:
            base["api_payload"] = raw_payload
    else:
        base["api_payload"] = None

    return base


def get_stats(
    project_id: str,
    status: Optional[str] = None,
    carrier: Optional[str] = None,
    invoice_number: Optional[str] = None,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
) -> dict:
    raw = get_cached_items(project_id)
    items = _apply_filters(
        [parse_attachment(r) for r in raw],
        status=status,
        carrier=carrier,
        invoice_number=invoice_number,
        created_after=created_after,
        created_before=created_before,
    )

    counts: dict[str, int] = {}
    for i in items:
        s = str(i.get("status") or "UNKNOWN")
        counts[s] = counts.get(s, 0) + 1

    return {
        "total": len(items),
        "by_status": counts,
        "completed": counts.get("COMPLETED", 0),
        "failed": counts.get("FAILED", 0) + counts.get("API_FAILED", 0),
        "processing": counts.get("PROCESSING", 0),
        "queued": counts.get("QUEUED", 0) + counts.get("BATCH_QUEUED", 0),
        "rejected": counts.get("REJECTED_MULTIPLE_INVOICES", 0)
        + counts.get("REJECTED_FILENAME_TOO_LONG", 0),
    }


def get_statuses(project_id: str) -> dict:
    raw = get_cached_items(project_id)
    statuses = sorted({str(r.get("status") or "UNKNOWN") for r in raw})
    return {"statuses": statuses}


# ── S3 bucket map per project ─────────────────────────────────────────────────
# Buckets live in the same account as the observability DynamoDB tables.
# Meta has its own separate account (same pattern as DynamoDB).
_PROJECT_S3_BUCKETS: dict[str, str] = {
    "delicato":      "delicato-demo-mail-watcher-dest",
    "ghent":         "ghent-demo-mail-watcher-dest",
    "west-marine":   "west-marine-demo-mail-watcher-1",
    "ge":            "pando-general-electronics-demo-mail-watcher",
    "unilever":      "unilever-demo-mail-watcher-dest",
    "unilever-excel":"unilever-demo-mail-watcher-dest",  # same bucket as unilever
    "jnj":           "jnj-shipment-demo-mail-watcher-dest",
    "otter":         "otter-demo-mail-watcher-1",
    "meta":          "meta-demo-mail-watcher-dest",
    "viking":        "gd-pando-demo-mail-automation",
}


def _s3_client_for_project(project_id: str):
    """
    Return an S3 client using the same credentials as the project's DynamoDB session.
    - Meta account → _meta_session()
    - All other projects → make_observability_ddb_session()
    The region is us-east-1 for all these buckets.
    """
    session = session_for_account(
        "meta" if project_id == "meta" else "ops"
    )
    return session.client("s3", region_name="us-east-1")


def retrigger_invoice(project_id: str, email_id: str) -> dict:
    """
    Re-trigger all attachments for a given email by:
      1. Querying ALL items under pk=EMAIL#{email_id} (metadata + all attachments).
      2. Deleting every item found (batch_writer handles batching in groups of 25).
      3. Downloading the original S3 object (key = email_id, no EMAIL# prefix).
      4. Re-PUTting the same bytes back to S3 — overwrites the object and
         fires the S3 event that re-starts the Lambda pipeline.

    Returns a summary dict with deleted_count and s3_key.
    Raises HTTPException on any failure so the router can surface it cleanly.
    """
    project = require_project(project_id)

    bucket = _PROJECT_S3_BUCKETS.get(project_id)
    if not bucket:
        raise HTTPException(
            status_code=400,
            detail=f"No S3 bucket configured for project '{project_id}'.",
        )

    table = _get_table(project_id)
    pk_value = f"EMAIL#{email_id}"

    # ── Step 1: Query all items under the PK ─────────────────────────────────
    log.info(
        "Retrigger: querying all items for pk=%s project=%s", pk_value, project_id
    )
    items_to_delete: list[dict] = []
    query_kwargs: dict = {
        "KeyConditionExpression": Key("pk").eq(pk_value),
        "ProjectionExpression": "pk, sk",  # only need keys for deletion
    }
    while True:
        resp = table.query(**query_kwargs)
        items_to_delete.extend(resp.get("Items", []))
        last = resp.get("LastEvaluatedKey")
        if not last:
            break
        query_kwargs["ExclusiveStartKey"] = last

    if not items_to_delete:
        raise HTTPException(
            status_code=404,
            detail=f"No records found for email_id='{email_id}' in project '{project_id}'.",
        )

    log.info(
        "Retrigger: found %d items to delete for pk=%s", len(items_to_delete), pk_value
    )

    # ── Step 2: Download the S3 object BEFORE deleting DynamoDB ──────────────
    # Fetch first so we know the object exists and can roll back if needed.
    s3 = _s3_client_for_project(project_id)
    s3_key = email_id  # no EMAIL# prefix

    log.info("Retrigger: downloading s3://%s/%s", bucket, s3_key)
    try:
        s3_resp = s3.get_object(Bucket=bucket, Key=s3_key)
        obj_bytes = s3_resp["Body"].read()
        content_type = s3_resp.get("ContentType", "application/octet-stream")
    except s3.exceptions.NoSuchKey:
        raise HTTPException(
            status_code=404,
            detail=f"S3 object not found: s3://{bucket}/{s3_key}",
        )
    except Exception as exc:
        log.exception("Retrigger: S3 download failed bucket=%s key=%s", bucket, s3_key)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to download S3 object: {exc}",
        ) from exc

    # ── Step 3: Delete all DynamoDB items under this PK ──────────────────────
    log.info(
        "Retrigger: deleting %d DynamoDB items for pk=%s", len(items_to_delete), pk_value
    )
    try:
        with table.batch_writer() as batch:
            for item in items_to_delete:
                batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
    except Exception as exc:
        log.exception("Retrigger: DynamoDB delete failed pk=%s", pk_value)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete DynamoDB records: {exc}",
        ) from exc

    # Invalidate the in-memory cache for this project so the list refreshes
    st = _state(project_id)
    with st["lock"]:
        st["timestamp"] = 0.0  # forces next request to re-scan

    # ── Step 4: Re-PUT the object to S3 (overwrites + fires S3 event) ────────
    log.info("Retrigger: re-uploading s3://%s/%s (%d bytes)", bucket, s3_key, len(obj_bytes))
    try:
        s3.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=obj_bytes,
            ContentType=content_type,
        )
    except Exception as exc:
        log.exception("Retrigger: S3 re-upload failed bucket=%s key=%s", bucket, s3_key)
        raise HTTPException(
            status_code=500,
            detail=(
                f"DynamoDB records deleted but S3 re-upload failed: {exc}. "
                f"Manual re-upload required for s3://{bucket}/{s3_key}."
            ),
        ) from exc

    log.info(
        "Retrigger complete: project=%s email_id=%s deleted=%d s3_key=%s",
        project_id, email_id, len(items_to_delete), s3_key,
    )
    return {
        "status": "retriggered",
        "project_id": project_id,
        "email_id": email_id,
        "deleted_count": len(items_to_delete),
        "s3_bucket": bucket,
        "s3_key": s3_key,
    }

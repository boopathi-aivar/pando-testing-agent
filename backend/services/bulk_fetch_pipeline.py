"""
Bulk Ops — read-only fetch pipelines (payload / PDF / logs).

Reuses Retrigger projects (DynamoDB table + S3). For each invoice number:
  1. Scan ATTACHMENT# rows and pick the LATEST email/attachment
  2. Download the requested artifact
  3. Save under bulk_output/<folder_name>/<action>/ on disk
     and optionally upload to destination_bucket/bulk-ops/<folder_name>/...

Actions are read-only (no DynamoDB deletes, no S3 copy-to-self).
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from config import make_source_aws_session
from observability.registry import get_project, log_groups_for_project
from services.observability_logs import (
    build_cloudwatch_url,
    find_cloudwatch_log_stream_detail,
)
from tools.retrigger_tools import (
    append_job_log,
    get_retrigger_job,
    update_job_step,
    update_retrigger_job,
)

BULK_ACTIONS = ("retrigger", "fetch_payload", "fetch_pdf", "fetch_logs")

# Extra log groups to try when the primary Observability group has no hits.
# (JnJ fallbacks live in observability.registry._FALLBACK_LOG_GROUPS)
_EXTRA_LOG_GROUPS: dict[str, list[str]] = {}

# Bulk Ops project_name → Observability registry id (same log groups / PDF rules).
_OBS_PROJECT_ALIASES = {
    "delicato": "delicato",
    "ge": "ge",
    "ge-demo": "ge",
    "jnj": "jnj",
    "jn j": "jnj",
    "meta": "meta",
    "otter": "otter",
    "ghent": "ghent",
    "west marine": "west-marine",
    "west-marine": "west-marine",
    "viking": "viking",
    "unilever": "unilever",
    "unilever logs": "unilever",
    "unilever excel demo": "unilever-excel",
}

_FETCH_STEPS = {
    "fetch_payload": ["resolve_rows", "download_payloads", "save_files"],
    "fetch_pdf": ["resolve_rows", "download_pdfs", "save_files"],
    "fetch_logs": ["resolve_rows", "find_streams", "download_logs", "save_files"],
}


def steps_for_action(action: str) -> dict:
    names = _FETCH_STEPS.get(action) or ["resolve_rows", "save_files"]
    return {n: {"status": "pending"} for n in names}


def _output_root() -> Path:
    # Prefer env; fall back to backend/bulk_output (local) or /tmp (Lambda).
    configured = (os.getenv("BULK_OUTPUT_DIR") or "").strip()
    if configured:
        return Path(configured)
    if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        return Path("/tmp/bulk_output")
    return Path(__file__).resolve().parent.parent / "bulk_output"


def _safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return name.strip("._-") or "item"


def _norm_invoice(value) -> str:
    return str(value or "").strip().upper()


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Not an s3:// URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def _ddb_table(cfg: dict):
    session = make_source_aws_session()
    return session.resource("dynamodb", region_name=cfg.get("aws_region") or "us-east-1").Table(
        cfg["dynamodb_table"]
    )


def _s3_client(cfg: dict):
    return make_source_aws_session().client("s3", region_name=cfg.get("aws_region") or "us-east-1")


def _logs_client(cfg: dict):
    return make_source_aws_session().client("logs", region_name=cfg.get("aws_region") or "us-east-1")


def _scan_attachments_for_invoices(table, invoices: list[str]) -> dict[str, list[dict]]:
    wanted = {_norm_invoice(i) for i in invoices}
    by_inv: dict[str, list[dict]] = defaultdict(list)
    scan_kwargs = {
        "FilterExpression": "begins_with(sk, :sk)",
        "ExpressionAttributeValues": {":sk": "ATTACHMENT#"},
        "ProjectionExpression": (
            "pk, sk, invoice_number, created_at_iso, s3_path, output_path, attachment_id, email_id"
        ),
    }
    while True:
        resp = table.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            inv = _norm_invoice(item.get("invoice_number"))
            if inv in wanted:
                by_inv[inv].append(item)
        last = resp.get("LastEvaluatedKey")
        if not last:
            break
        scan_kwargs["ExclusiveStartKey"] = last
    return by_inv


def _load_metadata_by_pk(table, pks: set[str]) -> dict[str, dict]:
    meta: dict[str, dict] = {}
    for pk in pks:
        if not pk:
            continue
        kwargs = {
            "KeyConditionExpression": "pk = :pk",
            "ExpressionAttributeValues": {":pk": pk},
        }
        while True:
            resp = table.query(**kwargs)
            for it in resp.get("Items", []):
                if str(it.get("sk") or "") == "METADATA":
                    meta[pk] = it
                    break
            last = resp.get("LastEvaluatedKey")
            if not last or pk in meta:
                break
            kwargs["ExclusiveStartKey"] = last
    return meta


def _pick_latest(candidates: list[dict], metadata_by_pk: dict[str, dict]) -> dict:
    def sort_key(item: dict):
        pk = str(item.get("pk") or "")
        meta = metadata_by_pk.get(pk) or {}
        meta_ts = str(meta.get("created_at_iso") or "")
        att_ts = str(item.get("created_at_iso") or "")
        return (meta_ts or att_ts, att_ts, pk)

    return max(candidates, key=sort_key)


def resolve_latest_rows(cfg: dict, invoices: list[str]) -> dict[str, dict | None]:
    table = _ddb_table(cfg)
    by_inv = _scan_attachments_for_invoices(table, invoices)
    pks = {str(it.get("pk") or "") for rows in by_inv.values() for it in rows}
    metadata_by_pk = _load_metadata_by_pk(table, pks)

    chosen: dict[str, dict | None] = {}
    for inv in invoices:
        cands = by_inv.get(_norm_invoice(inv), [])
        chosen[inv] = _pick_latest(cands, metadata_by_pk) if cands else None
    return chosen


def _obs_project_id(cfg: dict) -> str | None:
    """Map Bulk Ops project to Observability project id when possible."""
    explicit = (cfg.get("observability_project_id") or "").strip().lower()
    if explicit and get_project(explicit):
        return explicit
    name = (cfg.get("project_name") or "").strip().lower()
    if name in _OBS_PROJECT_ALIASES:
        return _OBS_PROJECT_ALIASES[name]
    # fuzzy: startswith
    for alias, pid in _OBS_PROJECT_ALIASES.items():
        if name.startswith(alias) or alias.startswith(name):
            return pid
    return None


def _effective_log_group(cfg: dict) -> str:
    """Prefer Observability registry (keeps Bulk Ops in sync with View Log)."""
    obs_id = _obs_project_id(cfg)
    if obs_id:
        groups = log_groups_for_project(obs_id)
        if groups:
            return groups[0]
    return (cfg.get("cloudwatch_log_group") or "").strip()


def _attachment_id_from_row(row: dict | None) -> str:
    if not row:
        return ""
    attachment_id = str(row.get("attachment_id") or "").strip()
    if attachment_id:
        return attachment_id
    sk = str(row.get("sk") or "")
    if sk.startswith("ATTACHMENT#"):
        return sk[len("ATTACHMENT#") :]
    return ""


def _read_bucket_and_key(uri: str) -> tuple[str, str]:
    """Always read from the bucket in the URI (never force project s3_bucket)."""
    return _parse_s3_uri(uri)


def _dest_bucket(cfg: dict, fallback_from_uri: str | None = None) -> str:
    """Bucket used only for optional upload of fetched artifacts."""
    explicit = (cfg.get("destination_bucket") or "").strip()
    if explicit:
        return explicit
    return (cfg.get("s3_bucket") or "").strip()


def _upload_optional(cfg: dict, folder_name: str, action: str, local_path: Path, rel_name: str) -> str | None:
    """Upload artifact to s3://destination_bucket/bulk-ops/<folder>/<action>/<file>."""
    bucket = _dest_bucket(cfg)
    if not bucket:
        return None
    key = f"bulk-ops/{_safe_name(folder_name)}/{action}/{rel_name}"
    try:
        _s3_client(cfg).upload_file(str(local_path), bucket, key)
        return f"s3://{bucket}/{key}"
    except Exception as exc:
        print(f"[BulkOps] S3 upload failed s3://{bucket}/{key}: {exc}")
        return None


def run_bulk_fetch_pipeline(job_id: str, invoice_numbers: list[str], cfg: dict, action: str) -> None:
    if action not in ("fetch_payload", "fetch_pdf", "fetch_logs"):
        raise ValueError(f"Unsupported bulk fetch action: {action}")

    job = get_retrigger_job(job_id)
    if not job:
        print(f"[BulkOps] Job {job_id} not found")
        return

    # Carry project_name so we can map to Observability registry.
    if not cfg.get("project_name"):
        cfg = {**cfg, "project_name": job.get("project_name") or ""}

    folder_name = job.get("folder_name") or job_id
    out_dir = _output_root() / _safe_name(folder_name) / action
    out_dir.mkdir(parents=True, exist_ok=True)

    update_retrigger_job(job_id, {"status": "running", "action": action})
    append_job_log(
        job_id,
        "info",
        f"Starting {action} for {len(invoice_numbers)} invoice(s) → folder '{folder_name}'",
    )

    try:
        update_job_step(job_id, "resolve_rows", "running")
        chosen = resolve_latest_rows(cfg, invoice_numbers)
        found = sum(1 for v in chosen.values() if v)
        update_job_step(job_id, "resolve_rows", "completed", {"matched": found, "total": len(invoice_numbers)})
        append_job_log(job_id, "info", f"Resolved latest rows for {found}/{len(invoice_numbers)} invoice(s)")

        artifacts: list[dict] = []
        ok = fail = 0

        if action == "fetch_pdf":
            update_job_step(job_id, "download_pdfs", "running")
            artifacts, ok, fail = _fetch_pdfs(job_id, chosen, cfg, folder_name, out_dir)
            update_job_step(job_id, "download_pdfs", "completed", {"ok": ok, "fail": fail})
        elif action == "fetch_payload":
            update_job_step(job_id, "download_payloads", "running")
            artifacts, ok, fail = _fetch_payloads(job_id, chosen, cfg, folder_name, out_dir)
            update_job_step(job_id, "download_payloads", "completed", {"ok": ok, "fail": fail})
        else:
            update_job_step(job_id, "find_streams", "running")
            stream_map, resolve_fail = _find_log_streams(job_id, chosen, cfg)
            update_job_step(
                job_id,
                "find_streams",
                "completed",
                {"invoices_with_streams": len(stream_map), "fail": resolve_fail},
            )
            update_job_step(job_id, "download_logs", "running")
            artifacts, ok, fail = _download_logs(job_id, chosen, stream_map, cfg, folder_name, out_dir)
            update_job_step(job_id, "download_logs", "completed", {"ok": ok, "fail": fail})

        update_job_step(job_id, "save_files", "completed", {"count": len(artifacts)})
        update_retrigger_job(
            job_id,
            {
                "status": "completed" if fail == 0 else ("completed" if ok else "failed"),
                "summary": {"ok": ok, "fail": fail, "total": len(invoice_numbers)},
                "artifacts": artifacts,
                "output_dir": str(out_dir),
                "error": None if ok else f"All {fail} invoice(s) failed",
            },
        )
        append_job_log(job_id, "info", f"Done {action}: ok={ok} fail={fail} saved under {out_dir}")
    except Exception as exc:
        update_retrigger_job(job_id, {"status": "failed", "error": str(exc)})
        append_job_log(job_id, "error", f"{action} failed: {exc}")
        print(f"[BulkOps] Job {job_id} failed: {exc}")


def _fetch_pdfs(job_id, chosen, cfg, folder_name, out_dir) -> tuple[list[dict], int, int]:
    """Same idea as Observability View PDF: read bucket/key from s3_path URI."""
    s3 = _s3_client(cfg)
    artifacts = []
    ok = fail = 0
    total = len(chosen)
    for i, (inv, row) in enumerate(chosen.items(), start=1):
        try:
            if not row:
                raise RuntimeError("no ATTACHMENT# row for this invoice")
            s3_path = str(row.get("s3_path") or "").strip()
            if not s3_path:
                raise RuntimeError("row has no s3_path")
            bucket, key = _read_bucket_and_key(s3_path)
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            fname = f"{_safe_name(inv)}.pdf"
            path = out_dir / fname
            path.write_bytes(body)
            s3_uri = _upload_optional(cfg, folder_name, "fetch_pdf", path, fname)
            art = {
                "invoice": inv,
                "status": "OK",
                "filename": fname,
                "local_path": str(path),
                "bytes": len(body),
                "source": f"s3://{bucket}/{key}",
                "s3_uri": s3_uri,
            }
            artifacts.append(art)
            ok += 1
            append_job_log(job_id, "info", f"[{i}/{total}] OK  {inv}  {fname} ({len(body)} bytes)")
        except Exception as exc:
            fail += 1
            artifacts.append({"invoice": inv, "status": "FAIL", "error": str(exc)})
            append_job_log(job_id, "error", f"[{i}/{total}] FAIL  {inv}  {exc}")
    return artifacts, ok, fail


def _fetch_payloads(job_id, chosen, cfg, folder_name, out_dir) -> tuple[list[dict], int, int]:
    s3 = _s3_client(cfg)
    payload_name = (cfg.get("payload_filename") or "api_payload.json").strip() or "api_payload.json"
    artifacts = []
    ok = fail = 0
    total = len(chosen)
    for i, (inv, row) in enumerate(chosen.items(), start=1):
        try:
            if not row:
                raise RuntimeError("no ATTACHMENT# row for this invoice")
            output_path = str(row.get("output_path") or "").strip()
            if not output_path:
                raise RuntimeError("row has no output_path")
            bucket, prefix = _read_bucket_and_key(output_path)
            if prefix and not prefix.endswith("/"):
                prefix += "/"
            key = prefix + payload_name
            raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            try:
                data = json.loads(raw)
                text = json.dumps(data, indent=2, ensure_ascii=False)
            except Exception:
                text = raw.decode("utf-8", errors="replace")
            fname = f"{_safe_name(inv)}.json"
            path = out_dir / fname
            path.write_text(text, encoding="utf-8")
            s3_uri = _upload_optional(cfg, folder_name, "fetch_payload", path, fname)
            artifacts.append(
                {
                    "invoice": inv,
                    "status": "OK",
                    "filename": fname,
                    "local_path": str(path),
                    "bytes": len(text.encode("utf-8")),
                    "source": f"s3://{bucket}/{key}",
                    "s3_uri": s3_uri,
                }
            )
            ok += 1
            append_job_log(job_id, "info", f"[{i}/{total}] OK  {inv}  {fname}")
        except Exception as exc:
            fail += 1
            artifacts.append({"invoice": inv, "status": "FAIL", "error": str(exc)})
            append_job_log(job_id, "error", f"[{i}/{total}] FAIL  {inv}  {exc}")
    return artifacts, ok, fail


def _email_id_from_pk(pk: str) -> str:
    pk = str(pk or "")
    if pk.startswith("EMAIL#"):
        return pk[len("EMAIL#") :]
    return pk


def _find_log_streams(job_id, chosen, cfg) -> tuple[dict[str, list[dict]], int]:
    """
    Same resolution as Observability "View CloudWatch log":
      - filter by attachment_id (then email_id, then invoice#)
      - time window around invoice created_at (± padding)
      - primary + fallback log groups from Observability registry
    Returns stream_map[invoice] = [{"group": ..., "stream": ...}, ...]
    """
    obs_id = _obs_project_id(cfg)
    primary = _effective_log_group(cfg)
    if not primary and not obs_id:
        raise RuntimeError(
            "cloudwatch_log_group is required on the project for Fetch Logs "
            "(same Lambda/Batch group Observability uses)."
        )

    stream_map: dict[str, list[dict]] = {}
    fail = 0
    for inv, row in chosen.items():
        if not row:
            fail += 1
            append_job_log(job_id, "error", f"logs: {inv} — no ATTACHMENT# row")
            continue

        attachment_id = _attachment_id_from_row(row)
        email_id = _email_id_from_pk(row.get("pk") or "") or str(row.get("email_id") or "")
        created_at = row.get("created_at_iso") or row.get("created_at")
        completed_at = row.get("completed_at_iso") or row.get("updated_at_iso")
        invoice_number = str(row.get("invoice_number") or inv)

        group = None
        stream = None
        if obs_id:
            group, stream = find_cloudwatch_log_stream_detail(
                obs_id,
                attachment_id=attachment_id,
                email_id=email_id,
                invoice_number=invoice_number,
                created_at=created_at,
                completed_at=completed_at,
            )
        if not stream and primary:
            stream = _filter_one_stream(
                cfg,
                primary,
                attachment_id=attachment_id,
                email_id=email_id,
                invoice_number=invoice_number,
                created_at=created_at,
                completed_at=completed_at,
            )
            if stream:
                group = primary

        if not stream:
            fail += 1
            tried = ", ".join(log_groups_for_project(obs_id)) if obs_id else primary
            append_job_log(
                job_id,
                "error",
                f"logs: {inv} — no stream in [{tried}] for "
                f"attachment_id={attachment_id or '-'} email_id={email_id or '-'}",
            )
            stream_map[inv] = []
            continue

        stream_map[inv] = [{"group": group or primary, "stream": stream}]
        append_job_log(
            job_id,
            "info",
            f"logs: {inv} — stream={stream} (group={group or primary}, attachment_id={attachment_id})",
        )
    return stream_map, fail


def _filter_one_stream(
    cfg: dict,
    log_group: str,
    *,
    attachment_id: str,
    email_id: str,
    invoice_number: str,
    created_at,
    completed_at,
) -> str | None:
    """FilterLogEvents with Observability-style time window + term priority."""
    from services.observability_logs import _cloudwatch_time_window

    start_ms, end_ms = _cloudwatch_time_window(
        created_at=created_at,
        completed_at=completed_at,
        pad_before_minutes=30,
        pad_after_hours=3,
    )
    if start_ms is None or end_ms is None:
        end_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        start_ms = end_ms - 7 * 24 * 3600 * 1000

    logs = _logs_client(cfg)
    for term in (attachment_id, email_id, invoice_number):
        term = (term or "").strip()
        if not term:
            continue
        try:
            resp = logs.filter_log_events(
                logGroupName=log_group,
                startTime=start_ms,
                endTime=end_ms,
                filterPattern=f'"{term}"',
                limit=20,
                interleaved=True,
            )
        except Exception:
            continue
        counts: dict[str, int] = {}
        for ev in resp.get("events") or []:
            name = ev.get("logStreamName") or ""
            if name:
                counts[name] = counts.get(name, 0) + 1
        if counts:
            return max(counts.items(), key=lambda kv: kv[1])[0]
    return None


def _read_full_stream(logs, log_group: str, stream_name: str) -> list[dict]:
    events = []
    kwargs = {
        "logGroupName": log_group,
        "logStreamName": stream_name,
        "startFromHead": True,
    }
    prev_token = None
    while True:
        resp = logs.get_log_events(**kwargs)
        events.extend(resp.get("events") or [])
        token = resp.get("nextForwardToken")
        if not token or token == prev_token:
            break
        prev_token = token
        kwargs["nextToken"] = token
    return events


def _fmt_event(ev: dict) -> str:
    ts = ev.get("timestamp")
    when = ""
    if ts is not None:
        when = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return f"{when}\t{(ev.get('message') or '').rstrip()}"


def _download_logs(job_id, chosen, stream_map, cfg, folder_name, out_dir) -> tuple[list[dict], int, int]:
    logs = _logs_client(cfg)
    primary_group = _effective_log_group(cfg)
    obs_id = _obs_project_id(cfg)
    artifacts = []
    ok = fail = 0
    total = len(chosen)
    for i, (inv, row) in enumerate(chosen.items(), start=1):
        entries = stream_map.get(inv) or []
        if not entries:
            fail += 1
            if not row:
                err = "no ATTACHMENT# row for this invoice"
            else:
                attachment_id = _attachment_id_from_row(row)
                email_id = _email_id_from_pk(row.get("pk") or "") or str(row.get("email_id") or "")
                cw_url = None
                if obs_id:
                    cw_url = build_cloudwatch_url(
                        obs_id,
                        attachment_id=attachment_id,
                        email_id=email_id,
                        invoice_number=str(row.get("invoice_number") or inv),
                        created_at=row.get("created_at_iso"),
                        completed_at=row.get("completed_at_iso"),
                    )
                tried = ", ".join(log_groups_for_project(obs_id)) if obs_id else primary_group
                err = (
                    f"no CloudWatch streams in [{tried}] for "
                    f"attachment_id={attachment_id or '-'} (same lookup as Observability View Log)"
                )
                if cw_url:
                    fname = f"{_safe_name(inv)}.cloudwatch-url.txt"
                    path = out_dir / fname
                    path.write_text(cw_url + "\n", encoding="utf-8")
                    artifacts.append(
                        {
                            "invoice": inv,
                            "status": "FAIL",
                            "error": err,
                            "filename": fname,
                            "local_path": str(path),
                            "cloudwatch_url": cw_url,
                        }
                    )
                    append_job_log(job_id, "error", f"[{i}/{total}] FAIL  {inv}  {err}")
                    continue
            artifacts.append({"invoice": inv, "status": "FAIL", "error": err})
            append_job_log(job_id, "error", f"[{i}/{total}] FAIL  {inv}  {err}")
            continue
        try:
            pk = str((row or {}).get("pk") or "")
            log_id = _email_id_from_pk(pk)
            attachment_id = _attachment_id_from_row(row)
            # Normalize entries: support legacy list[str] or list[dict]
            normalized = []
            for e in entries:
                if isinstance(e, dict):
                    normalized.append(e)
                else:
                    normalized.append({"group": primary_group, "stream": e})

            lines = [
                f"# invoice: {inv}",
                f"# pk: {pk}",
                f"# attachment_id: {attachment_id}",
                f"# email_id: {log_id}",
                f"# streams: {len(normalized)}",
                "",
            ]
            event_count = 0
            stream_names = []
            for item in normalized:
                group = item.get("group") or primary_group
                sname = item.get("stream") or ""
                if not sname:
                    continue
                stream_names.append(sname)
                events = _read_full_stream(logs, group, sname)
                event_count += len(events)
                lines.append(f"===== log group: {group} =====")
                lines.append(f"===== log stream: {sname} =====")
                lines.extend(_fmt_event(ev) for ev in events)
                lines.append("")
            fname = f"{_safe_name(inv)}.log"
            path = out_dir / fname
            path.write_text("\n".join(lines), encoding="utf-8")
            s3_uri = _upload_optional(cfg, folder_name, "fetch_logs", path, fname)
            artifacts.append(
                {
                    "invoice": inv,
                    "status": "OK",
                    "filename": fname,
                    "local_path": str(path),
                    "bytes": path.stat().st_size,
                    "streams": stream_names,
                    "log_group": normalized[0].get("group") if normalized else primary_group,
                    "events": event_count,
                    "s3_uri": s3_uri,
                }
            )
            ok += 1
            append_job_log(
                job_id,
                "info",
                f"[{i}/{total}] OK  {inv}  streams={len(stream_names)} events={event_count}",
            )
        except Exception as exc:
            fail += 1
            artifacts.append({"invoice": inv, "status": "FAIL", "error": str(exc)})
            append_job_log(job_id, "error", f"[{i}/{total}] FAIL  {inv}  {exc}")
    return artifacts, ok, fail

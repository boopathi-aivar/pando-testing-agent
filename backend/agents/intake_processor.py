"""
Intake Processor
Runs when Lambda pushes data directly via POST /api/intake.

Unlike the regular orchestrator (which pulls from CloudWatch),
this receives payload + LLM response + prompt in the request body,
so it skips the Log Analyzer step and goes straight to validation.

Steps:
  1. Find project  — by project_id, s3_bucket (exact→fuzzy), or log_group
  2. Collect S3 mapping files (field mapping, charge mapping, etc.)
  3. Validate payload using mappings + incoming prompt
  4. Save result to MongoDB
  5. Update project last_tested
"""

import json
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher

from database import tbl_projects
from tools.dynamodb_tools import save_test_result, update_project_last_tested, _from_dynamo
from agents.input_collector import run_input_collector, collect_invoice_pdf_parsed
from agents.scoring_agent import run_scoring_agent


# ── Project resolution ────────────────────────────────────────────────────────

def _find_by_project_id(project_id: str) -> dict | None:
    resp = tbl_projects().get_item(Key={"project_id": project_id})
    item = resp.get("Item")
    return _from_dynamo(item) if item else None


def _find_by_s3_bucket(bucket: str) -> dict | None:
    if not bucket:
        return None
    bucket_lower = bucket.lower()
    all_projects = [_from_dynamo(i) for i in tbl_projects().scan().get("Items", [])]

    # Exact match first
    for proj in all_projects:
        buckets = {(s.get("s3_bucket") or "").lower() for s in proj.get("file_slots", [])}
        if bucket_lower in buckets:
            return proj

    # Fuzzy fallback (threshold 0.75)
    best, best_ratio = None, 0.75
    for proj in all_projects:
        for s in proj.get("file_slots", []):
            b = (s.get("s3_bucket") or "").lower()
            if not b:
                continue
            ratio = SequenceMatcher(None, bucket_lower, b).ratio()
            if ratio > best_ratio:
                best_ratio, best = ratio, proj
    return best


def _find_by_log_group(log_group: str) -> dict | None:
    if not log_group:
        return None
    from boto3.dynamodb.conditions import Attr
    resp = tbl_projects().scan(
        FilterExpression=Attr("cloudwatch_log_group").eq(log_group)
    )
    items = resp.get("Items", [])
    return _from_dynamo(items[0]) if items else None


def resolve_project(project_id: str | None, s3_bucket: str | None, log_group: str | None) -> dict | None:
    """
    Try each routing key in priority order:
      1. project_id  (exact, fastest)
      2. s3_bucket   (exact then fuzzy against file_slots)
      3. log_group   (exact against cloudwatch_log_group)
    """
    if project_id:
        proj = _find_by_project_id(project_id)
        if proj:
            return proj

    if s3_bucket:
        proj = _find_by_s3_bucket(s3_bucket)
        if proj:
            return proj

    if log_group:
        proj = _find_by_log_group(log_group)
        if proj:
            return proj

    return None


# ── Vendor name extraction ────────────────────────────────────────────────────

def _extract_vendor_name(payload: dict) -> str | None:
    # Top-level fields
    for field in ("vendor_name", "carrier", "carrier_name"):
        val = payload.get(field)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    # Real Pando format: vendor_name inside payload.custom
    custom = payload.get("custom") or {}
    if isinstance(custom, dict):
        val = custom.get("vendor_name")
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    return None


# ── Main processor ────────────────────────────────────────────────────────────

def process_intake(intake_data: dict) -> dict:
    """
    Full validation pipeline driven by intake data (no CloudWatch calls).
    intake_data keys: project_id, s3_bucket, log_group, invoice_number,
                      payload, llm_response, prompt, execution_duration_ms,
                      cold_start, errors, warnings
    Returns the saved TestResult dict or an error dict.
    """
    project = resolve_project(
        intake_data.get("project_id"),
        intake_data.get("s3_bucket"),
        intake_data.get("log_group"),
    )

    if not project:
        return {
            "error": "Could not match intake data to any configured project. "
                     "Provide project_id, s3_bucket, or log_group.",
            "received_project_id": intake_data.get("project_id"),
            "received_s3_bucket":  intake_data.get("s3_bucket"),
        }

    project_id = project["project_id"]
    print(f"[Intake] Matched project: {project_id}")

    api_status  = intake_data.get("api_status")
    vendor_name = _extract_vendor_name(intake_data.get("payload", {}))
    result_id   = intake_data.get("reuse_result_id") or f"result-{project_id}-{uuid.uuid4().hex[:8]}"
    invoice_num = intake_data.get("invoice_number", "unknown")

    log_analysis = {
        "invoice_number":        invoice_num,
        "payload":               intake_data.get("payload", {}),
        "llm_response":          intake_data.get("llm_response", {}),
        "errors":                intake_data.get("errors", []),
        "warnings":              intake_data.get("warnings", []),
        "execution_duration_ms": intake_data.get("execution_duration_ms", 0),
        "cold_start":            intake_data.get("cold_start", False),
        "api_status":            api_status,
    }

    # Always parse the invoice PDF. A Lambda API error must not skip OCR /
    # expected extraction — we still need content-based expected values.
    payload       = intake_data.get("payload", {})
    vendor_ref_id = payload.get("vendor_reference_id", "") or payload.get("vendor_ref_id", "")
    print(f"[Intake] Fetching mapping files from S3 (vendor={vendor_ref_id or 'unknown'})…")
    input_files = run_input_collector(project, vendor_ref_id=vendor_ref_id)

    if intake_data.get("prompt"):
        input_files.setdefault("collected", {})["prompt-template"] = intake_data["prompt"]

    print("[Intake] Fetching invoice PDF from S3…")
    parsed_pdf = collect_invoice_pdf_parsed(payload)
    log_analysis["invoice_pdf"] = parsed_pdf.get("markdown") or ""
    log_analysis["invoice_pdf_layout"] = parsed_pdf.get("pages") or []

    print("[Intake] Running scoring agent (actual vs expected from PDF)…")
    validation = run_scoring_agent(project, input_files, log_analysis)

    api_failed = False
    try:
        api_failed = api_status is not None and int(api_status) >= 400
    except (TypeError, ValueError):
        api_failed = False

    if api_failed:
        print(f"[Intake] API error {api_status} — keeping parsed field checks, marking failed.")
        errors = log_analysis["errors"] or [f"Lambda API returned HTTP {api_status}"]
        suggestions = validation.get("suggestions") or []
        suggestions = [f"Fix the API error (HTTP {api_status}) before trusting the payload."] + list(suggestions)

    test_result = {
        "result_id":              result_id,
        "project_id":             project_id,
        "vendor_name":            vendor_name,
        "invoice_number":         invoice_num,
        "timestamp":              datetime.now(tz=timezone.utc).isoformat(),
        "overall_score":          0.0 if api_failed else validation.get("overall_score", 0.0),
        "status":                 "failed" if api_failed else validation.get("status", "failed"),
        "api_status":             api_status,
        "field_validations":      validation.get("field_validations", []),
        "prompt_suggestions":     suggestions if api_failed else validation.get("suggestions", []),
        "mandatory_fields_result": validation.get("mandatory_fields_result", {"total": 0, "passed": 0, "failed": 0, "failed_fields": []}),
        "log_summary": {
            "errors":                errors if api_failed else log_analysis["errors"],
            "warnings":              log_analysis["warnings"],
            "execution_duration_ms": log_analysis["execution_duration_ms"],
            "cold_start":            log_analysis["cold_start"],
        },
        "raw_payload":       log_analysis["payload"],
        "expected_from_pdf": validation.get("expected_from_pdf", {}),
        "source":            intake_data.get("source") or "lambda_push",
    }

    # Step 4 — persist
    print("[Intake] Saving result to DynamoDB…")
    save_test_result(project_id, json.dumps(test_result, default=str))
    update_project_last_tested(project_id, test_result["overall_score"], test_result["status"])

    print(f"[Intake] Done — {project_id} | {vendor_name} | "
          f"{test_result['overall_score']}% ({test_result['status']})")
    return test_result


def retest_from_stored_result(result_id: str) -> dict:
    """
    Re-score a previously saved invoice using its stored raw_payload + PDF.
    Does not depend on CloudWatch still having the original logs.
    """
    from tools.dynamodb_tools import get_result_by_id

    stored = get_result_by_id(result_id)
    if not stored:
        return {"error": f"Result '{result_id}' not found"}

    payload = stored.get("raw_payload") or {}
    if not isinstance(payload, dict) or not payload:
        return {"error": f"Result '{result_id}' has no raw_payload to retest"}

    log_summary = stored.get("log_summary") or {}
    if not isinstance(log_summary, dict):
        log_summary = {}

    print(f"[Intake] Retesting stored result {result_id} "
          f"(invoice={stored.get('invoice_number')}) — overwriting same DynamoDB item")
    return process_intake({
        "project_id":            stored.get("project_id"),
        "invoice_number":        stored.get("invoice_number"),
        "payload":               payload,
        "api_status":           stored.get("api_status"),
        "errors":                log_summary.get("errors") or [],
        "warnings":              log_summary.get("warnings") or [],
        "execution_duration_ms": log_summary.get("execution_duration_ms", 0),
        "cold_start":            log_summary.get("cold_start", False),
        "source":                "retest",
        "reuse_result_id":       result_id,
    })

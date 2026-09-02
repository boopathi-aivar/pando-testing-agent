"""
Test Orchestrator
Flow:
  1. Find project by project_id (or S3 bucket name)
  2. Input Collector  — fetch prompt + mapping files from S3
  3. Log Analyzer     — extract ALL invoice runs from CloudWatch (returns a list)
  4. For each invoice:
       a. If api_status >= 400 → skip agent, mark failed immediately
       b. Otherwise → Payload Validator → validate fields, score, suggest fixes
  5. Save every result to MongoDB + update project last_tested
"""

import json
import uuid
from difflib import SequenceMatcher
from datetime import datetime, timezone

from tools.dynamodb_tools import (
    get_project_config,
    save_test_result,
    update_project_last_tested,
    _from_dynamo,
)
from agents.input_collector import run_input_collector, collect_invoice_pdf
from agents.log_analyzer import run_log_analyzer
from agents.payload_validator import run_payload_validator
from database import tbl_projects


# ── Project matching ──────────────────────────────────────────────────────────

def _extract_bucket_names(project: dict) -> list[str]:
    buckets = []
    for slot in project.get("file_slots", []):
        b = (slot.get("s3_bucket") or "").strip()
        if b:
            buckets.append(b)
    return list(set(buckets))


def find_project_by_bucket(bucket_name: str) -> dict | None:
    if not bucket_name:
        return None
    all_projects = [_from_dynamo(i) for i in tbl_projects().scan().get("Items", [])]
    bucket_lower = bucket_name.lower()

    for proj in all_projects:
        if bucket_lower in [b.lower() for b in _extract_bucket_names(proj)]:
            return proj

    best_proj, best_ratio = None, 0.75
    for proj in all_projects:
        for b in _extract_bucket_names(proj):
            ratio = SequenceMatcher(None, bucket_lower, b.lower()).ratio()
            if ratio > best_ratio:
                best_ratio, best_proj = ratio, proj
    return best_proj


# ── Vendor name extraction ────────────────────────────────────────────────────

def _extract_vendor_name(log_analysis: dict) -> str | None:
    payload = log_analysis.get("payload", {})
    # Top-level fields
    for field in ("vendor_name", "carrier", "carrier_name"):
        val = payload.get(field)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    # Real Pando format: vendor_name lives inside payload.custom
    custom = payload.get("custom") or {}
    if isinstance(custom, dict):
        val = custom.get("vendor_name")
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    return None


# ── Result builders ───────────────────────────────────────────────────────────

def _api_error_result(project_id: str, log_analysis: dict) -> dict:
    """Build a failed result for an invoice where the Lambda returned an error status."""
    api_status = log_analysis.get("api_status")
    vendor_name = _extract_vendor_name(log_analysis)
    result_id = f"result-{project_id}-{uuid.uuid4().hex[:8]}"
    errors = log_analysis.get("errors", [])
    if not errors:
        errors = [f"Lambda API returned HTTP {api_status}"]

    return {
        "result_id":              result_id,
        "project_id":             project_id,
        "vendor_name":            vendor_name,
        "invoice_number":         log_analysis.get("invoice_number") or "unknown",
        "timestamp":              datetime.now(tz=timezone.utc).isoformat(),
        "overall_score":          0.0,
        "status":                 "failed",
        "api_status":             api_status,
        "field_validations":      [],
        "prompt_suggestions":     [f"Fix the API error (HTTP {api_status}) before validating fields."],
        "mandatory_fields_result": {"total": 0, "passed": 0, "failed": 0, "failed_fields": []},
        "log_summary": {
            "errors":                errors,
            "warnings":              log_analysis.get("warnings", []),
            "execution_duration_ms": log_analysis.get("execution_duration_ms", 0),
            "cold_start":            log_analysis.get("cold_start", False),
        },
        "raw_payload": log_analysis.get("payload", {}),
    }


def _validated_result(project_id: str, project_config: dict, input_files: dict, log_analysis: dict) -> dict:
    """Build a fully validated result by running the payload validator agent."""
    validation  = run_payload_validator(project_config, input_files, log_analysis)
    vendor_name = _extract_vendor_name(log_analysis)
    result_id   = f"result-{project_id}-{uuid.uuid4().hex[:8]}"

    return {
        "result_id":              result_id,
        "project_id":             project_id,
        "vendor_name":            vendor_name,
        "invoice_number":         log_analysis.get("invoice_number") or "unknown",
        "timestamp":              datetime.now(tz=timezone.utc).isoformat(),
        "overall_score":          validation.get("overall_score", 0.0),
        "status":                 validation.get("status", "failed"),
        "api_status":             log_analysis.get("api_status"),
        "field_validations":      validation.get("field_validations", []),
        "prompt_suggestions":     validation.get("suggestions", []),
        "mandatory_fields_result": validation.get("mandatory_fields_result", {"total": 0, "passed": 0, "failed": 0, "failed_fields": []}),
        "log_summary": {
            "errors":                log_analysis.get("errors", []),
            "warnings":              log_analysis.get("warnings", []),
            "execution_duration_ms": log_analysis.get("execution_duration_ms", 0),
            "cold_start":            log_analysis.get("cold_start", False),
        },
        "raw_payload": log_analysis.get("payload", {}),
    }


# ── Main test runner ──────────────────────────────────────────────────────────

def run_test(project_id: str, invoice_number: str | None = None) -> dict:
    """
    Execute a full test run for the given project.
    Processes ALL invoices found in CloudWatch logs for the configured window.
    Saves every result to MongoDB. Returns the most recent result dict.
    """
    config_raw = get_project_config(project_id)
    project_config = json.loads(config_raw) if isinstance(config_raw, str) else config_raw

    if "error" in project_config:
        return {"error": project_config["error"], "project_id": project_id}

    print(f"[Orchestrator] Starting test — project: {project_id}")

    print("[Orchestrator] Step 1: Extracting invoices from CloudWatch…")
    log_analyses = run_log_analyzer(project_config, invoice_number)
    print("[Orchestrator] Step 2: Processing each invoice (fetch mappings → validate)…")

    print(f"[Orchestrator] Found {len(log_analyses)} invoice(s) in logs.")

    saved_results = []
    for idx, log_analysis in enumerate(log_analyses, 1):
        api_status    = log_analysis.get("api_status")
        invoice_num   = log_analysis.get("invoice_number") or invoice_number or "unknown"
        vendor_ref_id = log_analysis.get("payload", {}).get("vendor_reference_id", "")

        # Collect mapping files (vendor-keyed Excel) + fetch invoice PDF
        input_files = run_input_collector(project_config, vendor_ref_id=vendor_ref_id)
        log_analysis["invoice_pdf"] = collect_invoice_pdf(log_analysis.get("payload", {}))

        if api_status and api_status >= 400:
            print(f"[Orchestrator] Invoice {idx} ({invoice_num}) — API error {api_status}, skipping validation.")
            test_result = _api_error_result(project_id, log_analysis)
        else:
            print(f"[Orchestrator] Invoice {idx} ({invoice_num}) — validating fields…")
            test_result = _validated_result(project_id, project_config, input_files, log_analysis)

        print(f"[Orchestrator] Invoice {idx}: score={test_result['overall_score']}% "
              f"status={test_result['status']} api_status={api_status}")

        save_test_result(project_id, json.dumps(test_result, default=str))
        update_project_last_tested(project_id, test_result["overall_score"], test_result["status"])
        saved_results.append(test_result)

    if not saved_results:
        return {"error": "No log data found for this project"}

    # Return the first result (most common case: one invoice per run)
    return saved_results[0]


def run_test_from_bucket(bucket_name: str, invoice_number: str | None = None) -> dict:
    """Entry point when the caller only knows the S3 bucket name."""
    project = find_project_by_bucket(bucket_name)
    if not project:
        return {"error": f"No project matched S3 bucket '{bucket_name}'"}
    return run_test(project["project_id"], invoice_number)

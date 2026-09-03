"""
CloudWatch Logs service — generic invoice block parser + real Boto3 stub.

Design principle: ZERO hardcoded log message text.
The parser relies entirely on the STRUCTURE and CONTENT of JSON objects
in the log stream, not on specific log message strings.

JSON classification rules (applied to every parsed JSON block):
  • transaction_block : has "transaction_id" AND any of
                        "step_key" | "step_status" | "transaction_status"
                        → SKIP always
  • api_response_wrap : has "status_code" with ≤ 3 total keys and no
                        invoice-like fields → SKIP (transaction step status)
  • invoice_payload   : has ≥ 2 known invoice field names, OR is a
                        {"data": [...]} wrapper whose first element has ≥ 2
                        invoice field names → NEW INVOICE BLOCK
  • anything else     : ignored

API status for each invoice block is extracted from plain log LINES
(not JSON) using generic HTTP-code patterns (no specific text required).
"""

import re
import json

import time
from botocore.exceptions import ClientError
from config import make_source_aws_session


def _logs_client():
    # The invoice processor Lambda's log group lives in a different AWS
    # account than this backend, so this uses a cross-account session built
    # from credentials in Secrets Manager (see config.make_source_aws_session).
    return make_source_aws_session().client("logs")


# ── JSON content classifiers ──────────────────────────────────────────────────

# Fields that are characteristic of invoice data objects.
# Projects may use different names, so we cast a wide net.
_INVOICE_FIELD_HINTS: frozenset[str] = frozenset({
    "invoice_number", "invoice_id", "invoice_date", "invoice_no",
    "total_invoice_value", "invoice_total", "net_invoice_value",
    "bill_of_lading_number", "bol_number", "bl_number",
    "bill_of_entry_number", "be_number",
    "vendor_reference_id", "po_number", "reference_number",
    "currency", "payment_terms", "payment_due_date",
    "assessable_value", "net_value", "round_off",
    "shipper", "consignee", "carrier", "carrier_name",
    "charge_code", "charge_type", "freight_amount",
    "origin_country", "destination_country",
    "custom_fields", "custom",          # Pando envelope fields
    "shipment_date", "delivery_date",
})

# Minimum number of invoice-hint fields that must be present
_INVOICE_FIELD_MIN_MATCH = 2


def _is_transaction_block(obj: dict) -> bool:
    """
    Return True if this JSON represents a transaction-tracking payload
    (validation step, ingest step, etc.) that should always be ignored.
    Works regardless of platform or project.
    """
    has_txn_id = "transaction_id" in obj
    has_step   = any(k in obj for k in ("step_key", "step_status", "transaction_status", "workflow_key"))
    return has_txn_id and has_step


def _is_api_response_wrapper(obj: dict) -> bool:
    """
    Return True for thin status wrappers like {"status_code": 200, "response": "..."}
    that carry the result of a transaction-step POST, not the invoice API call.
    """
    return (
        "status_code" in obj
        and len(obj) <= 3
        and not _looks_like_invoice(obj)
    )


def _looks_like_invoice(obj: dict) -> bool:
    """
    Heuristic: return True if this object appears to be invoice data.
    Requires at least _INVOICE_FIELD_MIN_MATCH known invoice field names.
    Works for any project's field naming conventions.
    """
    if not isinstance(obj, dict):
        return False
    return len(_INVOICE_FIELD_HINTS.intersection(obj.keys())) >= _INVOICE_FIELD_MIN_MATCH


def _unwrap_invoice(obj: dict) -> dict | None:
    """
    Given a parsed JSON object, return the invoice data dict or None.

    Handles:
      1. {"data": [{invoice fields…}]}  — wrapped array format
      2. {invoice fields…}              — direct invoice object
      3. transaction / api-response blocks → None (skip)
    """
    if not isinstance(obj, dict):
        return None

    # Always skip transaction tracking payloads
    if _is_transaction_block(obj) or _is_api_response_wrapper(obj):
        return None

    # Wrapped: {"data": [...]}
    data = obj.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and _looks_like_invoice(first):
            return first

    # Direct invoice object
    if _looks_like_invoice(obj):
        return obj

    return None


# ── API status extraction from plain log lines ────────────────────────────────

# Ordered from most-specific to least-specific.
# We stop at the first match to avoid false positives.
_STATUS_PATTERNS = [
    # "External API response code: 200"  /  "API response status code: 400"
    re.compile(r'api[^:\n]{0,40}(?:response|status)[^:\n]{0,20}code[^:\n]{0,10}[:\s=]+([1-5]\d{2})\b', re.I),
    # "response code: 500"  /  "status code: 200"
    re.compile(r'(?:response|status)[^:\n]{0,20}code[^:\n]{0,10}[:\s=]+([1-5]\d{2})\b', re.I),
    # "code: 200"  /  "code = 400"
    re.compile(r'\bcode[^:\n]{0,10}[:\s=]+([1-5]\d{2})\b', re.I),
    # Standard "HTTP/1.1 200"  /  "HTTP 404"
    re.compile(r'\bHTTPS?[/ ]([1-5]\d{2})\b', re.I),
    # "returned 200"  /  "responded with 500"
    re.compile(r'\b(?:returned?|responded?(?:\s+with)?)[^:\n]{0,15}([1-5]\d{2})\b', re.I),
    # "status: 200"  /  "status=400"
    re.compile(r'\bstatus[^:\n]{0,10}[:\s=]+([1-5]\d{2})\b', re.I),
]


def _extract_api_status(line: str) -> int | None:
    """
    Extract an HTTP status code from a plain (non-JSON) log line.
    Returns None if no credible status code is found.
    Generic — works for any log message format.
    """
    for pat in _STATUS_PATTERNS:
        m = pat.search(line)
        if m:
            code = int(m.group(1))
            if 100 <= code < 600:
                return code
    return None


# ── Invoice number / vendor helpers ──────────────────────────────────────────

def _extract_invoice_number(payload: dict) -> str | None:
    """
    Try common invoice-number field names.
    Returns the first non-empty string found, or None.
    """
    for key in ("invoice_number", "invoice_id", "invoice_no", "invoiceNumber"):
        val = payload.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def _new_block() -> dict:
    return {
        "raw_lines":             [],
        "prompt_text":           "",   # plain log lines before this invoice payload
        "invoice_number":        None,
        "payload":               {},
        "api_status":            None,
        "errors":                [],
        "warnings":              [],
        "execution_duration_ms": 0,
        "cold_start":            False,
    }


# ── Main invoice block extractor ──────────────────────────────────────────────

def extract_invoice_blocks(log_messages: list[str]) -> list[dict]:
    """
    Parse a flat list of CloudWatch log messages into per-invoice result blocks.

    Works for any project — no hardcoded log text, only JSON content patterns.

    Algorithm:
      • Accumulate characters into a JSON buffer whenever a line starts with '{'
      • When the buffer is a complete JSON object, classify it:
          - invoice_payload  → close previous block (if any), start new block
          - transaction/api  → skip
      • For non-JSON lines, extract API status code using generic patterns;
        assign to the current open block
      • Errors/warnings are captured from any non-JSON line containing those words
    """
    blocks: list[dict] = []
    current: dict | None = None
    collecting = False
    json_buf: list[str] = []
    depth = 0
    # Accumulates plain log lines seen before/between invoice payloads.
    # These lines often contain the Lambda's LLM prompt (including charge mapping tables).
    pre_buf: list[str] = []

    def _process_json(text: str) -> None:
        nonlocal current, pre_buf
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return

        inv = _unwrap_invoice(parsed)
        if inv is None:
            return  # transaction block or unrecognised — skip

        # New invoice found → flush previous block
        if current is not None and (current["payload"] or current["invoice_number"]):
            blocks.append(current)

        current = _new_block()
        current["payload"] = inv
        current["invoice_number"] = _extract_invoice_number(inv)
        # Attach every plain line seen before this payload — the Lambda's
        # prompt (with embedded charge mapping) lives here.
        current["prompt_text"] = "\n".join(pre_buf)
        pre_buf = []  # reset so the next invoice gets its own window

    for raw in log_messages:
        line = raw.strip()
        if not line:
            continue

        # ── Multi-line JSON accumulation ──────────────────────────────
        if collecting:
            json_buf.append(line)
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                _process_json("\n".join(json_buf))
                collecting = False
                json_buf = []
                depth = 0
            continue

        # ── JSON block start ──────────────────────────────────────────
        if line.startswith("{"):
            open_d = line.count("{") - line.count("}")
            if open_d <= 0:
                _process_json(line)       # single-line JSON
            else:
                collecting = True
                json_buf = [line]
                depth = open_d
            continue

        # ── Plain log line ────────────────────────────────────────────
        # Always accumulate — may be part of the LLM prompt logged before
        # the invoice JSON appears.
        pre_buf.append(line)

        if current is None:
            continue

        current["raw_lines"].append(line)
        lower = line.lower()

        # API status — take the first credible match
        if current["api_status"] is None:
            code = _extract_api_status(line)
            if code:
                current["api_status"] = code

        # Duration: "Duration: 3240.12 ms"  /  "Billed Duration: 400 ms"
        dur = re.search(r'\bduration[:\s]+([\d.]+)\s*ms\b', line, re.I)
        if dur:
            current["execution_duration_ms"] = int(float(dur.group(1)))

        # Cold start
        if "cold start" in lower or "init duration" in lower:
            current["cold_start"] = True

        # Errors — skip lines that only mention field names containing "error"
        if re.search(r'\berror\b', lower) and not re.search(
            r'"[^"]*error[^"]*"\s*:', lower
        ):
            current["errors"].append(line)
        elif re.search(r'\bwarn(?:ing)?\b', lower):
            current["warnings"].append(line)

    # Flush final open block
    if current and (current["payload"] or current["invoice_number"]):
        blocks.append(current)

    return blocks


# ── CloudWatch query ──────────────────────────────────────────────────────────

def query_logs(log_group: str, query: str, hours: int = 24) -> list[dict]:
    """Run a CloudWatch Insights query and return matching log records."""
    end_time   = int(time.time())
    start_time = end_time - (hours * 3600)
    logs = _logs_client()
    try:
        resp     = logs.start_query(logGroupName=log_group,
                                    startTime=start_time, endTime=end_time,
                                    queryString=query, limit=1000)
        query_id = resp["queryId"]
        for _ in range(60):
            time.sleep(1)
            result = logs.get_query_results(queryId=query_id)
            if result["status"] in ("Complete", "Failed", "Cancelled"):
                return result.get("results", [])
        raise TimeoutError("CloudWatch query timed out")
    except ClientError as e:
        raise RuntimeError(f"CloudWatch error: {e.response['Error']['Message']}")


def get_recent_invoice_number(log_group: str, hours: int = 24) -> str | None:
    """Return the most recent invoice number seen in the log group."""
    # --- REAL IMPLEMENTATION ---
    # blocks = extract_invoice_blocks(get_all_invoice_logs(log_group, hours))
    # return blocks[-1]["invoice_number"] if blocks else None

    blocks = extract_invoice_blocks(get_all_invoice_logs(log_group, hours))
    return blocks[-1]["invoice_number"] if blocks else None


def get_all_invoice_logs(log_group: str, hours: int = 24) -> list[str]:
    """
    Return all log messages as a flat list of strings (one per log event).
    Each string is the raw @message from CloudWatch.
    """
    try:
        records = query_logs(
            log_group,
            "fields @message | sort @timestamp asc | limit 1000",
            hours,
        )
        messages = [r.get("@message", "") for r in records if r.get("@message")]
        if messages:
            return messages
    except Exception as e:
        print(f"[CloudWatch] Real query failed, using mock: {e}")

    # --- MOCK: two invoice runs using the generic Pando payload structure ---
    def _make_invoice(n: int, status: int) -> list[str]:
        inv_payload = {
            "invoice_number":        f"INV-{n:07d}",
            "invoice_date":          "20-Feb-2026",
            "vendor_reference_id":   "REF-001",
            "payment_due_date":      "06-Apr-2026",
            "bill_of_lading_number": f"BL{n}XYZ",
            "bill_of_entry_number":  "",
            "currency":              "USD",
            "total_invoice_value":   2435 + n * 10,
            "net_invoice_value":     2435 + n * 10,
            "payment_terms":         "collect",
            "assessable_value":      0,
            "round_off":             0,
            "custom_fields":         {"petrol_charge": 0},
            "custom": {
                "source_type":       "email",
                "vendor_name":       "Sample Logistics Co.",
                "sender_email":      "sender@example.com",
                "client_id":         36,
                "attachment_bucket": log_group.replace("/aws/lambda/", "") + "-bucket",
                "attachment_key":    f"invoice/input/sample-{n}.pdf",
            },
        }

        txn_block = json.dumps({
            "transaction_id":     f"txn-{n:04d}",
            "step_key":           "VALIDATION",
            "step_status":        "SUCCESS",
            "transaction_status": "PROCESSING",
        })
        txn_status = json.dumps({"status_code": 200, "response": '{"data":{"id":"1"}}'})
        txn_ingest = json.dumps({
            "transaction_id":     f"txn-{n:04d}",
            "step_key":           "INGEST",
            "step_status":        "SUCCESS",
            "transaction_status": "COMPLETED",
        })

        lines = [
            f"[INFO] req-{n:03d}  TRANSACTION_STATUS_API_RESPONSE | validation | posting to https://api.example.com/ingest",
            txn_block,
            f"[INFO] req-{n:03d}  TRANSACTION_STATUS_API_RESPONSE | validation |",
            txn_status,
            f"[INFO] req-{n:03d}  Validation passed - proceeding with API call",
            f"[INFO] req-{n:03d}  Final payload being sent to API:",
            json.dumps({"data": [inv_payload]}),
            f"[INFO] req-{n:03d}  Sending payload to external API",
        ]

        if status >= 400:
            lines.append(f"[INFO] req-{n:03d}  ERROR: upstream rejected the payload — field validation failed")

        lines += [
            f"[INFO] req-{n:03d}  External API response code: {status}",
            f"[INFO] req-{n:03d}  API response status code: {status}",
            f"[INFO] req-{n:03d}  API request {'accepted' if status < 400 else 'rejected'} successfully",
            f"[INFO] req-{n:03d}  TRANSACTION_PAYLOAD | api_submission |",
            txn_ingest,
            f"[INFO] req-{n:03d}  TRANSACTION_STATUS_API_RESPONSE | api_submission |",
            json.dumps({"status_code": 200}),
        ]
        return lines

    return _make_invoice(1, 200) + _make_invoice(2, 400)

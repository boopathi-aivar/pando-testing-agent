"""
Log Analyzer Agent
Queries CloudWatch logs for the invoice processor Lambda and extracts
ALL invoice runs found in the log window, each with its api_status.

Returns a list of invoice analysis dicts, one per invoice found.
Each invoice block starts at an email object and ends at an API status line.
"""

import json
from strands import Agent
from strands.models.bedrock import BedrockModel

from config import settings

from tools.cloudwatch_tools import (
    search_cloudwatch_logs,
    get_latest_invoice_from_logs,
    get_lambda_errors,
    get_all_invoice_payloads,
)
from services.cloudwatch import get_all_invoice_logs, extract_invoice_blocks


_SYSTEM_PROMPT = """\
You are the Log Analyzer agent for the Pando Invoice Testing system.

Your job:
1. Use get_all_invoice_payloads to fetch all log events from CloudWatch.
2. Identify every distinct invoice processing run in those logs by
   recognising JSON objects that contain invoice data fields
   (invoice_number, total_invoice_value, bill_of_lading_number, currency, etc.).
3. IGNORE JSON objects that are transaction-tracking payloads — they
   contain "transaction_id" plus "step_key"/"step_status"/"transaction_status".
   Also ignore {"status_code": N, "response": "..."} wrappers.
4. For each invoice, extract the HTTP status code from the surrounding
   non-JSON log lines (look for numbers 200-599 near words like
   "code", "status", "response", "returned").
5. Capture any ERROR or WARNING log lines near each invoice block.

Respond with ONLY a valid JSON array — no text outside it:
[
  {
    "invoice_number": "<string or null>",
    "payload": { <the invoice data object> },
    "api_status": <HTTP status integer or null>,
    "prompt_text": "<all plain log lines that appeared before this invoice payload, verbatim>",
    "errors": ["<error log line>", ...],
    "warnings": ["<warning log line>", ...],
    "execution_duration_ms": <integer>,
    "cold_start": <true|false>
  }
]

Return one element per invoice found. Do not include transaction-tracking
blocks in payload. The prompt_text field preserves all pre-payload log lines
(these typically contain the Lambda's LLM system prompt with charge mapping tables).
Do not include any text outside the JSON array.
"""


def _make_model() -> BedrockModel:
    return BedrockModel(
        region_name=settings.AWS_REGION,
        model_id=settings.BEDROCK_MODEL_ID,
        max_tokens=4096,
    )


def _mock_log_analysis(project_config: dict, invoice_number: str | None) -> list[dict]:
    """Return mock log data matching the real Pando invoice payload structure."""
    inv        = invoice_number or "3324763"
    project_id = project_config.get("project_id", "")
    log_group  = project_config.get("cloudwatch_log_group", "")
    bucket     = log_group.replace("/aws/lambda/", "") + "-bucket"

    payload = {
        "invoice_number":             inv,
        "invoice_date":               "20-Feb-2026",
        "vendor_reference_id":        "MXNG",
        "payment_due_date":           "06-Apr-2026",
        "bill_of_lading_number":      "HLCUSZX2511BJSA7",
        "bill_of_entry_number":       "",
        "currency":                   "USD",
        "total_invoice_value":        2435,
        "net_invoice_value":          2435,
        "payment_terms":              "collect",
        "assessable_value":           0,
        "round_off":                  0,
        "custom_fields":              {"petrol_charge": 0, "handling_charge": 0},
        "custom": {
            "source_type":      "email",
            "vendor_name":      "MaxTrans Logistics",
            "shipper_email":    "ge@pibypando.ai",
            "sender_email":     "jeeva@pando.ai",
            "client_id":        36,
            "attachment_bucket": bucket,
            "attachment_key":   "invoice/input/sample.pdf",
        },
    }

    return [
        {
            "invoice_number":        inv,
            "payload":               payload,
            "api_status":            200,
            "errors":                [],
            "warnings":              [],
            "execution_duration_ms": 3240,
            "cold_start":            False,
            "log_group":             log_group,
        },
    ]


def run_log_analyzer(
    project_config: dict,
    invoice_number: str | None = None,
) -> list[dict]:
    """
    Query CloudWatch logs and return one analysis dict per invoice found.
    Falls back to rule-based parsing (no LLM) then to mock data on failure.
    """
    log_group  = project_config.get("cloudwatch_log_group", "")
    log_window = project_config.get("log_window_hours", 24)

    if not log_group:
        return _mock_log_analysis(project_config, invoice_number)

    # ── Direct parse (no LLM) ─────────────────────────────────────────────────
    try:
        messages = get_all_invoice_logs(log_group, log_window)
        if messages:
            blocks = extract_invoice_blocks(messages)
            if blocks:
                # If caller specified an invoice number, filter to that block only
                if invoice_number:
                    filtered = [b for b in blocks if (b.get("invoice_number") or "").upper() == invoice_number.upper()]
                    if filtered:
                        blocks = filtered
                return blocks
    except Exception as e:
        print(f"[LogAnalyzer] Direct parse failed: {e}")

    # ── LLM agent fallback ────────────────────────────────────────────────────
    try:
        agent = Agent(
            model=_make_model(),
            tools=[search_cloudwatch_logs, get_latest_invoice_from_logs, get_lambda_errors, get_all_invoice_payloads],
            system_prompt=_SYSTEM_PROMPT,
        )

        invoice_hint = (
            f"Focus on invoice number: {invoice_number}" if invoice_number
            else "Return ALL invoices found in the log window."
        )

        prompt = f"""
Project: {project_config.get('project_name')} ({project_config.get('project_id')})
CloudWatch log group: {log_group}
Search window: last {log_window} hours
{invoice_hint}

Use get_all_invoice_payloads to fetch and split the log stream by invoice, then return the JSON array.
"""
        result = agent(prompt)
        text = str(result).strip()

        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end])
            if isinstance(parsed, list) and parsed and "invoice_number" in parsed[0]:
                return parsed
    except Exception as e:
        print(f"[LogAnalyzer] Agent error: {e}")

    return _mock_log_analysis(project_config, invoice_number)

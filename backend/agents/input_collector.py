"""
Input Collector
Fetches all enabled S3 file slots for a project.

Excel workbooks (.xlsx / .xls) are parsed into structured field_mapping +
charge_mapping JSON keyed by vendor_ref_id — no LLM required.
Plain text / CSV files are returned as-is.
"""

import json
from services.s3 import get_object, get_binary, object_exists
from services.excel_parser import parse_field_mapping, parse_charge_mapping
from services.pdf_parser import pdf_to_markdown


def run_input_collector(project_config: dict, vendor_ref_id: str = "") -> dict:
    """
    Collect every enabled file slot.

    Args:
        project_config: project document from MongoDB
        vendor_ref_id:  value of vendor_reference_id from the invoice payload,
                        used to select the right sheet from Excel workbooks

    Returns:
        {
          "collected": { "<slot_id>": "<content or JSON string>" },
          "missing":   ["<slot_id>", ...],
          "summary":   "…"
        }
    """
    enabled_slots = [
        s for s in project_config.get("file_slots", [])
        if s.get("enabled") and s.get("s3_bucket") and s.get("s3_key")
    ]

    if not enabled_slots:
        return {"collected": {}, "missing": [], "summary": "No enabled file slots configured"}

    collected: dict[str, str] = {}
    missing:   list[str]      = []

    for slot in enabled_slots:
        bucket  = slot["s3_bucket"]
        key     = slot["s3_key"]
        slot_id = slot["id"]

        try:
            if not object_exists(bucket, key):
                missing.append(slot_id)
                continue

            if key.lower().endswith((".xlsx", ".xls")):
                _collect_excel(collected, slot_id, bucket, key, vendor_ref_id)
            else:
                collected[slot_id] = get_object(bucket, key)

        except Exception as e:
            print(f"[InputCollector] Failed to fetch slot '{slot_id}': {e}")
            missing.append(slot_id)

    excel_count = sum(1 for v in collected.values() if '"type": "excel_mapping"' in v)
    return {
        "collected": collected,
        "missing":   missing,
        "summary":   (
            f"Collected {len(collected)} slot(s) "
            f"({excel_count} Excel workbook(s) parsed), "
            f"{len(missing)} missing."
        ),
    }


def collect_invoice_pdf(payload: dict) -> str:
    """
    Download the invoice PDF referenced in payload.custom and convert it to markdown.

    Reads:
      payload.custom.attachment_bucket  — S3 bucket
      payload.custom.attachment_key     — S3 key  (e.g. "invoice/input/abc.pdf")

    Returns the markdown string, or "" if the PDF is missing / unreadable.
    """
    custom = payload.get("custom") or {}
    bucket = custom.get("attachment_bucket", "").strip()
    key    = custom.get("attachment_key", "").strip()

    if not bucket or not key:
        print("[InputCollector] No attachment_bucket/attachment_key in payload.custom — skipping PDF.")
        return ""

    try:
        if not object_exists(bucket, key):
            print(f"[InputCollector] Invoice PDF not found: s3://{bucket}/{key}")
            return ""

        pdf_bytes = get_binary(bucket, key)
        markdown  = pdf_to_markdown(pdf_bytes)

        if markdown:
            print(f"[InputCollector] Invoice PDF converted — {len(markdown)} chars — s3://{bucket}/{key}")
        else:
            print(f"[InputCollector] PDF downloaded but produced no text: s3://{bucket}/{key}")

        return markdown

    except Exception as e:
        print(f"[InputCollector] PDF fetch/convert failed: {e}")
        return ""


def _collect_excel(
    collected: dict,
    slot_id: str,
    bucket: str,
    key: str,
    vendor_ref_id: str,
) -> None:
    """Download and parse an Excel workbook; store result as JSON string."""
    excel_bytes = get_binary(bucket, key)

    if not vendor_ref_id:
        collected[slot_id] = json.dumps({
            "type": "excel_mapping",
            "note": "vendor_ref_id not provided — cannot select vendor sheet",
            "field_mapping":  [],
            "charge_mapping": [],
        })
        return

    field_rules    = parse_field_mapping(excel_bytes, vendor_ref_id)
    charge_rules   = parse_charge_mapping(excel_bytes, vendor_ref_id)

    collected[slot_id] = json.dumps({
        "type":           "excel_mapping",
        "vendor_ref_id":  vendor_ref_id,
        "field_mapping":  field_rules,
        "charge_mapping": charge_rules,
    })

    print(
        f"[InputCollector] Excel parsed — vendor={vendor_ref_id} "
        f"fields={len(field_rules)} charges={len(charge_rules)}"
    )

"""
Input Collector
Fetches all enabled S3 file slots for a project.

Excel workbooks (.xlsx / .xls) are parsed into structured field_mapping +
charge_mapping JSON keyed by vendor_ref_id — no LLM required.
Plain text / CSV files are returned as-is.
"""

import json
import os
from pathlib import Path

from services.s3 import get_object, get_binary
from services.excel_parser import parse_field_mapping, parse_charge_mapping
from services.pdf_parser import parse_pdf
from services.cache import content_key, excel_mapping_cache, pdf_markdown_cache


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
    """Download + convert the invoice PDF. Returns markdown for scoring."""
    return (collect_invoice_pdf_parsed(payload).get("markdown") or "")


def collect_invoice_pdf_parsed(payload: dict) -> dict:
    """
    Download the invoice PDF referenced in payload.custom and parse it.

    Set LOCAL_INVOICE_PDF=true and LOCAL_INVOICE_PDF_PATH=/path/to/file.pdf
    to parse a local file instead of S3 (local testing only).
    """
    empty = {"markdown": "", "pages": []}
    custom = payload.get("custom") or {}
    bucket = (custom.get("attachment_bucket") or "").strip()
    key    = (custom.get("attachment_key") or "").strip()

    cache_id = content_key("pdf-md", bucket or "local", key or _configured_local_pdf_path() or "")
    cached = pdf_markdown_cache.get(cache_id)
    if cached is not None:
        print(f"[InputCollector] Invoice PDF cache hit")
        if isinstance(cached, dict):
            return cached
        return {"markdown": cached or "", "pages": []}

    pdf_bytes = None
    source = ""

    local_path = _configured_local_pdf_path()
    if local_path:
        try:
            pdf_bytes = Path(local_path).read_bytes()
            source = f"local:{local_path}"
            print(f"[InputCollector] LOCAL_INVOICE_PDF=true — using {local_path}")
        except Exception as e:
            print(f"[InputCollector] LOCAL_INVOICE_PDF_PATH unreadable ({local_path}): {e}")
            return empty

    if not pdf_bytes:
        if not bucket or not key:
            print("[InputCollector] No attachment_bucket/attachment_key in payload.custom — skipping PDF.")
            return empty
        try:
            pdf_bytes = get_binary(bucket, key)
            source = f"s3://{bucket}/{key}"
        except FileNotFoundError:
            print(f"[InputCollector] Invoice PDF not found: s3://{bucket}/{key}")
            return empty
        except Exception as e:
            print(f"[InputCollector] PDF fetch/convert failed: {e}")
            return empty

    parsed = parse_pdf(pdf_bytes)
    markdown = parsed.get("markdown") or ""
    n_boxes = sum(len(p.get("items") or []) for p in parsed.get("pages") or [])

    if markdown:
        print(
            f"[InputCollector] Invoice PDF converted — {len(markdown)} chars, "
            f"{n_boxes} boxes — {source}"
        )
        pdf_markdown_cache.set(cache_id, parsed)
    else:
        print(f"[InputCollector] PDF produced no readable content ({source})")

    return parsed


def _configured_local_pdf_path() -> str | None:
    """Explicit local file when LOCAL_INVOICE_PDF=true and PATH is set."""
    flag = os.getenv("LOCAL_INVOICE_PDF", "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return None
    raw = (os.getenv("LOCAL_INVOICE_PDF_PATH") or "").strip()
    if not raw:
        print("[InputCollector] LOCAL_INVOICE_PDF=true but LOCAL_INVOICE_PDF_PATH is empty")
        return None
    path = Path(raw)
    if not path.is_absolute():
        root = Path(__file__).resolve().parents[2]
        path = (root / raw).resolve()
    if not path.is_file():
        print(f"[InputCollector] LOCAL_INVOICE_PDF_PATH not found: {path}")
        return None
    return str(path)


def _collect_excel(
    collected: dict,
    slot_id: str,
    bucket: str,
    key: str,
    vendor_ref_id: str,
) -> None:
    """Download and parse an Excel workbook; store result as JSON string."""
    cache_id = content_key("excel", bucket, key, vendor_ref_id or "")
    cached = excel_mapping_cache.get(cache_id)
    if cached is not None:
        collected[slot_id] = cached
        print(f"[InputCollector] Excel cache hit — vendor={vendor_ref_id} slot={slot_id}")
        return

    excel_bytes = get_binary(bucket, key)

    if not vendor_ref_id:
        payload = json.dumps({
            "type": "excel_mapping",
            "note": "vendor_ref_id not provided — cannot select vendor sheet",
            "field_mapping":  [],
            "charge_mapping": [],
        })
        excel_mapping_cache.set(cache_id, payload)
        collected[slot_id] = payload
        return

    field_rules    = parse_field_mapping(excel_bytes, vendor_ref_id)
    charge_rules   = parse_charge_mapping(excel_bytes, vendor_ref_id)

    payload = json.dumps({
        "type":           "excel_mapping",
        "vendor_ref_id":  vendor_ref_id,
        "field_mapping":  field_rules,
        "charge_mapping": charge_rules,
    })
    excel_mapping_cache.set(cache_id, payload)
    collected[slot_id] = payload

    print(
        f"[InputCollector] Excel parsed — vendor={vendor_ref_id} "
        f"fields={len(field_rules)} charges={len(charge_rules)}"
    )

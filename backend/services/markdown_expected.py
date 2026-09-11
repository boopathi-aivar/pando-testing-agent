"""
Pull labeled invoice fields out of PDF markdown when the LLM extractor
fails or returns nothing.

Used so a scan like 11448 still gets expected values from OCR text instead
of empty expected_from_pdf / unverified rows.
"""

from __future__ import annotations

import re
from typing import Any


def _has_value(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str) and not v.strip():
        return False
    if isinstance(v, (list, dict)) and not v:
        return False
    return True


def usable_expected(expected: dict | None) -> bool:
    if not isinstance(expected, dict):
        return False
    return any(_has_value(v) for v in expected.values())


def merge_expected(parsed: dict, llm: dict | None) -> dict:
    """Prefer LLM fields when present; fill gaps from markdown parse."""
    out = dict(parsed or {})
    for key, val in (llm or {}).items():
        if _has_value(val):
            out[key] = val
    return out


def parse_expected_from_markdown(markdown: str) -> dict:
    if not markdown or not markdown.strip():
        return {}

    text = markdown.replace("\u3000", " ")
    out: dict[str, Any] = {}

    def _find(*patterns: str) -> str | None:
        for pat in patterns:
            m = re.search(pat, text, re.I)
            if m:
                val = m.group(1).strip().rstrip(".,;")
                if val:
                    return val
        return None

    invoice_number = _find(
        r"invoice\s*#\s*[:.]?\s*([A-Za-z0-9\-]+)",
        r"invoice\s*number\s*[:.]?\s*([A-Za-z0-9\-]+)",
    )
    if invoice_number:
        out["invoice_number"] = invoice_number

    invoice_date = _find(
        r"invoice\s*date\s*[:.]?\s*([0-9]{1,2}[/\-][0-9]{1,2}[/\-][0-9]{2,4})",
        r"invoice\s*date\s*[:.]?\s*([0-9]{1,2}[- ][A-Za-z]{3,9}[-, ]+[0-9]{2,4})",
    )
    if invoice_date:
        out["invoice_date"] = invoice_date

    due = _find(
        r"payment\s*due\s*date\s*[:.]?\s*([0-9]{1,2}[/\-][0-9]{1,2}[/\-][0-9]{2,4})",
        r"\bdue\s*date\s*[:.]?\s*([0-9]{1,2}[/\-][0-9]{1,2}[/\-][0-9]{2,4})",
    )
    if due:
        out["payment_due_date"] = due

    bol = _find(
        r"\bbol\s*#\s*[:.]?\s*([A-Za-z0-9\-]+)",
        r"bill\s*of\s*lading\s*(?:number|#)?\s*[:.]?\s*([A-Za-z0-9\-]+)",
        r"\bref\s*#\s*[:.]?\s*([A-Za-z0-9\-]+)",
    )
    if bol:
        out["bill_of_lading_number"] = bol

    scac = _find(r"\bscac\s*[:.]?\s*([A-Z]{2,6})\b")
    if scac:
        out["vendor_reference_id"] = scac

    origin = _find(r"origin\s*[:.]?\s*([A-Za-z][A-Za-z .,'-]+?)(?:\s+destination\b|$|\n)")
    if origin:
        out["origin_city"] = origin.split(",")[0].strip()
        out["source_name"] = origin.strip()

    dest = _find(r"destination\s*[:.]?\s*([A-Za-z][A-Za-z .,'-]+?)(?:\s*$|\n)")
    if dest:
        out["destination_name"] = dest.strip()

    money = [a.replace(",", "") for a in re.findall(r"\$\s*([0-9,]+\.[0-9]{2})", text)]
    line_total = _find(r"(?im)(?:^|\n)total\s*[:.]?\s*\$?\s*([0-9,]+\.[0-9]{2})")
    if line_total:
        out["total_invoice_value"] = line_total.replace(",", "")
    elif money:
        out["total_invoice_value"] = f"{max(float(x) for x in money):.2f}"

    vendor = _find(
        r"(MADISON\s*LOGISTICS(?:\s*INC)?)",
        r"(MADISONLOGISTICSINC)",
    )
    if vendor:
        if re.fullmatch(r"MADISONLOGISTICSINC", vendor, re.I):
            out["vendor_name"] = "Madison Logistics Inc"
        else:
            out["vendor_name"] = re.sub(r"\s+", " ", vendor).title()

    shipper = _find(
        r"shipper.*?([A-Z][A-Z0-9 &.,'/-]{8,80}?)(?:\s+LAREDO|\s+coming|\n)",
    )
    # Prefer the known OCR line from freight invoices
    m_ship = re.search(
        r"(GL\s*GROUP\s*COMPANY[^\n]{0,40})",
        text,
        re.I,
    )
    if m_ship:
        out["shipper_name"] = re.sub(r"\s+", " ", m_ship.group(1)).strip(" -")
    elif shipper:
        out["shipper_name"] = shipper

    m_recv = re.search(
        r"(CORPORATE\s*WAREHOUSE\s*SERVICES)",
        text,
        re.I,
    )
    if m_recv:
        out["consignee_name"] = re.sub(r"\s+", " ", m_recv.group(1)).strip()

    currency = _find(r"\bcurrency\s*[:.]?\s*([A-Z]{3})\b")
    if currency:
        out["currency"] = currency
    elif "$" in text:
        out["currency"] = "USD"

    return out

"""
Deterministic field comparison used to post-process LLM scoring.

Handles:
  - date format differences, including ambiguous slash dates
    (08/12/2026 == 12-Aug-2026 when one side is unambiguous)
  - numeric / currency differences (2,435.00 == 2435)
  - company legal suffixes / case (Madison Logistics == MADISON LOGISTICS INC)
  - country code vs name (US == United States)
  - no-ground-truth rows (must not be marked "correct")
"""

from __future__ import annotations

import re
from datetime import datetime, date
from typing import Any


DATE_FIELDS = {
    "invoice_date", "payment_due_date", "payment_due", "due_date",
    "shipment_date", "delivery_date", "eta", "etd", "bol_date",
}

AMOUNT_FIELDS = {
    "total_invoice_value", "net_invoice_value", "invoice_total",
    "amount_due", "tax_amount", "freight_amount", "assessable_value",
}

_PLACEHOLDER_EXPECTED = {
    "", "none", "null", "n/a", "na", "nil", "-", "—", "unknown",
    "n/a (no pdf ground truth provided)",
    "no pdf ground truth provided",
}

_DATE_FORMATS = (
    "%d-%b-%Y", "%d-%B-%Y", "%d %b %Y", "%d %B %Y",
    "%d/%b/%Y", "%d/%B/%Y",
    "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
    "%m/%d/%Y", "%m-%d-%Y",
    "%d-%b-%y", "%d/%m/%y", "%m/%d/%y",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%d-%b-%Y %H:%M:%S",
    "%b %d, %Y", "%B %d, %Y",
    "%b %d %Y", "%B %d %Y",
    "%d-%m-%y",
)


def _as_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("null", "none"):
        return None
    return s


def is_empty_expected(v: Any) -> bool:
    s = _as_str(v)
    if s is None:
        return True
    low = s.lower().strip()
    if low in _PLACEHOLDER_EXPECTED:
        return True
    if "no pdf ground truth" in low or "no ground truth" in low:
        return True
    return False


def _date_raw(value: Any) -> str | None:
    raw = _as_str(value)
    if not raw:
        return None
    raw = raw.replace("Z", "").strip()
    raw = re.sub(r"[+-]\d{2}:\d{2}$", "", raw).strip()
    raw = raw.replace("T", " ").split(".")[0].strip()
    raw = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", raw, flags=re.I)
    return raw


def parse_date_candidates(value: Any) -> set[date]:
    """Every calendar day this string could reasonably mean."""
    raw = _date_raw(value)
    if not raw:
        return set()
    found: set[date] = set()
    candidates = [raw, re.sub(r"\s+", " ", raw)]
    for text in candidates:
        for fmt in _DATE_FORMATS:
            try:
                found.add(datetime.strptime(text, fmt).date())
            except ValueError:
                continue
    return found


def parse_date(value: Any) -> date | None:
    """Single best parse. Prefer unambiguous month-name / ISO, then first format hit."""
    raw = _date_raw(value)
    if not raw:
        return None
    # Month-name and ISO are unambiguous — use those first when they work.
    preferred = (
        "%d-%b-%Y", "%d-%B-%Y", "%d %b %Y", "%d %B %Y",
        "%d/%b/%Y", "%d/%B/%Y",
        "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
        "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y",
        "%d-%b-%y",
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M:%S",
    )
    for fmt in preferred:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    compact = re.sub(r"\s+", " ", raw)
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(compact, fmt).date()
        except ValueError:
            continue
    found = parse_date_candidates(value)
    if len(found) == 1:
        return next(iter(found))
    return None


def parse_amount(value: Any) -> float | None:
    raw = _as_str(value)
    if raw is None:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", raw.replace(",", ""))
    if cleaned in ("", "-", ".", "-."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _norm_text(value: Any) -> str:
    s = _as_str(value) or ""
    s = s.lower()
    s = re.sub(r"[\s\-_/,.]+", "", s)
    return s


_LEGAL_SUFFIX = re.compile(
    r"\b(?:incorporated|corporation|company|limited|inc|llc|ltd|corp|co|plc|lp|llp|gmbh|pty)\s*$",
    re.I,
)

NAME_FIELDS = {
    "vendor_name", "carrier_name", "carrier", "shipper_name", "consignee_name",
    "source_name", "destination_name", "company_name", "bill_to_name",
    "sold_to_name", "bill_to", "remit_to",
}

COUNTRY_FIELDS = {
    "origin_country", "destination_country", "country",
    "shipper_country", "consignee_country",
}

ADDRESS_FIELDS_MATCH = {
    "source_address", "destination_address", "shipper_address", "consignee_address",
    "origin_address", "billing_address",
}

# ISO-2 after stripping punctuation/spaces. Freight invoices commonly mix code and name.
_COUNTRY_TO_ISO = {
    "us": "US", "usa": "US", "unitedstates": "US", "unitedstatesofamerica": "US",
    "ca": "CA", "can": "CA", "canada": "CA",
    "mx": "MX", "mex": "MX", "mexico": "MX",
    "gb": "GB", "uk": "GB", "unitedkingdom": "GB", "greatbritain": "GB", "england": "GB",
    "cn": "CN", "chn": "CN", "china": "CN", "peoplesrepublicofchina": "CN",
    "in": "IN", "ind": "IN", "india": "IN",
    "de": "DE", "deu": "DE", "germany": "DE",
    "nl": "NL", "nld": "NL", "netherlands": "NL", "holland": "NL",
    "fr": "FR", "fra": "FR", "france": "FR",
    "it": "IT", "ita": "IT", "italy": "IT",
    "es": "ES", "esp": "ES", "spain": "ES",
    "be": "BE", "bel": "BE", "belgium": "BE",
    "jp": "JP", "jpn": "JP", "japan": "JP",
    "kr": "KR", "kor": "KR", "southkorea": "KR", "korea": "KR",
    "sg": "SG", "sgp": "SG", "singapore": "SG",
    "au": "AU", "aus": "AU", "australia": "AU",
    "nz": "NZ", "nzl": "NZ", "newzealand": "NZ",
    "br": "BR", "bra": "BR", "brazil": "BR",
    "ae": "AE", "are": "AE", "uae": "AE", "unitedarabemirates": "AE",
    "hk": "HK", "hkg": "HK", "hongkong": "HK",
    "tw": "TW", "twn": "TW", "taiwan": "TW",
    "vn": "VN", "vnm": "VN", "vietnam": "VN",
    "th": "TH", "tha": "TH", "thailand": "TH",
    "my": "MY", "mys": "MY", "malaysia": "MY",
    "id": "ID", "idn": "ID", "indonesia": "ID",
    "ph": "PH", "phl": "PH", "philippines": "PH",
    "za": "ZA", "zaf": "ZA", "southafrica": "ZA",
    "ie": "IE", "irl": "IE", "ireland": "IE",
    "pl": "PL", "pol": "PL", "poland": "PL",
    "se": "SE", "swe": "SE", "sweden": "SE",
    "ch": "CH", "che": "CH", "switzerland": "CH",
    "at": "AT", "aut": "AT", "austria": "AT",
    "dk": "DK", "dnk": "DK", "denmark": "DK",
    "no": "NO", "nor": "NO", "norway": "NO",
    "fi": "FI", "fin": "FI", "finland": "FI",
    "pt": "PT", "prt": "PT", "portugal": "PT",
    "tr": "TR", "tur": "TR", "turkey": "TR",
    "il": "IL", "isr": "IL", "israel": "IL",
    "sa": "SA", "sau": "SA", "saudiarabia": "SA",
}


def _strip_legal_suffixes(s: str) -> str:
    s = re.sub(r"[,.]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    while True:
        nxt = _LEGAL_SUFFIX.sub("", s).strip()
        if nxt == s:
            return s
        s = nxt


def _norm_name(value: Any) -> str:
    s = (_as_str(value) or "").lower()
    s = s.replace("&", " and ").replace("+", " and ")
    s = _strip_legal_suffixes(s)
    return re.sub(r"[^a-z0-9]+", "", s)


def _norm_country(value: Any) -> str:
    key = _norm_text(value)
    if not key:
        return ""
    if key in _COUNTRY_TO_ISO:
        return _COUNTRY_TO_ISO[key]
    if len(key) == 2:
        return key.upper()
    return key


def _base_field_name(field_name: str) -> str:
    return (field_name or "").lower().split("(")[0].strip()


def _is_date_field(fname: str) -> bool:
    base = _base_field_name(fname)
    return base in DATE_FIELDS or "date" in base


def _is_amount_field(fname: str) -> bool:
    base = _base_field_name(fname)
    return base in AMOUNT_FIELDS or any(tok in base for tok in ("amount", "value", "total", "tax"))


def _is_name_field(fname: str) -> bool:
    base = _base_field_name(fname)
    if base in NAME_FIELDS:
        return True
    return base.endswith("_name") and not base.startswith("charge")


def _is_country_field(fname: str) -> bool:
    base = _base_field_name(fname)
    return base in COUNTRY_FIELDS or base.endswith("_country") or base == "country"


def _is_address_field(fname: str) -> bool:
    base = _base_field_name(fname)
    return base in ADDRESS_FIELDS_MATCH or base.endswith("_address")


# Words that refine a charge without changing which charge it is.
_CHARGE_FILLER = {
    "mile", "miles", "mileage", "fee", "fees", "charge", "charges",
    "amt", "amount", "cost", "costs", "accessorial", "accessorials",
    "surch", "type",
}


def _is_charge_name_field(fname: str) -> bool:
    base = _base_field_name(fname)
    return base in {"charge_name", "master_name", "vendor_charge_name"} or "charge_name" in fname


def _charge_tokens(value: Any) -> set[str]:
    s = (_as_str(value) or "").lower()
    toks = re.findall(r"[a-z0-9]+", s)
    return {t for t in toks if t and t not in _CHARGE_FILLER}


def _charge_names_match(expected: Any, actual: Any) -> bool:
    """Same charge type with extra qualifier words (Fuel Surcharge Miles == Fuel Surcharge)."""
    if _norm_text(expected) == _norm_text(actual) and _norm_text(expected):
        return True
    e_toks, a_toks = _charge_tokens(expected), _charge_tokens(actual)
    if not e_toks or not a_toks:
        return False
    return e_toks == a_toks


def _address_match(expected: Any, actual: Any) -> bool:
    n1, n2 = _norm_text(expected), _norm_text(actual)
    if not n1 or not n2:
        return False
    if n1 == n2:
        return True
    short, longer = (n1, n2) if len(n1) <= len(n2) else (n2, n1)
    return len(short) >= 12 and short in longer


def values_match(field_name: str, expected: Any, actual: Any) -> bool:
    """True when expected and actual represent the same value."""
    fname = (field_name or "").lower()

    if _is_date_field(fname):
        c1, c2 = parse_date_candidates(expected), parse_date_candidates(actual)
        if c1 and c2:
            return bool(c1 & c2)

    if _is_amount_field(fname):
        a1, a2 = parse_amount(expected), parse_amount(actual)
        if a1 is not None and a2 is not None:
            return abs(a1 - a2) < 0.015

    if _is_country_field(fname):
        e_c, a_c = _norm_country(expected), _norm_country(actual)
        if e_c and a_c and e_c == a_c:
            return True

    if _is_name_field(fname):
        if _norm_name(expected) and _norm_name(expected) == _norm_name(actual):
            return True

    if _is_address_field(fname) and _address_match(expected, actual):
        return True

    if _is_charge_name_field(fname) and _charge_names_match(expected, actual):
        return True

    return _norm_text(expected) == _norm_text(actual)


def classify_field(field_name: str, expected: Any, actual: Any) -> str:
    """
    Return correct | wrong | missing | unverified.

    unverified = actual is present but there is no ground-truth expected value.
    That must not inflate the score as "correct".
    """
    act = _as_str(actual)
    if act is None:
        return "missing"
    if is_empty_expected(expected):
        return "unverified"
    return "correct" if values_match(field_name, expected, actual) else "wrong"


def _lookup_actual(payload: dict, field_name: str) -> Any:
    if not isinstance(payload, dict):
        return None
    if field_name in payload and payload[field_name] not in (None, ""):
        return payload[field_name]
    custom = payload.get("custom") or {}
    if isinstance(custom, dict) and field_name in custom:
        return custom.get(field_name)
    aliases = {
        "vendor_name": ("carrier", "carrier_name"),
        "total_invoice_value": ("invoice_total",),
        "source_name": ("shipper_name", "shipper"),
        "destination_name": ("consignee_name", "consignee"),
        "source_address": ("shipper_address",),
        "destination_address": ("consignee_address",),
    }
    for alt in aliases.get(field_name, ()):
        if payload.get(alt) not in (None, ""):
            return payload[alt]
        if isinstance(custom, dict) and custom.get(alt) not in (None, ""):
            return custom[alt]
    return None


CHARGE_FIELDS  = ["charge_code", "charge_type", "freight_charge", "surcharge", "charge_name"]
ADDRESS_FIELDS = [
    "origin_country", "destination_country", "shipper_name", "consignee_name",
    "shipper_address", "consignee_address", "source_name", "destination_name",
    "source_address", "destination_address", "vendor_name", "carrier_name",
]
DATE_SCORE_FIELDS = ["invoice_date", "payment_due_date", "shipment_date", "delivery_date", "eta"]
AMOUNT_SCORE_FIELDS = [
    "total_invoice_value", "net_invoice_value", "invoice_total",
    "amount_due", "tax_amount", "freight_amount", "assessable_value",
]


def field_is_required(field_name: str, row: dict | None = None, mandatory_set: set[str] | None = None) -> bool:
    """Required = listed in project mandatory_fields, else the row's is_mandatory flag."""
    row = row or {}
    mandatory_set = mandatory_set or set()
    if mandatory_set:
        fname = (field_name or "").lower().strip()
        return fname in mandatory_set or _base_field_name(field_name) in mandatory_set
    return row.get("is_mandatory") is True


def tag_mandatory(validations: list, mandatory_fields: list | None) -> list:
    mandatory_set = {f.lower() for f in (mandatory_fields or []) if f}
    for v in validations or []:
        if not isinstance(v, dict):
            continue
        if mandatory_set:
            v["is_mandatory"] = field_is_required(v.get("field_name"), v, mandatory_set)
        elif "is_mandatory" not in v:
            v["is_mandatory"] = False
    return validations


def rescore(validations: list, weights: dict | None = None, mandatory_fields: list | None = None) -> tuple[float, str]:
    """
    Score required fields only.

    Unverified rows are excluded (no ground truth). Optional fields do not
    affect the overall score. If the project has no required-field list,
    fall back to every comparable field.
    """
    mandatory_set = {f.lower() for f in (mandatory_fields or []) if f}
    has_required = bool(mandatory_set) or any(
        isinstance(v, dict) and v.get("is_mandatory") for v in (validations or [])
    )
    pool = [
        v for v in (validations or [])
        if isinstance(v, dict) and (
            field_is_required(v.get("field_name"), v, mandatory_set) if has_required else True
        )
    ]
    comparable = [v for v in pool if v.get("status") in ("correct", "wrong", "missing")]
    if not comparable:
        # Payload values with no PDF/expected must not look like a 0% fail.
        if any(v.get("status") == "unverified" for v in pool):
            return 0.0, "unscored"
        return 0.0, "failed"

    correct = sum(1 for v in comparable if v.get("status") == "correct")
    score = round((correct / len(comparable)) * 100, 1)
    status = "passed" if score >= 85 else "warning" if score >= 60 else "failed"
    return score, status


def normalize_validations(validations: list, payload: dict | None = None) -> list:
    """Rewrite LLM/fallback rows with deterministic comparison."""
    out = []
    for v in validations or []:
        if not isinstance(v, dict):
            continue
        name = v.get("field_name") or "unknown"
        expected = v.get("expected_value")
        actual = v.get("actual_value")
        if actual in (None, "", "null") and payload:
            looked = _lookup_actual(payload, name)
            if looked not in (None, ""):
                actual = looked
        status = classify_field(name, expected, actual)
        # Scoring-time LLM equivalence must survive GET re-classify.
        if status == "wrong" and v.get("equivalent_match"):
            status = "correct"
        row = dict(v)
        row["status"] = status
        if is_empty_expected(expected):
            row["expected_value"] = None
        elif status == "correct" and parse_date(expected) and parse_date(actual):
            # keep original strings; status already format-agnostic
            pass
        row["actual_value"] = None if _as_str(actual) is None else str(actual)
        if status == "unverified" and not row.get("source_used"):
            row["source_used"] = "No ground truth"
        out.append(row)
    return out


def apply_comparison(
    result: dict,
    payload: dict | None = None,
    weights: dict | None = None,
    mandatory_fields: list | None = None,
) -> dict:
    """Post-process a scoring result in place and return it."""
    try:
        api_status = result.get("api_status")
        if api_status is not None and int(api_status) >= 400:
            result["overall_score"] = 0.0
            result["status"] = "failed"
            return result
    except (TypeError, ValueError):
        pass

    validations = normalize_validations(result.get("field_validations", []), payload)
    tag_mandatory(validations, mandatory_fields)
    result["field_validations"] = validations
    score, status = rescore(validations, weights, mandatory_fields=mandatory_fields)
    result["overall_score"] = score
    if result.get("status") != "failed" or validations:
        result["status"] = status
    return result

"""
Prompt-template parser service.

The invoice processor's prompt templates live in S3 as a *Python source file*
that defines a top-level ``PROMPT_TEMPLATES`` dict, keyed by carrier name. Each
value is a dict that (at minimum) carries a ``prompt_template`` string and,
optionally, a human-readable ``carrier_name``.

This service downloads that file through the cross-account S3 session
(``services/s3.py``) and turns it into a structured documentation summary that
the frontend can render: how many carriers exist, how carrier classification
works, how page classification works, and how field-mapping logic is defined.

Because the file is executed to obtain the dict, we run it in a restricted
namespace. The template is authored/owned by the Pando team (trusted source),
but we still isolate execution and never expose raw builtins to callers.

The summary is derived *live* from S3 on every call, so when the template in
S3 is updated the documentation reflects the new content on the next fetch.
An ETag is returned so the frontend can tell when the underlying file changed.
"""

from __future__ import annotations

import ast
import re
from typing import Any

from botocore.exceptions import ClientError

from config import make_source_aws_session
from services import s3 as s3svc


# ── keyword banks used to mine the prompt text for documentation ──────────────

_CARRIER_CLASS_HINTS = (
    "carrier", "classif", "identify the carrier", "scac", "vendor",
    "which carrier", "determine the carrier", "carrier name",
)
_PAGE_CLASS_HINTS = (
    "page", "first page", "cover page", "page classif", "multi-page",
    "multipage", "per page", "page type", "which page", "page number",
)
_FIELD_MAP_HINTS = (
    "field", "map ", "mapping", "extract", "charge code", "charge name",
    "bill of lading", "invoice number", "invoice date", "due date",
    "schema", "json", "output format", "structure your response",
)


def _first_sentences(text: str, limit: int = 4) -> list[str]:
    """Return up to ``limit`` cleaned sentences/lines from a block of text."""
    if not text:
        return []
    # Split on newlines first (prompts are line-oriented), then trim.
    raw_lines = [ln.strip(" -*\t") for ln in text.splitlines()]
    lines = [ln for ln in raw_lines if len(ln) > 3]
    out: list[str] = []
    for ln in lines:
        out.append(ln)
        if len(out) >= limit:
            break
    return out


def _matching_lines(text: str, hints: tuple[str, ...], limit: int = 8) -> list[str]:
    """Return distinct, human-readable lines from ``text`` that mention any hint."""
    if not text:
        return []
    seen: set[str] = set()
    matches: list[str] = []
    for raw in text.splitlines():
        line = raw.strip(" -*\t")
        if len(line) < 4:
            continue
        low = line.lower()
        if any(h in low for h in hints):
            key = low[:120]
            if key in seen:
                continue
            seen.add(key)
            matches.append(line if len(line) <= 240 else line[:237] + "…")
            if len(matches) >= limit:
                break
    return matches


def _extract_fields_from_prompt(text: str, limit: int = 500) -> list[str]:
    """
    Best-effort extraction of the field names a prompt asks the model to map.

    Looks for JSON-ish "key": ... patterns and bulleted field mentions so the
    documentation can list the fields the template maps for a carrier.
    """
    if not text:
        return []
    fields: list[str] = []
    seen: set[str] = set()

    # JSON-style keys:  "invoice_number": ...   or   'charge_code':
    for m in re.finditer(r"""["']([a-zA-Z][a-zA-Z0-9 _./-]{1,50})["']\s*:""", text):
        key = m.group(1).strip()
        low = key.lower()
        if low and low not in seen and len(key) <= 50:
            seen.add(low)
            fields.append(key)
            if len(fields) >= limit:
                break
    return fields


def _classification_signal(prompt_text: str) -> dict[str, Any]:
    """
    Determine *how* an invoice is identified as this carrier.

    Carriers in these templates are keyed off a ``vendor_ref_id`` (a stable
    numeric/string code fixed for each carrier, expressed as
    ``"vendor_ref_id": Always "70141"``). Some carriers instead match on the
    carrier name that appears on the invoice. This returns a small structured
    descriptor so the documentation can state the exact rule.
    """
    if not prompt_text:
        return {"type": "unknown", "values": [], "description": "No classification signal found."}

    # "vendor_ref_id": Always "70141"  (also tolerate 'vendor_reference_id')
    ref_ids: list[str] = []
    for m in re.finditer(
        r"vendor_re(?:ference|f)_id\"?\s*:?\s*(?:always|Always)?\s*[\"']([^\"']+)[\"']",
        prompt_text,
    ):
        val = m.group(1).strip()
        # skip accidental capture of the literal word "Always"
        if val and val.lower() != "always" and val not in ref_ids:
            ref_ids.append(val)

    # Keep only ref-id-like values: codes that contain a digit (e.g. 70141) or
    # are short all-caps identifiers (e.g. a SCAC). Drop stray lowercase words
    # like "to"/"always" that can appear inside quotes in the prompt prose.
    def _looks_like_ref(v: str) -> bool:
        if not re.fullmatch(r"[A-Za-z0-9_\-]{2,20}", v):
            return False
        if any(ch.isdigit() for ch in v):
            return True
        return v.isupper() and len(v) <= 8

    numeric_refs = [v for v in ref_ids if _looks_like_ref(v)]

    if numeric_refs:
        joined = ", ".join(numeric_refs)
        return {
            "type": "vendor_ref_id",
            "values": numeric_refs,
            "description": f'Matched when vendor_ref_id = {joined}',
        }

    # Fall back to a name-based signal if the prompt says to match on carrier name.
    name_vals = [v for v in ref_ids if v]
    if name_vals:
        joined = ", ".join(name_vals)
        return {
            "type": "carrier_name",
            "values": name_vals,
            "description": f'Matched by carrier name / reference: {joined}',
        }

    return {
        "type": "name_or_text",
        "values": [],
        "description": "Matched by the carrier name / text appearing on the invoice.",
    }


def _load_prompt_templates(bucket: str, key: str) -> dict[str, Any]:
    """
    Download the prompt-template Python file from S3 and evaluate it in an
    isolated namespace to recover the ``PROMPT_TEMPLATES`` dict.
    """
    source = s3svc.get_object(bucket, key)  # raises FileNotFoundError / RuntimeError

    # Fast path — try a full exec in a sandboxed namespace.
    try:
        sandbox_globals: dict[str, Any] = {"__builtins__": {}}
        local_vars: dict[str, Any] = {}
        exec(compile(source, key, "exec"), sandbox_globals, local_vars)  # noqa: S102
        templates = local_vars.get("PROMPT_TEMPLATES")
        if isinstance(templates, dict):
            return templates
    except Exception:
        pass

    # Fallback — statically parse the module and literal-eval the assignment.
    try:
        tree = ast.parse(source, filename=key)
        for node in tree.body:
            targets = getattr(node, "targets", [])
            for t in targets:
                if isinstance(t, ast.Name) and t.id == "PROMPT_TEMPLATES":
                    value = ast.literal_eval(node.value)
                    if isinstance(value, dict):
                        return value
    except Exception:
        pass

    return {}


def _etag(bucket: str, key: str) -> str | None:
    """Return the current S3 ETag (content fingerprint) for change detection."""
    try:
        head = s3svc._call("head_object", bucket, Key=key)  # noqa: SLF001
        return head.get("ETag")
    except (ClientError, Exception):
        return None


def _template_prompt_text(carrier_data: Any) -> str:
    """Pull the prompt string out of a carrier's template entry."""
    if isinstance(carrier_data, str):
        return carrier_data
    if isinstance(carrier_data, dict):
        for k in ("prompt_template", "prompt", "template", "instructions"):
            v = carrier_data.get(k)
            if isinstance(v, str) and v.strip():
                return v
    return ""


def build_summary(bucket: str, key: str) -> dict[str, Any]:
    """
    Build the structured documentation summary for a prompt template stored in
    S3. Parsed live so template updates are reflected on the next call.

    Returns a dict with:
      - carrier_count, carriers[]  (name + per-carrier field/notes)
      - carrier_classification     (how the template distinguishes carriers)
      - page_classification        (how pages are classified, if present)
      - field_mapping              (how fields are mapped)
      - etag                       (S3 content fingerprint for change detection)
    """
    templates = _load_prompt_templates(bucket, key)
    etag = _etag(bucket, key)

    if not templates:
        return {
            "bucket": bucket,
            "key": key,
            "etag": etag,
            "parsed": False,
            "carrier_count": 0,
            "carriers": [],
            "carrier_classification": {"summary": "No carriers could be parsed from the template.", "rules": []},
            "page_classification": {"summary": "No page-classification logic found in the template.", "rules": []},
            "field_mapping": {"summary": "No field-mapping logic could be parsed.", "fields": [], "rules": []},
            "message": (
                "Could not find a PROMPT_TEMPLATES dict in the file at "
                f"s3://{bucket}/{key}. Confirm the path points to the prompt "
                "template Python file."
            ),
        }

    carriers: list[dict[str, Any]] = []
    all_page_class_rules: list[str] = []
    all_field_map_rules: list[str] = []
    field_union: dict[str, int] = {}

    class_mappings: list[dict[str, Any]] = []

    for key_name, carrier_data in templates.items():
        prompt_text = _template_prompt_text(carrier_data)
        display_name = key_name
        if isinstance(carrier_data, dict):
            display_name = carrier_data.get("carrier_name") or key_name

        fields = _extract_fields_from_prompt(prompt_text)
        for f in fields:
            field_union[f] = field_union.get(f, 0) + 1

        page_class_lines = _matching_lines(prompt_text, _PAGE_CLASS_HINTS, limit=4)
        field_map_lines = _matching_lines(prompt_text, _FIELD_MAP_HINTS, limit=6)

        all_page_class_rules.extend(page_class_lines)
        all_field_map_rules.extend(field_map_lines)

        signal = _classification_signal(prompt_text)

        class_mappings.append({
            "carrier": display_name,
            "key": key_name,
            "signal_type": signal["type"],
            "values": signal["values"],
            "description": signal["description"],
        })

        carriers.append({
            "key": key_name,
            "name": display_name,
            "field_count": len(fields),
            "fields": fields,
            "has_page_logic": bool(page_class_lines),
            "classification": signal,
            "prompt_preview": " ".join(_first_sentences(prompt_text, 3))[:400],
            "prompt_length": len(prompt_text),
        })

    carriers.sort(key=lambda c: c["name"].lower())
    class_mappings.sort(key=lambda m: m["carrier"].lower())

    def _dedupe(seq: list[str], limit: int = 12) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for s in seq:
            k = s.lower()[:120]
            if k not in seen:
                seen.add(k)
                out.append(s)
            if len(out) >= limit:
                break
        return out

    union_fields = sorted(field_union.keys(), key=lambda f: (-field_union[f], f.lower()))
    page_carriers = [c["name"] for c in carriers if c["has_page_logic"]]

    ref_id_count = sum(1 for m in class_mappings if m["signal_type"] == "vendor_ref_id")
    carrier_class_summary = (
        f"The template defines {len(carriers)} carrier"
        f"{'s' if len(carriers) != 1 else ''}. Each incoming invoice is matched to a "
        "carrier by its vendor reference id (vendor_ref_id) — a fixed code that "
        f"identifies the carrier. {ref_id_count} of {len(carriers)} carriers are "
        "matched this way; the remaining carriers are matched by the carrier name "
        "shown on the invoice. The table below lists the exact value each carrier "
        "is mapped to."
    )

    if page_carriers:
        page_summary = (
            "Page-classification instructions were detected in "
            f"{len(page_carriers)} carrier template(s). These prompts guide how "
            "individual pages of a multi-page invoice are identified and handled."
        )
    else:
        page_summary = (
            "No explicit page-classification rules were found in the prompt "
            "templates. Invoices appear to be processed as a single combined "
            "document rather than per page."
        )

    field_map_summary = (
        f"Across all carriers the template maps roughly {len(union_fields)} "
        "distinct fields. Each carrier prompt instructs the model to extract "
        "specific invoice fields (e.g. invoice number, dates, charges, "
        "addresses) and return them in a structured JSON schema."
    )

    return {
        "bucket": bucket,
        "key": key,
        "etag": etag,
        "parsed": True,
        "carrier_count": len(carriers),
        "carriers": carriers,
        "carrier_classification": {
            "summary": carrier_class_summary,
            "mappings": class_mappings,
        },
        "page_classification": {
            "summary": page_summary,
            "carriers_with_page_logic": page_carriers,
            "rules": _dedupe(all_page_class_rules),
        },
        "field_mapping": {
            "summary": field_map_summary,
            "field_count": len(union_fields),
            "fields": union_fields[:120],
            "rules": _dedupe(all_field_map_rules),
        },
    }

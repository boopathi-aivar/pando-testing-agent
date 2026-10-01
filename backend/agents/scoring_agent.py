"""
Scoring Agent
Validates invoice payloads using a two-phase LLM approach:

  Phase 1 — PDF Extraction
    Claude reads RapidOCR / native PDF text (with bounding boxes) using the
    matched carrier prompt from the project S3 Prompt Template slot. That
    prompt is what tells the model where each field lives. Generic extraction
    is only used when no S3 template is configured. Never copy the Lambda
    payload into expected.

  Phase 2 — Actual vs Expected Scoring
    Claude compares the ACTUAL payload (sent by the invoice processor Lambda)
    against the EXPECTED values from Phase 1, plus the Excel field/charge mappings.

This gives true actual-vs-expected comparison, not just "is the field present?"
"""

import json
import re
from strands import Agent
from strands.models.bedrock import BedrockModel
from config import settings
from services.cache import content_key, pdf_expected_cache
from services.field_compare import apply_comparison, classify_field
from services.semantic_match import apply_semantic_equivalence
from services.markdown_expected import (
    merge_expected,
    parse_expected_from_markdown,
    usable_expected,
)
from services.pdf_parser import format_ocr_layout
from services.prompt_template import (
    get_carrier_prompt_for_project,
    split_carrier_prompt,
)


# ── System prompts ─────────────────────────────────────────────────────────────

_EXTRACTION_PROMPT = """\
You are an invoice data extraction specialist.

Given the text content of an invoice PDF, extract the EXACT values for every
recognizable invoice field. Read carefully — values must match exactly what
is printed on the invoice. Do not invent or guess values.

Return ONLY a valid JSON object:
{
  "invoice_number":        "<exact value>",
  "invoice_date":          "<exact value>",
  "total_invoice_value":   <number>,
  "net_invoice_value":     <number>,
  "currency":              "<3-letter code>",
  "payment_terms":         "<exact value>",
  "payment_due_date":      "<exact value>",
  "bill_of_lading_number": "<exact value or null>",
  "vendor_name":           "<exact value or null>",
  "shipper_name":          "<exact value or null>",
  "consignee_name":        "<exact value or null>",
  "origin_country":        "<exact value or null>",
  "destination_country":   "<exact value or null>",
  "assessable_value":      <number or null>,
  "charge_items": [
    {"name": "<charge name as printed>", "amount": <number>}
  ]
}

Include only fields that are actually present. Return null for absent fields.
Do not include any text outside the JSON object.
"""

_SCORING_PROMPT = """\
You are a freight invoice validation expert for the Pando Invoice Testing system.

You receive:
  1. EXPECTED values  — extracted directly from the invoice PDF (ground truth)
  2. ACTUAL values    — what the invoice processor Lambda extracted and sent to the API
  3. Field mapping    — per-vendor rules defining which fields should be present
  4. Charge mapping   — per-vendor charge code ↔ charge name lookup table

Validation rules:
  "correct"     — actual matches expected (semantically identical is enough)
  "wrong"       — value is present but differs from expected
  "missing"     — expected a value but actual is null, empty, or absent
  "unverified"  — actual is present but there is NO ground-truth expected value
                  (do NOT mark these as "correct")

Date fields (invoice_date, payment_due_date, etc.):
  Treat as CORRECT when they represent the same calendar day regardless of format.
  Examples that MUST be "correct":
    12-Aug-2026  ==  2026-08-12  ==  12/08/2026  ==  08/12/2026  ==  12 Aug 2026
  Slash dates may be US (MM/DD) or EU (DD/MM). If either reading is the same
  day as the other value, mark correct. Do not fail hyphen vs slash vs month name.

Vendor / company names:
  Ignore case, punctuation, and legal suffixes (Inc, LLC, Ltd, Co, Company).
  MADISON LOGISTICS INC == Madison Logistics
  M & M Cartage Co., Inc. == M & M CARTAGE COMPANY INC

Country fields:
  ISO code, ISO3, and English name are the same.
  US == USA == United States

Charge names:
  Same charge type = CORRECT even if one side has extra qualifier words.
  Fuel Surcharge Miles == Fuel Surcharge
  Fuel Surcharge Fee == Fuel Surcharge
  Different charge types stay WRONG: APPLIANCE PARTS != Base Freight

Amount fields:
  Treat as CORRECT when the numeric value matches (ignore currency symbols and commas).
  2,435.00 == 2435 == 2435.0

If EXPECTED is null / missing / "N/A" / "no PDF ground truth":
  - actual present → status "unverified", expected_value null
  - actual empty   → status "missing"
  Never write expected_value as "N/A (no PDF ground truth provided)" and then mark correct.

Mandatory fields rule:
  If ANY mandatory field is missing or wrong → force status = "failed" regardless of score.
  Unverified mandatory fields (value present, no ground truth) do not fail the mandatory check.

Charge validation:
  Each charge in the charge mapping must appear in the actual payload's custom_fields,
  charge_code, or charge_type fields. Validate code and name against the vendor mapping.

Scoring (weights from project config):
  charge_fields: 25%  |  address_fields: 25%  |  date_fields: 25%  |  amount_fields: 25%
  passed ≥ 85  |  warning ≥ 60  |  failed < 60

Generate specific, actionable suggestions for improving the Lambda's LLM prompt
for every wrong or missing field (explain what instruction to add or change).

For every wrong or missing field, set reason to one short sentence: what the
processor extracted vs the invoice/mapping, and why they are not the same
(wrong entity, truncated name, missing value, dock code instead of company).
Do not put prompt-rewrite advice in reason.

Respond with ONLY a valid JSON object — no text outside it:
{
  "overall_score": <float 0.0-100.0>,
  "status": "passed" | "warning" | "failed",
  "field_validations": [
    {
      "field_name":     "<name>",
      "expected_value": "<from PDF or mapping>",
      "actual_value":   "<from Lambda payload>",
      "status":         "correct" | "wrong" | "missing" | "unverified",
      "source_used":    "Invoice PDF" | "Field Mapping Sheet" | "Charge Map Sheet",
      "is_mandatory":   true | false,
      "reason":         "<one sentence, only for wrong/missing>"
    }
  ],
  "suggestions": ["<specific prompt improvement>", ...]
}
"""


# ── Model factory ──────────────────────────────────────────────────────────────

def _make_model(max_tokens: int = 8192) -> BedrockModel:
    return BedrockModel(
        region_name=settings.AWS_REGION,
        model_id=settings.BEDROCK_MODEL_ID,
        max_tokens=max_tokens,
    )


# ── Phase 1: PDF extraction ────────────────────────────────────────────────────

def _carrier_identity(payload: dict | None) -> tuple[str | None, str | None]:
    """Vendor name + vendor_ref_id from the processor payload (lookup keys only)."""
    payload = payload or {}
    custom = payload.get("custom") if isinstance(payload.get("custom"), dict) else {}
    name = None
    for val in (
        custom.get("vendor_name"),
        payload.get("vendor_name"),
        payload.get("carrier"),
        payload.get("carrier_name"),
    ):
        if isinstance(val, str) and val.strip():
            name = val.strip()
            break
    ref = (
        payload.get("vendor_reference_id")
        or payload.get("vendor_ref_id")
        or custom.get("vendor_reference_id")
        or custom.get("vendor_ref_id")
        or ""
    )
    ref = str(ref).strip() or None
    return name, ref


def _canonical_field_key(key: str) -> str:
    raw = str(key or "").strip()
    if not raw:
        return raw
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", raw)
    snake = re.sub(r"[\s\-]+", "_", snake)
    return snake.lower().strip("_")


def _unwrap_extracted_value(val):
    if isinstance(val, dict) and "value" in val:
        extra = set(val.keys()) - {"value", "explanation", "reason", "confidence"}
        if not extra:
            return val.get("value")
    return val


def _flatten_extracted(obj: dict) -> dict:
    """Lift nested custom.* / {value, explanation} / camelCase into scoring keys."""
    if not isinstance(obj, dict):
        return {}
    merged = {k: v for k, v in obj.items() if k != "custom"}
    custom = obj.get("custom")
    if isinstance(custom, dict):
        for k, v in custom.items():
            if k not in merged or merged.get(k) in (None, "", []):
                merged[k] = v
    out: dict = {}
    for k, v in merged.items():
        key = _canonical_field_key(k)
        val = _unwrap_extracted_value(v)
        if key and (key not in out or out.get(key) in (None, "", [])):
            out[key] = val
        if k not in out:
            out[k] = val
    return out


def _parse_extraction_json(text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        return {}
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw, flags=re.I).strip()
        raw = re.sub(r"```$", "", raw).strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start < 0 or end <= start:
        print("[ScoringAgent] Phase 1 LLM returned no JSON object.")
        return {}
    snippet = raw[start:end]
    try:
        llm = json.loads(snippet)
    except json.JSONDecodeError as e:
        print(f"[ScoringAgent] Phase 1 JSON parse failed ({e}); snippet={snippet[:180]!r}")
        return {}
    if not isinstance(llm, dict):
        return {}
    return _flatten_extracted(llm)


def _run_extraction_llm(system_prompt: str, user_message: str, *, max_tokens: int) -> dict:
    agent = Agent(model=_make_model(max_tokens), tools=[], system_prompt=system_prompt)
    return _parse_extraction_json(str(agent(user_message)).strip())


_CARRIER_JSON_REMINDER = """
Follow the carrier extraction rules above exactly — including where each field
is located on the invoice (labels, header/body, mapped codes). The invoice text
is RapidOCR or native PDF text with [x0,y0-x1,y1] boxes in PDF points. Use those
positions when the prompt tells you where to read a value. Return only the JSON
object the carrier prompt specifies. Do not copy a processor payload.
"""


def _invoice_text_for_carrier(pdf_markdown: str, layout: list | None) -> str:
    laid_out = format_ocr_layout(layout)
    if laid_out:
        return laid_out
    return (pdf_markdown or "")[:32000]


def _extract_expected_from_pdf(
    pdf_markdown: str,
    *,
    project_config: dict | None = None,
    payload: dict | None = None,
    layout: list | None = None,
) -> dict:
    """
    Build expected JSON from invoice OCR + the client S3 carrier prompt.

    1. Parse labeled fields from RapidOCR / native markdown (safety net).
    2. If the project has a Prompt Template slot, always run that carrier
       prompt against the OCR text+boxes — that is what tells the model
       where each field lives. Do not switch to the generic extractor.
    3. Never invent expected from the Lambda payload.
    """
    parsed = parse_expected_from_markdown(pdf_markdown)
    if not pdf_markdown or not pdf_markdown.strip():
        print("[ScoringAgent] No PDF content — skipping expected-value extraction.")
        return parsed

    carrier_name, vendor_ref_id = _carrier_identity(payload)
    carrier_prompt, matched_key = (None, None)
    if project_config:
        carrier_prompt, matched_key = get_carrier_prompt_for_project(
            project_config,
            carrier_name=carrier_name,
            vendor_ref_id=vendor_ref_id,
        )

    invoice_text = _invoice_text_for_carrier(pdf_markdown, layout)
    user_message = None
    if carrier_prompt:
        system_prompt, invoice_tail = split_carrier_prompt(carrier_prompt, invoice_text)
        system_prompt = system_prompt.rstrip() + "\n" + _CARRIER_JSON_REMINDER
        user_message = invoice_tail
        prompt_tag = matched_key or "carrier"
        print(
            f"[ScoringAgent] Phase 1 S3 carrier prompt {matched_key!r} "
            f"({len(carrier_prompt)} chars) + OCR layout ({len(invoice_text)} chars) "
            f"for {carrier_name or vendor_ref_id!r}"
        )
        print(f"[ScoringAgent] Prompt preview: {system_prompt[:200]!r}")
    else:
        system_prompt = _EXTRACTION_PROMPT
        user_message = (
            f"Extract all invoice field values from this invoice PDF:\n\n"
            f"---\n{pdf_markdown[:8000]}\n---"
        )
        prompt_tag = "generic"
        print("[ScoringAgent] Phase 1 using generic extraction prompt (no S3 carrier template).")

    cache_id = content_key("pdf-expected", invoice_text, prompt_tag, system_prompt[:4000])
    cached = pdf_expected_cache.get(cache_id)
    if cached is not None and usable_expected(cached):
        print(f"[ScoringAgent] PDF expected values cache hit ({len(cached)} fields).")
        return cached

    llm: dict = {}
    try:
        max_tokens = 16384 if carrier_prompt else 8192
        llm = _run_extraction_llm(system_prompt, user_message, max_tokens=max_tokens)
        print(f"[ScoringAgent] LLM extracted {len(llm)} expected fields from PDF.")
    except Exception as e:
        print(f"[ScoringAgent] PDF extraction LLM failed — {e}")
        llm = {}

    if carrier_prompt and not usable_expected(llm):
        print("[ScoringAgent] Carrier extract unusable — retrying the same S3 prompt (not generic).")
        retry_user = (
            user_message
            + "\n\nYour previous reply was not valid JSON. "
            "Return only the JSON object defined in the carrier prompt."
        )
        try:
            llm = _run_extraction_llm(system_prompt, retry_user, max_tokens=16384)
            print(f"[ScoringAgent] Carrier retry extracted {len(llm)} expected fields.")
        except Exception as e:
            print(f"[ScoringAgent] Carrier prompt retry failed: {e}")
            llm = {}

    if not usable_expected(llm):
        print(
            f"[ScoringAgent] LLM expected empty/unusable — "
            f"using parsed OCR labels ({len(parsed)} fields)."
        )
        expected = parsed
    else:
        expected = merge_expected(parsed, llm)

    if usable_expected(expected):
        pdf_expected_cache.set(cache_id, expected)
        print(f"[ScoringAgent] Expected fields ready: {list(expected.keys())}")
    else:
        print("[ScoringAgent] No usable expected values from OCR or carrier prompt.")
    return expected


# ── Mandatory field helpers ────────────────────────────────────────────────────

def _build_mandatory_result(field_validations: list, mandatory_fields: list) -> dict:
    if not mandatory_fields:
        return {"total": 0, "passed": 0, "failed": 0, "failed_fields": []}

    mandatory_set = {f.lower() for f in mandatory_fields}
    failed_fields, passed_count = [], 0

    for v in field_validations:
        if v.get("field_name", "").lower() in mandatory_set:
            status = v.get("status")
            actual = v.get("actual_value")
            if status == "correct" or (status == "unverified" and actual not in (None, "", "null")):
                passed_count += 1
            else:
                failed_fields.append(v["field_name"])

    validated_names = {v.get("field_name", "").lower() for v in field_validations}
    for mf in mandatory_fields:
        if mf.lower() not in validated_names:
            failed_fields.append(mf)

    return {
        "total":         len(mandatory_fields),
        "passed":        passed_count,
        "failed":        len(failed_fields),
        "failed_fields": failed_fields,
    }


def _enforce_mandatory(result: dict, mandatory_fields: list) -> dict:
    mandatory_set = {f.lower() for f in mandatory_fields}
    for v in result.get("field_validations", []):
        v["is_mandatory"] = v.get("field_name", "").lower() in mandatory_set

    mandatory_result = _build_mandatory_result(result.get("field_validations", []), mandatory_fields)
    if mandatory_result["failed"] > 0:
        result["status"] = "failed"
    result["mandatory_fields_result"] = mandatory_result
    return result


# ── Phase 2: Scoring ───────────────────────────────────────────────────────────

def _run_scoring(
    project_config: dict,
    input_files: dict,
    log_analysis: dict,
    expected_values: dict,
) -> dict:
    """
    Run the LLM scoring agent: compare actual payload against expected values.
    """
    mandatory_fields = project_config.get("mandatory_fields", [])
    mandatory_section = (
        f"\nMandatory Fields (any missing/wrong → status forced to failed):\n{json.dumps(mandatory_fields)}\n"
        if mandatory_fields else "\nMandatory Fields: none configured\n"
    )

    collected = input_files.get("collected", {})
    mapping_payload = {
        k: v for k, v in collected.items()
        if k not in ("prompt-template", "runtime-prompt")
    }
    mapping_section = json.dumps(
        {k: (v[:500] + "...") if len(str(v)) > 500 else v for k, v in mapping_payload.items()},
        indent=2,
    )

    prompt = f"""
Project: {project_config.get('project_name')} ({project_config.get('project_id')})
Scoring weights: {json.dumps(project_config.get('scoring_weights', {}))}
{mandatory_section}
EXPECTED values — extracted from invoice PDF by independent LLM (ground truth):
{json.dumps(expected_values, indent=2)}

ACTUAL values — extracted by the invoice processor Lambda and sent to the Pando API:
{json.dumps(log_analysis.get('payload', {}), indent=2)}

Field and charge mappings loaded from S3:
{mapping_section}

Invoice number : {log_analysis.get('invoice_number', 'unknown')}
Processor errors : {json.dumps(log_analysis.get('errors', []))}
API status from processor : {log_analysis.get('api_status')}

Validate every field. Compare ACTUAL against EXPECTED (PDF ground truth).
Also apply the charge mapping and field mapping rules.
Return the scored JSON result.
"""

    agent = Agent(model=_make_model(), tools=[], system_prompt=_SCORING_PROMPT)
    result_text = str(agent(prompt)).strip()

    start = result_text.find("{")
    end = result_text.rfind("}") + 1
    if start >= 0 and end > start:
        parsed = json.loads(result_text[start:end])
        if "field_validations" in parsed and "overall_score" in parsed:
            return parsed

    raise ValueError("Scoring agent returned no parseable JSON")


# ── Fallback: rule-based scoring ───────────────────────────────────────────────

def _fallback_scoring(
    actual: dict,
    expected: dict,
    mandatory_fields: list,
    project_config: dict,
) -> dict:
    """Simple rule-based fallback used when Bedrock is unavailable."""
    weights = project_config.get("scoring_weights", {
        "charge_fields": 25, "address_fields": 25,
        "date_fields": 25,   "amount_fields": 25,
    })
    mandatory_set = {f.lower() for f in mandatory_fields}

    all_fields = list(set(list(expected.keys()) + list(actual.keys()) + mandatory_fields))
    validations = []

    for field in all_fields:
        if field in ("charge_items",):
            continue
        exp_val = expected.get(field)
        act_val = actual.get(field)

        if act_val is None or act_val == "" or act_val == "null":
            status = "missing"
        else:
            status = classify_field(field, exp_val, act_val)

        validations.append({
            "field_name":     field,
            "expected_value": str(exp_val) if exp_val is not None else None,
            "actual_value":   str(act_val) if act_val is not None else None,
            "status":         status,
            "source_used":    "Invoice PDF" if field in expected else "Mandatory Field",
            "is_mandatory":   field.lower() in mandatory_set,
        })

    charge_f  = ["charge_code", "charge_type", "freight_charge", "surcharge"]
    address_f = ["origin_country", "destination_country", "shipper_name", "consignee_name"]
    date_f    = ["invoice_date", "payment_due_date"]
    amount_f  = ["total_invoice_value", "net_invoice_value"]

    def _cat_score(fields, weight):
        m = [v for v in validations if v["field_name"] in fields]
        if not m:
            return weight
        return (sum(1 for v in m if v["status"] == "correct") / len(m)) * weight

    score = round(
        _cat_score(charge_f,  weights.get("charge_fields",  25)) +
        _cat_score(address_f, weights.get("address_fields", 25)) +
        _cat_score(date_f,    weights.get("date_fields",    25)) +
        _cat_score(amount_f,  weights.get("amount_fields",  25)),
        1,
    )
    status = "passed" if score >= 85 else "warning" if score >= 60 else "failed"

    return {
        "overall_score":     score,
        "status":            status,
        "field_validations": validations,
        "suggestions":       ["Bedrock unavailable — rule-based fallback used."],
        "expected_from_pdf": expected,
    }


# ── Public entry point ─────────────────────────────────────────────────────────

def run_scoring_agent(
    project_config: dict,
    input_files: dict,
    log_analysis: dict,
) -> dict:
    """
    Full two-phase scoring pipeline.

    Phase 1: Extract expected values from the invoice PDF using Claude.
    Phase 2: Score actual payload (from invoice processor) against expected values.

    Returns the same schema as payload_validator so it is a drop-in replacement.
    """
    mandatory_fields = project_config.get("mandatory_fields", [])
    invoice_pdf      = log_analysis.get("invoice_pdf", "").strip()

    # Phase 1 ─────────────────────────────────────────────────────────────────
    print("[ScoringAgent] Phase 1: Extracting expected values from invoice PDF…")
    expected_values = _extract_expected_from_pdf(
        invoice_pdf,
        project_config=project_config,
        payload=log_analysis.get("payload") or {},
        layout=log_analysis.get("invoice_pdf_layout") or [],
    )

    # Phase 2 ─────────────────────────────────────────────────────────────────
    print("[ScoringAgent] Phase 2: Scoring actual vs expected…")
    weights = project_config.get("scoring_weights")
    payload = log_analysis.get("payload", {})
    try:
        result = _run_scoring(project_config, input_files, log_analysis, expected_values)
        result["expected_from_pdf"] = expected_values
        apply_comparison(result, payload=payload, weights=weights, mandatory_fields=mandatory_fields)
        apply_semantic_equivalence(result, weights=weights, mandatory_fields=mandatory_fields)
        return _enforce_mandatory(result, mandatory_fields)
    except Exception as e:
        print(f"[ScoringAgent] LLM scoring failed, using fallback: {e}")
        result = _fallback_scoring(
            payload,
            expected_values,
            mandatory_fields,
            project_config,
        )
        apply_comparison(result, payload=payload, weights=weights, mandatory_fields=mandatory_fields)
        apply_semantic_equivalence(result, weights=weights, mandatory_fields=mandatory_fields)
        return _enforce_mandatory(result, mandatory_fields)

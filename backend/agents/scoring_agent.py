"""
Scoring Agent
Validates invoice payloads using a two-phase LLM approach:

  Phase 1 — PDF Extraction
    Claude independently reads the invoice PDF and extracts the EXPECTED field values.
    This is the ground truth — what the invoice actually says.

  Phase 2 — Actual vs Expected Scoring
    Claude compares the ACTUAL payload (sent by the invoice processor Lambda)
    against the EXPECTED values from Phase 1, plus the Excel field/charge mappings.

This gives true actual-vs-expected comparison, not just "is the field present?"
"""

import json
from strands import Agent
from strands.models.bedrock import BedrockModel
from config import settings


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
  "correct"  — actual matches expected exactly or is semantically identical
  "wrong"    — value is present but differs from expected
  "missing"  — expected a value but actual is null, empty, or absent

Charge validation:
  Each charge in the charge mapping must appear in the actual payload's custom_fields,
  charge_code, or charge_type fields. Validate code and name against the vendor mapping.

Scoring (weights from project config):
  charge_fields: 25%  |  address_fields: 25%  |  date_fields: 25%  |  amount_fields: 25%
  passed ≥ 85  |  warning ≥ 60  |  failed < 60

Mandatory fields rule:
  If ANY mandatory field is missing or wrong → force status = "failed" regardless of score.

Generate specific, actionable suggestions for improving the Lambda's LLM prompt
for every wrong or missing field (explain what instruction to add or change).

Respond with ONLY a valid JSON object — no text outside it:
{
  "overall_score": <float 0.0-100.0>,
  "status": "passed" | "warning" | "failed",
  "field_validations": [
    {
      "field_name":     "<name>",
      "expected_value": "<from PDF or mapping>",
      "actual_value":   "<from Lambda payload>",
      "status":         "correct" | "wrong" | "missing",
      "source_used":    "Invoice PDF" | "Field Mapping Sheet" | "Charge Map Sheet",
      "is_mandatory":   true | false
    }
  ],
  "suggestions": ["<specific prompt improvement>", ...]
}
"""


# ── Model factory ──────────────────────────────────────────────────────────────

def _make_model() -> BedrockModel:
    return BedrockModel(
        region_name=settings.AWS_REGION,
        model_id=settings.BEDROCK_MODEL_ID,
        max_tokens=8192,
    )


# ── Phase 1: PDF extraction ────────────────────────────────────────────────────

def _extract_expected_from_pdf(pdf_markdown: str) -> dict:
    """
    Use Claude to read the invoice PDF text and extract exact expected field values.
    Returns {field_name: expected_value, ...} or {} if extraction fails.
    """
    if not pdf_markdown or not pdf_markdown.strip():
        print("[ScoringAgent] No PDF content — skipping expected-value extraction.")
        return {}

    try:
        agent = Agent(model=_make_model(), tools=[], system_prompt=_EXTRACTION_PROMPT)
        result = agent(
            f"Extract all invoice field values from this invoice PDF:\n\n"
            f"---\n{pdf_markdown[:8000]}\n---"
        )
        text = str(result).strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end])
            print(f"[ScoringAgent] Extracted {len(parsed)} expected fields from PDF.")
            return parsed
    except Exception as e:
        print(f"[ScoringAgent] PDF extraction failed: {e}")

    return {}


# ── Mandatory field helpers ────────────────────────────────────────────────────

def _build_mandatory_result(field_validations: list, mandatory_fields: list) -> dict:
    if not mandatory_fields:
        return {"total": 0, "passed": 0, "failed": 0, "failed_fields": []}

    mandatory_set = {f.lower() for f in mandatory_fields}
    failed_fields, passed_count = [], 0

    for v in field_validations:
        if v.get("field_name", "").lower() in mandatory_set:
            if v.get("status") == "correct":
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
    mapping_section = json.dumps(
        {k: (v[:500] + "...") if len(str(v)) > 500 else v for k, v in collected.items()},
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
        elif exp_val is None:
            status = "correct"
        else:
            ne = str(exp_val).strip().lower().replace("-", "").replace(" ", "")
            na = str(act_val).strip().lower().replace("-", "").replace(" ", "")
            status = "correct" if ne == na else "wrong"

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
    expected_values = _extract_expected_from_pdf(invoice_pdf)

    # Phase 2 ─────────────────────────────────────────────────────────────────
    print("[ScoringAgent] Phase 2: Scoring actual vs expected…")
    try:
        result = _run_scoring(project_config, input_files, log_analysis, expected_values)
        result["expected_from_pdf"] = expected_values
        return _enforce_mandatory(result, mandatory_fields)
    except Exception as e:
        print(f"[ScoringAgent] LLM scoring failed, using fallback: {e}")
        result = _fallback_scoring(
            log_analysis.get("payload", {}),
            expected_values,
            mandatory_fields,
            project_config,
        )
        return _enforce_mandatory(result, mandatory_fields)

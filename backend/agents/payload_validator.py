"""
Payload Validator Agent
Compares the extracted Lambda payload against expected values using field mappings.
Uses LLM reasoning to handle fuzzy matches, classify mismatches, and generate
actionable prompt improvement suggestions.
"""

import json
from strands import Agent, tool
from strands.models.bedrock import BedrockModel

from config import settings


_SYSTEM_PROMPT = """\
You are the Payload Validator agent for the Pando Invoice Testing system.

Your job:
Given a project configuration, collected mapping files, and extracted invoice payload data,
validate each invoice field against its expected value.

── Collected file types ──────────────────────────────────────────────────────
A collected file may be plain text OR an Excel-parsed JSON object of this shape:
{
  "type": "excel_mapping",
  "vendor_ref_id": "CLIM",
  "field_mapping": [
    {
      "field_name": "invoice_number",
      "payload_field": "invoice_number",
      "ingestion_logic": "Extraction Logic",
      "mapping_required": "No",
      "remarks": "E.g.: Invoice #: 2390575 / Extracted Data: 2390575",
      "value_present": "Available in Payload"
    },
    ...
  ],
  "charge_mapping": [
    {"charge_code": "400", "master_name": "Base Freight",   "vendor_name": "Freight Charge"},
    {"charge_code": "405", "master_name": "Fuel Surcharge", "vendor_name": "Fuel Surcharge"},
    ...
  ]
}

When you see an excel_mapping object:
1. Use field_mapping to know which payload fields to validate and what is expected.
   - "remarks" often contains example extracted values — use them as expected values.
   - If value_present = "Available in Payload", the field must be present and non-empty.
   - If value_present = "Not Required", skip validation for that field.
   - Use "Field Mapping Sheet" as source_used for these validations.

2. Use charge_mapping to validate charge-related fields in the payload.
   - The invoice payload may contain charge codes or names inside custom_fields,
     charge_code, charge_type, or similar fields.
   - Match each extracted charge against the vendor_name column for that vendor.
   - "correct" → charge code/name matches the vendor mapping.
   - "wrong"   → charge is present but uses an incorrect code or name.
   - "missing" → a charge listed in the mapping is absent from the payload.
   - Use "Charge Map Sheet" as source_used for these validations.

── Charge mapping from Lambda prompt ────────────────────────────────────────
If "Lambda Prompt Text" is also provided, scan it for any additional embedded
charge code mapping rules not covered by the Excel file, and apply those too.
Use "Embedded Prompt Mapping" as source_used for those validations.

── Invoice PDF (ground truth) ───────────────────────────────────────────────
If "Invoice PDF Content" is provided (the actual scanned/digital invoice as
markdown text), treat it as the definitive ground truth for all field values:
- Cross-check every payload field against the corresponding value on the PDF.
- If the payload value matches what is printed on the invoice → "correct".
- If the payload value differs from what is on the invoice → "wrong";
  set expected_value to what the PDF shows.
- If a mandatory or mapped field is present on the PDF but absent in the
  payload → "missing".
- Use "Invoice PDF" as source_used for these validations.
- PDF validation takes precedence over mapping-file hints when values conflict.

── Container fields ─────────────────────────────────────────────────────────
- Container fields are nested inside payload.shipments[].container (an array).
- If the container array is empty ([]) or absent, SKIP all container-level
  field validations entirely — do not mark container fields as missing.
- Only validate container fields (container_number, container_type,
  container_weight, no_of_containers, etc.) when the array has at least
  one entry.

── Validation rules ─────────────────────────────────────────────────────────
- "correct": exact match or semantically identical
- "wrong":   value present but incorrect
- "missing": expected a value but got null or field is absent

── Mandatory fields ──────────────────────────────────────────────────────────
- Set is_mandatory=true for every field listed under "Mandatory Fields".
- If ANY mandatory field is missing, force overall status = "failed".

── Scoring ───────────────────────────────────────────────────────────────────
- correct = full weight, wrong = 0, missing = 0
- Apply scoring_weights: charge_fields, address_fields, date_fields, amount_fields
- Thresholds: passed ≥ 85, warning ≥ 60, failed < 60

After validating, generate specific actionable prompt improvement suggestions.

Respond with ONLY a valid JSON object — no text outside it:
{
  "overall_score": <float 0.0-100.0>,
  "status": "passed" | "warning" | "failed",
  "field_validations": [
    {
      "field_name": "<name>",
      "expected_value": "<expected or null>",
      "actual_value": "<actual or null>",
      "status": "correct" | "wrong" | "missing",
      "source_used": "Field Mapping Sheet | Charge Map Sheet | Embedded Prompt Mapping | LLM Response",
      "is_mandatory": true | false
    }
  ],
  "suggestions": ["<specific prompt improvement>", ...]
}
"""


@tool
def compare_field_values(field_name: str, expected: str, actual: str) -> str:
    """
    Compare an expected field value against the actual extracted value.
    Returns 'correct', 'wrong', or 'missing' based on the comparison.
    field_name: the invoice field being compared
    expected: the expected/reference value
    actual: the value extracted by the Lambda
    """
    if actual is None or actual == "" or actual == "null":
        return "missing"
    if expected is None:
        return "correct" if actual else "missing"
    norm_expected = str(expected).strip().lower().replace("-", "").replace(" ", "")
    norm_actual   = str(actual).strip().lower().replace("-", "").replace(" ", "")
    return "correct" if norm_expected == norm_actual else "wrong"


@tool
def calculate_weighted_score(validations_json: str, weights_json: str) -> str:
    """
    Calculate the overall score from field validations and scoring weights.
    validations_json: JSON array of {field_name, status} objects
    weights_json: JSON object with charge_fields, address_fields, date_fields, amount_fields keys
    Returns a JSON object with overall_score (float) and status string.
    """
    validations = json.loads(validations_json)
    weights = json.loads(weights_json)

    charge_fields  = ["charge_code", "charge_type", "freight_charge", "surcharge"]
    address_fields = ["origin_country", "destination_country", "shipper_name", "consignee_name",
                      "shipper_address", "consignee_address", "carrier_name"]
    date_fields    = ["invoice_date", "shipment_date", "delivery_date", "eta"]
    amount_fields  = ["invoice_total", "amount_due", "tax_amount", "freight_amount"]

    def _score_category(fields, category_weight):
        matching = [v for v in validations if v["field_name"] in fields]
        if not matching:
            return category_weight  # no fields → full score for that category
        correct = sum(1 for v in matching if v["status"] == "correct")
        return (correct / len(matching)) * category_weight

    score = (
        _score_category(charge_fields,  weights.get("charge_fields",  25)) +
        _score_category(address_fields, weights.get("address_fields", 25)) +
        _score_category(date_fields,    weights.get("date_fields",    25)) +
        _score_category(amount_fields,  weights.get("amount_fields",  25))
    )

    status = "passed" if score >= 85 else "warning" if score >= 60 else "failed"
    return json.dumps({"overall_score": round(score, 1), "status": status})


def _make_model() -> BedrockModel:
    return BedrockModel(
        region_name=settings.AWS_REGION,
        model_id=settings.BEDROCK_MODEL_ID,
        max_tokens=8192,
    )


def _build_mandatory_result(field_validations: list, mandatory_fields: list) -> dict:
    """Summarise how mandatory fields performed and enforce failed status if any are missing."""
    if not mandatory_fields:
        return {"total": 0, "passed": 0, "failed": 0, "failed_fields": []}

    mandatory_set = {f.lower() for f in mandatory_fields}
    failed_fields = []
    passed_count  = 0

    for v in field_validations:
        if v.get("field_name", "").lower() in mandatory_set:
            if v.get("status") != "correct":
                failed_fields.append(v["field_name"])
            else:
                passed_count += 1

    # Fields listed as mandatory but not found at all in validations → failed
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
    """
    Post-process agent result:
    - Tag each validation entry with is_mandatory
    - If any mandatory field is missing/wrong, force overall status to 'failed'
    - Attach mandatory_fields_result summary
    """
    mandatory_set = {f.lower() for f in mandatory_fields}

    for v in result.get("field_validations", []):
        v["is_mandatory"] = v.get("field_name", "").lower() in mandatory_set

    mandatory_result = _build_mandatory_result(result.get("field_validations", []), mandatory_fields)

    if mandatory_result["failed"] > 0:
        result["status"] = "failed"

    result["mandatory_fields_result"] = mandatory_result
    return result


def _fallback_validation(project_config: dict, log_analysis: dict) -> dict:
    """Rule-based fallback when the agent is unavailable."""
    payload          = log_analysis.get("payload", {})
    weights          = project_config.get("scoring_weights", {})
    mandatory_fields = project_config.get("mandatory_fields", [])
    mandatory_set    = {f.lower() for f in mandatory_fields}

    def _val(v):
        return str(v) if v is not None and v != "" else None

    custom = payload.get("custom") or {}

    field_map = {
        # Real Pando field names (payload.custom.vendor_name is the carrier)
        "vendor_name":            _val(custom.get("vendor_name") or payload.get("vendor_name") or payload.get("carrier")),
        "invoice_number":         _val(payload.get("invoice_number")),
        "invoice_date":           _val(payload.get("invoice_date")),
        "total_invoice_value":    _val(payload.get("total_invoice_value") or payload.get("invoice_total")),
        "currency":               _val(payload.get("currency")),
        "bill_of_lading_number":  _val(payload.get("bill_of_lading_number")),
        "payment_terms":          _val(payload.get("payment_terms")),
        "net_invoice_value":      _val(payload.get("net_invoice_value")),
    }

    # Container fields: only validate when at least one container entry exists
    _container_field_prefixes = ("container_number", "container_type", "container_weight",
                                  "no_of_containers", "container_id", "container_weight_uom")
    _has_containers = any(
        isinstance(s, dict) and isinstance(s.get("container"), list) and len(s["container"]) > 0
        for s in (payload.get("shipments") or payload.get("data", [{}]))
        if isinstance(s, dict)
    )

    # Ensure every mandatory field appears in the validation list,
    # but skip container fields if the container array is empty
    for mf in mandatory_fields:
        if mf.lower() not in {k.lower() for k in field_map}:
            if any(mf.lower().startswith(p) for p in _container_field_prefixes) and not _has_containers:
                continue  # skip — no containers in this invoice
            field_map[mf] = _val(payload.get(mf))

    validations = []
    for field, actual in field_map.items():
        status = "missing" if actual is None else "correct"
        validations.append({
            "field_name":     field,
            "expected_value": None,
            "actual_value":   str(actual) if actual is not None else None,
            "status":         status,
            "source_used":    "LLM Response",
            "is_mandatory":   field.lower() in mandatory_set,
        })

    score_result = json.loads(calculate_weighted_score(
        json.dumps([{"field_name": v["field_name"], "status": v["status"]} for v in validations]),
        json.dumps(weights or {"charge_fields": 25, "address_fields": 25,
                               "date_fields": 25, "amount_fields": 25}),
    ))

    result = {
        "overall_score":   score_result["overall_score"],
        "status":          score_result["status"],
        "field_validations": validations,
        "suggestions":     ["Connect field mapping files to enable detailed validation"],
    }

    return _enforce_mandatory(result, mandatory_fields)


def run_payload_validator(
    project_config: dict,
    input_files: dict,
    log_analysis: dict,
) -> dict:
    """
    Validate the extracted invoice payload against expected values.
    Returns field_validations, overall_score, status, suggestions, and mandatory_fields_result.
    """
    mandatory_fields = project_config.get("mandatory_fields", [])

    try:
        agent = Agent(
            model=_make_model(),
            tools=[compare_field_values, calculate_weighted_score],
            system_prompt=_SYSTEM_PROMPT,
        )

        mandatory_section = (
            f"\nMandatory Fields (must be present — any missing one forces status=failed):\n"
            f"{json.dumps(mandatory_fields)}\n"
            if mandatory_fields else "\nMandatory Fields: none configured\n"
        )

        prompt_text = log_analysis.get("prompt_text", "").strip()
        prompt_text_section = (
            f"\nLambda Prompt Text (scan for embedded charge mapping rules):\n"
            f"---\n{prompt_text[:4000]}\n---\n"
            if prompt_text else ""
        )

        invoice_pdf = log_analysis.get("invoice_pdf", "").strip()
        pdf_section = (
            f"\nInvoice PDF Content (ground truth — use this to cross-check all payload values):\n"
            f"---\n{invoice_pdf[:6000]}\n---\n"
            if invoice_pdf else ""
        )

        prompt = f"""
Project: {project_config.get('project_name')} ({project_config.get('project_id')})

Scoring weights:
{json.dumps(project_config.get('scoring_weights', {}), indent=2)}
{mandatory_section}{prompt_text_section}{pdf_section}
Collected input files (field mappings, prompt template, etc.):
{json.dumps({k: v[:500] + '...' if len(str(v)) > 500 else v
             for k, v in input_files.get('collected', {}).items()}, indent=2)}

Extracted invoice data from CloudWatch logs:
Invoice number: {log_analysis.get('invoice_number', 'unknown')}
Raw payload: {json.dumps(log_analysis.get('payload', {}), indent=2)}
Errors from logs: {json.dumps(log_analysis.get('errors', []))}
Warnings from logs: {json.dumps(log_analysis.get('warnings', []))}

Validate every field in the payload against the Invoice PDF (ground truth),
the field mapping sheet, and the charge mapping. Return the JSON result.
"""
        result = agent(prompt)
        text = str(result).strip()

        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end])
            if "field_validations" in parsed and "overall_score" in parsed:
                return _enforce_mandatory(parsed, mandatory_fields)
    except Exception as e:
        print(f"[PayloadValidator] Agent error: {e}")

    return _fallback_validation(project_config, log_analysis)

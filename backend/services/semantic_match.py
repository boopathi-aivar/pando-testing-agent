"""
LLM pass for remaining 'wrong' pairs after deterministic comparison.

Used at scoring time only (not on every results GET). Marks
equivalent_match so later display re-classify keeps the verdict.
"""

from __future__ import annotations

import json

from services.field_compare import AMOUNT_FIELDS, parse_amount, rescore, tag_mandatory

_SEMANTIC_PROMPT = """\
You compare freight-invoice field values. For each pair, decide if they
mean the SAME real-world value. Formatting differences are not errors.

Treat as equivalent:
- Same calendar day in any format (08/12/2026 == 12-Aug-2026)
- Same company ignoring case, punctuation, Inc/LLC/Ltd/Co/Company
  MADISON LOGISTICS INC == Madison Logistics
  M & M Cartage Co., Inc. == M & M CARTAGE COMPANY INC
- Same country as ISO code or English name (US == USA == United States)
- Same street with extra city/state/zip on one side only
- Same charge type with extra qualifier words
  Fuel Surcharge Miles == Fuel Surcharge
  Fuel Surcharge == Fuel Surcharge Fee
  Line Haul == Linehaul

Do NOT treat as equivalent:
- Different companies, cities, or people
- Different charge types (APPLIANCE PARTS != Base Freight, Flat Rate != Fuel Surcharge)
- Short codes that are not the same entity (MRO vs Monogram Refrigeration)
- Different amounts or different calendar days
- A field that extracted the wrong concept (shipper in source_name, etc.)

Return ONLY JSON:
{"matches": [{"field_name": "<name>", "equivalent": true|false, "reason": "<short>"}]}
"""


def _skip_numeric_mismatch(row: dict) -> bool:
    fname = (row.get("field_name") or "").lower()
    if fname in AMOUNT_FIELDS or any(tok in fname for tok in ("amount", "value", "total", "tax")):
        a1 = parse_amount(row.get("expected_value"))
        a2 = parse_amount(row.get("actual_value"))
        if a1 is not None and a2 is not None:
            return True
    return False


def _llm_equivalent_fields(rows: list[dict]) -> set[str]:
    from strands import Agent
    from strands.models.bedrock import BedrockModel
    from config import settings

    payload = [
        {
            "field_name": r.get("field_name"),
            "expected": r.get("expected_value"),
            "actual": r.get("actual_value"),
        }
        for r in rows
    ]
    model = BedrockModel(
        region_name=settings.AWS_REGION,
        model_id=settings.BEDROCK_MODEL_ID,
        max_tokens=2048,
    )
    agent = Agent(model=model, tools=[], system_prompt=_SEMANTIC_PROMPT)
    text = str(agent(f"Compare these pairs:\n{json.dumps(payload, indent=2)}")).strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        return set()
    parsed = json.loads(text[start:end])
    matches = parsed.get("matches") if isinstance(parsed, dict) else parsed
    if not isinstance(matches, list):
        return set()
    equivalent = set()
    for item in matches:
        if isinstance(item, dict) and item.get("equivalent") is True:
            name = item.get("field_name")
            if name:
                equivalent.add(str(name))
    return equivalent


def apply_semantic_equivalence(
    result: dict,
    weights: dict | None = None,
    mandatory_fields: list | None = None,
) -> dict:
    """Flip remaining semantic-equivalent 'wrong' rows to correct via LLM."""
    validations = result.get("field_validations") or []
    candidates = [
        v for v in validations
        if isinstance(v, dict)
        and v.get("status") == "wrong"
        and not _skip_numeric_mismatch(v)
    ]
    if not candidates:
        return result

    try:
        equivalent = _llm_equivalent_fields(candidates)
    except Exception as e:
        print(f"[SemanticMatch] skipped: {e}")
        return result

    if not equivalent:
        return result

    flipped = 0
    for v in validations:
        name = v.get("field_name")
        if v.get("status") == "wrong" and name in equivalent:
            v["status"] = "correct"
            v["equivalent_match"] = True
            flipped += 1

    if flipped:
        print(f"[SemanticMatch] marked {flipped} field(s) equivalent.")
        tag_mandatory(validations, mandatory_fields)
        score, status = rescore(validations, weights, mandatory_fields=mandatory_fields)
        result["overall_score"] = score
        if result.get("status") != "failed" or validations:
            result["status"] = status

    return result

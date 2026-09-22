"""Extraction node — map merged text using session_prompt (+ schema)."""

from __future__ import annotations

import json
import logging

from config import JSON_REPAIR_ATTEMPTS
from graph.state import InvoiceState
from utils.json_parser import parse_json_loose
from utils.llm import get_llm

logger = logging.getLogger(__name__)

FALLBACK_SYSTEM = """You are an invoice data extraction engine.
Map the invoice text to the JSON Schema below.
Return ONLY valid JSON that conforms to the schema.
Do not invent values that are not present in the text.
If a field is missing in the text, use null (or omit optional fields).
No markdown fences. No explanations.

JSON Schema:
{schema}
"""


def extraction_node(state: InvoiceState) -> dict:
    if state.get("fatal_error"):
        return {"extracted_json": {}}

    merged = (state.get("merged_text") or "").strip()
    schema = state.get("json_schema") or {}
    session_prompt = (state.get("session_prompt") or "").strip()

    if not merged:
        return {
            "extracted_json": {},
            "validation_errors": ["No text extracted from PDF."],
        }

    llm = get_llm(json_mode=True)

    if session_prompt:
        system = (
            f"{session_prompt}\n\n"
            "OUTPUT FORMAT:\n"
            "Return ONLY valid JSON that conforms to this JSON Schema. "
            "No markdown fences. No explanations.\n\n"
            f"JSON Schema:\n{json.dumps(schema, indent=2)}"
        )
    else:
        system = FALLBACK_SYSTEM.format(schema=json.dumps(schema, indent=2))

    user_msg = f"Invoice text:\n\n{merged}"

    last_raw = ""
    for attempt in range(1, JSON_REPAIR_ATTEMPTS + 1):
        try:
            if attempt == 1:
                messages = [
                    ("system", system),
                    ("human", user_msg),
                ]
            else:
                messages = [
                    ("system", system),
                    ("human", user_msg),
                    (
                        "human",
                        "Your previous reply was not valid JSON. "
                        f"Reply with ONLY a JSON object. Previous reply:\n{last_raw[:2000]}",
                    ),
                ]
            response = llm.invoke(messages)
            last_raw = getattr(response, "content", "") or ""
            parsed = parse_json_loose(last_raw)
            if isinstance(parsed, dict):
                logger.info("Extraction succeeded on attempt %d", attempt)
                return {"extracted_json": parsed, "validation_errors": []}
            if isinstance(parsed, list):
                logger.info("Extraction returned list; wrapping on attempt %d", attempt)
                return {"extracted_json": {"items": parsed}, "validation_errors": []}
            logger.warning("Extraction attempt %d: invalid JSON", attempt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Extraction attempt %d failed: %s", attempt, exc)
            last_raw = str(exc)

    return {
        "extracted_json": {},
        "validation_errors": [
            "LLM failed to return valid JSON after retries.",
            f"Last raw output (truncated): {last_raw[:500]}",
        ],
    }

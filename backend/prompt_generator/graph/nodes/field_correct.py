"""Field-level + generic-note correction: refine session_prompt and remap JSON."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from prompt_generator.graph.nodes.extraction import extraction_node
from prompt_generator.graph.nodes.validation import validation_node
from prompt_generator.graph.state import InvoiceState
from prompt_generator.utils.json_parser import parse_json_loose
from prompt_generator.utils.llm import get_llm
from prompt_generator.utils.prompt_quality import check_prompt_coverage, prompt_diff

logger = logging.getLogger(__name__)

REFINE_SYSTEM = """You refine freight-invoice extraction prompts based on human feedback.

You are given:
1. The current extraction prompt
2. Optional per-field corrections (fields marked wrong + notes)
3. Optional generic_note — a global instruction that applies to the whole extraction
4. The JSON schema

Update the prompt so future extractions follow that feedback.
Keep the same overall structure.
Incorporate generic_note as durable IMPORTANT INSTRUCTIONS when present.
Strengthen or rewrite field rules for any per-field corrections; keep other fields intact when possible.
Output ONLY the revised prompt text. No markdown fences. Do not extract invoice data."""

REMAP_SYSTEM = """You correct structured invoice extraction results.

Given invoice text, the JSON schema, the current extracted JSON, and human feedback, return a
FULL corrected JSON object that conforms to the schema.

Rules:
- Apply generic_note across the whole object when provided.
- Fix marked fields using the invoice text and user notes.
- Keep unmarked fields unchanged unless generic_note or a related fix requires change.
- Do not invent values not supported by the text or notes.
- Return ONLY valid JSON. No markdown fences."""


def refine_session_prompt(
    session_prompt: str,
    schema: dict,
    corrections: list[dict[str, Any]],
    generic_note: str = "",
) -> str:
    llm = get_llm(json_mode=False)
    payload = {
        "current_prompt": session_prompt,
        "json_schema": schema,
        "corrections": corrections,
        "generic_note": generic_note or None,
    }
    response = llm.invoke(
        [("system", REFINE_SYSTEM), ("human", json.dumps(payload, indent=2))]
    )
    text = (getattr(response, "content", "") or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text or session_prompt


def remap_extracted_json(
    state: InvoiceState,
    corrections: list[dict[str, Any]],
    generic_note: str = "",
) -> dict:
    llm = get_llm(json_mode=True)
    payload = {
        "json_schema": state.get("json_schema") or {},
        "current_extracted_json": state.get("extracted_json") or {},
        "corrections": corrections,
        "generic_note": generic_note or None,
        "invoice_text": state.get("merged_text") or "",
    }
    response = llm.invoke(
        [("system", REMAP_SYSTEM), ("human", json.dumps(payload, indent=2)[:180000])]
    )
    raw = getattr(response, "content", "") or ""
    parsed = parse_json_loose(raw)
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        return {"items": parsed}
    raise ValueError(f"Remap did not return valid JSON: {raw[:400]}")


def apply_field_corrections(
    state: InvoiceState,
    corrections: Optional[list[dict[str, Any]]] = None,
    *,
    generic_note: str = "",
    refine_prompt: bool = True,
) -> dict:
    corrections = list(corrections or [])
    note = (generic_note or "").strip()
    if not corrections and not note:
        raise ValueError("Provide at least one field correction or a generic_note.")

    updates: dict[str, Any] = {}
    session_prompt = state.get("session_prompt") or ""
    schema = state.get("json_schema") or {}

    if refine_prompt and session_prompt:
        logger.info("Refining session_prompt (corrections=%d, note=%s)", len(corrections), bool(note))
        before_prompt = session_prompt
        new_prompt = refine_session_prompt(session_prompt, schema, corrections, generic_note=note)
        updates["session_prompt"] = new_prompt
        updates["prompt_diff"] = prompt_diff(before_prompt, new_prompt)
        updates["prompt_coverage"] = check_prompt_coverage(schema, new_prompt)
        history = list(state.get("prompt_history") or [])
        history.append(
            {
                "action": "refined",
                "corrections": corrections,
                "generic_note": note or None,
                "prompt": new_prompt,
                "diff_stats": updates["prompt_diff"].get("stats"),
            }
        )
        updates["prompt_history"] = history
        session_prompt = new_prompt
    else:
        updates["prompt_diff"] = {
            "changed": False,
            "unified_diff": "",
            "stats": {"lines_added": 0, "lines_removed": 0},
        }

    working = {**state, **updates, "session_prompt": session_prompt}
    logger.info("Remapping extracted JSON")
    try:
        remapped = remap_extracted_json(working, corrections, generic_note=note)
    except Exception:
        logger.warning("Direct remap failed; falling back to full extraction")
        extract_out = extraction_node(working)
        remapped = extract_out.get("extracted_json") or {}
        if extract_out.get("validation_errors"):
            updates["validation_errors"] = extract_out["validation_errors"]

    updates["extracted_json"] = remapped
    validated = validation_node({**working, "extracted_json": remapped})
    updates.update(validated)
    return updates

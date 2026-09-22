"""Generate an extraction prompt from schema + sample style + optional mapping."""

from __future__ import annotations

import json
import logging

from prompt_generator.graph.state import InvoiceState
from prompt_generator.utils.llm import get_llm
from prompt_generator.utils.prompt_quality import check_prompt_coverage, match_schema_mapping

logger = logging.getLogger(__name__)

META_SYSTEM = """You write freight-invoice extraction prompts for production use.

You are given:
1. A JSON Schema — the fields the extraction must cover (SOURCE OF TRUTH for output shape)
2. A SAMPLE PROMPT — one existing carrier extraction template (style and structure reference only)
3. Optionally FIELD MAPPING text — invoice locations/remarks grouped by table name

Produce ONE new extraction prompt that an LLM will later use on invoice text.

Requirements:
- Match the SAMPLE PROMPT's structure and instruction style (role line, CARRIER CONTEXT if present,
  OUTPUT SHAPE, IMPORTANT INSTRUCTIONS, EXTRACT THE FOLLOWING FIELDS with per-field paragraphs).
- Cover EVERY field in the JSON Schema (including nested fields). Use schema "description" when present.
- When FIELD MAPPING is present:
  - Use table + column + location + remarks to write precise per-field rules.
  - Treat "Default: …" in location as a default-value rule.
  - Empty remarks are fine — still use location when present.
  - Do NOT invent locations for schema fields missing from the mapping.
  - Mapping fields not in the schema are hints only; do not require them in output unless schema has them.
- Adapt label variants, null rules, and pitfalls using the sample as a guide — do not invent unrelated
  carrier-specific charge lists or layout unless the sample or mapping provides them.
- Do NOT extract any invoice data.
- Do NOT wrap the prompt in markdown fences.
- Output ONLY the prompt text."""


def _quality_reports(state: InvoiceState, prompt: str) -> dict:
    schema = state.get("json_schema") or {}
    rows = state.get("field_mapping_rows") or []
    return {
        "prompt_coverage": check_prompt_coverage(schema, prompt),
        "mapping_match": match_schema_mapping(schema, rows),
    }


def prompt_gen_node(state: InvoiceState) -> dict:
    if state.get("fatal_error"):
        return {}

    existing = (state.get("session_prompt") or "").strip()
    if existing and not state.get("force_prompt_regen"):
        logger.info("Reusing existing session_prompt (%d chars)", len(existing))
        return {
            "session_prompt": existing,
            "force_prompt_regen": False,
            **_quality_reports(state, existing),
        }

    sample = (state.get("sample_prompt") or "").strip()
    if not sample:
        return {
            "session_prompt": "",
            "fatal_error": (
                "Sample prompt template is required. Paste one existing carrier "
                "extraction prompt as a style reference."
            ),
            "force_prompt_regen": False,
        }

    schema = state.get("json_schema") or {}
    mapping_text = (state.get("field_mapping_text") or "").strip()
    llm = get_llm(json_mode=False)

    user = (
        "Write the extraction prompt using the SAMPLE PROMPT as a style/structure reference "
        "and the JSON Schema as the field contract.\n\n"
        f"=== JSON SCHEMA (source of truth for output fields) ===\n"
        f"{json.dumps(schema, indent=2)}\n\n"
        f"=== SAMPLE PROMPT (existing carrier template — reference only) ===\n{sample}"
    )
    if mapping_text:
        user += (
            "\n\n=== FIELD MAPPING (optional — locations/remarks by table) ===\n"
            f"{mapping_text}"
        )

    try:
        response = llm.invoke([("system", META_SYSTEM), ("human", user)])
        text = (getattr(response, "content", "") or "").strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Prompt generation failed")
        return {
            "session_prompt": "",
            "fatal_error": f"Failed to generate extraction prompt: {exc}",
            "force_prompt_regen": False,
        }

    if not text:
        return {
            "session_prompt": "",
            "fatal_error": "LLM returned an empty extraction prompt.",
            "force_prompt_regen": False,
        }

    history = list(state.get("prompt_history") or [])
    history.append(
        {
            "action": "generated",
            "prompt": text,
            "used_sample": True,
            "used_field_mapping": bool(mapping_text),
        }
    )
    reports = _quality_reports(state, text)
    logger.info("Generated session_prompt (%d chars)", len(text))
    return {
        "session_prompt": text,
        "prompt_history": history,
        "force_prompt_regen": False,
        **reports,
    }

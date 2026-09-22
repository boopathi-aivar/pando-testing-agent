"""Validation node — schema presence checks + grounded accuracy score."""

from __future__ import annotations

import logging
import re
from typing import Any

from graph.state import InvoiceState

logger = logging.getLogger(__name__)


def _schema_required_paths(schema: dict, prefix: str = "") -> list[str]:
    """Collect required property paths from a JSON Schema (shallow + one level nested)."""
    paths: list[str] = []
    required = schema.get("required") or []
    props = schema.get("properties") or {}
    for key in required:
        path = f"{prefix}.{key}" if prefix else key
        paths.append(path)
        child = props.get(key)
        if isinstance(child, dict) and child.get("type") == "object":
            paths.extend(_schema_required_paths(child, path))
    return paths


def _get_by_path(data: Any, path: str) -> Any:
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _value_in_text(value: Any, text: str) -> bool:
    """Lightweight grounding: string/number values should appear in source text."""
    if value is None:
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        # Allow currency formatting variants
        raw = str(value)
        if raw in text:
            return True
        # 1234.5 vs 1,234.50
        compact = raw.replace(",", "")
        text_compact = text.replace(",", "")
        return compact in text_compact
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return False
        if v.lower() in text.lower():
            return True
        # Digits-only match for invoice numbers with OCR noise
        digits = re.sub(r"\D", "", v)
        if len(digits) >= 4 and digits in re.sub(r"\D", "", text):
            return True
        return False
    return True  # objects/lists scored via children elsewhere


def _score_field(value: Any, required: bool, merged_text: str) -> float:
    if value is None or value == "" or value == [] or value == {}:
        return 0.0 if required else 0.5
    if isinstance(value, (str, int, float)) and merged_text:
        if not _value_in_text(value, merged_text):
            return 0.4 if required else 0.3  # present but ungrounded
    return 1.0


def validation_node(state: InvoiceState) -> dict:
    if state.get("fatal_error"):
        return {
            "accuracy_score": 0.0,
            "validation_errors": [state["fatal_error"]],
            "confidence_scores": {},
        }

    schema = state.get("json_schema") or {}
    extracted = state.get("extracted_json") or {}
    merged = state.get("merged_text") or ""
    errors = list(state.get("validation_errors") or [])

    required_paths = _schema_required_paths(schema)
    # Also score top-level properties even if not required
    props = list((schema.get("properties") or {}).keys())
    all_paths = list(dict.fromkeys(required_paths + props))

    if not all_paths:
        # No schema guidance — score by non-empty top-level keys
        all_paths = list(extracted.keys()) if isinstance(extracted, dict) else []

    scores: dict[str, float] = {}
    for path in all_paths:
        required = path in required_paths or path.split(".")[0] in (schema.get("required") or [])
        value = _get_by_path(extracted, path) if "." in path else extracted.get(path)
        score = _score_field(value, required, merged)
        scores[path] = score
        if required and score < 0.5:
            errors.append(f"Missing or weak required field: {path}")

    accuracy = (sum(scores.values()) / len(scores)) if scores else 0.0
    # Penalize empty extraction
    if not extracted:
        accuracy = 0.0
        if "Empty extraction result." not in errors:
            errors.append("Empty extraction result.")

    logger.info("Accuracy score=%.3f errors=%d", accuracy, len(errors))
    return {
        "accuracy_score": round(accuracy, 3),
        "validation_errors": errors,
        "confidence_scores": scores,
    }

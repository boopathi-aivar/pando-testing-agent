"""Prompt quality helpers: coverage, schema↔mapping match, prompt diffs."""

from __future__ import annotations

import difflib
import re
from typing import Any


def collect_schema_field_names(schema: Any, prefix: str = "") -> list[str]:
    """Collect property names (and dotted paths) from a JSON Schema."""
    names: list[str] = []
    if not isinstance(schema, dict):
        return names

    props = schema.get("properties")
    if isinstance(props, dict):
        for key, child in props.items():
            path = f"{prefix}.{key}" if prefix else key
            names.append(path)
            names.append(key)
            names.extend(collect_schema_field_names(child, path))

    items = schema.get("items")
    if isinstance(items, dict):
        names.extend(collect_schema_field_names(items, prefix))

    # oneOf / anyOf
    for key in ("oneOf", "anyOf", "allOf"):
        alts = schema.get(key)
        if isinstance(alts, list):
            for alt in alts:
                names.extend(collect_schema_field_names(alt, prefix))

    # unique, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def schema_leaf_fields(schema: dict) -> list[str]:
    """Prefer unique leaf-ish property names for coverage (last path segment + top-level)."""
    all_names = collect_schema_field_names(schema)
    # Prefer dotted paths that look like real fields; also keep simple keys
    preferred: list[str] = []
    seen: set[str] = set()
    for name in all_names:
        # Skip wrapper-only noise if any
        leaf = name.split(".")[-1]
        if leaf in ("properties", "items", "value"):
            continue
        # Use leaf for matching in prompt text (prompts name fields by leaf)
        if leaf not in seen:
            seen.add(leaf)
            preferred.append(leaf)
    return preferred


def check_prompt_coverage(schema: dict, prompt: str) -> dict[str, Any]:
    """
    Verify schema field names appear in the generated prompt.

    Returns:
      {
        "total_fields": int,
        "covered": [...],
        "missing": [...],
        "coverage_ratio": float,
        "ok": bool,
      }
    """
    fields = schema_leaf_fields(schema or {})
    text = (prompt or "").lower()
    covered: list[str] = []
    missing: list[str] = []
    for field in fields:
        # Word-ish match: field name as whole token (underscores allowed)
        pattern = r"(?<![a-z0-9])" + re.escape(field.lower()) + r"(?![a-z0-9])"
        if re.search(pattern, text):
            covered.append(field)
        else:
            missing.append(field)
    total = len(fields)
    ratio = (len(covered) / total) if total else 1.0
    return {
        "total_fields": total,
        "covered": covered,
        "missing": missing,
        "coverage_ratio": round(ratio, 3),
        "ok": len(missing) == 0,
    }


def match_schema_mapping(
    schema: dict,
    mapping_rows: list[dict[str, str]] | None,
) -> dict[str, Any]:
    """
    Compare schema fields to mapping sheet columns.

    Returns report with:
      - schema_fields_missing_location: in schema, not in mapping (or mapping has no location)
      - mapping_fields_not_in_schema: in mapping, not in schema
      - matched: in both
    """
    schema_fields = set(schema_leaf_fields(schema or {}))
    rows = mapping_rows or []

    mapping_cols: set[str] = set()
    mapping_with_location: set[str] = set()
    for row in rows:
        col = (row.get("column") or "").strip()
        if not col:
            continue
        mapping_cols.add(col)
        if (row.get("location") or "").strip():
            mapping_with_location.add(col)

    matched = sorted(schema_fields & mapping_cols)
    schema_missing = sorted(schema_fields - mapping_cols)
    # Also flag schema fields present in mapping but without location
    schema_no_location = sorted(
        (schema_fields & mapping_cols) - mapping_with_location
    )
    mapping_extra = sorted(mapping_cols - schema_fields)

    return {
        "matched": matched,
        "schema_fields_with_no_mapping": schema_missing,
        "schema_fields_mapped_without_location": schema_no_location,
        "mapping_fields_not_in_schema": mapping_extra,
        "has_mapping": bool(rows),
        "summary": (
            f"{len(matched)} matched, "
            f"{len(schema_missing)} schema fields with no mapping row, "
            f"{len(mapping_extra)} mapping fields not in schema"
            if rows
            else "No field mapping uploaded"
        ),
    }


def prompt_diff(before: str, after: str, *, context_lines: int = 2) -> dict[str, Any]:
    """Unified diff between previous and refined session prompts."""
    before = before or ""
    after = after or ""
    if before == after:
        return {
            "changed": False,
            "unified_diff": "",
            "stats": {"lines_added": 0, "lines_removed": 0},
        }

    before_lines = before.splitlines()
    after_lines = after.splitlines()
    diff_lines = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile="prompt_before",
            tofile="prompt_after",
            lineterm="",
            n=context_lines,
        )
    )
    added = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))
    return {
        "changed": True,
        "unified_diff": "\n".join(diff_lines),
        "stats": {"lines_added": added, "lines_removed": removed},
    }

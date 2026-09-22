"""Validate user-provided JSON schemas."""

from __future__ import annotations

from typing import Any, Tuple

from jsonschema import Draft7Validator


def validate_user_schema(schema: Any) -> Tuple[bool, str]:
    """Return (ok, error_message). Empty error_message when ok."""
    if not isinstance(schema, dict):
        return False, "Schema must be a JSON object."
    if not schema:
        return False, "Schema is empty."
    try:
        Draft7Validator.check_schema(schema)
    except Exception as exc:  # noqa: BLE001 — surface any schema error to the user
        return False, f"Invalid JSON Schema: {exc}"
    return True, ""

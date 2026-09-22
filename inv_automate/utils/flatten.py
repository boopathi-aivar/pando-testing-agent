"""Flatten nested JSON into dotted key-value rows for the UI."""

from __future__ import annotations

from typing import Any


def flatten_json(data: Any, prefix: str = "") -> list[dict[str, Any]]:
    """Return [{path, value}] for leaf fields (arrays as JSON-ish strings or indexed)."""
    rows: list[dict[str, Any]] = []

    if isinstance(data, dict):
        if not data:
            rows.append({"path": prefix or "(root)", "value": {}})
            return rows
        for key, val in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(flatten_json(val, path))
        return rows

    if isinstance(data, list):
        if not data:
            rows.append({"path": prefix or "(root)", "value": []})
            return rows
        # Short primitive lists stay as one cell
        if all(not isinstance(x, (dict, list)) for x in data):
            rows.append({"path": prefix, "value": data})
            return rows
        for i, item in enumerate(data):
            rows.extend(flatten_json(item, f"{prefix}[{i}]"))
        return rows

    rows.append({"path": prefix or "(root)", "value": data})
    return rows


def set_by_path(data: dict, path: str, value: Any) -> dict:
    """Set a dotted / indexed path on a dict copy. Best-effort for simple paths."""
    import copy
    import re

    root = copy.deepcopy(data)
    # Split on . but keep [n] with previous segment
    parts = re.findall(r"[^.\[\]]+|\[\d+\]", path)
    if not parts:
        return root

    cur: Any = root
    for i, part in enumerate(parts[:-1]):
        if part.startswith("[") and part.endswith("]"):
            idx = int(part[1:-1])
            while len(cur) <= idx:
                cur.append({})
            cur = cur[idx]
        else:
            nxt = parts[i + 1] if i + 1 < len(parts) else None
            if nxt and nxt.startswith("["):
                cur = cur.setdefault(part, [])
            else:
                cur = cur.setdefault(part, {})

    last = parts[-1]
    if last.startswith("[") and last.endswith("]"):
        idx = int(last[1:-1])
        while len(cur) <= idx:
            cur.append(None)
        cur[idx] = value
    else:
        cur[last] = value
    return root

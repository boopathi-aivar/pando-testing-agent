"""Merge & clean page texts into one block."""

from __future__ import annotations

import logging
import re

from prompt_generator.graph.state import InvoiceState

logger = logging.getLogger(__name__)


def _clean(text: str) -> str:
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ch.isprintable())
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def merge_node(state: InvoiceState) -> dict:
    if state.get("fatal_error"):
        return {"merged_text": ""}

    parts = []
    for page in state.get("pages") or []:
        content = _clean(page.get("content") or "")
        if not content:
            continue
        page_num = int(page.get("page_num", 0)) + 1
        page_type = page.get("type") or "unknown"
        parts.append(f"--- Page {page_num} ({page_type}) ---\n{content}")

    merged = "\n\n".join(parts)
    logger.info("Merged text length: %d chars across %d page block(s)", len(merged), len(parts))
    return {"merged_text": merged}

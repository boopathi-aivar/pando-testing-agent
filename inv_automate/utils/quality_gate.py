"""Markdown / text quality gates for digital vs scanned classification."""

from __future__ import annotations

from config import MARKITDOWN_MIN_UNIQUE_RATIO, MARKITDOWN_MIN_WORDS


def is_meaningful_markdown(md: str) -> bool:
    """Return True when MarkItDown output looks like real digital text."""
    if not md or not md.strip():
        return False
    words = md.split()
    if len(words) < MARKITDOWN_MIN_WORDS:
        return False
    unique_ratio = len(set(words)) / len(words)
    return unique_ratio > MARKITDOWN_MIN_UNIQUE_RATIO

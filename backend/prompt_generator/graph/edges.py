"""Conditional edge routers."""

from __future__ import annotations

from prompt_generator.config import ACCURACY_THRESHOLD, MAX_DOCLING_RETRIES
from prompt_generator.graph.state import InvoiceState


def route_after_splitter(state: InvoiceState) -> str:
    if state.get("fatal_error"):
        return "end"
    return "classifier"


def route_after_prompt_gen(state: InvoiceState) -> str:
    if state.get("fatal_error"):
        return "end"
    return "extraction"


def route_after_validation(state: InvoiceState) -> str:
    if state.get("fatal_error"):
        return "end"
    accuracy = float(state.get("accuracy_score") or 0.0)
    retry_count = int(state.get("retry_count") or 0)
    if accuracy < ACCURACY_THRESHOLD and retry_count < MAX_DOCLING_RETRIES:
        return "feedback"
    return "prompt"


def route_after_feedback(state: InvoiceState) -> str:
    if state.get("user_approved_retry"):
        return "docling_retry"
    return "prompt"

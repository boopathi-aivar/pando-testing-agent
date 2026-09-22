"""HITL feedback node — pause for Docling retry decision."""

from __future__ import annotations

import logging

from langgraph.types import interrupt

from graph.state import InvoiceState

logger = logging.getLogger(__name__)


def feedback_node(state: InvoiceState) -> dict:
    payload = {
        "message": "Extraction accuracy is low. Retry with Docling?",
        "accuracy": state.get("accuracy_score", 0.0),
        "errors": state.get("validation_errors") or [],
        "partial_result": state.get("extracted_json") or {},
    }
    logger.info("Interrupting for HITL feedback (accuracy=%.3f)", payload["accuracy"])
    user_decision = interrupt(payload)
    retry = False
    if isinstance(user_decision, dict):
        retry = bool(user_decision.get("retry", False))
    elif isinstance(user_decision, bool):
        retry = user_decision
    return {"user_approved_retry": retry}

"""Prompt node — Q&A grounded in extracted JSON."""

from __future__ import annotations

import json
import logging

from graph.state import InvoiceState
from utils.llm import get_llm

logger = logging.getLogger(__name__)


def prompt_node(state: InvoiceState) -> dict:
    user_prompt = (state.get("user_prompt") or "").strip()
    if not user_prompt:
        return {"prompt_response": None}

    extracted = state.get("extracted_json") or {}
    accuracy = state.get("accuracy_score", 0.0)
    warning = ""
    if accuracy < 0.75:
        warning = (
            f"\nNote: extraction accuracy was low ({accuracy}). "
            "Answer cautiously and say when data may be unreliable."
        )

    llm = get_llm(json_mode=False)
    system = f"""You are an invoice assistant.
Extracted invoice data:
{json.dumps(extracted, indent=2)}
{warning}

Answer questions strictly from this data.
If the answer is not present, say so clearly.
Do not hallucinate values."""

    try:
        response = llm.invoke(
            [
                ("system", system),
                ("human", user_prompt),
            ]
        )
        content = getattr(response, "content", "") or ""
    except Exception as exc:  # noqa: BLE001
        logger.exception("Prompt node failed")
        content = f"Error calling LLM: {exc}"

    return {
        "prompt_response": content,
        "chat_history": [{"user": user_prompt, "assistant": content}],
        "user_prompt": None,
    }

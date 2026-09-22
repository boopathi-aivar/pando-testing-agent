"""LangGraph state definitions."""

from __future__ import annotations

import operator
from typing import Annotated, Optional, TypedDict


class PageResult(TypedDict, total=False):
    page_num: int
    type: Optional[str]  # "digital" | "scanned"
    content: str
    confidence: float
    img_bytes: bytes


class InvoiceState(TypedDict, total=False):
    # Inputs
    pdf_bytes: bytes
    json_schema: dict
    sample_prompt: str  # required: one existing carrier prompt (style reference)
    field_mapping_text: str  # optional: parsed field-mapping sheet
    field_mapping_row_count: int
    field_mapping_rows: list  # structured rows for schema↔mapping report

    # Page processing
    pages: list[PageResult]

    # Merged content
    merged_text: str

    # LLM-generated extraction prompt
    session_prompt: str
    prompt_history: list
    force_prompt_regen: bool

    # Quality reports (prompt gen / feedback)
    prompt_coverage: dict
    mapping_match: dict
    prompt_diff: dict

    # Extraction output
    extracted_json: dict
    accuracy_score: float
    validation_errors: list[str]
    confidence_scores: dict

    # Retry tracking
    retry_count: int
    used_docling: bool

    # HITL
    user_approved_retry: Optional[bool]

    # Prompt interface (Q&A)
    chat_history: Annotated[list, operator.add]
    user_prompt: Optional[str]
    prompt_response: Optional[str]

    # Errors
    fatal_error: Optional[str]

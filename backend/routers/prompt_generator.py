"""Prompt Generator router — agentic invoice extraction endpoints.

Mounted at /api/prompt-generator by main.py.
All logic lives in the prompt_generator sub-package.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from langgraph.types import Command
from pydantic import BaseModel, Field

from prompt_generator.config import (
    ACCURACY_THRESHOLD,
    LLM_PROVIDER,
    active_model_label,
)
from prompt_generator.graph.builder import build_app
from prompt_generator.graph.nodes.field_correct import apply_field_corrections
from prompt_generator.utils.field_mapping import EXPECTED_HEADERS, parse_field_mapping_xlsx
from prompt_generator.utils.flatten import flatten_json
from prompt_generator.utils.schema_validator import validate_user_schema

logger = logging.getLogger(__name__)

router = APIRouter(tags=["prompt-generator"])

# Lazily initialised so startup is not blocked
_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_app()
    return _graph


# ── Pydantic request models ───────────────────────────────────────────────────

class FeedbackRequest(BaseModel):
    thread_id: str
    retry: bool = False


class DoclingRetryRequest(BaseModel):
    thread_id: str


class PromptRequest(BaseModel):
    thread_id: str
    prompt: str = Field(..., min_length=1)


class FieldCorrection(BaseModel):
    path: str
    current_value: Any = None
    note: str = ""


class CorrectRequest(BaseModel):
    thread_id: str
    corrections: list[FieldCorrection] = Field(default_factory=list)
    generic_note: str = ""
    refine_prompt: bool = True


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pages_classified(state: dict) -> list[dict]:
    return [
        {
            "page": int(p.get("page_num", 0)) + 1,
            "type": p.get("type"),
            "confidence": p.get("confidence"),
        }
        for p in (state.get("pages") or [])
    ]


def _public_state(thread_id: str, state: dict, status: str) -> dict[str, Any]:
    extracted = state.get("extracted_json") or {}
    return {
        "thread_id": thread_id,
        "status": status,
        "extracted_json": extracted,
        "fields": flatten_json(extracted),
        "session_prompt": state.get("session_prompt") or "",
        "sample_prompt_provided": bool((state.get("sample_prompt") or "").strip()),
        "field_mapping_used": bool((state.get("field_mapping_text") or "").strip()),
        "field_mapping_row_count": int(state.get("field_mapping_row_count") or 0),
        "prompt_coverage": state.get("prompt_coverage") or None,
        "mapping_match": state.get("mapping_match") or None,
        "prompt_diff": state.get("prompt_diff") or None,
        "accuracy_score": state.get("accuracy_score", 0.0),
        "validation_errors": state.get("validation_errors") or [],
        "confidence_scores": state.get("confidence_scores") or {},
        "used_docling": bool(state.get("used_docling")),
        "pages_classified": _pages_classified(state),
        "fatal_error": state.get("fatal_error"),
        "accuracy_threshold": ACCURACY_THRESHOLD,
        "provider": LLM_PROVIDER,
        "model": active_model_label(),
        "chat_history": state.get("chat_history") or [],
        "prompt_response": state.get("prompt_response"),
    }


def _interrupt_payload(result: Any) -> Optional[dict]:
    if isinstance(result, dict) and "__interrupt__" in result:
        interrupts = result["__interrupt__"]
        if interrupts:
            first = interrupts[0]
            value = getattr(first, "value", first)
            return value if isinstance(value, dict) else {"message": str(value)}
    return None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/health")
def pg_health():
    """Health / model check for the prompt generator."""
    return {"ok": True, "provider": LLM_PROVIDER, "model": active_model_label()}


@router.post("/extract")
async def extract_invoice(
    files: List[UploadFile] = File(...),
    schema_json: str = Form(..., alias="schema"),
    sample_prompt: str = Form(...),
    field_mapping: Optional[UploadFile] = File(None),
    thread_id: Optional[str] = Form(None),
):
    if not files:
        raise HTTPException(status_code=400, detail="Upload at least one PDF file.")
    if len(files) > 3:
        raise HTTPException(status_code=400, detail="Maximum 3 PDF files allowed per extraction.")

    for f in files:
        if not f.filename or not f.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"'{f.filename}' is not a PDF file.")

    sample = (sample_prompt or "").strip()
    if not sample:
        raise HTTPException(
            status_code=400,
            detail="sample_prompt is required — paste one existing carrier extraction prompt.",
        )

    try:
        schema_obj = json.loads(schema_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid schema JSON: {exc}") from exc

    ok, err = validate_user_schema(schema_obj)
    if not ok:
        raise HTTPException(status_code=400, detail=err)

    # Merge multiple PDFs into one using PyMuPDF
    import fitz  # PyMuPDF

    merged_doc = fitz.open()
    for upload in files:
        raw = await upload.read()
        if not raw:
            raise HTTPException(status_code=400, detail=f"'{upload.filename}' is empty.")
        try:
            src = fitz.open(stream=raw, filetype="pdf")
            merged_doc.insert_pdf(src)
            src.close()
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Could not read '{upload.filename}': {exc}",
            ) from exc

    pdf_bytes = merged_doc.tobytes()
    merged_doc.close()

    mapping_text = ""
    mapping_row_count = 0
    mapping_rows: list = []
    if field_mapping is not None and field_mapping.filename:
        if not field_mapping.filename.lower().endswith(".xlsx"):
            raise HTTPException(status_code=400, detail="Field mapping must be a .xlsx file.")
        mapping_bytes = await field_mapping.read()
        if mapping_bytes:
            try:
                mapping_text, mapping_row_count, mapping_rows = parse_field_mapping_xlsx(mapping_bytes)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    tid = thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": tid}}

    initial: dict[str, Any] = {
        "pdf_bytes": pdf_bytes,
        "json_schema": schema_obj,
        "sample_prompt": sample,
        "field_mapping_text": mapping_text,
        "field_mapping_row_count": mapping_row_count,
        "field_mapping_rows": mapping_rows,
        "chat_history": [],
        "user_prompt": None,
        "prompt_response": None,
        "session_prompt": "",
        "prompt_history": [],
        "force_prompt_regen": True,
        "prompt_coverage": None,
        "mapping_match": None,
        "prompt_diff": None,
    }

    try:
        result = _get_graph().invoke(initial, config=config)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Extract failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    interrupt = _interrupt_payload(result)
    if interrupt:
        snap = _get_graph().get_state(config)
        values = snap.values if snap else {}
        body = _public_state(tid, values, "awaiting_feedback")
        body["feedback"] = interrupt
        return body

    status = "failed" if result.get("fatal_error") else "completed"
    return _public_state(tid, result, status)


@router.get("/field-mapping/headers")
def field_mapping_headers():
    return {
        "required_headers": list(EXPECTED_HEADERS),
        "rules": [
            "Exactly one sheet in the workbook.",
            "Headers must match exactly (first data header row).",
            "JSON schema defines output fields; mapping is optional location/remarks hints.",
            "Rows need Column Name plus Location and/or Remarks.",
            "Empty Remarks are allowed when Location is present.",
            "Table Name is used to group fields.",
        ],
    }


@router.post("/feedback")
def submit_feedback(body: FeedbackRequest):
    graph = _get_graph()
    config = {"configurable": {"thread_id": body.thread_id}}
    snap = graph.get_state(config)
    if not snap or not snap.values:
        raise HTTPException(status_code=404, detail="Unknown thread_id or expired session.")

    try:
        result = graph.invoke(Command(resume={"retry": body.retry}), config=config)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Feedback resume failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    interrupt = _interrupt_payload(result)
    if interrupt:
        values = graph.get_state(config).values
        out = _public_state(body.thread_id, values, "awaiting_feedback")
        out["feedback"] = interrupt
        return out

    status = "failed" if result.get("fatal_error") else "completed"
    return _public_state(body.thread_id, result, status)


@router.post("/docling-retry")
def voluntary_docling_retry(body: DoclingRetryRequest):
    graph = _get_graph()
    config = {"configurable": {"thread_id": body.thread_id}}
    snap = graph.get_state(config)
    if not snap or not snap.values:
        raise HTTPException(status_code=404, detail="Unknown thread_id or expired session.")

    values = dict(snap.values)
    if not values.get("pdf_bytes"):
        raise HTTPException(status_code=400, detail="No PDF in session.")

    interrupts = getattr(snap, "interrupts", None) or ()
    if interrupts:
        try:
            result = graph.invoke(Command(resume={"retry": True}), config=config)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        interrupt = _interrupt_payload(result)
        if interrupt:
            values = graph.get_state(config).values
            out = _public_state(body.thread_id, values, "awaiting_feedback")
            out["feedback"] = interrupt
            return out
        status = "failed" if result.get("fatal_error") else "completed"
        return _public_state(body.thread_id, result, status)

    from prompt_generator.graph.nodes.docling_retry import docling_retry_node
    from prompt_generator.graph.nodes.extraction import extraction_node
    from prompt_generator.graph.nodes.validation import validation_node

    try:
        docling_out = docling_retry_node(values)
        working = {**values, **docling_out}
        updates: dict[str, Any] = dict(docling_out)

        if not (working.get("merged_text") or "").strip():
            graph.update_state(config, updates)
            final = graph.get_state(config).values
            return _public_state(body.thread_id, final, "failed")

        working["force_prompt_regen"] = False
        extract_out = extraction_node(working)
        updates.update(extract_out)
        working = {**working, **extract_out}

        val_out = validation_node(working)
        updates.update(val_out)
        graph.update_state(config, updates)
        final = graph.get_state(config).values
    except Exception as exc:  # noqa: BLE001
        logger.exception("Voluntary Docling retry failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    status = "failed" if final.get("fatal_error") else "completed"
    return _public_state(body.thread_id, final, status)


@router.post("/correct")
def correct_fields(body: CorrectRequest):
    graph = _get_graph()
    config = {"configurable": {"thread_id": body.thread_id}}
    snap = graph.get_state(config)
    if not snap or not snap.values:
        raise HTTPException(status_code=404, detail="Unknown thread_id or expired session.")

    values = dict(snap.values)
    if not values.get("merged_text"):
        raise HTTPException(status_code=400, detail="No invoice text in session.")

    corrections = [c.model_dump() for c in body.corrections]
    note = (body.generic_note or "").strip()
    if not corrections and not note:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one field correction or a generic_note.",
        )

    try:
        updates = apply_field_corrections(
            values, corrections, generic_note=note, refine_prompt=body.refine_prompt
        )
        graph.update_state(config, updates)
        final = graph.get_state(config).values
    except Exception as exc:  # noqa: BLE001
        logger.exception("Field correction failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    status = "failed" if final.get("fatal_error") else "completed"
    return _public_state(body.thread_id, final, status)


@router.post("/ask")
def ask_prompt(body: PromptRequest):
    graph = _get_graph()
    config = {"configurable": {"thread_id": body.thread_id}}
    snap = graph.get_state(config)
    if not snap or not snap.values:
        raise HTTPException(status_code=404, detail="Unknown thread_id. Run extract first.")

    values = dict(snap.values)
    if not values.get("extracted_json") and not values.get("fatal_error"):
        raise HTTPException(status_code=400, detail="No extraction result yet.")

    try:
        from prompt_generator.graph.nodes.prompt import prompt_node
        updated = prompt_node({**values, "user_prompt": body.prompt})
        graph.update_state(
            config,
            {
                "prompt_response": updated.get("prompt_response"),
                "chat_history": updated.get("chat_history") or [],
                "user_prompt": None,
            },
        )
        final = graph.get_state(config).values
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ask prompt failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "thread_id": body.thread_id,
        "response": final.get("prompt_response"),
        "chat_history": final.get("chat_history") or [],
    }

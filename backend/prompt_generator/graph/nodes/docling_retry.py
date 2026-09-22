"""Docling retry — heavy fallback on full PDF (lazy import)."""

from __future__ import annotations

import logging
import os
import tempfile

from prompt_generator.graph.state import InvoiceState

logger = logging.getLogger(__name__)


def docling_retry_node(state: InvoiceState) -> dict:
    pdf_bytes = state.get("pdf_bytes") or b""
    if not pdf_bytes:
        return {
            "validation_errors": (state.get("validation_errors") or [])
            + ["Docling retry skipped: no PDF bytes."],
            "used_docling": False,
        }

    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        logger.error("docling is not installed")
        return {
            "validation_errors": (state.get("validation_errors") or [])
            + ["Docling is not installed. Run: pip install docling"],
            "used_docling": False,
            "retry_count": int(state.get("retry_count") or 0) + 1,
        }

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name
        converter = DocumentConverter()
        result = converter.convert(tmp_path)
        md = result.document.export_to_markdown()
        logger.info("Docling produced %d chars of markdown", len(md or ""))
        return {
            "merged_text": md or "",
            "used_docling": True,
            "retry_count": int(state.get("retry_count") or 0) + 1,
            "user_approved_retry": None,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Docling conversion failed")
        return {
            "validation_errors": (state.get("validation_errors") or [])
            + [f"Docling failed: {exc}"],
            "used_docling": False,
            "retry_count": int(state.get("retry_count") or 0) + 1,
        }
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

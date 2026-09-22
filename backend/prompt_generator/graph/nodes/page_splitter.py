"""Page splitter — rasterize PDF pages and detect corrupt/password-protected files."""

from __future__ import annotations

import logging

import fitz

from prompt_generator.config import MAX_PAGES_V1, PDF_DPI
from prompt_generator.graph.state import InvoiceState

logger = logging.getLogger(__name__)


def page_splitter_node(state: InvoiceState) -> dict:
    pdf_bytes = state.get("pdf_bytes") or b""
    if not pdf_bytes:
        return {"fatal_error": "No PDF bytes provided.", "pages": []}

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to open PDF")
        return {"fatal_error": f"Corrupt or unreadable PDF: {exc}", "pages": []}

    if doc.is_encrypted:
        if not doc.authenticate(""):
            doc.close()
            return {
                "fatal_error": "PDF is password-protected. Remove the password and retry.",
                "pages": [],
            }

    page_count = len(doc)
    if page_count == 0:
        doc.close()
        return {"fatal_error": "PDF has no pages.", "pages": []}

    if page_count > MAX_PAGES_V1:
        doc.close()
        return {
            "fatal_error": (
                f"PDF has {page_count} pages; v1 supports up to {MAX_PAGES_V1}. "
                "Split the document or raise MAX_PAGES_V1."
            ),
            "pages": [],
        }

    pages = []
    for i in range(page_count):
        pix = doc[i].get_pixmap(dpi=PDF_DPI)
        pages.append(
            {
                "page_num": i,
                "img_bytes": pix.tobytes("png"),
                "type": None,
                "content": "",
                "confidence": 0.0,
            }
        )
    doc.close()

    logger.info("Split PDF into %d page(s) at %d DPI", len(pages), PDF_DPI)
    return {
        "pages": pages,
        "retry_count": 0,
        "used_docling": False,
        "fatal_error": None,
        "extracted_json": {},
        "validation_errors": [],
        "confidence_scores": {},
        "accuracy_score": 0.0,
        "merged_text": "",
        "user_approved_retry": None,
        "session_prompt": "",
        "prompt_history": [],
        "force_prompt_regen": False,
    }

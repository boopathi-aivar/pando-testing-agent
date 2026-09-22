"""Per-page classifier: MarkItDown (digital) with RapidOCR fallback (scanned)."""

from __future__ import annotations

import logging
import tempfile
from functools import lru_cache
from typing import Optional

import fitz

from prompt_generator.graph.state import InvoiceState, PageResult
from prompt_generator.utils.quality_gate import is_meaningful_markdown

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _markitdown():
    from markitdown import MarkItDown
    return MarkItDown()


@lru_cache(maxsize=1)
def _rapid_ocr():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def _single_page_pdf_bytes(full_pdf: bytes, page_num: int) -> bytes:
    src = fitz.open(stream=full_pdf, filetype="pdf")
    dst = fitz.open()
    dst.insert_pdf(src, from_page=page_num, to_page=page_num)
    out = dst.tobytes()
    dst.close()
    src.close()
    return out


def _try_markitdown_page(pdf_bytes: bytes, page_num: int) -> Optional[str]:
    try:
        page_pdf = _single_page_pdf_bytes(pdf_bytes, page_num)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
            tmp.write(page_pdf)
            tmp.flush()
            result = _markitdown().convert(tmp.name)
        md = (getattr(result, "text_content", None) or "").strip()
        return md or None
    except Exception as exc:  # noqa: BLE001
        logger.debug("MarkItDown failed on page %d: %s", page_num + 1, exc)
        return None


def _try_rapidocr(img_bytes: bytes) -> tuple[str, float]:
    try:
        ocr = _rapid_ocr()
        result, _ = ocr(img_bytes)
        if not result:
            return "", 0.3
        lines = []
        confs = []
        for item in result:
            if len(item) >= 3:
                lines.append(str(item[1]))
                try:
                    confs.append(float(item[2]))
                except (TypeError, ValueError):
                    pass
        text = "\n".join(lines).strip()
        avg = sum(confs) / len(confs) if confs else 0.5
        if not text:
            return "", 0.3
        return text, max(0.3, min(0.95, avg))
    except Exception as exc:  # noqa: BLE001
        logger.warning("RapidOCR failed: %s", exc)
        return "", 0.3


def per_page_classifier_node(state: InvoiceState) -> dict:
    if state.get("fatal_error"):
        return {}

    pdf_bytes = state.get("pdf_bytes") or b""
    pages_in = state.get("pages") or []
    pages_out: list[PageResult] = []

    for page in pages_in:
        page_num = int(page.get("page_num", 0))
        img_bytes = page.get("img_bytes") or b""

        md = _try_markitdown_page(pdf_bytes, page_num) if pdf_bytes else None
        if md and is_meaningful_markdown(md):
            pages_out.append(
                {
                    "page_num": page_num,
                    "type": "digital",
                    "content": md,
                    "confidence": 0.90,
                    "img_bytes": img_bytes,
                }
            )
            continue

        text, conf = _try_rapidocr(img_bytes) if img_bytes else ("", 0.3)
        pages_out.append(
            {
                "page_num": page_num,
                "type": "scanned",
                "content": text,
                "confidence": conf if text else 0.30,
                "img_bytes": img_bytes,
            }
        )

    digital = sum(1 for p in pages_out if p.get("type") == "digital")
    scanned = len(pages_out) - digital
    logger.info("Classified pages: %d digital, %d scanned", digital, scanned)
    return {"pages": pages_out}

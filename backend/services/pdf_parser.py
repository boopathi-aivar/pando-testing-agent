"""
PDF → text + coordinates.

Digital PDFs: native PyMuPDF glyphs (exact) with PDF-space bboxes.
Scanned PDFs: RapidOCR (default) or PaddleOCR on the page image, with boxes
mapped back into PDF coordinates.

RapidOCR is an ONNX runtime of PaddleOCR's PP-OCR models. It already returns
the same 4-point boxes as PaddleOCR; we keep them instead of discarding them.

PDF_OCR_ENGINE = rapidocr | paddleocr | off
PDF_OCR_MODE   = auto (native when a text layer exists) | always (OCR every page)
"""

from __future__ import annotations

import io
import os
import re
from typing import Any

try:
    import fitz  # PyMuPDF
    _HAS_FITZ = True
except ImportError:
    _HAS_FITZ = False

_MIN_NATIVE_CHARS = 80
_OCR_MAX_SIDE = 2000
_MIN_OCR_SCORE = 0.45

_engines: dict[str, Any] = {}
_engine_failed: set[str] = set()


def pdf_to_markdown(pdf_bytes: bytes) -> str:
    """Markdown only — used by the scoring prompt."""
    return parse_pdf(pdf_bytes).get("markdown") or ""


def parse_pdf(pdf_bytes: bytes) -> dict:
    """
    Convert a PDF to markdown plus per-line coordinates.

    Returns:
      {
        "markdown": str,
        "pages": [
          {
            "page": int,
            "width": float,
            "height": float,
            "source": "native" | "rapidocr" | "paddleocr",
            "items": [
              {
                "text": str,
                "score": float,
                "bbox": [x0, y0, x1, y1],   # PDF points
                "poly": [[x, y], ...],      # 4-point quad, PDF points
              }
            ]
          }
        ]
      }
    """
    empty = {"markdown": "", "pages": []}
    if not _HAS_FITZ or not pdf_bytes:
        return empty

    try:
        doc = fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
    except Exception as e:
        print(f"[PDFParser] Open failed: {type(e).__name__}: {e}")
        return empty

    engine = _ocr_engine_name()
    mode = os.getenv("PDF_OCR_MODE", "auto").strip().lower()
    pages_out: list[dict] = []
    md_pages: list[str] = []

    try:
        for page_num, page in enumerate(doc, start=1):
            native_items = _native_items(page)
            native_text = "\n".join(i["text"] for i in native_items)
            use_ocr = engine != "off" and (
                mode == "always" or _needs_ocr(native_text)
            )

            items = native_items
            source = "native"
            if use_ocr:
                ocr_items, ocr_source = _ocr_page_items(page, engine)
                if ocr_items:
                    items = ocr_items
                    source = ocr_source
                    print(
                        f"[PDFParser] Page {page_num}: native={len(native_text)} chars, "
                        f"{ocr_source}={sum(len(i['text']) for i in ocr_items)} chars, "
                        f"{len(ocr_items)} boxes"
                    )

            page_rec = {
                "page": page_num,
                "width": round(float(page.rect.width), 2),
                "height": round(float(page.rect.height), 2),
                "source": source,
                "items": items,
            }
            pages_out.append(page_rec)
            text = "\n".join(i["text"] for i in items).strip()
            if text:
                md_pages.append(f"### Page {page_num} ({source})\n\n{text}")
    finally:
        doc.close()

    markdown = "\n\n---\n\n".join(md_pages)
    if not markdown.strip():
        print("[PDFParser] No text from native layer or OCR")
    return {"markdown": markdown, "pages": pages_out}


def _ocr_engine_name() -> str:
    raw = os.getenv("PDF_OCR_ENGINE", "rapidocr").strip().lower()
    if raw in ("off", "none", "false", "0", "native"):
        return "off"
    if raw in ("paddle", "paddleocr", "ppocr"):
        return "paddleocr"
    return "rapidocr"


def _needs_ocr(native: str) -> bool:
    compact = re.sub(r"\s+", "", native or "")
    return len(compact) < _MIN_NATIVE_CHARS


def _native_items(page) -> list[dict]:
    """Line-level text + bbox from the PDF text layer (digital invoices)."""
    try:
        data = page.get_text("dict")
    except Exception:
        text = _page_native_text(page)
        return [{"text": text, "score": 1.0, "bbox": [0, 0, 0, 0], "poly": []}] if text else []

    items: list[dict] = []
    for block in data.get("blocks") or []:
        if block.get("type") not in (0, None) and "lines" not in block:
            continue
        for line in block.get("lines") or []:
            spans = [s for s in (line.get("spans") or []) if (s.get("text") or "").strip()]
            if not spans:
                continue
            text = " ".join(s["text"].strip() for s in spans).strip()
            if not text:
                continue
            bbox = _union_bbox([s.get("bbox") for s in spans])
            items.append({
                "text": text,
                "score": 1.0,
                "bbox": bbox,
                "poly": _bbox_to_poly(bbox),
            })
    if items:
        return items
    text = _page_native_text(page)
    return [{"text": text, "score": 1.0, "bbox": [0, 0, 0, 0], "poly": []}] if text else []


def _page_native_text(page) -> str:
    try:
        text = page.get_text("markdown")
    except (TypeError, AttributeError, AssertionError):
        text = page.get_text("text")
    return (text or "").strip()


def _union_bbox(boxes) -> list[float]:
    xs0, ys0, xs1, ys1 = [], [], [], []
    for b in boxes:
        if not b or len(b) < 4:
            continue
        xs0.append(float(b[0])); ys0.append(float(b[1]))
        xs1.append(float(b[2])); ys1.append(float(b[3]))
    if not xs0:
        return [0.0, 0.0, 0.0, 0.0]
    return [round(min(xs0), 2), round(min(ys0), 2), round(max(xs1), 2), round(max(ys1), 2)]


def _bbox_to_poly(bbox: list[float]) -> list[list[float]]:
    x0, y0, x1, y1 = bbox
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _poly_to_bbox(poly) -> list[float]:
    xs = [float(p[0]) for p in poly]
    ys = [float(p[1]) for p in poly]
    return [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)]


def _ocr_page_items(page, engine_name: str) -> tuple[list[dict], str]:
    img = _page_to_rgb(page)
    if img is None:
        return [], engine_name
    max_side = max(page.rect.width, page.rect.height) or 1.0
    scale = _OCR_MAX_SIDE / max_side

    raw = _run_ocr(img, engine_name)
    used = engine_name
    if not raw and engine_name == "paddleocr":
        print("[PDFParser] PaddleOCR returned nothing — falling back to RapidOCR")
        raw = _run_ocr(img, "rapidocr")
        used = "rapidocr"

    items: list[dict] = []
    for it in raw:
        poly_px = it.get("poly") or []
        if len(poly_px) < 4:
            continue
        poly_pdf = [
            [round(float(p[0]) / scale, 2), round(float(p[1]) / scale, 2)]
            for p in poly_px
        ]
        items.append({
            "text": it["text"],
            "score": round(float(it.get("score") or 0), 4),
            "bbox": _poly_to_bbox(poly_pdf),
            "poly": poly_pdf,
        })
    items.sort(key=lambda r: (r["bbox"][1], r["bbox"][0]))
    return items, used


def _run_ocr(img, engine_name: str) -> list[dict]:
    if engine_name == "paddleocr":
        items = _paddle_ocr(img)
        if items:
            return items
        return []
    return _rapid_ocr(img)


def _get_engine(name: str):
    if name in _engines:
        return _engines[name]
    if name in _engine_failed:
        return None
    try:
        if name == "rapidocr":
            from rapidocr_onnxruntime import RapidOCR
            _engines[name] = RapidOCR()
            print("[PDFParser] RapidOCR engine ready")
        elif name == "paddleocr":
            from paddleocr import PaddleOCR
            try:
                _engines[name] = PaddleOCR(
                    lang="en",
                    use_angle_cls=True,
                    show_log=False,
                    use_gpu=False,
                )
            except TypeError:
                _engines[name] = PaddleOCR(lang="en")
            print("[PDFParser] PaddleOCR engine ready")
        else:
            return None
        return _engines[name]
    except Exception as e:
        _engine_failed.add(name)
        print(f"[PDFParser] {name} unavailable ({type(e).__name__}: {e})")
        return None


def _rapid_ocr(img) -> list[dict]:
    engine = _get_engine("rapidocr")
    if engine is None:
        return []
    try:
        result, _elapsed = engine(img)
    except Exception as e:
        print(f"[PDFParser] RapidOCR failed: {e}")
        return []
    return _parse_rapid_result(result)


def _parse_rapid_result(result: Any) -> list[dict]:
    """RapidOCR 1.x: [[box, text, score], ...]. Same geometry as PaddleOCR."""
    if not result:
        return []
    items = []
    for item in result:
        if not item or len(item) < 2:
            continue
        box, text = item[0], item[1]
        score = float(item[2]) if len(item) > 2 else 1.0
        if not text or score < _MIN_OCR_SCORE:
            continue
        poly = [[float(p[0]), float(p[1])] for p in box]
        items.append({"text": str(text).strip(), "score": score, "poly": poly})
    return items


def _paddle_ocr(img) -> list[dict]:
    engine = _get_engine("paddleocr")
    if engine is None:
        return []
    try:
        if hasattr(engine, "ocr"):
            result = engine.ocr(img, cls=True)
        elif hasattr(engine, "predict"):
            result = engine.predict(img)
        else:
            print("[PDFParser] PaddleOCR has no ocr/predict method")
            return []
    except Exception as e:
        print(f"[PDFParser] PaddleOCR failed: {e}")
        return []
    return _parse_paddle_result(result)


def _parse_paddle_result(result: Any) -> list[dict]:
    """
    PaddleOCR 2.x: [ [ [box, (text, score)], ... ] ]
    Some builds return the inner list directly.
    """
    if not result:
        return []
    page = result[0] if (isinstance(result, list) and result and isinstance(result[0], list)) else result
    if not page:
        return []
    items = []
    for line in page:
        if not line or len(line) < 2:
            continue
        box, rec = line[0], line[1]
        if isinstance(rec, (list, tuple)):
            text, score = rec[0], float(rec[1] if len(rec) > 1 else 1.0)
        else:
            text, score = str(rec), 1.0
        if not text or score < _MIN_OCR_SCORE:
            continue
        poly = [[float(p[0]), float(p[1])] for p in box]
        items.append({"text": str(text).strip(), "score": score, "poly": poly})
    return items


def _pix_to_rgb(pix):
    import numpy as np

    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 1:
        return np.stack([arr[:, :, 0]] * 3, axis=-1)
    if pix.n == 4:
        return arr[:, :, :3]
    return arr


def _page_to_rgb(page):
    max_side = max(page.rect.width, page.rect.height) or 1.0
    scale = _OCR_MAX_SIDE / max_side
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return _pix_to_rgb(pix)

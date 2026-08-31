"""
PDF → Markdown converter for invoice PDFs.

Uses PyMuPDF (fitz). Tries native markdown output (PyMuPDF >= 1.24),
falls back to plain text with basic structure if not available.
"""

import io

try:
    import fitz  # PyMuPDF
    _HAS_FITZ = True
except ImportError:
    _HAS_FITZ = False


def pdf_to_markdown(pdf_bytes: bytes) -> str:
    """
    Convert a PDF to a markdown string.
    Returns an empty string if PyMuPDF is not installed or conversion fails.
    """
    if not _HAS_FITZ:
        return ""

    try:
        doc = fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
        pages: list[str] = []

        for page_num, page in enumerate(doc, start=1):
            try:
                # Native markdown output (PyMuPDF >= 1.24)
                text = page.get_text("markdown")
            except (TypeError, AttributeError):
                # Older PyMuPDF — plain text fallback
                text = page.get_text("text")

            if text.strip():
                pages.append(f"### Page {page_num}\n\n{text.strip()}")

        return "\n\n---\n\n".join(pages)

    except Exception as e:
        print(f"[PDFParser] Conversion failed: {e}")
        return ""

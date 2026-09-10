"""PDF → markdown: native text plus OCR fallback for scans."""

import os

from services.pdf_parser import (
    pdf_to_markdown,
    parse_pdf,
    _needs_ocr,
    _parse_rapid_result,
    _parse_paddle_result,
    _poly_to_bbox,
)


def _digital_pdf_bytes() -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Invoice 11448")
    page.insert_text((72, 96), "Total: 4401.62")
    data = doc.tobytes()
    doc.close()
    return data


def test_native_digital_pdf_skips_ocr():
    os.environ["PDF_OCR_ENGINE"] = "off"
    md = pdf_to_markdown(_digital_pdf_bytes())
    os.environ.pop("PDF_OCR_ENGINE", None)
    assert "11448" in md
    assert "4401.62" in md
    assert "(native)" in md


def test_native_digital_pdf_has_coordinates():
    os.environ["PDF_OCR_ENGINE"] = "off"
    parsed = parse_pdf(_digital_pdf_bytes())
    os.environ.pop("PDF_OCR_ENGINE", None)
    assert parsed["pages"]
    items = parsed["pages"][0]["items"]
    assert parsed["pages"][0]["source"] == "native"
    hit = next(i for i in items if "11448" in i["text"])
    x0, y0, x1, y1 = hit["bbox"]
    assert x1 > x0 and y1 > y0
    assert 50 < x0 < 200
    assert 50 < y0 < 120
    assert len(hit["poly"]) == 4


def test_empty_bytes_returns_empty():
    assert pdf_to_markdown(b"") == ""
    assert pdf_to_markdown(b"not-a-pdf") == ""


def test_sparse_text_needs_ocr():
    assert _needs_ocr("") is True
    assert _needs_ocr("   \n") is True
    assert _needs_ocr("abc") is True
    assert _needs_ocr("Invoice number 11448 dated 12-Aug-2026 Madison Logistics GL GROUP COMPANY IMPORT EXPORT CORPORATE WAREHOUSE SERVICES total 4401.62") is False


def test_rapidocr_result_includes_boxes():
    result = [
        [[[10.0, 20.0], [80.0, 20.0], [80.0, 40.0], [10.0, 40.0]], "Invoice #: 11448", 0.98],
        [[[10.0, 50.0], [90.0, 50.0], [90.0, 70.0], [10.0, 70.0]], "Total 4401.62", 0.91],
    ]
    items = _parse_rapid_result(result)
    assert [i["text"] for i in items] == ["Invoice #: 11448", "Total 4401.62"]
    assert items[0]["poly"][0] == [10.0, 20.0]
    assert _poly_to_bbox(items[0]["poly"]) == [10.0, 20.0, 80.0, 40.0]


def test_paddleocr_result_includes_boxes():
    result = [[
        [[[10, 20], [80, 20], [80, 40], [10, 40]], ("Invoice #: 11448", 0.97)],
        [[[10, 50], [90, 50], [90, 70], [10, 70]], ("Total 4401.62", 0.88)],
    ]]
    items = _parse_paddle_result(result)
    assert items[0]["text"] == "Invoice #: 11448"
    assert items[0]["poly"][2] == [80.0, 40.0]
    assert items[1]["score"] == 0.88

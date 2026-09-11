from services.markdown_expected import (
    merge_expected,
    parse_expected_from_markdown,
    usable_expected,
)

SAMPLE_11448 = """
### Page 1 (rapidocr)

Freight Invoice
MADISONLOGISTICSINC Invoice #: 11448
Invoice Date: 08/12/2026
SCAC: MOLP
BOL#:202654820190
Origin:LAREDO,TX Destination:DECATUR,AL
Total $4,401.62
GLGROUPCOMPANY-IMPORT&EXPORT
CORPORATEWAREHOUSESERVICES
"""


def test_parse_11448_markdown_without_llm():
    expected = parse_expected_from_markdown(SAMPLE_11448)
    assert expected["invoice_number"] == "11448"
    assert expected["invoice_date"] == "08/12/2026"
    assert expected["vendor_reference_id"] == "MOLP"
    assert expected["bill_of_lading_number"] == "202654820190"
    assert expected["total_invoice_value"] == "4401.62"
    assert expected["currency"] == "USD"
    assert usable_expected(expected)


def test_empty_llm_falls_back_to_parsed():
    parsed = parse_expected_from_markdown(SAMPLE_11448)
    merged = merge_expected(parsed, {})
    assert merged["invoice_number"] == "11448"


def test_llm_fills_over_parse_when_present():
    parsed = {"invoice_number": "11448"}
    llm = {"invoice_number": "99", "currency": "USD"}
    merged = merge_expected(parsed, llm)
    assert merged["invoice_number"] == "99"
    assert merged["currency"] == "USD"


def test_unusable_expected():
    assert usable_expected({}) is False
    assert usable_expected({"invoice_number": None}) is False
    assert usable_expected({"invoice_number": "11448"}) is True

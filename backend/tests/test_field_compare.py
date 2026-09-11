"""Deterministic comparison used after LLM scoring."""

from services.field_compare import (
    apply_comparison,
    classify_field,
    values_match,
)


def test_dates_match_across_formats():
    expected = "2024-08-12"
    for actual in (
        "12-Aug-2024",
        "12 Aug 2024",
        "12/08/2024",
        "2024/08/12",
        "Aug 12, 2024",
        "12-Aug-24",
    ):
        assert values_match("invoice_date", expected, actual)
        assert classify_field("invoice_date", expected, actual) == "correct"


def test_ambiguous_slash_date_matches_month_name():
    # 08/12/2026 is Aug 12 (US) or 8 Dec (EU); 12-Aug-2026 is unambiguous Aug 12.
    assert classify_field("invoice_date", "08/12/2026", "12-Aug-2026") == "correct"
    assert classify_field("invoice_date", "07/28/2026", "28-Jul-2026") == "correct"
    assert classify_field("invoice_date", "08/12/2026", "09-Aug-2026") == "wrong"


def test_vendor_name_ignores_legal_suffix_and_case():
    assert classify_field(
        "vendor_name", "MADISON LOGISTICS INC", "Madison Logistics",
    ) == "correct"
    assert classify_field(
        "vendor_name", "M & M Cartage Co., Inc.", "M & M CARTAGE COMPANY INC",
    ) == "correct"
    assert classify_field("vendor_name", "Madison Logistics", "APS") == "wrong"


def test_vendor_short_brand_matches_legal_name():
    assert classify_field("vendor_name", "AVERITT EXPRESS INC.", "Averitt") == "correct"
    assert classify_field("vendor_name", "AVERITT EXPRESS INC.", "Express") == "wrong"
    assert classify_field("destination_name", "GE APPLIANCE", "AP5") == "wrong"
    assert classify_field("vendor_name", "GE APPLIANCE", "GE") == "wrong"


def test_country_code_matches_english_name():
    assert classify_field("origin_country", "US", "United States") == "correct"
    assert classify_field("destination_country", "USA", "US") == "correct"
    assert classify_field("origin_country", "US", "Mexico") == "wrong"


def test_address_prefix_is_correct():
    assert classify_field(
        "source_address",
        "55901 CURRANT ROAD, MISHAWAKA, IN 46545",
        "55901 CURRANT ROAD",
    ) == "correct"
    assert classify_field("destination_state", "TN", "1670 TENNESSEE SELMER TN") == "wrong"


def test_charge_name_ignores_qualifier_words():
    assert classify_field(
        "charge_name (charge 2)", "Fuel Surcharge Miles", "Fuel Surcharge",
    ) == "correct"
    assert classify_field("charge_name", "Fuel Surcharge Fee", "Fuel Surcharge") == "correct"
    assert classify_field("charge_name (charge 1)", "APPLIANCE PARTS", "Base Freight") == "wrong"
    assert classify_field("charge_name", "Flat Rate", "Base Freight") == "wrong"


def test_amounts_ignore_commas_and_currency():
    assert classify_field("total_invoice_value", "2,435.00", "2435") == "correct"
    assert classify_field("total_invoice_value", "$1,200.50", "1200.50") == "correct"
    assert classify_field("total_invoice_value", "100", "99") == "wrong"


def test_no_ground_truth_is_unverified_not_correct():
    assert classify_field(
        "invoice_number",
        "N/A (no PDF ground truth provided)",
        "INV-11448",
    ) == "unverified"
    assert classify_field("invoice_date", None, "12-Aug-2024") == "unverified"
    assert classify_field("source_address", None, None) == "missing"


def test_apply_comparison_rewrites_false_correct():
    result = {
        "status": "failed",
        "overall_score": 63,
        "field_validations": [
            {
                "field_name": "invoice_date",
                "expected_value": "N/A (no PDF ground truth provided)",
                "actual_value": "12-Aug-2024",
                "status": "correct",
                "source_used": "Invoice PDF",
            },
            {
                "field_name": "vendor_reference_id",
                "expected_value": "MOLP",
                "actual_value": "MOLP",
                "status": "correct",
            },
            {
                "field_name": "source_address",
                "expected_value": "N/A (no PDF ground truth provided)",
                "actual_value": None,
                "status": "missing",
            },
        ],
    }
    apply_comparison(result)
    statuses = {v["field_name"]: v["status"] for v in result["field_validations"]}
    assert statuses["invoice_date"] == "unverified"
    assert statuses["vendor_reference_id"] == "correct"
    assert statuses["source_address"] == "missing"
    assert result["field_validations"][0]["expected_value"] is None


def test_expected_from_pdf_fills_unverified_rows():
    result = {
        "status": "failed",
        "overall_score": 0,
        "expected_from_pdf": {
            "invoice_number": "11498",
            "invoice_date": "09/10/2026",
            "total_invoice_value": "4401.62",
        },
        "field_validations": [
            {
                "field_name": "invoice_number",
                "expected_value": None,
                "actual_value": "11498",
                "status": "unverified",
                "is_mandatory": True,
            },
            {
                "field_name": "invoice_date",
                "expected_value": None,
                "actual_value": "10-Sep-2026",
                "status": "unverified",
                "is_mandatory": True,
            },
        ],
    }
    apply_comparison(result, mandatory_fields=["invoice_number", "invoice_date"])
    by_name = {v["field_name"]: v for v in result["field_validations"]}
    assert by_name["invoice_number"]["status"] == "correct"
    assert by_name["invoice_number"]["expected_value"] == "11498"
    assert by_name["invoice_date"]["status"] == "correct"
    assert result["overall_score"] == 100.0


def test_score_uses_required_fields_only():
    result = {
        "status": "passed",
        "overall_score": 100,
        "field_validations": [
            {
                "field_name": "invoice_number",
                "expected_value": "11448",
                "actual_value": "11448",
                "status": "correct",
                "is_mandatory": True,
            },
            {
                "field_name": "invoice_date",
                "expected_value": "2024-08-12",
                "actual_value": "wrong-date",
                "status": "wrong",
                "is_mandatory": True,
            },
            {
                "field_name": "currency",
                "expected_value": "USD",
                "actual_value": "USD",
                "status": "correct",
                "is_mandatory": False,
            },
        ],
    }
    apply_comparison(result, mandatory_fields=["invoice_number", "invoice_date"])
    # 1 of 2 required comparable fields correct → 50%
    assert result["overall_score"] == 50.0
    assert result["status"] == "failed"


def test_optional_fields_do_not_affect_score():
    result = {
        "status": "passed",
        "overall_score": 100,
        "field_validations": [
            {
                "field_name": "invoice_number",
                "expected_value": "11448",
                "actual_value": "11448",
                "status": "correct",
                "is_mandatory": True,
            },
            {
                "field_name": "currency",
                "expected_value": "USD",
                "actual_value": "EUR",
                "status": "wrong",
                "is_mandatory": True,  # LLM flag must not override project list
            },
        ],
    }
    apply_comparison(result, mandatory_fields=["invoice_number"])
    assert result["overall_score"] == 100.0
    assert result["status"] == "passed"
    assert result["field_validations"][1]["is_mandatory"] is False


def test_api_error_rows_are_not_marked_all_correct():
    result = {
        "api_status": 400,
        "status": "failed",
        "overall_score": 0,
        "field_validations": [],
    }
    apply_comparison(result)
    assert result["status"] == "failed"
    assert result["overall_score"] == 0.0
    assert result["field_validations"] == []


def test_llm_equivalent_match_survives_reclassify():
    result = {
        "status": "failed",
        "overall_score": 0,
        "field_validations": [
            {
                "field_name": "charge_name",
                "expected_value": "Fuel Surcharge Miles",
                "actual_value": "Fuel Surcharge",
                "status": "correct",
                "equivalent_match": True,
                "is_mandatory": True,
            },
        ],
    }
    apply_comparison(result, mandatory_fields=["charge_name"])
    assert result["field_validations"][0]["status"] == "correct"
    assert result["overall_score"] == 100.0


def test_all_unverified_is_unscored_not_failed():
    result = {
        "status": "failed",
        "overall_score": 0,
        "field_validations": [
            {
                "field_name": "invoice_number",
                "expected_value": None,
                "actual_value": "35791570",
                "status": "unverified",
                "is_mandatory": True,
            },
            {
                "field_name": "invoice_date",
                "expected_value": None,
                "actual_value": "08-Jul-2026",
                "status": "unverified",
                "is_mandatory": True,
            },
        ],
    }
    apply_comparison(result, mandatory_fields=["invoice_number", "invoice_date"])
    assert result["status"] == "unscored"
    assert all(v["status"] == "unverified" for v in result["field_validations"])


def test_failure_reason_kept_on_wrong_and_cleared_on_correct():
    result = {
        "status": "failed",
        "overall_score": 0,
        "field_validations": [
            {
                "field_name": "destination_name",
                "expected_value": "GE APPLIANCE",
                "actual_value": "AP5",
                "status": "wrong",
                "reason": "AP5 is a location code, not the consignee GE APPLIANCE.",
            },
            {
                "field_name": "vendor_name",
                "expected_value": "AVERITT EXPRESS INC.",
                "actual_value": "Averitt",
                "status": "wrong",
                "reason": "should be cleared after brand match",
            },
        ],
    }
    apply_comparison(result)
    by_name = {v["field_name"]: v for v in result["field_validations"]}
    dest = by_name["destination_name"]
    vend = by_name["vendor_name"]
    assert dest["status"] == "wrong"
    assert "AP5" in dest["reason"]
    assert vend["status"] == "correct"
    assert not vend.get("reason")


def test_wrong_field_gets_default_reason():
    result = {
        "status": "failed",
        "overall_score": 0,
        "field_validations": [
            {
                "field_name": "destination_name",
                "expected_value": "GE APPLIANCE",
                "actual_value": "AP5",
                "status": "wrong",
            },
        ],
    }
    apply_comparison(result)
    reason = result["field_validations"][0]["reason"]
    assert "GE APPLIANCE" in reason
    assert "AP5" in reason

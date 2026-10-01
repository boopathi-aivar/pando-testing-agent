from services.prompt_template import (
    format_carrier_prompt,
    get_prompt_template,
    resolve_carrier_entry,
    resolve_classified_carrier_name,
    slot_from_project,
    split_carrier_prompt,
)

TEMPLATES = {
    "Madison Logistics": {
        "carrier_name": "Madison Logistics",
        "prompt_template": (
            '"vendor_ref_id": Always "MOLP"\n'
            "Extract Madison fields and map SCAC MOLP.\n{pdf_text}"
        ),
    },
    "DAYTON_FREIGHT_LINES_INC": {
        "carrier_name": "Dayton Freight Lines Inc",
        "prompt_template": (
            '"vendor_ref_id": Always "DAFG"\n'
            "Use the labeled Invoice Date. Map ROPER to RPF.\n{pdf_text}"
        ),
    },
    "XPO_LOGISTICS_LLC": {
        "carrier_name": "XPO Logistics",
        "prompt_template": "XPO extraction rules only.",
    },
}


def test_exact_carrier_key():
    key, data = resolve_carrier_entry(TEMPLATES, "Madison Logistics", None)
    assert key == "Madison Logistics"
    assert "MOLP" in data["prompt_template"]


def test_processor_underscore_key():
    key = resolve_classified_carrier_name(TEMPLATES, "Dayton Freight Lines Inc")
    assert key == "DAYTON_FREIGHT_LINES_INC"


def test_llc_suffix_key():
    key = resolve_classified_carrier_name(TEMPLATES, "XPO Logistics")
    assert key == "XPO_LOGISTICS_LLC"


def test_get_prompt_template_returns_prompt_field():
    prompt, key = get_prompt_template("Dayton Freight Lines Inc", TEMPLATES)
    assert key == "DAYTON_FREIGHT_LINES_INC"
    assert "ROPER" in prompt
    assert "{pdf_text}" in prompt


def test_vendor_ref_id_match():
    key, _ = resolve_carrier_entry(TEMPLATES, None, "DAFG")
    assert key == "DAYTON_FREIGHT_LINES_INC"


def test_fuzzy_carrier_name():
    key, _ = resolve_carrier_entry(TEMPLATES, "XPO Logistics Inc", None)
    assert key == "XPO_LOGISTICS_LLC"


def test_no_match():
    key, data = resolve_carrier_entry(TEMPLATES, "Unknown Carrier LLC", None)
    assert key is None
    assert data is None


def test_format_injects_pdf_text():
    out = format_carrier_prompt("Rules {pdf_text}", "INVOICE BODY")
    assert "Rules" in out
    assert "INVOICE BODY" in out
    assert "{pdf_text}" not in out


def test_split_carrier_prompt_matches_processor():
    static, dynamic = split_carrier_prompt("Rules {pdf_text} more", "INVOICE BODY")
    assert "{pdf_text}" not in static
    assert "the invoice text provided at the end of this message" in static
    assert dynamic.startswith("=== INVOICE TEXT ===")
    assert "INVOICE BODY" in dynamic


def test_slot_from_project_requires_enabled():
    project = {
        "file_slots": [
            {
                "id": "prompt-template",
                "enabled": False,
                "s3_bucket": "client-bucket",
                "s3_key": "prompts/PROMPT_TEMPLATES.py",
            }
        ]
    }
    assert slot_from_project(project) == ("", "")
    project["file_slots"][0]["enabled"] = True
    assert slot_from_project(project) == ("client-bucket", "prompts/PROMPT_TEMPLATES.py")


def test_normalize_prefix_and_s3_uri():
    from services.prompt_template import normalize_s3_ref
    assert normalize_s3_ref(
        "pando-general-electronics-destination-bucket-temp",
        "templates/invoice/",
    ) == (
        "pando-general-electronics-destination-bucket-temp",
        "templates/invoice/prompt_template.py",
    )
    assert normalize_s3_ref(
        "s3://pando-general-electronics-destination-bucket-temp/templates/invoice/",
        "",
    ) == (
        "pando-general-electronics-destination-bucket-temp",
        "templates/invoice/prompt_template.py",
    )

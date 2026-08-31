"""
Seed script — inserts initial projects and results into MongoDB only when
the collections are empty.  Called automatically at server startup.
Run standalone:  python seed.py
"""

from database import col_projects, col_results

# ── Default file slots ────────────────────────────────────────────────────────
_SLOTS = [
    {"id": "field-mapping-sheet",  "label": "Field Mapping Sheet",  "required": False, "enabled": False, "s3_bucket": "", "s3_key": "", "description": "Excel/CSV mapping expected output fields to sources", "isCustom": False},
    {"id": "charge-mapping",       "label": "Charge Mapping",       "required": False, "enabled": False, "s3_bucket": "", "s3_key": "", "description": "Charge code mapping stored in S3",                    "isCustom": False},
    {"id": "country-code-mapping", "label": "Country Code Mapping", "required": False, "enabled": False, "s3_bucket": "", "s3_key": "", "description": "Country code lookup table",                           "isCustom": False},
]

_W = {"charge_fields": 25, "address_fields": 25, "date_fields": 25, "amount_fields": 25}
_API = "http://localhost:3001/api"


# ── Seed data ─────────────────────────────────────────────────────────────────
PROJECTS = [
    {
        "project_id": "ge-freight",
        "project_name": "GE Freight",
        "status": "configured",
        "last_score": 91.0,
        "last_tested": "2 hours ago",
        "cloudwatch_log_group": "/aws/lambda/invoice-processor-ge-freight",
        "email_recipients": ["admin@ge.com", "ops@ge.com"],
        "target_api_url": _API,
        "file_slots": _SLOTS,
        "scoring_weights": _W,
        "log_window_hours": 24,
        "invoice_filter": "",
        "notify_email": True,
    },
    {
        "project_id": "dhl-express",
        "project_name": "DHL Express",
        "status": "incomplete",
        "last_score": None,
        "last_tested": None,
        "cloudwatch_log_group": "",
        "email_recipients": [],
        "target_api_url": "",
        "file_slots": _SLOTS,
        "scoring_weights": _W,
        "log_window_hours": 24,
        "invoice_filter": "",
        "notify_email": True,
    },
    {
        "project_id": "maersk-logistics",
        "project_name": "Maersk Logistics",
        "status": "configured",
        "last_score": 74.0,
        "last_tested": "1 day ago",
        "cloudwatch_log_group": "/aws/lambda/invoice-processor-maersk",
        "email_recipients": ["dev@maersk.com"],
        "target_api_url": _API,
        "file_slots": _SLOTS,
        "scoring_weights": {"charge_fields": 30, "address_fields": 20, "date_fields": 25, "amount_fields": 25},
        "log_window_hours": 48,
        "invoice_filter": "",
        "notify_email": True,
    },
    {
        "project_id": "fedex-intl",
        "project_name": "FedEx Intl",
        "status": "never_tested",
        "last_score": None,
        "last_tested": None,
        "cloudwatch_log_group": "/aws/lambda/invoice-processor-fedex",
        "email_recipients": ["tech@fedex.com"],
        "target_api_url": _API,
        "file_slots": _SLOTS,
        "scoring_weights": _W,
        "log_window_hours": 24,
        "invoice_filter": "",
        "notify_email": False,
    },
    {
        "project_id": "kn-global",
        "project_name": "Kuehne+Nagel",
        "status": "configured",
        "last_score": 88.0,
        "last_tested": "5 hours ago",
        "cloudwatch_log_group": "/aws/lambda/invoice-processor-kn",
        "email_recipients": ["it@kuehne-nagel.com"],
        "target_api_url": _API,
        "file_slots": _SLOTS,
        "scoring_weights": _W,
        "log_window_hours": 24,
        "invoice_filter": "",
        "notify_email": True,
    },
    {
        "project_id": "panalpina-ch",
        "project_name": "Panalpina",
        "status": "incomplete",
        "last_score": None,
        "last_tested": None,
        "cloudwatch_log_group": "",
        "email_recipients": ["admin@panalpina.com"],
        "target_api_url": "",
        "file_slots": _SLOTS,
        "scoring_weights": _W,
        "log_window_hours": 24,
        "invoice_filter": "",
        "notify_email": True,
    },
]

RESULTS = [
    # ── GE Freight ─────────────────────────────────────────────────────────
    {
        "result_id": "r-ge-001",
        "project_id": "ge-freight",
        "invoice_number": "INV-2024-0091",
        "timestamp": "2 hours ago",
        "overall_score": 94.0,
        "status": "passed",
        "vendor_name": "GE Freight Lines",
        "field_validations": [
            {"field_name": "carrier_name",   "expected_value": "GE Freight Lines", "actual_value": "GE Freight Lines", "status": "correct", "source_used": "LLM Response"},
            {"field_name": "charge_code",    "expected_value": "FCL-20",           "actual_value": "FCL20",            "status": "wrong",   "source_used": "LLM Response"},
            {"field_name": "origin_country", "expected_value": "US",               "actual_value": "US",               "status": "correct", "source_used": "Country Code Mapping"},
            {"field_name": "invoice_date",   "expected_value": "2024-03-15",       "actual_value": "2024-03-15",       "status": "correct", "source_used": "LLM Response"},
            {"field_name": "invoice_total",  "expected_value": "4250.00",          "actual_value": "4250.00",          "status": "correct", "source_used": "LLM Response"},
            {"field_name": "shipper_name",   "expected_value": "ACME Corp Ltd",    "actual_value": "ACME Corp Ltd",    "status": "correct", "source_used": "LLM Response"},
        ],
        "prompt_suggestions": [
            "Specify charge code format explicitly — include hyphen: FCL-20 not FCL20",
            "Add few-shot example for charge code formatting in the prompt",
        ],
        "log_summary": {
            "errors": [],
            "warnings": ["charge code format mismatch, fuzzy matched"],
            "execution_duration_ms": 3240,
            "cold_start": False,
        },
        "raw_payload": {
            "invoice_number": "INV-2024-0091",
            "carrier": "GE Freight Lines",
            "charge_code": "FCL20",
            "origin_country": "US",
            "destination_country": "DE",
            "invoice_date": "2024-03-15",
            "invoice_total": 4250.00,
            "shipper": {"name": "ACME Corp Ltd", "address": "123 Main St, New York, NY"},
            "consignee": {"name": "Berlin Imports GmbH", "address": "Berliner Str. 45, Berlin, DE"},
            "line_items": [
                {"description": "Ocean Freight",     "amount": 3800.00},
                {"description": "Documentation Fee", "amount": 150.00},
                {"description": "Port Surcharge",    "amount": 300.00},
            ],
        },
    },
    {
        "result_id": "r-ge-002",
        "project_id": "ge-freight",
        "invoice_number": "INV-2024-0087",
        "timestamp": "1 day ago",
        "overall_score": 71.0,
        "status": "warning",
        "vendor_name": "GE Freight Lines",
        "field_validations": [
            {"field_name": "carrier_name",   "expected_value": "GE Freight Lines", "actual_value": "GE Freight Lines", "status": "correct", "source_used": "LLM Response"},
            {"field_name": "charge_code",    "expected_value": "FCL-40",           "actual_value": "FCL40",            "status": "wrong",   "source_used": "LLM Response"},
            {"field_name": "origin_country", "expected_value": "CN",               "actual_value": None,               "status": "missing", "source_used": "LLM Response"},
            {"field_name": "invoice_date",   "expected_value": "2024-03-10",       "actual_value": "2024-03-10",       "status": "correct", "source_used": "LLM Response"},
            {"field_name": "shipper_name",   "expected_value": "ACME Corp Ltd",    "actual_value": "ACME Corp",        "status": "wrong",   "source_used": "LLM Response"},
        ],
        "prompt_suggestions": [
            "Add explicit instruction to extract the full legal entity name for shipper",
            "Include origin country extraction in the prompt — field is consistently missing",
            "Add country code mapping reference in the prompt context",
        ],
        "log_summary": {
            "errors": ["origin_country field extraction failed", "shipper name truncation detected"],
            "warnings": ["charge code format mismatch, fuzzy matched", "shipper name partial match", "origin country defaulted to null"],
            "execution_duration_ms": 4120,
            "cold_start": True,
        },
        "raw_payload": {
            "invoice_number": "INV-2024-0087",
            "carrier": "GE Freight Lines",
            "charge_code": "FCL40",
            "origin_country": None,
            "destination_country": "US",
            "invoice_date": "2024-03-10",
            "invoice_total": 6100.00,
            "shipper": {"name": "ACME Corp", "address": "456 Industrial Ave, Shanghai, CN"},
            "consignee": {"name": "West Coast Distributors", "address": "789 Harbor Blvd, Los Angeles, CA"},
        },
    },
    {
        "result_id": "r-ge-003",
        "project_id": "ge-freight",
        "invoice_number": "INV-2024-0081",
        "timestamp": "3 days ago",
        "overall_score": 88.0,
        "status": "passed",
        "vendor_name": "GE Freight Lines",
        "field_validations": [
            {"field_name": "carrier_name",   "expected_value": "GE Freight Lines",    "actual_value": "GE Freight Lines",    "status": "correct", "source_used": "LLM Response"},
            {"field_name": "charge_code",    "expected_value": "LCL-10",              "actual_value": "LCL-10",              "status": "correct", "source_used": "LLM Response"},
            {"field_name": "origin_country", "expected_value": "JP",                  "actual_value": "JP",                  "status": "correct", "source_used": "Country Code Mapping"},
            {"field_name": "invoice_date",   "expected_value": "2024-03-05",          "actual_value": "2024-03-05",          "status": "correct", "source_used": "LLM Response"},
            {"field_name": "shipper_name",   "expected_value": "Tokyo Electronics Ltd","actual_value": "Tokyo Electronics",  "status": "wrong",   "source_used": "LLM Response"},
        ],
        "prompt_suggestions": [
            "Instruct the model to preserve full legal entity suffix (Ltd, Inc, GmbH) in company names",
        ],
        "log_summary": {
            "errors": [],
            "warnings": ["shipper legal suffix stripped"],
            "execution_duration_ms": 2980,
            "cold_start": False,
        },
        "raw_payload": {
            "invoice_number": "INV-2024-0081",
            "carrier": "GE Freight Lines",
            "charge_code": "LCL-10",
            "origin_country": "JP",
            "invoice_date": "2024-03-05",
            "invoice_total": 2780.00,
            "shipper": {"name": "Tokyo Electronics", "address": "1-1 Akihabara, Tokyo, JP"},
        },
    },
    {
        "result_id": "r-ge-004",
        "project_id": "ge-freight",
        "invoice_number": "INV-2024-0074",
        "timestamp": "5 days ago",
        "overall_score": 55.0,
        "status": "failed",
        "vendor_name": "GE Freight Lines",
        "field_validations": [
            {"field_name": "charge_code",    "expected_value": "AIR-XL",                    "actual_value": "AIRXL",                "status": "wrong",   "source_used": "LLM Response"},
            {"field_name": "origin_country", "expected_value": "IN",                        "actual_value": "IND",                  "status": "wrong",   "source_used": "LLM Response"},
            {"field_name": "consignee_name", "expected_value": "Global Trade Partners LLC", "actual_value": "Global Trade Partners","status": "wrong",   "source_used": "LLM Response"},
            {"field_name": "invoice_total",  "expected_value": "9840.50",                   "actual_value": "9840",                 "status": "wrong",   "source_used": "LLM Response"},
            {"field_name": "carrier_name",   "expected_value": "GE Freight Lines",          "actual_value": "GE Freight Lines",     "status": "correct", "source_used": "LLM Response"},
        ],
        "prompt_suggestions": [
            "Restructure prompt to process charge fields separately from address fields",
            "Add ISO 3166-1 alpha-2 enforcement instruction for country codes",
            "Include decimal precision instruction for monetary amounts (always 2 decimal places)",
            "Add few-shot examples specifically for air freight charge codes with hyphen notation",
        ],
        "log_summary": {
            "errors": [
                "charge mapping table not found in S3 path",
                "country code validation failed: IND is not ISO alpha-2",
                "invoice_total decimal truncation detected",
                "consignee legal suffix missing",
            ],
            "warnings": [
                "fallback to fuzzy match for charge_code",
                "decimal precision warning on invoice_total",
            ],
            "execution_duration_ms": 5500,
            "cold_start": True,
        },
        "raw_payload": {
            "invoice_number": "INV-2024-0074",
            "carrier": "GE Freight Lines",
            "charge_code": "AIRXL",
            "origin_country": "IND",
            "invoice_date": "2024-02-28",
            "invoice_total": 9840,
            "consignee": {"name": "Global Trade Partners", "address": "Mumbai, IN"},
        },
    },
]


# ── Runner ────────────────────────────────────────────────────────────────────
def seed_if_empty() -> None:
    """Insert seed data only when the collections are empty."""
    projects_col = col_projects()
    results_col  = col_results()

    if projects_col.count_documents({}) == 0:
        projects_col.insert_many(PROJECTS)
        print(f"[seed] Inserted {len(PROJECTS)} projects.")
    else:
        print(f"[seed] Projects collection already has data — skipping.")

    if results_col.count_documents({}) == 0:
        results_col.insert_many(RESULTS)
        print(f"[seed] Inserted {len(RESULTS)} results.")
    else:
        print(f"[seed] Results collection already has data — skipping.")


if __name__ == "__main__":
    seed_if_empty()
    print("[seed] Done.")

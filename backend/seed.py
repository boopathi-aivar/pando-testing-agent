"""
Seed script — inserts initial projects and results into DynamoDB only when
the tables are empty.  Called automatically at server startup.
Run standalone:  python seed.py
"""

from database import tbl_projects, tbl_results
from tools.dynamodb_tools import _to_dynamo

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
    {
        "result_id": "r-ge-001",
        "project_id": "ge-freight",
        "invoice_number": "INV-2024-0091",
        "timestamp": "2024-08-01T10:00:00+00:00",
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
            "invoice_date": "2024-03-15",
            "invoice_total": 4250.00,
        },
    },
    {
        "result_id": "r-ge-002",
        "project_id": "ge-freight",
        "invoice_number": "INV-2024-0087",
        "timestamp": "2024-07-31T10:00:00+00:00",
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
            "Include origin country extraction in the prompt — field is consistently missing",
        ],
        "log_summary": {
            "errors": ["origin_country field extraction failed"],
            "warnings": ["charge code format mismatch"],
            "execution_duration_ms": 4120,
            "cold_start": True,
        },
        "raw_payload": {
            "invoice_number": "INV-2024-0087",
            "carrier": "GE Freight Lines",
            "invoice_date": "2024-03-10",
            "invoice_total": 6100.00,
        },
    },
]


# ── Runner ────────────────────────────────────────────────────────────────────
def seed_if_empty() -> None:
    """Insert seed data only when the tables are empty."""
    projects_tbl = tbl_projects()
    results_tbl  = tbl_results()

    proj_count = projects_tbl.scan(Select="COUNT").get("Count", 0)
    if proj_count == 0:
        with projects_tbl.batch_writer() as batch:
            for p in PROJECTS:
                # Remove None values — DynamoDB cannot store them
                clean = {k: v for k, v in p.items() if v is not None}
                batch.put_item(Item=_to_dynamo(clean))
        print(f"[seed] Inserted {len(PROJECTS)} projects.")
    else:
        print(f"[seed] Projects table already has data — skipping.")

    res_count = results_tbl.scan(Select="COUNT").get("Count", 0)
    if res_count == 0:
        with results_tbl.batch_writer() as batch:
            for r in RESULTS:
                clean = {k: v for k, v in r.items() if v is not None}
                batch.put_item(Item=_to_dynamo(clean))
        print(f"[seed] Inserted {len(RESULTS)} results.")
    else:
        print(f"[seed] Results table already has data — skipping.")


if __name__ == "__main__":
    seed_if_empty()
    print("[seed] Done.")

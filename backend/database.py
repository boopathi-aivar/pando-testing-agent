"""
DynamoDB connection — boto3 resource/table helpers reused across requests.
Tables:
  pando-projects  — project configurations        (PK: project_id)
  pando-results   — test results per project      (PK: result_id, GSI: project_id-index)
  pando-jobs      — in-flight test jobs with TTL  (PK: job_id)
"""

import os
import time
import boto3
from dotenv import load_dotenv
from boto3.dynamodb.conditions import Key

load_dotenv()

_AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")

_PROJECTS_TABLE = os.getenv("PROJECTS_TABLE", "pando-projects")
_RESULTS_TABLE  = os.getenv("RESULTS_TABLE",  "pando-results")
_JOBS_TABLE     = os.getenv("JOBS_TABLE",     "pando-jobs")

_dynamodb = None


def _get_resource():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=_AWS_REGION)
    return _dynamodb


def tbl_projects():
    return _get_resource().Table(_PROJECTS_TABLE)


def tbl_results():
    return _get_resource().Table(_RESULTS_TABLE)


def tbl_jobs():
    return _get_resource().Table(_JOBS_TABLE)


def check_connection() -> None:
    print("\n" + "─" * 60)
    print("  DynamoDB connection check")
    print(f"  Region          : {_AWS_REGION}")
    print(f"  Projects table  : {_PROJECTS_TABLE}")
    print(f"  Results table   : {_RESULTS_TABLE}")
    print(f"  Jobs table      : {_JOBS_TABLE}")
    print("─" * 60)

    try:
        tbl_projects().load()
        print("  ✓  Connected — tables are accessible.")
    except Exception as exc:
        print(f"  ✗  FAILED — {exc}")
        print("     Check: IAM permissions, table names, AWS_REGION env var.")

    print("─" * 60 + "\n")


def ensure_tables() -> None:
    """
    Create DynamoDB tables if they don't already exist.
    Safe to call on every startup — no-ops when tables exist.
    """
    client = boto3.client("dynamodb", region_name=_AWS_REGION)
    existing = {t["TableName"] for t in client.list_tables()["TableNames"]}

    # ── pando-projects ────────────────────────────────────────────────────────
    if _PROJECTS_TABLE not in existing:
        client.create_table(
            TableName=_PROJECTS_TABLE,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "project_id", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "project_id", "KeyType": "HASH"},
            ],
        )
        print(f"[DynamoDB] Created table: {_PROJECTS_TABLE}")

    # ── pando-results ─────────────────────────────────────────────────────────
    if _RESULTS_TABLE not in existing:
        client.create_table(
            TableName=_RESULTS_TABLE,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "result_id",  "AttributeType": "S"},
                {"AttributeName": "project_id", "AttributeType": "S"},
                {"AttributeName": "timestamp",  "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "result_id", "KeyType": "HASH"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "project_id-index",
                    "KeySchema": [
                        {"AttributeName": "project_id", "KeyType": "HASH"},
                        {"AttributeName": "timestamp",  "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
        )
        print(f"[DynamoDB] Created table: {_RESULTS_TABLE}")

    # ── pando-jobs ────────────────────────────────────────────────────────────
    if _JOBS_TABLE not in existing:
        client.create_table(
            TableName=_JOBS_TABLE,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "job_id", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "job_id", "KeyType": "HASH"},
            ],
        )
        # Enable TTL on the 'ttl' attribute
        client.update_time_to_live(
            TableName=_JOBS_TABLE,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
        )
        print(f"[DynamoDB] Created table: {_JOBS_TABLE} (TTL on 'ttl' attribute)")

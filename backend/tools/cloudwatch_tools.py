"""
CloudWatch Strands tools — thin wrappers around services/cloudwatch.py.
"""

import json
from strands import tool
from services.cloudwatch import query_logs, get_recent_invoice_number, get_all_invoice_logs, extract_invoice_blocks


@tool
def search_cloudwatch_logs(log_group: str, query_string: str, hours: int = 24) -> str:
    """
    Run a CloudWatch Insights query against a log group and return matching records.
    log_group: CloudWatch log group name (e.g. /aws/lambda/invoice-processor-ge-freight)
    query_string: CloudWatch Insights query expression
    hours: how many hours back to search (default 24)
    Returns a JSON array of log record objects.
    """
    records = query_logs(log_group, query_string, hours)
    return json.dumps(records)


@tool
def get_latest_invoice_from_logs(log_group: str, hours: int = 24) -> str:
    """
    Extract the most recent invoice number processed by the Lambda from CloudWatch logs.
    log_group: CloudWatch log group name
    hours: how many hours back to search
    Returns the invoice number string, or empty string if none found.
    """
    invoice = get_recent_invoice_number(log_group, hours)
    return invoice or ""


@tool
def get_lambda_errors(log_group: str, hours: int = 24) -> str:
    """
    Query CloudWatch for ERROR-level log entries from the invoice processor Lambda.
    log_group: CloudWatch log group name
    hours: how many hours back to search
    Returns a JSON array of error log records.
    """
    records = query_logs(
        log_group,
        "fields @message | filter @message like /ERROR/ | sort @timestamp desc | limit 20",
        hours,
    )
    return json.dumps(records)


@tool
def get_all_invoice_payloads(log_group: str, hours: int = 24) -> str:
    """
    Fetch all log messages from a CloudWatch log group, split them into per-invoice
    blocks (each starting at an email object and ending at an API status code),
    and return structured data for every invoice found in that time window.
    log_group: CloudWatch log group name
    hours: how many hours back to search
    Returns a JSON array. Each element: {invoice_number, payload, api_status, errors, warnings, execution_duration_ms, cold_start}
    """
    messages = get_all_invoice_logs(log_group, hours)
    blocks = extract_invoice_blocks(messages)
    return json.dumps(blocks)

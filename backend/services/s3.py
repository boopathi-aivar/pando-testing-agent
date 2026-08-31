"""
S3 service — real Boto3 implementation using credentials from config.

IAM permissions needed:
  s3:GetObject
  s3:HeadObject
"""

from botocore.exceptions import ClientError
from config import make_aws_session


def _client():
    return make_aws_session().client("s3")


def get_object(bucket: str, key: str) -> str:
    """Read a text file from S3 and return its content as a string."""
    try:
        resp = _client().get_object(Bucket=bucket, Key=key)
        return resp["Body"].read().decode("utf-8")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "NoSuchKey":
            raise FileNotFoundError(f"s3://{bucket}/{key} not found")
        raise RuntimeError(f"S3 GetObject failed: {e.response['Error']['Message']}")


def get_binary(bucket: str, key: str) -> bytes:
    """Download any binary file from S3 and return raw bytes."""
    try:
        resp = _client().get_object(Bucket=bucket, Key=key)
        return resp["Body"].read()
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "NoSuchKey":
            raise FileNotFoundError(f"s3://{bucket}/{key} not found")
        raise RuntimeError(f"S3 GetObject failed: {e.response['Error']['Message']}")


get_excel_bytes = get_binary   # backward-compat alias used by input_collector
get_pdf_bytes   = get_binary   # alias for PDF downloads


def object_exists(bucket: str, key: str) -> bool:
    """Return True if the S3 object exists."""
    try:
        _client().head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        raise RuntimeError(f"S3 HeadObject failed: {e.response['Error']['Message']}")

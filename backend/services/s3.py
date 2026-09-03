"""
S3 service — real Boto3 implementation using credentials from config.

IAM permissions needed:
  s3:GetObject
  s3:HeadObject
  s3:GetBucketLocation

The invoice PDFs and mapping files live in a different AWS account than
this backend (and possibly a different region per bucket — e.g. one bucket
in us-east-1, another in us-east-2), so this uses make_source_aws_session()
— a session built from credentials stored in Secrets Manager (see config.py)
— rather than this account's own IAM role/credentials, and resolves each
bucket's actual region before making requests to avoid PermanentRedirect
errors.
"""

from botocore.exceptions import ClientError
from config import make_source_aws_session

# Cache of bucket_name -> region, so we only call GetBucketLocation once per
# bucket per warm Lambda container instead of on every request.
_bucket_region_cache: dict[str, str] = {}


def _bucket_region(bucket: str) -> str:
    """Resolve the AWS region a bucket actually lives in."""
    if bucket in _bucket_region_cache:
        return _bucket_region_cache[bucket]

    # GetBucketLocation can be called from any region's endpoint.
    probe = make_source_aws_session().client("s3", region_name="us-east-1")
    try:
        resp = probe.get_bucket_location(Bucket=bucket)
        region = resp.get("LocationConstraint") or "us-east-1"
        # S3 quirk: EU is reported as "EU" instead of "eu-west-1"; None means us-east-1
        if region == "EU":
            region = "eu-west-1"
    except ClientError as e:
        raise RuntimeError(f"Could not resolve region for bucket '{bucket}': {e.response['Error']['Message']}")

    _bucket_region_cache[bucket] = region
    return region


def _client(bucket: str):
    return make_source_aws_session().client("s3", region_name=_bucket_region(bucket))


def get_object(bucket: str, key: str) -> str:
    """Read a text file from S3 and return its content as a string."""
    try:
        resp = _client(bucket).get_object(Bucket=bucket, Key=key)
        return resp["Body"].read().decode("utf-8")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "NoSuchKey":
            raise FileNotFoundError(f"s3://{bucket}/{key} not found")
        raise RuntimeError(f"S3 GetObject failed: {e.response['Error']['Message']}")


def get_binary(bucket: str, key: str) -> bytes:
    """Download any binary file from S3 and return raw bytes."""
    try:
        resp = _client(bucket).get_object(Bucket=bucket, Key=key)
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
        _client(bucket).head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        raise RuntimeError(f"S3 HeadObject failed: {e.response['Error']['Message']}")

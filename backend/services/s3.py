"""
S3 service — boto3 via make_source_aws_session().

IAM needed for invoice PDFs / mappings:
  s3:GetObject
  s3:HeadObject

Do not call s3:GetBucketLocation up front. Many Pando IAM policies allow
GetObject on a prefix but deny GetBucketLocation on the bucket, which used
to abort every download with Access Denied before the object was even read.
Region is taken from SOURCE_ACCOUNT_REGION / AWS_REGION, then S3 redirect
headers if the bucket lives elsewhere.
"""

from botocore.exceptions import ClientError

from config import make_source_aws_session, settings
from services.cache import content_key, s3_object_cache

_bucket_region_cache: dict[str, str] = {}

_FALLBACK_REGIONS = ("us-east-1", "us-east-2", "us-west-2", "ap-south-1")
_REDIRECT_CODES = {
    "301", "PermanentRedirect", "AuthorizationHeaderMalformed",
    "IllegalLocationConstraintException",
}


def _preferred_regions(bucket: str) -> list[str]:
    ordered: list[str] = []
    for region in (
        _bucket_region_cache.get(bucket),
        settings.SOURCE_ACCOUNT_REGION,
        settings.AWS_REGION,
        *_FALLBACK_REGIONS,
    ):
        if region and region not in ordered:
            ordered.append(region)
    return ordered


def _region_from_redirect(exc: ClientError) -> str | None:
    resp = exc.response or {}
    headers = (resp.get("ResponseMetadata") or {}).get("HTTPHeaders") or {}
    for name, value in headers.items():
        if name.lower() == "x-amz-bucket-region" and value:
            return str(value)
    err = resp.get("Error") or {}
    endpoint = str(err.get("Endpoint") or "")
    # bucket.s3.us-east-2.amazonaws.com
    parts = endpoint.split(".")
    if "s3" in parts:
        i = parts.index("s3")
        if i + 1 < len(parts) and parts[i + 1] not in ("amazonaws", "com"):
            return parts[i + 1]
    return None


def _client(region: str):
    return make_source_aws_session().client("s3", region_name=region)


def _call(op: str, bucket: str, **kwargs):
    """Run an S3 API in the right region without GetBucketLocation."""
    last: Exception | None = None
    extra: list[str] = []
    for region in _preferred_regions(bucket) + extra:
        try:
            result = getattr(_client(region), op)(Bucket=bucket, **kwargs)
            _bucket_region_cache[bucket] = region
            return result
        except ClientError as e:
            last = e
            code = str((e.response.get("Error") or {}).get("Code") or "")
            if code in _REDIRECT_CODES:
                hint = _region_from_redirect(e)
                if hint and hint not in extra and hint not in _preferred_regions(bucket):
                    extra.append(hint)
                    continue
                continue
            raise
    if last is not None:
        raise last
    raise RuntimeError(f"S3 {op} failed for bucket '{bucket}'")


def get_object(bucket: str, key: str) -> str:
    """Read a text file from S3 and return its content as a string."""
    cache_id = content_key("s3-text", bucket, key)
    cached = s3_object_cache.get(cache_id)
    if cached is not None:
        return cached
    try:
        resp = _call("get_object", bucket, Key=key)
        text = resp["Body"].read().decode("utf-8")
        s3_object_cache.set(cache_id, text)
        return text
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("NoSuchKey", "404"):
            raise FileNotFoundError(f"s3://{bucket}/{key} not found")
        raise RuntimeError(f"S3 GetObject failed: {e.response['Error']['Message']}")


def get_binary(bucket: str, key: str) -> bytes:
    """Download any binary file from S3 and return raw bytes."""
    try:
        resp = _call("get_object", bucket, Key=key)
        return resp["Body"].read()
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("NoSuchKey", "404"):
            raise FileNotFoundError(f"s3://{bucket}/{key} not found")
        raise RuntimeError(f"S3 GetObject failed: {e.response['Error']['Message']}")


get_excel_bytes = get_binary
get_pdf_bytes = get_binary


def object_exists(bucket: str, key: str) -> bool:
    """Return True if the S3 object exists."""
    try:
        _call("head_object", bucket, Key=key)
        return True
    except ClientError as e:
        code = str((e.response.get("Error") or {}).get("Code") or "")
        if code in ("404", "404 Not Found", "NotFound", "NoSuchKey"):
            return False
        raise RuntimeError(f"S3 HeadObject failed: {e.response['Error']['Message']}")

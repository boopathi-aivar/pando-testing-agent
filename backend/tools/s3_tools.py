"""
S3 Strands tools — thin wrappers around services/s3.py.
"""

from strands import tool
from services.s3 import get_object, object_exists


@tool
def fetch_s3_file(bucket: str, key: str) -> str:
    """
    Download a text file from S3 and return its content as a string.
    bucket: S3 bucket name
    key: object key (path within the bucket)
    Returns the file content, or an error message if the object does not exist.
    """
    if not bucket or not key:
        return ""
    if not object_exists(bucket, key):
        return f"[not found] s3://{bucket}/{key}"
    return get_object(bucket, key)


@tool
def check_s3_file_exists(bucket: str, key: str) -> str:
    """
    Check whether an S3 object exists.
    Returns 'true' or 'false' as a string.
    bucket: S3 bucket name
    key: object key
    """
    if not bucket or not key:
        return "false"
    return "true" if object_exists(bucket, key) else "false"

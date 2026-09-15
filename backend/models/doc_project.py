"""
Pydantic models for Documentation projects.

A documentation project points at a prompt template stored in S3. It captures
only two user-supplied fields — a display name and the S3 path to the prompt
template — and the derived summary is parsed live from S3 on demand.
"""

from pydantic import BaseModel
from typing import Optional


class DocProjectCreate(BaseModel):
    project_name: str
    # Either an s3:// URI or an explicit bucket + key. The router accepts an
    # s3_path (s3://bucket/key) and/or the split fields.
    s3_path: Optional[str] = None
    s3_bucket: Optional[str] = None
    s3_key: Optional[str] = None


class DocProject(BaseModel):
    doc_id: str
    project_name: str
    s3_bucket: str
    s3_key: str
    s3_path: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

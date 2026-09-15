"""
Documentation projects router.

A documentation project stores a display name plus the S3 location of a prompt
template. The summary endpoint parses that template live from S3 and returns a
structured breakdown (carriers, carrier classification, page classification,
field mapping) — so whenever the template in S3 is updated, re-fetching the
summary reflects the new content.
"""

import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs, unquote

from fastapi import APIRouter, Depends, HTTPException, status

from models.doc_project import DocProject, DocProjectCreate
from database import tbl_docs
from tools.dynamodb_tools import _to_dynamo, _from_dynamo
from routers.auth import get_current_user
from services import prompt_template as pt

router = APIRouter(tags=["docprojects"])


def _parse_s3_path(s3_path: str | None, bucket: str | None, key: str | None) -> tuple[str, str]:
    """
    Normalize the user-supplied S3 location into (bucket, key).

    Accepts:
      - s3://my-bucket/path/to/template.py
      - my-bucket/path/to/template.py
      - an AWS S3 console URL, e.g.
          https://<region>.console.aws.amazon.com/s3/object/<bucket>?prefix=<key>
          https://console.aws.amazon.com/s3/buckets/<bucket>?prefix=<key>
      - a virtual-hosted / path-style HTTPS S3 URL, e.g.
          https://<bucket>.s3.<region>.amazonaws.com/<key>
          https://s3.<region>.amazonaws.com/<bucket>/<key>
      - explicit bucket + key fields
    """
    if bucket and key:
        return bucket.strip(), key.strip().lstrip("/")

    raw = (s3_path or "").strip()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An S3 path to the prompt template is required (e.g. s3://bucket/path/to/template.py).",
        )

    # s3://bucket/key
    m = re.match(r"^s3://([^/]+)/(.+)$", raw, re.IGNORECASE)
    if m:
        return m.group(1), m.group(2).lstrip("/")

    # https:// URLs — AWS console links or S3 REST endpoints
    if raw.lower().startswith(("http://", "https://")):
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        path = parsed.path or ""
        query = parse_qs(parsed.query or "")
        prefix = (query.get("prefix", [None])[0] or "").strip()

        # AWS console: /s3/object/<bucket>  or  /s3/buckets/<bucket>
        cm = re.search(r"/s3/(?:object|buckets)/([^/?#]+)", path, re.IGNORECASE)
        if cm:
            b = unquote(cm.group(1))
            k = unquote(prefix) if prefix else unquote(path.split(cm.group(1), 1)[-1]).lstrip("/")
            if b and k:
                return b, k.lstrip("/")

        # Virtual-hosted style: <bucket>.s3(.<region>).amazonaws.com/<key>
        vm = re.match(r"^([^.]+)\.s3[.-][^/]*amazonaws\.com$", host) or re.match(r"^([^.]+)\.s3\.amazonaws\.com$", host)
        if vm:
            k = unquote(path).lstrip("/")
            if vm.group(1) and k:
                return vm.group(1), k

        # Path style: s3(.<region>).amazonaws.com/<bucket>/<key>
        if "amazonaws.com" in host and path.strip("/"):
            b, _, k = path.strip("/").partition("/")
            if b and k:
                return unquote(b), unquote(k)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Could not read the bucket and key from that URL. Paste the S3 path "
                "in the form s3://bucket-name/path/to/template.py."
            ),
        )

    # bucket/key form
    if "/" in raw:
        b, k = raw.split("/", 1)
        if b and k:
            return b.strip(), k.strip().lstrip("/")

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Could not parse the S3 path. Use the form s3://bucket-name/path/to/template.py.",
    )


@router.get("/doc-projects", response_model=list[DocProject])
def list_doc_projects(_user=Depends(get_current_user)):
    resp = tbl_docs().scan()
    items = [_from_dynamo(i) for i in resp.get("Items", [])]
    items.sort(key=lambda d: (d.get("created_at") or ""), reverse=True)
    return items


@router.get("/doc-projects/{doc_id}", response_model=DocProject)
def get_doc_project(doc_id: str, _user=Depends(get_current_user)):
    item = tbl_docs().get_item(Key={"doc_id": doc_id}).get("Item")
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Documentation project '{doc_id}' not found")
    return _from_dynamo(item)


@router.post("/doc-projects", response_model=DocProject, status_code=status.HTTP_201_CREATED)
def create_doc_project(body: DocProjectCreate, _user=Depends(get_current_user)):
    name = (body.project_name or "").strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Project name is required.")

    bucket, key = _parse_s3_path(body.s3_path, body.s3_bucket, body.s3_key)

    now = datetime.now(tz=timezone.utc).isoformat()
    doc = {
        "doc_id": uuid.uuid4().hex,
        "project_name": name,
        "s3_bucket": bucket,
        "s3_key": key,
        "s3_path": f"s3://{bucket}/{key}",
        "created_at": now,
        "updated_at": now,
    }
    tbl_docs().put_item(Item=_to_dynamo(doc))
    return doc


@router.delete("/doc-projects/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_doc_project(doc_id: str, _user=Depends(get_current_user)):
    existing = tbl_docs().get_item(Key={"doc_id": doc_id}).get("Item")
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Documentation project '{doc_id}' not found")
    tbl_docs().delete_item(Key={"doc_id": doc_id})


@router.get("/doc-projects/{doc_id}/summary")
def get_doc_project_summary(doc_id: str, _user=Depends(get_current_user)):
    """
    Parse the prompt template from S3 and return a structured documentation
    summary. Parsed live on each call, so updates to the S3 template are
    reflected the next time this endpoint is hit.
    """
    item = tbl_docs().get_item(Key={"doc_id": doc_id}).get("Item")
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Documentation project '{doc_id}' not found")

    doc = _from_dynamo(item)
    bucket = doc.get("s3_bucket") or ""
    key = doc.get("s3_key") or ""

    try:
        summary = pt.build_summary(bucket, key)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prompt template not found at s3://{bucket}/{key}.",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to read the prompt template from S3: {exc}",
        )

    return {
        "doc_id": doc_id,
        "project_name": doc.get("project_name"),
        "s3_path": doc.get("s3_path") or f"s3://{bucket}/{key}",
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
        **summary,
    }

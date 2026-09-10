"""
In-process TTL cache for PDF text, Excel mappings, and PDF expected-value extraction.

Warm Lambda containers and local uvicorn reuse this across invoices that share
the same S3 files / PDF content — avoids repeat S3 downloads and Bedrock calls.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any


class TtlCache:
    def __init__(self, max_items: int = 64, ttl_seconds: int = 3600):
        self.max_items = max_items
        self.ttl_seconds = ttl_seconds
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if not item:
            return None
        expires_at, value = item
        if time.time() > expires_at:
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        if len(self._data) >= self.max_items:
            oldest = min(self._data, key=lambda k: self._data[k][0])
            self._data.pop(oldest, None)
        self._data[key] = (time.time() + self.ttl_seconds, value)


def content_key(*parts: str) -> str:
    joined = "|".join(str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8", errors="ignore")).hexdigest()


# Shared caches (module-level so they survive across requests in one process)
pdf_markdown_cache = TtlCache(max_items=32, ttl_seconds=6 * 3600)
excel_mapping_cache = TtlCache(max_items=32, ttl_seconds=6 * 3600)
pdf_expected_cache = TtlCache(max_items=64, ttl_seconds=6 * 3600)
s3_object_cache = TtlCache(max_items=32, ttl_seconds=6 * 3600)

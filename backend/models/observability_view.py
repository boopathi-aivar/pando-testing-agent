"""Pydantic models for configurable Observability views."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class DiscoverSchemaRequest(BaseModel):
    table_name: str = Field(..., min_length=1)
    account: str = Field(default="ops", description="ops | meta")
    sample_limit: int = Field(default=25, ge=1, le=100)


class ObservabilityViewCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    table_name: str = Field(..., min_length=1)
    account: str = Field(default="ops")
    description: str = ""
    selected_fields: list[str] = Field(default_factory=list)
    # Action columns — default all on
    action_view_pdf: bool = True
    action_cloudwatch: bool = True
    action_retrigger: bool = True
    discovered_fields: list[str] = Field(default_factory=list)
    view_id: Optional[str] = None  # optional custom id; auto-slug from name if omitted


class ObservabilityViewUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    table_name: Optional[str] = None
    account: Optional[str] = None
    selected_fields: Optional[list[str]] = None
    action_view_pdf: Optional[bool] = None
    action_cloudwatch: Optional[bool] = None
    action_retrigger: Optional[bool] = None
    discovered_fields: Optional[list[str]] = None

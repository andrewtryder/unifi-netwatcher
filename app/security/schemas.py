"""Pydantic schemas for security API responses."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CidrPreviewResponse(BaseModel):
    valid: bool
    normalized_cidrs: list[str] = Field(default_factory=list)
    effective_client_ip: str
    client_will_remain_allowed: bool
    errors: list[str] = Field(default_factory=list)


class HostsPreviewResponse(BaseModel):
    valid: bool
    normalized_hosts: list[str] = Field(default_factory=list)
    effective_request_host: str
    client_will_remain_allowed: bool
    errors: list[str] = Field(default_factory=list)

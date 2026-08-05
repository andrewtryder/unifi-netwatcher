from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, field_validator


class RenameRequest(BaseModel):
    display_name: Annotated[str, Field(min_length=1, max_length=128)]

    @field_validator("display_name", mode="before")
    @classmethod
    def _strip_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class NotesRequest(BaseModel):
    notes: Annotated[str, Field(max_length=4000)]

    @field_validator("notes", mode="before")
    @classmethod
    def _strip_notes(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value


class BulkDeviceIdsRequest(BaseModel):
    device_ids: Annotated[list[int], Field(min_length=1, max_length=500)]

    @field_validator("device_ids", mode="after")
    @classmethod
    def _positive_unique(cls, values: list[int]) -> list[int]:
        cleaned: list[int] = []
        seen: set[int] = set()
        for item in values:
            if item < 1:
                raise ValueError("device_ids must be positive integers")
            if item in seen:
                continue
            seen.add(item)
            cleaned.append(item)
        if not cleaned:
            raise ValueError("device_ids must contain at least one valid id")
        return cleaned

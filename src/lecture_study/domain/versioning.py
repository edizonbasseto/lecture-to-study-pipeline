"""Shared strict schema-version primitives for persisted domain contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BeforeValidator, Field


def _require_schema_version_one(value: object) -> object:
    if type(value) is not int or value != 1:
        raise ValueError("schema_version must be the integer 1")
    return value


SchemaVersionOne = Annotated[Literal[1], BeforeValidator(_require_schema_version_one)]
PersistedSequenceNumber = Annotated[int, Field(ge=1, le=(1 << 63) - 1)]

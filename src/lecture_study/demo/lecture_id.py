"""Validation of the lecture identifier the demo tooling splices into paths."""

from __future__ import annotations

from pydantic import TypeAdapter, ValidationError

from lecture_study.domain.models import SafeIdentifier

__all__ = ["parse_lecture_id"]

_LECTURE_ID = TypeAdapter(SafeIdentifier)


def parse_lecture_id(value: str) -> str:
    """The lecture id, or ValueError when it is not a safe filename slug."""
    try:
        return _LECTURE_ID.validate_python(value)
    except ValidationError as error:
        raise ValueError(f"unsafe lecture id {value!r}") from error

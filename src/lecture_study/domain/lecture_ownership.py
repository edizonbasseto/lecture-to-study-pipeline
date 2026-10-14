"""Ownership check for per-lecture artifacts read from a lecture directory.

A script clears its remote-egress decision against the lecture id on the
command line. The artifacts it then sends to a model are read from
directories keyed by that id, so a misfiled or overridden tree could carry
another lecture's content. This check ties the loaded content back to the id
that was cleared. Its error names identifiers only, never artifact content.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol


class LectureOwnershipError(ValueError):
    """Artifacts filed under one lecture identify another lecture."""


class HasLectureId(Protocol):
    @property
    def lecture_id(self) -> str: ...


def require_lecture_ownership[T: HasLectureId](
    expected_lecture_id: str, artifacts: Iterable[T], *, kind: str
) -> tuple[T, ...]:
    """Return ``artifacts`` when every one carries ``expected_lecture_id``."""

    loaded = tuple(artifacts)
    foreign_ids = sorted(
        {item.lecture_id for item in loaded if item.lecture_id != expected_lecture_id}
    )
    if foreign_ids:
        raise LectureOwnershipError(
            f"{kind} filed under lecture {expected_lecture_id!r} belong to other "
            f"lectures: {', '.join(repr(foreign_id) for foreign_id in foreign_ids)}"
        )
    return loaded

"""Loaded artifacts must belong to the lecture whose egress was cleared."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from lecture_study.domain.lecture_ownership import (
    LectureOwnershipError,
    require_lecture_ownership,
)


@dataclass(frozen=True)
class _Artifact:
    lecture_id: str
    secret_text: str = "ARTIFACT-CONTENT-MARKER"


def test_artifacts_of_the_expected_lecture_are_returned_unchanged() -> None:
    artifacts = [_Artifact("925101"), _Artifact("925101")]

    assert require_lecture_ownership("925101", artifacts, kind="packs") == tuple(
        artifacts
    )


def test_no_artifacts_pass() -> None:
    assert require_lecture_ownership("925101", [], kind="packs") == ()


def test_a_foreign_artifact_is_refused_naming_only_identifiers() -> None:
    artifacts = [_Artifact("925101"), _Artifact("925102"), _Artifact("925102")]

    with pytest.raises(LectureOwnershipError) as raised:
        require_lecture_ownership("925101", artifacts, kind="evidence packs")

    message = str(raised.value)
    assert "evidence packs" in message
    assert "'925101'" in message
    assert message.count("'925102'") == 1
    assert "ARTIFACT-CONTENT-MARKER" not in message

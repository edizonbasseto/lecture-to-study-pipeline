from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from lecture_study.quality.certification import (
    REQUIRED_QUALITY_GATES,
    QualityCertificationError,
    QualityGateName,
)
from lecture_study.quality.issuer import (
    CertificationAttempt,
    GateExecutionBlocker,
    QualityCertificateIssuer,
)


def test_issuer_accepts_only_lecture_identity_at_execution_boundary(
    tmp_path: Path,
) -> None:
    issuer = QualityCertificateIssuer(attempts_root=tmp_path)

    parameters = inspect.signature(issuer.issue).parameters

    assert tuple(parameters) == ("lecture_id",)


def test_issuer_executes_exact_registry_and_fails_closed(tmp_path: Path) -> None:
    issuer = QualityCertificateIssuer(attempts_root=tmp_path)

    with pytest.raises(QualityCertificationError, match="fail-closed"):
        issuer.issue("lecture-1")

    attempt_path = tmp_path / "lecture-1" / "latest_attempt.json"
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    observed_gates = {item["gate_name"] for item in attempt["blockers"]}
    blockers_by_gate = {
        item["gate_name"]: item["reason_code"] for item in attempt["blockers"]
    }

    assert observed_gates == {gate.value for gate in REQUIRED_QUALITY_GATES}
    assert len(attempt["blockers"]) == len(REQUIRED_QUALITY_GATES)
    assert (
        blockers_by_gate[QualityGateName.TIMELINE_CLASSIFICATION.value]
        == "source-universe-evidence-not-integrated"
    )
    assert (
        blockers_by_gate[QualityGateName.LEARNING_UNIT_COMPLETENESS.value]
        == "source-universe-evidence-not-integrated"
    )
    assert not (tmp_path / "lecture-1" / "certificate.json").exists()
    assert not (tmp_path / "lecture-1" / "reports").exists()


def test_issuer_rejects_unsafe_lecture_identity_before_writing(tmp_path: Path) -> None:
    issuer = QualityCertificateIssuer(attempts_root=tmp_path)

    with pytest.raises(ValueError):
        issuer.issue("../escape")

    assert list(tmp_path.iterdir()) == []


def test_issuer_replaces_diagnostic_attempt_atomically(tmp_path: Path) -> None:
    issuer = QualityCertificateIssuer(attempts_root=tmp_path)

    for _ in range(2):
        with pytest.raises(QualityCertificationError):
            issuer.issue("lecture-1")

    lecture_dir = tmp_path / "lecture-1"
    assert (lecture_dir / "latest_attempt.json").is_file()
    assert not (lecture_dir / ".latest_attempt.json.tmp").exists()


def test_certification_attempt_rejects_boolean_schema_version() -> None:
    attempt = QualityCertificateIssuer._execute_gate_registry("lecture-1")
    data = attempt.model_dump(mode="python")
    data["schema_version"] = True

    with pytest.raises(ValidationError, match="schema_version"):
        CertificationAttempt.model_validate(data)


@pytest.mark.parametrize(
    "lecture_id",
    ["lecture-1", "another-lecture-2", "aula-10002"],
)
def test_issue_never_returns_for_any_lecture_id(
    tmp_path: Path, lecture_id: str
) -> None:
    """ADR 0024 regression lock: no code path may make
    QualityCertificateIssuer.issue() return instead of raise. This holds
    for every lecture_id, not only the one exercised elsewhere in this
    file -- a future approval branch introduced for one identity, and
    left in place for others, would still be caught here."""
    issuer = QualityCertificateIssuer(attempts_root=tmp_path)

    with pytest.raises(QualityCertificationError):
        issuer.issue(lecture_id)


def test_ready_for_publication_never_evaluates_true_for_any_constructed_attempt() -> (
    None
):
    """ADR 0024 regression lock: CertificationAttempt.ready_for_publication
    must never evaluate true, regardless of the blockers' own content --
    the property is a fixed False, never derived from what the blockers
    say. Exercised against the real registry output for two different
    lectures, and against a hand-constructed attempt whose reason codes
    claim every gate was satisfied, to prove the property does not
    inspect its own blockers to decide the answer."""
    for lecture_id in ("lecture-1", "a-completely-different-lecture-id"):
        attempt = QualityCertificateIssuer._execute_gate_registry(lecture_id)
        assert attempt.ready_for_publication is False

    optimistic_blockers = tuple(
        GateExecutionBlocker(
            schema_version=1,
            gate_name=gate_name,
            reason_code="hypothetically-satisfied",
            detail="Pretend evidence exists for this gate.",
            expected_evidence_relative_path=(
                f"quality_evidence/lecture-1/{gate_name.value}.json"
            ),
        )
        for gate_name in QualityGateName
    )
    optimistic_attempt = CertificationAttempt(
        schema_version=1,
        lecture_id="lecture-1",
        blockers=optimistic_blockers,
    )

    assert optimistic_attempt.ready_for_publication is False

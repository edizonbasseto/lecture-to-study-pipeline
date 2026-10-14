from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from lecture_study.domain.models import (
    MockExam,
    MockQuestion,
    PodcastLine,
    PodcastScript,
)
from lecture_study.quality.certification import QualityCertificateVerifier
from lecture_study.quality.derivative import (
    DerivativeArtifactKind,
    DerivativeExecutionIdentity,
    DerivativeExecutionIdentityResolver,
    DerivativeGateReport,
    DerivativeQualityCertificate,
    DerivativeQualityCertificateVerifier,
    DerivativeQualityError,
    DerivativeQualityGate,
    DerivativeQualityIssuer,
    GenerationReceipt,
    UncertifiedMockExamCandidate,
    UncertifiedPodcastScriptCandidate,
)
from lecture_study.quality.trust import (
    HmacQualityTrust,
    canonical_model_bytes,
)

_LECTURE_ID = "lecture-1"


@dataclass(frozen=True, slots=True)
class _VerifierFixture:
    certificates_root: Path
    canonical_documents_root: Path
    metadata_root: Path
    trust: HmacQualityTrust
    exam: MockExam
    execution_identity: DerivativeExecutionIdentity
    quality_certificate_verifier: QualityCertificateVerifier

    def verifier(
        self,
        identity_resolver: DerivativeExecutionIdentityResolver | None = None,
    ) -> DerivativeQualityCertificateVerifier:
        return DerivativeQualityCertificateVerifier(
            certificates_root=self.certificates_root,
            canonical_documents_root=self.canonical_documents_root,
            metadata_root=self.metadata_root,
            trust=self.trust,
            execution_identity_resolver=identity_resolver,
            quality_certificate_verifier=self.quality_certificate_verifier,
        )


def _digest(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def _write_bytes(path: Path, file_bytes: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(file_bytes)


def _execution_identity() -> DerivativeExecutionIdentity:
    return DerivativeExecutionIdentity(
        generator_sha256="4" * 64,
        prompt_sha256="5" * 64,
        model_sha256="6" * 64,
        schema_sha256="7" * 64,
    )


_RECEIPT_TRUST = HmacQualityTrust(b"unit-test-only-quality-trust-material")
_SOURCE_CERTIFICATE_DIGEST = "8" * 64
_CANONICAL_DOCUMENT_DIGEST = "9" * 64


def _source_verifier(
    monkeypatch: pytest.MonkeyPatch,
    *,
    source_certificate_sha256: str = _SOURCE_CERTIFICATE_DIGEST,
    canonical_document_sha256: str = _CANONICAL_DOCUMENT_DIGEST,
) -> QualityCertificateVerifier:
    verifier = QualityCertificateVerifier(trust=_RECEIPT_TRUST)

    def verify(lecture_id: str) -> SimpleNamespace:
        assert lecture_id == _LECTURE_ID
        return SimpleNamespace(
            certificate=SimpleNamespace(
                canonical_document_sha256=canonical_document_sha256
            ),
            certificate_sha256=source_certificate_sha256,
        )

    monkeypatch.setattr(verifier, "verify", verify)
    return verifier


def _receipt(
    payload: MockExam | PodcastScript,
    *,
    artifact_kind: DerivativeArtifactKind = DerivativeArtifactKind.MOCK_EXAM,
    lecture_id: str = _LECTURE_ID,
    execution_identity: DerivativeExecutionIdentity | None = None,
    source_certificate_sha256: str = _SOURCE_CERTIFICATE_DIGEST,
    canonical_document_sha256: str = _CANONICAL_DOCUMENT_DIGEST,
) -> GenerationReceipt:
    return GenerationReceipt.issue(
        artifact_kind=artifact_kind,
        lecture_id=lecture_id,
        payload=payload,
        execution_identity=execution_identity or _execution_identity(),
        source_certificate_sha256=source_certificate_sha256,
        canonical_document_sha256=canonical_document_sha256,
        trust=_RECEIPT_TRUST,
    )


def _issuer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    live_identity: DerivativeExecutionIdentity | None = None,
    source_certificate_sha256: str = _SOURCE_CERTIFICATE_DIGEST,
) -> DerivativeQualityIssuer:
    identity = live_identity or _execution_identity()
    return DerivativeQualityIssuer(
        certificates_root=tmp_path,
        trust=_RECEIPT_TRUST,
        execution_identity_resolver=lambda _artifact_kind: identity,
        quality_certificate_verifier=_source_verifier(
            monkeypatch,
            source_certificate_sha256=source_certificate_sha256,
        ),
    )


def _certificate(
    trust: HmacQualityTrust,
    execution_identity: DerivativeExecutionIdentity | None = None,
) -> DerivativeQualityCertificate:
    identity = execution_identity or _execution_identity()
    certificate = DerivativeQualityCertificate(
        schema_version=1,
        lecture_id=_LECTURE_ID,
        artifact_kind=DerivativeArtifactKind.MOCK_EXAM,
        status="approved",
        payload_sha256="1" * 64,
        parent_certificate_sha256="2" * 64,
        canonical_document_sha256="3" * 64,
        generator_sha256=identity.generator_sha256,
        prompt_sha256=identity.prompt_sha256,
        model_sha256=identity.model_sha256,
        schema_sha256=identity.schema_sha256,
        gate_report_sha256={gate: "8" * 64 for gate in DerivativeQualityGate},
        gate_evaluator_artifact_sha256={
            gate: "9" * 64 for gate in DerivativeQualityGate
        },
        trust_key_id=trust.key_id,
        signature_sha256="0" * 64,
    )
    return certificate.model_copy(
        update={"signature_sha256": trust.sign(certificate.authentication_payload())}
    )


def _write_gate_reports(
    fixture: _VerifierFixture,
    parent_certificate_sha256: str,
    payload_sha256: str,
) -> dict[DerivativeQualityGate, str]:
    report_digests: dict[DerivativeQualityGate, str] = {}
    for gate_name in DerivativeQualityGate:
        report = DerivativeGateReport(
            schema_version=1,
            lecture_id=_LECTURE_ID,
            artifact_kind=DerivativeArtifactKind.MOCK_EXAM,
            gate_name=gate_name,
            status="approved",
            payload_sha256=payload_sha256,
            parent_certificate_sha256=parent_certificate_sha256,
            evaluator_artifact_sha256="9" * 64,
            findings=(),
        )
        report_bytes = report.model_dump_json().encode()
        report_path = (
            fixture.certificates_root
            / _LECTURE_ID
            / "derivatives"
            / "mock-exam"
            / "reports"
            / f"{gate_name.value}.json"
        )
        _write_bytes(report_path, report_bytes)
        report_digests[gate_name] = _digest(report_bytes)
    return report_digests


def _write_derivative_chain(fixture: _VerifierFixture) -> None:
    source_certificate_path = (
        fixture.certificates_root / _LECTURE_ID / "certificate.json"
    )
    source_certificate_bytes = b"source-certificate-placeholder"
    _write_bytes(source_certificate_path, source_certificate_bytes)
    parent_digest = _digest(source_certificate_bytes)
    payload_digest = _digest(canonical_model_bytes(fixture.exam))
    report_digests = _write_gate_reports(fixture, parent_digest, payload_digest)
    unsigned = _certificate(fixture.trust, fixture.execution_identity).model_copy(
        update={
            "payload_sha256": payload_digest,
            "parent_certificate_sha256": parent_digest,
            "gate_report_sha256": report_digests,
            "signature_sha256": "0" * 64,
        }
    )
    certificate = unsigned.model_copy(
        update={
            "signature_sha256": fixture.trust.sign(unsigned.authentication_payload())
        }
    )
    certificate_path = (
        fixture.certificates_root
        / _LECTURE_ID
        / "derivatives"
        / "mock-exam"
        / "certificate.json"
    )
    _write_bytes(certificate_path, certificate.model_dump_json().encode())


def _build_verifier_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> _VerifierFixture:
    certificates_root = tmp_path / "certificates"
    canonical_documents_root = tmp_path / "canonical"
    metadata_root = tmp_path / "metadata"
    trust = HmacQualityTrust(secrets.token_bytes(32))
    source_verifier = QualityCertificateVerifier(
        certificates_root=certificates_root,
        canonical_documents_root=canonical_documents_root,
        metadata_root=metadata_root,
        trust=trust,
    )
    fixture = _VerifierFixture(
        certificates_root=certificates_root,
        canonical_documents_root=canonical_documents_root,
        metadata_root=metadata_root,
        trust=trust,
        exam=MockExam(lecture_id=_LECTURE_ID, questions=[]),
        execution_identity=_execution_identity(),
        quality_certificate_verifier=source_verifier,
    )
    _write_derivative_chain(fixture)

    def verify_source(lecture_id: str) -> SimpleNamespace:
        assert lecture_id == _LECTURE_ID
        return SimpleNamespace(
            certificate=SimpleNamespace(canonical_document_sha256="3" * 64),
            certificate_sha256=hashlib.sha256(b"source-certificate").hexdigest(),
        )

    monkeypatch.setattr(source_verifier, "verify", verify_source)
    return fixture


def test_derivative_certificate_authenticates_exact_payload_identity() -> None:
    trust = HmacQualityTrust(secrets.token_bytes(32))
    certificate = _certificate(trust)

    certificate.require_authenticated(trust)


def test_derivative_certificate_rejects_coordinated_field_tamper() -> None:
    trust = HmacQualityTrust(secrets.token_bytes(32))
    certificate = _certificate(trust).model_copy(update={"payload_sha256": "a" * 64})

    with pytest.raises(DerivativeQualityError, match="authentication failed"):
        certificate.require_authenticated(trust)


def test_derivative_certificate_requires_exact_quality_gate_set() -> None:
    trust = HmacQualityTrust(secrets.token_bytes(32))

    with pytest.raises(ValidationError, match="report gate set mismatch"):
        DerivativeQualityCertificate(
            schema_version=1,
            lecture_id=_LECTURE_ID,
            artifact_kind=DerivativeArtifactKind.MOCK_EXAM,
            status="approved",
            payload_sha256="1" * 64,
            parent_certificate_sha256="2" * 64,
            canonical_document_sha256="3" * 64,
            generator_sha256="4" * 64,
            prompt_sha256="5" * 64,
            model_sha256="6" * 64,
            schema_sha256="7" * 64,
            gate_report_sha256={},
            gate_evaluator_artifact_sha256={
                gate: "9" * 64 for gate in DerivativeQualityGate
            },
            trust_key_id=trust.key_id,
            signature_sha256="0" * 64,
        )


def test_derivative_verifier_fails_closed_without_source_certificate(
    tmp_path: Path,
) -> None:
    verifier = DerivativeQualityCertificateVerifier(
        certificates_root=tmp_path / "certificates",
        canonical_documents_root=tmp_path / "canonical",
        metadata_root=tmp_path / "metadata",
        trust=HmacQualityTrust(secrets.token_bytes(32)),
    )
    exam = MockExam(lecture_id=_LECTURE_ID, questions=[])

    with pytest.raises(DerivativeQualityError, match="source publication"):
        verifier.verify_exam(exam)


def test_derivative_verifier_accepts_exact_live_execution_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_verifier_fixture(tmp_path, monkeypatch)
    resolved_kinds: list[DerivativeArtifactKind] = []

    def resolve_identity(
        artifact_kind: DerivativeArtifactKind,
    ) -> DerivativeExecutionIdentity:
        resolved_kinds.append(artifact_kind)
        return fixture.execution_identity

    certificate = fixture.verifier(resolve_identity).verify_exam(fixture.exam)

    assert certificate.execution_identity() == fixture.execution_identity
    assert resolved_kinds == [DerivativeArtifactKind.MOCK_EXAM]


def test_derivative_verifier_fails_closed_without_execution_identity_resolver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_verifier_fixture(tmp_path, monkeypatch)

    with pytest.raises(DerivativeQualityError, match="resolver is not configured"):
        fixture.verifier().verify_exam(fixture.exam)


@pytest.mark.parametrize(
    "changed_field",
    (
        "generator_sha256",
        "prompt_sha256",
        "model_sha256",
        "schema_sha256",
    ),
)
def test_derivative_verifier_rejects_authenticated_stale_execution_identity(
    changed_field: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_verifier_fixture(tmp_path, monkeypatch)
    live_identity = fixture.execution_identity.model_copy(
        update={changed_field: "a" * 64}
    )

    with pytest.raises(DerivativeQualityError, match="execution identity changed"):
        fixture.verifier(lambda _artifact_kind: live_identity).verify_exam(fixture.exam)


def test_uncertified_candidates_are_distinct_frozen_receipt_snapshots() -> None:
    exam_payload = MockExam(lecture_id=_LECTURE_ID, questions=[])
    script_payload = PodcastScript(
        lecture_id=_LECTURE_ID,
        title="Clinical review",
        dialogue=[
            PodcastLine(
                speaker="Host",
                text="Introduction.",
                source_unit_id="unit-001",
            )
        ],
    )
    exam_candidate = UncertifiedMockExamCandidate(
        payload=exam_payload,
        receipt=_receipt(exam_payload),
    )
    script_candidate = UncertifiedPodcastScriptCandidate(
        payload=script_payload,
        receipt=_receipt(
            script_payload,
            artifact_kind=DerivativeArtifactKind.PODCAST_SCRIPT,
        ),
    )

    assert type(exam_candidate) is not type(script_candidate)
    with pytest.raises(FrozenInstanceError):
        setattr(exam_candidate, "receipt", exam_candidate.receipt)
    with pytest.raises(FrozenInstanceError):
        setattr(script_candidate, "receipt", script_candidate.receipt)


def test_derivative_issuer_rejects_publicly_forged_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = MockExam(lecture_id=_LECTURE_ID, questions=[])
    forged = _receipt(payload).model_copy(update={"signature_sha256": "a" * 64})
    candidate = UncertifiedMockExamCandidate(payload=payload, receipt=forged)

    with pytest.raises(DerivativeQualityError, match="authentication failed"):
        _issuer(tmp_path, monkeypatch).evaluate_exam(candidate)

    assert not (tmp_path / _LECTURE_ID).exists()


def test_derivative_issuer_rejects_payload_mutated_after_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = MockExam(lecture_id=_LECTURE_ID, questions=[])
    candidate = UncertifiedMockExamCandidate(
        payload=payload,
        receipt=_receipt(payload),
    )
    candidate.payload.lecture_id = "lecture-2"

    with pytest.raises(DerivativeQualityError, match="does not match"):
        _issuer(tmp_path, monkeypatch).evaluate_exam(candidate)

    assert not (tmp_path / "lecture-2").exists()


def test_derivative_issuer_rejects_wrong_kind_and_lecture_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = MockExam(lecture_id=_LECTURE_ID, questions=[])
    wrong_kind = UncertifiedMockExamCandidate(
        payload=payload,
        receipt=_receipt(
            payload,
            artifact_kind=DerivativeArtifactKind.PODCAST_SCRIPT,
        ),
    )
    wrong_lecture = UncertifiedMockExamCandidate(
        payload=payload,
        receipt=_receipt(payload, lecture_id="lecture-2"),
    )
    issuer = _issuer(tmp_path, monkeypatch)

    for candidate in (wrong_kind, wrong_lecture):
        with pytest.raises(DerivativeQualityError, match="does not match"):
            issuer.evaluate_exam(candidate)

    assert not (tmp_path / _LECTURE_ID).exists()


def test_derivative_issuer_rejects_stale_source_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = MockExam(lecture_id=_LECTURE_ID, questions=[])
    candidate = UncertifiedMockExamCandidate(
        payload=payload,
        receipt=_receipt(payload),
    )

    with pytest.raises(DerivativeQualityError, match="source lineage"):
        _issuer(
            tmp_path,
            monkeypatch,
            source_certificate_sha256="a" * 64,
        ).evaluate_exam(candidate)

    assert not (tmp_path / _LECTURE_ID).exists()


def test_derivative_issuer_fails_closed_without_execution_identity_resolver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = MockExam(lecture_id=_LECTURE_ID, questions=[])
    candidate = UncertifiedMockExamCandidate(
        payload=payload,
        receipt=_receipt(payload),
    )
    issuer = DerivativeQualityIssuer(
        certificates_root=tmp_path,
        trust=_RECEIPT_TRUST,
        quality_certificate_verifier=_source_verifier(monkeypatch),
    )

    with pytest.raises(DerivativeQualityError, match="resolver is not configured"):
        issuer.evaluate_exam(candidate)

    assert not (tmp_path / _LECTURE_ID).exists()


def test_derivative_issuer_rejects_candidate_with_stale_execution_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = MockExam(lecture_id=_LECTURE_ID, questions=[])
    captured_identity = _execution_identity()
    live_identity = captured_identity.model_copy(update={"model_sha256": "a" * 64})
    candidate = UncertifiedMockExamCandidate(
        payload=payload,
        receipt=_receipt(payload, execution_identity=captured_identity),
    )

    with pytest.raises(DerivativeQualityError, match="identity changed"):
        _issuer(
            tmp_path,
            monkeypatch,
            live_identity=live_identity,
        ).evaluate_exam(candidate)

    assert not (tmp_path / _LECTURE_ID).exists()


def test_derivative_issuer_executes_all_critics_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = MockExam(lecture_id=_LECTURE_ID, questions=[])
    candidate = UncertifiedMockExamCandidate(
        payload=payload,
        receipt=_receipt(payload),
    )

    with pytest.raises(DerivativeQualityError, match="fail-closed"):
        _issuer(tmp_path, monkeypatch).evaluate_exam(candidate)

    artifact_dir = tmp_path / _LECTURE_ID / "derivatives" / "mock-exam"
    attempt = json.loads(
        (artifact_dir / "latest_attempt.json").read_text(encoding="utf-8")
    )
    assert {item["gate_name"] for item in attempt["blockers"]} == {
        gate.value for gate in DerivativeQualityGate
    }
    assert not (artifact_dir / "certificate.json").exists()
    assert not (artifact_dir / "reports").exists()


def _podcast_payload(
    lecture_id: str = _LECTURE_ID,
    title: str = "Título do episódio",
) -> PodcastScript:
    return PodcastScript(
        lecture_id=lecture_id,
        title=title,
        dialogue=[
            PodcastLine(speaker="Host", text="Olá, bem-vindos.", source_unit_id="u-1")
        ],
    )


@pytest.mark.parametrize(
    "questions",
    [
        [],
        [
            {
                "question_text": "Uma pergunta clínica qualquer.",
                "options": {"A": "a", "B": "b", "C": "c", "D": "d"},
                "correct_answer": "A",
                "explanation": "Uma explicação qualquer.",
            }
        ],
    ],
)
def test_evaluate_exam_never_returns_for_any_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    questions: list[dict[str, object]],
) -> None:
    """ADR 0024 regression lock: no code path may make
    DerivativeQualityIssuer.evaluate_exam() succeed without a real
    derivative certificate -- exercised over an empty and a populated
    question set, since the fail-closed behavior must not depend on the
    payload's own content."""
    payload = MockExam(
        lecture_id=_LECTURE_ID,
        questions=[MockQuestion(**question) for question in questions],
    )
    candidate = UncertifiedMockExamCandidate(payload=payload, receipt=_receipt(payload))

    with pytest.raises(DerivativeQualityError, match="fail-closed"):
        _issuer(tmp_path, monkeypatch).evaluate_exam(candidate)


@pytest.mark.parametrize(
    "title",
    ["Título do episódio", "Um título completamente diferente"],
)
def test_evaluate_podcast_script_never_returns_for_any_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    title: str,
) -> None:
    """ADR 0024 regression lock: the equivalent guarantee for
    DerivativeQualityIssuer.evaluate_podcast_script() -- fail-closed
    regardless of the script's own title or content."""
    payload = _podcast_payload(title=title)
    candidate = UncertifiedPodcastScriptCandidate(
        payload=payload,
        receipt=_receipt(payload, artifact_kind=DerivativeArtifactKind.PODCAST_SCRIPT),
    )

    with pytest.raises(DerivativeQualityError, match="fail-closed"):
        _issuer(tmp_path, monkeypatch).evaluate_podcast_script(candidate)

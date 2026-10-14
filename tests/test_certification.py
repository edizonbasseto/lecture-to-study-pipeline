from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from lecture_study.domain.models import (
    CanonicalLectureDocument,
    CriticReview,
    LearningUnitDraft,
    LectureGlobalIndex,
    ValidatedLearningUnit,
)
from lecture_study.quality import (
    PublicationQualityCertificate,
    PublicationRuntimeIdentity,
    QualityCertificateVerifier,
    QualityCertificationError,
    QualityGateName,
    QualityGateReport,
)

from lecture_study.quality.trust import HmacQualityTrust

_LECTURE_ID = "lecture-001"
_SOURCE_MANIFEST_DIGEST = "a" * 64
_POLICY_MANIFEST_DIGEST = "b" * 64
_IMPLEMENTATION_DIGEST = "c" * 64
_EVALUATOR_DIGEST = "d" * 64


@dataclass(frozen=True, slots=True)
class _CertificateFixture:
    verifier: QualityCertificateVerifier
    trust: HmacQualityTrust
    certificates_root: Path
    canonical_root: Path
    metadata_root: Path
    certificate_path: Path
    canonical_path: Path
    global_index_path: Path
    reports_dir: Path


@dataclass(frozen=True, slots=True)
class _StaticPublicationRuntimeIdentityResolver:
    identity: PublicationRuntimeIdentity

    def resolve(self, lecture_id: str) -> PublicationRuntimeIdentity:
        assert lecture_id == _LECTURE_ID
        return self.identity


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_document(
    lecture_id: str,
    *,
    empty: bool = False,
) -> CanonicalLectureDocument:
    units = (
        []
        if empty
        else [
            ValidatedLearningUnit(
                lecture_id=lecture_id,
                pack_sequence=1,
                content=LearningUnitDraft(
                    lecture_id=lecture_id,
                    pack_sequence=1,
                    title="Clinical foundation",
                    body_markdown="Complete grounded clinical content.",
                    clinical_warnings=[],
                ),
                review=CriticReview(
                    passed=True,
                    hallucination_score=0.0,
                    feedback="Grounded.",
                ),
            )
        ]
    )
    return CanonicalLectureDocument(
        lecture_id=lecture_id,
        total_units=len(units),
        units=units,
        assembled_at=1.0,
    )


def _build_certificate_fixture(
    tmp_path: Path,
    *,
    document_lecture_id: str = _LECTURE_ID,
    index_lecture_id: str = _LECTURE_ID,
    empty_document: bool = False,
) -> _CertificateFixture:
    certificates_root = tmp_path / "quality-certificates"
    canonical_root = tmp_path / "canonical"
    metadata_root = tmp_path / "metadata"
    canonical_path = canonical_root / _LECTURE_ID / "canonical_document.json"
    global_index_path = metadata_root / _LECTURE_ID / "global_index.json"
    certificate_dir = certificates_root / _LECTURE_ID
    reports_dir = certificate_dir / "reports"
    certificate_path = certificate_dir / "certificate.json"

    document = _canonical_document(
        document_lecture_id,
        empty=empty_document,
    )
    global_index = LectureGlobalIndex(
        lecture_id=index_lecture_id,
        glossary_terms=[],
        dosages=[],
        red_flags=[],
    )
    canonical_bytes = document.model_dump_json().encode()
    global_index_bytes = global_index.model_dump_json().encode()
    _write_bytes(canonical_path, canonical_bytes)
    _write_bytes(global_index_path, global_index_bytes)

    canonical_digest = _digest(canonical_bytes)
    global_index_digest = _digest(global_index_bytes)
    report_digests: dict[QualityGateName, str] = {}
    for gate_name in QualityGateName:
        report = QualityGateReport(
            schema_version=1,
            lecture_id=_LECTURE_ID,
            gate_name=gate_name,
            passed=True,
            calibrated=True,
            canonical_document_sha256=canonical_digest,
            global_index_sha256=global_index_digest,
            source_manifest_sha256=_SOURCE_MANIFEST_DIGEST,
            policy_manifest_sha256=_POLICY_MANIFEST_DIGEST,
            implementation_sha256=_IMPLEMENTATION_DIGEST,
            evaluator_artifact_sha256=_EVALUATOR_DIGEST,
            findings=[],
        )
        report_bytes = report.model_dump_json().encode()
        _write_bytes(reports_dir / f"{gate_name.value}.json", report_bytes)
        report_digests[gate_name] = _digest(report_bytes)

    trust = HmacQualityTrust(secrets.token_bytes(32))
    certificate = PublicationQualityCertificate(
        schema_version=1,
        lecture_id=_LECTURE_ID,
        status="approved",
        canonical_document_sha256=canonical_digest,
        global_index_sha256=global_index_digest,
        source_manifest_sha256=_SOURCE_MANIFEST_DIGEST,
        policy_manifest_sha256=_POLICY_MANIFEST_DIGEST,
        implementation_sha256=_IMPLEMENTATION_DIGEST,
        gate_report_sha256=report_digests,
        gate_evaluator_artifact_sha256={
            gate_name: _EVALUATOR_DIGEST for gate_name in QualityGateName
        },
        trust_key_id=trust.key_id,
        signature_sha256="0" * 64,
    )
    certificate = certificate.model_copy(
        update={"signature_sha256": trust.sign(certificate.authentication_payload())}
    )
    _write_bytes(certificate_path, certificate.model_dump_json().encode())
    return _CertificateFixture(
        verifier=QualityCertificateVerifier(
            certificates_root=certificates_root,
            canonical_documents_root=canonical_root,
            metadata_root=metadata_root,
            trust=trust,
            runtime_identity_resolver=_StaticPublicationRuntimeIdentityResolver(
                PublicationRuntimeIdentity(
                    source_manifest_sha256=_SOURCE_MANIFEST_DIGEST,
                    policy_manifest_sha256=_POLICY_MANIFEST_DIGEST,
                    implementation_sha256=_IMPLEMENTATION_DIGEST,
                    gate_evaluator_artifact_sha256={
                        gate_name: _EVALUATOR_DIGEST for gate_name in QualityGateName
                    },
                )
            ),
        ),
        trust=trust,
        certificates_root=certificates_root,
        canonical_root=canonical_root,
        metadata_root=metadata_root,
        certificate_path=certificate_path,
        canonical_path=canonical_path,
        global_index_path=global_index_path,
        reports_dir=reports_dir,
    )


def test_certificate_verifier_returns_inputs_parsed_from_certified_bytes(
    tmp_path: Path,
) -> None:
    fixture = _build_certificate_fixture(tmp_path)

    verified = fixture.verifier.verify(_LECTURE_ID)

    assert verified.document.lecture_id == _LECTURE_ID
    assert verified.global_index.lecture_id == _LECTURE_ID
    assert verified.certificate.status == "approved"
    assert (
        verified.certificate_sha256
        == hashlib.sha256(fixture.certificate_path.read_bytes()).hexdigest()
    )
    assert fixture.verifier.is_certified(_LECTURE_ID) is True


def test_certificate_verifier_rejects_empty_canonical_document(
    tmp_path: Path,
) -> None:
    fixture = _build_certificate_fixture(tmp_path, empty_document=True)

    with pytest.raises(QualityCertificationError, match="no validated learning units"):
        fixture.verifier.verify(_LECTURE_ID)


def test_certificate_verifier_rejects_missing_gate_report(tmp_path: Path) -> None:
    fixture = _build_certificate_fixture(tmp_path)
    (fixture.reports_dir / "clinical-safety.json").unlink()

    with pytest.raises(QualityCertificationError, match="missing"):
        fixture.verifier.verify(_LECTURE_ID)


def test_certificate_verifier_rejects_canonical_document_changed_after_approval(
    tmp_path: Path,
) -> None:
    fixture = _build_certificate_fixture(tmp_path)
    fixture.canonical_path.write_text(
        fixture.canonical_path.read_text() + "\n",
        encoding="utf-8",
    )

    with pytest.raises(QualityCertificationError, match="changed after"):
        fixture.verifier.verify(_LECTURE_ID)
    assert fixture.verifier.is_certified(_LECTURE_ID) is False


def test_certificate_verifier_rejects_coordinated_gate_and_certificate_tamper(
    tmp_path: Path,
) -> None:
    fixture = _build_certificate_fixture(tmp_path)
    gate_name = QualityGateName.CLINICAL_SAFETY
    report_path = fixture.reports_dir / f"{gate_name.value}.json"
    failed_report = json.loads(report_path.read_text())
    failed_report["passed"] = False
    failed_bytes = json.dumps(failed_report, separators=(",", ":")).encode()
    report_path.write_bytes(failed_bytes)

    certificate_data = json.loads(fixture.certificate_path.read_text())
    certificate_data["gate_report_sha256"][gate_name.value] = _digest(failed_bytes)
    fixture.certificate_path.write_text(
        json.dumps(certificate_data, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(QualityCertificationError, match="authentication failed"):
        fixture.verifier.verify(_LECTURE_ID)


def test_certificate_verifier_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    fixture = _build_certificate_fixture(tmp_path)
    certificate_text = fixture.certificate_path.read_text()
    fixture.certificate_path.write_text(
        certificate_text.replace(
            '"schema_version":1',
            '"schema_version":1,"schema_version":1',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(QualityCertificationError, match="ambiguous JSON"):
        fixture.verifier.verify(_LECTURE_ID)


def test_certificate_verifier_rejects_symlinked_report(tmp_path: Path) -> None:
    fixture = _build_certificate_fixture(tmp_path)
    report_path = fixture.reports_dir / "traceability.json"
    report_copy = fixture.reports_dir / "traceability-copy.json"
    report_copy.write_bytes(report_path.read_bytes())
    report_path.unlink()
    report_path.symlink_to(report_copy.name)

    with pytest.raises(QualityCertificationError, match="safely"):
        fixture.verifier.verify(_LECTURE_ID)


def test_publication_certificate_requires_exact_closed_gate_set() -> None:
    with pytest.raises(ValidationError, match="gate set mismatch"):
        PublicationQualityCertificate(
            schema_version=1,
            lecture_id=_LECTURE_ID,
            status="approved",
            canonical_document_sha256="a" * 64,
            global_index_sha256="b" * 64,
            source_manifest_sha256="c" * 64,
            policy_manifest_sha256="d" * 64,
            implementation_sha256="e" * 64,
            gate_report_sha256={
                QualityGateName.CLINICAL_SAFETY: "f" * 64,
            },
            gate_evaluator_artifact_sha256={
                gate_name: "f" * 64 for gate_name in QualityGateName
            },
            trust_key_id="1" * 64,
            signature_sha256="2" * 64,
        )


def test_certificate_verifier_rejects_canonical_internal_lecture_mismatch(
    tmp_path: Path,
) -> None:
    fixture = _build_certificate_fixture(
        tmp_path,
        document_lecture_id="lecture-002",
    )

    with pytest.raises(QualityCertificationError, match="canonical document"):
        fixture.verifier.verify(_LECTURE_ID)


def test_certificate_verifier_rejects_global_index_internal_lecture_mismatch(
    tmp_path: Path,
) -> None:
    fixture = _build_certificate_fixture(
        tmp_path,
        index_lecture_id="lecture-002",
    )

    with pytest.raises(QualityCertificationError, match="global index"):
        fixture.verifier.verify(_LECTURE_ID)


def test_certificate_verifier_rejects_exponent_overflow(tmp_path: Path) -> None:
    fixture = _build_certificate_fixture(tmp_path)
    canonical_bytes = fixture.canonical_path.read_bytes().replace(b"1.0", b"1e400", 1)
    fixture.canonical_path.write_bytes(canonical_bytes)

    with pytest.raises(QualityCertificationError, match="ambiguous JSON"):
        fixture.verifier.verify(_LECTURE_ID)


def test_bounded_reader_rejects_declared_size_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_certificate_fixture(tmp_path)
    canonical_size = fixture.canonical_path.stat().st_size

    def fail_if_read_attempted(descriptor: int, maximum_bytes: int) -> bytes:
        raise AssertionError((descriptor, maximum_bytes))

    monkeypatch.setattr(os, "read", fail_if_read_attempted)
    with pytest.raises(QualityCertificationError, match="exceeds"):
        fixture.verifier._read_regular_file(
            fixture.canonical_path,
            fixture.canonical_path.parents[1],
            canonical_size - 1,
        )


def test_certificate_verifier_rejects_intermediate_directory_symlink(
    tmp_path: Path,
) -> None:
    fixture = _build_certificate_fixture(tmp_path)
    real_reports_dir = fixture.reports_dir.with_name("reports-real")
    fixture.reports_dir.rename(real_reports_dir)
    fixture.reports_dir.symlink_to(real_reports_dir.name, target_is_directory=True)

    with pytest.raises(QualityCertificationError, match="safely"):
        fixture.verifier.verify(_LECTURE_ID)


def test_certificate_verifier_rejects_hardlinked_report(tmp_path: Path) -> None:
    fixture = _build_certificate_fixture(tmp_path)
    report_path = fixture.reports_dir / "traceability.json"
    report_hardlink = fixture.reports_dir / "traceability-hardlink.json"
    report_hardlink.hardlink_to(report_path)

    with pytest.raises(QualityCertificationError, match="exactly one hard link"):
        fixture.verifier.verify(_LECTURE_ID)


def test_certificate_verifier_rejects_declared_evaluator_identity_mismatch(
    tmp_path: Path,
) -> None:
    fixture = _build_certificate_fixture(tmp_path)
    gate_name = QualityGateName.CLINICAL_SAFETY
    report_path = fixture.reports_dir / f"{gate_name.value}.json"
    report_data = json.loads(report_path.read_text())
    report_data["evaluator_artifact_sha256"] = "e" * 64
    report_bytes = json.dumps(report_data, separators=(",", ":")).encode()
    report_path.write_bytes(report_bytes)

    certificate_data = json.loads(fixture.certificate_path.read_text())
    certificate_data["gate_report_sha256"][gate_name.value] = _digest(report_bytes)
    certificate = PublicationQualityCertificate.model_validate_json(
        json.dumps(certificate_data)
    )
    certificate = certificate.model_copy(
        update={
            "signature_sha256": fixture.trust.sign(certificate.authentication_payload())
        }
    )
    fixture.certificate_path.write_text(
        certificate.model_dump_json(),
        encoding="utf-8",
    )

    with pytest.raises(QualityCertificationError, match="declared identity"):
        fixture.verifier.verify(_LECTURE_ID)


def test_publication_certificate_requires_exact_evaluator_gate_set() -> None:
    report_digests = {gate_name: "f" * 64 for gate_name in QualityGateName}

    with pytest.raises(ValidationError, match="evaluator artifact gate set mismatch"):
        PublicationQualityCertificate(
            schema_version=1,
            lecture_id=_LECTURE_ID,
            status="approved",
            canonical_document_sha256="a" * 64,
            global_index_sha256="b" * 64,
            source_manifest_sha256="c" * 64,
            policy_manifest_sha256="d" * 64,
            implementation_sha256="e" * 64,
            gate_report_sha256=report_digests,
            gate_evaluator_artifact_sha256={
                QualityGateName.CLINICAL_SAFETY: "f" * 64,
            },
            trust_key_id="1" * 64,
            signature_sha256="2" * 64,
        )


def test_certificate_verifier_fails_closed_without_runtime_identity_resolver(
    tmp_path: Path,
) -> None:
    fixture = _build_certificate_fixture(tmp_path)
    verifier = QualityCertificateVerifier(
        certificates_root=fixture.certificates_root,
        canonical_documents_root=fixture.canonical_root,
        metadata_root=fixture.metadata_root,
        trust=fixture.trust,
    )

    with pytest.raises(
        QualityCertificationError,
        match="runtime identity resolution is not configured",
    ):
        verifier.verify(_LECTURE_ID)


@pytest.mark.parametrize(
    "identity_field",
    [
        "source_manifest_sha256",
        "policy_manifest_sha256",
        "implementation_sha256",
        "gate_evaluator_artifact_sha256",
    ],
)
def test_certificate_verifier_rejects_stale_runtime_identity(
    tmp_path: Path,
    identity_field: str,
) -> None:
    fixture = _build_certificate_fixture(tmp_path)
    identity = PublicationRuntimeIdentity(
        source_manifest_sha256=_SOURCE_MANIFEST_DIGEST,
        policy_manifest_sha256=_POLICY_MANIFEST_DIGEST,
        implementation_sha256=_IMPLEMENTATION_DIGEST,
        gate_evaluator_artifact_sha256={
            gate_name: _EVALUATOR_DIGEST for gate_name in QualityGateName
        },
    )
    if identity_field == "gate_evaluator_artifact_sha256":
        stale_evaluators = dict(identity.gate_evaluator_artifact_sha256)
        stale_evaluators[QualityGateName.SOURCE_MEDIA_INTEGRITY] = "e" * 64
        stale_identity = identity.model_copy(update={identity_field: stale_evaluators})
    else:
        stale_identity = identity.model_copy(update={identity_field: "e" * 64})
    verifier = QualityCertificateVerifier(
        certificates_root=fixture.certificates_root,
        canonical_documents_root=fixture.canonical_root,
        metadata_root=fixture.metadata_root,
        trust=fixture.trust,
        runtime_identity_resolver=_StaticPublicationRuntimeIdentityResolver(
            stale_identity
        ),
    )

    with pytest.raises(
        QualityCertificationError,
        match="runtime identity changed after certification",
    ):
        verifier.verify(_LECTURE_ID)

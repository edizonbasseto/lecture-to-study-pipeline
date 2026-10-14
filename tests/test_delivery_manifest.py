from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import secrets
import stat
import threading
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path

import pytest
from markdown_it import MarkdownIt

import lecture_study.quality.delivery as delivery_module

from lecture_study.domain.models import (
    CanonicalLectureDocument,
    CriticReview,
    LearningUnitDraft,
    LectureGlobalIndex,
    MockExam,
    PodcastLine,
    PodcastScript,
    ValidatedLearningUnit,
)
from lecture_study.quality.certification import (
    PublicationQualityCertificate,
    PublicationRuntimeIdentity,
    QualityCertificateVerifier,
    QualityGateName,
    QualityGateReport,
)
from lecture_study.quality.delivery import (
    AudioRuntimeIdentityResolver,
    DeliveryArtifactKind,
    DeliveryExecutionIdentity,
    DeliveryManifest,
    DeliveryManifestIssuer,
    DeliveryManifestVerifier,
    DeliveryOutput,
    DeliveryVerificationError,
    EXPECTED_OUTPUT_RELATIVE_PATHS,
    RootedArtifactStore,
    VerifiedDelivery,
    deterministic_delivery_execution_identity,
    write_delivery_outputs,
)
from lecture_study.quality.derivative import (
    DerivativeArtifactKind,
    DerivativeExecutionIdentity,
    DerivativeGateReport,
    DerivativeQualityCertificate,
    DerivativeQualityCertificateVerifier,
    DerivativeQualityGate,
)
from lecture_study.quality.trust import (
    HmacQualityTrust,
    canonical_json_bytes,
    canonical_model_bytes,
)

_LECTURE_ID = "lecture-001"
_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_DIGEST_C = "c" * 64
_DIGEST_D = "d" * 64


@dataclass(frozen=True, slots=True)
class _DeliveryFixture:
    verifier: DeliveryManifestVerifier
    issuer: DeliveryManifestIssuer
    identity: DeliveryExecutionIdentity
    manifest_path: Path
    certificate_path: Path
    canonical_path: Path
    global_index_path: Path
    gate_reports_dir: Path
    output_dir: Path
    trust: HmacQualityTrust
    certificates_root: Path
    canonical_root: Path
    metadata_root: Path
    output_roots: Mapping[DeliveryArtifactKind, Path]
    quality_verifier: QualityCertificateVerifier

    def output_bytes(
        self,
        artifact_kind: DeliveryArtifactKind,
    ) -> dict[str, bytes]:
        lecture_root = self.output_roots[artifact_kind] / _LECTURE_ID
        return {
            relative_path: (lecture_root / relative_path).read_bytes()
            for relative_path in EXPECTED_OUTPUT_RELATIVE_PATHS[artifact_kind]
        }


@dataclass(frozen=True, slots=True)
class _StaticPublicationRuntimeIdentityResolver:
    identity: PublicationRuntimeIdentity

    def resolve(self, _lecture_id: str) -> PublicationRuntimeIdentity:
        return self.identity


@dataclass(frozen=True, slots=True)
class _StaticDerivativeIdentityResolver:
    identity: DerivativeExecutionIdentity

    def __call__(
        self,
        _artifact_kind: DerivativeArtifactKind,
        /,
    ) -> DerivativeExecutionIdentity:
        return self.identity


@dataclass(frozen=True, slots=True)
class _StaticAudioRuntimeIdentityResolver:
    identity: DeliveryExecutionIdentity

    def __call__(
        self,
        lecture_id: str,
        artifact_kind: DeliveryArtifactKind,
        /,
    ) -> DeliveryExecutionIdentity:
        if lecture_id != _LECTURE_ID:
            raise DeliveryVerificationError("audio identity lecture context changed")
        if artifact_kind is not DeliveryArtifactKind.PODCAST_AUDIO:
            raise DeliveryVerificationError("audio identity artifact context changed")
        return self.identity


@dataclass(frozen=True, slots=True)
class _DerivativeDeliveryFixture:
    base: _DeliveryFixture
    verifier: DeliveryManifestVerifier
    issuer: DeliveryManifestIssuer
    derivative_verifier: DerivativeQualityCertificateVerifier
    artifact_kind: DeliveryArtifactKind
    payload: MockExam | PodcastScript
    derivative_certificate_path: Path
    payload_path: Path
    audio_identity: DeliveryExecutionIdentity

    def output_bytes(self) -> dict[str, bytes]:
        return self.base.output_bytes(self.artifact_kind)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _build_fixture(tmp_path: Path) -> _DeliveryFixture:
    manifests_root = tmp_path / "delivery-manifests"
    certificates_root = tmp_path / "quality-certificates"
    canonical_root = tmp_path / "canonical"
    metadata_root = tmp_path / "metadata"
    output_roots = {
        kind: tmp_path / "outputs" / kind.value for kind in DeliveryArtifactKind
    }

    canonical_path = canonical_root / _LECTURE_ID / "canonical_document.json"
    unit = ValidatedLearningUnit(
        lecture_id=_LECTURE_ID,
        pack_sequence=1,
        content=LearningUnitDraft(
            lecture_id=_LECTURE_ID,
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
    document = CanonicalLectureDocument(
        lecture_id=_LECTURE_ID,
        total_units=1,
        units=[unit],
        assembled_at=1.0,
    )
    canonical_bytes = document.model_dump_json().encode()
    _write_bytes(canonical_path, canonical_bytes)
    canonical_digest = _digest(canonical_bytes)

    global_index = LectureGlobalIndex(
        lecture_id=_LECTURE_ID,
        glossary_terms=[],
        dosages=[],
        red_flags=[],
    )
    global_index_bytes = global_index.model_dump_json().encode()
    global_index_path = metadata_root / _LECTURE_ID / "global_index.json"
    _write_bytes(global_index_path, global_index_bytes)
    global_index_digest = _digest(global_index_bytes)

    certificate_dir = certificates_root / _LECTURE_ID
    gate_reports_dir = certificate_dir / "reports"
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
            source_manifest_sha256=_DIGEST_B,
            policy_manifest_sha256=_DIGEST_C,
            implementation_sha256=_DIGEST_D,
            evaluator_artifact_sha256=_DIGEST_B,
            findings=[],
        )
        report_bytes = report.model_dump_json().encode()
        _write_bytes(gate_reports_dir / f"{gate_name.value}.json", report_bytes)
        report_digests[gate_name] = _digest(report_bytes)

    trust = HmacQualityTrust(secrets.token_bytes(32))
    certificate = PublicationQualityCertificate(
        schema_version=1,
        lecture_id=_LECTURE_ID,
        status="approved",
        canonical_document_sha256=canonical_digest,
        global_index_sha256=global_index_digest,
        source_manifest_sha256=_DIGEST_B,
        policy_manifest_sha256=_DIGEST_C,
        implementation_sha256=_DIGEST_D,
        gate_report_sha256=report_digests,
        gate_evaluator_artifact_sha256={gate: _DIGEST_B for gate in QualityGateName},
        trust_key_id=trust.key_id,
        signature_sha256="0" * 64,
    )
    certificate = certificate.model_copy(
        update={"signature_sha256": trust.sign(certificate.authentication_payload())}
    )
    certificate_path = certificate_dir / "certificate.json"
    certificate_bytes = certificate.model_dump_json().encode()
    _write_bytes(certificate_path, certificate_bytes)

    output_dir = output_roots[DeliveryArtifactKind.PUBLISHING] / _LECTURE_ID
    output_content = {
        "study_guide.html": b"<main>Complete study guide</main>",
        "study_guide.md": b"# Complete study guide\n",
    }
    for relative_path, content in output_content.items():
        _write_bytes(output_dir / relative_path, content)

    identity = DeliveryExecutionIdentity(
        generator_sha256=_DIGEST_A,
        prompt_sha256=_DIGEST_B,
        model_sha256=_DIGEST_C,
        schema_sha256=_DIGEST_D,
        renderer_sha256=_DIGEST_A,
    )
    manifest = DeliveryManifest(
        schema_version=2,
        lecture_id=_LECTURE_ID,
        artifact_kind=DeliveryArtifactKind.PUBLISHING,
        parent_certificate_sha256=_digest(certificate_bytes),
        canonical_document_sha256=canonical_digest,
        execution_identity=identity,
        outputs=[
            DeliveryOutput(
                relative_path=relative_path,
                sha256=_digest(content),
                byte_size=len(content),
            )
            for relative_path, content in sorted(output_content.items())
        ],
        trust_key_id=trust.key_id,
        signature_sha256="0" * 64,
    )
    manifest = manifest.model_copy(
        update={"signature_sha256": trust.sign(manifest.authentication_payload())}
    )
    manifest_path = manifests_root / _LECTURE_ID / "publishing.json"
    _write_bytes(
        manifest_path,
        manifest.model_dump_json(exclude_none=True).encode(),
    )

    runtime_identity = PublicationRuntimeIdentity(
        source_manifest_sha256=_DIGEST_B,
        policy_manifest_sha256=_DIGEST_C,
        implementation_sha256=_DIGEST_D,
        gate_evaluator_artifact_sha256={gate: _DIGEST_B for gate in QualityGateName},
    )
    quality_verifier = QualityCertificateVerifier(
        certificates_root=certificates_root,
        canonical_documents_root=canonical_root,
        metadata_root=metadata_root,
        trust=trust,
        runtime_identity_resolver=_StaticPublicationRuntimeIdentityResolver(
            runtime_identity
        ),
    )
    return _DeliveryFixture(
        verifier=DeliveryManifestVerifier(
            manifests_root=manifests_root,
            certificates_root=certificates_root,
            canonical_documents_root=canonical_root,
            metadata_root=metadata_root,
            output_roots=output_roots,
            trust=trust,
            quality_certificate_verifier=quality_verifier,
        ),
        issuer=DeliveryManifestIssuer(
            manifests_root=manifests_root,
            certificates_root=certificates_root,
            canonical_documents_root=canonical_root,
            metadata_root=metadata_root,
            output_roots=output_roots,
            trust=trust,
            quality_certificate_verifier=quality_verifier,
        ),
        identity=identity,
        manifest_path=manifest_path,
        certificate_path=certificate_path,
        canonical_path=canonical_path,
        global_index_path=global_index_path,
        gate_reports_dir=gate_reports_dir,
        output_dir=output_dir,
        trust=trust,
        certificates_root=certificates_root,
        canonical_root=canonical_root,
        metadata_root=metadata_root,
        output_roots=output_roots,
        quality_verifier=quality_verifier,
    )


def _mock_exam() -> MockExam:
    return MockExam(lecture_id=_LECTURE_ID, questions=[])


def _podcast_script() -> PodcastScript:
    return PodcastScript(
        lecture_id=_LECTURE_ID,
        title="Clinical review",
        dialogue=[
            PodcastLine(
                speaker="Host",
                text="Clinical introduction.",
                source_unit_id="unit-001",
            )
        ],
    )


def _write_derivative_certificate(
    fixture: _DeliveryFixture,
    payload: MockExam | PodcastScript,
    derivative_kind: DerivativeArtifactKind,
    execution_identity: DerivativeExecutionIdentity,
) -> Path:
    parent_digest = _digest(fixture.certificate_path.read_bytes())
    payload_digest = _digest(canonical_model_bytes(payload))
    derivative_dir = (
        fixture.certificates_root / _LECTURE_ID / "derivatives" / derivative_kind.value
    )
    report_digests: dict[DerivativeQualityGate, str] = {}
    for gate_name in DerivativeQualityGate:
        report = DerivativeGateReport(
            schema_version=1,
            lecture_id=_LECTURE_ID,
            artifact_kind=derivative_kind,
            gate_name=gate_name,
            status="approved",
            payload_sha256=payload_digest,
            parent_certificate_sha256=parent_digest,
            evaluator_artifact_sha256=_DIGEST_B,
            findings=(),
        )
        report_bytes = report.model_dump_json().encode()
        _write_bytes(
            derivative_dir / "reports" / f"{gate_name.value}.json",
            report_bytes,
        )
        report_digests[gate_name] = _digest(report_bytes)
    certificate = DerivativeQualityCertificate(
        schema_version=1,
        lecture_id=_LECTURE_ID,
        artifact_kind=derivative_kind,
        status="approved",
        payload_sha256=payload_digest,
        parent_certificate_sha256=parent_digest,
        canonical_document_sha256=_digest(fixture.canonical_path.read_bytes()),
        generator_sha256=execution_identity.generator_sha256,
        prompt_sha256=execution_identity.prompt_sha256,
        model_sha256=execution_identity.model_sha256,
        schema_sha256=execution_identity.schema_sha256,
        gate_report_sha256=report_digests,
        gate_evaluator_artifact_sha256={
            gate: _DIGEST_B for gate in DerivativeQualityGate
        },
        trust_key_id=fixture.trust.key_id,
        signature_sha256="0" * 64,
    )
    certificate = certificate.model_copy(
        update={
            "signature_sha256": fixture.trust.sign(certificate.authentication_payload())
        }
    )
    certificate_path = derivative_dir / "certificate.json"
    _write_bytes(certificate_path, certificate.model_dump_json().encode())
    return certificate_path


def _build_derivative_delivery_fixture(
    tmp_path: Path,
    artifact_kind: DeliveryArtifactKind,
) -> _DerivativeDeliveryFixture:
    base = _build_fixture(tmp_path)
    derivative_identity = DerivativeExecutionIdentity(
        generator_sha256=_DIGEST_A,
        prompt_sha256=_DIGEST_B,
        model_sha256=_DIGEST_C,
        schema_sha256=_DIGEST_D,
    )
    audio_identity = DeliveryExecutionIdentity(
        generator_sha256=_DIGEST_D,
        prompt_sha256=_DIGEST_C,
        model_sha256=_DIGEST_B,
        schema_sha256=_DIGEST_A,
        renderer_sha256=_DIGEST_D,
    )
    if artifact_kind is DeliveryArtifactKind.MOCK_EXAM:
        payload: MockExam | PodcastScript = _mock_exam()
        derivative_kind = DerivativeArtifactKind.MOCK_EXAM
        payload_path = base.output_roots[artifact_kind] / _LECTURE_ID / "exam.json"
        _write_bytes(payload_path, payload.model_dump_json().encode())
        _write_bytes(
            base.output_roots[artifact_kind] / _LECTURE_ID / "simulado.md",
            b"# Exam\n",
        )
    else:
        payload = _podcast_script()
        derivative_kind = DerivativeArtifactKind.PODCAST_SCRIPT
        payload_path = (
            base.output_roots[DeliveryArtifactKind.PODCAST_SCRIPT]
            / _LECTURE_ID
            / "script.json"
        )
        _write_bytes(payload_path, payload.model_dump_json().encode())
        _write_bytes(
            base.output_roots[DeliveryArtifactKind.PODCAST_SCRIPT]
            / _LECTURE_ID
            / "script_legivel.md",
            b"# Podcast script\n",
        )
        if artifact_kind is DeliveryArtifactKind.PODCAST_AUDIO:
            _write_bytes(
                base.output_roots[artifact_kind] / _LECTURE_ID / "podcast_final.m4a",
                b"audio-bytes",
            )
    derivative_certificate_path = _write_derivative_certificate(
        base,
        payload,
        derivative_kind,
        derivative_identity,
    )
    derivative_verifier = DerivativeQualityCertificateVerifier(
        certificates_root=base.certificates_root,
        canonical_documents_root=base.canonical_root,
        metadata_root=base.metadata_root,
        trust=base.trust,
        execution_identity_resolver=_StaticDerivativeIdentityResolver(
            derivative_identity
        ),
        quality_certificate_verifier=base.quality_verifier,
    )
    audio_resolver: AudioRuntimeIdentityResolver | None = None
    if artifact_kind is DeliveryArtifactKind.PODCAST_AUDIO:
        audio_resolver = _StaticAudioRuntimeIdentityResolver(audio_identity)
    verifier = DeliveryManifestVerifier(
        manifests_root=base.manifest_path.parents[1],
        certificates_root=base.certificates_root,
        canonical_documents_root=base.canonical_root,
        metadata_root=base.metadata_root,
        output_roots=base.output_roots,
        trust=base.trust,
        quality_certificate_verifier=base.quality_verifier,
        derivative_certificate_verifier=derivative_verifier,
        audio_runtime_identity_resolver=audio_resolver,
    )
    issuer = DeliveryManifestIssuer(
        manifests_root=base.manifest_path.parents[1],
        certificates_root=base.certificates_root,
        canonical_documents_root=base.canonical_root,
        metadata_root=base.metadata_root,
        output_roots=base.output_roots,
        trust=base.trust,
        quality_certificate_verifier=base.quality_verifier,
        derivative_certificate_verifier=derivative_verifier,
        audio_runtime_identity_resolver=audio_resolver,
    )
    return _DerivativeDeliveryFixture(
        base=base,
        verifier=verifier,
        issuer=issuer,
        derivative_verifier=derivative_verifier,
        artifact_kind=artifact_kind,
        payload=payload,
        derivative_certificate_path=derivative_certificate_path,
        payload_path=payload_path,
        audio_identity=audio_identity,
    )


def _as_mock_exam(payload: MockExam | PodcastScript) -> MockExam:
    if not isinstance(payload, MockExam):
        raise AssertionError("expected a typed mock exam fixture payload")
    return payload


def _as_podcast_script(payload: MockExam | PodcastScript) -> PodcastScript:
    if not isinstance(payload, PodcastScript):
        raise AssertionError("expected a typed podcast script fixture payload")
    return payload


def test_verifier_accepts_exact_deterministic_delivery(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)

    first = fixture.verifier.verify(
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        fixture.identity,
    )
    second = fixture.verifier.verify(
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        fixture.identity,
    )

    assert first == second
    assert [output.relative_path for output in first.manifest.outputs] == [
        "study_guide.html",
        "study_guide.md",
    ]
    manifest_data = json.loads(fixture.manifest_path.read_text())
    assert "derivative_certificate_sha256" not in manifest_data
    assert "derivative_payload_sha256" not in manifest_data


def test_verifier_rejects_changed_output_bytes(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    (fixture.output_dir / "study_guide.md").write_text("changed")

    with pytest.raises(DeliveryVerificationError, match="changed"):
        fixture.verifier.verify(
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            fixture.identity,
        )


def test_verifier_rejects_changed_execution_identity(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    changed_identity = fixture.identity.model_copy(
        update={"generator_sha256": _DIGEST_D}
    )

    with pytest.raises(DeliveryVerificationError, match="identity changed"):
        fixture.verifier.verify(
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            changed_identity,
        )


def test_publication_identity_tracks_renderer_version_and_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        delivery_module,
        "distribution_version",
        lambda distribution: "4.2.0",
    )
    current_identity = deterministic_delivery_execution_identity(
        DeliveryArtifactKind.PUBLISHING
    )
    renderer_payload = canonical_json_bytes(
        {
            "distribution": "markdown-it-py",
            "version": "4.2.0",
            "preset": "commonmark",
            "options": dict(MarkdownIt("commonmark", {"html": False}).options),
        }
    )

    assert current_identity.renderer_sha256 == _digest(renderer_payload)

    monkeypatch.setattr(
        delivery_module,
        "distribution_version",
        lambda distribution: "5.0.0",
    )
    upgraded_identity = deterministic_delivery_execution_identity(
        DeliveryArtifactKind.PUBLISHING
    )

    assert upgraded_identity.renderer_sha256 != current_identity.renderer_sha256
    assert upgraded_identity.generator_sha256 == current_identity.generator_sha256
    assert upgraded_identity.schema_sha256 == current_identity.schema_sha256


@pytest.mark.parametrize("stale_subject", ["certificate", "canonical"])
def test_verifier_rejects_stale_parent_subject(
    tmp_path: Path,
    stale_subject: str,
) -> None:
    fixture = _build_fixture(tmp_path)
    stale_path = (
        fixture.certificate_path
        if stale_subject == "certificate"
        else fixture.canonical_path
    )
    stale_path.write_bytes(stale_path.read_bytes() + b"\n")

    with pytest.raises(DeliveryVerificationError, match="changed"):
        fixture.verifier.verify(
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            fixture.identity,
        )


@pytest.mark.parametrize(
    ("source_artifact", "mutation"),
    [
        ("global-index", "missing"),
        ("global-index", "changed"),
        ("gate-report", "missing"),
        ("gate-report", "changed"),
    ],
)
def test_verifier_rejects_incomplete_source_certification(
    tmp_path: Path,
    source_artifact: str,
    mutation: str,
) -> None:
    fixture = _build_fixture(tmp_path)
    source_path = (
        fixture.global_index_path
        if source_artifact == "global-index"
        else fixture.gate_reports_dir / f"{next(iter(QualityGateName)).value}.json"
    )
    if mutation == "missing":
        source_path.unlink()
    else:
        source_path.write_bytes(source_path.read_bytes() + b"\n")

    with pytest.raises(DeliveryVerificationError, match="source certification"):
        fixture.verifier.verify(
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            fixture.identity,
        )


def test_verifier_rejects_coordinated_certificate_forgery(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    certificate_data = json.loads(fixture.certificate_path.read_text())
    certificate_data["signature_sha256"] = _DIGEST_A
    certificate_bytes = json.dumps(certificate_data).encode()
    fixture.certificate_path.write_bytes(certificate_bytes)

    manifest_data = json.loads(fixture.manifest_path.read_text())
    manifest_data["parent_certificate_sha256"] = _digest(certificate_bytes)
    fixture.manifest_path.write_text(json.dumps(manifest_data))

    with pytest.raises(DeliveryVerificationError, match="authentication failed"):
        fixture.verifier.verify(
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            fixture.identity,
        )


@pytest.mark.parametrize("filesystem_change", ["missing", "extra"])
def test_verifier_rejects_missing_or_extra_output(
    tmp_path: Path,
    filesystem_change: str,
) -> None:
    fixture = _build_fixture(tmp_path)
    if filesystem_change == "missing":
        (fixture.output_dir / "study_guide.md").unlink()
    else:
        (fixture.output_dir / "unexpected.txt").write_text("unexpected")

    with pytest.raises(DeliveryVerificationError, match="output set"):
        fixture.verifier.verify(
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            fixture.identity,
        )


def test_verifier_rejects_traversal_in_manifest(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    manifest_data = json.loads(fixture.manifest_path.read_text())
    manifest_data["outputs"][0]["relative_path"] = "../study_guide.html"
    fixture.manifest_path.write_text(json.dumps(manifest_data))

    with pytest.raises(DeliveryVerificationError, match="invalid"):
        fixture.verifier.verify(
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            fixture.identity,
        )


def test_verifier_rejects_symlinked_output(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    output_path = fixture.output_dir / "study_guide.md"
    target_path = tmp_path / "outside.md"
    target_path.write_text("outside")
    output_path.unlink()
    output_path.symlink_to(target_path)

    with pytest.raises(DeliveryVerificationError, match="symbolic link"):
        fixture.verifier.verify(
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            fixture.identity,
        )


@pytest.mark.parametrize("root_style", ["absolute", "relative"])
def test_rooted_store_rejects_intermediate_root_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root_style: str,
) -> None:
    real_parent = tmp_path / "real-parent"
    control_path = real_parent / "artifacts" / "control.json"
    _write_bytes(control_path, b"{}")
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    if root_style == "relative":
        monkeypatch.chdir(tmp_path)
        configured_root = Path("linked-parent/artifacts")
    else:
        configured_root = linked_parent / "artifacts"

    with pytest.raises(DeliveryVerificationError, match="missing or unsafe"):
        RootedArtifactStore(configured_root).read_file(("control.json",))


def test_verifier_rejects_hardlinked_output(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    os.link(
        fixture.output_dir / "study_guide.md",
        fixture.output_dir / "study_guide-copy.md",
    )

    with pytest.raises(DeliveryVerificationError, match="hard link"):
        fixture.verifier.verify(
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            fixture.identity,
        )


@pytest.mark.parametrize(
    ("field_name", "wrong_value"),
    [
        ("lecture_id", "lecture-002"),
        ("artifact_kind", DeliveryArtifactKind.FLASHCARDS.value),
    ],
)
def test_verifier_rejects_wrong_lecture_or_kind(
    tmp_path: Path,
    field_name: str,
    wrong_value: str,
) -> None:
    fixture = _build_fixture(tmp_path)
    manifest_data = json.loads(fixture.manifest_path.read_text())
    manifest_data[field_name] = wrong_value
    fixture.manifest_path.write_text(json.dumps(manifest_data))

    with pytest.raises(DeliveryVerificationError):
        fixture.verifier.verify(
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            fixture.identity,
        )


@pytest.mark.parametrize(
    "ambiguous_json",
    [
        '{"schema_version":1,"schema_version":1}',
        '{"schema_version":NaN}',
    ],
)
def test_verifier_rejects_ambiguous_json(
    tmp_path: Path,
    ambiguous_json: str,
) -> None:
    fixture = _build_fixture(tmp_path)
    fixture.manifest_path.write_text(ambiguous_json)

    with pytest.raises(DeliveryVerificationError, match="invalid"):
        fixture.verifier.verify(
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            fixture.identity,
        )


def test_issuer_revalidates_source_and_signs_deterministic_delivery(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)

    verified = fixture.issuer.issue_deterministic(
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        expected_output_bytes=fixture.output_bytes(DeliveryArtifactKind.PUBLISHING),
    )

    assert verified.manifest.artifact_kind is DeliveryArtifactKind.PUBLISHING
    assert verified.manifest.signature_sha256 != "0" * 64
    assert len(verified.output_paths) == 2


def test_issuer_preserves_sibling_manifests_for_the_same_lecture(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    publishing_delivery = fixture.issuer.issue_deterministic(
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        expected_output_bytes=fixture.output_bytes(DeliveryArtifactKind.PUBLISHING),
    )
    flashcard_bytes = {"anki_deck.csv": b"front,back\nquestion,answer\n"}
    write_delivery_outputs(
        fixture.output_roots[DeliveryArtifactKind.FLASHCARDS],
        _LECTURE_ID,
        DeliveryArtifactKind.FLASHCARDS,
        flashcard_bytes,
    )

    fixture.issuer.issue_deterministic(
        _LECTURE_ID,
        DeliveryArtifactKind.FLASHCARDS,
        expected_output_bytes=flashcard_bytes,
    )

    assert fixture.manifest_path.is_file()
    assert (fixture.manifest_path.parent / "flashcards.json").is_file()
    assert fixture.verifier.is_current(
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        publishing_delivery.manifest.execution_identity,
    )


def test_concurrent_issuers_serialize_output_manifest_and_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    first_output_bytes = {
        "study_guide.html": b"<main>first</main>",
        "study_guide.md": b"# First\n",
    }
    second_output_bytes = {
        "study_guide.html": b"<main>second</main>",
        "study_guide.md": b"# Second\n",
    }
    first_fingerprinted = threading.Event()
    release_first = threading.Event()
    second_fingerprinted = threading.Event()
    original_fingerprint = DeliveryManifestIssuer._fingerprint_exact_outputs

    def coordinate_fingerprints(
        issuer: DeliveryManifestIssuer,
        lecture_id: str,
        artifact_kind: DeliveryArtifactKind,
        expected_output_bytes: Mapping[str, bytes],
    ) -> list[DeliveryOutput]:
        outputs = original_fingerprint(
            issuer,
            lecture_id,
            artifact_kind,
            expected_output_bytes,
        )
        if expected_output_bytes is first_output_bytes:
            first_fingerprinted.set()
            if not release_first.wait(timeout=5):
                raise AssertionError("first issuer was not released")
        else:
            second_fingerprinted.set()
        return outputs

    monkeypatch.setattr(
        DeliveryManifestIssuer,
        "_fingerprint_exact_outputs",
        coordinate_fingerprints,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            fixture.issuer.issue_deterministic,
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            expected_output_bytes=first_output_bytes,
        )
        assert first_fingerprinted.wait(timeout=5)
        second_future = executor.submit(
            fixture.issuer.issue_deterministic,
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            expected_output_bytes=second_output_bytes,
        )
        assert not second_fingerprinted.wait(timeout=0.1)
        release_first.set()
        first_delivery = first_future.result(timeout=5)
        second_delivery = second_future.result(timeout=5)

    assert first_delivery.manifest.outputs != second_delivery.manifest.outputs
    assert second_fingerprinted.is_set()
    assert fixture.output_bytes(DeliveryArtifactKind.PUBLISHING) == second_output_bytes
    assert fixture.verifier.is_current(
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        second_delivery.manifest.execution_identity,
    )


def test_issuer_rejects_llm_derivative_without_quality_certificate(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)

    with pytest.raises(DeliveryVerificationError, match="derivative quality"):
        fixture.issuer.issue_deterministic(
            _LECTURE_ID,
            DeliveryArtifactKind.MOCK_EXAM,
            expected_output_bytes={},
        )


def test_issuer_transaction_removes_stale_output_before_signing(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    expected_output_bytes = fixture.output_bytes(DeliveryArtifactKind.PUBLISHING)
    (fixture.output_dir / "unexpected.txt").write_text("unexpected", encoding="utf-8")

    verified = fixture.issuer.issue_deterministic(
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        expected_output_bytes=expected_output_bytes,
    )

    assert not (fixture.output_dir / "unexpected.txt").exists()
    assert (
        fixture.output_bytes(DeliveryArtifactKind.PUBLISHING) == expected_output_bytes
    )
    assert len(verified.output_paths) == len(expected_output_bytes)


@pytest.mark.parametrize(
    "artifact_kind",
    [
        DeliveryArtifactKind.MOCK_EXAM,
        DeliveryArtifactKind.PODCAST_SCRIPT,
        DeliveryArtifactKind.PODCAST_AUDIO,
    ],
)
def test_typed_derivative_delivery_binds_exact_certificate_and_payload(
    tmp_path: Path,
    artifact_kind: DeliveryArtifactKind,
) -> None:
    fixture = _build_derivative_delivery_fixture(tmp_path, artifact_kind)

    if artifact_kind is DeliveryArtifactKind.MOCK_EXAM:
        issued = fixture.issuer.issue_mock_exam(
            _as_mock_exam(fixture.payload),
            expected_output_bytes=fixture.output_bytes(),
        )
        verified = fixture.verifier.verify_mock_exam(_LECTURE_ID)
        assert fixture.verifier.is_mock_exam_current(_LECTURE_ID)
    elif artifact_kind is DeliveryArtifactKind.PODCAST_SCRIPT:
        issued = fixture.issuer.issue_podcast_script(
            _as_podcast_script(fixture.payload),
            expected_output_bytes=fixture.output_bytes(),
        )
        verified = fixture.verifier.verify_podcast_script(_LECTURE_ID)
        assert fixture.verifier.is_podcast_script_current(_LECTURE_ID)
    else:
        issued = fixture.issuer.issue_podcast_audio(
            _as_podcast_script(fixture.payload),
            expected_output_bytes=fixture.output_bytes(),
        )
        verified = fixture.verifier.verify_podcast_audio(_LECTURE_ID)
        assert fixture.verifier.is_podcast_audio_current(_LECTURE_ID)

    assert issued == verified
    assert verified.manifest.schema_version == 2
    assert verified.manifest.derivative_certificate_sha256 == _digest(
        fixture.derivative_certificate_path.read_bytes()
    )
    assert verified.manifest.derivative_payload_sha256 == _digest(
        canonical_model_bytes(fixture.payload)
    )
    if artifact_kind is DeliveryArtifactKind.PODCAST_AUDIO:
        assert verified.manifest.execution_identity == fixture.audio_identity
    else:
        compiler_path = {
            DeliveryArtifactKind.MOCK_EXAM: Path("src/lecture_study/exams/compiler.py"),
            DeliveryArtifactKind.PODCAST_SCRIPT: Path(
                "src/lecture_study/podcast/compiler.py"
            ),
        }[artifact_kind]
        assert verified.manifest.execution_identity.renderer_sha256 == _digest(
            compiler_path.read_bytes()
        )


def test_schema_v2_requires_exact_derivative_lineage_field_pair(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    deterministic_data = json.loads(fixture.manifest_path.read_text())
    deterministic_data["derivative_certificate_sha256"] = _DIGEST_A
    deterministic_data["derivative_payload_sha256"] = _DIGEST_B

    with pytest.raises(ValueError, match="forbid derivative lineage"):
        DeliveryManifest.model_validate_json(json.dumps(deterministic_data))

    deterministic_data["derivative_certificate_sha256"] = None
    deterministic_data["derivative_payload_sha256"] = None
    with pytest.raises(ValueError, match="forbid derivative lineage"):
        DeliveryManifest.model_validate_json(json.dumps(deterministic_data))

    derivative_data = json.loads(fixture.manifest_path.read_text())
    derivative_data["artifact_kind"] = DeliveryArtifactKind.MOCK_EXAM.value
    derivative_data["outputs"] = [
        {
            "relative_path": "exam.json",
            "sha256": _DIGEST_A,
            "byte_size": 1,
        },
        {
            "relative_path": "simulado.md",
            "sha256": _DIGEST_B,
            "byte_size": 1,
        },
    ]

    with pytest.raises(ValueError, match="require exact certificate and payload"):
        DeliveryManifest.model_validate_json(json.dumps(derivative_data))


def test_derivative_verifier_rejects_changed_exact_certificate_bytes(
    tmp_path: Path,
) -> None:
    fixture = _build_derivative_delivery_fixture(
        tmp_path,
        DeliveryArtifactKind.MOCK_EXAM,
    )
    fixture.issuer.issue_mock_exam(
        _as_mock_exam(fixture.payload),
        expected_output_bytes=fixture.output_bytes(),
    )
    fixture.derivative_certificate_path.write_bytes(
        fixture.derivative_certificate_path.read_bytes() + b"\n"
    )

    with pytest.raises(
        DeliveryVerificationError, match="certificate or payload changed"
    ):
        fixture.verifier.verify_mock_exam(_LECTURE_ID)


def test_mock_exam_delivery_reparses_and_binds_canonical_payload(
    tmp_path: Path,
) -> None:
    fixture = _build_derivative_delivery_fixture(
        tmp_path,
        DeliveryArtifactKind.MOCK_EXAM,
    )
    fixture.issuer.issue_mock_exam(
        _as_mock_exam(fixture.payload),
        expected_output_bytes=fixture.output_bytes(),
    )
    payload_data = json.loads(fixture.payload_path.read_text())
    payload_data["questions"] = [
        {
            "question_text": "Changed question",
            "options": {"A": "A", "B": "B", "C": "C", "D": "D"},
            "correct_answer": "A",
            "explanation": "Changed explanation",
        }
    ]
    fixture.payload_path.write_text(json.dumps(payload_data))

    with pytest.raises(
        DeliveryVerificationError, match="derivative source certification"
    ):
        fixture.verifier.verify_mock_exam(_LECTURE_ID)


def test_audio_delivery_binds_exact_certified_podcast_script(
    tmp_path: Path,
) -> None:
    fixture = _build_derivative_delivery_fixture(
        tmp_path,
        DeliveryArtifactKind.PODCAST_AUDIO,
    )
    fixture.issuer.issue_podcast_audio(
        _as_podcast_script(fixture.payload),
        expected_output_bytes=fixture.output_bytes(),
    )
    payload_data = json.loads(fixture.payload_path.read_text())
    payload_data["title"] = "Changed title"
    fixture.payload_path.write_text(json.dumps(payload_data))

    with pytest.raises(
        DeliveryVerificationError, match="derivative source certification"
    ):
        fixture.verifier.verify_podcast_audio(_LECTURE_ID)


def test_derivative_delivery_fails_closed_without_live_identity_resolver(
    tmp_path: Path,
) -> None:
    fixture = _build_derivative_delivery_fixture(
        tmp_path,
        DeliveryArtifactKind.MOCK_EXAM,
    )
    fixture.issuer.issue_mock_exam(
        _as_mock_exam(fixture.payload),
        expected_output_bytes=fixture.output_bytes(),
    )
    verifier = DeliveryManifestVerifier(
        manifests_root=fixture.base.manifest_path.parents[1],
        certificates_root=fixture.base.certificates_root,
        canonical_documents_root=fixture.base.canonical_root,
        metadata_root=fixture.base.metadata_root,
        output_roots=fixture.base.output_roots,
        trust=fixture.base.trust,
        quality_certificate_verifier=fixture.base.quality_verifier,
    )

    with pytest.raises(DeliveryVerificationError, match="identity resolver"):
        verifier.verify_mock_exam(_LECTURE_ID)


def test_audio_delivery_fails_closed_without_audio_runtime_resolver(
    tmp_path: Path,
) -> None:
    fixture = _build_derivative_delivery_fixture(
        tmp_path,
        DeliveryArtifactKind.PODCAST_AUDIO,
    )
    fixture.issuer.issue_podcast_audio(
        _as_podcast_script(fixture.payload),
        expected_output_bytes=fixture.output_bytes(),
    )
    verifier = DeliveryManifestVerifier(
        manifests_root=fixture.base.manifest_path.parents[1],
        certificates_root=fixture.base.certificates_root,
        canonical_documents_root=fixture.base.canonical_root,
        metadata_root=fixture.base.metadata_root,
        output_roots=fixture.base.output_roots,
        trust=fixture.base.trust,
        quality_certificate_verifier=fixture.base.quality_verifier,
        derivative_certificate_verifier=fixture.derivative_verifier,
    )

    with pytest.raises(DeliveryVerificationError, match="audio runtime identity"):
        verifier.verify_podcast_audio(_LECTURE_ID)


def test_generic_verifier_forbids_caller_identity_for_derivative_delivery(
    tmp_path: Path,
) -> None:
    fixture = _build_derivative_delivery_fixture(
        tmp_path,
        DeliveryArtifactKind.MOCK_EXAM,
    )

    with pytest.raises(DeliveryVerificationError, match="typed verifier API"):
        fixture.verifier.verify(
            _LECTURE_ID,
            DeliveryArtifactKind.MOCK_EXAM,
            fixture.base.identity,
        )


def test_rooted_store_write_rejects_symlinked_lecture_directory(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    outside = tmp_path / "outside"
    output_root.mkdir()
    outside.mkdir()
    (output_root / _LECTURE_ID).symlink_to(outside, target_is_directory=True)

    with pytest.raises(DeliveryVerificationError, match="missing or unsafe"):
        RootedArtifactStore(output_root).write_files_atomically(
            (_LECTURE_ID,),
            {"artifact.txt": b"trusted"},
        )

    assert not (outside / "artifact.txt").exists()


def test_rooted_store_write_rejects_precreated_staging_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    outside = tmp_path / "outside"
    output_root.mkdir()
    outside.mkdir()
    (output_root / f".{_LECTURE_ID}.fixed.staging").symlink_to(
        outside,
        target_is_directory=True,
    )
    monkeypatch.setattr(secrets, "token_hex", lambda _length: "fixed")

    with pytest.raises(DeliveryVerificationError, match="missing or unsafe"):
        RootedArtifactStore(output_root).write_files_atomically(
            (_LECTURE_ID,),
            {"artifact.txt": b"trusted"},
        )

    assert not (outside / "artifact.txt").exists()


def test_multi_file_promotion_failure_preserves_previous_complete_delivery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    previous_outputs = {
        "study_guide.html": b"<main>previous</main>",
        "study_guide.md": b"# Previous\n",
    }
    replacement_outputs = {
        "study_guide.html": b"<main>replacement</main>",
        "study_guide.md": b"# Replacement\n",
    }
    write_delivery_outputs(
        output_root,
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        previous_outputs,
    )
    original_replace = os.replace
    promotion_count = 0

    def fail_second_file_promotion(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal promotion_count
        if source.endswith(".tmp"):
            promotion_count += 1
            if promotion_count == 2:
                raise OSError("injected second promotion failure")
        original_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "replace", fail_second_file_promotion)

    with pytest.raises(DeliveryVerificationError, match="missing or unsafe"):
        write_delivery_outputs(
            output_root,
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            replacement_outputs,
        )

    lecture_root = output_root / _LECTURE_ID
    assert {
        path.name: path.read_bytes() for path in lecture_root.iterdir()
    } == previous_outputs
    assert [path.name for path in output_root.iterdir()] == [_LECTURE_ID]


def test_atomic_directory_exchange_failure_preserves_previous_delivery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    previous_outputs = {
        "study_guide.html": b"<main>previous</main>",
        "study_guide.md": b"# Previous\n",
    }
    write_delivery_outputs(
        output_root,
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        previous_outputs,
    )
    exchange_attempted = False

    def fail_atomic_exchange(
        parent_descriptor: int,
        _source_name: str,
        target_name: str,
    ) -> None:
        nonlocal exchange_attempted
        exchange_attempted = True
        current_status = os.stat(
            target_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        assert stat.S_ISDIR(current_status.st_mode)
        raise OSError("injected atomic exchange failure")

    monkeypatch.setattr(
        delivery_module,
        "_exchange_entries_atomically",
        fail_atomic_exchange,
    )

    with pytest.raises(DeliveryVerificationError, match="missing or unsafe"):
        write_delivery_outputs(
            output_root,
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            {
                "study_guide.html": b"<main>replacement</main>",
                "study_guide.md": b"# Replacement\n",
            },
        )

    lecture_root = output_root / _LECTURE_ID
    assert exchange_attempted
    assert {
        path.name: path.read_bytes() for path in lecture_root.iterdir()
    } == previous_outputs
    assert [path.name for path in output_root.iterdir()] == [_LECTURE_ID]


def test_parent_fsync_failure_after_exchange_restores_previous_delivery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    previous_outputs = {
        "study_guide.html": b"<main>previous</main>",
        "study_guide.md": b"# Previous\n",
    }
    write_delivery_outputs(
        output_root,
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        previous_outputs,
    )
    parent_status = output_root.stat()
    original_fsync = os.fsync
    parent_failure_injected = False

    def fail_first_parent_fsync(descriptor: int) -> None:
        nonlocal parent_failure_injected
        descriptor_status = os.fstat(descriptor)
        if not parent_failure_injected and (
            descriptor_status.st_dev,
            descriptor_status.st_ino,
        ) == (parent_status.st_dev, parent_status.st_ino):
            parent_failure_injected = True
            raise OSError("injected parent fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_first_parent_fsync)

    with pytest.raises(DeliveryVerificationError, match="missing or unsafe"):
        write_delivery_outputs(
            output_root,
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            {
                "study_guide.html": b"<main>replacement</main>",
                "study_guide.md": b"# Replacement\n",
            },
        )

    lecture_root = output_root / _LECTURE_ID
    assert parent_failure_injected
    assert {
        path.name: path.read_bytes() for path in lecture_root.iterdir()
    } == previous_outputs
    assert not [
        path for path in output_root.iterdir() if path.name.endswith(".staging")
    ]


def test_failed_rollback_quarantines_prior_generation_and_blocks_rewrite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    previous_outputs = {
        "study_guide.html": b"<main>previous</main>",
        "study_guide.md": b"# Previous\n",
    }
    replacement_outputs = {
        "study_guide.html": b"<main>replacement</main>",
        "study_guide.md": b"# Replacement\n",
    }
    write_delivery_outputs(
        output_root,
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        previous_outputs,
    )
    parent_status = output_root.stat()
    original_fsync = os.fsync
    original_exchange = delivery_module._exchange_entries_atomically
    parent_failure_injected = False
    exchange_count = 0

    def fail_first_parent_fsync(descriptor: int) -> None:
        nonlocal parent_failure_injected
        descriptor_status = os.fstat(descriptor)
        if not parent_failure_injected and (
            descriptor_status.st_dev,
            descriptor_status.st_ino,
        ) == (parent_status.st_dev, parent_status.st_ino):
            parent_failure_injected = True
            raise OSError("injected parent fsync failure")
        original_fsync(descriptor)

    def fail_rollback_exchange(
        parent_descriptor: int,
        source_name: str,
        target_name: str,
    ) -> None:
        nonlocal exchange_count
        exchange_count += 1
        if exchange_count == 2:
            raise OSError("injected rollback exchange failure")
        original_exchange(parent_descriptor, source_name, target_name)

    monkeypatch.setattr(os, "fsync", fail_first_parent_fsync)
    monkeypatch.setattr(
        delivery_module,
        "_exchange_entries_atomically",
        fail_rollback_exchange,
    )

    with pytest.raises(
        DeliveryVerificationError,
        match="previous complete delivery rollback failed",
    ):
        write_delivery_outputs(
            output_root,
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            replacement_outputs,
        )

    recovery_directories = list(output_root.glob(f".{_LECTURE_ID}.*.recovery"))
    assert len(recovery_directories) == 1
    assert {
        path.name: path.read_bytes() for path in recovery_directories[0].iterdir()
    } == previous_outputs

    with pytest.raises(
        DeliveryVerificationError,
        match="quarantined generation requiring recovery",
    ):
        write_delivery_outputs(
            output_root,
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            replacement_outputs,
        )

    assert recovery_directories[0].is_dir()


def test_writer_recovers_pre_exchange_staging_after_process_crash(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    orphaned_staging = output_root / f".{_LECTURE_ID}.{'a' * 32}.staging"
    orphaned_staging.mkdir()
    (orphaned_staging / "study_guide.md").write_bytes(b"incomplete")
    expected_outputs = {
        "study_guide.html": b"<main>current</main>",
        "study_guide.md": b"# Current\n",
    }

    write_delivery_outputs(
        output_root,
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        expected_outputs,
    )

    lecture_root = output_root / _LECTURE_ID
    assert {
        path.name: path.read_bytes() for path in lecture_root.iterdir()
    } == expected_outputs
    assert not orphaned_staging.exists()


def test_writer_quarantines_ambiguous_staging_after_process_crash(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    current_outputs = {
        "study_guide.html": b"<main>current</main>",
        "study_guide.md": b"# Current\n",
    }
    write_delivery_outputs(
        output_root,
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        current_outputs,
    )
    token = "b" * 32
    orphaned_staging = output_root / f".{_LECTURE_ID}.{token}.staging"
    recovery_directory = output_root / f".{_LECTURE_ID}.{token}.recovery"
    orphaned_staging.mkdir()
    (orphaned_staging / "study_guide.html").write_bytes(b"previous")

    with pytest.raises(
        DeliveryVerificationError,
        match="quarantined for recovery",
    ):
        write_delivery_outputs(
            output_root,
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            {
                "study_guide.html": b"<main>replacement</main>",
                "study_guide.md": b"# Replacement\n",
            },
        )

    lecture_root = output_root / _LECTURE_ID
    assert {
        path.name: path.read_bytes() for path in lecture_root.iterdir()
    } == current_outputs
    assert not orphaned_staging.exists()
    assert (recovery_directory / "study_guide.html").read_bytes() == b"previous"


@pytest.mark.parametrize("failure_point", ["write", "fsync"])
def test_stage_file_removes_temporary_after_write_or_fsync_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_point: str,
) -> None:
    directory = tmp_path / "outputs"
    directory.mkdir()
    directory_descriptor = os.open(
        directory,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    monkeypatch.setattr(secrets, "token_hex", lambda _length: "fixed")

    if failure_point == "write":

        def fail_write(_descriptor: int, _payload: bytes) -> None:
            raise OSError("injected write failure")

        monkeypatch.setattr(
            RootedArtifactStore,
            "_write_all",
            staticmethod(fail_write),
        )
    else:

        def fail_fsync(_descriptor: int) -> None:
            raise OSError("injected fsync failure")

        monkeypatch.setattr(os, "fsync", fail_fsync)

    try:
        with pytest.raises(OSError, match=f"injected {failure_point} failure"):
            RootedArtifactStore._stage_file(
                directory_descriptor,
                "artifact.txt",
                b"payload",
            )
    finally:
        os.close(directory_descriptor)

    assert list(directory.iterdir()) == []


def test_closed_output_writer_removes_stale_files_without_following_symlinks(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    lecture_root = output_root / _LECTURE_ID
    lecture_root.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    (lecture_root / "study_guide.html").write_bytes(b"old-html")
    (lecture_root / "study_guide.md").write_bytes(b"old-markdown")
    (lecture_root / "stale.txt").write_bytes(b"stale")
    (lecture_root / "stale-link").symlink_to(outside)
    expected_outputs = {
        "study_guide.html": b"<main>current</main>",
        "study_guide.md": b"# Current\n",
    }

    write_delivery_outputs(
        output_root,
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        expected_outputs,
    )

    assert {
        path.name: path.read_bytes() for path in lecture_root.iterdir()
    } == expected_outputs
    assert outside.read_bytes() == b"outside"


def test_issuer_transaction_overwrites_replaced_output_with_exact_bytes(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    expected_output_bytes = fixture.output_bytes(DeliveryArtifactKind.PUBLISHING)
    (fixture.output_dir / "study_guide.md").write_bytes(b"attacker-controlled")

    verified = fixture.issuer.issue_deterministic(
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        expected_output_bytes=expected_output_bytes,
    )

    assert (
        fixture.output_bytes(DeliveryArtifactKind.PUBLISHING) == expected_output_bytes
    )
    assert fixture.verifier.is_current(
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        verified.manifest.execution_identity,
    )


def test_manifest_stage_failure_restores_previous_output_and_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    previous_output_bytes = fixture.output_bytes(DeliveryArtifactKind.PUBLISHING)
    previous_manifest_bytes = fixture.manifest_path.read_bytes()
    replacement_output_bytes = {
        "study_guide.html": b"<main>replacement</main>",
        "study_guide.md": b"# Replacement\n",
    }
    original_stage_file = RootedArtifactStore._stage_file

    def fail_manifest_stage(
        parent_descriptor: int,
        file_name: str,
        payload: bytes,
    ) -> str:
        if file_name == "publishing.json":
            raise OSError("injected manifest stage failure")
        return original_stage_file(parent_descriptor, file_name, payload)

    monkeypatch.setattr(
        RootedArtifactStore,
        "_stage_file",
        staticmethod(fail_manifest_stage),
    )

    with pytest.raises(DeliveryVerificationError, match="missing or unsafe"):
        fixture.issuer.issue_deterministic(
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            expected_output_bytes=replacement_output_bytes,
        )

    assert (
        fixture.output_bytes(DeliveryArtifactKind.PUBLISHING) == previous_output_bytes
    )
    assert fixture.manifest_path.read_bytes() == previous_manifest_bytes
    assert fixture.verifier.is_current(
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        fixture.identity,
    )


def test_final_verification_failure_restores_previous_output_and_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    previous_output_bytes = fixture.output_bytes(DeliveryArtifactKind.PUBLISHING)
    previous_manifest_bytes = fixture.manifest_path.read_bytes()
    replacement_output_bytes = {
        "study_guide.html": b"<main>replacement</main>",
        "study_guide.md": b"# Replacement\n",
    }
    original_verify = DeliveryManifestVerifier.verify

    def fail_final_verification(
        _verifier: DeliveryManifestVerifier,
        _lecture_id: str,
        _artifact_kind: DeliveryArtifactKind,
        _expected_execution_identity: DeliveryExecutionIdentity,
    ) -> VerifiedDelivery:
        raise DeliveryVerificationError("injected final verification failure")

    monkeypatch.setattr(
        DeliveryManifestVerifier,
        "verify",
        fail_final_verification,
    )

    with pytest.raises(
        DeliveryVerificationError,
        match="injected final verification failure",
    ):
        fixture.issuer.issue_deterministic(
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            expected_output_bytes=replacement_output_bytes,
        )

    monkeypatch.setattr(DeliveryManifestVerifier, "verify", original_verify)
    assert (
        fixture.output_bytes(DeliveryArtifactKind.PUBLISHING) == previous_output_bytes
    )
    assert fixture.manifest_path.read_bytes() == previous_manifest_bytes
    assert fixture.verifier.is_current(
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        fixture.identity,
    )


def test_post_verification_manifest_cleanup_failure_keeps_verified_pair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    replacement_output_bytes = {
        "study_guide.html": b"<main>replacement</main>",
        "study_guide.md": b"# Replacement\n",
    }
    original_unlink = os.unlink
    original_fsync = os.fsync
    manifest_backup_unlinked = False
    cleanup_failure_injected = False

    def track_manifest_backup_unlink(
        path: str,
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal manifest_backup_unlinked
        original_unlink(path, dir_fd=dir_fd)
        if path.startswith(".publishing.json.") and path.endswith(".tmp"):
            manifest_backup_unlinked = True

    def fail_fsync_after_manifest_backup_unlink(descriptor: int) -> None:
        nonlocal cleanup_failure_injected
        if manifest_backup_unlinked and not cleanup_failure_injected:
            cleanup_failure_injected = True
            raise OSError("injected post-commit manifest cleanup failure")
        original_fsync(descriptor)

    monkeypatch.setattr(os, "unlink", track_manifest_backup_unlink)
    monkeypatch.setattr(os, "fsync", fail_fsync_after_manifest_backup_unlink)

    verified = fixture.issuer.issue_deterministic(
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        expected_output_bytes=replacement_output_bytes,
    )

    assert manifest_backup_unlinked
    assert cleanup_failure_injected
    assert fixture.output_bytes(DeliveryArtifactKind.PUBLISHING) == (
        replacement_output_bytes
    )
    assert fixture.verifier.is_current(
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        verified.manifest.execution_identity,
    )


@pytest.mark.parametrize("failing_unlock_number", (1, 2))
def test_post_verification_lock_release_failure_keeps_verified_pair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failing_unlock_number: int,
) -> None:
    fixture = _build_fixture(tmp_path)
    replacement_output_bytes = {
        "study_guide.html": b"<main>replacement</main>",
        "study_guide.md": b"# Replacement\n",
    }
    original_flock = delivery_module.fcntl.flock
    unlock_count = 0
    unlock_failure_injected = False

    def fail_selected_unlock(descriptor: int, operation: int) -> None:
        nonlocal unlock_count, unlock_failure_injected
        if operation == delivery_module.fcntl.LOCK_UN:
            unlock_count += 1
            if unlock_count == failing_unlock_number:
                unlock_failure_injected = True
                raise OSError("injected post-commit lock release failure")
        original_flock(descriptor, operation)

    monkeypatch.setattr(delivery_module.fcntl, "flock", fail_selected_unlock)

    verified = fixture.issuer.issue_deterministic(
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        expected_output_bytes=replacement_output_bytes,
    )

    assert unlock_failure_injected
    assert fixture.output_bytes(DeliveryArtifactKind.PUBLISHING) == (
        replacement_output_bytes
    )
    assert fixture.verifier.is_current(
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        verified.manifest.execution_identity,
    )


def test_post_verification_base_exception_preserves_pair_before_propagation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    replacement_output_bytes = {
        "study_guide.html": b"<main>replacement</main>",
        "study_guide.md": b"# Replacement\n",
    }

    def interrupt_manifest_cleanup(
        _store: RootedArtifactStore,
        _parent_descriptor: int,
        _staged_name: str,
    ) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        RootedArtifactStore,
        "_discard_staged_file_after_commit",
        interrupt_manifest_cleanup,
    )

    with pytest.raises(KeyboardInterrupt):
        fixture.issuer.issue_deterministic(
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            expected_output_bytes=replacement_output_bytes,
        )

    assert fixture.output_bytes(DeliveryArtifactKind.PUBLISHING) == (
        replacement_output_bytes
    )
    assert fixture.verifier.is_current(
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        deterministic_delivery_execution_identity(DeliveryArtifactKind.PUBLISHING),
    )


def test_exception_after_commit_mark_preserves_pair_before_propagation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    replacement_output_bytes = {
        "study_guide.html": b"<main>replacement</main>",
        "study_guide.md": b"# Replacement\n",
    }
    original_commit = delivery_module._DeliveryCommitState.commit

    def commit_then_interrupt(
        commit_state: delivery_module._DeliveryCommitState,
        verified_delivery: VerifiedDelivery,
    ) -> None:
        original_commit(commit_state, verified_delivery)
        raise KeyboardInterrupt

    monkeypatch.setattr(
        delivery_module._DeliveryCommitState,
        "commit",
        commit_then_interrupt,
    )

    with pytest.raises(KeyboardInterrupt):
        fixture.issuer.issue_deterministic(
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            expected_output_bytes=replacement_output_bytes,
        )

    assert fixture.output_bytes(DeliveryArtifactKind.PUBLISHING) == (
        replacement_output_bytes
    )
    assert fixture.verifier.is_current(
        _LECTURE_ID,
        DeliveryArtifactKind.PUBLISHING,
        deterministic_delivery_execution_identity(DeliveryArtifactKind.PUBLISHING),
    )


def test_manifest_writer_quarantines_ambiguous_crash_staging(
    tmp_path: Path,
) -> None:
    manifests_root = tmp_path / "delivery-manifests"
    lecture_root = manifests_root / _LECTURE_ID
    lecture_root.mkdir(parents=True)
    manifest_path = lecture_root / "publishing.json"
    manifest_path.write_bytes(b"previous-manifest")
    token = "c" * 32
    staged_path = lecture_root / f".publishing.json.{token}.tmp"
    recovery_path = lecture_root / f".publishing.json.{token}.recovery"
    staged_path.write_bytes(b"ambiguous-manifest")

    with pytest.raises(
        DeliveryVerificationError,
        match="quarantined for recovery",
    ):
        RootedArtifactStore(manifests_root).write_file_atomically(
            (_LECTURE_ID, "publishing.json"),
            b"replacement-manifest",
        )

    assert manifest_path.read_bytes() == b"previous-manifest"
    assert not staged_path.exists()
    assert recovery_path.read_bytes() == b"ambiguous-manifest"


def test_derivative_payload_size_is_checked_before_allocation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = _build_derivative_delivery_fixture(
        tmp_path,
        DeliveryArtifactKind.MOCK_EXAM,
    )
    monkeypatch.setattr(delivery_module, "_MAX_DERIVATIVE_PAYLOAD_BYTES", 8)

    with pytest.raises(DeliveryVerificationError, match="maximum size"):
        fixture.issuer.issue_mock_exam(
            _as_mock_exam(fixture.payload),
            expected_output_bytes=fixture.output_bytes(),
        )


def test_canonical_document_size_is_checked_before_delivery_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    monkeypatch.setattr(delivery_module, "_MAX_CANONICAL_DOCUMENT_BYTES", 8)

    with pytest.raises(DeliveryVerificationError, match="maximum size"):
        fixture.issuer.issue_deterministic(
            _LECTURE_ID,
            DeliveryArtifactKind.PUBLISHING,
            expected_output_bytes=fixture.output_bytes(DeliveryArtifactKind.PUBLISHING),
        )

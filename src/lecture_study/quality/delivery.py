"""Fail-closed lineage verification for final delivery artifacts."""

from __future__ import annotations

import ctypes
import fcntl
import hashlib
import json
import os
import secrets
import stat
import sys
from collections.abc import Iterator, Mapping
from typing import Protocol
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, cast

from markdown_it import MarkdownIt
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)
from pydantic_core import ValidationError

from lecture_study.domain.models import (
    CanonicalLectureDocument,
    MockExam,
    PodcastScript,
    SafeIdentifier,
)
from lecture_study.quality.certification import (
    PublicationQualityCertificate,
    QualityCertificateVerifier,
    QualityCertificationError,
    PublicationRuntimeIdentityResolver,
    VerifiedPublicationInputs,
)
from lecture_study.quality.derivative import (
    DerivativeArtifactKind,
    DerivativeExecutionIdentityResolver,
    DerivativeQualityCertificate,
    DerivativeQualityCertificateVerifier,
    DerivativeQualityError,
)
from lecture_study.quality.trust import (
    HmacQualityTrust,
    QualityTrustError,
    canonical_json_bytes,
    canonical_model_bytes,
)

DELIVERY_MANIFESTS_ROOT = Path("workspace/data/delivery_manifests")
QUALITY_CERTIFICATES_ROOT = Path("workspace/data/quality_certificates")
CANONICAL_DOCUMENTS_ROOT = Path("workspace/data/10_canonical_documents")
METADATA_ROOT = Path("workspace/data/11_metadata")

_LECTURE_ID_ADAPTER = TypeAdapter(SafeIdentifier)
_MAX_MANIFEST_BYTES = 1_048_576
_MAX_CERTIFICATE_BYTES = 1_048_576
_MAX_DERIVATIVE_CERTIFICATE_BYTES = 1_048_576
_MAX_DERIVATIVE_PAYLOAD_BYTES = 16 * 1_048_576
_MAX_CANONICAL_DOCUMENT_BYTES = 64 * 1_048_576
_Digest = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]


class DeliveryVerificationError(RuntimeError):
    """Raised when a delivery cannot be proven current and complete."""


class _RetainStagedDeliveryError(DeliveryVerificationError):
    """Raised when cleanup would destroy the only recoverable prior generation."""


@dataclass(slots=True)
class _DeliveryCommitState:
    """Records the verified delivery and defers teardown errors until it is stable."""

    verified_delivery: VerifiedDelivery | None = None
    post_commit_error: BaseException | None = None

    @property
    def is_committed(self) -> bool:
        return self.verified_delivery is not None

    def commit(self, verified_delivery: VerifiedDelivery) -> None:
        if self.is_committed:
            raise DeliveryVerificationError("delivery transaction was committed twice")
        self.verified_delivery = verified_delivery

    def defer_post_commit_error(self, error: BaseException) -> None:
        if not self.is_committed:
            raise DeliveryVerificationError(
                "delivery teardown error cannot be deferred before commit"
            ) from error
        if self.post_commit_error is None:
            self.post_commit_error = error

    def finalize(self) -> VerifiedDelivery:
        if self.verified_delivery is None:
            raise DeliveryVerificationError("delivery transaction did not reach commit")
        if self.post_commit_error is not None:
            raise self.post_commit_error
        return self.verified_delivery


class DeliveryArtifactKind(StrEnum):
    """Closed registry of externally consumable delivery artifacts."""

    PUBLISHING = "publishing"
    FLASHCARDS = "flashcards"
    MOCK_EXAM = "mock-exam"
    PODCAST_SCRIPT = "podcast-script"
    PODCAST_AUDIO = "podcast-audio"


DELIVERY_OUTPUT_ROOTS: Mapping[DeliveryArtifactKind, Path] = {
    DeliveryArtifactKind.PUBLISHING: Path("workspace/data/12_published"),
    DeliveryArtifactKind.FLASHCARDS: Path("workspace/data/13_flashcards"),
    DeliveryArtifactKind.MOCK_EXAM: Path("workspace/data/14_mock_exams"),
    DeliveryArtifactKind.PODCAST_SCRIPT: Path("workspace/data/15_podcast_scripts"),
    DeliveryArtifactKind.PODCAST_AUDIO: Path("workspace/data/16_podcast_audio"),
}

EXPECTED_OUTPUT_RELATIVE_PATHS: Mapping[
    DeliveryArtifactKind,
    tuple[str, ...],
] = {
    DeliveryArtifactKind.PUBLISHING: (
        "study_guide.html",
        "study_guide.md",
    ),
    DeliveryArtifactKind.FLASHCARDS: ("anki_deck.csv",),
    DeliveryArtifactKind.MOCK_EXAM: (
        "exam.json",
        "simulado.md",
    ),
    DeliveryArtifactKind.PODCAST_SCRIPT: (
        "script.json",
        "script_legivel.md",
    ),
    DeliveryArtifactKind.PODCAST_AUDIO: ("podcast_final.m4a",),
}


class DeliveryExecutionIdentity(BaseModel):
    """Exact implementation identities that produced a delivery."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    generator_sha256: _Digest
    prompt_sha256: _Digest
    model_sha256: _Digest
    schema_sha256: _Digest
    renderer_sha256: _Digest


class AudioRuntimeIdentityResolver(Protocol):
    """Resolves the authoritative delivery identity for one audio context."""

    def __call__(
        self,
        lecture_id: str,
        artifact_kind: DeliveryArtifactKind,
        /,
    ) -> DeliveryExecutionIdentity: ...


class DeliveryOutput(BaseModel):
    """Digest and size of one output at its canonical relative path."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    relative_path: str = Field(min_length=1, max_length=512)
    sha256: _Digest
    byte_size: int = Field(ge=0)

    @field_validator("relative_path")
    @classmethod
    def _validate_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or "\\" in value
            or "\x00" in value
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.as_posix() != value
        ):
            raise ValueError("relative_path must be a canonical safe relative path")
        return value


class DeliveryManifest(BaseModel):
    """Authenticated completion proof for one closed delivery artifact kind."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[2]
    lecture_id: SafeIdentifier
    artifact_kind: DeliveryArtifactKind
    parent_certificate_sha256: _Digest
    canonical_document_sha256: _Digest
    derivative_certificate_sha256: _Digest | None = None
    derivative_payload_sha256: _Digest | None = None
    execution_identity: DeliveryExecutionIdentity
    outputs: list[DeliveryOutput] = Field(min_length=1)
    trust_key_id: _Digest
    signature_sha256: _Digest

    @model_validator(mode="after")
    def _validate_exact_ordered_output_set(self) -> DeliveryManifest:
        observed_paths = tuple(output.relative_path for output in self.outputs)
        expected_paths = EXPECTED_OUTPUT_RELATIVE_PATHS[self.artifact_kind]
        if observed_paths != expected_paths:
            raise ValueError(
                "manifest output paths must exactly match the closed ordered output set"
            )
        return self

    @model_validator(mode="after")
    def _validate_derivative_lineage_fields(self) -> DeliveryManifest:
        lineage_values = (
            self.derivative_certificate_sha256,
            self.derivative_payload_sha256,
        )
        if self.artifact_kind in _DERIVATIVE_DELIVERY_KINDS:
            if any(value is None for value in lineage_values):
                raise ValueError(
                    "derivative delivery manifests require exact certificate and payload digests"
                )
        elif self.model_fields_set & {
            "derivative_certificate_sha256",
            "derivative_payload_sha256",
        }:
            raise ValueError(
                "deterministic delivery manifests forbid derivative lineage fields"
            )
        return self

    def authentication_payload(self) -> bytes:
        payload = self.model_dump(
            mode="json",
            exclude={"signature_sha256"},
            exclude_none=True,
        )
        return canonical_json_bytes(payload)

    def require_authenticated(self, trust: HmacQualityTrust) -> None:
        try:
            trust.require_valid(
                payload=self.authentication_payload(),
                signature=self.signature_sha256,
                key_id=self.trust_key_id,
            )
        except QualityTrustError as error:
            raise DeliveryVerificationError(
                "delivery manifest authentication failed"
            ) from error


@dataclass(frozen=True, slots=True)
class VerifiedDelivery:
    """Delivery manifest and paths proven against the current workspace bytes."""

    manifest: DeliveryManifest
    manifest_sha256: str
    output_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _VerifiedDerivativeLineage:
    certificate: DerivativeQualityCertificate
    certificate_sha256: str
    payload_sha256: str
    execution_identity: DeliveryExecutionIdentity


@dataclass(frozen=True, slots=True)
class _FileFingerprint:
    sha256: str
    byte_size: int


class DeliveryManifestVerifier:
    """Verifies a delivery manifest without trusting producer assertions."""

    def __init__(
        self,
        *,
        manifests_root: Path = DELIVERY_MANIFESTS_ROOT,
        certificates_root: Path = QUALITY_CERTIFICATES_ROOT,
        canonical_documents_root: Path = CANONICAL_DOCUMENTS_ROOT,
        metadata_root: Path = METADATA_ROOT,
        output_roots: Mapping[DeliveryArtifactKind, Path] | None = None,
        trust: HmacQualityTrust | None = None,
        quality_certificate_verifier: QualityCertificateVerifier | None = None,
        derivative_certificate_verifier: (
            DerivativeQualityCertificateVerifier | None
        ) = None,
        audio_runtime_identity_resolver: AudioRuntimeIdentityResolver | None = None,
    ) -> None:
        configured_output_roots = (
            DELIVERY_OUTPUT_ROOTS if output_roots is None else output_roots
        )
        if frozenset(configured_output_roots) != frozenset(DeliveryArtifactKind):
            raise ValueError(
                "output_roots must define every closed artifact kind exactly"
            )
        self._manifests = RootedArtifactStore(manifests_root)
        self._certificates_root = certificates_root
        self._canonical_documents_root = canonical_documents_root
        self._metadata_root = metadata_root
        self._certificates = RootedArtifactStore(certificates_root)
        self._canonical_documents = RootedArtifactStore(canonical_documents_root)
        self._trust = trust
        self._quality_certificate_verifier = quality_certificate_verifier
        self._derivative_certificate_verifier = derivative_certificate_verifier
        self._audio_runtime_identity_resolver = audio_runtime_identity_resolver
        self._output_roots = {
            kind: RootedArtifactStore(configured_output_roots[kind])
            for kind in DeliveryArtifactKind
        }

    def verify(
        self,
        lecture_id: str,
        artifact_kind: DeliveryArtifactKind,
        expected_execution_identity: DeliveryExecutionIdentity,
    ) -> VerifiedDelivery:
        validated_lecture_id = str(_LECTURE_ID_ADAPTER.validate_python(lecture_id))
        if not isinstance(artifact_kind, DeliveryArtifactKind):
            raise DeliveryVerificationError("artifact kind is not registered")
        if artifact_kind not in _DETERMINISTIC_DELIVERY_KINDS:
            raise DeliveryVerificationError(
                "derivative deliveries require the typed verifier API"
            )
        if not isinstance(expected_execution_identity, DeliveryExecutionIdentity):
            raise DeliveryVerificationError("execution identity is not typed")

        manifest_bytes = self._manifests.read_file(
            (validated_lecture_id, f"{artifact_kind.value}.json"),
            maximum_bytes=_MAX_MANIFEST_BYTES,
        )
        manifest = self._parse_manifest(manifest_bytes)
        trust = self._resolve_trust()
        manifest.require_authenticated(trust)
        self._validate_manifest_identity(
            manifest,
            validated_lecture_id,
            artifact_kind,
            expected_execution_identity,
        )
        verified_source = self._verify_source_certification(
            validated_lecture_id,
            trust,
        )
        self._validate_parent_artifacts(
            manifest,
            validated_lecture_id,
            verified_source,
            trust,
        )
        output_paths = self._validate_outputs(manifest, validated_lecture_id)
        return VerifiedDelivery(
            manifest=manifest,
            manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            output_paths=output_paths,
        )

    def is_current(
        self,
        lecture_id: str,
        artifact_kind: DeliveryArtifactKind,
        expected_execution_identity: DeliveryExecutionIdentity,
    ) -> bool:
        try:
            self.verify(
                lecture_id,
                artifact_kind,
                expected_execution_identity,
            )
        except (DeliveryVerificationError, ValueError):
            return False
        return True

    def verify_mock_exam(self, lecture_id: str) -> VerifiedDelivery:
        return self._verify_derivative_delivery(
            lecture_id,
            DeliveryArtifactKind.MOCK_EXAM,
        )

    def verify_podcast_script(self, lecture_id: str) -> VerifiedDelivery:
        return self._verify_derivative_delivery(
            lecture_id,
            DeliveryArtifactKind.PODCAST_SCRIPT,
        )

    def verify_podcast_audio(self, lecture_id: str) -> VerifiedDelivery:
        return self._verify_derivative_delivery(
            lecture_id,
            DeliveryArtifactKind.PODCAST_AUDIO,
        )

    def is_mock_exam_current(self, lecture_id: str) -> bool:
        return self._is_derivative_delivery_current(
            lecture_id,
            DeliveryArtifactKind.MOCK_EXAM,
        )

    def is_podcast_script_current(self, lecture_id: str) -> bool:
        return self._is_derivative_delivery_current(
            lecture_id,
            DeliveryArtifactKind.PODCAST_SCRIPT,
        )

    def is_podcast_audio_current(self, lecture_id: str) -> bool:
        return self._is_derivative_delivery_current(
            lecture_id,
            DeliveryArtifactKind.PODCAST_AUDIO,
        )

    def _is_derivative_delivery_current(
        self,
        lecture_id: str,
        artifact_kind: DeliveryArtifactKind,
    ) -> bool:
        try:
            self._verify_derivative_delivery(lecture_id, artifact_kind)
        except (DeliveryVerificationError, ValueError):
            return False
        return True

    def _verify_derivative_delivery(
        self,
        lecture_id: str,
        artifact_kind: DeliveryArtifactKind,
    ) -> VerifiedDelivery:
        validated_lecture_id = str(_LECTURE_ID_ADAPTER.validate_python(lecture_id))
        manifest_bytes = self._manifests.read_file(
            (validated_lecture_id, f"{artifact_kind.value}.json"),
            maximum_bytes=_MAX_MANIFEST_BYTES,
        )
        manifest = self._parse_manifest(manifest_bytes)
        trust = self._resolve_trust()
        manifest.require_authenticated(trust)
        self._validate_manifest_subject(
            manifest,
            validated_lecture_id,
            artifact_kind,
        )
        verified_source = self._verify_source_certification(
            validated_lecture_id,
            trust,
        )
        self._validate_parent_artifacts(
            manifest,
            validated_lecture_id,
            verified_source,
            trust,
        )
        payload = self._load_derivative_payload(
            validated_lecture_id,
            artifact_kind,
        )
        lineage = self._verify_derivative_lineage(
            validated_lecture_id,
            artifact_kind,
            payload,
            trust,
        )
        self._validate_manifest_identity(
            manifest,
            validated_lecture_id,
            artifact_kind,
            lineage.execution_identity,
        )
        self._validate_manifest_derivative_lineage(manifest, lineage)
        output_paths = self._validate_outputs(manifest, validated_lecture_id)
        return VerifiedDelivery(
            manifest=manifest,
            manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            output_paths=output_paths,
        )

    def _load_derivative_payload(
        self,
        lecture_id: str,
        artifact_kind: DeliveryArtifactKind,
    ) -> MockExam | PodcastScript:
        if artifact_kind is DeliveryArtifactKind.MOCK_EXAM:
            payload_bytes = self._output_roots[artifact_kind].read_file(
                (lecture_id, "exam.json"),
                maximum_bytes=_MAX_DERIVATIVE_PAYLOAD_BYTES,
            )
            payload_type: type[MockExam] | type[PodcastScript] = MockExam
        else:
            payload_bytes = self._output_roots[
                DeliveryArtifactKind.PODCAST_SCRIPT
            ].read_file(
                (lecture_id, "script.json"),
                maximum_bytes=_MAX_DERIVATIVE_PAYLOAD_BYTES,
            )
            payload_type = PodcastScript
        try:
            return payload_type.model_validate_json(
                _normalize_strict_json(payload_bytes)
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise DeliveryVerificationError(
                "derivative delivery payload contains invalid or ambiguous JSON"
            ) from error

    def _verify_derivative_lineage(
        self,
        lecture_id: str,
        artifact_kind: DeliveryArtifactKind,
        payload: MockExam | PodcastScript,
        trust: HmacQualityTrust,
    ) -> _VerifiedDerivativeLineage:
        verifier = self._derivative_certificate_verifier
        if verifier is None:
            verifier = DerivativeQualityCertificateVerifier(
                certificates_root=self._certificates_root,
                canonical_documents_root=self._canonical_documents_root,
                metadata_root=self._metadata_root,
                trust=trust,
                quality_certificate_verifier=self._quality_certificate_verifier,
            )
        try:
            if isinstance(payload, MockExam):
                verified_certificate = verifier.verify_exam(payload)
            else:
                verified_certificate = verifier.verify_podcast_script(payload)
        except DerivativeQualityError as error:
            raise DeliveryVerificationError(
                f"derivative source certification changed or is invalid: {error}"
            ) from error
        certificate_bytes = self._read_derivative_certificate(
            lecture_id,
            artifact_kind,
        )
        certificate = self._parse_derivative_certificate(certificate_bytes)
        if certificate != verified_certificate:
            raise DeliveryVerificationError(
                "verified derivative certificate differs from the exact delivery parent"
            )
        payload_sha256 = hashlib.sha256(canonical_model_bytes(payload)).hexdigest()
        execution_identity = self._resolve_derivative_delivery_identity(
            lecture_id,
            artifact_kind,
            certificate,
        )
        return _VerifiedDerivativeLineage(
            certificate=certificate,
            certificate_sha256=hashlib.sha256(certificate_bytes).hexdigest(),
            payload_sha256=payload_sha256,
            execution_identity=execution_identity,
        )

    def _read_derivative_certificate(
        self,
        lecture_id: str,
        artifact_kind: DeliveryArtifactKind,
    ) -> bytes:
        derivative_kind = _DERIVATIVE_ARTIFACT_KINDS[artifact_kind]
        return self._certificates.read_file(
            (
                lecture_id,
                "derivatives",
                derivative_kind.value,
                "certificate.json",
            ),
            maximum_bytes=_MAX_DERIVATIVE_CERTIFICATE_BYTES,
        )

    def _resolve_derivative_delivery_identity(
        self,
        lecture_id: str,
        artifact_kind: DeliveryArtifactKind,
        certificate: DerivativeQualityCertificate,
    ) -> DeliveryExecutionIdentity:
        if artifact_kind is DeliveryArtifactKind.PODCAST_AUDIO:
            if self._audio_runtime_identity_resolver is None:
                raise DeliveryVerificationError(
                    "audio runtime identity resolver is not configured"
                )
            identity = self._audio_runtime_identity_resolver(
                lecture_id,
                artifact_kind,
            )
            if not isinstance(identity, DeliveryExecutionIdentity):
                raise DeliveryVerificationError(
                    "audio runtime identity resolver returned an invalid identity"
                )
            return identity
        return _delivery_identity_from_derivative_certificate(
            certificate,
            artifact_kind,
        )

    @staticmethod
    def _validate_manifest_derivative_lineage(
        manifest: DeliveryManifest,
        lineage: _VerifiedDerivativeLineage,
    ) -> None:
        if (
            manifest.derivative_certificate_sha256 != lineage.certificate_sha256
            or manifest.derivative_payload_sha256 != lineage.payload_sha256
            or lineage.certificate.payload_sha256 != lineage.payload_sha256
        ):
            raise DeliveryVerificationError(
                "derivative certificate or payload changed after delivery"
            )

    @staticmethod
    def _parse_derivative_certificate(
        certificate_bytes: bytes,
    ) -> DerivativeQualityCertificate:
        try:
            return DerivativeQualityCertificate.model_validate_json(
                _normalize_strict_json(certificate_bytes)
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise DeliveryVerificationError(
                "derivative certificate contains invalid or ambiguous JSON"
            ) from error

    @staticmethod
    def _parse_manifest(manifest_bytes: bytes) -> DeliveryManifest:
        try:
            return DeliveryManifest.model_validate_json(
                _normalize_strict_json(manifest_bytes)
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise DeliveryVerificationError(
                "delivery manifest contains invalid or ambiguous JSON"
            ) from error

    @staticmethod
    def _validate_manifest_subject(
        manifest: DeliveryManifest,
        lecture_id: str,
        artifact_kind: DeliveryArtifactKind,
    ) -> None:
        if str(manifest.lecture_id) != lecture_id:
            raise DeliveryVerificationError(
                "delivery manifest belongs to a different lecture"
            )
        if manifest.artifact_kind is not artifact_kind:
            raise DeliveryVerificationError(
                "delivery manifest belongs to a different artifact kind"
            )

    @staticmethod
    def _validate_manifest_identity(
        manifest: DeliveryManifest,
        lecture_id: str,
        artifact_kind: DeliveryArtifactKind,
        expected_execution_identity: DeliveryExecutionIdentity,
    ) -> None:
        DeliveryManifestVerifier._validate_manifest_subject(
            manifest,
            lecture_id,
            artifact_kind,
        )
        if manifest.execution_identity != expected_execution_identity:
            raise DeliveryVerificationError(
                "delivery execution identity changed after generation"
            )

    def _verify_source_certification(
        self,
        lecture_id: str,
        trust: HmacQualityTrust,
    ) -> VerifiedPublicationInputs:
        verifier = self._quality_certificate_verifier
        if verifier is None:
            verifier = QualityCertificateVerifier(
                certificates_root=self._certificates_root,
                canonical_documents_root=self._canonical_documents_root,
                metadata_root=self._metadata_root,
                trust=trust,
            )
        try:
            return verifier.verify(lecture_id)
        except QualityCertificationError as error:
            raise DeliveryVerificationError(
                f"parent source certification changed or is invalid: {error}"
            ) from error

    def _validate_parent_artifacts(
        self,
        manifest: DeliveryManifest,
        lecture_id: str,
        verified_source: VerifiedPublicationInputs,
        trust: HmacQualityTrust,
    ) -> None:
        certificate_bytes = self._certificates.read_file(
            (lecture_id, "certificate.json"),
            maximum_bytes=_MAX_CERTIFICATE_BYTES,
        )
        certificate_digest = hashlib.sha256(certificate_bytes).hexdigest()
        if certificate_digest != manifest.parent_certificate_sha256:
            raise DeliveryVerificationError(
                "parent publication quality certificate changed after delivery"
            )
        certificate = self._parse_parent_certificate(certificate_bytes)
        if certificate != verified_source.certificate:
            raise DeliveryVerificationError(
                "verified parent certificate differs from the exact delivery parent"
            )
        try:
            certificate.require_authenticated(trust)
        except QualityCertificationError as error:
            raise DeliveryVerificationError(
                "parent publication quality certificate is not authenticated"
            ) from error
        if str(certificate.lecture_id) != lecture_id:
            raise DeliveryVerificationError(
                "parent publication quality certificate belongs to another lecture"
            )

        canonical_bytes = self._canonical_documents.read_file(
            (lecture_id, "canonical_document.json"),
            maximum_bytes=_MAX_CANONICAL_DOCUMENT_BYTES,
        )
        canonical_document = self._parse_canonical_document(canonical_bytes)
        if canonical_document != verified_source.document:
            raise DeliveryVerificationError(
                "verified canonical document differs from the exact delivery parent"
            )
        if str(canonical_document.lecture_id) != lecture_id:
            raise DeliveryVerificationError(
                "canonical document belongs to another lecture"
            )
        canonical_digest = hashlib.sha256(canonical_bytes).hexdigest()
        if canonical_digest != manifest.canonical_document_sha256:
            raise DeliveryVerificationError("canonical document changed after delivery")
        if canonical_digest != certificate.canonical_document_sha256:
            raise DeliveryVerificationError(
                "parent certificate no longer identifies the canonical document"
            )

    @staticmethod
    def _parse_canonical_document(
        canonical_bytes: bytes,
    ) -> CanonicalLectureDocument:
        try:
            return CanonicalLectureDocument.model_validate_json(
                _normalize_strict_json(canonical_bytes)
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise DeliveryVerificationError(
                "canonical document contains invalid or ambiguous JSON"
            ) from error

    def _resolve_trust(self) -> HmacQualityTrust:
        if self._trust is not None:
            return self._trust
        try:
            return HmacQualityTrust.from_environment()
        except QualityTrustError as error:
            raise DeliveryVerificationError(
                "quality certificate trust root is not configured"
            ) from error

    @staticmethod
    def _parse_parent_certificate(
        certificate_bytes: bytes,
    ) -> PublicationQualityCertificate:
        try:
            return PublicationQualityCertificate.model_validate_json(
                _normalize_strict_json(certificate_bytes)
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise DeliveryVerificationError(
                "parent publication quality certificate contains invalid JSON"
            ) from error

    def _validate_outputs(
        self,
        manifest: DeliveryManifest,
        lecture_id: str,
    ) -> tuple[Path, ...]:
        output_root = self._output_roots[manifest.artifact_kind]
        observed_outputs, observed_directories = output_root.fingerprint_tree(
            (lecture_id,)
        )
        expected_paths = tuple(output.relative_path for output in manifest.outputs)
        expected_directories = _expected_directories(expected_paths)
        if (
            tuple(observed_outputs) != expected_paths
            or observed_directories != expected_directories
        ):
            raise DeliveryVerificationError(
                "delivery output set differs from the exact manifest output set"
            )

        for output in manifest.outputs:
            observed = observed_outputs[output.relative_path]
            if (
                observed.sha256 != output.sha256
                or observed.byte_size != output.byte_size
            ):
                raise DeliveryVerificationError(
                    f"delivery output changed after generation: {output.relative_path}"
                )
        return tuple(
            output_root.root / lecture_id / output.relative_path
            for output in manifest.outputs
        )


class RootedArtifactStore:
    """Reads one configured tree without following links or accepting aliases."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def read_file(
        self,
        relative_parts: tuple[str, ...],
        *,
        maximum_bytes: int | None = None,
    ) -> bytes:
        with self._open_regular_file(relative_parts) as (
            descriptor,
            status_before,
        ):
            if maximum_bytes is not None and status_before.st_size > maximum_bytes:
                raise DeliveryVerificationError(
                    "delivery control file exceeds its maximum size"
                )
            chunks: list[bytes] = []
            byte_count = 0
            while chunk := os.read(descriptor, 1024 * 1024):
                chunks.append(chunk)
                byte_count += len(chunk)
                if maximum_bytes is not None and byte_count > maximum_bytes:
                    raise DeliveryVerificationError(
                        "delivery control file exceeds its maximum size"
                    )
            self._require_unchanged(descriptor, status_before)
            return b"".join(chunks)

    def write_file_atomically(
        self,
        relative_parts: tuple[str, ...],
        payload: bytes,
    ) -> None:
        with self.reversible_file_transaction(relative_parts, payload):
            pass

    @contextmanager
    def reversible_file_transaction(
        self,
        relative_parts: tuple[str, ...],
        payload: bytes,
        *,
        commit_state: _DeliveryCommitState | None = None,
    ) -> Iterator[None]:
        if not relative_parts:
            raise DeliveryVerificationError(
                "delivery control file path must not be empty"
            )
        for part in relative_parts:
            _validate_path_part(part)
        if not isinstance(payload, bytes):
            raise DeliveryVerificationError(
                "delivery control file payload must be bytes"
            )

        parent_parts = relative_parts[:-1]
        file_name = relative_parts[-1]
        with self._open_or_create_directory(parent_parts) as parent_descriptor:
            with self._lock_delivery_parent(parent_descriptor):
                self._recover_abandoned_file_staging(
                    parent_descriptor,
                    file_name,
                )
                staged_name = self._stage_file(
                    parent_descriptor,
                    file_name,
                    payload,
                )
                try:
                    previous_status = self._promote_staged_file(
                        parent_descriptor,
                        staged_name,
                        file_name,
                    )
                except _RetainStagedDeliveryError:
                    self._quarantine_staged_file(
                        parent_descriptor,
                        staged_name,
                    )
                    raise
                except BaseException:
                    self._remove_staged_file_if_present(
                        parent_descriptor,
                        staged_name,
                    )
                    raise

                try:
                    yield
                except BaseException:
                    if commit_state is not None and commit_state.is_committed:
                        self._discard_staged_file_after_commit(
                            parent_descriptor,
                            staged_name,
                        )
                        raise
                    try:
                        self._rollback_committed_file(
                            parent_descriptor,
                            staged_name,
                            file_name,
                            previous_status,
                        )
                    except _RetainStagedDeliveryError:
                        if previous_status is not None:
                            self._quarantine_staged_file(
                                parent_descriptor,
                                staged_name,
                            )
                        raise
                    self._remove_staged_file_if_present(
                        parent_descriptor,
                        staged_name,
                    )
                    raise
                else:
                    self._discard_staged_file_after_commit(
                        parent_descriptor,
                        staged_name,
                    )

    def _recover_abandoned_file_staging(
        self,
        parent_descriptor: int,
        file_name: str,
    ) -> None:
        prefix = f".{file_name}."
        entry_names = os.listdir(parent_descriptor)
        if any(
            self._has_transaction_name(entry_name, prefix, ".recovery")
            for entry_name in entry_names
        ):
            raise DeliveryVerificationError(
                "delivery control file has quarantined state requiring recovery"
            )
        staged_names = [
            entry_name
            for entry_name in entry_names
            if self._has_transaction_name(entry_name, prefix, ".tmp")
        ]
        if not staged_names:
            return
        if self._existing_control_file_status(parent_descriptor, file_name) is None:
            for staged_name in staged_names:
                os.unlink(staged_name, dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)
            return
        for staged_name in staged_names:
            self._quarantine_staged_file(parent_descriptor, staged_name)
        raise DeliveryVerificationError(
            "ambiguous delivery control files were quarantined for recovery"
        )

    @staticmethod
    def _existing_control_file_status(
        parent_descriptor: int,
        file_name: str,
    ) -> os.stat_result | None:
        try:
            status = os.stat(
                file_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
            raise DeliveryVerificationError(
                "existing delivery control file is missing or unsafe"
            )
        return status

    def _promote_staged_file(
        self,
        parent_descriptor: int,
        staged_name: str,
        file_name: str,
    ) -> os.stat_result | None:
        previous_status = self._existing_control_file_status(
            parent_descriptor,
            file_name,
        )
        if previous_status is None:
            os.replace(
                staged_name,
                file_name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
        else:
            _exchange_entries_atomically(
                parent_descriptor,
                staged_name,
                file_name,
            )
        try:
            os.fsync(parent_descriptor)
        except OSError as commit_error:
            self._rollback_committed_file(
                parent_descriptor,
                staged_name,
                file_name,
                previous_status,
            )
            raise commit_error
        return previous_status

    def _rollback_committed_file(
        self,
        parent_descriptor: int,
        staged_name: str,
        file_name: str,
        previous_status: os.stat_result | None,
    ) -> None:
        if previous_status is None:
            self._withdraw_uncertain_first_delivery(
                parent_descriptor,
                staged_name,
                file_name,
            )
            return
        self._restore_previous_delivery_after_failed_commit(
            parent_descriptor,
            staged_name,
            file_name,
            previous_status,
        )

    @staticmethod
    def _quarantine_staged_file(
        parent_descriptor: int,
        staged_name: str,
    ) -> None:
        if not staged_name.endswith(".tmp"):
            raise DeliveryVerificationError(
                "delivery control staging file name is invalid"
            )
        recovery_name = f"{staged_name[: -len('.tmp')]}.recovery"
        os.replace(
            staged_name,
            recovery_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        os.fsync(parent_descriptor)

    @staticmethod
    def _remove_staged_file_if_present(
        parent_descriptor: int,
        staged_name: str,
    ) -> None:
        try:
            os.unlink(staged_name, dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)
        except FileNotFoundError:
            pass

    def _discard_staged_file_after_commit(
        self,
        parent_descriptor: int,
        staged_name: str,
    ) -> None:
        try:
            self._remove_staged_file_if_present(
                parent_descriptor,
                staged_name,
            )
        except OSError:
            pass

    def write_files_atomically(
        self,
        relative_directory_parts: tuple[str, ...],
        files: Mapping[str, bytes],
    ) -> None:
        with self.reversible_files_transaction(
            relative_directory_parts,
            files,
        ):
            pass

    @contextmanager
    def reversible_files_transaction(
        self,
        relative_directory_parts: tuple[str, ...],
        files: Mapping[str, bytes],
        *,
        commit_state: _DeliveryCommitState | None = None,
    ) -> Iterator[None]:
        self._validate_write_request(relative_directory_parts, files)
        parent_parts = relative_directory_parts[:-1]
        target_directory_name = relative_directory_parts[-1]
        with self._open_or_create_directory(parent_parts) as parent_descriptor:
            with self._lock_delivery_parent(parent_descriptor):
                self._recover_abandoned_staging_directories(
                    parent_descriptor,
                    target_directory_name,
                )
                staged_name = self._create_staging_directory(
                    parent_descriptor,
                    target_directory_name,
                )
                try:
                    self._populate_staging_directory(
                        parent_descriptor,
                        staged_name,
                        files,
                    )
                    previous_status = self._promote_staged_directory(
                        parent_descriptor,
                        staged_name,
                        target_directory_name,
                    )
                except _RetainStagedDeliveryError:
                    self._quarantine_staged_directory(
                        parent_descriptor,
                        staged_name,
                    )
                    raise
                except BaseException:
                    self._remove_staging_directory_if_present(
                        parent_descriptor,
                        staged_name,
                    )
                    raise

                try:
                    yield
                except BaseException:
                    if commit_state is not None and commit_state.is_committed:
                        self._discard_staging_directory_after_commit(
                            parent_descriptor,
                            staged_name,
                        )
                        raise
                    try:
                        self._rollback_committed_files(
                            parent_descriptor,
                            staged_name,
                            target_directory_name,
                            previous_status,
                        )
                    except _RetainStagedDeliveryError:
                        if previous_status is not None:
                            self._quarantine_staged_directory(
                                parent_descriptor,
                                staged_name,
                            )
                        raise
                    self._remove_staging_directory_if_present(
                        parent_descriptor,
                        staged_name,
                    )
                    raise
                else:
                    self._discard_staging_directory_after_commit(
                        parent_descriptor,
                        staged_name,
                    )

    def _rollback_committed_files(
        self,
        parent_descriptor: int,
        staged_name: str,
        target_directory_name: str,
        previous_status: os.stat_result | None,
    ) -> None:
        if previous_status is None:
            self._withdraw_uncertain_first_delivery(
                parent_descriptor,
                staged_name,
                target_directory_name,
            )
            return
        self._restore_previous_delivery_after_failed_commit(
            parent_descriptor,
            staged_name,
            target_directory_name,
            previous_status,
        )

    def fingerprint_tree(
        self,
        relative_parts: tuple[str, ...],
    ) -> tuple[dict[str, _FileFingerprint], frozenset[str]]:
        with self._open_directory(relative_parts) as descriptor:
            files: dict[str, _FileFingerprint] = {}
            directories: set[str] = set()
            self._fingerprint_directory(
                descriptor,
                PurePosixPath(),
                files,
                directories,
            )
        return dict(sorted(files.items())), frozenset(directories)

    def _fingerprint_directory(
        self,
        descriptor: int,
        relative_directory: PurePosixPath,
        files: dict[str, _FileFingerprint],
        directories: set[str],
    ) -> None:
        status_before = os.fstat(descriptor)
        for entry_name in sorted(os.listdir(descriptor)):
            _validate_path_part(entry_name)
            entry_status = os.stat(
                entry_name,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
            relative_path = relative_directory / entry_name
            relative_text = relative_path.as_posix()
            if stat.S_ISLNK(entry_status.st_mode):
                raise DeliveryVerificationError(
                    f"delivery output must not be a symbolic link: {relative_text}"
                )
            if stat.S_ISREG(entry_status.st_mode):
                files[relative_text] = self._fingerprint_entry(
                    descriptor,
                    entry_name,
                    entry_status,
                )
                continue
            if stat.S_ISDIR(entry_status.st_mode):
                directories.add(relative_text)
                self._fingerprint_child_directory(
                    descriptor,
                    entry_name,
                    entry_status,
                    relative_path,
                    files,
                    directories,
                )
                continue
            raise DeliveryVerificationError(
                f"delivery output is not a regular file: {relative_text}"
            )
        self._require_unchanged(descriptor, status_before)

    def _fingerprint_child_directory(
        self,
        parent_descriptor: int,
        entry_name: str,
        entry_status: os.stat_result,
        relative_path: PurePosixPath,
        files: dict[str, _FileFingerprint],
        directories: set[str],
    ) -> None:
        child_descriptor = self._open_entry(
            parent_descriptor,
            entry_name,
            directory=True,
        )
        try:
            self._require_same_inode(child_descriptor, entry_status)
            self._fingerprint_directory(
                child_descriptor,
                relative_path,
                files,
                directories,
            )
        finally:
            os.close(child_descriptor)

    def _fingerprint_entry(
        self,
        parent_descriptor: int,
        entry_name: str,
        entry_status: os.stat_result,
    ) -> _FileFingerprint:
        if entry_status.st_nlink != 1:
            raise DeliveryVerificationError(
                f"delivery output must have exactly one hard link: {entry_name}"
            )
        descriptor = self._open_entry(
            parent_descriptor,
            entry_name,
            directory=False,
        )
        try:
            self._require_same_inode(descriptor, entry_status)
            digest = hashlib.sha256()
            byte_count = 0
            while chunk := os.read(descriptor, 1024 * 1024):
                digest.update(chunk)
                byte_count += len(chunk)
            self._require_unchanged(descriptor, entry_status)
            return _FileFingerprint(
                sha256=digest.hexdigest(),
                byte_size=byte_count,
            )
        finally:
            os.close(descriptor)

    @contextmanager
    def _open_regular_file(
        self,
        relative_parts: tuple[str, ...],
    ) -> Iterator[tuple[int, os.stat_result]]:
        if not relative_parts:
            raise DeliveryVerificationError("file path must not be empty")
        with self._open_directory(relative_parts[:-1]) as parent_descriptor:
            file_name = relative_parts[-1]
            _validate_path_part(file_name)
            try:
                entry_status = os.stat(
                    file_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            except OSError as error:
                raise DeliveryVerificationError(
                    f"required delivery artifact is missing: {file_name}"
                ) from error
            if stat.S_ISLNK(entry_status.st_mode):
                raise DeliveryVerificationError(
                    f"delivery artifact must not be a symbolic link: {file_name}"
                )
            if not stat.S_ISREG(entry_status.st_mode):
                raise DeliveryVerificationError(
                    f"delivery artifact is not a regular file: {file_name}"
                )
            if entry_status.st_nlink != 1:
                raise DeliveryVerificationError(
                    f"delivery artifact must have exactly one hard link: {file_name}"
                )
            descriptor = self._open_entry(
                parent_descriptor,
                file_name,
                directory=False,
            )
            try:
                self._require_same_inode(descriptor, entry_status)
                yield descriptor, entry_status
            finally:
                os.close(descriptor)

    @contextmanager
    def _open_directory(
        self,
        relative_parts: tuple[str, ...],
    ) -> Iterator[int]:
        descriptors: list[int] = []
        try:
            current_descriptor = self._open_root()
            descriptors.append(current_descriptor)
            for part in relative_parts:
                _validate_path_part(part)
                current_descriptor = self._open_entry(
                    current_descriptor,
                    part,
                    directory=True,
                )
                descriptors.append(current_descriptor)
            yield current_descriptor
        except OSError as error:
            raise DeliveryVerificationError(
                "required delivery directory is missing or unsafe"
            ) from error
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def _open_root(self) -> int:
        return self._walk_root(create_missing=False)

    @staticmethod
    def _open_entry(
        parent_descriptor: int,
        entry_name: str,
        *,
        directory: bool,
    ) -> int:
        return os.open(
            entry_name,
            _open_flags(directory=directory),
            dir_fd=parent_descriptor,
        )

    @staticmethod
    def _require_same_inode(
        descriptor: int,
        expected_status: os.stat_result,
    ) -> None:
        observed_status = os.fstat(descriptor)
        if (
            observed_status.st_dev,
            observed_status.st_ino,
        ) != (
            expected_status.st_dev,
            expected_status.st_ino,
        ):
            raise DeliveryVerificationError(
                "delivery artifact changed while being opened"
            )

    @staticmethod
    def _require_unchanged(
        descriptor: int,
        expected_status: os.stat_result,
    ) -> None:
        observed_status = os.fstat(descriptor)
        expected_identity = (
            expected_status.st_dev,
            expected_status.st_ino,
            expected_status.st_size,
            expected_status.st_mtime_ns,
        )
        observed_identity = (
            observed_status.st_dev,
            observed_status.st_ino,
            observed_status.st_size,
            observed_status.st_mtime_ns,
        )
        if observed_identity != expected_identity:
            raise DeliveryVerificationError(
                "delivery artifact changed while being verified"
            )

    @staticmethod
    @contextmanager
    def _lock_delivery_parent(parent_descriptor: int) -> Iterator[None]:
        fcntl.flock(parent_descriptor, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(parent_descriptor, fcntl.LOCK_UN)

    def _recover_abandoned_staging_directories(
        self,
        parent_descriptor: int,
        target_directory_name: str,
    ) -> None:
        prefix = f".{target_directory_name}."
        staging_suffix = ".staging"
        recovery_suffix = ".recovery"
        entry_names = os.listdir(parent_descriptor)
        recovery_names = [
            entry_name
            for entry_name in entry_names
            if self._has_transaction_name(
                entry_name,
                prefix,
                recovery_suffix,
            )
        ]
        if recovery_names:
            raise DeliveryVerificationError(
                "delivery has a quarantined generation requiring recovery"
            )

        staging_names = [
            entry_name
            for entry_name in entry_names
            if self._has_transaction_name(
                entry_name,
                prefix,
                staging_suffix,
            )
        ]
        if not staging_names:
            return

        if (
            self._existing_delivery_status(
                parent_descriptor,
                target_directory_name,
            )
            is None
        ):
            for staging_name in staging_names:
                self._remove_entry_tree(parent_descriptor, staging_name)
            os.fsync(parent_descriptor)
            return

        for staging_name in staging_names:
            self._quarantine_staged_directory(
                parent_descriptor,
                staging_name,
            )
        raise DeliveryVerificationError(
            "ambiguous delivery generations were quarantined for recovery"
        )

    @staticmethod
    def _has_transaction_name(
        entry_name: str,
        prefix: str,
        suffix: str,
    ) -> bool:
        if not entry_name.startswith(prefix) or not entry_name.endswith(suffix):
            return False
        token = entry_name[len(prefix) : -len(suffix)]
        return len(token) == 32 and all(
            character in "0123456789abcdef" for character in token
        )

    @staticmethod
    def _quarantine_staged_directory(
        parent_descriptor: int,
        staged_name: str,
    ) -> None:
        if not staged_name.endswith(".staging"):
            raise DeliveryVerificationError(
                "delivery staging directory name is invalid"
            )
        recovery_name = f"{staged_name[: -len('.staging')]}.recovery"
        os.replace(
            staged_name,
            recovery_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        os.fsync(parent_descriptor)

    @staticmethod
    def _validate_write_request(
        relative_directory_parts: tuple[str, ...],
        files: Mapping[str, bytes],
    ) -> None:
        if not relative_directory_parts:
            raise DeliveryVerificationError(
                "delivery output directory must not be empty"
            )
        if not files:
            raise DeliveryVerificationError("delivery output set must not be empty")
        for part in relative_directory_parts:
            _validate_path_part(part)
        for file_name, payload in files.items():
            _validate_path_part(file_name)
            if not isinstance(payload, bytes):
                raise DeliveryVerificationError("delivery output payload must be bytes")

    @staticmethod
    def _create_staging_directory(
        parent_descriptor: int,
        target_directory_name: str,
    ) -> str:
        staged_name = f".{target_directory_name}.{secrets.token_hex(16)}.staging"
        os.mkdir(staged_name, mode=0o700, dir_fd=parent_descriptor)
        return staged_name

    def _populate_staging_directory(
        self,
        parent_descriptor: int,
        staged_name: str,
        files: Mapping[str, bytes],
    ) -> None:
        staged_descriptor = self._open_entry(
            parent_descriptor,
            staged_name,
            directory=True,
        )
        try:
            for file_name, payload in files.items():
                temporary_name = self._stage_file(
                    staged_descriptor,
                    file_name,
                    payload,
                )
                os.replace(
                    temporary_name,
                    file_name,
                    src_dir_fd=staged_descriptor,
                    dst_dir_fd=staged_descriptor,
                )
            os.fsync(staged_descriptor)
        finally:
            os.close(staged_descriptor)

    def _promote_staged_directory(
        self,
        parent_descriptor: int,
        staged_name: str,
        target_directory_name: str,
    ) -> os.stat_result | None:
        previous_status = self._existing_delivery_status(
            parent_descriptor,
            target_directory_name,
        )
        if previous_status is None:
            os.replace(
                staged_name,
                target_directory_name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            try:
                os.fsync(parent_descriptor)
            except OSError as commit_error:
                self._withdraw_uncertain_first_delivery(
                    parent_descriptor,
                    staged_name,
                    target_directory_name,
                )
                raise commit_error
            return None

        _exchange_entries_atomically(
            parent_descriptor,
            staged_name,
            target_directory_name,
        )
        try:
            os.fsync(parent_descriptor)
        except OSError as commit_error:
            self._restore_previous_delivery_after_failed_commit(
                parent_descriptor,
                staged_name,
                target_directory_name,
                previous_status,
            )
            raise commit_error
        return previous_status

    @staticmethod
    def _withdraw_uncertain_first_delivery(
        parent_descriptor: int,
        staged_name: str,
        target_directory_name: str,
    ) -> None:
        try:
            os.replace(
                target_directory_name,
                staged_name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            os.fsync(parent_descriptor)
        except OSError as rollback_error:
            raise _RetainStagedDeliveryError(
                "uncertain first delivery could not be withdrawn safely"
            ) from rollback_error

    @staticmethod
    def _restore_previous_delivery_after_failed_commit(
        parent_descriptor: int,
        staged_name: str,
        target_directory_name: str,
        previous_status: os.stat_result,
    ) -> None:
        try:
            _exchange_entries_atomically(
                parent_descriptor,
                staged_name,
                target_directory_name,
            )
            restored_status = os.stat(
                target_directory_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (
                restored_status.st_dev,
                restored_status.st_ino,
            ) != (
                previous_status.st_dev,
                previous_status.st_ino,
            ):
                raise DeliveryVerificationError(
                    "previous complete delivery rollback could not be verified"
                )
            os.fsync(parent_descriptor)
        except (OSError, DeliveryVerificationError) as rollback_error:
            raise _RetainStagedDeliveryError(
                "previous complete delivery rollback failed"
            ) from rollback_error

    @staticmethod
    def _existing_delivery_status(
        parent_descriptor: int,
        target_directory_name: str,
    ) -> os.stat_result | None:
        try:
            status = os.stat(
                target_directory_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return None
        if not stat.S_ISDIR(status.st_mode):
            raise DeliveryVerificationError(
                "existing delivery output directory is missing or unsafe"
            )
        return status

    def _remove_staging_directory_if_present(
        self,
        parent_descriptor: int,
        staged_name: str,
    ) -> None:
        try:
            self._remove_entry_tree(parent_descriptor, staged_name)
            os.fsync(parent_descriptor)
        except FileNotFoundError:
            pass

    def _discard_staging_directory_after_commit(
        self,
        parent_descriptor: int,
        staged_name: str,
    ) -> None:
        try:
            self._remove_staging_directory_if_present(
                parent_descriptor,
                staged_name,
            )
        except (OSError, DeliveryVerificationError):
            pass

    def _remove_entry_tree(
        self,
        parent_descriptor: int,
        entry_name: str,
    ) -> None:
        entry_status = os.stat(
            entry_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if not stat.S_ISDIR(entry_status.st_mode):
            os.unlink(entry_name, dir_fd=parent_descriptor)
            return
        child_descriptor = self._open_entry(
            parent_descriptor,
            entry_name,
            directory=True,
        )
        try:
            self._require_same_inode(child_descriptor, entry_status)
            for child_name in os.listdir(child_descriptor):
                _validate_path_part(child_name)
                self._remove_entry_tree(child_descriptor, child_name)
        finally:
            os.close(child_descriptor)
        os.rmdir(entry_name, dir_fd=parent_descriptor)

    @contextmanager
    def _open_or_create_directory(
        self,
        relative_parts: tuple[str, ...],
    ) -> Iterator[int]:
        descriptors: list[int] = []
        try:
            current_descriptor = self._walk_root(create_missing=True)
            descriptors.append(current_descriptor)
            for part in relative_parts:
                _validate_path_part(part)
                self._ensure_directory(current_descriptor, part)
                current_descriptor = self._open_entry(
                    current_descriptor,
                    part,
                    directory=True,
                )
                descriptors.append(current_descriptor)
            yield current_descriptor
        except OSError as error:
            raise DeliveryVerificationError(
                "delivery output directory is missing or unsafe"
            ) from error
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def _walk_root(self, *, create_missing: bool) -> int:
        flags = _open_flags(directory=True)
        root_parts = self.root.parts
        if self.root.is_absolute():
            descriptor = os.open(self.root.anchor, flags)
            components = root_parts[1:]
        else:
            descriptor = os.open(".", flags)
            components = root_parts

        try:
            for component in components:
                _validate_path_part(component)
                if create_missing:
                    self._ensure_directory(descriptor, component)
                next_descriptor = self._open_entry(
                    descriptor,
                    component,
                    directory=True,
                )
                os.close(descriptor)
                descriptor = next_descriptor
        except (OSError, DeliveryVerificationError):
            os.close(descriptor)
            raise

        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise DeliveryVerificationError(
                "configured delivery root is not a directory"
            )
        return descriptor

    @staticmethod
    def _ensure_directory(parent_descriptor: int, directory_name: str) -> None:
        try:
            os.mkdir(directory_name, mode=0o700, dir_fd=parent_descriptor)
        except FileExistsError:
            pass

    @staticmethod
    def _stage_file(
        parent_descriptor: int,
        file_name: str,
        payload: bytes,
    ) -> str:
        staged_name = f".{file_name}.{secrets.token_hex(16)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
        descriptor = os.open(
            staged_name,
            flags,
            0o600,
            dir_fd=parent_descriptor,
        )
        try:
            RootedArtifactStore._write_all(descriptor, payload)
            os.fsync(descriptor)
        except BaseException:
            try:
                os.close(descriptor)
            finally:
                try:
                    os.unlink(staged_name, dir_fd=parent_descriptor)
                except FileNotFoundError:
                    pass
            raise
        os.close(descriptor)
        return staged_name

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> None:
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise DeliveryVerificationError(
                    "delivery output write made no progress"
                )
            remaining = remaining[written:]


_RENAME_EXCHANGE = 0x00000002


def _exchange_entries_atomically(
    parent_descriptor: int,
    source_name: str,
    target_name: str,
) -> None:
    function_name: str
    if sys.platform == "darwin":
        function_name = "renameatx_np"
    elif sys.platform.startswith("linux"):
        function_name = "renameat2"
    else:
        raise DeliveryVerificationError(
            "atomic delivery directory exchange is unavailable on this platform"
        )

    library = ctypes.CDLL(None, use_errno=True)
    try:
        exchange = getattr(library, function_name)
    except AttributeError as error:
        raise DeliveryVerificationError(
            "atomic delivery directory exchange is unavailable on this platform"
        ) from error

    result = exchange(
        parent_descriptor,
        os.fsencode(source_name),
        parent_descriptor,
        os.fsencode(target_name),
        _RENAME_EXCHANGE,
    )
    if result == 0:
        return

    error_number = ctypes.get_errno()
    raise OSError(
        error_number,
        os.strerror(error_number),
        f"{source_name} <-> {target_name}",
    )


def _open_flags(*, directory: bool) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        raise DeliveryVerificationError("O_NOFOLLOW is required for safe delivery I/O")
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if directory:
        if not hasattr(os, "O_DIRECTORY"):
            raise DeliveryVerificationError(
                "O_DIRECTORY is required for safe delivery I/O"
            )
        flags |= os.O_DIRECTORY
    return flags


def _validate_path_part(part: str) -> None:
    if not part or part in {".", ".."} or "/" in part or "\\" in part or "\x00" in part:
        raise DeliveryVerificationError("delivery path contains an unsafe component")


def _expected_directories(relative_paths: tuple[str, ...]) -> frozenset[str]:
    directories: set[str] = set()
    for relative_path in relative_paths:
        parent = PurePosixPath(relative_path).parent
        while parent != PurePosixPath("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return frozenset(directories)


def _delivery_outputs_from_bytes(
    artifact_kind: DeliveryArtifactKind,
    output_bytes: Mapping[str, bytes],
) -> list[DeliveryOutput]:
    expected_paths = EXPECTED_OUTPUT_RELATIVE_PATHS[artifact_kind]
    if frozenset(output_bytes) != frozenset(expected_paths):
        raise DeliveryVerificationError(
            "delivery output bytes differ from the closed expected set"
        )
    return [
        DeliveryOutput(
            relative_path=relative_path,
            sha256=hashlib.sha256(output_bytes[relative_path]).hexdigest(),
            byte_size=len(output_bytes[relative_path]),
        )
        for relative_path in expected_paths
    ]


def write_delivery_outputs(
    output_root: Path,
    lecture_id: str,
    artifact_kind: DeliveryArtifactKind,
    output_bytes: Mapping[str, bytes],
) -> None:
    validated_lecture_id = str(_LECTURE_ID_ADAPTER.validate_python(lecture_id))
    _delivery_outputs_from_bytes(artifact_kind, output_bytes)
    RootedArtifactStore(output_root).write_files_atomically(
        (validated_lecture_id,),
        output_bytes,
    )


def _parse_strict_json(file_bytes: bytes) -> dict[str, object]:
    decoded = file_bytes.decode("utf-8")

    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise json.JSONDecodeError(
                    f"duplicate JSON key: {key}",
                    decoded,
                    0,
                )
            result[key] = value
        return result

    def reject_non_finite(value: str) -> None:
        raise json.JSONDecodeError(
            f"non-finite JSON value: {value}",
            decoded,
            0,
        )

    parsed = json.loads(
        decoded,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_non_finite,
    )
    if not isinstance(parsed, dict):
        raise json.JSONDecodeError(
            "top-level JSON value must be an object",
            decoded,
            0,
        )
    return cast(dict[str, object], parsed)


def _normalize_strict_json(file_bytes: bytes) -> bytes:
    return json.dumps(
        _parse_strict_json(file_bytes),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


_DETERMINISTIC_DELIVERY_KINDS = frozenset(
    {
        DeliveryArtifactKind.PUBLISHING,
        DeliveryArtifactKind.FLASHCARDS,
    }
)
_DERIVATIVE_DELIVERY_KINDS = frozenset(
    {
        DeliveryArtifactKind.MOCK_EXAM,
        DeliveryArtifactKind.PODCAST_SCRIPT,
        DeliveryArtifactKind.PODCAST_AUDIO,
    }
)
_DERIVATIVE_ARTIFACT_KINDS: Mapping[
    DeliveryArtifactKind,
    DerivativeArtifactKind,
] = {
    DeliveryArtifactKind.MOCK_EXAM: DerivativeArtifactKind.MOCK_EXAM,
    DeliveryArtifactKind.PODCAST_SCRIPT: DerivativeArtifactKind.PODCAST_SCRIPT,
    DeliveryArtifactKind.PODCAST_AUDIO: DerivativeArtifactKind.PODCAST_SCRIPT,
}
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DERIVATIVE_COMPILER_FILES: Mapping[DeliveryArtifactKind, str] = {
    DeliveryArtifactKind.MOCK_EXAM: "src/lecture_study/exams/compiler.py",
    DeliveryArtifactKind.PODCAST_SCRIPT: "src/lecture_study/podcast/compiler.py",
}
_IMPLEMENTATION_FILES: Mapping[DeliveryArtifactKind, tuple[str, ...]] = {
    DeliveryArtifactKind.PUBLISHING: (
        "src/lecture_study/publishing/markdown_compiler.py",
        "src/lecture_study/publishing/pipeline.py",
    ),
    DeliveryArtifactKind.FLASHCARDS: ("src/lecture_study/flashcards/generator.py",),
}
_NOT_APPLICABLE_DIGEST = hashlib.sha256(b"not-applicable").hexdigest()
_PUBLISHING_RENDERER_PRESET = "commonmark"
_PUBLISHING_RENDERER_OPTIONS: Mapping[str, bool] = {"html": False}


class DeliveryManifestIssuer:
    """Issues authenticated manifests only for deterministic deliveries."""

    def __init__(
        self,
        *,
        manifests_root: Path = DELIVERY_MANIFESTS_ROOT,
        certificates_root: Path = QUALITY_CERTIFICATES_ROOT,
        canonical_documents_root: Path = CANONICAL_DOCUMENTS_ROOT,
        metadata_root: Path = METADATA_ROOT,
        output_roots: Mapping[DeliveryArtifactKind, Path] | None = None,
        trust: HmacQualityTrust | None = None,
        quality_certificate_verifier: QualityCertificateVerifier | None = None,
        derivative_certificate_verifier: (
            DerivativeQualityCertificateVerifier | None
        ) = None,
        audio_runtime_identity_resolver: AudioRuntimeIdentityResolver | None = None,
    ) -> None:
        configured_output_roots = dict(
            DELIVERY_OUTPUT_ROOTS if output_roots is None else output_roots
        )
        if frozenset(configured_output_roots) != frozenset(DeliveryArtifactKind):
            raise ValueError("output_roots must define every artifact kind exactly")
        self._manifests_root = manifests_root
        self._certificates_root = certificates_root
        self._canonical_documents_root = canonical_documents_root
        self._metadata_root = metadata_root
        self._output_roots = configured_output_roots
        self._trust = trust
        self._quality_certificate_verifier = quality_certificate_verifier
        self._derivative_certificate_verifier = derivative_certificate_verifier
        self._audio_runtime_identity_resolver = audio_runtime_identity_resolver

    def issue_deterministic(
        self,
        lecture_id: str,
        artifact_kind: DeliveryArtifactKind,
        *,
        expected_output_bytes: Mapping[str, bytes],
    ) -> VerifiedDelivery:
        lecture_id = str(_LECTURE_ID_ADAPTER.validate_python(lecture_id))
        if artifact_kind not in _DETERMINISTIC_DELIVERY_KINDS:
            raise DeliveryVerificationError(
                "LLM and audio deliveries require a derivative quality certificate"
            )
        trust = self._resolve_trust()
        quality_verifier = self._quality_certificate_verifier
        if quality_verifier is None:
            quality_verifier = QualityCertificateVerifier(
                certificates_root=self._certificates_root,
                canonical_documents_root=self._canonical_documents_root,
                metadata_root=self._metadata_root,
                trust=trust,
            )
        verified_source = quality_verifier.verify(lecture_id)
        certificate_bytes = RootedArtifactStore(self._certificates_root).read_file(
            (lecture_id, "certificate.json"),
            maximum_bytes=_MAX_CERTIFICATE_BYTES,
        )
        output_file_system = RootedArtifactStore(self._output_roots[artifact_kind])
        commit_state = _DeliveryCommitState()
        with self._output_transaction(
            output_file_system,
            lecture_id,
            expected_output_bytes,
            commit_state,
        ):
            outputs = self._fingerprint_exact_outputs(
                lecture_id,
                artifact_kind,
                expected_output_bytes,
            )
            identity = deterministic_delivery_execution_identity(artifact_kind)
            manifest = DeliveryManifest(
                schema_version=2,
                lecture_id=lecture_id,
                artifact_kind=artifact_kind,
                parent_certificate_sha256=hashlib.sha256(certificate_bytes).hexdigest(),
                canonical_document_sha256=(
                    verified_source.certificate.canonical_document_sha256
                ),
                execution_identity=identity,
                outputs=outputs,
                trust_key_id=trust.key_id,
                signature_sha256="0" * 64,
            )
            manifest = manifest.model_copy(
                update={
                    "signature_sha256": trust.sign(manifest.authentication_payload())
                }
            )
            verifier = DeliveryManifestVerifier(
                manifests_root=self._manifests_root,
                certificates_root=self._certificates_root,
                canonical_documents_root=self._canonical_documents_root,
                metadata_root=self._metadata_root,
                output_roots=self._output_roots,
                trust=trust,
                quality_certificate_verifier=quality_verifier,
            )
            with self._manifest_transaction(manifest, commit_state):
                commit_state.commit(
                    verifier.verify(lecture_id, artifact_kind, identity)
                )
        return commit_state.finalize()

    def issue_mock_exam(
        self,
        exam: MockExam,
        *,
        expected_output_bytes: Mapping[str, bytes],
    ) -> VerifiedDelivery:
        if not isinstance(exam, MockExam):
            raise DeliveryVerificationError("mock exam delivery payload is not typed")
        return self._issue_derivative(
            exam,
            DeliveryArtifactKind.MOCK_EXAM,
            expected_output_bytes,
        )

    def issue_podcast_script(
        self,
        script: PodcastScript,
        *,
        expected_output_bytes: Mapping[str, bytes],
    ) -> VerifiedDelivery:
        if not isinstance(script, PodcastScript):
            raise DeliveryVerificationError(
                "podcast script delivery payload is not typed"
            )
        return self._issue_derivative(
            script,
            DeliveryArtifactKind.PODCAST_SCRIPT,
            expected_output_bytes,
        )

    def issue_podcast_audio(
        self,
        script: PodcastScript,
        *,
        expected_output_bytes: Mapping[str, bytes],
    ) -> VerifiedDelivery:
        if not isinstance(script, PodcastScript):
            raise DeliveryVerificationError("podcast audio source script is not typed")
        return self._issue_derivative(
            script,
            DeliveryArtifactKind.PODCAST_AUDIO,
            expected_output_bytes,
        )

    def _issue_derivative(
        self,
        payload: MockExam | PodcastScript,
        artifact_kind: DeliveryArtifactKind,
        expected_output_bytes: Mapping[str, bytes],
    ) -> VerifiedDelivery:
        lecture_id = str(payload.lecture_id)
        trust = self._resolve_trust()
        quality_verifier = self._quality_certificate_verifier
        if quality_verifier is None:
            quality_verifier = QualityCertificateVerifier(
                certificates_root=self._certificates_root,
                canonical_documents_root=self._canonical_documents_root,
                metadata_root=self._metadata_root,
                trust=trust,
            )
        verified_source = quality_verifier.verify(lecture_id)
        derivative_verifier = self._derivative_certificate_verifier
        if derivative_verifier is None:
            derivative_verifier = DerivativeQualityCertificateVerifier(
                certificates_root=self._certificates_root,
                canonical_documents_root=self._canonical_documents_root,
                metadata_root=self._metadata_root,
                trust=trust,
                quality_certificate_verifier=quality_verifier,
            )
        output_file_system = RootedArtifactStore(self._output_roots[artifact_kind])
        commit_state = _DeliveryCommitState()
        with self._output_transaction(
            output_file_system,
            lecture_id,
            expected_output_bytes,
            commit_state,
        ):
            observed_payload = self._load_derivative_payload(
                lecture_id,
                artifact_kind,
            )
            if observed_payload != payload:
                raise DeliveryVerificationError(
                    "typed derivative payload differs from the exact delivery output"
                )
            try:
                if isinstance(payload, MockExam):
                    verified_derivative = derivative_verifier.verify_exam(payload)
                else:
                    verified_derivative = derivative_verifier.verify_podcast_script(
                        payload
                    )
            except DerivativeQualityError as error:
                raise DeliveryVerificationError(
                    f"derivative source certification changed or is invalid: {error}"
                ) from error
            certificate_bytes = self._read_derivative_certificate(
                lecture_id,
                artifact_kind,
            )
            derivative_certificate = (
                DeliveryManifestVerifier._parse_derivative_certificate(
                    certificate_bytes
                )
            )
            if derivative_certificate != verified_derivative:
                raise DeliveryVerificationError(
                    "verified derivative certificate differs from the exact delivery parent"
                )
            parent_certificate_bytes = RootedArtifactStore(
                self._certificates_root
            ).read_file(
                (lecture_id, "certificate.json"),
                maximum_bytes=_MAX_CERTIFICATE_BYTES,
            )
            execution_identity = self._resolve_derivative_delivery_identity(
                lecture_id,
                artifact_kind,
                derivative_certificate,
            )
            outputs = self._fingerprint_exact_outputs(
                lecture_id,
                artifact_kind,
                expected_output_bytes,
            )
            manifest = DeliveryManifest(
                schema_version=2,
                lecture_id=lecture_id,
                artifact_kind=artifact_kind,
                parent_certificate_sha256=hashlib.sha256(
                    parent_certificate_bytes
                ).hexdigest(),
                canonical_document_sha256=(
                    verified_source.certificate.canonical_document_sha256
                ),
                derivative_certificate_sha256=hashlib.sha256(
                    certificate_bytes
                ).hexdigest(),
                derivative_payload_sha256=hashlib.sha256(
                    canonical_model_bytes(payload)
                ).hexdigest(),
                execution_identity=execution_identity,
                outputs=outputs,
                trust_key_id=trust.key_id,
                signature_sha256="0" * 64,
            )
            manifest = manifest.model_copy(
                update={
                    "signature_sha256": trust.sign(manifest.authentication_payload())
                }
            )
            verifier = DeliveryManifestVerifier(
                manifests_root=self._manifests_root,
                certificates_root=self._certificates_root,
                canonical_documents_root=self._canonical_documents_root,
                metadata_root=self._metadata_root,
                output_roots=self._output_roots,
                trust=trust,
                quality_certificate_verifier=quality_verifier,
                derivative_certificate_verifier=derivative_verifier,
                audio_runtime_identity_resolver=self._audio_runtime_identity_resolver,
            )
            with self._manifest_transaction(manifest, commit_state):
                if artifact_kind is DeliveryArtifactKind.MOCK_EXAM:
                    verified_delivery = verifier.verify_mock_exam(lecture_id)
                elif artifact_kind is DeliveryArtifactKind.PODCAST_SCRIPT:
                    verified_delivery = verifier.verify_podcast_script(lecture_id)
                else:
                    verified_delivery = verifier.verify_podcast_audio(lecture_id)
                commit_state.commit(verified_delivery)
        return commit_state.finalize()

    def _load_derivative_payload(
        self,
        lecture_id: str,
        artifact_kind: DeliveryArtifactKind,
    ) -> MockExam | PodcastScript:
        if artifact_kind is DeliveryArtifactKind.MOCK_EXAM:
            payload_store = RootedArtifactStore(
                self._output_roots[DeliveryArtifactKind.MOCK_EXAM]
            )
            payload_bytes = payload_store.read_file(
                (lecture_id, "exam.json"),
                maximum_bytes=_MAX_DERIVATIVE_PAYLOAD_BYTES,
            )
            payload_type: type[MockExam] | type[PodcastScript] = MockExam
        else:
            payload_store = RootedArtifactStore(
                self._output_roots[DeliveryArtifactKind.PODCAST_SCRIPT]
            )
            payload_bytes = payload_store.read_file(
                (lecture_id, "script.json"),
                maximum_bytes=_MAX_DERIVATIVE_PAYLOAD_BYTES,
            )
            payload_type = PodcastScript
        try:
            return payload_type.model_validate_json(
                _normalize_strict_json(payload_bytes)
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise DeliveryVerificationError(
                "derivative delivery payload contains invalid or ambiguous JSON"
            ) from error

    def _read_derivative_certificate(
        self,
        lecture_id: str,
        artifact_kind: DeliveryArtifactKind,
    ) -> bytes:
        derivative_kind = _DERIVATIVE_ARTIFACT_KINDS[artifact_kind]
        return RootedArtifactStore(self._certificates_root).read_file(
            (
                lecture_id,
                "derivatives",
                derivative_kind.value,
                "certificate.json",
            ),
            maximum_bytes=_MAX_DERIVATIVE_CERTIFICATE_BYTES,
        )

    def _resolve_derivative_delivery_identity(
        self,
        lecture_id: str,
        artifact_kind: DeliveryArtifactKind,
        certificate: DerivativeQualityCertificate,
    ) -> DeliveryExecutionIdentity:
        if artifact_kind is DeliveryArtifactKind.PODCAST_AUDIO:
            if self._audio_runtime_identity_resolver is None:
                raise DeliveryVerificationError(
                    "audio runtime identity resolver is not configured"
                )
            identity = self._audio_runtime_identity_resolver(
                lecture_id,
                artifact_kind,
            )
            if not isinstance(identity, DeliveryExecutionIdentity):
                raise DeliveryVerificationError(
                    "audio runtime identity resolver returned an invalid identity"
                )
            return identity
        return _delivery_identity_from_derivative_certificate(
            certificate,
            artifact_kind,
        )

    def _resolve_trust(self) -> HmacQualityTrust:
        if self._trust is not None:
            return self._trust
        try:
            return HmacQualityTrust.from_environment()
        except QualityTrustError as error:
            raise DeliveryVerificationError(
                "quality certificate trust root is not configured"
            ) from error

    def _fingerprint_exact_outputs(
        self,
        lecture_id: str,
        artifact_kind: DeliveryArtifactKind,
        expected_output_bytes: Mapping[str, bytes],
    ) -> list[DeliveryOutput]:
        expected_outputs = _delivery_outputs_from_bytes(
            artifact_kind,
            expected_output_bytes,
        )
        expected_by_path = {output.relative_path: output for output in expected_outputs}
        output_file_system = RootedArtifactStore(self._output_roots[artifact_kind])
        observed, directories = output_file_system.fingerprint_tree((lecture_id,))
        expected_paths = EXPECTED_OUTPUT_RELATIVE_PATHS[artifact_kind]
        if tuple(observed) != expected_paths or directories != _expected_directories(
            expected_paths
        ):
            raise DeliveryVerificationError(
                "delivery output set differs from the closed expected set"
            )
        for relative_path, fingerprint in observed.items():
            expected = expected_by_path[relative_path]
            if (
                fingerprint.sha256 != expected.sha256
                or fingerprint.byte_size != expected.byte_size
            ):
                raise DeliveryVerificationError(
                    "delivery output differs from the exact bytes produced in memory"
                )
        return expected_outputs

    @staticmethod
    @contextmanager
    def _output_transaction(
        output_file_system: RootedArtifactStore,
        lecture_id: str,
        expected_output_bytes: Mapping[str, bytes],
        commit_state: _DeliveryCommitState,
    ) -> Iterator[None]:
        try:
            with output_file_system.reversible_files_transaction(
                (lecture_id,),
                expected_output_bytes,
                commit_state=commit_state,
            ):
                yield
        except BaseException as error:
            if not commit_state.is_committed:
                raise
            if not isinstance(error, (OSError, DeliveryVerificationError)):
                commit_state.defer_post_commit_error(error)

    @contextmanager
    def _manifest_transaction(
        self,
        manifest: DeliveryManifest,
        commit_state: _DeliveryCommitState,
    ) -> Iterator[None]:
        payload = canonical_json_bytes(
            manifest.model_dump(mode="json", exclude_none=True)
        )
        try:
            with RootedArtifactStore(self._manifests_root).reversible_file_transaction(
                (
                    str(manifest.lecture_id),
                    f"{manifest.artifact_kind.value}.json",
                ),
                payload,
                commit_state=commit_state,
            ):
                yield
        except BaseException as error:
            if not commit_state.is_committed:
                raise
            if not isinstance(error, (OSError, DeliveryVerificationError)):
                commit_state.defer_post_commit_error(error)


def _delivery_identity_from_derivative_certificate(
    certificate: DerivativeQualityCertificate,
    artifact_kind: DeliveryArtifactKind,
) -> DeliveryExecutionIdentity:
    compiler_relative_path = _DERIVATIVE_COMPILER_FILES.get(artifact_kind)
    if compiler_relative_path is None:
        raise DeliveryVerificationError(
            "derivative compiler identity is not registered for delivery kind"
        )
    compiler_sha256 = hashlib.sha256(
        (_PROJECT_ROOT / compiler_relative_path).read_bytes()
    ).hexdigest()
    derivative_identity = certificate.execution_identity()
    return DeliveryExecutionIdentity(
        generator_sha256=derivative_identity.generator_sha256,
        prompt_sha256=derivative_identity.prompt_sha256,
        model_sha256=derivative_identity.model_sha256,
        schema_sha256=derivative_identity.schema_sha256,
        renderer_sha256=compiler_sha256,
    )


def _renderer_identity_sha256(
    artifact_kind: DeliveryArtifactKind,
) -> str:
    if artifact_kind is not DeliveryArtifactKind.PUBLISHING:
        return _NOT_APPLICABLE_DIGEST
    try:
        renderer_version = distribution_version("markdown-it-py")
    except PackageNotFoundError as error:
        raise DeliveryVerificationError(
            "markdown-it-py runtime identity cannot be resolved"
        ) from error
    renderer = MarkdownIt(
        _PUBLISHING_RENDERER_PRESET,
        dict(_PUBLISHING_RENDERER_OPTIONS),
    )
    payload = canonical_json_bytes(
        {
            "distribution": "markdown-it-py",
            "version": renderer_version,
            "preset": _PUBLISHING_RENDERER_PRESET,
            "options": dict(renderer.options),
        }
    )
    return hashlib.sha256(payload).hexdigest()


def deterministic_delivery_execution_identity(
    artifact_kind: DeliveryArtifactKind,
) -> DeliveryExecutionIdentity:
    """Resolve the live implementation identity for a deterministic delivery."""

    relative_paths = _IMPLEMENTATION_FILES.get(artifact_kind)
    if relative_paths is None:
        raise DeliveryVerificationError(
            "execution identity requires a derivative quality certificate"
        )
    generator_hasher = hashlib.sha256()
    for relative_path in relative_paths:
        path = _PROJECT_ROOT / relative_path
        file_bytes = path.read_bytes()
        generator_hasher.update(relative_path.encode("utf-8"))
        generator_hasher.update(b"\0")
        generator_hasher.update(file_bytes)
        generator_hasher.update(b"\0")
    schema_path = _PROJECT_ROOT / "src/lecture_study/domain/models.py"
    return DeliveryExecutionIdentity(
        generator_sha256=generator_hasher.hexdigest(),
        prompt_sha256=_NOT_APPLICABLE_DIGEST,
        model_sha256=_NOT_APPLICABLE_DIGEST,
        schema_sha256=hashlib.sha256(schema_path.read_bytes()).hexdigest(),
        renderer_sha256=_renderer_identity_sha256(artifact_kind),
    )


def issue_deterministic_delivery(
    lecture_id: str,
    artifact_kind: DeliveryArtifactKind,
    *,
    expected_output_bytes: Mapping[str, bytes],
) -> VerifiedDelivery:
    return DeliveryManifestIssuer().issue_deterministic(
        lecture_id,
        artifact_kind,
        expected_output_bytes=expected_output_bytes,
    )


def is_deterministic_delivery_current(
    lecture_id: str,
    artifact_kind: DeliveryArtifactKind,
) -> bool:
    try:
        identity = deterministic_delivery_execution_identity(artifact_kind)
        return DeliveryManifestVerifier().is_current(
            lecture_id,
            artifact_kind,
            identity,
        )
    except (DeliveryVerificationError, OSError, ValueError):
        return False


def issue_mock_exam_delivery(
    exam: MockExam,
    *,
    expected_output_bytes: Mapping[str, bytes],
    publication_runtime_identity_resolver: PublicationRuntimeIdentityResolver,
    derivative_execution_identity_resolver: DerivativeExecutionIdentityResolver,
) -> VerifiedDelivery:
    issuer = _typed_derivative_delivery_issuer(
        publication_runtime_identity_resolver,
        derivative_execution_identity_resolver,
    )
    return issuer.issue_mock_exam(
        exam,
        expected_output_bytes=expected_output_bytes,
    )


def issue_podcast_script_delivery(
    script: PodcastScript,
    *,
    expected_output_bytes: Mapping[str, bytes],
    publication_runtime_identity_resolver: PublicationRuntimeIdentityResolver,
    derivative_execution_identity_resolver: DerivativeExecutionIdentityResolver,
) -> VerifiedDelivery:
    issuer = _typed_derivative_delivery_issuer(
        publication_runtime_identity_resolver,
        derivative_execution_identity_resolver,
    )
    return issuer.issue_podcast_script(
        script,
        expected_output_bytes=expected_output_bytes,
    )


def issue_podcast_audio_delivery(
    script: PodcastScript,
    *,
    expected_output_bytes: Mapping[str, bytes],
    publication_runtime_identity_resolver: PublicationRuntimeIdentityResolver,
    derivative_execution_identity_resolver: DerivativeExecutionIdentityResolver,
    audio_runtime_identity_resolver: AudioRuntimeIdentityResolver,
) -> VerifiedDelivery:
    issuer = _typed_derivative_delivery_issuer(
        publication_runtime_identity_resolver,
        derivative_execution_identity_resolver,
        audio_runtime_identity_resolver=audio_runtime_identity_resolver,
    )
    return issuer.issue_podcast_audio(
        script,
        expected_output_bytes=expected_output_bytes,
    )


def is_mock_exam_delivery_current(
    lecture_id: str,
    *,
    publication_runtime_identity_resolver: PublicationRuntimeIdentityResolver,
    derivative_execution_identity_resolver: DerivativeExecutionIdentityResolver,
) -> bool:
    try:
        verifier = _typed_derivative_delivery_verifier(
            publication_runtime_identity_resolver,
            derivative_execution_identity_resolver,
        )
        return verifier.is_mock_exam_current(lecture_id)
    except (DeliveryVerificationError, ValueError):
        return False


def is_podcast_script_delivery_current(
    lecture_id: str,
    *,
    publication_runtime_identity_resolver: PublicationRuntimeIdentityResolver,
    derivative_execution_identity_resolver: DerivativeExecutionIdentityResolver,
) -> bool:
    try:
        verifier = _typed_derivative_delivery_verifier(
            publication_runtime_identity_resolver,
            derivative_execution_identity_resolver,
        )
        return verifier.is_podcast_script_current(lecture_id)
    except (DeliveryVerificationError, ValueError):
        return False


def is_podcast_audio_delivery_current(
    lecture_id: str,
    *,
    publication_runtime_identity_resolver: PublicationRuntimeIdentityResolver,
    derivative_execution_identity_resolver: DerivativeExecutionIdentityResolver,
    audio_runtime_identity_resolver: AudioRuntimeIdentityResolver,
) -> bool:
    try:
        verifier = _typed_derivative_delivery_verifier(
            publication_runtime_identity_resolver,
            derivative_execution_identity_resolver,
            audio_runtime_identity_resolver=audio_runtime_identity_resolver,
        )
        return verifier.is_podcast_audio_current(lecture_id)
    except (DeliveryVerificationError, ValueError):
        return False


def _typed_derivative_delivery_issuer(
    publication_runtime_identity_resolver: PublicationRuntimeIdentityResolver,
    derivative_execution_identity_resolver: DerivativeExecutionIdentityResolver,
    *,
    audio_runtime_identity_resolver: AudioRuntimeIdentityResolver | None = None,
) -> DeliveryManifestIssuer:
    trust, quality_verifier, derivative_verifier = _typed_lineage_verifiers(
        publication_runtime_identity_resolver,
        derivative_execution_identity_resolver,
    )
    return DeliveryManifestIssuer(
        trust=trust,
        quality_certificate_verifier=quality_verifier,
        derivative_certificate_verifier=derivative_verifier,
        audio_runtime_identity_resolver=audio_runtime_identity_resolver,
    )


def _typed_derivative_delivery_verifier(
    publication_runtime_identity_resolver: PublicationRuntimeIdentityResolver,
    derivative_execution_identity_resolver: DerivativeExecutionIdentityResolver,
    *,
    audio_runtime_identity_resolver: AudioRuntimeIdentityResolver | None = None,
) -> DeliveryManifestVerifier:
    trust, quality_verifier, derivative_verifier = _typed_lineage_verifiers(
        publication_runtime_identity_resolver,
        derivative_execution_identity_resolver,
    )
    return DeliveryManifestVerifier(
        trust=trust,
        quality_certificate_verifier=quality_verifier,
        derivative_certificate_verifier=derivative_verifier,
        audio_runtime_identity_resolver=audio_runtime_identity_resolver,
    )


def _typed_lineage_verifiers(
    publication_runtime_identity_resolver: PublicationRuntimeIdentityResolver,
    derivative_execution_identity_resolver: DerivativeExecutionIdentityResolver,
) -> tuple[
    HmacQualityTrust,
    QualityCertificateVerifier,
    DerivativeQualityCertificateVerifier,
]:
    try:
        trust = HmacQualityTrust.from_environment()
    except QualityTrustError as error:
        raise DeliveryVerificationError(
            "quality certificate trust root is not configured"
        ) from error
    quality_verifier = QualityCertificateVerifier(
        trust=trust,
        runtime_identity_resolver=publication_runtime_identity_resolver,
    )
    derivative_verifier = DerivativeQualityCertificateVerifier(
        trust=trust,
        execution_identity_resolver=derivative_execution_identity_resolver,
        quality_certificate_verifier=quality_verifier,
    )
    return trust, quality_verifier, derivative_verifier

"""Production certificate verification for canonical and derivative material."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, NoReturn, Protocol, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)
from pydantic_core import ValidationError

from lecture_study.domain.models import (
    CanonicalLectureDocument,
    LectureGlobalIndex,
    SafeIdentifier,
)

from lecture_study.quality.trust import (
    HmacQualityTrust,
    QualityTrustError,
    canonical_json_bytes,
)

QUALITY_CERTIFICATES_ROOT = Path("workspace/data/quality_certificates")
CANONICAL_DOCUMENTS_ROOT = Path("workspace/data/10_canonical_documents")
METADATA_ROOT = Path("workspace/data/11_metadata")

_LECTURE_ID_ADAPTER = TypeAdapter(SafeIdentifier)
_MAX_CANONICAL_DOCUMENT_BYTES = 67_108_864
_MAX_GLOBAL_INDEX_BYTES = 33_554_432
_MAX_GATE_REPORT_BYTES = 4_194_304
_MAX_CERTIFICATE_BYTES = 1_048_576
_READ_CHUNK_BYTES = 65_536
_Digest = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]


class QualityCertificationError(RuntimeError):
    """Raised when canonical material is not certified for downstream use."""


class QualityGateName(StrEnum):
    """Closed registry of mandatory publication-quality gates."""

    SOURCE_MEDIA_INTEGRITY = "source-media-integrity"
    TRANSCRIPT_COMPLETENESS = "transcript-completeness"
    VISUAL_OCR_COMPLETENESS = "visual-ocr-completeness"
    TIMELINE_CLASSIFICATION = "timeline-classification"
    LEARNING_UNIT_COMPLETENESS = "learning-unit-completeness"
    PROMPT_BUDGET = "prompt-budget"
    ATOMIC_CONTENT_LEDGER = "atomic-content-ledger"
    SOURCE_TO_MATERIAL_COVERAGE = "source-to-material-coverage"
    MATERIAL_TO_SOURCE_FAITHFULNESS = "material-to-source-faithfulness"
    ANALYTICAL_DEPTH = "analytical-depth"
    CLINICAL_SAFETY = "clinical-safety"
    NUMERIC_CONSISTENCY = "numeric-consistency"
    TRACEABILITY = "traceability"
    CRITIC_RESOLUTION = "critic-resolution"
    CONTENT_DENSITY = "content-density"
    GLOBAL_RECONCILIATION = "global-reconciliation"
    ARTIFACT_LINEAGE = "artifact-lineage"


REQUIRED_QUALITY_GATES = frozenset(QualityGateName)


class QualityGateReport(BaseModel):
    """Immutable envelope for one independently generated gate report."""

    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: Literal[1]
    lecture_id: SafeIdentifier
    gate_name: QualityGateName
    passed: Literal[True]
    calibrated: Literal[True]
    canonical_document_sha256: _Digest
    global_index_sha256: _Digest
    source_manifest_sha256: _Digest
    policy_manifest_sha256: _Digest
    implementation_sha256: _Digest
    evaluator_artifact_sha256: _Digest
    findings: list[str] = Field(default_factory=list, max_length=0)


class PublicationQualityCertificate(BaseModel):
    """Approval record authenticated by the runtime quality trust root.

    Evaluator digests bind each report to the evaluator identity declared by the
    issuer. Verification of the referenced evaluator bytes is a separate live
    evidence check; the authentication fields prevent coordinated JSON forgery.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: Literal[1]
    lecture_id: SafeIdentifier
    status: Literal["approved"]
    canonical_document_sha256: _Digest
    global_index_sha256: _Digest
    source_manifest_sha256: _Digest
    policy_manifest_sha256: _Digest
    implementation_sha256: _Digest
    gate_report_sha256: dict[QualityGateName, _Digest]
    gate_evaluator_artifact_sha256: dict[QualityGateName, _Digest]
    trust_key_id: _Digest
    signature_sha256: _Digest

    @model_validator(mode="after")
    def _validate_exact_gate_sets(self) -> PublicationQualityCertificate:
        self._require_exact_gate_set("report", self.gate_report_sha256)
        self._require_exact_gate_set(
            "evaluator artifact",
            self.gate_evaluator_artifact_sha256,
        )
        return self

    def authentication_payload(self) -> bytes:
        payload = self.model_dump(mode="json", exclude={"signature_sha256"})
        return canonical_json_bytes(payload)

    def require_authenticated(self, trust: HmacQualityTrust) -> None:
        try:
            trust.require_valid(
                payload=self.authentication_payload(),
                signature=self.signature_sha256,
                key_id=self.trust_key_id,
            )
        except QualityTrustError as error:
            raise QualityCertificationError(
                "publication certificate authentication failed"
            ) from error

    @staticmethod
    def _require_exact_gate_set(
        mapping_name: str,
        gate_mapping: dict[QualityGateName, str],
    ) -> None:
        observed_gates = frozenset(gate_mapping)
        if observed_gates == REQUIRED_QUALITY_GATES:
            return
        missing = sorted(gate.value for gate in REQUIRED_QUALITY_GATES - observed_gates)
        unexpected = sorted(
            str(gate) for gate in observed_gates - REQUIRED_QUALITY_GATES
        )
        raise ValueError(
            f"certificate {mapping_name} gate set mismatch: missing={missing}, "
            f"unexpected={unexpected}"
        )


@dataclass(frozen=True, slots=True)
class VerifiedPublicationInputs:
    """Parsed from the same bytes whose hashes were certified."""

    certificate: PublicationQualityCertificate
    certificate_sha256: _Digest
    document: CanonicalLectureDocument
    global_index: LectureGlobalIndex


class PublicationRuntimeIdentity(BaseModel):
    """Live identities that make an authenticated certificate current."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    source_manifest_sha256: _Digest
    policy_manifest_sha256: _Digest
    implementation_sha256: _Digest
    gate_evaluator_artifact_sha256: dict[QualityGateName, _Digest]

    @model_validator(mode="after")
    def _validate_exact_evaluator_gate_set(self) -> PublicationRuntimeIdentity:
        PublicationQualityCertificate._require_exact_gate_set(
            "runtime evaluator artifact",
            self.gate_evaluator_artifact_sha256,
        )
        return self


class PublicationRuntimeIdentityResolver(Protocol):
    """Resolves identities from authoritative live artifacts."""

    def resolve(self, lecture_id: str) -> PublicationRuntimeIdentity:
        """Return the current source, policy, implementation, and evaluator identity."""
        ...


class UnavailablePublicationRuntimeIdentityResolver:
    """Fails closed until authoritative live identity resolution is integrated."""

    def resolve(self, lecture_id: str) -> PublicationRuntimeIdentity:
        del lecture_id
        raise QualityCertificationError(
            "authoritative publication runtime identity resolution is not configured"
        )


class QualityCertificateVerifier:
    """Recomputes every subject and report hash before allowing downstream work."""

    def __init__(
        self,
        *,
        certificates_root: Path = QUALITY_CERTIFICATES_ROOT,
        canonical_documents_root: Path = CANONICAL_DOCUMENTS_ROOT,
        metadata_root: Path = METADATA_ROOT,
        trust: HmacQualityTrust | None = None,
        runtime_identity_resolver: PublicationRuntimeIdentityResolver | None = None,
    ) -> None:
        self._certificates_root = certificates_root
        self._canonical_documents_root = canonical_documents_root
        self._metadata_root = metadata_root
        self._trust = trust
        self._runtime_identity_resolver = (
            runtime_identity_resolver
            if runtime_identity_resolver is not None
            else UnavailablePublicationRuntimeIdentityResolver()
        )

    def _resolve_trust(self) -> HmacQualityTrust:
        if self._trust is not None:
            return self._trust
        try:
            return HmacQualityTrust.from_environment()
        except QualityTrustError as error:
            raise QualityCertificationError(
                "quality certificate trust root is not configured"
            ) from error

    def verify(self, lecture_id: str) -> VerifiedPublicationInputs:
        lecture_id = str(_LECTURE_ID_ADAPTER.validate_python(lecture_id))
        canonical_path = (
            self._canonical_documents_root / lecture_id / "canonical_document.json"
        )
        global_index_path = self._metadata_root / lecture_id / "global_index.json"
        certificate_dir = self._certificates_root / lecture_id
        certificate_path = certificate_dir / "certificate.json"

        canonical_bytes = self._read_regular_file(
            canonical_path,
            self._canonical_documents_root,
            _MAX_CANONICAL_DOCUMENT_BYTES,
        )
        global_index_bytes = self._read_regular_file(
            global_index_path,
            self._metadata_root,
            _MAX_GLOBAL_INDEX_BYTES,
        )
        canonical_digest = hashlib.sha256(canonical_bytes).hexdigest()
        global_index_digest = hashlib.sha256(global_index_bytes).hexdigest()
        certificate_bytes = self._read_regular_file(
            certificate_path,
            self._certificates_root,
            _MAX_CERTIFICATE_BYTES,
        )
        certificate_digest = hashlib.sha256(certificate_bytes).hexdigest()
        try:
            document = CanonicalLectureDocument.model_validate_json(
                self._normalize_strict_json(canonical_bytes)
            )
            global_index = LectureGlobalIndex.model_validate_json(
                self._normalize_strict_json(global_index_bytes)
            )
            certificate = PublicationQualityCertificate.model_validate_json(
                self._normalize_strict_json(certificate_bytes)
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise QualityCertificationError(
                "certification inputs contain invalid or ambiguous JSON"
            ) from error

        certificate.require_authenticated(self._resolve_trust())
        self._validate_certificate_subject(
            certificate=certificate,
            lecture_id=lecture_id,
            canonical_digest=canonical_digest,
            global_index_digest=global_index_digest,
        )
        self._validate_runtime_identity(
            certificate=certificate,
            runtime_identity=self._runtime_identity_resolver.resolve(lecture_id),
        )
        self._validate_internal_lecture_identities(
            document=document,
            global_index=global_index,
            certificate=certificate,
            lecture_id=lecture_id,
        )
        self._validate_non_empty_canonical(document)
        self._validate_gate_reports(
            certificate=certificate,
            certificate_dir=certificate_dir,
        )
        return VerifiedPublicationInputs(
            certificate=certificate,
            certificate_sha256=certificate_digest,
            document=document,
            global_index=global_index,
        )

    def is_certified(self, lecture_id: str) -> bool:
        try:
            self.verify(lecture_id)
        except (QualityCertificationError, ValueError):
            return False
        return True

    @staticmethod
    def _validate_certificate_subject(
        *,
        certificate: PublicationQualityCertificate,
        lecture_id: str,
        canonical_digest: str,
        global_index_digest: str,
    ) -> None:
        if str(certificate.lecture_id) != lecture_id:
            raise QualityCertificationError(
                "quality certificate belongs to a different lecture"
            )
        if certificate.canonical_document_sha256 != canonical_digest:
            raise QualityCertificationError(
                "canonical document changed after quality certification"
            )
        if certificate.global_index_sha256 != global_index_digest:
            raise QualityCertificationError(
                "global index changed after quality certification"
            )

    @staticmethod
    def _validate_runtime_identity(
        *,
        certificate: PublicationQualityCertificate,
        runtime_identity: PublicationRuntimeIdentity,
    ) -> None:
        certified_identity = (
            certificate.source_manifest_sha256,
            certificate.policy_manifest_sha256,
            certificate.implementation_sha256,
            certificate.gate_evaluator_artifact_sha256,
        )
        live_identity = (
            runtime_identity.source_manifest_sha256,
            runtime_identity.policy_manifest_sha256,
            runtime_identity.implementation_sha256,
            runtime_identity.gate_evaluator_artifact_sha256,
        )
        if certified_identity != live_identity:
            raise QualityCertificationError(
                "publication runtime identity changed after certification"
            )

    @staticmethod
    def _validate_internal_lecture_identities(
        *,
        document: CanonicalLectureDocument,
        global_index: LectureGlobalIndex,
        certificate: PublicationQualityCertificate,
        lecture_id: str,
    ) -> None:
        expected_identity = str(certificate.lecture_id)
        observed_identities = {
            "requested lecture": lecture_id,
            "canonical document": str(document.lecture_id),
            "global index": str(global_index.lecture_id),
        }
        mismatches = [
            subject
            for subject, observed_identity in observed_identities.items()
            if observed_identity != expected_identity
        ]
        if mismatches:
            raise QualityCertificationError(
                "certification subject identity mismatch: " + ", ".join(mismatches)
            )

    @staticmethod
    def _validate_non_empty_canonical(document: CanonicalLectureDocument) -> None:
        if document.total_units == 0 or not document.units:
            raise QualityCertificationError(
                "canonical document contains no validated learning units"
            )

    def _validate_gate_reports(
        self,
        *,
        certificate: PublicationQualityCertificate,
        certificate_dir: Path,
    ) -> None:
        reports_dir = certificate_dir / "reports"
        for gate_name in sorted(REQUIRED_QUALITY_GATES, key=lambda gate: gate.value):
            report_path = reports_dir / f"{gate_name.value}.json"
            report_bytes = self._read_regular_file(
                report_path,
                self._certificates_root,
                _MAX_GATE_REPORT_BYTES,
            )
            report_digest = hashlib.sha256(report_bytes).hexdigest()
            if certificate.gate_report_sha256[gate_name] != report_digest:
                raise QualityCertificationError(
                    f"{gate_name.value} report changed after certification"
                )

            try:
                report = QualityGateReport.model_validate_json(
                    self._normalize_strict_json(report_bytes)
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
                raise QualityCertificationError(
                    f"{gate_name.value} report contains invalid or ambiguous JSON"
                ) from error
            self._validate_gate_report(
                certificate=certificate,
                expected_gate=gate_name,
                report=report,
            )

    @staticmethod
    def _validate_gate_report(
        *,
        certificate: PublicationQualityCertificate,
        expected_gate: QualityGateName,
        report: QualityGateReport,
    ) -> None:
        expected_identity = (
            certificate.lecture_id,
            expected_gate,
            certificate.canonical_document_sha256,
            certificate.global_index_sha256,
            certificate.source_manifest_sha256,
            certificate.policy_manifest_sha256,
            certificate.implementation_sha256,
            certificate.gate_evaluator_artifact_sha256[expected_gate],
        )
        observed_identity = (
            report.lecture_id,
            report.gate_name,
            report.canonical_document_sha256,
            report.global_index_sha256,
            report.source_manifest_sha256,
            report.policy_manifest_sha256,
            report.implementation_sha256,
            report.evaluator_artifact_sha256,
        )
        if observed_identity != expected_identity:
            raise QualityCertificationError(
                f"{expected_gate.value} report declared identity does not match "
                "the certificate"
            )

    @staticmethod
    def _parse_strict_json(file_bytes: bytes) -> dict[str, object]:
        source_text = file_bytes.decode("utf-8")

        def reject_duplicate_keys(
            pairs: list[tuple[str, object]],
        ) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise json.JSONDecodeError(
                        f"duplicate JSON key: {key}",
                        source_text,
                        0,
                    )
                result[key] = value
            return result

        def reject_non_finite(value: str) -> NoReturn:
            raise json.JSONDecodeError(
                f"non-finite JSON value: {value}",
                source_text,
                0,
            )

        def parse_finite_float(value: str) -> float:
            parsed_value = float(value)
            if not math.isfinite(parsed_value):
                reject_non_finite(value)
            return parsed_value

        parsed = json.loads(
            source_text,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_non_finite,
            parse_float=parse_finite_float,
        )
        if not isinstance(parsed, dict):
            raise json.JSONDecodeError(
                "top-level JSON value must be an object",
                source_text,
                0,
            )
        return cast(dict[str, object], parsed)

    @classmethod
    def _normalize_strict_json(cls, file_bytes: bytes) -> bytes:
        parsed = cls._parse_strict_json(file_bytes)
        return json.dumps(
            parsed,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def _read_regular_file(
        cls,
        path: Path,
        allowed_root: Path,
        maximum_bytes: int,
    ) -> bytes:
        relative_parts = cls._relative_artifact_parts(path, allowed_root)
        try:
            directory_descriptor = cls._open_directory_path(allowed_root)
            try:
                file_descriptor = cls._open_relative_artifact(
                    directory_descriptor,
                    relative_parts,
                )
            finally:
                os.close(directory_descriptor)
        except FileNotFoundError as error:
            raise QualityCertificationError(
                f"required certification artifact is missing: {path}"
            ) from error
        except OSError as error:
            raise QualityCertificationError(
                f"cannot open certification artifact safely: {path}"
            ) from error

        try:
            return cls._read_bounded_descriptor(
                file_descriptor,
                path,
                maximum_bytes,
            )
        finally:
            os.close(file_descriptor)

    @staticmethod
    def _relative_artifact_parts(path: Path, allowed_root: Path) -> tuple[str, ...]:
        try:
            relative_path = path.relative_to(allowed_root)
        except ValueError as error:
            raise QualityCertificationError(
                f"certification artifact escapes its allowed root: {path}"
            ) from error
        parts = relative_path.parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise QualityCertificationError(
                f"certification artifact has an unsafe path: {path}"
            )
        return parts

    @classmethod
    def _open_directory_path(cls, directory_path: Path) -> int:
        flags = cls._directory_open_flags()
        anchor = "/" if directory_path.is_absolute() else "."
        components = (
            directory_path.parts[1:]
            if directory_path.is_absolute()
            else directory_path.parts
        )
        if any(component in {"", ".", ".."} for component in components):
            raise QualityCertificationError(
                f"certification root has an unsafe path: {directory_path}"
            )

        current_descriptor = os.open(anchor, flags)
        try:
            for component in components:
                next_descriptor = os.open(
                    component,
                    flags,
                    dir_fd=current_descriptor,
                )
                try:
                    if not stat.S_ISDIR(os.fstat(next_descriptor).st_mode):
                        raise QualityCertificationError(
                            f"certification path component is not a directory: {component}"
                        )
                except Exception:
                    os.close(next_descriptor)
                    raise
                os.close(current_descriptor)
                current_descriptor = next_descriptor
            return current_descriptor
        except Exception:
            os.close(current_descriptor)
            raise

    @classmethod
    def _open_relative_artifact(
        cls,
        root_descriptor: int,
        relative_parts: tuple[str, ...],
    ) -> int:
        current_descriptor = os.dup(root_descriptor)
        try:
            for component in relative_parts[:-1]:
                next_descriptor = os.open(
                    component,
                    cls._directory_open_flags(),
                    dir_fd=current_descriptor,
                )
                os.close(current_descriptor)
                current_descriptor = next_descriptor
            return os.open(
                relative_parts[-1],
                cls._file_open_flags(),
                dir_fd=current_descriptor,
            )
        finally:
            os.close(current_descriptor)

    @staticmethod
    def _directory_open_flags() -> int:
        if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
            raise QualityCertificationError(
                "platform cannot enforce no-follow directory traversal"
            )
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        return flags

    @staticmethod
    def _file_open_flags() -> int:
        if not hasattr(os, "O_NOFOLLOW"):
            raise QualityCertificationError(
                "platform cannot enforce no-follow artifact access"
            )
        flags = os.O_RDONLY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        return flags

    @classmethod
    def _read_bounded_descriptor(
        cls,
        descriptor: int,
        path: Path,
        maximum_bytes: int,
    ) -> bytes:
        status_before = os.fstat(descriptor)
        cls._validate_regular_file_status(status_before, path)
        if status_before.st_size > maximum_bytes:
            raise QualityCertificationError(
                f"certification artifact exceeds {maximum_bytes} bytes: {path.name}"
            )

        file_bytes = bytearray()
        while len(file_bytes) <= maximum_bytes:
            remaining_bytes = maximum_bytes + 1 - len(file_bytes)
            chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, remaining_bytes))
            if not chunk:
                break
            file_bytes.extend(chunk)
        if len(file_bytes) > maximum_bytes:
            raise QualityCertificationError(
                f"certification artifact exceeds {maximum_bytes} bytes: {path.name}"
            )

        status_after = os.fstat(descriptor)
        cls._validate_regular_file_status(status_after, path)
        if cls._file_status_identity(status_before) != cls._file_status_identity(
            status_after
        ):
            raise QualityCertificationError(
                f"certification artifact changed while being read: {path}"
            )
        if len(file_bytes) != status_after.st_size:
            raise QualityCertificationError(
                f"certification artifact size changed while being read: {path}"
            )
        return bytes(file_bytes)

    @staticmethod
    def _validate_regular_file_status(file_status: os.stat_result, path: Path) -> None:
        if not stat.S_ISREG(file_status.st_mode):
            raise QualityCertificationError(
                f"certification artifact is not a regular file: {path}"
            )
        if file_status.st_nlink != 1:
            raise QualityCertificationError(
                f"certification artifact must have exactly one hard link: {path}"
            )

    @staticmethod
    def _file_status_identity(file_status: os.stat_result) -> tuple[int, ...]:
        return (
            file_status.st_dev,
            file_status.st_ino,
            file_status.st_mode,
            file_status.st_nlink,
            file_status.st_size,
            file_status.st_mtime_ns,
            file_status.st_ctime_ns,
        )


def require_publication_ready(lecture_id: str) -> VerifiedPublicationInputs:
    """Fail-closed production entry point for all canonical derivatives."""

    return QualityCertificateVerifier().verify(lecture_id)


def is_publication_ready(lecture_id: str) -> bool:
    """Returns true only after complete certificate revalidation."""

    return QualityCertificateVerifier().is_certified(lecture_id)

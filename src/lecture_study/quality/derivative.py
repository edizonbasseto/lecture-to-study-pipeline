"""Authenticated quality authorization for LLM-generated derivatives."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Protocol

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
    MockExam,
    PodcastScript,
    SafeIdentifier,
)
from lecture_study.quality.certification import (
    CANONICAL_DOCUMENTS_ROOT,
    METADATA_ROOT,
    QUALITY_CERTIFICATES_ROOT,
    QualityCertificateVerifier,
    QualityCertificationError,
    VerifiedPublicationInputs,
)
from lecture_study.quality.trust import (
    HmacQualityTrust,
    QualityTrustError,
    canonical_json_bytes,
    canonical_model_bytes,
)

_Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_LECTURE_ID_ADAPTER = TypeAdapter(SafeIdentifier)
_MAX_CERTIFICATE_BYTES = 1_048_576
_MAX_REPORT_BYTES = 4_194_304


class DerivativeQualityError(RuntimeError):
    """Raised when an LLM derivative lacks exact quality authorization."""


class DerivativeArtifactKind(StrEnum):
    MOCK_EXAM = "mock-exam"
    PODCAST_SCRIPT = "podcast-script"
    # Retained for on-disk compatibility: generation receipts written by
    # the retired personal-study route (ADR 0024, removed under ADR 0038)
    # carry artifact_kind="flashcards" and must keep deserializing. Never
    # dispatched to the verifier or issuer, which handle only
    # MOCK_EXAM/PODCAST_SCRIPT -- inert with respect to the certified
    # derivative flow.
    FLASHCARDS = "flashcards"


class DerivativeExecutionIdentity(BaseModel):
    """Exact generator, prompt, model, and schema identities in production."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    generator_sha256: _Digest
    prompt_sha256: _Digest
    model_sha256: _Digest
    schema_sha256: _Digest


class DerivativeExecutionIdentityResolver(Protocol):
    """Resolves the authoritative live identity for one derivative kind."""

    def __call__(
        self,
        artifact_kind: DerivativeArtifactKind,
        /,
    ) -> DerivativeExecutionIdentity: ...


class GenerationReceipt(BaseModel):
    """Authenticates one generated payload and its exact source lineage."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    artifact_kind: DerivativeArtifactKind
    lecture_id: SafeIdentifier
    payload_sha256: _Digest
    execution_identity: DerivativeExecutionIdentity
    source_certificate_sha256: _Digest
    canonical_document_sha256: _Digest
    trust_key_id: _Digest
    signature_sha256: _Digest

    @classmethod
    def issue(
        cls,
        *,
        artifact_kind: DerivativeArtifactKind,
        lecture_id: str,
        payload: BaseModel,
        execution_identity: DerivativeExecutionIdentity,
        source_certificate_sha256: str,
        canonical_document_sha256: str,
        trust: HmacQualityTrust,
    ) -> GenerationReceipt:
        unsigned = cls(
            artifact_kind=artifact_kind,
            lecture_id=lecture_id,
            payload_sha256=hashlib.sha256(canonical_model_bytes(payload)).hexdigest(),
            execution_identity=execution_identity,
            source_certificate_sha256=source_certificate_sha256,
            canonical_document_sha256=canonical_document_sha256,
            trust_key_id=trust.key_id,
            signature_sha256="0" * 64,
        )
        return unsigned.model_copy(
            update={"signature_sha256": trust.sign(unsigned.authentication_payload())}
        )

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
            raise DerivativeQualityError(
                "generation receipt authentication failed"
            ) from error


@dataclass(frozen=True, slots=True)
class UncertifiedMockExamCandidate:
    """Generated mock exam awaiting authoritative derivative certification."""

    payload: MockExam
    receipt: GenerationReceipt


@dataclass(frozen=True, slots=True)
class UncertifiedPodcastScriptCandidate:
    """Generated podcast script awaiting authoritative certification."""

    payload: PodcastScript
    receipt: GenerationReceipt


class DerivativeQualityGate(StrEnum):
    SOURCE_COVERAGE = "source-coverage"
    SOURCE_FAITHFULNESS = "source-faithfulness"
    ANALYTICAL_DEPTH = "analytical-depth"
    CLINICAL_SAFETY = "clinical-safety"
    TRACEABILITY = "traceability"
    CRITIC_RESOLUTION = "critic-resolution"


_REQUIRED_DERIVATIVE_GATES = frozenset(DerivativeQualityGate)


class DerivativeGateReport(BaseModel):
    """Positive report for one independently evaluated derivative gate."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[1]
    lecture_id: SafeIdentifier
    artifact_kind: DerivativeArtifactKind
    gate_name: DerivativeQualityGate
    status: Literal["approved"]
    payload_sha256: _Digest
    parent_certificate_sha256: _Digest
    evaluator_artifact_sha256: _Digest
    findings: tuple[str, ...] = Field(default=(), max_length=0)


class DerivativeQualityCertificate(BaseModel):
    """Authenticated approval bound to one exact derivative payload."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[1]
    lecture_id: SafeIdentifier
    artifact_kind: DerivativeArtifactKind
    status: Literal["approved"]
    payload_sha256: _Digest
    parent_certificate_sha256: _Digest
    canonical_document_sha256: _Digest
    generator_sha256: _Digest
    prompt_sha256: _Digest
    model_sha256: _Digest
    schema_sha256: _Digest
    gate_report_sha256: dict[DerivativeQualityGate, _Digest]
    gate_evaluator_artifact_sha256: dict[DerivativeQualityGate, _Digest]
    trust_key_id: _Digest
    signature_sha256: _Digest

    @model_validator(mode="after")
    def _require_exact_gate_sets(self) -> DerivativeQualityCertificate:
        if frozenset(self.gate_report_sha256) != _REQUIRED_DERIVATIVE_GATES:
            raise ValueError("derivative certificate report gate set mismatch")
        if frozenset(self.gate_evaluator_artifact_sha256) != _REQUIRED_DERIVATIVE_GATES:
            raise ValueError("derivative certificate evaluator gate set mismatch")
        return self

    def execution_identity(self) -> DerivativeExecutionIdentity:
        """Return the authenticated execution identity as one typed value."""

        return DerivativeExecutionIdentity(
            generator_sha256=self.generator_sha256,
            prompt_sha256=self.prompt_sha256,
            model_sha256=self.model_sha256,
            schema_sha256=self.schema_sha256,
        )

    def authentication_payload(self) -> bytes:
        return canonical_json_bytes(
            self.model_dump(mode="json", exclude={"signature_sha256"})
        )

    def require_authenticated(self, trust: HmacQualityTrust) -> None:
        try:
            trust.require_valid(
                payload=self.authentication_payload(),
                signature=self.signature_sha256,
                key_id=self.trust_key_id,
            )
        except QualityTrustError as error:
            raise DerivativeQualityError(
                "derivative quality certificate authentication failed"
            ) from error


class DerivativeQualityCertificateVerifier:
    """Verifies source lineage, payload, and authoritative live identities.

    Production must inject the resolver that owns the current generator, prompt,
    model, and schema identities. No model path or environment default is inferred;
    omitting the resolver intentionally keeps derivative publication fail-closed.
    """

    def __init__(
        self,
        *,
        certificates_root: Path = QUALITY_CERTIFICATES_ROOT,
        canonical_documents_root: Path = CANONICAL_DOCUMENTS_ROOT,
        metadata_root: Path = METADATA_ROOT,
        trust: HmacQualityTrust | None = None,
        execution_identity_resolver: (
            DerivativeExecutionIdentityResolver | None
        ) = None,
        quality_certificate_verifier: QualityCertificateVerifier | None = None,
    ) -> None:
        from lecture_study.quality.delivery import RootedArtifactStore

        self._certificates_root = certificates_root
        self._canonical_documents_root = canonical_documents_root
        self._metadata_root = metadata_root
        self._certificates = RootedArtifactStore(certificates_root)
        self._trust = trust
        self._execution_identity_resolver = execution_identity_resolver
        self._quality_certificate_verifier = quality_certificate_verifier

    def verify_exam(self, exam: MockExam) -> DerivativeQualityCertificate:
        return self._verify_payload(
            lecture_id=str(exam.lecture_id),
            artifact_kind=DerivativeArtifactKind.MOCK_EXAM,
            payload=exam,
        )

    def verify_podcast_script(
        self,
        script: PodcastScript,
    ) -> DerivativeQualityCertificate:
        return self._verify_payload(
            lecture_id=str(script.lecture_id),
            artifact_kind=DerivativeArtifactKind.PODCAST_SCRIPT,
            payload=script,
        )

    def _verify_payload(
        self,
        *,
        lecture_id: str,
        artifact_kind: DerivativeArtifactKind,
        payload: BaseModel,
    ) -> DerivativeQualityCertificate:
        lecture_id = str(_LECTURE_ID_ADAPTER.validate_python(lecture_id))
        trust = self._resolve_trust()
        try:
            source = self._resolve_quality_certificate_verifier(trust).verify(
                lecture_id
            )
        except QualityCertificationError as error:
            raise DerivativeQualityError(
                "source publication certificate is not valid"
            ) from error

        parent_bytes = self._certificates.read_file(
            (lecture_id, "certificate.json"),
            maximum_bytes=_MAX_CERTIFICATE_BYTES,
        )
        parent_digest = hashlib.sha256(parent_bytes).hexdigest()
        certificate_parts = (
            lecture_id,
            "derivatives",
            artifact_kind.value,
            "certificate.json",
        )
        certificate = self._parse_certificate(
            self._certificates.read_file(
                certificate_parts,
                maximum_bytes=_MAX_CERTIFICATE_BYTES,
            )
        )
        certificate.require_authenticated(trust)
        payload_digest = hashlib.sha256(canonical_model_bytes(payload)).hexdigest()
        if (
            certificate.lecture_id != lecture_id
            or certificate.artifact_kind is not artifact_kind
            or certificate.payload_sha256 != payload_digest
            or certificate.parent_certificate_sha256 != parent_digest
            or certificate.canonical_document_sha256
            != source.certificate.canonical_document_sha256
        ):
            raise DerivativeQualityError(
                "derivative certificate subject or source lineage changed"
            )
        live_execution_identity = self._resolve_execution_identity(artifact_kind)
        if certificate.execution_identity() != live_execution_identity:
            raise DerivativeQualityError(
                "derivative execution identity changed after generation"
            )
        self._verify_reports(certificate)
        return certificate

    def _verify_reports(self, certificate: DerivativeQualityCertificate) -> None:
        for gate_name in DerivativeQualityGate:
            report_bytes = self._certificates.read_file(
                (
                    str(certificate.lecture_id),
                    "derivatives",
                    certificate.artifact_kind.value,
                    "reports",
                    f"{gate_name.value}.json",
                ),
                maximum_bytes=_MAX_REPORT_BYTES,
            )
            if (
                hashlib.sha256(report_bytes).hexdigest()
                != certificate.gate_report_sha256[gate_name]
            ):
                raise DerivativeQualityError("derivative gate report changed")
            report = self._parse_report(report_bytes)
            if (
                report.lecture_id != certificate.lecture_id
                or report.artifact_kind is not certificate.artifact_kind
                or report.gate_name is not gate_name
                or report.payload_sha256 != certificate.payload_sha256
                or report.parent_certificate_sha256
                != certificate.parent_certificate_sha256
                or report.evaluator_artifact_sha256
                != certificate.gate_evaluator_artifact_sha256[gate_name]
            ):
                raise DerivativeQualityError("derivative gate report identity changed")

    def _resolve_trust(self) -> HmacQualityTrust:
        if self._trust is not None:
            return self._trust
        try:
            return HmacQualityTrust.from_environment()
        except QualityTrustError as error:
            raise DerivativeQualityError(
                "quality certificate trust root is not configured"
            ) from error

    def _resolve_quality_certificate_verifier(
        self,
        trust: HmacQualityTrust,
    ) -> QualityCertificateVerifier:
        if self._quality_certificate_verifier is not None:
            return self._quality_certificate_verifier
        return QualityCertificateVerifier(
            certificates_root=self._certificates_root,
            canonical_documents_root=self._canonical_documents_root,
            metadata_root=self._metadata_root,
            trust=trust,
        )

    def _resolve_execution_identity(
        self,
        artifact_kind: DerivativeArtifactKind,
    ) -> DerivativeExecutionIdentity:
        if self._execution_identity_resolver is None:
            raise DerivativeQualityError(
                "derivative execution identity resolver is not configured"
            )
        return self._execution_identity_resolver(artifact_kind)

    @staticmethod
    def _parse_certificate(file_bytes: bytes) -> DerivativeQualityCertificate:
        try:
            return DerivativeQualityCertificate.model_validate_json(
                _normalize_json(file_bytes)
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise DerivativeQualityError(
                "derivative certificate contains invalid or ambiguous JSON"
            ) from error

    @staticmethod
    def _parse_report(file_bytes: bytes) -> DerivativeGateReport:
        try:
            return DerivativeGateReport.model_validate_json(_normalize_json(file_bytes))
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise DerivativeQualityError(
                "derivative report contains invalid or ambiguous JSON"
            ) from error


class DerivativeGateBlocker(BaseModel):
    """Diagnostic record for one derivative critic that cannot run yet."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[1]
    gate_name: DerivativeQualityGate
    reason_code: SafeIdentifier
    detail: str = Field(min_length=1)


class DerivativeCertificationAttempt(BaseModel):
    """Non-authoritative diagnostic covering every required derivative critic."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[1]
    lecture_id: SafeIdentifier
    artifact_kind: DerivativeArtifactKind
    payload_sha256: _Digest
    blockers: tuple[DerivativeGateBlocker, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _require_exact_gate_set(self) -> DerivativeCertificationAttempt:
        observed = tuple(blocker.gate_name for blocker in self.blockers)
        if len(observed) != len(set(observed)):
            raise ValueError("derivative attempt contains duplicate gates")
        if frozenset(observed) != _REQUIRED_DERIVATIVE_GATES:
            raise ValueError("derivative attempt must cover the exact gate set")
        return self


class DerivativeQualityIssuer:
    """Runs the closed critic registry without accepting asserted approvals."""

    def __init__(
        self,
        *,
        certificates_root: Path = QUALITY_CERTIFICATES_ROOT,
        canonical_documents_root: Path = CANONICAL_DOCUMENTS_ROOT,
        metadata_root: Path = METADATA_ROOT,
        trust: HmacQualityTrust | None = None,
        execution_identity_resolver: (
            DerivativeExecutionIdentityResolver | None
        ) = None,
        quality_certificate_verifier: QualityCertificateVerifier | None = None,
    ) -> None:
        self._certificates_root = certificates_root
        self._canonical_documents_root = canonical_documents_root
        self._metadata_root = metadata_root
        self._trust = trust
        self._execution_identity_resolver = execution_identity_resolver
        self._quality_certificate_verifier = quality_certificate_verifier

    def evaluate_exam(self, candidate: UncertifiedMockExamCandidate) -> None:
        artifact_kind = DerivativeArtifactKind.MOCK_EXAM
        self._require_valid_receipt(
            receipt=candidate.receipt,
            payload=candidate.payload,
            artifact_kind=artifact_kind,
        )
        self._evaluate(
            lecture_id=str(candidate.payload.lecture_id),
            artifact_kind=artifact_kind,
            payload=candidate.payload,
        )

    def evaluate_podcast_script(
        self,
        candidate: UncertifiedPodcastScriptCandidate,
    ) -> None:
        artifact_kind = DerivativeArtifactKind.PODCAST_SCRIPT
        self._require_valid_receipt(
            receipt=candidate.receipt,
            payload=candidate.payload,
            artifact_kind=artifact_kind,
        )
        self._evaluate(
            lecture_id=str(candidate.payload.lecture_id),
            artifact_kind=artifact_kind,
            payload=candidate.payload,
        )

    def _require_valid_receipt(
        self,
        *,
        receipt: GenerationReceipt,
        payload: BaseModel,
        artifact_kind: DerivativeArtifactKind,
    ) -> None:
        trust = self._resolve_trust()
        receipt.require_authenticated(trust)
        lecture_id = str(
            _LECTURE_ID_ADAPTER.validate_python(getattr(payload, "lecture_id", None))
        )
        payload_digest = hashlib.sha256(canonical_model_bytes(payload)).hexdigest()
        if (
            receipt.artifact_kind is not artifact_kind
            or str(receipt.lecture_id) != lecture_id
            or receipt.payload_sha256 != payload_digest
        ):
            raise DerivativeQualityError(
                "generation receipt does not match the candidate payload"
            )
        source = self._verify_source(lecture_id, trust)
        if (
            receipt.source_certificate_sha256 != source.certificate_sha256
            or receipt.canonical_document_sha256
            != source.certificate.canonical_document_sha256
        ):
            raise DerivativeQualityError(
                "generation receipt source lineage is no longer current"
            )
        self._require_live_execution_identity(
            receipt.execution_identity,
            artifact_kind,
        )

    def _verify_source(
        self,
        lecture_id: str,
        trust: HmacQualityTrust,
    ) -> VerifiedPublicationInputs:
        try:
            return self._resolve_quality_certificate_verifier(trust).verify(lecture_id)
        except QualityCertificationError as error:
            raise DerivativeQualityError(
                "source publication certificate is not valid"
            ) from error

    def _resolve_quality_certificate_verifier(
        self,
        trust: HmacQualityTrust,
    ) -> QualityCertificateVerifier:
        if self._quality_certificate_verifier is not None:
            return self._quality_certificate_verifier
        return QualityCertificateVerifier(
            certificates_root=self._certificates_root,
            canonical_documents_root=self._canonical_documents_root,
            metadata_root=self._metadata_root,
            trust=trust,
        )

    def _resolve_trust(self) -> HmacQualityTrust:
        if self._trust is not None:
            return self._trust
        try:
            return HmacQualityTrust.from_environment()
        except QualityTrustError as error:
            raise DerivativeQualityError(
                "quality certificate trust root is not configured"
            ) from error

    def _require_live_execution_identity(
        self,
        captured_identity: DerivativeExecutionIdentity,
        artifact_kind: DerivativeArtifactKind,
    ) -> None:
        if self._execution_identity_resolver is None:
            raise DerivativeQualityError(
                "derivative execution identity resolver is not configured"
            )
        live_identity = self._execution_identity_resolver(artifact_kind)
        if captured_identity != live_identity:
            raise DerivativeQualityError(
                "derivative candidate execution identity changed before quality evaluation"
            )

    def _evaluate(
        self,
        *,
        lecture_id: str,
        artifact_kind: DerivativeArtifactKind,
        payload: BaseModel,
    ) -> None:
        lecture_id = str(_LECTURE_ID_ADAPTER.validate_python(lecture_id))
        payload_digest = hashlib.sha256(canonical_model_bytes(payload)).hexdigest()
        blockers = tuple(
            DerivativeGateBlocker(
                schema_version=1,
                gate_name=gate_name,
                reason_code="authoritative-critic-not-implemented",
                detail=(
                    "The independent calibrated critic and its typed evidence "
                    "payload are not implemented."
                ),
            )
            for gate_name in DerivativeQualityGate
        )
        attempt = DerivativeCertificationAttempt(
            schema_version=1,
            lecture_id=lecture_id,
            artifact_kind=artifact_kind,
            payload_sha256=payload_digest,
            blockers=blockers,
        )
        self._write_attempt_atomically(attempt)
        blocked_names = ", ".join(gate.value for gate in DerivativeQualityGate)
        raise DerivativeQualityError(
            "derivative remains fail-closed; authoritative critics are unavailable "
            f"for: {blocked_names}"
        )

    def _write_attempt_atomically(
        self,
        attempt: DerivativeCertificationAttempt,
    ) -> None:
        artifact_dir = (
            self._certificates_root
            / str(attempt.lecture_id)
            / "derivatives"
            / attempt.artifact_kind.value
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        target = artifact_dir / "latest_attempt.json"
        temporary = artifact_dir / ".latest_attempt.json.tmp"
        temporary.unlink(missing_ok=True)
        payload = canonical_json_bytes(attempt.model_dump(mode="json"))
        try:
            with temporary.open("xb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def require_mock_exam_ready(exam: MockExam) -> DerivativeQualityCertificate:
    return DerivativeQualityCertificateVerifier().verify_exam(exam)


def require_podcast_script_ready(
    script: PodcastScript,
) -> DerivativeQualityCertificate:
    return DerivativeQualityCertificateVerifier().verify_podcast_script(script)


def _normalize_json(file_bytes: bytes) -> bytes:
    decoded = file_bytes.decode("utf-8")

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise json.JSONDecodeError(f"duplicate key: {key}", decoded, 0)
            result[key] = value
        return result

    def reject_non_finite(value: str) -> None:
        raise json.JSONDecodeError(f"non-finite value: {value}", decoded, 0)

    parsed = json.loads(
        decoded,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_non_finite,
        parse_float=_parse_finite_float,
    )
    return json.dumps(
        parsed,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not (float("-inf") < parsed < float("inf")):
        raise ValueError("non-finite JSON number")
    return parsed

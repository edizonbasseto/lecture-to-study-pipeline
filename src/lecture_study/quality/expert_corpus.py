"""Dedicated authentication and promotion of veterinary expert corpora."""

from __future__ import annotations

import hashlib
import hmac
import weakref
from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn, Protocol, runtime_checkable

from pydantic import TypeAdapter

from lecture_study.domain.expert_corpus import (
    AuthenticatedVeterinaryExpertCorpus,
    CorpusLicenseEvidence,
    CorpusCustodyEvent,
    ExpertAdjudicationEvidence,
    ExpertAnnotationEvidence,
    ExpertAnnotationPolicy,
    ExpertCorpusInventoryItem,
    ExpertQualificationReference,
    VETERINARY_EXPERT_CORPUS_CRYPTOGRAPHIC_DOMAIN,
    VETERINARY_EXPERT_CORPUS_SIGNATURE_PURPOSE,
    VeterinaryExpertCorpusPromotionRequest,
)
from lecture_study.domain.models import SafeIdentifier
from lecture_study.quality.calibration import (
    canonical_reference_corpus_case_sha256,
)
from lecture_study.quality.trust import canonical_json_bytes

_MINIMUM_KEY_BYTES = 32
_KEY_ID_CONTEXT = b"lecture-study:veterinary-expert-corpus:key-id:v1\0"
_SIGNATURE_CONTEXT = (
    b"lecture-study.veterinary-expert-corpus.v1\0expert-corpus-promotion\0"
)
_IDENTITY_CONTEXT = b"lecture-study:veterinary-expert-corpus:identity:v1\0"
_INVENTORY_CONTEXT = b"lecture-study:veterinary-expert-corpus:inventory:v1\0"
_ANNOTATION_POLICY_CONTEXT = (
    b"lecture-study:veterinary-expert-corpus:annotation-policy:v1\0"
)
_CUSTODY_EVENT_CONTEXT = b"lecture-study:veterinary-expert-corpus:custody-event:v1\0"
_SAFE_IDENTIFIER_ADAPTER = TypeAdapter(SafeIdentifier)


class ExpertCorpusDisposition(StrEnum):
    """Scope-local fail-closed state without certification authority."""

    NEEDS_REVIEW = "needs_review"
    QUARANTINE = "quarantine"


class ExpertCorpusFailureKind(StrEnum):
    """Closed failure classification for promotion and current-state checks."""

    MISSING = "missing"
    UNVERIFIABLE = "unverifiable"
    INVALID = "invalid"
    SUBSTITUTED = "substituted"
    FALSIFIED = "falsified"
    REVOKED = "revoked"
    CONFLICTING = "conflicting"
    UNAVAILABLE = "unavailable"
    STALE = "stale"


class ExpertCorpusPromotionError(RuntimeError):
    """Fail-closed promotion failure with an explicit local disposition."""

    def __init__(
        self,
        message: str,
        *,
        kind: ExpertCorpusFailureKind,
        reason_code: str,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.reason_code = reason_code
        self.disposition = (
            ExpertCorpusDisposition.NEEDS_REVIEW
            if kind
            in {
                ExpertCorpusFailureKind.MISSING,
                ExpertCorpusFailureKind.UNVERIFIABLE,
                ExpertCorpusFailureKind.UNAVAILABLE,
                ExpertCorpusFailureKind.STALE,
            }
            else ExpertCorpusDisposition.QUARANTINE
        )

    @property
    def independent_work_may_continue(self) -> bool:
        """A local corpus failure never aborts unrelated DAG work."""

        return True

    @property
    def publication_ready(self) -> bool:
        return False


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class VeterinaryCorpusPromotionHmacKey:
    """Opaque trust material accepted only by the corpus promotion issuer."""

    _material: bytes

    def __post_init__(self) -> None:
        if not isinstance(self._material, bytes):
            raise TypeError("veterinary corpus promotion key material must be bytes")
        if len(self._material) < _MINIMUM_KEY_BYTES:
            raise ValueError(
                "veterinary corpus promotion key material must contain at least "
                f"{_MINIMUM_KEY_BYTES} bytes"
            )


@runtime_checkable
class ExpertCorpusCurrentStatePort(Protocol):
    """Inward port for durable promotion state and revocation evidence."""

    def record_promotion(
        self,
        evidence: AuthenticatedVeterinaryExpertCorpus,
        *,
        evidence_payload_sha256: str,
    ) -> None:
        """Atomically register one newly issued promotion."""

    def require_current(
        self,
        evidence: AuthenticatedVeterinaryExpertCorpus,
        *,
        evidence_payload_sha256: str,
    ) -> None:
        """Reject unavailable, stale, conflicting, or revoked promotion state."""

    def record_revocation(
        self,
        evidence: AuthenticatedVeterinaryExpertCorpus,
        *,
        evidence_payload_sha256: str,
        reason_code: str,
    ) -> None:
        """Append a restrictive revocation without any release operation."""


@dataclass(frozen=True, slots=True)
class ExpertCorpusCurrentStateObservation:
    """Trusted monotonic observation used for expiry and rollback decisions."""

    epoch_seconds: int
    sequence: int

    def __post_init__(self) -> None:
        if type(self.epoch_seconds) is not int or self.epoch_seconds < 0:
            raise ValueError("epoch_seconds must be a non-negative integer")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("sequence must be a positive integer")


@runtime_checkable
class ExpertCorpusTrustedCurrentStatePort(Protocol):
    """Inward source of trusted current time and monotonic sequence."""

    def observe(self) -> ExpertCorpusCurrentStateObservation:
        """Return one current observation with a non-reusable sequence."""


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class VerifiedStoredExpertCorpus:
    """Issuer-only snapshot resolved from an external corpus store."""

    request: VeterinaryExpertCorpusPromotionRequest

    def __new__(cls) -> VerifiedStoredExpertCorpus:
        raise TypeError(
            "VerifiedStoredExpertCorpus is issued only through a "
            "StoredExpertCorpusCapabilityIssuer"
        )

    def __copy__(self) -> VerifiedStoredExpertCorpus:
        raise TypeError("stored expert corpus capabilities cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> VerifiedStoredExpertCorpus:
        del memo
        raise TypeError("stored expert corpus capabilities cannot be copied")

    @property
    def production_eligible(self) -> bool:
        """No external production corpus authority is selected in this phase."""

        return False


@runtime_checkable
class StoredExpertCorpusEvidencePort(Protocol):
    """Resolve exact current bytes and governance evidence from external storage."""

    def resolve_current(
        self,
        request: VeterinaryExpertCorpusPromotionRequest,
        *,
        capability_issuer: StoredExpertCorpusCapabilityIssuer,
    ) -> VerifiedStoredExpertCorpus:
        """Resolve and authenticate the complete stored corpus snapshot."""

    def require_current(
        self,
        capability: VerifiedStoredExpertCorpus,
    ) -> VeterinaryExpertCorpusPromotionRequest:
        """Revalidate exact bytes, inventory, provenance, license, and review state."""


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class StoredExpertCorpusCapabilityIssuer:
    """Authority-held issuer delegated only to one injected external resolver."""

    _evidence_port: StoredExpertCorpusEvidencePort

    def __new__(cls) -> StoredExpertCorpusCapabilityIssuer:
        raise TypeError(
            "StoredExpertCorpusCapabilityIssuer is created only by the "
            "corpus-promotion authority"
        )

    def issue(
        self,
        *,
        evidence_port: StoredExpertCorpusEvidencePort,
        request: VeterinaryExpertCorpusPromotionRequest,
    ) -> VerifiedStoredExpertCorpus:
        """Issue only for the exact resolver bound during composition."""

        _require_stored_corpus_issuer(self, evidence_port=evidence_port)
        validated = VeterinaryExpertCorpusPromotionRequest.model_validate(
            request.model_dump(mode="python", warnings=False)
        )
        return _issue_stored_corpus_capability(
            evidence_port=evidence_port,
            request=validated,
        )


@dataclass(frozen=True, slots=True)
class _StoredCorpusCapabilityRegistration:
    capability_reference: weakref.ReferenceType[VerifiedStoredExpertCorpus]
    evidence_port: StoredExpertCorpusEvidencePort
    request: VeterinaryExpertCorpusPromotionRequest


_STORED_CORPUS_CAPABILITY_REGISTRATIONS: dict[
    int,
    _StoredCorpusCapabilityRegistration,
] = {}


@dataclass(frozen=True, slots=True)
class _StoredCorpusIssuerRegistration:
    issuer_reference: weakref.ReferenceType[StoredExpertCorpusCapabilityIssuer]
    evidence_port: StoredExpertCorpusEvidencePort


_STORED_CORPUS_ISSUER_REGISTRATIONS: dict[
    int,
    _StoredCorpusIssuerRegistration,
] = {}


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class VerifiedExpertQualification:
    """Issuer-only result that remains authoritative through its external port."""

    qualification: ExpertQualificationReference

    def __new__(cls) -> VerifiedExpertQualification:
        raise TypeError(
            "VerifiedExpertQualification is issued only through an "
            "ExpertQualificationCapabilityIssuer"
        )

    def __copy__(self) -> VerifiedExpertQualification:
        raise TypeError("expert qualification capabilities cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> VerifiedExpertQualification:
        del memo
        raise TypeError("expert qualification capabilities cannot be copied")


@runtime_checkable
class ExpertQualificationEvidencePort(Protocol):
    """Inward boundary for external qualification authentication and currency."""

    def verify_current(
        self,
        qualification: ExpertQualificationReference,
        *,
        capability_issuer: ExpertQualificationCapabilityIssuer,
    ) -> VerifiedExpertQualification:
        """Authenticate an exact qualification and return an issuer-owned capability."""

    def require_current(
        self,
        capability: VerifiedExpertQualification,
    ) -> ExpertQualificationReference:
        """Revalidate issuer provenance, scope, expiry, and revocation."""


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class ExpertQualificationCapabilityIssuer:
    """Authority-held issuer delegated only to the injected external verifier."""

    _evidence_port: ExpertQualificationEvidencePort

    def __new__(cls) -> ExpertQualificationCapabilityIssuer:
        raise TypeError(
            "ExpertQualificationCapabilityIssuer is created only by the "
            "corpus-promotion authority"
        )

    def issue(
        self,
        *,
        evidence_port: ExpertQualificationEvidencePort,
        qualification: ExpertQualificationReference,
    ) -> VerifiedExpertQualification:
        """Issue only for the exact verifier bound during composition."""

        _require_qualification_issuer(self, evidence_port=evidence_port)
        validated = ExpertQualificationReference.model_validate(
            qualification.model_dump(mode="python", warnings=False)
        )
        return _issue_qualification_capability(
            evidence_port=evidence_port,
            qualification=validated,
        )


@dataclass(frozen=True, slots=True)
class _QualificationCapabilityRegistration:
    capability_reference: weakref.ReferenceType[VerifiedExpertQualification]
    evidence_port: ExpertQualificationEvidencePort
    qualification: ExpertQualificationReference


_QUALIFICATION_CAPABILITY_REGISTRATIONS: dict[
    int,
    _QualificationCapabilityRegistration,
] = {}


@dataclass(frozen=True, slots=True)
class _QualificationIssuerRegistration:
    issuer_reference: weakref.ReferenceType[ExpertQualificationCapabilityIssuer]
    evidence_port: ExpertQualificationEvidencePort


_QUALIFICATION_ISSUER_REGISTRATIONS: dict[
    int,
    _QualificationIssuerRegistration,
] = {}


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class VerifiedVeterinaryExpertCorpus:
    """Issuer-only capability for one exact, currently promoted corpus."""

    evidence: AuthenticatedVeterinaryExpertCorpus

    def __new__(cls) -> VerifiedVeterinaryExpertCorpus:
        raise TypeError(
            "VerifiedVeterinaryExpertCorpus is issued only by "
            "VeterinaryCorpusPromotionHmacAuthority"
        )

    def __copy__(self) -> VerifiedVeterinaryExpertCorpus:
        raise TypeError("promoted corpus capabilities cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> VerifiedVeterinaryExpertCorpus:
        del memo
        raise TypeError("promoted corpus capabilities cannot be copied")

    @property
    def publication_ready(self) -> bool:
        """Corpus promotion never grants publication authority."""

        return False

    @property
    def production_eligible(self) -> bool:
        """External production qualification authority is not selected in this phase."""

        return False


@dataclass(frozen=True, slots=True)
class _CapabilityRegistration:
    capability_reference: weakref.ReferenceType[VerifiedVeterinaryExpertCorpus]
    authority_reference: weakref.ReferenceType[VeterinaryCorpusPromotionHmacAuthority]
    evidence_payload_sha256: str
    detached_signature: str
    qualification_evidence: ExpertQualificationEvidencePort
    qualification_capabilities: tuple[VerifiedExpertQualification, ...]
    stored_corpus_evidence: StoredExpertCorpusEvidencePort
    stored_corpus_capability: VerifiedStoredExpertCorpus


_CAPABILITY_REGISTRATIONS: dict[int, _CapabilityRegistration] = {}


class VeterinaryCorpusPromotionHmacAuthority:
    """Authenticate exact expert-corpus promotion evidence only."""

    def __init__(
        self,
        *,
        key: VeterinaryCorpusPromotionHmacKey,
        authority_id: str,
        current_state: ExpertCorpusCurrentStatePort,
        qualification_evidence: ExpertQualificationEvidencePort,
        stored_corpus_evidence: StoredExpertCorpusEvidencePort,
    ) -> None:
        if not isinstance(key, VeterinaryCorpusPromotionHmacKey):
            raise TypeError("key must be a VeterinaryCorpusPromotionHmacKey")
        if not isinstance(current_state, ExpertCorpusCurrentStatePort):
            raise TypeError("current_state must implement ExpertCorpusCurrentStatePort")
        if not isinstance(qualification_evidence, ExpertQualificationEvidencePort):
            raise TypeError(
                "qualification_evidence must implement ExpertQualificationEvidencePort"
            )
        if not isinstance(stored_corpus_evidence, StoredExpertCorpusEvidencePort):
            raise TypeError(
                "stored_corpus_evidence must implement StoredExpertCorpusEvidencePort"
            )
        self._key = key
        self._authority_id = str(
            _SAFE_IDENTIFIER_ADAPTER.validate_python(authority_id, strict=True)
        )
        self._current_state = current_state
        self._qualification_evidence = qualification_evidence
        self._stored_corpus_evidence = stored_corpus_evidence
        self._qualification_capability_issuer = _create_qualification_capability_issuer(
            qualification_evidence
        )
        self._stored_corpus_capability_issuer = _create_stored_corpus_capability_issuer(
            stored_corpus_evidence
        )

    @property
    def key_id(self) -> str:
        """Return the key identity in the corpus-promotion trust domain."""

        return hashlib.sha256(_KEY_ID_CONTEXT + self._key._material).hexdigest()

    @property
    def authority_id(self) -> str:
        return self._authority_id

    def issue(
        self,
        request: VeterinaryExpertCorpusPromotionRequest,
    ) -> VerifiedVeterinaryExpertCorpus:
        """Validate, sign, register, and issue one exact promoted corpus."""

        validated_request = VeterinaryExpertCorpusPromotionRequest.model_validate(
            request.model_dump(mode="python", warnings=False)
        )
        self._require_promotable(validated_request)
        qualifications = self._verify_current_qualifications(validated_request)
        stored_corpus = self._verify_stored_corpus(validated_request)
        authentication_payload = _expert_corpus_authentication_payload(
            request=validated_request,
            trust_key_id=self.key_id,
            authority_id=self.authority_id,
        )
        evidence = AuthenticatedVeterinaryExpertCorpus(
            request=validated_request,
            cryptographic_domain="lecture-study.veterinary-expert-corpus.v1",
            signature_purpose="expert-corpus-promotion",
            trust_key_id=self.key_id,
            authority_id=self.authority_id,
            detached_signature=self._sign(authentication_payload),
        )
        payload_sha256 = canonical_expert_corpus_evidence_sha256(evidence)
        self._record_promotion(evidence, payload_sha256)
        self._require_state_current(evidence, payload_sha256)
        self._require_qualifications_current(validated_request, qualifications)
        self._require_stored_corpus_current(validated_request, stored_corpus)
        return _issue_capability(
            authority=self,
            evidence=evidence,
            qualification_capabilities=qualifications,
            stored_corpus_capability=stored_corpus,
        )

    def verify(
        self,
        evidence: AuthenticatedVeterinaryExpertCorpus,
    ) -> VerifiedVeterinaryExpertCorpus:
        """Authenticate stored evidence and its durable current state."""

        try:
            validated = AuthenticatedVeterinaryExpertCorpus.model_validate(
                evidence.model_dump(mode="python", warnings=False)
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise ExpertCorpusPromotionError(
                "expert corpus evidence is structurally invalid",
                kind=ExpertCorpusFailureKind.INVALID,
                reason_code="invalid_corpus_evidence",
            ) from error
        self._require_authentic(validated)
        self._require_promotable(validated.request)
        qualifications = self._verify_current_qualifications(validated.request)
        stored_corpus = self._verify_stored_corpus(validated.request)
        payload_sha256 = canonical_expert_corpus_evidence_sha256(validated)
        self._require_state_current(validated, payload_sha256)
        self._require_qualifications_current(validated.request, qualifications)
        self._require_stored_corpus_current(validated.request, stored_corpus)
        return _issue_capability(
            authority=self,
            evidence=validated,
            qualification_capabilities=qualifications,
            stored_corpus_capability=stored_corpus,
        )

    def require_current(
        self,
        capability: VerifiedVeterinaryExpertCorpus,
    ) -> AuthenticatedVeterinaryExpertCorpus:
        """Revalidate capability provenance, signature, and revocation state."""

        if type(capability) is not VerifiedVeterinaryExpertCorpus:
            raise ExpertCorpusPromotionError(
                "expert corpus capability type is invalid",
                kind=ExpertCorpusFailureKind.FALSIFIED,
                reason_code="forged_corpus_capability",
            )
        registration = _CAPABILITY_REGISTRATIONS.get(id(capability))
        if (
            registration is None
            or registration.capability_reference() is not capability
            or registration.authority_reference() is not self
            or registration.qualification_evidence is not self._qualification_evidence
            or registration.stored_corpus_evidence is not self._stored_corpus_evidence
        ):
            raise ExpertCorpusPromotionError(
                "expert corpus capability was not issued by this authority",
                kind=ExpertCorpusFailureKind.FALSIFIED,
                reason_code="foreign_corpus_capability",
            )
        evidence = AuthenticatedVeterinaryExpertCorpus.model_validate(
            capability.evidence.model_dump(mode="python", warnings=False)
        )
        payload_sha256 = canonical_expert_corpus_evidence_sha256(evidence)
        if not hmac.compare_digest(
            registration.evidence_payload_sha256,
            payload_sha256,
        ) or not hmac.compare_digest(
            registration.detached_signature,
            evidence.detached_signature,
        ):
            raise ExpertCorpusPromotionError(
                "expert corpus capability evidence was substituted",
                kind=ExpertCorpusFailureKind.SUBSTITUTED,
                reason_code="substituted_corpus_capability",
            )
        self._require_promotable(evidence.request)
        self._require_authentic(evidence)
        self._require_state_current(evidence, payload_sha256)
        self._require_qualifications_current(
            evidence.request,
            registration.qualification_capabilities,
        )
        self._require_stored_corpus_current(
            evidence.request,
            registration.stored_corpus_capability,
        )
        return evidence

    def _verify_stored_corpus(
        self,
        request: VeterinaryExpertCorpusPromotionRequest,
    ) -> VerifiedStoredExpertCorpus:
        try:
            capability = self._stored_corpus_evidence.resolve_current(
                request,
                capability_issuer=self._stored_corpus_capability_issuer,
            )
        except ExpertCorpusPromotionError:
            raise
        except Exception as error:
            raise ExpertCorpusPromotionError(
                "external stored expert corpus evidence is unavailable",
                kind=ExpertCorpusFailureKind.UNAVAILABLE,
                reason_code="stored_corpus_evidence_unavailable",
            ) from error
        if type(capability) is not VerifiedStoredExpertCorpus:
            _raise_quarantine(
                "external stored corpus capability is invalid",
                "forged_stored_corpus_capability",
            )
        _require_stored_corpus_capability(
            capability,
            evidence_port=self._stored_corpus_evidence,
        )
        self._require_exact_stored_corpus(capability, request)
        return capability

    def _require_stored_corpus_current(
        self,
        request: VeterinaryExpertCorpusPromotionRequest,
        capability: VerifiedStoredExpertCorpus,
    ) -> None:
        _require_stored_corpus_capability(
            capability,
            evidence_port=self._stored_corpus_evidence,
        )
        try:
            current = self._stored_corpus_evidence.require_current(capability)
        except ExpertCorpusPromotionError:
            raise
        except Exception as error:
            raise ExpertCorpusPromotionError(
                "external stored corpus current state is unavailable",
                kind=ExpertCorpusFailureKind.UNAVAILABLE,
                reason_code="stored_corpus_current_state_unavailable",
            ) from error
        _require_stored_corpus_capability(
            capability,
            evidence_port=self._stored_corpus_evidence,
        )
        self._require_exact_stored_corpus(capability, request)
        if current != request:
            _raise_quarantine(
                "external stored corpus current state was substituted",
                "substituted_stored_corpus_current_state",
            )

    @staticmethod
    def _require_exact_stored_corpus(
        capability: VerifiedStoredExpertCorpus,
        request: VeterinaryExpertCorpusPromotionRequest,
    ) -> None:
        if capability.request != request:
            _raise_quarantine(
                "external stored corpus capability was substituted",
                "substituted_stored_corpus_capability",
            )

    def _verify_current_qualifications(
        self,
        request: VeterinaryExpertCorpusPromotionRequest,
    ) -> tuple[VerifiedExpertQualification, ...]:
        capabilities: list[VerifiedExpertQualification] = []
        for qualification in request.qualifications:
            try:
                capability = self._qualification_evidence.verify_current(
                    qualification,
                    capability_issuer=self._qualification_capability_issuer,
                )
            except ExpertCorpusPromotionError:
                raise
            except Exception as error:
                raise ExpertCorpusPromotionError(
                    "external expert qualification evidence is unavailable",
                    kind=ExpertCorpusFailureKind.UNAVAILABLE,
                    reason_code="qualification_evidence_unavailable",
                ) from error
            if type(capability) is not VerifiedExpertQualification:
                _raise_quarantine(
                    "external qualification capability is invalid",
                    "forged_qualification_capability",
                )
            _require_qualification_capability(
                capability,
                evidence_port=self._qualification_evidence,
            )
            self._require_exact_qualification(capability, qualification)
            capabilities.append(capability)
        return tuple(capabilities)

    def _require_qualifications_current(
        self,
        request: VeterinaryExpertCorpusPromotionRequest,
        capabilities: tuple[VerifiedExpertQualification, ...],
    ) -> None:
        if len(capabilities) != len(request.qualifications):
            _raise_quarantine(
                "external qualification capability inventory is incomplete",
                "qualification_capability_inventory_mismatch",
            )
        for qualification, capability in zip(
            request.qualifications,
            capabilities,
            strict=True,
        ):
            _require_qualification_capability(
                capability,
                evidence_port=self._qualification_evidence,
            )
            try:
                current = self._qualification_evidence.require_current(capability)
            except ExpertCorpusPromotionError:
                raise
            except Exception as error:
                raise ExpertCorpusPromotionError(
                    "external expert qualification current state is unavailable",
                    kind=ExpertCorpusFailureKind.UNAVAILABLE,
                    reason_code="qualification_current_state_unavailable",
                ) from error
            _require_qualification_capability(
                capability,
                evidence_port=self._qualification_evidence,
            )
            self._require_exact_qualification(capability, qualification)
            if current != qualification:
                _raise_quarantine(
                    "external qualification current state was substituted",
                    "substituted_qualification_current_state",
                )

    @staticmethod
    def _require_exact_qualification(
        capability: VerifiedExpertQualification,
        qualification: ExpertQualificationReference,
    ) -> None:
        if capability.qualification != qualification:
            _raise_quarantine(
                "external qualification capability was substituted",
                "substituted_qualification_capability",
            )

    def _require_promotable(
        self,
        request: VeterinaryExpertCorpusPromotionRequest,
    ) -> None:
        self._require_usable_license(
            request.provenance.license,
            reason_prefix="corpus",
        )
        if (
            not request.inventory
            or not request.annotations
            or not request.adjudications
        ):
            _raise_needs_review(
                "corpus expert review evidence is incomplete",
                "missing_expert_review_evidence",
            )
        if not request.qualifications:
            _raise_needs_review(
                "corpus expert qualification evidence is missing",
                "missing_expert_qualifications",
            )
        if any(
            event.event_record_sha256 != canonical_corpus_custody_event_sha256(event)
            for event in request.provenance.custody_history
        ):
            _raise_quarantine(
                "corpus custody event identity is substituted",
                "corpus_custody_event_hash_mismatch",
            )

        expected_cases = {
            str(case.case_id): case for case in request.corpus_manifest.cases
        }
        inventory = {str(item.case_id): item for item in request.inventory}
        if set(inventory) != set(expected_cases):
            _raise_quarantine(
                "promoted inventory differs from the corpus manifest",
                "corpus_inventory_mismatch",
            )
        if request.provenance.immutable_inventory_sha256 != (
            canonical_expert_corpus_inventory_sha256(request.inventory)
        ):
            _raise_quarantine(
                "corpus immutable inventory identity is substituted",
                "corpus_inventory_hash_mismatch",
            )

        annotations = {
            str(annotation.annotation_id): annotation
            for annotation in request.annotations
        }
        adjudications = {
            str(adjudication.adjudication_id): adjudication
            for adjudication in request.adjudications
        }
        policy_sha256 = canonical_expert_annotation_policy_sha256(
            request.annotation_policy
        )
        used_annotation_ids: set[str] = set()
        used_adjudication_ids: set[str] = set()
        used_qualification_keys: set[tuple[str, str]] = set()
        qualifications = {
            (
                str(item.pseudonymous_expert_id),
                str(item.qualification_reference_id),
            ): item
            for item in request.qualifications
        }
        self._require_qualification_scopes(request)

        for case_id, item in inventory.items():
            self._require_record_governance(request, item)
            expected_case = expected_cases[case_id]
            if item.reference_case_sha256 != (
                canonical_reference_corpus_case_sha256(expected_case)
            ):
                _raise_quarantine(
                    "reference case identity is substituted",
                    "reference_case_hash_mismatch",
                )
            case_annotations = self._require_case_annotations(
                request=request,
                item=item,
                annotations=annotations,
                policy_sha256=policy_sha256,
                qualifications=qualifications,
            )
            adjudication = adjudications.get(str(item.adjudication_id))
            if adjudication is None:
                _raise_needs_review(
                    "corpus case adjudication is missing",
                    "missing_case_adjudication",
                )
            self._require_case_adjudication(
                request=request,
                item=item,
                annotations=case_annotations,
                adjudication=adjudication,
                policy_sha256=policy_sha256,
                qualifications=qualifications,
            )
            used_annotation_ids.update(
                str(value.annotation_id) for value in case_annotations
            )
            used_adjudication_ids.add(str(adjudication.adjudication_id))
            used_qualification_keys.update(
                (
                    str(value.pseudonymous_expert_id),
                    str(value.qualification_reference_id),
                )
                for value in case_annotations
            )
            used_qualification_keys.add(
                (
                    str(adjudication.pseudonymous_expert_id),
                    str(adjudication.qualification_reference_id),
                )
            )

        if used_annotation_ids != set(annotations):
            _raise_quarantine(
                "corpus contains unbound or omitted annotations",
                "annotation_inventory_mismatch",
            )
        if used_adjudication_ids != set(adjudications):
            _raise_quarantine(
                "corpus contains unbound or omitted adjudications",
                "adjudication_inventory_mismatch",
            )
        if used_qualification_keys != set(qualifications):
            _raise_quarantine(
                "corpus contains unbound or omitted qualifications",
                "qualification_inventory_mismatch",
            )

    def _require_qualification_scopes(
        self,
        request: VeterinaryExpertCorpusPromotionRequest,
    ) -> None:
        required_scope = {
            str(scope_id) for scope_id in request.permitted_clinical_scope_ids
        }
        for qualification in request.qualifications:
            if qualification.specialty_id != request.specialty_id:
                _raise_quarantine(
                    "expert qualification specialty differs from the corpus",
                    "expert_specialty_mismatch",
                )
            qualification_scope = {
                str(scope_id) for scope_id in qualification.permitted_clinical_scope_ids
            }
            if not required_scope.issubset(qualification_scope):
                _raise_quarantine(
                    "expert qualification does not cover the corpus clinical scope",
                    "expert_clinical_scope_mismatch",
                )

    def _require_record_governance(
        self,
        request: VeterinaryExpertCorpusPromotionRequest,
        item: ExpertCorpusInventoryItem,
    ) -> None:
        self._require_usable_license(
            item.license,
            reason_prefix="record",
        )
        permitted_custodians = {
            str(request.provenance.ownership_reference_id),
            *(
                str(event.custodian_reference_id)
                for event in request.provenance.custody_history
            ),
        }
        if str(item.owner_or_custodian_reference_id) not in permitted_custodians:
            _raise_quarantine(
                "record ownership or custodianship is not in the provenance chain",
                "record_custodian_mismatch",
            )
        expected_provenance = tuple(
            event.event_record_sha256 for event in request.provenance.custody_history
        )
        if item.provenance_event_sha256s != expected_provenance:
            _raise_quarantine(
                "record provenance chain differs from the corpus custody chain",
                "record_provenance_chain_mismatch",
            )
        annotations = {
            str(annotation.annotation_id): annotation
            for annotation in request.annotations
        }
        if any(
            str(annotation_id) not in annotations
            for annotation_id in item.annotation_ids
        ):
            _raise_needs_review(
                "record annotation history cannot resolve every annotation",
                "missing_record_annotation_history",
            )
        expected_annotation_history = tuple(
            sorted(
                annotations[str(annotation_id)].annotation_payload_sha256
                for annotation_id in item.annotation_ids
            )
        )
        if item.annotation_history_sha256s != expected_annotation_history:
            _raise_quarantine(
                "record annotation history differs from exact annotation evidence",
                "record_annotation_history_mismatch",
            )
        adjudications = {
            str(adjudication.adjudication_id): adjudication
            for adjudication in request.adjudications
        }
        adjudication = adjudications.get(str(item.adjudication_id))
        if adjudication is None:
            _raise_needs_review(
                "record expert review history cannot resolve its adjudication",
                "missing_record_adjudication_history",
            )
        expected_review_history = tuple(
            sorted(
                (
                    *expected_annotation_history,
                    adjudication.adjudicated_payload_sha256,
                    adjudication.disagreement_record_sha256,
                )
            )
        )
        if item.expert_review_history_sha256s != expected_review_history:
            _raise_quarantine(
                "record expert review history differs from exact review evidence",
                "record_expert_review_history_mismatch",
            )

    @staticmethod
    def _require_usable_license(
        license_evidence: CorpusLicenseEvidence,
        *,
        reason_prefix: str,
    ) -> None:
        if license_evidence.permission_status in {
            "unknown",
            "ambiguous",
            "expired",
        }:
            _raise_needs_review(
                "corpus permission cannot be verified",
                f"{reason_prefix}_permission_{license_evidence.permission_status}",
            )
        if license_evidence.permission_status in {
            "incompatible",
            "revoked",
            "conflicting",
        }:
            _raise_quarantine(
                "corpus permission is not admissible",
                f"{reason_prefix}_permission_{license_evidence.permission_status}",
            )
        if not license_evidence.permits_evaluation_use:
            _raise_quarantine(
                "corpus license does not permit evaluation",
                f"{reason_prefix}_evaluation_use_not_licensed",
            )

    def _require_case_annotations(
        self,
        *,
        request: VeterinaryExpertCorpusPromotionRequest,
        item: ExpertCorpusInventoryItem,
        annotations: dict[str, ExpertAnnotationEvidence],
        policy_sha256: str,
        qualifications: dict[tuple[str, str], ExpertQualificationReference],
    ) -> tuple[ExpertAnnotationEvidence, ...]:
        case_annotations: list[ExpertAnnotationEvidence] = []
        for annotation_id in item.annotation_ids:
            annotation = annotations.get(str(annotation_id))
            if annotation is None:
                _raise_needs_review(
                    "corpus case annotation is missing",
                    "missing_case_annotation",
                )
            if (
                annotation.case_id != item.case_id
                or annotation.annotation_policy_sha256 != policy_sha256
            ):
                _raise_quarantine(
                    "corpus case annotation is substituted",
                    "substituted_case_annotation",
                )
            self._require_active_qualification(
                request=request,
                case_id=str(item.case_id),
                expert_id=str(annotation.pseudonymous_expert_id),
                qualification_id=str(annotation.qualification_reference_id),
                qualifications=qualifications,
            )
            case_annotations.append(annotation)

        expert_ids = {
            str(annotation.pseudonymous_expert_id) for annotation in case_annotations
        }
        required = request.annotation_policy.minimum_independent_annotators
        if len(case_annotations) < required or len(expert_ids) < required:
            _raise_needs_review(
                "corpus case lacks the required independent annotators",
                "insufficient_independent_annotators",
            )
        return tuple(case_annotations)

    def _require_case_adjudication(
        self,
        *,
        request: VeterinaryExpertCorpusPromotionRequest,
        item: ExpertCorpusInventoryItem,
        annotations: tuple[ExpertAnnotationEvidence, ...],
        adjudication: ExpertAdjudicationEvidence,
        policy_sha256: str,
        qualifications: dict[tuple[str, str], ExpertQualificationReference],
    ) -> None:
        expected_annotation_ids = tuple(
            sorted(str(annotation.annotation_id) for annotation in annotations)
        )
        if (
            adjudication.case_id != item.case_id
            or tuple(str(value) for value in adjudication.annotation_ids)
            != expected_annotation_ids
            or adjudication.annotation_policy_sha256 != policy_sha256
        ):
            _raise_quarantine(
                "corpus case adjudication is substituted",
                "substituted_case_adjudication",
            )
        annotator_ids = {
            str(annotation.pseudonymous_expert_id) for annotation in annotations
        }
        if str(adjudication.pseudonymous_expert_id) in annotator_ids:
            _raise_quarantine(
                "corpus adjudicator is not independent from annotators",
                "non_independent_adjudicator",
            )
        self._require_active_qualification(
            request=request,
            case_id=str(item.case_id),
            expert_id=str(adjudication.pseudonymous_expert_id),
            qualification_id=str(adjudication.qualification_reference_id),
            qualifications=qualifications,
        )

    @staticmethod
    def _require_active_qualification(
        *,
        request: VeterinaryExpertCorpusPromotionRequest,
        case_id: str,
        expert_id: str,
        qualification_id: str,
        qualifications: dict[tuple[str, str], ExpertQualificationReference],
    ) -> None:
        qualification = qualifications.get((expert_id, qualification_id))
        if qualification is None:
            _raise_needs_review(
                "expert qualification cannot be verified",
                "missing_expert_qualification",
            )
        if qualification.revoked_at_epoch_seconds is not None:
            _raise_quarantine(
                "expert qualification was revoked",
                "revoked_expert_qualification",
            )
        if not (
            qualification.valid_from_epoch_seconds
            <= request.issued_at_epoch_seconds
            < qualification.valid_until_epoch_seconds
        ):
            _raise_needs_review(
                "expert qualification is outside its validity window",
                "expired_expert_qualification",
            )
        if case_id not in {str(value) for value in qualification.authorized_case_ids}:
            _raise_quarantine(
                "expert qualification does not authorize the corpus case",
                "expert_scope_mismatch",
            )
        if qualification.specialty_id != request.specialty_id:
            _raise_quarantine(
                "expert qualification specialty differs from the corpus",
                "expert_specialty_mismatch",
            )
        if not {
            str(scope_id) for scope_id in request.permitted_clinical_scope_ids
        }.issubset(
            {str(scope_id) for scope_id in qualification.permitted_clinical_scope_ids}
        ):
            _raise_quarantine(
                "expert qualification does not authorize the clinical scope",
                "expert_clinical_scope_mismatch",
            )

    def _require_authentic(
        self,
        evidence: AuthenticatedVeterinaryExpertCorpus,
    ) -> None:
        if (
            evidence.cryptographic_domain
            != VETERINARY_EXPERT_CORPUS_CRYPTOGRAPHIC_DOMAIN
            or evidence.signature_purpose != VETERINARY_EXPERT_CORPUS_SIGNATURE_PURPOSE
            or not hmac.compare_digest(evidence.trust_key_id, self.key_id)
            or str(evidence.authority_id) != self.authority_id
        ):
            _raise_quarantine(
                "expert corpus trust scope is invalid",
                "expert_corpus_trust_confusion",
            )
        expected = self._sign(evidence.authentication_payload())
        if not hmac.compare_digest(expected, evidence.detached_signature):
            _raise_quarantine(
                "expert corpus signature is invalid",
                "invalid_expert_corpus_signature",
            )

    def _record_promotion(
        self,
        evidence: AuthenticatedVeterinaryExpertCorpus,
        payload_sha256: str,
    ) -> None:
        try:
            self._current_state.record_promotion(
                evidence,
                evidence_payload_sha256=payload_sha256,
            )
        except ExpertCorpusPromotionError:
            raise
        except Exception as error:
            raise ExpertCorpusPromotionError(
                "expert corpus current-state registration is unavailable",
                kind=ExpertCorpusFailureKind.UNAVAILABLE,
                reason_code="corpus_state_registration_unavailable",
            ) from error

    def _require_state_current(
        self,
        evidence: AuthenticatedVeterinaryExpertCorpus,
        payload_sha256: str,
    ) -> None:
        try:
            self._current_state.require_current(
                evidence,
                evidence_payload_sha256=payload_sha256,
            )
        except ExpertCorpusPromotionError:
            raise
        except Exception as error:
            raise ExpertCorpusPromotionError(
                "expert corpus current state is unavailable",
                kind=ExpertCorpusFailureKind.UNAVAILABLE,
                reason_code="corpus_current_state_unavailable",
            ) from error

    def _sign(self, payload: bytes) -> str:
        return hmac.new(
            self._key._material,
            _SIGNATURE_CONTEXT + payload,
            hashlib.sha256,
        ).hexdigest()


def canonical_expert_annotation_policy_sha256(
    policy: ExpertAnnotationPolicy,
) -> str:
    """Identify the complete versioned annotation policy in its own domain."""

    validated = ExpertAnnotationPolicy.model_validate(
        policy.model_dump(mode="python", warnings=False)
    )
    return hashlib.sha256(
        _ANNOTATION_POLICY_CONTEXT
        + canonical_json_bytes(validated.model_dump(mode="json"))
    ).hexdigest()


def _expert_corpus_authentication_payload(
    *,
    request: VeterinaryExpertCorpusPromotionRequest,
    trust_key_id: str,
    authority_id: str,
) -> bytes:
    return canonical_json_bytes(
        {
            "authority_id": authority_id,
            "cryptographic_domain": VETERINARY_EXPERT_CORPUS_CRYPTOGRAPHIC_DOMAIN,
            "request": request.model_dump(mode="json"),
            "signature_purpose": VETERINARY_EXPERT_CORPUS_SIGNATURE_PURPOSE,
            "trust_key_id": trust_key_id,
        }
    )


def canonical_corpus_custody_event_sha256(event: CorpusCustodyEvent) -> str:
    """Identify every custody-event field except its self-referential identity."""

    validated = CorpusCustodyEvent.model_validate(
        event.model_dump(mode="python", warnings=False)
    )
    return hashlib.sha256(
        _CUSTODY_EVENT_CONTEXT
        + canonical_json_bytes(
            validated.model_dump(mode="json", exclude={"event_record_sha256"})
        )
    ).hexdigest()


def canonical_expert_corpus_inventory_sha256(
    inventory: tuple[ExpertCorpusInventoryItem, ...],
) -> str:
    """Identify the exact canonical promoted inventory."""

    validated = tuple(
        ExpertCorpusInventoryItem.model_validate(
            item.model_dump(mode="python", warnings=False)
        )
        for item in inventory
    )
    return hashlib.sha256(
        _INVENTORY_CONTEXT
        + canonical_json_bytes([item.model_dump(mode="json") for item in validated])
    ).hexdigest()


def canonical_expert_corpus_evidence_sha256(
    evidence: AuthenticatedVeterinaryExpertCorpus,
) -> str:
    """Identify the entire signed payload without reusing another trust domain."""

    validated = AuthenticatedVeterinaryExpertCorpus.model_validate(
        evidence.model_dump(mode="python", warnings=False)
    )
    return hashlib.sha256(
        _IDENTITY_CONTEXT + validated.authentication_payload()
    ).hexdigest()


def _issue_capability(
    *,
    authority: VeterinaryCorpusPromotionHmacAuthority,
    evidence: AuthenticatedVeterinaryExpertCorpus,
    qualification_capabilities: tuple[VerifiedExpertQualification, ...],
    stored_corpus_capability: VerifiedStoredExpertCorpus,
) -> VerifiedVeterinaryExpertCorpus:
    capability = object.__new__(VerifiedVeterinaryExpertCorpus)
    object.__setattr__(capability, "evidence", evidence)
    capability_id = id(capability)

    def discard(
        reference: weakref.ReferenceType[VerifiedVeterinaryExpertCorpus],
    ) -> None:
        registration = _CAPABILITY_REGISTRATIONS.get(capability_id)
        if registration is not None and registration.capability_reference is reference:
            _CAPABILITY_REGISTRATIONS.pop(capability_id, None)

    reference = weakref.ref(capability, discard)
    _CAPABILITY_REGISTRATIONS[capability_id] = _CapabilityRegistration(
        capability_reference=reference,
        authority_reference=weakref.ref(authority),
        evidence_payload_sha256=canonical_expert_corpus_evidence_sha256(evidence),
        detached_signature=evidence.detached_signature,
        qualification_evidence=authority._qualification_evidence,
        qualification_capabilities=qualification_capabilities,
        stored_corpus_evidence=authority._stored_corpus_evidence,
        stored_corpus_capability=stored_corpus_capability,
    )
    return capability


def _create_stored_corpus_capability_issuer(
    evidence_port: StoredExpertCorpusEvidencePort,
) -> StoredExpertCorpusCapabilityIssuer:
    issuer = object.__new__(StoredExpertCorpusCapabilityIssuer)
    object.__setattr__(issuer, "_evidence_port", evidence_port)
    issuer_id = id(issuer)

    def discard(
        reference: weakref.ReferenceType[StoredExpertCorpusCapabilityIssuer],
    ) -> None:
        registration = _STORED_CORPUS_ISSUER_REGISTRATIONS.get(issuer_id)
        if registration is not None and registration.issuer_reference is reference:
            _STORED_CORPUS_ISSUER_REGISTRATIONS.pop(issuer_id, None)

    reference = weakref.ref(issuer, discard)
    _STORED_CORPUS_ISSUER_REGISTRATIONS[issuer_id] = _StoredCorpusIssuerRegistration(
        issuer_reference=reference,
        evidence_port=evidence_port,
    )
    return issuer


def _require_stored_corpus_issuer(
    issuer: StoredExpertCorpusCapabilityIssuer,
    *,
    evidence_port: StoredExpertCorpusEvidencePort,
) -> None:
    registration = _STORED_CORPUS_ISSUER_REGISTRATIONS.get(id(issuer))
    if (
        type(issuer) is not StoredExpertCorpusCapabilityIssuer
        or registration is None
        or registration.issuer_reference() is not issuer
        or registration.evidence_port is not evidence_port
        or issuer._evidence_port is not evidence_port
    ):
        raise ExpertCorpusPromotionError(
            "stored corpus issuer was not delegated to this resolver",
            kind=ExpertCorpusFailureKind.FALSIFIED,
            reason_code="foreign_stored_corpus_issuer",
        )


def _issue_stored_corpus_capability(
    *,
    evidence_port: StoredExpertCorpusEvidencePort,
    request: VeterinaryExpertCorpusPromotionRequest,
) -> VerifiedStoredExpertCorpus:
    capability = object.__new__(VerifiedStoredExpertCorpus)
    object.__setattr__(capability, "request", request)
    capability_id = id(capability)

    def discard(
        reference: weakref.ReferenceType[VerifiedStoredExpertCorpus],
    ) -> None:
        registration = _STORED_CORPUS_CAPABILITY_REGISTRATIONS.get(capability_id)
        if registration is not None and registration.capability_reference is reference:
            _STORED_CORPUS_CAPABILITY_REGISTRATIONS.pop(capability_id, None)

    reference = weakref.ref(capability, discard)
    _STORED_CORPUS_CAPABILITY_REGISTRATIONS[capability_id] = (
        _StoredCorpusCapabilityRegistration(
            capability_reference=reference,
            evidence_port=evidence_port,
            request=request,
        )
    )
    return capability


def _require_stored_corpus_capability(
    capability: VerifiedStoredExpertCorpus,
    *,
    evidence_port: StoredExpertCorpusEvidencePort,
) -> None:
    registration = _STORED_CORPUS_CAPABILITY_REGISTRATIONS.get(id(capability))
    if (
        type(capability) is not VerifiedStoredExpertCorpus
        or registration is None
        or registration.capability_reference() is not capability
        or registration.evidence_port is not evidence_port
        or registration.request != capability.request
    ):
        raise ExpertCorpusPromotionError(
            "stored expert corpus capability was not issued by this resolver",
            kind=ExpertCorpusFailureKind.FALSIFIED,
            reason_code="foreign_stored_corpus_capability",
        )


def _create_qualification_capability_issuer(
    evidence_port: ExpertQualificationEvidencePort,
) -> ExpertQualificationCapabilityIssuer:
    issuer = object.__new__(ExpertQualificationCapabilityIssuer)
    object.__setattr__(issuer, "_evidence_port", evidence_port)
    issuer_id = id(issuer)

    def discard(
        reference: weakref.ReferenceType[ExpertQualificationCapabilityIssuer],
    ) -> None:
        registration = _QUALIFICATION_ISSUER_REGISTRATIONS.get(issuer_id)
        if registration is not None and registration.issuer_reference is reference:
            _QUALIFICATION_ISSUER_REGISTRATIONS.pop(issuer_id, None)

    reference = weakref.ref(issuer, discard)
    _QUALIFICATION_ISSUER_REGISTRATIONS[issuer_id] = _QualificationIssuerRegistration(
        issuer_reference=reference,
        evidence_port=evidence_port,
    )
    return issuer


def _require_qualification_issuer(
    issuer: ExpertQualificationCapabilityIssuer,
    *,
    evidence_port: ExpertQualificationEvidencePort,
) -> None:
    registration = _QUALIFICATION_ISSUER_REGISTRATIONS.get(id(issuer))
    if (
        type(issuer) is not ExpertQualificationCapabilityIssuer
        or registration is None
        or registration.issuer_reference() is not issuer
        or registration.evidence_port is not evidence_port
        or issuer._evidence_port is not evidence_port
    ):
        raise ExpertCorpusPromotionError(
            "qualification issuer was not delegated to this verifier",
            kind=ExpertCorpusFailureKind.FALSIFIED,
            reason_code="foreign_qualification_issuer",
        )


def _issue_qualification_capability(
    *,
    evidence_port: ExpertQualificationEvidencePort,
    qualification: ExpertQualificationReference,
) -> VerifiedExpertQualification:
    capability = object.__new__(VerifiedExpertQualification)
    object.__setattr__(capability, "qualification", qualification)
    capability_id = id(capability)

    def discard(
        reference: weakref.ReferenceType[VerifiedExpertQualification],
    ) -> None:
        registration = _QUALIFICATION_CAPABILITY_REGISTRATIONS.get(capability_id)
        if registration is not None and registration.capability_reference is reference:
            _QUALIFICATION_CAPABILITY_REGISTRATIONS.pop(capability_id, None)

    reference = weakref.ref(capability, discard)
    _QUALIFICATION_CAPABILITY_REGISTRATIONS[capability_id] = (
        _QualificationCapabilityRegistration(
            capability_reference=reference,
            evidence_port=evidence_port,
            qualification=qualification,
        )
    )
    return capability


def _require_qualification_capability(
    capability: VerifiedExpertQualification,
    *,
    evidence_port: ExpertQualificationEvidencePort,
) -> None:
    registration = _QUALIFICATION_CAPABILITY_REGISTRATIONS.get(id(capability))
    if (
        type(capability) is not VerifiedExpertQualification
        or registration is None
        or registration.capability_reference() is not capability
        or registration.evidence_port is not evidence_port
        or registration.qualification != capability.qualification
    ):
        raise ExpertCorpusPromotionError(
            "expert qualification capability was not issued by this verifier",
            kind=ExpertCorpusFailureKind.FALSIFIED,
            reason_code="foreign_qualification_capability",
        )


def _raise_needs_review(message: str, reason_code: str) -> NoReturn:
    raise ExpertCorpusPromotionError(
        message,
        kind=ExpertCorpusFailureKind.UNVERIFIABLE,
        reason_code=reason_code,
    )


def _raise_quarantine(message: str, reason_code: str) -> NoReturn:
    raise ExpertCorpusPromotionError(
        message,
        kind=ExpertCorpusFailureKind.INVALID,
        reason_code=reason_code,
    )

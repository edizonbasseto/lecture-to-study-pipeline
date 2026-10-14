"""Fail-closed verification boundary for external human-review authority."""

from __future__ import annotations

import hashlib
import hmac
import threading
import weakref
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from lecture_study.domain.human_review import (
    HumanReviewDecision,
    HumanReviewEvidence,
    HumanReviewScope,
)
from lecture_study.domain.models import SafeIdentifier
from lecture_study.domain.versioning import PersistedSequenceNumber
from lecture_study.quality.content_identity import (
    canonical_human_review_evidence_sha256,
    canonical_human_review_payload_base_sha256,
    canonical_human_review_scope_sha256,
)

_Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_NonNegativePersistedInteger = Annotated[
    int,
    Field(ge=0, le=(1 << 63) - 1),
]
_SAFE_IDENTIFIER_ADAPTER = TypeAdapter(SafeIdentifier)


class HumanReviewFailureKind(StrEnum):
    """Closed failure categories used by scope-local review assessment."""

    PENDING = "pending"
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    INVALID = "invalid"
    SPOOFED = "spoofed"
    FALSIFIED = "falsified"


_QUARANTINE_FAILURE_KINDS = frozenset(
    {
        HumanReviewFailureKind.REJECTED,
        HumanReviewFailureKind.INVALID,
        HumanReviewFailureKind.SPOOFED,
        HumanReviewFailureKind.FALSIFIED,
    }
)


class HumanReviewError(RuntimeError):
    """Raised with a closed reason when human-review authority is not proven."""

    def __init__(self, message: str, *, kind: HumanReviewFailureKind) -> None:
        super().__init__(message)
        self.kind = kind


class HumanReviewRequirementError(HumanReviewError):
    """Raised when caller or trusted requirement state cannot be established."""


class HumanReviewCapabilityError(HumanReviewError):
    """Raised when a local verified-review capability is not issuer-authentic."""


class HumanReviewQuarantineRegistryError(RuntimeError):
    """Raised when durable quarantine state cannot be trusted."""


class HumanReviewEvidenceQuarantined(RuntimeError):
    """Raised when exact or scope-local evidence freshness is permanently blocked."""


class HumanReviewEvidenceIdentity(BaseModel):
    """Sole canonical identity of the signed evidence payload."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    evidence_payload_sha256: _Digest


class HumanReviewQuarantineEvidence(BaseModel):
    """Canonical evidence facts used for quarantine and freshness checks."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    identity: HumanReviewEvidenceIdentity
    cryptographic_domain: Literal["lecture-study.human-review.v1"]
    signature_purpose: Literal["human-review-scope-decision"]
    trust_key_id: _Digest
    authority_id: SafeIdentifier
    lecture_id: SafeIdentifier
    canonical_scope_sha256: _Digest
    payload_base_sha256: _Digest
    review_version: PersistedSequenceNumber
    observed_signature_sha256: _Digest


class HumanReviewQuarantineRecord(BaseModel):
    """One complete append-only durable quarantine record."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    registry_sequence: PersistedSequenceNumber
    evidence_payload_sha256: _Digest
    cryptographic_domain: Literal["lecture-study.human-review.v1"]
    signature_purpose: Literal["human-review-scope-decision"]
    trust_key_id: _Digest
    authority_id: SafeIdentifier
    lecture_id: SafeIdentifier
    canonical_scope_sha256: _Digest
    payload_base_sha256: _Digest
    review_version: PersistedSequenceNumber
    reason_kind: HumanReviewFailureKind
    reason_code: SafeIdentifier
    observed_signature_sha256: _Digest
    previous_record_sha256: _Digest | None
    record_sha256: _Digest


class HumanReviewQuarantineRegistry(Protocol):
    """Inward port for trusted append-only human-review quarantine."""

    def require_not_quarantined(
        self,
        evidence: HumanReviewQuarantineEvidence,
    ) -> None:
        """Reject exact quarantine or a non-fresh payload for the same scope."""
        ...

    def append(
        self,
        evidence: HumanReviewQuarantineEvidence,
        *,
        kind: HumanReviewFailureKind,
        reason_code: str,
    ) -> HumanReviewQuarantineRecord:
        """Durably append a quarantine record or return its existing record."""
        ...


class UnavailableHumanReviewQuarantineRegistry:
    """Fail closed when durable human-review quarantine is unavailable."""

    def require_not_quarantined(
        self,
        evidence: HumanReviewQuarantineEvidence,
    ) -> None:
        del evidence
        raise HumanReviewQuarantineRegistryError(
            "durable human-review quarantine registry is not configured"
        )

    def append(
        self,
        evidence: HumanReviewQuarantineEvidence,
        *,
        kind: HumanReviewFailureKind,
        reason_code: str,
    ) -> HumanReviewQuarantineRecord:
        del evidence, kind, reason_code
        raise HumanReviewQuarantineRegistryError(
            "durable human-review quarantine registry is not configured"
        )


class HumanReviewRequirement(BaseModel):
    """Current identities that one review must cover exactly."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    scope: HumanReviewScope
    policy_sha256: _Digest
    calibration_sha256: _Digest
    cryptographic_domain: Literal["lecture-study.human-review.v1"]
    signature_purpose: Literal["human-review-scope-decision"]
    authority_key_id: _Digest
    authority_id: SafeIdentifier
    authority_sequence: PersistedSequenceNumber
    review_version: PersistedSequenceNumber
    verification_time_epoch_seconds: _NonNegativePersistedInteger


class HumanReviewCurrentState(BaseModel):
    """Trusted current identities required for one review scope."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    policy_sha256: _Digest
    calibration_sha256: _Digest
    cryptographic_domain: Literal["lecture-study.human-review.v1"]
    signature_purpose: Literal["human-review-scope-decision"]
    authority_key_id: _Digest
    authority_id: SafeIdentifier
    authority_sequence: PersistedSequenceNumber
    review_version: PersistedSequenceNumber


class HumanReviewCurrentStateProvider(Protocol):
    """Inward port for trusted scope-local human-review currentness."""

    def current_state(self, scope: HumanReviewScope) -> HumanReviewCurrentState:
        """Return the current policy, calibration, authority, and review identities."""
        ...


class UnavailableHumanReviewCurrentStateProvider:
    """Fail closed when trusted human-review current state is unavailable."""

    def current_state(self, scope: HumanReviewScope) -> HumanReviewCurrentState:
        del scope
        raise HumanReviewError(
            "trusted human-review current state is not configured",
            kind=HumanReviewFailureKind.UNAVAILABLE,
        )


class HumanReviewClock(Protocol):
    """Inward port for trusted human-review verification time."""

    def now_epoch_seconds(self) -> int:
        """Return current Unix time in whole seconds."""
        ...


class UnavailableHumanReviewClock:
    """Fail closed when no trusted human-review clock is configured."""

    def now_epoch_seconds(self) -> int:
        raise HumanReviewError(
            "trusted human-review clock is not configured",
            kind=HumanReviewFailureKind.UNAVAILABLE,
        )


class _HumanReviewClockReading(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    epoch_seconds: _NonNegativePersistedInteger


class HumanReviewAuthorityDecision(BaseModel):
    """Non-PII eligibility result returned by the external authority."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    cryptographic_domain: Literal["lecture-study.human-review.v1"]
    signature_purpose: Literal["human-review-scope-decision"]
    authority_key_id: _Digest
    authority_id: SafeIdentifier
    decision_conclusive: bool
    signature_valid: bool
    authority_current: bool
    evidence_revoked: bool
    scope_authorized: bool
    quorum_satisfied: bool


class HumanReviewAuthority(Protocol):
    """Inward port implemented by the dedicated external review authority."""

    def verify(
        self,
        evidence: HumanReviewEvidence,
        signed_payload: bytes,
    ) -> HumanReviewAuthorityDecision:
        """Authenticate evidence and return current non-PII eligibility."""
        ...


class UnavailableHumanReviewAuthority:
    """Fail closed when no external human-review authority is configured."""

    def verify(
        self,
        evidence: HumanReviewEvidence,
        signed_payload: bytes,
    ) -> HumanReviewAuthorityDecision:
        del evidence, signed_payload
        raise HumanReviewError(
            "external human-review authority is not configured",
            kind=HumanReviewFailureKind.UNAVAILABLE,
        )


@dataclass(
    frozen=True,
    slots=True,
    init=False,
    weakref_slot=True,
    repr=False,
    eq=False,
)
class VerifiedHumanReview:
    """Internally issued result of one current external-authority verification."""

    evidence: HumanReviewEvidence
    authority_decision: HumanReviewAuthorityDecision

    def __new__(cls) -> VerifiedHumanReview:
        raise TypeError("VerifiedHumanReview is issued only by HumanReviewVerifier")

    def __copy__(self) -> VerifiedHumanReview:
        raise TypeError("VerifiedHumanReview capabilities cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> VerifiedHumanReview:
        del memo
        raise TypeError("VerifiedHumanReview capabilities cannot be copied")


@dataclass(frozen=True, slots=True)
class _VerifiedHumanReviewIssuance:
    reference: weakref.ReferenceType[VerifiedHumanReview]
    verifier_reference: weakref.ReferenceType[HumanReviewVerifier]
    capability_evidence: HumanReviewEvidence
    capability_authority_decision: HumanReviewAuthorityDecision
    evidence: HumanReviewEvidence
    evidence_json: str
    detached_signature_sha256: str
    authority_decision: HumanReviewAuthorityDecision
    authority_decision_json: str
    identity: HumanReviewEvidenceIdentity


class _VerifiedHumanReviewRegistry:
    """Retain issuer provenance outside caller-controlled capability fields."""

    def __init__(self) -> None:
        self._issuances: dict[int, _VerifiedHumanReviewIssuance] = {}

    def register(
        self,
        verified: VerifiedHumanReview,
        *,
        verifier: HumanReviewVerifier,
        identity: HumanReviewEvidenceIdentity,
    ) -> None:
        capability_id = id(verified)
        reference = weakref.ref(
            verified,
            lambda expired: self._remove(capability_id, expired),
        )
        capability_evidence = verified.evidence
        capability_authority_decision = verified.authority_decision
        evidence_snapshot = HumanReviewEvidence.model_validate(
            capability_evidence.model_dump(mode="python", warnings=False)
        )
        authority_decision_snapshot = HumanReviewAuthorityDecision.model_validate(
            capability_authority_decision.model_dump(mode="python", warnings=False)
        )
        self._issuances[capability_id] = _VerifiedHumanReviewIssuance(
            reference=reference,
            verifier_reference=weakref.ref(verifier),
            capability_evidence=capability_evidence,
            capability_authority_decision=capability_authority_decision,
            evidence=evidence_snapshot,
            evidence_json=evidence_snapshot.model_dump_json(),
            detached_signature_sha256=hashlib.sha256(
                evidence_snapshot.detached_signature.encode("utf-8")
            ).hexdigest(),
            authority_decision=authority_decision_snapshot,
            authority_decision_json=authority_decision_snapshot.model_dump_json(),
            identity=identity,
        )

    def require_authentic(
        self,
        verified: VerifiedHumanReview,
        *,
        verifier: HumanReviewVerifier,
    ) -> _VerifiedHumanReviewIssuance:
        if type(verified) is not VerifiedHumanReview:
            raise HumanReviewCapabilityError(
                "verified human-review capability type is invalid",
                kind=HumanReviewFailureKind.INVALID,
            )
        issuance = self._issuances.get(id(verified))
        if (
            issuance is None
            or issuance.reference() is not verified
            or issuance.verifier_reference() is not verifier
        ):
            raise HumanReviewCapabilityError(
                "verified human-review capability was not authentically issued",
                kind=HumanReviewFailureKind.INVALID,
            )
        try:
            current_evidence = verified.evidence
            current_authority_decision = verified.authority_decision
        except AttributeError as error:
            raise HumanReviewCapabilityError(
                "verified human-review capability is structurally invalid",
                kind=HumanReviewFailureKind.INVALID,
            ) from error
        if (
            issuance.capability_evidence is not current_evidence
            or issuance.capability_authority_decision is not current_authority_decision
        ):
            raise HumanReviewCapabilityError(
                "verified human-review capability was altered after issuance",
                kind=HumanReviewFailureKind.INVALID,
            )
        try:
            signature_sha256 = hashlib.sha256(
                current_evidence.detached_signature.encode("utf-8")
            ).hexdigest()
            evidence_json = current_evidence.model_dump_json()
            authority_decision_json = current_authority_decision.model_dump_json()
        except (AttributeError, TypeError, ValueError) as error:
            raise HumanReviewCapabilityError(
                "verified human-review capability is structurally invalid",
                kind=HumanReviewFailureKind.INVALID,
            ) from error
        if (
            issuance.evidence_json != evidence_json
            or not hmac.compare_digest(
                issuance.detached_signature_sha256,
                signature_sha256,
            )
            or issuance.authority_decision_json != authority_decision_json
        ):
            raise HumanReviewCapabilityError(
                "verified human-review capability was altered after issuance",
                kind=HumanReviewFailureKind.INVALID,
            )
        return issuance

    def _remove(
        self,
        capability_id: int,
        expired: weakref.ReferenceType[VerifiedHumanReview],
    ) -> None:
        issuance = self._issuances.get(capability_id)
        if issuance is not None and issuance.reference is expired:
            del self._issuances[capability_id]


_VERIFIED_HUMAN_REVIEW_REGISTRY = _VerifiedHumanReviewRegistry()


def _issue_verified_human_review(
    evidence: HumanReviewEvidence,
    authority_decision: HumanReviewAuthorityDecision,
    *,
    verifier: HumanReviewVerifier,
    identity: HumanReviewEvidenceIdentity,
) -> VerifiedHumanReview:
    verified = object.__new__(VerifiedHumanReview)
    object.__setattr__(verified, "evidence", evidence)
    object.__setattr__(verified, "authority_decision", authority_decision)
    _VERIFIED_HUMAN_REVIEW_REGISTRY.register(
        verified,
        verifier=verifier,
        identity=identity,
    )
    return verified


class HumanReviewVerifier:
    """Require trusted currentness, exact scope, and external authority eligibility."""

    def __init__(
        self,
        authority: HumanReviewAuthority | None = None,
        current_state_provider: HumanReviewCurrentStateProvider | None = None,
        clock: HumanReviewClock | None = None,
        quarantine_registry: HumanReviewQuarantineRegistry | None = None,
    ) -> None:
        self._authority = (
            authority if authority is not None else UnavailableHumanReviewAuthority()
        )
        self._current_state_provider = (
            current_state_provider
            if current_state_provider is not None
            else UnavailableHumanReviewCurrentStateProvider()
        )
        self._clock = clock if clock is not None else UnavailableHumanReviewClock()
        self._quarantine_registry = (
            quarantine_registry
            if quarantine_registry is not None
            else UnavailableHumanReviewQuarantineRegistry()
        )
        self._unconfirmed_quarantine_identities: set[str] = set()
        self._quarantine_latch_lock = threading.RLock()

    def require_current_requirement(
        self,
        requirement: HumanReviewRequirement,
    ) -> HumanReviewRequirement:
        """Return a requirement reconstructed from trusted current state and time."""

        declared_requirement, current_requirement = self._current_requirement_snapshot(
            requirement
        )
        self._require_requirement_matches_current(
            declared_requirement,
            current_requirement,
        )
        return current_requirement

    def require_approved(
        self,
        evidence: HumanReviewEvidence,
        requirement: HumanReviewRequirement,
    ) -> VerifiedHumanReview:
        evidence = self._validate_evidence(evidence)
        identity = self.require_not_quarantined(evidence)
        declared_requirement, current_requirement = self._current_requirement_snapshot(
            requirement
        )
        self._require_exact_local_identity(evidence, current_requirement)
        self._require_current(evidence, current_requirement)
        self._require_requirement_matches_current(
            declared_requirement,
            current_requirement,
        )
        authority_decision = self._verify_external_authority(evidence)
        identity_after_authentication = self.require_not_quarantined(evidence)
        if identity_after_authentication != identity:
            raise HumanReviewError(
                "human-review quarantine identity changed during verification",
                kind=HumanReviewFailureKind.UNAVAILABLE,
            )
        self._require_eligible_authority_decision(
            authority_decision,
            current_requirement,
        )
        self._require_approved_decision(evidence)
        return _issue_verified_human_review(
            evidence,
            authority_decision,
            verifier=self,
            identity=identity,
        )

    def require_not_quarantined(
        self,
        evidence: HumanReviewEvidence,
    ) -> HumanReviewEvidenceIdentity:
        """Return the sole canonical identity when durable quarantine permits it."""

        validated_evidence = self._validate_evidence(evidence)
        quarantine_evidence = self._quarantine_evidence(validated_evidence)
        self._require_no_unconfirmed_quarantine(quarantine_evidence.identity)
        try:
            self._quarantine_registry.require_not_quarantined(quarantine_evidence)
        except HumanReviewEvidenceQuarantined as error:
            raise HumanReviewError(
                "human-review evidence is quarantined or is not fresh",
                kind=HumanReviewFailureKind.INVALID,
            ) from error
        except Exception as error:
            raise HumanReviewError(
                "human-review quarantine registry is unavailable",
                kind=HumanReviewFailureKind.UNAVAILABLE,
            ) from error
        return quarantine_evidence.identity

    def require_capability_evidence(
        self,
        verified: VerifiedHumanReview,
    ) -> HumanReviewEvidence:
        """Return only evidence bound to an issuer-authentic local capability."""

        issuance = _VERIFIED_HUMAN_REVIEW_REGISTRY.require_authentic(
            verified,
            verifier=self,
        )
        return HumanReviewEvidence.model_validate(
            issuance.evidence.model_dump(mode="python", warnings=False)
        )

    def quarantine(
        self,
        evidence: HumanReviewEvidence,
        kind: HumanReviewFailureKind,
        reason_code: str,
    ) -> HumanReviewQuarantineRecord:
        """Durably quarantine a canonically identifiable failure."""

        if (
            not isinstance(kind, HumanReviewFailureKind)
            or kind not in _QUARANTINE_FAILURE_KINDS
        ):
            raise HumanReviewError(
                "human-review failure kind is not quarantine-eligible",
                kind=HumanReviewFailureKind.INVALID,
            )
        try:
            validated_reason_code = _SAFE_IDENTIFIER_ADAPTER.validate_python(
                reason_code,
                strict=True,
            )
        except (TypeError, ValueError) as error:
            raise HumanReviewError(
                "human-review quarantine reason code is invalid",
                kind=HumanReviewFailureKind.INVALID,
            ) from error
        validated_evidence = self._validate_evidence(evidence)
        quarantine_evidence = self._quarantine_evidence(validated_evidence)
        try:
            record = HumanReviewQuarantineRecord.model_validate(
                self._quarantine_registry.append(
                    quarantine_evidence,
                    kind=kind,
                    reason_code=validated_reason_code,
                ).model_dump(mode="python"),
            )
        except Exception as error:
            self._remember_unconfirmed_quarantine(quarantine_evidence.identity)
            raise HumanReviewError(
                "human-review quarantine registry is unavailable",
                kind=HumanReviewFailureKind.UNAVAILABLE,
            ) from error
        if not self._record_matches_identity(record, quarantine_evidence):
            self._remember_unconfirmed_quarantine(quarantine_evidence.identity)
            raise HumanReviewError(
                "human-review quarantine registry returned a mismatched identity",
                kind=HumanReviewFailureKind.UNAVAILABLE,
            )
        self._clear_unconfirmed_quarantine(quarantine_evidence.identity)
        return record

    def require_verified_current(
        self,
        verified: VerifiedHumanReview,
        requirement: HumanReviewRequirement,
    ) -> VerifiedHumanReview:
        """Revalidate an authentic issued capability at its consumption boundary."""

        issued = _VERIFIED_HUMAN_REVIEW_REGISTRY.require_authentic(
            verified,
            verifier=self,
        )
        issued_identity = issued.identity
        evidence = self._validate_evidence(issued.evidence)
        current_identity = self.require_not_quarantined(evidence)
        if current_identity != issued_identity:
            raise HumanReviewError(
                "verified human-review identity no longer matches its issuance",
                kind=HumanReviewFailureKind.INVALID,
            )
        declared_requirement, current_requirement = self._current_requirement_snapshot(
            requirement
        )
        self._require_exact_local_identity(evidence, current_requirement)
        self._require_current(evidence, current_requirement)
        self._require_requirement_matches_current(
            declared_requirement,
            current_requirement,
        )
        authority_decision = self._verify_external_authority(evidence)
        identity_after_authentication = self.require_not_quarantined(evidence)
        if identity_after_authentication != issued_identity:
            raise HumanReviewError(
                "verified human-review was quarantined during revalidation",
                kind=HumanReviewFailureKind.INVALID,
            )
        self._require_eligible_authority_decision(
            authority_decision,
            current_requirement,
        )
        self._require_approved_decision(evidence)
        if authority_decision != issued.authority_decision:
            raise HumanReviewError(
                "verified human-review authority decision changed after issuance",
                kind=HumanReviewFailureKind.INVALID,
            )
        final_issuance = _VERIFIED_HUMAN_REVIEW_REGISTRY.require_authentic(
            verified,
            verifier=self,
        )
        if final_issuance is not issued or final_issuance.identity != issued_identity:
            raise HumanReviewError(
                "verified human-review issuance identity changed",
                kind=HumanReviewFailureKind.INVALID,
            )
        return verified

    @staticmethod
    def _validate_evidence(evidence: HumanReviewEvidence) -> HumanReviewEvidence:
        try:
            return HumanReviewEvidence.model_validate(
                evidence.model_dump(mode="python", warnings=False)
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise HumanReviewError(
                "human-review evidence is invalid",
                kind=HumanReviewFailureKind.INVALID,
            ) from error

    @staticmethod
    def _quarantine_evidence(
        evidence: HumanReviewEvidence,
    ) -> HumanReviewQuarantineEvidence:
        return HumanReviewQuarantineEvidence(
            identity=HumanReviewEvidenceIdentity(
                evidence_payload_sha256=canonical_human_review_evidence_sha256(evidence)
            ),
            cryptographic_domain=evidence.cryptographic_domain,
            signature_purpose=evidence.signature_purpose,
            trust_key_id=evidence.authority_key_id,
            authority_id=evidence.authority_id,
            lecture_id=evidence.scope.lecture_id,
            canonical_scope_sha256=canonical_human_review_scope_sha256(evidence.scope),
            payload_base_sha256=canonical_human_review_payload_base_sha256(evidence),
            review_version=evidence.review_version,
            observed_signature_sha256=hashlib.sha256(
                evidence.detached_signature.encode("utf-8")
            ).hexdigest(),
        )

    @staticmethod
    def _record_matches_identity(
        record: HumanReviewQuarantineRecord,
        evidence: HumanReviewQuarantineEvidence,
    ) -> bool:
        return (
            record.evidence_payload_sha256 == evidence.identity.evidence_payload_sha256
            and record.cryptographic_domain == evidence.cryptographic_domain
            and record.signature_purpose == evidence.signature_purpose
            and record.trust_key_id == evidence.trust_key_id
            and record.authority_id == evidence.authority_id
            and record.lecture_id == evidence.lecture_id
            and record.canonical_scope_sha256 == evidence.canonical_scope_sha256
            and record.payload_base_sha256 == evidence.payload_base_sha256
            and record.review_version == evidence.review_version
        )

    def _require_no_unconfirmed_quarantine(
        self,
        identity: HumanReviewEvidenceIdentity,
    ) -> None:
        with self._quarantine_latch_lock:
            blocked = (
                identity.evidence_payload_sha256
                in self._unconfirmed_quarantine_identities
            )
        if blocked:
            raise HumanReviewError(
                "human-review quarantine persistence remains unconfirmed",
                kind=HumanReviewFailureKind.INVALID,
            )

    def _remember_unconfirmed_quarantine(
        self,
        identity: HumanReviewEvidenceIdentity,
    ) -> None:
        with self._quarantine_latch_lock:
            self._unconfirmed_quarantine_identities.add(
                identity.evidence_payload_sha256
            )

    def _clear_unconfirmed_quarantine(
        self,
        identity: HumanReviewEvidenceIdentity,
    ) -> None:
        with self._quarantine_latch_lock:
            self._unconfirmed_quarantine_identities.discard(
                identity.evidence_payload_sha256
            )

    @staticmethod
    def _validate_requirement(
        requirement: HumanReviewRequirement,
    ) -> HumanReviewRequirement:
        try:
            return HumanReviewRequirement.model_validate(
                requirement.model_dump(mode="python", warnings=False)
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise HumanReviewRequirementError(
                "human-review requirement is invalid",
                kind=HumanReviewFailureKind.INVALID,
            ) from error

    def _current_requirement_snapshot(
        self,
        requirement: HumanReviewRequirement,
    ) -> tuple[HumanReviewRequirement, HumanReviewRequirement]:
        declared_requirement = self._validate_requirement(requirement)
        current_state = self._load_current_state(declared_requirement.scope)
        current_time = self._load_current_time()
        current_requirement = self._trusted_current_requirement(
            declared_requirement.scope,
            current_state,
            current_time,
        )
        return declared_requirement, current_requirement

    def _load_current_state(
        self,
        scope: HumanReviewScope,
    ) -> HumanReviewCurrentState:
        try:
            state = self._current_state_provider.current_state(scope)
        except HumanReviewError as error:
            if error.kind is HumanReviewFailureKind.INVALID:
                raise HumanReviewRequirementError(
                    str(error),
                    kind=error.kind,
                ) from error
            raise
        except Exception as error:
            raise HumanReviewError(
                "trusted human-review current state is unavailable",
                kind=HumanReviewFailureKind.UNAVAILABLE,
            ) from error
        try:
            return HumanReviewCurrentState.model_validate(
                state.model_dump(mode="python", warnings=False)
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise HumanReviewRequirementError(
                "trusted human-review current state is invalid",
                kind=HumanReviewFailureKind.INVALID,
            ) from error

    def _load_current_time(self) -> int:
        try:
            current_time = self._clock.now_epoch_seconds()
        except HumanReviewError as error:
            if error.kind is HumanReviewFailureKind.INVALID:
                raise HumanReviewRequirementError(
                    str(error),
                    kind=error.kind,
                ) from error
            raise
        except Exception as error:
            raise HumanReviewError(
                "trusted human-review clock is unavailable",
                kind=HumanReviewFailureKind.UNAVAILABLE,
            ) from error
        try:
            return _HumanReviewClockReading(epoch_seconds=current_time).epoch_seconds
        except (TypeError, ValueError) as error:
            raise HumanReviewRequirementError(
                "trusted human-review clock returned an invalid time",
                kind=HumanReviewFailureKind.INVALID,
            ) from error

    @staticmethod
    def _trusted_current_requirement(
        scope: HumanReviewScope,
        state: HumanReviewCurrentState,
        current_time: int,
    ) -> HumanReviewRequirement:
        return HumanReviewRequirement(
            scope=scope,
            policy_sha256=state.policy_sha256,
            calibration_sha256=state.calibration_sha256,
            cryptographic_domain=state.cryptographic_domain,
            signature_purpose=state.signature_purpose,
            authority_key_id=state.authority_key_id,
            authority_id=state.authority_id,
            authority_sequence=state.authority_sequence,
            review_version=state.review_version,
            verification_time_epoch_seconds=current_time,
        )

    @staticmethod
    def _require_requirement_matches_current(
        declared: HumanReviewRequirement,
        current: HumanReviewRequirement,
    ) -> None:
        if declared != current:
            raise HumanReviewRequirementError(
                "human-review requirement does not match trusted current state and time",
                kind=HumanReviewFailureKind.INVALID,
            )

    @staticmethod
    def _require_exact_local_identity(
        evidence: HumanReviewEvidence,
        requirement: HumanReviewRequirement,
    ) -> None:
        if evidence.scope != requirement.scope:
            raise HumanReviewError(
                "human-review evidence scope does not match",
                kind=HumanReviewFailureKind.INVALID,
            )
        HumanReviewVerifier._require_exact_evidence_trust(evidence, requirement)
        current_identity = (
            requirement.policy_sha256,
            requirement.calibration_sha256,
            requirement.authority_sequence,
            requirement.review_version,
        )
        evidence_identity = (
            evidence.policy_sha256,
            evidence.calibration_sha256,
            evidence.authority_sequence,
            evidence.review_version,
        )
        if evidence_identity != current_identity:
            raise HumanReviewError(
                "human-review evidence is stale",
                kind=HumanReviewFailureKind.INVALID,
            )

    @staticmethod
    def _require_exact_evidence_trust(
        evidence: HumanReviewEvidence,
        requirement: HumanReviewRequirement,
    ) -> None:
        evidence_trust = (
            evidence.cryptographic_domain,
            evidence.signature_purpose,
            evidence.authority_key_id,
            evidence.authority_id,
        )
        required_trust = (
            requirement.cryptographic_domain,
            requirement.signature_purpose,
            requirement.authority_key_id,
            requirement.authority_id,
        )
        if evidence_trust != required_trust:
            raise HumanReviewError(
                "human-review evidence authority trust identity does not match",
                kind=HumanReviewFailureKind.INVALID,
            )

    @staticmethod
    def _require_current(
        evidence: HumanReviewEvidence,
        requirement: HumanReviewRequirement,
    ) -> None:
        verification_time = requirement.verification_time_epoch_seconds
        if verification_time < evidence.issued_at_epoch_seconds:
            raise HumanReviewError(
                "human-review evidence is not yet valid",
                kind=HumanReviewFailureKind.INVALID,
            )
        if verification_time >= evidence.expires_at_epoch_seconds:
            raise HumanReviewError(
                "human-review evidence is expired",
                kind=HumanReviewFailureKind.INVALID,
            )

    @staticmethod
    def _require_approved_decision(evidence: HumanReviewEvidence) -> None:
        if evidence.decision is not HumanReviewDecision.APPROVED:
            raise HumanReviewError(
                "human-review evidence is not approved",
                kind=HumanReviewFailureKind.REJECTED,
            )

    def _verify_external_authority(
        self,
        evidence: HumanReviewEvidence,
    ) -> HumanReviewAuthorityDecision:
        authority_evidence = HumanReviewEvidence.model_validate(
            evidence.model_dump(mode="python", warnings=False)
        )
        signed_payload = evidence.signed_payload()
        try:
            decision = self._authority.verify(authority_evidence, signed_payload)
        except HumanReviewError:
            raise
        except Exception as error:
            raise HumanReviewError(
                "external human-review authority is unavailable",
                kind=HumanReviewFailureKind.UNAVAILABLE,
            ) from error
        try:
            return HumanReviewAuthorityDecision.model_validate(
                decision.model_dump(mode="python", warnings=False)
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise HumanReviewError(
                "external human-review authority returned an invalid decision",
                kind=HumanReviewFailureKind.INVALID,
            ) from error

    @staticmethod
    def _require_eligible_authority_decision(
        decision: HumanReviewAuthorityDecision,
        requirement: HumanReviewRequirement,
    ) -> None:
        decision_trust = (
            decision.cryptographic_domain,
            decision.signature_purpose,
            decision.authority_key_id,
            decision.authority_id,
        )
        required_trust = (
            requirement.cryptographic_domain,
            requirement.signature_purpose,
            requirement.authority_key_id,
            requirement.authority_id,
        )
        if decision_trust != required_trust:
            raise HumanReviewError(
                "external human-review authority trust identity does not match",
                kind=HumanReviewFailureKind.INVALID,
            )
        if not decision.signature_valid:
            raise HumanReviewError(
                "human-review detached signature is invalid",
                kind=HumanReviewFailureKind.SPOOFED,
            )
        if not decision.authority_current:
            raise HumanReviewError(
                "external human-review authority is not current",
                kind=HumanReviewFailureKind.INVALID,
            )
        if decision.evidence_revoked:
            raise HumanReviewError(
                "human-review evidence is revoked",
                kind=HumanReviewFailureKind.INVALID,
            )
        if not decision.decision_conclusive:
            raise HumanReviewError(
                "external human-review authority decision is inconclusive",
                kind=HumanReviewFailureKind.INCONCLUSIVE,
            )
        if not decision.scope_authorized:
            raise HumanReviewError(
                "human-review authority rejected the review scope",
                kind=HumanReviewFailureKind.INVALID,
            )
        if not decision.quorum_satisfied:
            raise HumanReviewError(
                "human-review authority quorum is not satisfied",
                kind=HumanReviewFailureKind.INVALID,
            )

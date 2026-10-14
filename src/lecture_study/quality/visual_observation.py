"""Dedicated authentication and exact binding for generated visual observations."""

from __future__ import annotations

import hashlib
import hmac
import json
import weakref
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, StringConstraints, TypeAdapter

from lecture_study.domain.models import SafeIdentifier
from lecture_study.domain.semantic_observation import (
    AuthenticatedVisualObservationReceipt,
    GeneratedObservationIdentity,
    SourceClause,
    SourceClauseInventory,
    VISUAL_OBSERVATION_CRYPTOGRAPHIC_DOMAIN,
    VISUAL_OBSERVATION_SIGNATURE_PURPOSE,
    VisualFullFrameSelector,
    VisualObservationSelector,
    VisualPartitionSelector,
)
from lecture_study.domain.source_universe import EvidenceModality
from lecture_study.domain.versioning import SchemaVersionOne

_MINIMUM_VISUAL_OBSERVATION_KEY_BYTES = 32
_VISUAL_OBSERVATION_KEY_ID_CONTEXT = b"lecture-study:visual-observation:key-id:v1\0"
_VISUAL_OBSERVATION_SIGNATURE_CONTEXT = (
    b"lecture-study:visual-observation:signature:v1\0"
)
_SAFE_IDENTIFIER_ADAPTER = TypeAdapter(SafeIdentifier)
_Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class VisualObservationTrustError(ValueError):
    """Raised when visual-observation authentication fails closed."""


class VisualObservationRegistryFailureKind(StrEnum):
    """Closed failure classes exposed by the persistent registry port."""

    CONFLICT = "conflict"
    MISSING = "missing"
    MISMATCH = "mismatch"
    UNAVAILABLE = "unavailable"


class VisualObservationRegistryError(VisualObservationTrustError):
    """Fail-closed persistent reservation or revalidation failure."""

    def __init__(
        self,
        message: str,
        *,
        kind: VisualObservationRegistryFailureKind,
    ) -> None:
        super().__init__(message)
        self.kind = kind


class VisualObservationReceiptFailureKind(StrEnum):
    """Local disposition of a visual-observation receipt failure."""

    MISSING = "missing"
    UNVERIFIABLE = "unverifiable"
    INVALID = "invalid"


class VisualObservationReceiptBindingError(ValueError):
    """Exact receipt-set failure with its local disposition and affected spans."""

    def __init__(
        self,
        message: str,
        *,
        kind: VisualObservationReceiptFailureKind,
        affected_source_span_ids: Sequence[str],
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.affected_source_span_ids = tuple(sorted(set(affected_source_span_ids)))


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class VisualObservationHmacKey:
    """Opaque key material accepted only by the visual-observation authority."""

    _material: bytes

    def __post_init__(self) -> None:
        if not isinstance(self._material, bytes):
            raise VisualObservationTrustError(
                "visual-observation trust material must be bytes"
            )
        if len(self._material) < _MINIMUM_VISUAL_OBSERVATION_KEY_BYTES:
            raise VisualObservationTrustError(
                "visual-observation trust material must contain at least "
                f"{_MINIMUM_VISUAL_OBSERVATION_KEY_BYTES} bytes"
            )


class VisualObservationReceiptRequest(BaseModel):
    """Unsigned exact observation identity accepted by the dedicated issuer."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    receipt_id: SafeIdentifier
    invocation_id: SafeIdentifier
    lecture_id: SafeIdentifier
    learning_unit_id: SafeIdentifier
    source_span_id: SafeIdentifier
    original_frame_sha256: _Digest
    selector: VisualObservationSelector
    generated_observation_identity: GeneratedObservationIdentity
    observed_text_sha256: _Digest


class VisualObservationReceiptReservation(BaseModel):
    """Persistent identity consumed permanently before receipt signing."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    cryptographic_domain: Literal["lecture-study.visual-observation.v1"]
    signature_purpose: Literal["source-span-visual-observation"]
    trust_key_id: _Digest
    authority_id: SafeIdentifier
    receipt_id: SafeIdentifier
    invocation_id: SafeIdentifier
    authentication_payload_sha256: _Digest


class VisualObservationInvocationExpiration(BaseModel):
    """Persistent trust-scoped expiration of one issued invocation."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    cryptographic_domain: Literal["lecture-study.visual-observation.v1"]
    signature_purpose: Literal["source-span-visual-observation"]
    trust_key_id: _Digest
    authority_id: SafeIdentifier
    invocation_id: SafeIdentifier


@runtime_checkable
class VisualObservationReceiptRegistry(Protocol):
    """Inward port for durable receipt and invocation identity consumption."""

    def reserve_once(self, reservation: VisualObservationReceiptReservation) -> None:
        """Atomically and permanently reserve both identifiers."""

    def confirm_issued(self, reservation: VisualObservationReceiptReservation) -> None:
        """Append immutable evidence that the reserved receipt was issued."""

    def expire_invocation(
        self,
        expiration: VisualObservationInvocationExpiration,
    ) -> None:
        """Append immutable evidence that an issued invocation expired."""

    def require_issued(self, reservation: VisualObservationReceiptReservation) -> None:
        """Require the exact confirmed reservation to remain authoritative."""


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class VerifiedVisualObservationReceipt:
    """Issuer-only capability for one currently authenticated receipt."""

    receipt: AuthenticatedVisualObservationReceipt

    def __new__(cls) -> VerifiedVisualObservationReceipt:
        raise TypeError(
            "VerifiedVisualObservationReceipt is issued only by "
            "VisualObservationHmacAuthority"
        )


@dataclass(frozen=True, slots=True)
class _CapabilityRegistration:
    capability_reference: weakref.ReferenceType[VerifiedVisualObservationReceipt]
    authority_reference: weakref.ReferenceType[VisualObservationHmacAuthority]
    authentication_payload_sha256: str
    detached_signature: str


_CAPABILITY_REGISTRATIONS: dict[int, _CapabilityRegistration] = {}


def _issue_verified_receipt(
    *,
    authority: VisualObservationHmacAuthority,
    receipt: AuthenticatedVisualObservationReceipt,
) -> VerifiedVisualObservationReceipt:
    capability = object.__new__(VerifiedVisualObservationReceipt)
    object.__setattr__(capability, "receipt", receipt)
    capability_id = id(capability)

    def discard_registration(
        capability_reference: weakref.ReferenceType[VerifiedVisualObservationReceipt],
    ) -> None:
        registration = _CAPABILITY_REGISTRATIONS.get(capability_id)
        if (
            registration is not None
            and registration.capability_reference is capability_reference
        ):
            _CAPABILITY_REGISTRATIONS.pop(capability_id, None)

    capability_reference = weakref.ref(capability, discard_registration)
    _CAPABILITY_REGISTRATIONS[capability_id] = _CapabilityRegistration(
        capability_reference=capability_reference,
        authority_reference=weakref.ref(authority),
        authentication_payload_sha256=hashlib.sha256(
            receipt.authentication_payload()
        ).hexdigest(),
        detached_signature=receipt.detached_signature,
    )
    return capability


def _receipt_reservation(
    receipt: AuthenticatedVisualObservationReceipt,
) -> VisualObservationReceiptReservation:
    return VisualObservationReceiptReservation(
        cryptographic_domain=receipt.cryptographic_domain,
        signature_purpose=receipt.signature_purpose,
        trust_key_id=receipt.trust_key_id,
        authority_id=receipt.authority_id,
        receipt_id=receipt.receipt_id,
        invocation_id=receipt.invocation_id,
        authentication_payload_sha256=hashlib.sha256(
            receipt.authentication_payload()
        ).hexdigest(),
    )


class VisualObservationHmacAuthority:
    """Issue and revalidate visual-observation capabilities under dedicated trust."""

    def __init__(
        self,
        *,
        key: VisualObservationHmacKey,
        authority_id: str,
        registry: VisualObservationReceiptRegistry,
    ) -> None:
        if not isinstance(key, VisualObservationHmacKey):
            raise TypeError("key must be a VisualObservationHmacKey")
        if not isinstance(registry, VisualObservationReceiptRegistry):
            raise TypeError("registry must implement VisualObservationReceiptRegistry")
        self._key = key
        self._authority_id = str(
            _SAFE_IDENTIFIER_ADAPTER.validate_python(authority_id, strict=True)
        )
        self._registry = registry

    @property
    def key_id(self) -> str:
        return hashlib.sha256(
            _VISUAL_OBSERVATION_KEY_ID_CONTEXT + self._key._material
        ).hexdigest()

    @property
    def authority_id(self) -> str:
        return self._authority_id

    def issue(
        self,
        request: VisualObservationReceiptRequest,
    ) -> VerifiedVisualObservationReceipt:
        validated_request = VisualObservationReceiptRequest.model_validate(
            request.model_dump(mode="python", warnings=False)
        )
        unsigned_receipt = AuthenticatedVisualObservationReceipt(
            **validated_request.model_dump(mode="python", warnings=False),
            cryptographic_domain="lecture-study.visual-observation.v1",
            signature_purpose="source-span-visual-observation",
            trust_key_id=self.key_id,
            authority_id=self.authority_id,
            detached_signature="0" * 64,
        )
        reservation = _receipt_reservation(unsigned_receipt)
        self._registry.reserve_once(reservation)
        receipt = unsigned_receipt.model_copy(
            update={
                "detached_signature": self._sign(
                    unsigned_receipt.authentication_payload()
                )
            }
        )
        self._require_authentic(receipt)
        self._registry.confirm_issued(reservation)
        return self.verify(receipt)

    def verify(
        self,
        receipt: AuthenticatedVisualObservationReceipt,
    ) -> VerifiedVisualObservationReceipt:
        try:
            validated_receipt = self._validated_receipt(receipt)
        except (AttributeError, TypeError, ValueError) as error:
            raise VisualObservationTrustError(
                "visual-observation receipt is structurally invalid"
            ) from error
        self._require_authentic(validated_receipt)
        self._require_registry_issued(validated_receipt)
        return _issue_verified_receipt(authority=self, receipt=validated_receipt)

    def require_current(
        self,
        capability: VerifiedVisualObservationReceipt,
    ) -> AuthenticatedVisualObservationReceipt:
        if type(capability) is not VerifiedVisualObservationReceipt:
            raise VisualObservationTrustError(
                "visual-observation capability type is invalid"
            )
        registration = _CAPABILITY_REGISTRATIONS.get(id(capability))
        if (
            registration is None
            or registration.capability_reference() is not capability
            or registration.authority_reference() is not self
        ):
            raise VisualObservationTrustError(
                "visual-observation capability was not issued by this authority"
            )
        try:
            receipt = self._validated_receipt(capability.receipt)
        except (AttributeError, TypeError, ValueError) as error:
            raise VisualObservationTrustError(
                "visual-observation capability receipt is invalid"
            ) from error
        payload_sha256 = hashlib.sha256(receipt.authentication_payload()).hexdigest()
        if not hmac.compare_digest(
            registration.authentication_payload_sha256,
            payload_sha256,
        ) or not hmac.compare_digest(
            registration.detached_signature,
            receipt.detached_signature,
        ):
            raise VisualObservationTrustError(
                "visual-observation capability receipt was substituted"
            )
        self._require_authentic(receipt)
        self._require_registry_issued(receipt)
        return receipt

    def expire_invocation(self, invocation_id: str) -> None:
        validated_id = _SAFE_IDENTIFIER_ADAPTER.validate_python(
            invocation_id,
            strict=True,
        )
        self._registry.expire_invocation(
            VisualObservationInvocationExpiration(
                cryptographic_domain="lecture-study.visual-observation.v1",
                signature_purpose="source-span-visual-observation",
                trust_key_id=self.key_id,
                authority_id=self.authority_id,
                invocation_id=validated_id,
            )
        )

    @staticmethod
    def _validated_receipt(
        receipt: AuthenticatedVisualObservationReceipt,
    ) -> AuthenticatedVisualObservationReceipt:
        return AuthenticatedVisualObservationReceipt.model_validate(
            receipt.model_dump(mode="python", warnings=False)
        )

    def _require_authentic(
        self,
        receipt: AuthenticatedVisualObservationReceipt,
    ) -> None:
        if receipt.cryptographic_domain != VISUAL_OBSERVATION_CRYPTOGRAPHIC_DOMAIN:
            raise VisualObservationTrustError(
                "visual-observation cryptographic domain is invalid"
            )
        if receipt.signature_purpose != VISUAL_OBSERVATION_SIGNATURE_PURPOSE:
            raise VisualObservationTrustError(
                "visual-observation signature purpose is invalid"
            )
        if not hmac.compare_digest(receipt.trust_key_id, self.key_id):
            raise VisualObservationTrustError(
                "visual observation was signed by a different trust key"
            )
        if str(receipt.authority_id) != self.authority_id:
            raise VisualObservationTrustError(
                "visual observation was signed by a different authority"
            )
        expected_signature = self._sign(receipt.authentication_payload())
        if not hmac.compare_digest(
            expected_signature,
            receipt.detached_signature,
        ):
            raise VisualObservationTrustError(
                "visual-observation detached signature is invalid"
            )

    def _require_registry_issued(
        self,
        receipt: AuthenticatedVisualObservationReceipt,
    ) -> None:
        self._registry.require_issued(_receipt_reservation(receipt))

    def _sign(self, payload: bytes) -> str:
        if not isinstance(payload, bytes):
            raise TypeError("visual-observation authentication payload must be bytes")
        message = (
            _VISUAL_OBSERVATION_SIGNATURE_CONTEXT
            + VISUAL_OBSERVATION_CRYPTOGRAPHIC_DOMAIN.encode("utf-8")
            + b"\0"
            + VISUAL_OBSERVATION_SIGNATURE_PURPOSE.encode("utf-8")
            + b"\0"
            + payload
        )
        return hmac.new(self._key._material, message, hashlib.sha256).hexdigest()


class VisualObservationReceiptSetValidator:
    """Require one current signed receipt for every visual clause in one inventory."""

    def __init__(self, authority: VisualObservationHmacAuthority) -> None:
        if not isinstance(authority, VisualObservationHmacAuthority):
            raise TypeError("authority must be a VisualObservationHmacAuthority")
        self._authority = authority

    def require_exact_receipts(
        self,
        *,
        inventory: SourceClauseInventory,
        capabilities: Sequence[VerifiedVisualObservationReceipt],
    ) -> tuple[AuthenticatedVisualObservationReceipt, ...]:
        try:
            validated_inventory = SourceClauseInventory.model_validate(
                inventory.model_dump(mode="python", warnings=False)
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise VisualObservationReceiptBindingError(
                "visual observation inventory is structurally invalid",
                kind=VisualObservationReceiptFailureKind.INVALID,
                affected_source_span_ids=(),
            ) from error
        visual_clauses = tuple(
            clause
            for clause in validated_inventory.clauses
            if clause.modality is EvidenceModality.VISUAL
        )
        affected_span_ids = tuple(
            str(clause.source_span_id) for clause in visual_clauses
        )
        capability_tuple = tuple(capabilities)
        if visual_clauses and not capability_tuple:
            raise VisualObservationReceiptBindingError(
                "every visual observation requires an authenticated receipt",
                kind=VisualObservationReceiptFailureKind.MISSING,
                affected_source_span_ids=affected_span_ids,
            )
        try:
            receipts = tuple(
                self._authority.require_current(capability)
                for capability in capability_tuple
            )
        except VisualObservationRegistryError as error:
            kind_by_registry_failure = {
                VisualObservationRegistryFailureKind.MISSING: (
                    VisualObservationReceiptFailureKind.INVALID
                ),
                VisualObservationRegistryFailureKind.UNAVAILABLE: (
                    VisualObservationReceiptFailureKind.UNVERIFIABLE
                ),
                VisualObservationRegistryFailureKind.CONFLICT: (
                    VisualObservationReceiptFailureKind.INVALID
                ),
                VisualObservationRegistryFailureKind.MISMATCH: (
                    VisualObservationReceiptFailureKind.INVALID
                ),
            }
            raise VisualObservationReceiptBindingError(
                "visual-observation receipt registry could not verify current state",
                kind=kind_by_registry_failure[error.kind],
                affected_source_span_ids=affected_span_ids,
            ) from error
        except (AttributeError, TypeError, ValueError) as error:
            raise VisualObservationReceiptBindingError(
                "visual-observation receipt capability is not current and authentic",
                kind=VisualObservationReceiptFailureKind.INVALID,
                affected_source_span_ids=affected_span_ids,
            ) from error
        self._require_unique_receipt_identities(receipts, affected_span_ids)
        expected = Counter(
            _clause_binding_identity(clause) for clause in visual_clauses
        )
        observed = Counter(_receipt_binding_identity(receipt) for receipt in receipts)
        if expected == observed:
            return receipts
        missing_bindings = expected - observed
        additional_bindings = observed - expected
        if missing_bindings and not additional_bindings:
            raise VisualObservationReceiptBindingError(
                "one or more visual observations have no authenticated receipt",
                kind=VisualObservationReceiptFailureKind.MISSING,
                affected_source_span_ids=affected_span_ids,
            )
        raise VisualObservationReceiptBindingError(
            "visual-observation receipts do not match the exact inventory observations",
            kind=VisualObservationReceiptFailureKind.INVALID,
            affected_source_span_ids=affected_span_ids,
        )

    @staticmethod
    def _require_unique_receipt_identities(
        receipts: Sequence[AuthenticatedVisualObservationReceipt],
        affected_span_ids: Sequence[str],
    ) -> None:
        receipt_ids = tuple(str(receipt.receipt_id) for receipt in receipts)
        invocation_ids = tuple(str(receipt.invocation_id) for receipt in receipts)
        if len(receipt_ids) != len(set(receipt_ids)):
            raise VisualObservationReceiptBindingError(
                "visual-observation receipt IDs must be unique",
                kind=VisualObservationReceiptFailureKind.INVALID,
                affected_source_span_ids=affected_span_ids,
            )
        if len(invocation_ids) != len(set(invocation_ids)):
            raise VisualObservationReceiptBindingError(
                "visual-observation invocation IDs must be unique",
                kind=VisualObservationReceiptFailureKind.INVALID,
                affected_source_span_ids=affected_span_ids,
            )


def _clause_binding_identity(clause: SourceClause) -> bytes:
    if clause.modality is not EvidenceModality.VISUAL:
        raise ValueError("visual receipt binding requires a visual clause")
    if not isinstance(
        clause.selector,
        VisualFullFrameSelector | VisualPartitionSelector,
    ):
        raise ValueError("visual receipt binding requires a visual selector")
    if clause.generated_observation_identity is None:
        raise ValueError("visual receipt binding requires a generator identity")
    return _canonical_binding_identity(
        lecture_id=str(clause.lecture_id),
        learning_unit_id=str(clause.learning_unit_id),
        source_span_id=str(clause.source_span_id),
        original_frame_sha256=clause.source_content_sha256,
        selector=clause.selector,
        generated_observation_identity=clause.generated_observation_identity,
        observed_text_sha256=clause.text_sha256,
    )


def _receipt_binding_identity(
    receipt: AuthenticatedVisualObservationReceipt,
) -> bytes:
    return _canonical_binding_identity(
        lecture_id=str(receipt.lecture_id),
        learning_unit_id=str(receipt.learning_unit_id),
        source_span_id=str(receipt.source_span_id),
        original_frame_sha256=receipt.original_frame_sha256,
        selector=receipt.selector,
        generated_observation_identity=receipt.generated_observation_identity,
        observed_text_sha256=receipt.observed_text_sha256,
    )


def _canonical_binding_identity(
    *,
    lecture_id: str,
    learning_unit_id: str,
    source_span_id: str,
    original_frame_sha256: str,
    selector: VisualFullFrameSelector | VisualPartitionSelector,
    generated_observation_identity: GeneratedObservationIdentity,
    observed_text_sha256: str,
) -> bytes:
    return json.dumps(
        {
            "generated_observation_identity": (
                generated_observation_identity.model_dump(
                    mode="json",
                    warnings=False,
                )
            ),
            "learning_unit_id": learning_unit_id,
            "lecture_id": lecture_id,
            "observed_text_sha256": observed_text_sha256,
            "original_frame_sha256": original_frame_sha256,
            "selector": selector.model_dump(mode="json", warnings=False),
            "source_span_id": source_span_id,
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

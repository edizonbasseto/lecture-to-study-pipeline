from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable

import pytest

from lecture_study.domain.semantic_observation import (
    AuthenticatedVisualObservationReceipt,
    GeneratedObservationIdentity,
    SourceClause,
    SourceClauseInventory,
    SourcePartitionKind,
    SourceVisualExtent,
    VisualFullFrameSelector,
    VisualPartitionSelector,
    canonical_visual_region_binding_sha256,
)
from lecture_study.domain.source_universe import EvidenceModality
from lecture_study.quality.trust import HmacQualityTrust
from lecture_study.quality.visual_observation import (
    VerifiedVisualObservationReceipt,
    VisualObservationHmacAuthority,
    VisualObservationHmacKey,
    VisualObservationInvocationExpiration,
    VisualObservationReceiptBindingError,
    VisualObservationReceiptFailureKind,
    VisualObservationReceiptRegistry,
    VisualObservationReceiptRequest,
    VisualObservationReceiptReservation,
    VisualObservationReceiptSetValidator,
    VisualObservationRegistryError,
    VisualObservationRegistryFailureKind,
    VisualObservationTrustError,
)
from lecture_study.source_content.manifest import SourceManifestHmacKey

_FRAME_SHA256 = "a" * 64
_OTHER_FRAME_SHA256 = "b" * 64
_SELECTED_SHA256 = "c" * 64
_OBSERVED_TEXT = "The diagram identifies the affected structure."
_OBSERVED_TEXT_SHA256 = hashlib.sha256(_OBSERVED_TEXT.encode("utf-8")).hexdigest()


class _InMemoryVisualObservationReceiptRegistry:
    def __init__(self, *, fail_confirmation: bool = False) -> None:
        self._reservations: list[VisualObservationReceiptReservation] = []
        self._issued: set[VisualObservationReceiptReservation] = set()
        self._expired: set[VisualObservationInvocationExpiration] = set()
        self._fail_confirmation = fail_confirmation
        self.available = True
        self.require_issued_call_count = 0

    def reserve_once(
        self,
        reservation: VisualObservationReceiptReservation,
    ) -> None:
        if any(
            self._same_scope(existing, reservation)
            and (
                existing.receipt_id == reservation.receipt_id
                or existing.invocation_id == reservation.invocation_id
            )
            for existing in self._reservations
        ):
            raise VisualObservationRegistryError(
                "visual-observation identity is already reserved",
                kind=VisualObservationRegistryFailureKind.CONFLICT,
            )
        self._reservations.append(reservation)

    def confirm_issued(
        self,
        reservation: VisualObservationReceiptReservation,
    ) -> None:
        if self._fail_confirmation:
            self._fail_confirmation = False
            raise VisualObservationRegistryError(
                "visual-observation registry confirmation failed",
                kind=VisualObservationRegistryFailureKind.UNAVAILABLE,
            )
        if reservation not in self._reservations:
            raise VisualObservationRegistryError(
                "visual-observation reservation is missing",
                kind=VisualObservationRegistryFailureKind.MISSING,
            )
        self._issued.add(reservation)

    def expire_invocation(
        self,
        expiration: VisualObservationInvocationExpiration,
    ) -> None:
        matches = tuple(
            reservation
            for reservation in self._issued
            if self._matches_expiration(reservation, expiration)
        )
        if len(matches) != 1:
            raise VisualObservationRegistryError(
                "visual-observation expiration has no issued invocation",
                kind=VisualObservationRegistryFailureKind.MISSING,
            )
        if expiration in self._expired:
            raise VisualObservationRegistryError(
                "visual-observation invocation has already expired",
                kind=VisualObservationRegistryFailureKind.CONFLICT,
            )
        self._expired.add(expiration)

    def require_issued(
        self,
        reservation: VisualObservationReceiptReservation,
    ) -> None:
        self.require_issued_call_count += 1
        if not self.available:
            raise VisualObservationRegistryError(
                "visual-observation registry is unavailable",
                kind=VisualObservationRegistryFailureKind.UNAVAILABLE,
            )
        if any(
            self._matches_expiration(reservation, expiration)
            for expiration in self._expired
        ):
            raise VisualObservationRegistryError(
                "visual-observation invocation has expired",
                kind=VisualObservationRegistryFailureKind.MISSING,
            )
        if reservation not in self._issued:
            raise VisualObservationRegistryError(
                "visual-observation issuance is missing",
                kind=VisualObservationRegistryFailureKind.MISSING,
            )

    def discard_issuance(
        self,
        reservation: VisualObservationReceiptReservation,
    ) -> None:
        self._issued.discard(reservation)

    @staticmethod
    def _same_scope(
        first: VisualObservationReceiptReservation,
        second: VisualObservationReceiptReservation,
    ) -> bool:
        return (
            first.cryptographic_domain == second.cryptographic_domain
            and first.signature_purpose == second.signature_purpose
            and first.trust_key_id == second.trust_key_id
            and first.authority_id == second.authority_id
        )

    @staticmethod
    def _matches_expiration(
        reservation: VisualObservationReceiptReservation,
        expiration: VisualObservationInvocationExpiration,
    ) -> bool:
        return (
            reservation.cryptographic_domain == expiration.cryptographic_domain
            and reservation.signature_purpose == expiration.signature_purpose
            and reservation.trust_key_id == expiration.trust_key_id
            and reservation.authority_id == expiration.authority_id
            and reservation.invocation_id == expiration.invocation_id
        )


def _generator(suffix: str = "d") -> GeneratedObservationIdentity:
    return GeneratedObservationIdentity(
        implementation_sha256=suffix * 64,
        configuration_sha256="e" * 64,
    )


def _full_frame_selector(
    frame_sha256: str = _FRAME_SHA256,
) -> VisualFullFrameSelector:
    return VisualFullFrameSelector(
        kind=SourcePartitionKind.VISUAL_FULL_FRAME,
        original_frame_sha256=frame_sha256,
    )


def _region_selector() -> VisualPartitionSelector:
    hashing_identity = _generator("f")
    coordinates = (0.1, 0.2, 0.8, 0.9)
    return VisualPartitionSelector(
        kind=SourcePartitionKind.VISUAL_REGION,
        original_frame_sha256=_FRAME_SHA256,
        left=coordinates[0],
        top=coordinates[1],
        right=coordinates[2],
        bottom=coordinates[3],
        selected_sha256=_SELECTED_SHA256,
        hashing_identity=hashing_identity,
        region_binding_sha256=canonical_visual_region_binding_sha256(
            original_frame_sha256=_FRAME_SHA256,
            coordinates=coordinates,
            region_content_sha256=_SELECTED_SHA256,
            hashing_identity=hashing_identity,
        ),
    )


def _clause(
    *,
    clause_id: str = "visual-clause-001",
    sequence: int = 1,
    lecture_id: str = "lecture-001",
    learning_unit_id: str = "unit-001",
    source_span_id: str = "span-001",
    selector: VisualFullFrameSelector | VisualPartitionSelector | None = None,
    generator: GeneratedObservationIdentity | None = None,
    observed_text: str = _OBSERVED_TEXT,
) -> SourceClause:
    return SourceClause(
        schema_version=1,
        clause_id=clause_id,
        lecture_id=lecture_id,
        learning_unit_id=learning_unit_id,
        sequence=sequence,
        source_span_id=source_span_id,
        source_content_sha256=_FRAME_SHA256,
        modality=EvidenceModality.VISUAL,
        selector=selector or _full_frame_selector(),
        observed_text=observed_text,
        text_sha256=hashlib.sha256(observed_text.encode("utf-8")).hexdigest(),
        generated_observation_identity=generator or _generator(),
    )


def _inventory(
    clauses: tuple[SourceClause, ...] | None = None,
    *,
    lecture_id: str = "lecture-001",
    learning_unit_id: str = "unit-001",
    source_span_id: str = "span-001",
) -> SourceClauseInventory:
    selected_clauses = clauses or (_clause(),)
    return SourceClauseInventory(
        schema_version=1,
        lecture_id=lecture_id,
        learning_unit_id=learning_unit_id,
        source_universe_sha256="9" * 64,
        visual_extents=(
            SourceVisualExtent(
                source_span_id=source_span_id,
                source_content_sha256=_FRAME_SHA256,
            ),
        ),
        clauses=selected_clauses,
    )


def _request_for_clause(
    clause: SourceClause,
    *,
    receipt_id: str = "receipt-001",
    invocation_id: str = "invocation-001",
) -> VisualObservationReceiptRequest:
    assert isinstance(
        clause.selector,
        VisualFullFrameSelector | VisualPartitionSelector,
    )
    assert clause.generated_observation_identity is not None
    return VisualObservationReceiptRequest(
        schema_version=1,
        receipt_id=receipt_id,
        invocation_id=invocation_id,
        lecture_id=clause.lecture_id,
        learning_unit_id=clause.learning_unit_id,
        source_span_id=clause.source_span_id,
        original_frame_sha256=clause.source_content_sha256,
        selector=clause.selector,
        generated_observation_identity=clause.generated_observation_identity,
        observed_text_sha256=clause.text_sha256,
    )


def _authority(
    *,
    material_offset: int = 0,
    authority_id: str = "visual-observation-authority",
    registry: VisualObservationReceiptRegistry | None = None,
) -> VisualObservationHmacAuthority:
    material = bytes((value + material_offset) % 256 for value in range(32))
    selected_registry = (
        registry
        if registry is not None
        else _InMemoryVisualObservationReceiptRegistry()
    )
    return VisualObservationHmacAuthority(
        key=VisualObservationHmacKey(material),
        authority_id=authority_id,
        registry=selected_registry,
    )


def _issue(
    authority: VisualObservationHmacAuthority,
    clause: SourceClause,
    *,
    receipt_id: str = "receipt-001",
    invocation_id: str = "invocation-001",
) -> VerifiedVisualObservationReceipt:
    return authority.issue(
        _request_for_clause(
            clause,
            receipt_id=receipt_id,
            invocation_id=invocation_id,
        )
    )


def _reservation_for_receipt(
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


def test_exact_visual_observation_receipt_is_current_and_bound() -> None:
    authority = _authority()
    inventory = _inventory()
    capability = _issue(authority, inventory.clauses[0])

    receipt = authority.require_current(capability)
    validated = VisualObservationReceiptSetValidator(authority).require_exact_receipts(
        inventory=inventory, capabilities=(capability,)
    )

    assert validated == (receipt,)
    assert receipt.cryptographic_domain == "lecture-study.visual-observation.v1"
    assert receipt.signature_purpose == "source-span-visual-observation"
    assert receipt.trust_key_id == authority.key_id
    assert str(receipt.authority_id) == authority.authority_id


def test_full_frame_and_region_each_require_an_individual_receipt() -> None:
    authority = _authority()
    full_frame_clause = _clause()
    region_clause = _clause(
        clause_id="visual-clause-region",
        sequence=2,
        selector=_region_selector(),
    )
    inventory = _inventory((full_frame_clause, region_clause))
    capabilities = (
        _issue(authority, full_frame_clause),
        _issue(
            authority,
            region_clause,
            receipt_id="receipt-region",
            invocation_id="invocation-region",
        ),
    )

    receipts = VisualObservationReceiptSetValidator(authority).require_exact_receipts(
        inventory=inventory,
        capabilities=capabilities,
    )

    assert len(receipts) == 2
    assert len({str(receipt.receipt_id) for receipt in receipts}) == 2


def test_byte_identical_frames_in_distinct_spans_require_distinct_receipts() -> None:
    authority = _authority()
    first_clause = _clause()
    second_clause = _clause(
        clause_id="visual-clause-002",
        sequence=2,
        source_span_id="span-002",
    )
    inventory = SourceClauseInventory(
        schema_version=1,
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        source_universe_sha256="9" * 64,
        visual_extents=(
            SourceVisualExtent(
                source_span_id="span-001",
                source_content_sha256=_FRAME_SHA256,
            ),
            SourceVisualExtent(
                source_span_id="span-002",
                source_content_sha256=_FRAME_SHA256,
            ),
        ),
        clauses=(first_clause, second_clause),
    )
    capabilities = (
        _issue(authority, first_clause),
        _issue(
            authority,
            second_clause,
            receipt_id="receipt-002",
            invocation_id="invocation-002",
        ),
    )

    receipts = VisualObservationReceiptSetValidator(authority).require_exact_receipts(
        inventory=inventory,
        capabilities=capabilities,
    )

    assert {str(receipt.source_span_id) for receipt in receipts} == {
        "span-001",
        "span-002",
    }


def test_visual_region_must_remain_inside_the_authenticated_frame() -> None:
    selector = _region_selector().model_dump(mode="python")
    selector["right"] = 1.1

    with pytest.raises(ValueError):
        VisualObservationReceiptRequest.model_validate(
            {
                **_request_for_clause(_clause()).model_dump(mode="python"),
                "selector": selector,
            }
        )


@pytest.mark.parametrize(
    "mutator",
    (
        lambda receipt: {"schema_version": 2},
        lambda receipt: {"receipt_id": "receipt-substituted"},
        lambda receipt: {"invocation_id": "invocation-substituted"},
        lambda receipt: {"lecture_id": "lecture-substituted"},
        lambda receipt: {"learning_unit_id": "unit-substituted"},
        lambda receipt: {"source_span_id": "span-substituted"},
        lambda receipt: {
            "original_frame_sha256": _OTHER_FRAME_SHA256,
            "selector": _full_frame_selector(_OTHER_FRAME_SHA256),
        },
        lambda receipt: {"selector": _region_selector()},
        lambda receipt: {"generated_observation_identity": _generator("1")},
        lambda receipt: {"observed_text_sha256": "2" * 64},
        lambda receipt: {"cryptographic_domain": "other-domain"},
        lambda receipt: {"signature_purpose": "other-purpose"},
        lambda receipt: {"trust_key_id": "3" * 64},
        lambda receipt: {"authority_id": "other-authority"},
        lambda receipt: {"detached_signature": "4" * 64},
    ),
)
def test_mutating_any_signed_receipt_field_invalidates_authentication(
    mutator: Callable[[AuthenticatedVisualObservationReceipt], dict[str, object]],
) -> None:
    authority = _authority()
    capability = _issue(authority, _clause())
    mutated = capability.receipt.model_copy(update=mutator(capability.receipt))

    with pytest.raises(VisualObservationTrustError):
        authority.verify(mutated)


@pytest.mark.parametrize(
    ("changed_clause", "label"),
    (
        (_clause(source_span_id="span-002"), "byte-identical frame in another span"),
        (_clause(lecture_id="lecture-002"), "another lecture"),
        (_clause(learning_unit_id="unit-002"), "another learning unit"),
        (_clause(generator=_generator("1")), "another generator"),
        (_clause(observed_text="A substituted visual description."), "another text"),
    ),
)
def test_authentic_receipt_cannot_replay_against_another_observation(
    changed_clause: SourceClause,
    label: str,
) -> None:
    del label
    authority = _authority()
    capability = _issue(authority, _clause())
    inventory = _inventory(
        (changed_clause,),
        lecture_id=str(changed_clause.lecture_id),
        learning_unit_id=str(changed_clause.learning_unit_id),
        source_span_id=str(changed_clause.source_span_id),
    )

    with pytest.raises(
        VisualObservationReceiptBindingError,
        match="do not match",
    ) as raised:
        VisualObservationReceiptSetValidator(authority).require_exact_receipts(
            inventory=inventory,
            capabilities=(capability,),
        )

    assert raised.value.kind is VisualObservationReceiptFailureKind.INVALID


def test_region_receipt_cannot_replay_as_a_full_frame_receipt() -> None:
    authority = _authority()
    region_capability = _issue(
        authority,
        _clause(selector=_region_selector()),
    )

    with pytest.raises(VisualObservationReceiptBindingError) as raised:
        VisualObservationReceiptSetValidator(authority).require_exact_receipts(
            inventory=_inventory(),
            capabilities=(region_capability,),
        )

    assert raised.value.kind is VisualObservationReceiptFailureKind.INVALID


def test_missing_receipt_is_reviewable_and_regions_do_not_replace_full_frame() -> None:
    authority = _authority()
    full_frame_clause = _clause()
    region_clause = _clause(
        clause_id="visual-clause-region",
        sequence=2,
        selector=_region_selector(),
    )
    inventory = _inventory((full_frame_clause, region_clause))
    region_capability = _issue(
        authority,
        region_clause,
        receipt_id="receipt-region",
        invocation_id="invocation-region",
    )

    with pytest.raises(VisualObservationReceiptBindingError) as raised:
        VisualObservationReceiptSetValidator(authority).require_exact_receipts(
            inventory=inventory,
            capabilities=(region_capability,),
        )

    assert raised.value.kind is VisualObservationReceiptFailureKind.MISSING


def test_only_regions_remain_structurally_invalid_even_when_signed() -> None:
    authority = _authority()
    region_clause = _clause(selector=_region_selector())
    invalid_inventory = SourceClauseInventory.model_construct(
        schema_version=1,
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        source_universe_sha256="9" * 64,
        text_extents=(),
        visual_extents=(
            SourceVisualExtent(
                source_span_id="span-001",
                source_content_sha256=_FRAME_SHA256,
            ),
        ),
        clauses=(region_clause,),
    )
    capability = _issue(authority, region_clause)

    with pytest.raises(VisualObservationReceiptBindingError) as raised:
        VisualObservationReceiptSetValidator(authority).require_exact_receipts(
            inventory=invalid_inventory,
            capabilities=(capability,),
        )

    assert raised.value.kind is VisualObservationReceiptFailureKind.INVALID


@pytest.mark.parametrize("duplicate_field", ("receipt_id", "invocation_id"))
def test_duplicate_receipt_or_invocation_identity_is_rejected(
    duplicate_field: str,
) -> None:
    registry = _InMemoryVisualObservationReceiptRegistry()
    authority = _authority(registry=registry)
    clause = _clause()
    _issue(authority, clause)
    reconstructed_authority = _authority(registry=registry)

    with pytest.raises(VisualObservationRegistryError, match="already reserved"):
        _issue(
            reconstructed_authority,
            clause,
            receipt_id=(
                "receipt-001" if duplicate_field == "receipt_id" else "receipt-002"
            ),
            invocation_id=(
                "invocation-001"
                if duplicate_field == "invocation_id"
                else "invocation-002"
            ),
        )


def test_issuer_rejects_duplicate_receipt_and_invocation_ids() -> None:
    authority = _authority()
    clause = _clause()
    _issue(authority, clause)

    with pytest.raises(VisualObservationTrustError, match="already reserved"):
        _issue(
            authority,
            clause,
            receipt_id="receipt-001",
            invocation_id="invocation-002",
        )
    with pytest.raises(VisualObservationTrustError, match="already reserved"):
        _issue(
            authority,
            clause,
            receipt_id="receipt-002",
            invocation_id="invocation-001",
        )


def test_issue_verify_and_consume_revalidate_the_persistent_registry() -> None:
    registry = _InMemoryVisualObservationReceiptRegistry()
    authority = _authority(registry=registry)

    capability = _issue(authority, _clause())
    assert registry.require_issued_call_count == 1

    authority.verify(capability.receipt)
    assert registry.require_issued_call_count == 2

    authority.require_current(capability)
    assert registry.require_issued_call_count == 3


def test_registry_loss_invalidates_raw_receipt_and_existing_capability() -> None:
    registry = _InMemoryVisualObservationReceiptRegistry()
    authority = _authority(registry=registry)
    capability = _issue(authority, _clause())
    reservation = _reservation_for_receipt(capability.receipt)
    registry.discard_issuance(reservation)

    with pytest.raises(VisualObservationRegistryError, match="issuance is missing"):
        authority.verify(capability.receipt)
    with pytest.raises(VisualObservationRegistryError, match="issuance is missing"):
        authority.require_current(capability)
    with pytest.raises(VisualObservationReceiptBindingError) as caught:
        VisualObservationReceiptSetValidator(authority).require_exact_receipts(
            inventory=_inventory(),
            capabilities=(capability,),
        )

    assert caught.value.kind is VisualObservationReceiptFailureKind.INVALID


def test_registry_unavailability_is_unverifiable_not_invalid() -> None:
    registry = _InMemoryVisualObservationReceiptRegistry()
    authority = _authority(registry=registry)
    clause = _clause()
    capability = _issue(authority, clause)
    registry.available = False

    with pytest.raises(VisualObservationReceiptBindingError) as caught:
        VisualObservationReceiptSetValidator(authority).require_exact_receipts(
            inventory=_inventory((clause,)),
            capabilities=(capability,),
        )

    assert caught.value.kind is VisualObservationReceiptFailureKind.UNVERIFIABLE


def test_authentic_receipt_is_rejected_by_an_unrelated_registry() -> None:
    issuing_authority = _authority()
    capability = _issue(issuing_authority, _clause())
    reconstructed_authority = _authority(
        registry=_InMemoryVisualObservationReceiptRegistry()
    )

    with pytest.raises(VisualObservationRegistryError, match="issuance is missing"):
        reconstructed_authority.verify(capability.receipt)


def test_failed_confirmation_burns_both_identifiers() -> None:
    registry = _InMemoryVisualObservationReceiptRegistry(fail_confirmation=True)
    authority = _authority(registry=registry)

    with pytest.raises(VisualObservationRegistryError, match="confirmation failed"):
        _issue(authority, _clause())

    reconstructed_authority = _authority(registry=registry)
    with pytest.raises(VisualObservationRegistryError, match="already reserved"):
        _issue(reconstructed_authority, _clause())


def test_registry_hash_covers_every_receipt_field_except_signature() -> None:
    authority = _authority()
    capability = _issue(authority, _clause())
    receipt = capability.receipt
    reservation = _reservation_for_receipt(receipt)
    signature_mutation = receipt.model_copy(update={"detached_signature": "7" * 64})

    assert (
        reservation.authentication_payload_sha256
        == hashlib.sha256(receipt.authentication_payload()).hexdigest()
    )
    assert (
        signature_mutation.authentication_payload() == receipt.authentication_payload()
    )


def test_public_receipt_and_manually_allocated_capability_have_no_authority() -> None:
    authority = _authority()
    legitimate = _issue(authority, _clause())
    forged_receipt = legitimate.receipt.model_copy(
        update={"detached_signature": "0" * 64}
    )
    forged_capability = object.__new__(VerifiedVisualObservationReceipt)
    object.__setattr__(forged_capability, "receipt", legitimate.receipt)

    with pytest.raises(VisualObservationTrustError):
        authority.verify(forged_receipt)
    with pytest.raises(VisualObservationTrustError, match="not issued"):
        authority.require_current(forged_capability)
    with pytest.raises((TypeError, VisualObservationTrustError)):
        authority.require_current(copy.copy(legitimate))


def test_capability_receipt_substitution_and_expiration_fail_revalidation() -> None:
    authority = _authority()
    capability = _issue(authority, _clause())
    object.__setattr__(
        capability,
        "receipt",
        capability.receipt.model_copy(update={"source_span_id": "span-substituted"}),
    )

    with pytest.raises(VisualObservationTrustError, match="substituted"):
        authority.require_current(capability)

    current = _issue(
        authority,
        _clause(),
        receipt_id="receipt-current",
        invocation_id="invocation-current",
    )
    authority.expire_invocation("invocation-current")
    with pytest.raises(VisualObservationTrustError, match="expired"):
        authority.require_current(current)


def test_key_domain_purpose_and_authority_confusion_are_rejected() -> None:
    expected_authority = _authority()
    capability = _issue(expected_authority, _clause())

    with pytest.raises(VisualObservationTrustError):
        _authority(material_offset=1).verify(capability.receipt)
    with pytest.raises(VisualObservationTrustError):
        _authority(authority_id="other-visual-authority").verify(capability.receipt)
    with pytest.raises(TypeError):
        VisualObservationHmacAuthority(
            key=SourceManifestHmacKey(bytes(range(32))),  # type: ignore[arg-type]
            authority_id="visual-observation-authority",
            registry=_InMemoryVisualObservationReceiptRegistry(),
        )
    with pytest.raises(TypeError):
        VisualObservationHmacAuthority(
            key=HmacQualityTrust(bytes(range(32))),  # type: ignore[arg-type]
            authority_id="visual-observation-authority",
            registry=_InMemoryVisualObservationReceiptRegistry(),
        )


def test_visual_observation_cannot_be_laundered_as_verbatim() -> None:
    with pytest.raises(ValueError, match="visual.*verbatim"):
        SourceClause(
            **{
                **_clause().model_dump(mode="python"),
                "verbatim_text": _OBSERVED_TEXT,
                "observed_text": None,
            }
        )

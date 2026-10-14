from __future__ import annotations

import hashlib
from dataclasses import replace
from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast

import pytest

from lecture_study.domain.human_review import (
    HumanReviewDecision,
    HumanReviewEvidence,
    HumanReviewPairScope,
    HumanReviewScope,
    NumericReviewSubject,
)
from lecture_study.quality.human_review import (
    HumanReviewAuthority,
    HumanReviewAuthorityDecision,
    HumanReviewClock,
    HumanReviewCapabilityError,
    HumanReviewCurrentState,
    HumanReviewCurrentStateProvider,
    HumanReviewError,
    HumanReviewFailureKind,
    HumanReviewQuarantineEvidence,
    HumanReviewQuarantineRecord,
    HumanReviewQuarantineRegistry,
    HumanReviewRequirement,
    HumanReviewVerifier,
    VerifiedHumanReview,
)
from lecture_study.quality.human_review_quarantine import (
    SqliteHumanReviewQuarantineRegistry,
    SqliteHumanReviewQuarantineRegistryOpenMode,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _pair(suffix: str = "001") -> HumanReviewPairScope:
    return HumanReviewPairScope(
        disagreement_id=f"disagreement-{suffix}",
        proposition_id=f"proposition-{suffix}",
    )


def _numeric_subject(
    suffix: str = "001",
    **changes: object,
) -> NumericReviewSubject:
    values: dict[str, object] = {
        "proposition_id": f"proposition-{suffix}",
        "quantity_id": f"quantity-{suffix}",
        "source_clause_id": f"clause-{suffix}",
        "component": "unit",
        "source_binding_sha256": _digest(f"source-binding-{suffix}"),
        "reason_code": "implicit-component",
    }
    values.update(changes)
    return NumericReviewSubject.model_validate(values)


def _scope(**changes: object) -> HumanReviewScope:
    values: dict[str, object] = {
        "lecture_id": "lecture-001",
        "clause_id": "clause-001",
        "review_pairs": (_pair(),),
        "source_content_aggregation_sha256": _digest("source"),
        "clause_inventory_sha256": _digest("clause-inventory"),
        "candidate_batches_sha256": _digest("candidate-batches"),
        "atomic_ledger_sha256": _digest("ledger"),
        "reconciliations_sha256": _digest("reconciliations"),
    }
    values.update(changes)
    return HumanReviewScope.model_validate(values)


def _evidence(
    *,
    scope: HumanReviewScope | None = None,
    decision: HumanReviewDecision = HumanReviewDecision.APPROVED,
    policy_sha256: str | None = None,
    calibration_sha256: str | None = None,
    cryptographic_domain: Literal[
        "lecture-study.human-review.v1"
    ] = "lecture-study.human-review.v1",
    signature_purpose: Literal[
        "human-review-scope-decision"
    ] = "human-review-scope-decision",
    authority_key_id: str | None = None,
    authority_id: str = "external-review-authority",
    authority_sequence: int = 41,
    review_version: int = 3,
    issued_at_epoch_seconds: int = 1_000,
    expires_at_epoch_seconds: int = 2_000,
    detached_signature: str = "valid-test-signature",
) -> HumanReviewEvidence:
    return HumanReviewEvidence(
        schema_version=1,
        scope=scope or _scope(),
        decision=decision,
        policy_sha256=policy_sha256 or _digest("policy"),
        calibration_sha256=calibration_sha256 or _digest("calibration"),
        cryptographic_domain=cryptographic_domain,
        signature_purpose=signature_purpose,
        authority_key_id=authority_key_id or _digest("human-review-key"),
        authority_id=authority_id,
        authority_sequence=authority_sequence,
        review_version=review_version,
        issued_at_epoch_seconds=issued_at_epoch_seconds,
        expires_at_epoch_seconds=expires_at_epoch_seconds,
        detached_signature=detached_signature,
    )


def _requirement(
    *,
    scope: HumanReviewScope | None = None,
    policy_sha256: str | None = None,
    calibration_sha256: str | None = None,
    cryptographic_domain: Literal[
        "lecture-study.human-review.v1"
    ] = "lecture-study.human-review.v1",
    signature_purpose: Literal[
        "human-review-scope-decision"
    ] = "human-review-scope-decision",
    authority_key_id: str | None = None,
    authority_id: str = "external-review-authority",
    authority_sequence: int = 41,
    review_version: int = 3,
    verification_time_epoch_seconds: int = 1_500,
) -> HumanReviewRequirement:
    return HumanReviewRequirement(
        scope=scope or _scope(),
        policy_sha256=policy_sha256 or _digest("policy"),
        calibration_sha256=calibration_sha256 or _digest("calibration"),
        cryptographic_domain=cryptographic_domain,
        signature_purpose=signature_purpose,
        authority_key_id=authority_key_id or _digest("human-review-key"),
        authority_id=authority_id,
        authority_sequence=authority_sequence,
        review_version=review_version,
        verification_time_epoch_seconds=verification_time_epoch_seconds,
    )


class _Authority:
    def __init__(
        self,
        *,
        cryptographic_domain: Literal[
            "lecture-study.human-review.v1"
        ] = "lecture-study.human-review.v1",
        signature_purpose: Literal[
            "human-review-scope-decision"
        ] = "human-review-scope-decision",
        authority_key_id: str | None = None,
        authority_id: str = "external-review-authority",
        decision_conclusive: bool = True,
        authority_current: bool = True,
        evidence_revoked: bool = False,
        scope_authorized: bool = True,
        quorum_satisfied: bool = True,
    ) -> None:
        self._decision = HumanReviewAuthorityDecision(
            cryptographic_domain=cryptographic_domain,
            signature_purpose=signature_purpose,
            authority_key_id=authority_key_id or _digest("human-review-key"),
            authority_id=authority_id,
            decision_conclusive=decision_conclusive,
            signature_valid=True,
            authority_current=authority_current,
            evidence_revoked=evidence_revoked,
            scope_authorized=scope_authorized,
            quorum_satisfied=quorum_satisfied,
        )
        self.signed_payloads: list[bytes] = []

    def verify(
        self,
        evidence: HumanReviewEvidence,
        signed_payload: bytes,
    ) -> HumanReviewAuthorityDecision:
        self.signed_payloads.append(signed_payload)
        if evidence.detached_signature != "valid-test-signature":
            return self._decision.model_copy(update={"signature_valid": False})
        return self._decision


class _MutatingEvidenceAuthority(_Authority):
    def verify(
        self,
        evidence: HumanReviewEvidence,
        signed_payload: bytes,
    ) -> HumanReviewAuthorityDecision:
        decision = super().verify(evidence, signed_payload)
        object.__setattr__(evidence, "detached_signature", "mutated-signature")
        return decision


class _UnavailableAuthority:
    def verify(
        self,
        evidence: HumanReviewEvidence,
        signed_payload: bytes,
    ) -> HumanReviewAuthorityDecision:
        del evidence, signed_payload
        raise OSError("review authority offline")


class _CurrentStateProvider:
    def __init__(
        self,
        state: HumanReviewCurrentState | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.state = state if state is not None else _current_state()
        self.error = error
        self.scopes: list[HumanReviewScope] = []

    def current_state(self, scope: HumanReviewScope) -> HumanReviewCurrentState:
        self.scopes.append(scope)
        if self.error is not None:
            raise self.error
        return self.state


class _Clock:
    def __init__(
        self,
        epoch_seconds: int = 1_500,
        *,
        error: Exception | None = None,
    ) -> None:
        self.epoch_seconds = epoch_seconds
        self.error = error
        self.readings = 0

    def now_epoch_seconds(self) -> int:
        self.readings += 1
        if self.error is not None:
            raise self.error
        return self.epoch_seconds


class _AllowingQuarantineRegistry:
    def __init__(self) -> None:
        self.lookups = 0

    def require_not_quarantined(
        self,
        evidence: HumanReviewQuarantineEvidence,
    ) -> None:
        del evidence
        self.lookups += 1

    def append(
        self,
        evidence: HumanReviewQuarantineEvidence,
        *,
        kind: HumanReviewFailureKind,
        reason_code: str,
    ) -> HumanReviewQuarantineRecord:
        del evidence, kind, reason_code
        raise AssertionError("the allowing registry does not append records")


def _current_state(
    requirement: HumanReviewRequirement | None = None,
) -> HumanReviewCurrentState:
    current = requirement if requirement is not None else _requirement()
    return HumanReviewCurrentState(
        policy_sha256=current.policy_sha256,
        calibration_sha256=current.calibration_sha256,
        cryptographic_domain=current.cryptographic_domain,
        signature_purpose=current.signature_purpose,
        authority_key_id=current.authority_key_id,
        authority_id=current.authority_id,
        authority_sequence=current.authority_sequence,
        review_version=current.review_version,
    )


def _verifier(
    authority: HumanReviewAuthority | None = None,
    *,
    current_state_provider: HumanReviewCurrentStateProvider | None = None,
    clock: HumanReviewClock | None = None,
    quarantine_registry: HumanReviewQuarantineRegistry | None = None,
) -> HumanReviewVerifier:
    trusted_state = (
        current_state_provider
        if current_state_provider is not None
        else _CurrentStateProvider()
    )
    trusted_clock = clock if clock is not None else _Clock()
    trusted_quarantine = (
        quarantine_registry
        if quarantine_registry is not None
        else _AllowingQuarantineRegistry()
    )
    return HumanReviewVerifier(
        authority=authority,
        current_state_provider=trusted_state,
        clock=trusted_clock,
        quarantine_registry=trusted_quarantine,
    )


def test_review_evidence_signature_payload_binds_every_authorization_field() -> None:
    evidence = _evidence()
    payload = evidence.signed_payload()

    assert b"detached_signature" not in payload
    for expected_value in (
        evidence.scope.lecture_id,
        evidence.scope.clause_id,
        evidence.scope.review_pairs[0].proposition_id,
        evidence.scope.review_pairs[0].disagreement_id,
        evidence.scope.source_content_aggregation_sha256,
        evidence.scope.clause_inventory_sha256,
        evidence.scope.candidate_batches_sha256,
        evidence.scope.atomic_ledger_sha256,
        evidence.scope.reconciliations_sha256,
        evidence.decision.value,
        evidence.policy_sha256,
        evidence.calibration_sha256,
        evidence.cryptographic_domain,
        evidence.signature_purpose,
        evidence.authority_key_id,
        evidence.authority_id,
        str(evidence.authority_sequence),
        str(evidence.review_version),
    ):
        assert expected_value is not None
        assert str(expected_value).encode("utf-8") in payload


def test_valid_external_review_is_verified_without_reviewer_pii() -> None:
    authority = _Authority()
    evidence = _evidence()

    verified = _verifier(authority).require_approved(
        evidence,
        _requirement(),
    )

    assert verified.evidence == evidence
    assert verified.authority_decision.quorum_satisfied is True
    assert authority.signed_payloads == [evidence.signed_payload()]
    assert (
        not {
            "reviewer_name",
            "reviewer_email",
            "reviewer_license",
            "reviewer_ids",
        }
        & HumanReviewEvidence.model_fields.keys()
    )


def test_external_authority_cannot_mutate_issued_evidence() -> None:
    authority = _MutatingEvidenceAuthority()
    verifier = _verifier(authority)
    requirement = _requirement()
    verified = verifier.require_approved(_evidence(), requirement)

    current = verifier.require_verified_current(verified, requirement)

    assert current is verified
    assert verified.evidence.detached_signature == "valid-test-signature"


def test_verified_review_cannot_be_forged_through_the_public_constructor() -> None:
    evidence = _evidence()
    authority_decision = _Authority().verify(evidence, evidence.signed_payload())
    verified = _verifier(_Authority()).require_approved(
        evidence,
        _requirement(),
    )
    constructor = cast(Callable[..., VerifiedHumanReview], VerifiedHumanReview)

    with pytest.raises(TypeError):
        constructor(
            evidence=evidence,
            authority_decision=authority_decision,
        )
    with pytest.raises(TypeError, match="issued only"):
        VerifiedHumanReview()
    with pytest.raises(TypeError):
        replace(
            verified,
            authority_decision=authority_decision.model_copy(
                update={"evidence_revoked": True}
            ),
        )


def test_structurally_altered_verified_review_raises_capability_error() -> None:
    verifier = _verifier(_Authority())
    requirement = _requirement()
    verified = verifier.require_approved(_evidence(), requirement)
    object.__setattr__(verified, "evidence", object())

    with pytest.raises(
        HumanReviewCapabilityError,
        match="altered after issuance",
    ):
        verifier.require_verified_current(verified, requirement)


@pytest.mark.parametrize("field_name", ("evidence", "authority_decision"))
def test_missing_verified_review_field_raises_capability_error(
    field_name: str,
) -> None:
    verifier = _verifier(_Authority())
    requirement = _requirement()
    verified = verifier.require_approved(_evidence(), requirement)
    object.__delattr__(verified, field_name)

    with pytest.raises(
        HumanReviewCapabilityError,
        match="structurally invalid",
    ):
        verifier.require_verified_current(verified, requirement)


def test_unavailable_authority_fails_closed() -> None:
    with pytest.raises(HumanReviewError, match="not configured") as captured:
        _verifier().require_approved(_evidence(), _requirement())

    assert captured.value.kind is HumanReviewFailureKind.UNAVAILABLE


def test_inconclusive_authority_decision_fails_closed() -> None:
    with pytest.raises(HumanReviewError, match="inconclusive") as captured:
        _verifier(_Authority(decision_conclusive=False)).require_approved(
            _evidence(), _requirement()
        )

    assert captured.value.kind is HumanReviewFailureKind.INCONCLUSIVE


def test_external_authority_failure_is_classified_as_unavailable() -> None:
    with pytest.raises(HumanReviewError, match="unavailable") as captured:
        _verifier(_UnavailableAuthority()).require_approved(
            _evidence(),
            _requirement(),
        )

    assert captured.value.kind is HumanReviewFailureKind.UNAVAILABLE


def test_spoofed_detached_signature_is_rejected() -> None:
    evidence = _evidence(detached_signature="spoofed-signature")

    with pytest.raises(HumanReviewError, match="signature") as captured:
        _verifier(_Authority()).require_approved(evidence, _requirement())

    assert captured.value.kind is HumanReviewFailureKind.SPOOFED


def test_spoofed_rejection_is_not_classified_as_authenticated_rejection() -> None:
    evidence = _evidence(
        decision=HumanReviewDecision.REJECTED,
        detached_signature="spoofed-signature",
    )

    with pytest.raises(HumanReviewError, match="signature") as captured:
        _verifier(_Authority()).require_approved(evidence, _requirement())

    assert captured.value.kind is HumanReviewFailureKind.SPOOFED


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("lecture_id", "lecture-002"),
        ("clause_id", "clause-002"),
        ("review_pairs", (_pair("002"),)),
        ("source_content_aggregation_sha256", _digest("other-source")),
        ("clause_inventory_sha256", _digest("other-clause-inventory")),
        ("candidate_batches_sha256", _digest("other-candidate-batches")),
        ("atomic_ledger_sha256", _digest("other-ledger")),
        ("reconciliations_sha256", _digest("other-reconciliations")),
    ],
)
def test_review_replay_against_another_scope_is_rejected(
    field_name: str,
    replacement: object,
) -> None:
    replay_requirement = _requirement(
        scope=_scope(**{field_name: replacement}),
    )

    with pytest.raises(HumanReviewError, match="scope"):
        _verifier(_Authority()).require_approved(
            _evidence(),
            replay_requirement,
        )


def test_aggregate_review_signature_binds_every_exact_pair() -> None:
    aggregate_scope = _scope(review_pairs=(_pair("001"), _pair("002")))
    evidence = _evidence(scope=aggregate_scope)

    payload = evidence.signed_payload()

    for pair in aggregate_scope.review_pairs:
        assert str(pair.disagreement_id).encode("utf-8") in payload
        assert str(pair.proposition_id).encode("utf-8") in payload
    assert (
        payload
        != _evidence(scope=_scope(review_pairs=(_pair("001"),))).signed_payload()
    )
    assert (
        payload
        != _evidence(
            scope=_scope(review_pairs=(_pair("001"), _pair("002"), _pair("003")))
        ).signed_payload()
    )


@pytest.mark.parametrize(
    "required_pairs",
    [
        (_pair("001"),),
        (_pair("001"), _pair("002"), _pair("003")),
    ],
)
def test_aggregate_review_pair_omission_or_extra_pair_is_rejected(
    required_pairs: tuple[HumanReviewPairScope, ...],
) -> None:
    evidence = _evidence(
        scope=_scope(review_pairs=(_pair("001"), _pair("002"))),
    )

    with pytest.raises(HumanReviewError, match="scope"):
        _verifier(_Authority()).require_approved(
            evidence,
            _requirement(scope=_scope(review_pairs=required_pairs)),
        )


def test_review_scope_rejects_duplicate_or_noncanonical_pairs() -> None:
    with pytest.raises(ValueError, match="unique"):
        _scope(review_pairs=(_pair("001"), _pair("001")))

    with pytest.raises(ValueError, match="canonical"):
        _scope(review_pairs=(_pair("002"), _pair("001")))


@pytest.mark.parametrize(
    ("first_field", "second_field"),
    [
        ("source_content_aggregation_sha256", "clause_inventory_sha256"),
        ("clause_inventory_sha256", "candidate_batches_sha256"),
        ("candidate_batches_sha256", "atomic_ledger_sha256"),
        ("atomic_ledger_sha256", "reconciliations_sha256"),
    ],
)
def test_review_scope_rejects_cross_domain_hash_substitution(
    first_field: str,
    second_field: str,
) -> None:
    expected_scope = _scope()
    substituted_values = expected_scope.model_dump(mode="python")
    substituted_values[first_field], substituted_values[second_field] = (
        substituted_values[second_field],
        substituted_values[first_field],
    )
    substituted_scope = HumanReviewScope.model_validate(substituted_values)

    with pytest.raises(HumanReviewError, match="scope"):
        _verifier(_Authority()).require_approved(
            _evidence(scope=substituted_scope),
            _requirement(scope=expected_scope),
        )


def test_authority_rejecting_exact_scope_fails_closed() -> None:
    with pytest.raises(HumanReviewError, match="scope"):
        _verifier(_Authority(scope_authorized=False)).require_approved(
            _evidence(), _requirement()
        )


def test_wrong_authority_is_rejected_before_external_verification() -> None:
    with pytest.raises(HumanReviewError, match="authority"):
        _verifier(_Authority()).require_approved(
            _evidence(authority_id="other-authority"),
            _requirement(),
        )


def test_wrong_authority_decision_is_rejected() -> None:
    with pytest.raises(HumanReviewError, match="authority"):
        _verifier(_Authority(authority_id="other-authority")).require_approved(
            _evidence(), _requirement()
        )


@pytest.mark.parametrize(
    (
        "authority_current",
        "evidence_revoked",
        "quorum_satisfied",
        "message",
    ),
    [
        (False, False, True, "current"),
        (True, True, True, "revoked"),
        (True, False, False, "quorum"),
    ],
)
def test_external_authority_status_must_remain_eligible(
    authority_current: bool,
    evidence_revoked: bool,
    quorum_satisfied: bool,
    message: str,
) -> None:
    authority = _Authority(
        authority_current=authority_current,
        evidence_revoked=evidence_revoked,
        quorum_satisfied=quorum_satisfied,
    )

    with pytest.raises(HumanReviewError, match=message):
        _verifier(authority).require_approved(
            _evidence(),
            _requirement(),
        )


def test_expired_review_is_rejected_without_calling_authority() -> None:
    authority = _Authority()

    with pytest.raises(HumanReviewError, match="expired"):
        _verifier(authority).require_approved(
            _evidence(expires_at_epoch_seconds=1_500),
            _requirement(verification_time_epoch_seconds=1_500),
        )

    assert authority.signed_payloads == []


def test_not_yet_valid_review_is_rejected() -> None:
    with pytest.raises(HumanReviewError, match="not yet valid"):
        _verifier(_Authority()).require_approved(
            _evidence(issued_at_epoch_seconds=1_501),
            _requirement(verification_time_epoch_seconds=1_500),
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("policy_sha256", _digest("stale-policy")),
        ("calibration_sha256", _digest("stale-calibration")),
        ("authority_sequence", 40),
        ("review_version", 2),
    ],
)
def test_stale_review_identity_is_rejected(field_name: str, value: str | int) -> None:
    evidence = _evidence().model_copy(update={field_name: value})

    with pytest.raises(HumanReviewError, match="stale"):
        _verifier(_Authority()).require_approved(
            evidence,
            _requirement(),
        )


def test_authenticated_rejection_cannot_authorize_content() -> None:
    with pytest.raises(HumanReviewError, match="not approved") as captured:
        _verifier(_Authority()).require_approved(
            _evidence(decision=HumanReviewDecision.REJECTED),
            _requirement(),
        )

    assert captured.value.kind is HumanReviewFailureKind.REJECTED


def test_exact_numeric_review_subject_is_approved() -> None:
    subject = _numeric_subject()
    scope = _scope(review_pairs=(), numeric_review_subjects=(subject,))

    verified = _verifier(_Authority()).require_approved(
        _evidence(scope=scope),
        _requirement(scope=scope),
    )

    assert verified.evidence.scope.numeric_review_subjects == (subject,)
    assert verified.evidence.scope.review_pairs == ()


def test_numeric_review_signature_payload_binds_every_subject_field() -> None:
    subject = _numeric_subject()
    evidence = _evidence(
        scope=_scope(review_pairs=(), numeric_review_subjects=(subject,))
    )

    payload = evidence.signed_payload()

    for expected_value in (
        subject.proposition_id,
        subject.quantity_id,
        subject.source_clause_id,
        subject.component,
        subject.source_binding_sha256,
        subject.reason_code,
    ):
        assert str(expected_value).encode("utf-8") in payload


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("proposition_id", "proposition-002"),
        ("quantity_id", "quantity-002"),
        ("source_clause_id", "clause-002"),
        ("component", "duration"),
        ("source_binding_sha256", _digest("changed-source-binding")),
        ("reason_code", "different-reason"),
    ],
)
def test_numeric_review_subject_substitution_is_rejected(
    field_name: str,
    replacement: str,
) -> None:
    signed_subject = _numeric_subject()
    current_subject = signed_subject.model_copy(update={field_name: replacement})
    signed_scope = _scope(
        review_pairs=(),
        numeric_review_subjects=(signed_subject,),
    )
    current_scope = _scope(
        review_pairs=(),
        numeric_review_subjects=(current_subject,),
    )

    with pytest.raises(HumanReviewError, match="scope") as captured:
        _verifier(_Authority()).require_approved(
            _evidence(scope=signed_scope),
            _requirement(scope=current_scope),
        )

    assert captured.value.kind is HumanReviewFailureKind.INVALID


@pytest.mark.parametrize(
    "required_subjects",
    [
        (_numeric_subject("001"),),
        (
            _numeric_subject("001"),
            _numeric_subject("002"),
            _numeric_subject("003"),
        ),
    ],
)
def test_numeric_aggregate_omission_or_extra_subject_is_rejected(
    required_subjects: tuple[NumericReviewSubject, ...],
) -> None:
    signed_subjects = (_numeric_subject("001"), _numeric_subject("002"))
    signed_scope = _scope(
        review_pairs=(),
        numeric_review_subjects=signed_subjects,
    )
    required_scope = _scope(
        review_pairs=(),
        numeric_review_subjects=required_subjects,
    )

    assert (
        _evidence(scope=signed_scope).signed_payload()
        != _evidence(scope=required_scope).signed_payload()
    )
    with pytest.raises(HumanReviewError, match="scope"):
        _verifier(_Authority()).require_approved(
            _evidence(scope=signed_scope),
            _requirement(scope=required_scope),
        )


def test_numeric_subject_cannot_authorize_multiple_components() -> None:
    signed_subject = _numeric_subject(component="unit")
    additional_subject = _numeric_subject(
        "002",
        proposition_id=signed_subject.proposition_id,
        quantity_id=signed_subject.quantity_id,
        source_clause_id=signed_subject.source_clause_id,
        component="denominator",
        source_binding_sha256=signed_subject.source_binding_sha256,
    )
    signed_scope = _scope(
        review_pairs=(),
        numeric_review_subjects=(signed_subject,),
    )
    required_scope = _scope(
        review_pairs=(),
        numeric_review_subjects=(additional_subject, signed_subject),
    )

    with pytest.raises(HumanReviewError, match="scope"):
        _verifier(_Authority()).require_approved(
            _evidence(scope=signed_scope),
            _requirement(scope=required_scope),
        )


def test_review_pair_cannot_replace_numeric_review_subject() -> None:
    numeric_scope = _scope(
        review_pairs=(),
        numeric_review_subjects=(_numeric_subject(),),
    )
    disagreement_scope = _scope(
        review_pairs=(_pair(),),
        numeric_review_subjects=(),
    )

    with pytest.raises(HumanReviewError, match="scope"):
        _verifier(_Authority()).require_approved(
            _evidence(scope=disagreement_scope),
            _requirement(scope=numeric_scope),
        )


def test_mixed_aggregate_requires_every_pair_and_numeric_subject() -> None:
    pair = _pair()
    subject = _numeric_subject()
    signed_scope = _scope(
        review_pairs=(pair,),
        numeric_review_subjects=(subject,),
    )
    incomplete_scope = _scope(
        review_pairs=(pair,),
        numeric_review_subjects=(),
    )

    assert (
        str(pair.disagreement_id).encode()
        in _evidence(scope=signed_scope).signed_payload()
    )
    assert (
        str(subject.quantity_id).encode()
        in _evidence(scope=signed_scope).signed_payload()
    )
    with pytest.raises(HumanReviewError, match="scope"):
        _verifier(_Authority()).require_approved(
            _evidence(scope=incomplete_scope),
            _requirement(scope=signed_scope),
        )


def test_numeric_review_replay_against_another_lecture_is_rejected() -> None:
    subject = _numeric_subject()
    signed_scope = _scope(
        review_pairs=(),
        numeric_review_subjects=(subject,),
    )
    replay_scope = _scope(
        lecture_id="lecture-002",
        review_pairs=(),
        numeric_review_subjects=(subject,),
    )

    with pytest.raises(HumanReviewError, match="scope"):
        _verifier(_Authority()).require_approved(
            _evidence(scope=signed_scope),
            _requirement(scope=replay_scope),
        )


def test_numeric_review_scope_rejects_duplicate_or_noncanonical_subjects() -> None:
    first = _numeric_subject("001")
    second = _numeric_subject("002")

    with pytest.raises(ValueError, match="unique"):
        _scope(
            review_pairs=(),
            numeric_review_subjects=(first, first),
        )

    with pytest.raises(ValueError, match="canonical"):
        _scope(
            review_pairs=(),
            numeric_review_subjects=(second, first),
        )


def test_numeric_review_scope_rejects_empty_subject_union() -> None:
    with pytest.raises(ValueError, match="at least one"):
        _scope(review_pairs=(), numeric_review_subjects=())


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("cryptographic_domain", "lecture-study.source-manifest.v1"),
        ("signature_purpose", "source-identity-location-integrity"),
    ],
)
def test_cross_domain_or_purpose_evidence_is_invalid(
    field_name: str,
    replacement: str,
) -> None:
    valid_evidence = _evidence()
    values = valid_evidence.model_dump(mode="python")
    values[field_name] = replacement
    confused_evidence = HumanReviewEvidence.model_construct(**values)

    with pytest.raises(HumanReviewError, match="evidence is invalid") as captured:
        _verifier(_Authority()).require_approved(
            confused_evidence,
            _requirement(),
        )

    assert captured.value.kind is HumanReviewFailureKind.INVALID


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("cryptographic_domain", "lecture-study.source-manifest.v1"),
        ("signature_purpose", "source-identity-location-integrity"),
    ],
)
def test_cross_domain_or_purpose_requirement_is_invalid(
    field_name: str,
    replacement: str,
) -> None:
    valid_requirement = _requirement()
    values = valid_requirement.model_dump(mode="python")
    values[field_name] = replacement
    confused_requirement = HumanReviewRequirement.model_construct(**values)

    with pytest.raises(HumanReviewError, match="requirement is invalid") as captured:
        _verifier(_Authority()).require_approved(
            _evidence(),
            confused_requirement,
        )

    assert captured.value.kind is HumanReviewFailureKind.INVALID


def test_wrong_human_review_key_is_rejected_before_external_verification() -> None:
    authority = _Authority()

    with pytest.raises(HumanReviewError, match="trust identity") as captured:
        _verifier(authority).require_approved(
            _evidence(authority_key_id=_digest("other-human-review-key")),
            _requirement(),
        )

    assert captured.value.kind is HumanReviewFailureKind.INVALID
    assert authority.signed_payloads == []


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("cryptographic_domain", "lecture-study.source-manifest.v1"),
        ("signature_purpose", "source-identity-location-integrity"),
    ],
)
def test_cross_domain_or_purpose_authority_decision_is_invalid(
    field_name: str,
    replacement: str,
) -> None:
    authority = _Authority()
    decision_values = authority._decision.model_dump(mode="python")
    decision_values[field_name] = replacement
    authority._decision = HumanReviewAuthorityDecision.model_construct(
        **decision_values
    )

    with pytest.raises(HumanReviewError, match="invalid decision") as captured:
        _verifier(authority).require_approved(
            _evidence(),
            _requirement(),
        )

    assert captured.value.kind is HumanReviewFailureKind.INVALID


def test_external_authority_key_must_match_exact_review_trust() -> None:
    authority = _Authority(authority_key_id=_digest("other-human-review-key"))

    with pytest.raises(HumanReviewError, match="trust identity") as captured:
        _verifier(authority).require_approved(
            _evidence(),
            _requirement(),
        )

    assert captured.value.kind is HumanReviewFailureKind.INVALID


def test_external_authority_id_must_match_exact_review_trust() -> None:
    authority = _Authority(authority_id="other-review-authority")

    with pytest.raises(HumanReviewError, match="trust identity") as captured:
        _verifier(authority).require_approved(
            _evidence(),
            _requirement(),
        )

    assert captured.value.kind is HumanReviewFailureKind.INVALID


@pytest.mark.parametrize(
    ("decision", "detached_signature", "expected_kind"),
    [
        (
            HumanReviewDecision.APPROVED,
            "spoofed-signature",
            HumanReviewFailureKind.SPOOFED,
        ),
        (
            HumanReviewDecision.REJECTED,
            "valid-test-signature",
            HumanReviewFailureKind.REJECTED,
        ),
    ],
)
def test_numeric_review_spoofing_or_rejection_never_authorizes(
    decision: HumanReviewDecision,
    detached_signature: str,
    expected_kind: HumanReviewFailureKind,
) -> None:
    scope = _scope(
        review_pairs=(),
        numeric_review_subjects=(_numeric_subject(),),
    )

    with pytest.raises(HumanReviewError) as captured:
        _verifier(_Authority()).require_approved(
            _evidence(
                scope=scope,
                decision=decision,
                detached_signature=detached_signature,
            ),
            _requirement(scope=scope),
        )

    assert captured.value.kind is expected_kind


@pytest.mark.parametrize(
    ("field_name", "stale_value"),
    [
        ("policy_sha256", _digest("stale-policy")),
        ("calibration_sha256", _digest("stale-calibration")),
        ("authority_sequence", 40),
        ("review_version", 2),
        ("authority_key_id", _digest("stale-authority-key")),
        ("authority_id", "stale-authority"),
        ("cryptographic_domain", "lecture-study.source-manifest.v1"),
        ("signature_purpose", "source-identity-location-integrity"),
    ],
)
def test_caller_selected_current_state_identity_cannot_approve(
    field_name: str,
    stale_value: object,
) -> None:
    authority = _Authority()
    stale_requirement = _requirement().model_copy(update={field_name: stale_value})

    with pytest.raises(HumanReviewError) as captured:
        _verifier(authority).require_approved(_evidence(), stale_requirement)

    assert captured.value.kind is HumanReviewFailureKind.INVALID
    assert authority.signed_payloads == []


def test_backdated_caller_time_cannot_approve_expired_evidence() -> None:
    authority = _Authority()
    clock = _Clock(1_500)
    backdated_requirement = _requirement(verification_time_epoch_seconds=1_499)

    with pytest.raises(HumanReviewError, match="evidence is expired") as captured:
        _verifier(authority, clock=clock).require_approved(
            _evidence(expires_at_epoch_seconds=1_500),
            backdated_requirement,
        )

    assert captured.value.kind is HumanReviewFailureKind.INVALID
    assert clock.readings == 1
    assert authority.signed_payloads == []


def test_current_requirement_reads_exact_scope_and_trusted_time() -> None:
    requirement = _requirement()
    current_state_provider = _CurrentStateProvider()
    clock = _Clock()

    current = _verifier(
        current_state_provider=current_state_provider,
        clock=clock,
    ).require_current_requirement(requirement)

    assert current == requirement
    assert current_state_provider.scopes == [requirement.scope]
    assert clock.readings == 1


def test_quarantine_is_checked_before_and_after_authority_authentication() -> None:
    registry = _AllowingQuarantineRegistry()
    verifier = _verifier(
        _Authority(),
        quarantine_registry=registry,
    )

    verified = verifier.require_approved(_evidence(), _requirement())

    assert registry.lookups == 2
    assert verifier.require_verified_current(verified, _requirement()) is verified
    assert registry.lookups == 4
    with pytest.raises(HumanReviewError, match="not authentically issued"):
        _verifier(
            _Authority(),
            quarantine_registry=_AllowingQuarantineRegistry(),
        ).require_verified_current(verified, _requirement())


def test_verifier_preserves_original_quarantine_reason_for_exact_identity(
    tmp_path: Path,
) -> None:
    registry = SqliteHumanReviewQuarantineRegistry(
        registry_root=tmp_path / "human-review-quarantine",
        mode=SqliteHumanReviewQuarantineRegistryOpenMode.INITIALIZE,
        busy_timeout_milliseconds=5_000,
        lock_timeout_milliseconds=5_000,
    )
    verifier = _verifier(_Authority(), quarantine_registry=registry)
    evidence = _evidence()
    original = verifier.quarantine(
        evidence,
        HumanReviewFailureKind.INVALID,
        "original-reason",
    )
    resigned = evidence.model_copy(update={"detached_signature": "signature-v2"})

    repeated = verifier.quarantine(
        resigned,
        HumanReviewFailureKind.SPOOFED,
        "replacement-reason",
    )

    assert repeated == original
    assert repeated.reason_kind is HumanReviewFailureKind.INVALID
    assert repeated.reason_code == "original-reason"
    with pytest.raises(HumanReviewError) as captured:
        verifier.require_not_quarantined(resigned)
    assert captured.value.kind is HumanReviewFailureKind.INVALID


def test_verifier_rejects_quarantine_record_metadata_substitution(
    tmp_path: Path,
) -> None:
    persistent = SqliteHumanReviewQuarantineRegistry(
        registry_root=tmp_path / "human-review-quarantine",
        mode=SqliteHumanReviewQuarantineRegistryOpenMode.INITIALIZE,
        busy_timeout_milliseconds=5_000,
        lock_timeout_milliseconds=5_000,
    )

    class _SubstitutingRegistry:
        def require_not_quarantined(
            self,
            evidence: HumanReviewQuarantineEvidence,
        ) -> None:
            persistent.require_not_quarantined(evidence)

        def append(
            self,
            evidence: HumanReviewQuarantineEvidence,
            *,
            kind: HumanReviewFailureKind,
            reason_code: str,
        ) -> HumanReviewQuarantineRecord:
            record = persistent.append(
                evidence,
                kind=kind,
                reason_code=reason_code,
            )
            return record.model_copy(update={"authority_id": "substituted-authority"})

    verifier = _verifier(
        _Authority(),
        quarantine_registry=_SubstitutingRegistry(),
    )

    with pytest.raises(HumanReviewError, match="mismatched identity") as captured:
        verifier.quarantine(
            _evidence(),
            HumanReviewFailureKind.INVALID,
            "invalid-evidence",
        )

    assert captured.value.kind is HumanReviewFailureKind.UNAVAILABLE


def test_failed_quarantine_append_latches_until_durable_retry(
    tmp_path: Path,
) -> None:
    persistent = SqliteHumanReviewQuarantineRegistry(
        registry_root=tmp_path / "human-review-quarantine",
        mode=SqliteHumanReviewQuarantineRegistryOpenMode.INITIALIZE,
        busy_timeout_milliseconds=5_000,
        lock_timeout_milliseconds=5_000,
    )

    class _FailOnceRegistry:
        def __init__(self) -> None:
            self.fail_append = True

        def require_not_quarantined(
            self,
            evidence: HumanReviewQuarantineEvidence,
        ) -> None:
            persistent.require_not_quarantined(evidence)

        def append(
            self,
            evidence: HumanReviewQuarantineEvidence,
            *,
            kind: HumanReviewFailureKind,
            reason_code: str,
        ) -> HumanReviewQuarantineRecord:
            if self.fail_append:
                raise OSError("injected append failure")
            return persistent.append(
                evidence,
                kind=kind,
                reason_code=reason_code,
            )

    registry = _FailOnceRegistry()
    verifier = _verifier(_Authority(), quarantine_registry=registry)
    evidence = _evidence()

    with pytest.raises(HumanReviewError) as failed_append:
        verifier.quarantine(
            evidence,
            HumanReviewFailureKind.INVALID,
            "invalid-evidence",
        )
    assert failed_append.value.kind is HumanReviewFailureKind.UNAVAILABLE

    with pytest.raises(HumanReviewError) as latched:
        verifier.require_approved(evidence, _requirement())
    assert latched.value.kind is HumanReviewFailureKind.INVALID

    registry.fail_append = False
    verifier.quarantine(
        evidence,
        HumanReviewFailureKind.INVALID,
        "invalid-evidence",
    )
    with pytest.raises(HumanReviewError) as durable:
        verifier.require_approved(evidence, _requirement())
    assert durable.value.kind is HumanReviewFailureKind.INVALID

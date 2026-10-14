"""Immutable evidence contracts for externally authenticated human review."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    field_validator,
    model_validator,
)

from lecture_study.domain.models import SafeIdentifier
from lecture_study.domain.versioning import (
    PersistedSequenceNumber,
    SchemaVersionOne,
)

_Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_DetachedSignature = Annotated[str, StringConstraints(min_length=1, max_length=4_096)]


class HumanReviewDecision(StrEnum):
    """Closed decision vocabulary for an externally authenticated review."""

    APPROVED = "approved"
    REJECTED = "rejected"


class HumanReviewPairScope(BaseModel):
    """One exact disagreement-to-proposition decision scope."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    disagreement_id: SafeIdentifier
    proposition_id: SafeIdentifier


class NumericReviewSubject(BaseModel):
    """One exact numeric-binding component covered by a review decision."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    proposition_id: SafeIdentifier
    quantity_id: SafeIdentifier
    source_clause_id: SafeIdentifier
    component: SafeIdentifier
    source_binding_sha256: _Digest
    reason_code: SafeIdentifier


class HumanReviewScope(BaseModel):
    """Exact content identities and review subjects covered by one decision."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    lecture_id: SafeIdentifier
    clause_id: SafeIdentifier
    review_pairs: tuple[HumanReviewPairScope, ...] = ()
    numeric_review_subjects: tuple[NumericReviewSubject, ...] = ()
    source_content_aggregation_sha256: _Digest
    clause_inventory_sha256: _Digest
    candidate_batches_sha256: _Digest
    atomic_ledger_sha256: _Digest
    reconciliations_sha256: _Digest

    @model_validator(mode="after")
    def _require_exact_canonical_subjects(self) -> HumanReviewScope:
        if not self.review_pairs and not self.numeric_review_subjects:
            raise ValueError(
                "human-review scope must contain at least one review subject"
            )
        self._require_canonical_pairs()
        self._require_canonical_numeric_subjects()
        return self

    def _require_canonical_pairs(self) -> None:
        identities = tuple(
            (str(pair.disagreement_id), str(pair.proposition_id))
            for pair in self.review_pairs
        )
        self._require_unique_canonical_order(identities, "human-review pairs")

    def _require_canonical_numeric_subjects(self) -> None:
        identities = tuple(
            (
                str(subject.proposition_id),
                str(subject.quantity_id),
                str(subject.source_clause_id),
                str(subject.component),
                subject.source_binding_sha256,
                str(subject.reason_code),
            )
            for subject in self.numeric_review_subjects
        )
        self._require_unique_canonical_order(
            identities,
            "numeric-review subjects",
        )

    @staticmethod
    def _require_unique_canonical_order(
        identities: tuple[tuple[str, ...], ...],
        label: str,
    ) -> None:
        if len(identities) != len(set(identities)):
            raise ValueError(f"{label} must be unique")
        if identities != tuple(sorted(identities)):
            raise ValueError(f"{label} must use canonical order")


class HumanReviewEvidence(BaseModel):
    """Detached external-authority signature over one exact review decision."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    scope: HumanReviewScope
    decision: HumanReviewDecision
    policy_sha256: _Digest
    calibration_sha256: _Digest
    cryptographic_domain: Literal["lecture-study.human-review.v1"]
    signature_purpose: Literal["human-review-scope-decision"]
    authority_id: SafeIdentifier
    authority_key_id: _Digest
    authority_sequence: PersistedSequenceNumber
    review_version: PersistedSequenceNumber
    issued_at_epoch_seconds: PersistedSequenceNumber
    expires_at_epoch_seconds: PersistedSequenceNumber
    detached_signature: _DetachedSignature

    @field_validator("detached_signature")
    @classmethod
    def _require_non_blank_signature(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("detached_signature must not be blank")
        return value

    @model_validator(mode="after")
    def _require_finite_validity_window(self) -> HumanReviewEvidence:
        if self.expires_at_epoch_seconds <= self.issued_at_epoch_seconds:
            raise ValueError("review evidence must expire after it is issued")
        return self

    def signed_payload(self) -> bytes:
        """Return canonical bytes covered by the external detached signature."""

        payload = self.model_dump(
            mode="json",
            exclude={"detached_signature"},
        )
        return json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

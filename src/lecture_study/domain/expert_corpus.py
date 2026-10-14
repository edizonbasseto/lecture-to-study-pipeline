"""Immutable contracts for promoted veterinary expert reference corpora."""

from __future__ import annotations

import json
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from lecture_study.domain.calibration import ReferenceCorpusManifest
from lecture_study.domain.models import SafeIdentifier
from lecture_study.domain.versioning import (
    PersistedSequenceNumber,
    SchemaVersionOne,
)

VETERINARY_EXPERT_CORPUS_CRYPTOGRAPHIC_DOMAIN = (
    "lecture-study.veterinary-expert-corpus.v1"
)
VETERINARY_EXPERT_CORPUS_SIGNATURE_PURPOSE = "expert-corpus-promotion"

_Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_DetachedSignature = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_NonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class ExpertQualificationReference(BaseModel):
    """Pseudonymous, non-PII evidence of one scope-limited qualification."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    pseudonymous_expert_id: SafeIdentifier
    qualification_reference_id: SafeIdentifier
    qualification_authority_id: SafeIdentifier
    jurisdiction_id: SafeIdentifier
    professional_category: Literal[
        "licensed_veterinarian",
        "formally_recognized_veterinary_specialist",
    ]
    specialty_id: SafeIdentifier
    permitted_clinical_scope_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)
    authorized_case_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)
    valid_from_epoch_seconds: PersistedSequenceNumber
    valid_until_epoch_seconds: PersistedSequenceNumber
    revoked_at_epoch_seconds: PersistedSequenceNumber | None
    qualification_evidence_sha256: _Digest
    verification_record_sha256: _Digest
    verifier_reference_id: SafeIdentifier

    @model_validator(mode="after")
    def _require_valid_canonical_scope(self) -> Self:
        if self.valid_until_epoch_seconds <= self.valid_from_epoch_seconds:
            raise ValueError("qualification validity window must be positive")
        if (
            self.revoked_at_epoch_seconds is not None
            and self.revoked_at_epoch_seconds < self.valid_from_epoch_seconds
        ):
            raise ValueError("qualification revocation cannot predate validity")
        _require_unique_canonical(
            tuple(str(scope_id) for scope_id in self.permitted_clinical_scope_ids),
            "qualification permitted clinical scope IDs",
        )
        _require_unique_canonical(
            tuple(str(case_id) for case_id in self.authorized_case_ids),
            "qualification authorized case IDs",
        )
        return self


class CorpusLicenseEvidence(BaseModel):
    """Authenticated license identity and explicit permitted-use boundary."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    license_reference_id: SafeIdentifier
    license_text_sha256: _Digest
    permission_status: Literal[
        "verified",
        "unknown",
        "incompatible",
        "ambiguous",
        "expired",
        "revoked",
        "conflicting",
    ]
    permits_training_use: bool
    permits_evaluation_use: bool
    permits_derivative_use: bool


class CorpusCustodyEvent(BaseModel):
    """One ordered, content-addressed custody event without personal data."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    sequence: PersistedSequenceNumber
    custodian_reference_id: SafeIdentifier
    event_payload_sha256: _Digest
    previous_event_sha256: _Digest | None
    event_record_sha256: _Digest
    recorded_at_epoch_seconds: PersistedSequenceNumber


class ExpertCorpusProvenance(BaseModel):
    """Complete corpus origin, ownership, license, custody, and inventory evidence."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    source_origin_sha256: _Digest
    ownership_reference_id: SafeIdentifier
    ownership_evidence_sha256: _Digest
    license: CorpusLicenseEvidence
    custody_history: tuple[CorpusCustodyEvent, ...] = Field(min_length=1)
    immutable_inventory_sha256: _Digest

    @model_validator(mode="after")
    def _require_canonical_custody_chain(self) -> Self:
        sequences = tuple(event.sequence for event in self.custody_history)
        if sequences != tuple(range(1, len(sequences) + 1)):
            raise ValueError("corpus custody history must use a contiguous sequence")
        for index, event in enumerate(self.custody_history):
            expected_previous = (
                None
                if index == 0
                else self.custody_history[index - 1].event_record_sha256
            )
            if event.previous_event_sha256 != expected_previous:
                raise ValueError("corpus custody chain is broken")
        return self


class ExpertAnnotationPolicy(BaseModel):
    """Versioned independent-annotation and adjudication policy."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    policy_id: SafeIdentifier
    policy_version: SafeIdentifier
    policy_document_sha256: _Digest
    minimum_independent_annotators: Annotated[int, Field(ge=2)]
    independent_annotation_required: Literal[True]
    blinded_annotation_required: Literal[True]
    separate_adjudicator_required: Literal[True]
    disagreement_retention_required: Literal[True]


class ExpertAnnotationEvidence(BaseModel):
    """One independent expert annotation retained before adjudication."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    annotation_id: SafeIdentifier
    case_id: SafeIdentifier
    pseudonymous_expert_id: SafeIdentifier
    qualification_reference_id: SafeIdentifier
    annotation_payload_sha256: _Digest
    annotation_policy_sha256: _Digest
    created_at_epoch_seconds: PersistedSequenceNumber
    blinded_to_other_annotations: Literal[True]


class ExpertAdjudicationEvidence(BaseModel):
    """One separate qualified adjudication over exact independent annotations."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    adjudication_id: SafeIdentifier
    case_id: SafeIdentifier
    pseudonymous_expert_id: SafeIdentifier
    qualification_reference_id: SafeIdentifier
    annotation_ids: tuple[SafeIdentifier, ...] = Field(min_length=2)
    adjudicated_payload_sha256: _Digest
    annotation_policy_sha256: _Digest
    disagreement_record_sha256: _Digest
    created_at_epoch_seconds: PersistedSequenceNumber

    @model_validator(mode="after")
    def _require_canonical_annotations(self) -> Self:
        _require_unique_canonical(
            tuple(str(annotation_id) for annotation_id in self.annotation_ids),
            "adjudication annotation IDs",
        )
        return self


class ExpertCorpusInventoryItem(BaseModel):
    """Exact reference case and expert-review evidence promoted together."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    case_id: SafeIdentifier
    source_identity_sha256: _Digest
    owner_or_custodian_reference_id: SafeIdentifier
    license: CorpusLicenseEvidence
    provenance_event_sha256s: tuple[_Digest, ...] = Field(min_length=1)
    content_sha256: _Digest
    reference_case_sha256: _Digest
    annotation_ids: tuple[SafeIdentifier, ...] = Field(min_length=2)
    adjudication_id: SafeIdentifier
    annotation_history_sha256s: tuple[_Digest, ...] = Field(min_length=1)
    expert_review_history_sha256s: tuple[_Digest, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _require_canonical_annotations(self) -> Self:
        _require_unique_canonical(
            tuple(str(annotation_id) for annotation_id in self.annotation_ids),
            "corpus inventory annotation IDs",
        )
        if len(self.provenance_event_sha256s) != len(
            set(self.provenance_event_sha256s)
        ):
            raise ValueError(
                "corpus inventory provenance-event identities must be unique"
            )
        _require_unique_canonical(
            self.annotation_history_sha256s,
            "corpus inventory annotation-history identities",
        )
        _require_unique_canonical(
            self.expert_review_history_sha256s,
            "corpus inventory expert-review-history identities",
        )
        return self


class VeterinaryExpertCorpusPromotionRequest(BaseModel):
    """Unsigned complete corpus promotion request accepted by the issuer."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    promotion_id: SafeIdentifier
    corpus_manifest: ReferenceCorpusManifest
    veterinary_domain: Literal["veterinary_medicine"]
    specialty_id: SafeIdentifier
    permitted_clinical_scope_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)
    approval_decision: Literal["approved"]
    calibration_eligible: Literal[True]
    qualifications: tuple[ExpertQualificationReference, ...] = ()
    provenance: ExpertCorpusProvenance
    annotation_policy: ExpertAnnotationPolicy
    annotations: tuple[ExpertAnnotationEvidence, ...] = ()
    adjudications: tuple[ExpertAdjudicationEvidence, ...] = ()
    inventory: tuple[ExpertCorpusInventoryItem, ...] = ()
    authority_sequence: PersistedSequenceNumber
    issued_at_epoch_seconds: PersistedSequenceNumber
    expires_at_epoch_seconds: PersistedSequenceNumber

    @model_validator(mode="after")
    def _require_canonical_collections(self) -> Self:
        if self.expires_at_epoch_seconds <= self.issued_at_epoch_seconds:
            raise ValueError("corpus promotion must expire after issuance")
        _require_unique_canonical(
            tuple(str(scope_id) for scope_id in self.permitted_clinical_scope_ids),
            "corpus permitted clinical scope IDs",
        )
        _require_unique_canonical(
            tuple(
                (
                    str(qualification.pseudonymous_expert_id),
                    str(qualification.qualification_reference_id),
                )
                for qualification in self.qualifications
            ),
            "expert qualifications",
        )
        _require_unique_canonical(
            tuple(str(annotation.annotation_id) for annotation in self.annotations),
            "expert annotations",
        )
        _require_unique_canonical(
            tuple(
                str(adjudication.adjudication_id) for adjudication in self.adjudications
            ),
            "expert adjudications",
        )
        _require_unique_canonical(
            tuple(str(item.case_id) for item in self.inventory),
            "expert corpus inventory",
        )
        return self


class AuthenticatedVeterinaryExpertCorpus(BaseModel):
    """Signed promoted corpus evidence without publication authority."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    request: VeterinaryExpertCorpusPromotionRequest
    cryptographic_domain: Literal["lecture-study.veterinary-expert-corpus.v1"]
    signature_purpose: Literal["expert-corpus-promotion"]
    trust_key_id: _Digest
    authority_id: SafeIdentifier
    detached_signature: _DetachedSignature

    def authentication_payload(self) -> bytes:
        """Return every promoted-corpus field except the detached signature."""

        return json.dumps(
            self.model_dump(mode="json", exclude={"detached_signature"}),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


def _require_unique_canonical(
    identities: tuple[str, ...] | tuple[tuple[str, str], ...],
    label: str,
) -> None:
    if len(identities) != len(set(identities)):
        raise ValueError(f"{label} must be unique")
    if identities != tuple(sorted(identities)):
        raise ValueError(f"{label} must use canonical order")

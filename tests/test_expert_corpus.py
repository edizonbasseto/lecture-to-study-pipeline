"""Adversarial tests for veterinary expert-corpus promotion trust."""

from __future__ import annotations

import hashlib
import multiprocessing
import sqlite3
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from lecture_study.domain.calibration import (
    CalibrationArtifactIdentities,
    ReferenceCorpusCase,
    ReferenceCorpusManifest,
    ReferenceFacetExpectation,
)
from lecture_study.domain.clause_reconciliation import (
    ClauseReconciliationOutcome,
    FacetOutcome,
    SemanticFacet,
)
from lecture_study.domain.expert_corpus import (
    AuthenticatedVeterinaryExpertCorpus,
    CorpusCustodyEvent,
    CorpusLicenseEvidence,
    ExpertAdjudicationEvidence,
    ExpertAnnotationEvidence,
    ExpertAnnotationPolicy,
    ExpertCorpusInventoryItem,
    ExpertCorpusProvenance,
    ExpertQualificationReference,
    VeterinaryExpertCorpusPromotionRequest,
)
from lecture_study.quality.calibration import (
    canonical_reference_corpus_case_sha256,
)
from lecture_study.quality.expert_corpus import (
    ExpertCorpusCurrentStatePort,
    ExpertCorpusCurrentStateObservation,
    ExpertCorpusDisposition,
    ExpertCorpusFailureKind,
    ExpertCorpusPromotionError,
    ExpertCorpusTrustedCurrentStatePort,
    ExpertQualificationCapabilityIssuer,
    ExpertQualificationEvidencePort,
    StoredExpertCorpusCapabilityIssuer,
    StoredExpertCorpusEvidencePort,
    VerifiedExpertQualification,
    VerifiedStoredExpertCorpus,
    VeterinaryCorpusPromotionHmacAuthority,
    VeterinaryCorpusPromotionHmacKey,
    canonical_corpus_custody_event_sha256,
    canonical_expert_annotation_policy_sha256,
    canonical_expert_corpus_evidence_sha256,
    canonical_expert_corpus_inventory_sha256,
)
from lecture_study.quality.expert_corpus_sqlite import (
    SqliteExpertCorpusCurrentState,
    SqliteExpertCorpusCurrentStateOpenMode,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


class _CurrentCorpusState(ExpertCorpusCurrentStatePort):
    def __init__(self) -> None:
        self.current: set[str] = set()
        self.revoked: set[str] = set()
        self.available = True
        self.current_epoch_seconds = 100

    def record_promotion(
        self,
        evidence: AuthenticatedVeterinaryExpertCorpus,
        *,
        evidence_payload_sha256: str,
    ) -> None:
        if not self.available:
            raise RuntimeError("synthetic state unavailable")
        if self.current_epoch_seconds >= evidence.request.expires_at_epoch_seconds:
            raise ExpertCorpusPromotionError(
                "synthetic promotion is stale",
                kind=ExpertCorpusFailureKind.STALE,
                reason_code="stale_promoted_corpus",
            )
        self.current.add(evidence_payload_sha256)

    def require_current(
        self,
        evidence: AuthenticatedVeterinaryExpertCorpus,
        *,
        evidence_payload_sha256: str,
    ) -> None:
        if not self.available:
            raise RuntimeError("synthetic state unavailable")
        if evidence_payload_sha256 in self.revoked:
            raise ExpertCorpusPromotionError(
                "synthetic promotion revoked",
                kind=ExpertCorpusFailureKind.REVOKED,
                reason_code="revoked_promoted_corpus",
            )
        if self.current_epoch_seconds >= evidence.request.expires_at_epoch_seconds:
            raise ExpertCorpusPromotionError(
                "synthetic promotion is stale",
                kind=ExpertCorpusFailureKind.STALE,
                reason_code="stale_promoted_corpus",
            )
        if evidence_payload_sha256 not in self.current:
            raise RuntimeError("synthetic promotion missing")

    def record_revocation(
        self,
        evidence: AuthenticatedVeterinaryExpertCorpus,
        *,
        evidence_payload_sha256: str,
        reason_code: str,
    ) -> None:
        del evidence, reason_code
        if evidence_payload_sha256 not in self.current:
            raise RuntimeError("synthetic promotion missing")
        self.revoked.add(evidence_payload_sha256)


class _TrustedCurrentState(ExpertCorpusTrustedCurrentStatePort):
    def __init__(self, *, epoch_seconds: int = 100) -> None:
        self.epoch_seconds = epoch_seconds
        self.sequence = 0
        self.available = True

    def observe(self) -> ExpertCorpusCurrentStateObservation:
        if not self.available:
            raise RuntimeError("synthetic trusted current state unavailable")
        self.sequence += 1
        return ExpertCorpusCurrentStateObservation(
            epoch_seconds=self.epoch_seconds,
            sequence=self.sequence,
        )


class _ProcessTrustedCurrentState(ExpertCorpusTrustedCurrentStatePort):
    def observe(self) -> ExpertCorpusCurrentStateObservation:
        return ExpertCorpusCurrentStateObservation(
            epoch_seconds=100,
            sequence=time.monotonic_ns(),
        )


class _SyntheticQualificationEvidence(ExpertQualificationEvidencePort):
    """Non-production external-boundary fixture with issuer-local capabilities."""

    def __init__(
        self,
        qualifications: tuple[ExpertQualificationReference, ...],
        *,
        authority_id: str = "synthetic-qualification-authority",
    ) -> None:
        self._authority_id = authority_id
        self._expected = {
            self._identity(qualification): qualification
            for qualification in qualifications
        }
        self._issued: dict[int, VerifiedExpertQualification] = {}
        self.revoked: set[tuple[str, str]] = set()
        self.current_epoch_seconds = 100
        self.available = True
        self.current_checks = 0
        self.last_capability: VerifiedExpertQualification | None = None

    def verify_current(
        self,
        qualification: ExpertQualificationReference,
        *,
        capability_issuer: ExpertQualificationCapabilityIssuer,
    ) -> VerifiedExpertQualification:
        self._require_available()
        self._require_expected(qualification)
        capability = capability_issuer.issue(
            evidence_port=self,
            qualification=qualification,
        )
        self._issued[id(capability)] = capability
        self.last_capability = capability
        self.require_current(capability)
        return capability

    def require_current(
        self,
        capability: VerifiedExpertQualification,
    ) -> ExpertQualificationReference:
        self._require_available()
        self.current_checks += 1
        if (
            type(capability) is not VerifiedExpertQualification
            or self._issued.get(id(capability)) is not capability
        ):
            raise ExpertCorpusPromotionError(
                "synthetic qualification capability was not issued here",
                kind=ExpertCorpusFailureKind.FALSIFIED,
                reason_code="foreign_qualification_capability",
            )
        qualification = capability.qualification
        self._require_expected(qualification)
        identity = self._identity(qualification)
        if identity in self.revoked:
            raise ExpertCorpusPromotionError(
                "synthetic qualification was revoked",
                kind=ExpertCorpusFailureKind.REVOKED,
                reason_code="revoked_external_qualification",
            )
        if not (
            qualification.valid_from_epoch_seconds
            <= self.current_epoch_seconds
            < qualification.valid_until_epoch_seconds
        ):
            raise ExpertCorpusPromotionError(
                "synthetic qualification is not currently valid",
                kind=ExpertCorpusFailureKind.UNVERIFIABLE,
                reason_code="expired_external_qualification",
            )
        return qualification

    @staticmethod
    def _identity(
        qualification: ExpertQualificationReference,
    ) -> tuple[str, str]:
        return (
            str(qualification.pseudonymous_expert_id),
            str(qualification.qualification_reference_id),
        )

    def _require_available(self) -> None:
        if not self.available:
            raise RuntimeError("synthetic qualification evidence unavailable")

    def _require_expected(
        self,
        qualification: ExpertQualificationReference,
    ) -> None:
        identity = self._identity(qualification)
        expected = self._expected.get(identity)
        if (
            expected is None
            or qualification.qualification_authority_id != self._authority_id
            or expected != qualification
        ):
            raise ExpertCorpusPromotionError(
                "synthetic qualification evidence was substituted",
                kind=ExpertCorpusFailureKind.SUBSTITUTED,
                reason_code="substituted_external_qualification",
            )


class _SyntheticStoredCorpusEvidence(StoredExpertCorpusEvidencePort):
    """Non-production resolver for exact synthetic stored-corpus snapshots."""

    def __init__(self, request: VeterinaryExpertCorpusPromotionRequest) -> None:
        self.expected = request
        self.available = True
        self.substituted: VeterinaryExpertCorpusPromotionRequest | None = None
        self._issued: dict[int, VerifiedStoredExpertCorpus] = {}
        self.current_checks = 0
        self.last_capability: VerifiedStoredExpertCorpus | None = None

    def resolve_current(
        self,
        request: VeterinaryExpertCorpusPromotionRequest,
        *,
        capability_issuer: StoredExpertCorpusCapabilityIssuer,
    ) -> VerifiedStoredExpertCorpus:
        self._require_available()
        if request != self.expected:
            raise ExpertCorpusPromotionError(
                "synthetic stored corpus was substituted",
                kind=ExpertCorpusFailureKind.SUBSTITUTED,
                reason_code="substituted_stored_corpus",
            )
        capability = capability_issuer.issue(
            evidence_port=self,
            request=request,
        )
        self._issued[id(capability)] = capability
        self.last_capability = capability
        return capability

    def require_current(
        self,
        capability: VerifiedStoredExpertCorpus,
    ) -> VeterinaryExpertCorpusPromotionRequest:
        self._require_available()
        self.current_checks += 1
        if (
            type(capability) is not VerifiedStoredExpertCorpus
            or self._issued.get(id(capability)) is not capability
        ):
            raise ExpertCorpusPromotionError(
                "synthetic stored corpus capability was not issued here",
                kind=ExpertCorpusFailureKind.FALSIFIED,
                reason_code="foreign_stored_corpus_capability",
            )
        if capability.request != self.expected:
            raise ExpertCorpusPromotionError(
                "synthetic stored corpus capability was substituted",
                kind=ExpertCorpusFailureKind.SUBSTITUTED,
                reason_code="substituted_stored_corpus_capability",
            )
        return self.substituted if self.substituted is not None else self.expected

    def _require_available(self) -> None:
        if not self.available:
            raise RuntimeError("synthetic stored corpus evidence unavailable")


def _artifact_identities(case_index: int) -> CalibrationArtifactIdentities:
    return CalibrationArtifactIdentities(
        source_manifest_sha256=_digest(f"source-manifest-{case_index}"),
        source_content_aggregation_sha256=_digest(f"aggregation-{case_index}"),
        source_universe_sha256=_digest(f"source-universe-{case_index}"),
        clause_inventory_sha256=_digest(f"clause-inventory-{case_index}"),
        candidate_batches_sha256=_digest(f"candidate-batches-{case_index}"),
        atomic_ledger_sha256=_digest(f"atomic-ledger-{case_index}"),
        reconciliations_sha256=_digest(f"reconciliations-{case_index}"),
    )


def _reference_manifest() -> ReferenceCorpusManifest:
    cases = tuple(
        ReferenceCorpusCase(
            schema_version=1,
            case_id=f"case-{index}",
            lecture_id=f"lecture-{index}",
            learning_unit_id=f"unit-{index}",
            source_clause_id=f"clause-{index}",
            artifact_identities=_artifact_identities(index),
            reference_annotation_sha256=_digest(f"reference-annotation-{index}"),
            expected_outcome=ClauseReconciliationOutcome.REPRESENTED,
            facet_expectations=(
                ReferenceFacetExpectation(
                    facet=SemanticFacet.DISPOSITION,
                    expected_outcome=FacetOutcome.MATCHED,
                ),
            ),
        )
        for index in range(4)
    )
    return ReferenceCorpusManifest(
        schema_version=1,
        corpus_id="synthetic-corpus",
        corpus_version="synthetic-v1",
        annotation_schema_sha256=_digest("synthetic-annotation-schema"),
        rubric_sha256=_digest("synthetic-rubric"),
        cases=cases,
    )


def _promotion_request() -> VeterinaryExpertCorpusPromotionRequest:
    manifest = _reference_manifest()
    case_ids = tuple(str(case.case_id) for case in manifest.cases)
    policy = ExpertAnnotationPolicy(
        policy_id="synthetic-policy",
        policy_version="synthetic-v1",
        policy_document_sha256=_digest("synthetic-policy-document"),
        minimum_independent_annotators=2,
        independent_annotation_required=True,
        blinded_annotation_required=True,
        separate_adjudicator_required=True,
        disagreement_retention_required=True,
    )
    policy_sha256 = canonical_expert_annotation_policy_sha256(policy)
    annotations = tuple(
        ExpertAnnotationEvidence(
            annotation_id=f"annotation-{annotator}-{case_id}",
            case_id=case_id,
            pseudonymous_expert_id=f"expert-{annotator}",
            qualification_reference_id=f"qualification-{annotator}",
            annotation_payload_sha256=_digest(
                f"synthetic-annotation-{annotator}-{case_id}"
            ),
            annotation_policy_sha256=policy_sha256,
            created_at_epoch_seconds=20,
            blinded_to_other_annotations=True,
        )
        for annotator in ("a", "b")
        for case_id in case_ids
    )
    adjudications = tuple(
        ExpertAdjudicationEvidence(
            adjudication_id=f"adjudication-{case_id}",
            case_id=case_id,
            pseudonymous_expert_id="expert-c",
            qualification_reference_id="qualification-c",
            annotation_ids=(
                f"annotation-a-{case_id}",
                f"annotation-b-{case_id}",
            ),
            adjudicated_payload_sha256=_digest(f"adjudicated-{case_id}"),
            annotation_policy_sha256=policy_sha256,
            disagreement_record_sha256=_digest(f"disagreement-{case_id}"),
            created_at_epoch_seconds=30,
        )
        for case_id in case_ids
    )
    custody = CorpusCustodyEvent(
        sequence=1,
        custodian_reference_id="synthetic-custodian",
        event_payload_sha256=_digest("synthetic-custody-event"),
        previous_event_sha256=None,
        event_record_sha256=_digest("synthetic-custody-record"),
        recorded_at_epoch_seconds=10,
    )
    custody = custody.model_copy(
        update={"event_record_sha256": canonical_corpus_custody_event_sha256(custody)}
    )
    record_license = CorpusLicenseEvidence(
        license_reference_id="synthetic-license",
        license_text_sha256=_digest("synthetic-license-text"),
        permission_status="verified",
        permits_training_use=True,
        permits_evaluation_use=True,
        permits_derivative_use=False,
    )
    annotation_by_id = {
        str(annotation.annotation_id): annotation for annotation in annotations
    }
    adjudication_by_id = {
        str(adjudication.adjudication_id): adjudication
        for adjudication in adjudications
    }
    inventory_items: list[ExpertCorpusInventoryItem] = []
    for case in manifest.cases:
        annotation_ids = (
            f"annotation-a-{case.case_id}",
            f"annotation-b-{case.case_id}",
        )
        annotation_history = tuple(
            sorted(
                annotation_by_id[annotation_id].annotation_payload_sha256
                for annotation_id in annotation_ids
            )
        )
        adjudication_id = f"adjudication-{case.case_id}"
        adjudication = adjudication_by_id[adjudication_id]
        inventory_items.append(
            ExpertCorpusInventoryItem(
                case_id=str(case.case_id),
                source_identity_sha256=_digest(
                    f"synthetic-source-identity-{case.case_id}"
                ),
                owner_or_custodian_reference_id="synthetic-owner",
                license=record_license,
                provenance_event_sha256s=(custody.event_record_sha256,),
                content_sha256=_digest(f"synthetic-record-content-{case.case_id}"),
                reference_case_sha256=canonical_reference_corpus_case_sha256(case),
                annotation_ids=annotation_ids,
                adjudication_id=adjudication_id,
                annotation_history_sha256s=annotation_history,
                expert_review_history_sha256s=tuple(
                    sorted(
                        (
                            *annotation_history,
                            adjudication.adjudicated_payload_sha256,
                            adjudication.disagreement_record_sha256,
                        )
                    )
                ),
            )
        )
    inventory = tuple(inventory_items)
    qualifications = tuple(
        ExpertQualificationReference(
            pseudonymous_expert_id=f"expert-{expert}",
            qualification_reference_id=f"qualification-{expert}",
            qualification_authority_id="synthetic-qualification-authority",
            jurisdiction_id="synthetic-jurisdiction",
            professional_category="licensed_veterinarian",
            specialty_id="synthetic-specialty",
            permitted_clinical_scope_ids=("synthetic-clinical-scope",),
            authorized_case_ids=case_ids,
            valid_from_epoch_seconds=1,
            valid_until_epoch_seconds=1_000,
            revoked_at_epoch_seconds=None,
            qualification_evidence_sha256=_digest(f"synthetic-qualification-{expert}"),
            verification_record_sha256=_digest(f"synthetic-verification-{expert}"),
            verifier_reference_id="synthetic-verifier",
        )
        for expert in ("a", "b", "c")
    )
    return VeterinaryExpertCorpusPromotionRequest(
        schema_version=1,
        promotion_id="synthetic-promotion",
        corpus_manifest=manifest,
        veterinary_domain="veterinary_medicine",
        specialty_id="synthetic-specialty",
        permitted_clinical_scope_ids=("synthetic-clinical-scope",),
        approval_decision="approved",
        calibration_eligible=True,
        qualifications=qualifications,
        provenance=ExpertCorpusProvenance(
            source_origin_sha256=_digest("synthetic-origin"),
            ownership_reference_id="synthetic-owner",
            ownership_evidence_sha256=_digest("synthetic-ownership"),
            license=CorpusLicenseEvidence(
                license_reference_id="synthetic-license",
                license_text_sha256=_digest("synthetic-license-text"),
                permission_status="verified",
                permits_training_use=True,
                permits_evaluation_use=True,
                permits_derivative_use=False,
            ),
            custody_history=(custody,),
            immutable_inventory_sha256=canonical_expert_corpus_inventory_sha256(
                inventory
            ),
        ),
        annotation_policy=policy,
        annotations=annotations,
        adjudications=adjudications,
        inventory=inventory,
        authority_sequence=1,
        issued_at_epoch_seconds=100,
        expires_at_epoch_seconds=900,
    )


def _authority(
    state: ExpertCorpusCurrentStatePort,
    *,
    authority_id: str = "synthetic-corpus-authority",
    key_label: str = "primary",
    qualification_evidence: _SyntheticQualificationEvidence | None = None,
    stored_corpus_evidence: _SyntheticStoredCorpusEvidence | None = None,
) -> VeterinaryCorpusPromotionHmacAuthority:
    material = (
        f"synthetic-non-production-corpus-key-{key_label}-not-a-real-secret"
    ).encode()
    return VeterinaryCorpusPromotionHmacAuthority(
        key=VeterinaryCorpusPromotionHmacKey(material),
        authority_id=authority_id,
        current_state=state,
        qualification_evidence=(
            qualification_evidence
            if qualification_evidence is not None
            else _SyntheticQualificationEvidence(_promotion_request().qualifications)
        ),
        stored_corpus_evidence=(
            stored_corpus_evidence
            if stored_corpus_evidence is not None
            else _SyntheticStoredCorpusEvidence(_promotion_request())
        ),
    )


def test_exact_promoted_corpus_is_issuer_only_and_never_publication_ready() -> None:
    state = _CurrentCorpusState()
    authority = _authority(state)

    capability = authority.issue(_promotion_request())
    evidence = authority.require_current(capability)

    assert evidence.cryptographic_domain == (
        "lecture-study.veterinary-expert-corpus.v1"
    )
    assert evidence.signature_purpose == "expert-corpus-promotion"
    assert capability.publication_ready is False
    assert capability.production_eligible is False
    with pytest.raises(TypeError, match="issued only"):
        type(capability)()


def test_license_and_inventory_substitution_are_quarantined() -> None:
    state = _CurrentCorpusState()
    authority = _authority(state)
    request = _promotion_request()
    forbidden_license = request.provenance.license.model_copy(
        update={"permits_evaluation_use": False}
    )
    forbidden_request = request.model_copy(
        update={
            "provenance": request.provenance.model_copy(
                update={"license": forbidden_license}
            )
        }
    )

    with pytest.raises(ExpertCorpusPromotionError) as license_error:
        authority.issue(forbidden_request)
    assert license_error.value.disposition is ExpertCorpusDisposition.QUARANTINE

    substituted_item = request.inventory[0].model_copy(
        update={"reference_case_sha256": _digest("substituted-reference")}
    )
    substituted_request = request.model_copy(
        update={"inventory": (substituted_item, *request.inventory[1:])}
    )
    with pytest.raises(ExpertCorpusPromotionError) as inventory_error:
        authority.issue(substituted_request)
    assert inventory_error.value.reason_code == "corpus_inventory_hash_mismatch"


def test_insufficient_independence_needs_review_and_adjudicator_reuse_quarantines() -> (
    None
):
    state = _CurrentCorpusState()
    authority = _authority(state)
    request = _promotion_request()
    strict_policy = request.annotation_policy.model_copy(
        update={"minimum_independent_annotators": 3}
    )
    strict_policy_hash = canonical_expert_annotation_policy_sha256(strict_policy)
    strict_annotations = tuple(
        annotation.model_copy(update={"annotation_policy_sha256": strict_policy_hash})
        for annotation in request.annotations
    )
    strict_adjudications = tuple(
        adjudication.model_copy(update={"annotation_policy_sha256": strict_policy_hash})
        for adjudication in request.adjudications
    )
    strict_request = request.model_copy(
        update={
            "annotation_policy": strict_policy,
            "annotations": strict_annotations,
            "adjudications": strict_adjudications,
        }
    )
    with pytest.raises(ExpertCorpusPromotionError) as independence_error:
        authority.issue(strict_request)
    assert independence_error.value.disposition is ExpertCorpusDisposition.NEEDS_REVIEW

    reused_adjudications = tuple(
        adjudication.model_copy(
            update={
                "pseudonymous_expert_id": "expert-a",
                "qualification_reference_id": "qualification-a",
            }
        )
        for adjudication in request.adjudications
    )
    with pytest.raises(ExpertCorpusPromotionError) as adjudication_error:
        authority.issue(
            request.model_copy(update={"adjudications": reused_adjudications})
        )
    assert adjudication_error.value.reason_code == "non_independent_adjudicator"


def test_revocation_restart_and_trust_confusion_fail_closed() -> None:
    state = _CurrentCorpusState()
    authority = _authority(state)
    capability = authority.issue(_promotion_request())
    evidence_sha256 = canonical_expert_corpus_evidence_sha256(capability.evidence)
    state.revoked.add(evidence_sha256)

    with pytest.raises(ExpertCorpusPromotionError) as revoked:
        authority.require_current(capability)
    assert revoked.value.kind is ExpertCorpusFailureKind.REVOKED
    assert revoked.value.disposition is ExpertCorpusDisposition.QUARANTINE

    foreign_authority = _authority(
        state,
        authority_id="foreign-corpus-authority",
        key_label="foreign",
    )
    with pytest.raises(ExpertCorpusPromotionError) as foreign:
        foreign_authority.require_current(capability)
    assert foreign.value.reason_code == "foreign_corpus_capability"


def test_revoked_qualification_and_custody_substitution_are_quarantined() -> None:
    state = _CurrentCorpusState()
    authority = _authority(state)
    request = _promotion_request()
    revoked_qualification = request.qualifications[0].model_copy(
        update={"revoked_at_epoch_seconds": 50}
    )
    with pytest.raises(ExpertCorpusPromotionError) as revoked:
        authority.issue(
            request.model_copy(
                update={
                    "qualifications": (
                        revoked_qualification,
                        *request.qualifications[1:],
                    )
                }
            )
        )
    assert revoked.value.reason_code == "revoked_expert_qualification"
    assert revoked.value.disposition is ExpertCorpusDisposition.QUARANTINE

    substituted_custody = request.provenance.custody_history[0].model_copy(
        update={"custodian_reference_id": "substituted-custodian"}
    )
    substituted_provenance = request.provenance.model_copy(
        update={"custody_history": (substituted_custody,)}
    )
    with pytest.raises(ExpertCorpusPromotionError) as substituted:
        authority.issue(
            request.model_copy(update={"provenance": substituted_provenance})
        )
    assert substituted.value.reason_code == "corpus_custody_event_hash_mismatch"


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("cryptographic_domain", "lecture-study.source-manifest.v1"),
        ("signature_purpose", "source-identity-location-integrity"),
        ("trust_key_id", _digest("foreign-key")),
        ("authority_id", "foreign-authority"),
    ),
)
def test_signed_corpus_trust_scope_mutation_is_rejected(
    field: str,
    replacement: str,
) -> None:
    state = _CurrentCorpusState()
    authority = _authority(state)
    evidence = authority.issue(_promotion_request()).evidence
    mutated = evidence.model_copy(update={field: replacement})

    with pytest.raises(ExpertCorpusPromotionError) as rejected:
        authority.verify(mutated)
    assert rejected.value.disposition is ExpertCorpusDisposition.QUARANTINE


def test_missing_or_unavailable_evidence_blocks_only_dependent_scope() -> None:
    state = _CurrentCorpusState()
    authority = _authority(state)
    request = _promotion_request().model_copy(
        update={"annotations": (), "adjudications": (), "inventory": ()}
    )
    with pytest.raises(ExpertCorpusPromotionError) as missing:
        authority.issue(request)
    assert missing.value.disposition is ExpertCorpusDisposition.NEEDS_REVIEW

    state.available = False
    with pytest.raises(ExpertCorpusPromotionError) as unavailable:
        authority.issue(_promotion_request())
    assert unavailable.value.disposition is ExpertCorpusDisposition.NEEDS_REVIEW
    assert unavailable.value.independent_work_may_continue is True
    assert unavailable.value.publication_ready is False


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("qualification_authority_id", "caller-selected-authority"),
        ("qualification_evidence_sha256", _digest("caller-selected-evidence")),
        ("verification_record_sha256", _digest("caller-selected-verification")),
        ("verifier_reference_id", "caller-selected-verifier"),
    ),
)
def test_caller_declared_qualification_fields_cannot_self_authenticate(
    field: str,
    replacement: str,
) -> None:
    state = _CurrentCorpusState()
    request = _promotion_request()
    qualification_evidence = _SyntheticQualificationEvidence(request.qualifications)
    authority = _authority(
        state,
        qualification_evidence=qualification_evidence,
    )
    substituted = request.qualifications[0].model_copy(update={field: replacement})

    with pytest.raises(ExpertCorpusPromotionError) as rejected:
        authority.issue(
            request.model_copy(
                update={
                    "qualifications": (
                        substituted,
                        *request.qualifications[1:],
                    )
                }
            )
        )

    assert rejected.value.kind is ExpertCorpusFailureKind.SUBSTITUTED
    assert rejected.value.reason_code == "substituted_external_qualification"
    assert rejected.value.disposition is ExpertCorpusDisposition.QUARANTINE


def test_external_qualification_is_revalidated_on_every_consumption() -> None:
    state = _CurrentCorpusState()
    request = _promotion_request()
    qualification_evidence = _SyntheticQualificationEvidence(request.qualifications)
    authority = _authority(
        state,
        qualification_evidence=qualification_evidence,
    )
    capability = authority.issue(request)
    checks_after_issue = qualification_evidence.current_checks

    authority.require_current(capability)
    checks_after_first_consumption = qualification_evidence.current_checks
    authority.require_current(capability)

    qualification_count = len(request.qualifications)
    assert checks_after_first_consumption == checks_after_issue + qualification_count
    assert (
        qualification_evidence.current_checks
        == checks_after_first_consumption + qualification_count
    )


def test_external_qualification_revocation_invalidates_existing_capability() -> None:
    state = _CurrentCorpusState()
    request = _promotion_request()
    qualification_evidence = _SyntheticQualificationEvidence(request.qualifications)
    authority = _authority(
        state,
        qualification_evidence=qualification_evidence,
    )
    capability = authority.issue(request)
    revoked = request.qualifications[0]
    qualification_evidence.revoked.add(
        (
            str(revoked.pseudonymous_expert_id),
            str(revoked.qualification_reference_id),
        )
    )

    with pytest.raises(ExpertCorpusPromotionError) as rejected:
        authority.require_current(capability)

    assert rejected.value.kind is ExpertCorpusFailureKind.REVOKED
    assert rejected.value.reason_code == "revoked_external_qualification"
    assert rejected.value.disposition is ExpertCorpusDisposition.QUARANTINE


def test_external_qualification_expiry_blocks_existing_capability() -> None:
    state = _CurrentCorpusState()
    request = _promotion_request()
    qualification_evidence = _SyntheticQualificationEvidence(request.qualifications)
    authority = _authority(
        state,
        qualification_evidence=qualification_evidence,
    )
    capability = authority.issue(request)
    qualification_evidence.current_epoch_seconds = 1_000

    with pytest.raises(ExpertCorpusPromotionError) as rejected:
        authority.require_current(capability)

    assert rejected.value.kind is ExpertCorpusFailureKind.UNVERIFIABLE
    assert rejected.value.reason_code == "expired_external_qualification"
    assert rejected.value.disposition is ExpertCorpusDisposition.NEEDS_REVIEW


def test_external_qualification_authority_confusion_is_quarantined() -> None:
    state = _CurrentCorpusState()
    request = _promotion_request()
    foreign_evidence = _SyntheticQualificationEvidence(
        request.qualifications,
        authority_id="foreign-qualification-authority",
    )
    authority = _authority(state, qualification_evidence=foreign_evidence)

    with pytest.raises(ExpertCorpusPromotionError) as rejected:
        authority.issue(request)

    assert rejected.value.kind is ExpertCorpusFailureKind.SUBSTITUTED
    assert rejected.value.reason_code == "substituted_external_qualification"


def test_external_qualification_unavailability_fails_closed_at_issue_and_use() -> None:
    state = _CurrentCorpusState()
    request = _promotion_request()
    qualification_evidence = _SyntheticQualificationEvidence(request.qualifications)
    authority = _authority(
        state,
        qualification_evidence=qualification_evidence,
    )
    qualification_evidence.available = False

    with pytest.raises(ExpertCorpusPromotionError) as issue_failure:
        authority.issue(request)
    assert issue_failure.value.kind is ExpertCorpusFailureKind.UNAVAILABLE
    assert issue_failure.value.reason_code == "qualification_evidence_unavailable"

    qualification_evidence.available = True
    capability = authority.issue(request)
    qualification_evidence.available = False
    with pytest.raises(ExpertCorpusPromotionError) as consumption_failure:
        authority.require_current(capability)
    assert consumption_failure.value.kind is ExpertCorpusFailureKind.UNAVAILABLE
    assert (
        consumption_failure.value.reason_code
        == "qualification_current_state_unavailable"
    )


def test_constructed_qualification_capability_has_no_authority() -> None:
    request = _promotion_request()
    qualification_evidence = _SyntheticQualificationEvidence(request.qualifications)

    with pytest.raises(TypeError, match="issued only"):
        VerifiedExpertQualification()

    constructed = object.__new__(VerifiedExpertQualification)
    object.__setattr__(constructed, "qualification", request.qualifications[0])
    with pytest.raises(ExpertCorpusPromotionError) as rejected:
        qualification_evidence.require_current(constructed)

    assert rejected.value.kind is ExpertCorpusFailureKind.FALSIFIED
    assert rejected.value.reason_code == "foreign_qualification_capability"

    with pytest.raises(TypeError, match="created only"):
        ExpertQualificationCapabilityIssuer()
    forged_issuer = object.__new__(ExpertQualificationCapabilityIssuer)
    object.__setattr__(forged_issuer, "_evidence_port", qualification_evidence)
    with pytest.raises(ExpertCorpusPromotionError) as forged:
        forged_issuer.issue(
            evidence_port=qualification_evidence,
            qualification=request.qualifications[0],
        )
    assert forged.value.reason_code == "foreign_qualification_issuer"


def test_qualification_capability_replay_across_verifiers_is_rejected() -> None:
    state = _CurrentCorpusState()
    request = _promotion_request()
    issuing_verifier = _SyntheticQualificationEvidence(request.qualifications)
    foreign_verifier = _SyntheticQualificationEvidence(request.qualifications)
    authority = _authority(state, qualification_evidence=issuing_verifier)
    authority.issue(request)
    capability = issuing_verifier.last_capability
    assert capability is not None

    with pytest.raises(ExpertCorpusPromotionError) as rejected:
        foreign_verifier.require_current(capability)

    assert rejected.value.kind is ExpertCorpusFailureKind.FALSIFIED
    assert rejected.value.reason_code == "foreign_qualification_capability"


def test_professional_categories_and_corpus_approval_are_closed() -> None:
    request = _promotion_request()
    qualification_payload = request.qualifications[0].model_dump(mode="python")
    qualification_payload["professional_category"] = "self_declared_expert"
    with pytest.raises(ValidationError):
        ExpertQualificationReference.model_validate(qualification_payload)

    request_payload = request.model_dump(mode="python")
    request_payload["approval_decision"] = "pending"
    with pytest.raises(ValidationError):
        VeterinaryExpertCorpusPromotionRequest.model_validate(request_payload)

    request_payload = request.model_dump(mode="python")
    request_payload["calibration_eligible"] = False
    with pytest.raises(ValidationError):
        VeterinaryExpertCorpusPromotionRequest.model_validate(request_payload)


def test_specialty_and_clinical_scope_are_exactly_bound() -> None:
    request = _promotion_request()
    specialty_mismatch = request.qualifications[0].model_copy(
        update={"specialty_id": "other-specialty"}
    )
    with pytest.raises(ExpertCorpusPromotionError) as specialty:
        _authority(
            _CurrentCorpusState(),
            stored_corpus_evidence=_SyntheticStoredCorpusEvidence(
                request.model_copy(
                    update={
                        "qualifications": (
                            specialty_mismatch,
                            *request.qualifications[1:],
                        )
                    }
                )
            ),
        ).issue(
            request.model_copy(
                update={
                    "qualifications": (
                        specialty_mismatch,
                        *request.qualifications[1:],
                    )
                }
            )
        )
    assert specialty.value.reason_code == "expert_specialty_mismatch"

    scope_mismatch = request.qualifications[0].model_copy(
        update={"permitted_clinical_scope_ids": ("other-scope",)}
    )
    scope_request = request.model_copy(
        update={
            "qualifications": (
                scope_mismatch,
                *request.qualifications[1:],
            )
        }
    )
    with pytest.raises(ExpertCorpusPromotionError) as scope:
        _authority(
            _CurrentCorpusState(),
            stored_corpus_evidence=_SyntheticStoredCorpusEvidence(scope_request),
        ).issue(scope_request)
    assert scope.value.reason_code == "expert_clinical_scope_mismatch"


@pytest.mark.parametrize(
    ("permission_status", "expected_disposition"),
    (
        ("unknown", ExpertCorpusDisposition.NEEDS_REVIEW),
        ("ambiguous", ExpertCorpusDisposition.NEEDS_REVIEW),
        ("expired", ExpertCorpusDisposition.NEEDS_REVIEW),
        ("incompatible", ExpertCorpusDisposition.QUARANTINE),
        ("revoked", ExpertCorpusDisposition.QUARANTINE),
        ("conflicting", ExpertCorpusDisposition.QUARANTINE),
    ),
)
def test_uncertain_or_invalid_permissions_fail_closed(
    permission_status: str,
    expected_disposition: ExpertCorpusDisposition,
) -> None:
    request = _promotion_request()
    license_evidence = request.provenance.license.model_copy(
        update={"permission_status": permission_status}
    )
    mutated = request.model_copy(
        update={
            "provenance": request.provenance.model_copy(
                update={"license": license_evidence}
            )
        }
    )
    with pytest.raises(ExpertCorpusPromotionError) as rejected:
        _authority(
            _CurrentCorpusState(),
            stored_corpus_evidence=_SyntheticStoredCorpusEvidence(mutated),
        ).issue(mutated)
    assert rejected.value.disposition is expected_disposition


@pytest.mark.parametrize(
    "field",
    (
        "source_identity_sha256",
        "content_sha256",
        "license",
    ),
)
def test_stored_record_substitution_is_not_accepted_from_caller_fields(
    field: str,
) -> None:
    request = _promotion_request()
    replacement: object
    if field == "license":
        replacement = request.inventory[0].license.model_copy(
            update={"license_text_sha256": _digest("substituted-record-license")}
        )
    else:
        replacement = _digest(f"substituted-{field}")
    item = request.inventory[0].model_copy(update={field: replacement})
    inventory = (item, *request.inventory[1:])
    mutated = request.model_copy(
        update={
            "inventory": inventory,
            "provenance": request.provenance.model_copy(
                update={
                    "immutable_inventory_sha256": (
                        canonical_expert_corpus_inventory_sha256(inventory)
                    )
                }
            ),
        }
    )

    with pytest.raises(ExpertCorpusPromotionError) as rejected:
        _authority(_CurrentCorpusState()).issue(mutated)

    assert rejected.value.kind is ExpertCorpusFailureKind.SUBSTITUTED
    assert rejected.value.reason_code == "substituted_stored_corpus"


def test_stored_corpus_is_issuer_only_and_revalidated_on_every_use() -> None:
    request = _promotion_request()
    stored = _SyntheticStoredCorpusEvidence(request)
    authority = _authority(
        _CurrentCorpusState(),
        stored_corpus_evidence=stored,
    )
    capability = authority.issue(request)
    assert stored.current_checks >= 1
    assert capability.production_eligible is False

    stored.substituted = request.model_copy(
        update={"approval_decision": "not-approved"}
    )
    with pytest.raises(ExpertCorpusPromotionError) as substituted:
        authority.require_current(capability)
    assert substituted.value.reason_code == "substituted_stored_corpus_current_state"

    with pytest.raises(TypeError, match="issued only"):
        VerifiedStoredExpertCorpus()
    with pytest.raises(TypeError, match="created only"):
        StoredExpertCorpusCapabilityIssuer()


def test_forged_or_replayed_stored_corpus_capability_has_no_authority() -> None:
    request = _promotion_request()
    issuing_resolver = _SyntheticStoredCorpusEvidence(request)
    foreign_resolver = _SyntheticStoredCorpusEvidence(request)
    authority = _authority(
        _CurrentCorpusState(),
        stored_corpus_evidence=issuing_resolver,
    )
    authority.issue(request)
    capability = issuing_resolver.last_capability
    assert capability is not None

    with pytest.raises(ExpertCorpusPromotionError) as replayed:
        foreign_resolver.require_current(capability)
    assert replayed.value.kind is ExpertCorpusFailureKind.FALSIFIED

    constructed = object.__new__(VerifiedStoredExpertCorpus)
    object.__setattr__(constructed, "request", request)
    with pytest.raises(ExpertCorpusPromotionError) as forged:
        authority._require_stored_corpus_current(request, constructed)
    assert forged.value.kind is ExpertCorpusFailureKind.FALSIFIED

    forged_issuer = object.__new__(StoredExpertCorpusCapabilityIssuer)
    object.__setattr__(forged_issuer, "_evidence_port", issuing_resolver)
    with pytest.raises(ExpertCorpusPromotionError) as issuer_failure:
        forged_issuer.issue(
            evidence_port=issuing_resolver,
            request=request,
        )
    assert issuer_failure.value.reason_code == "foreign_stored_corpus_issuer"


def test_stored_corpus_unavailability_blocks_issue_and_consumption() -> None:
    request = _promotion_request()
    stored = _SyntheticStoredCorpusEvidence(request)
    authority = _authority(
        _CurrentCorpusState(),
        stored_corpus_evidence=stored,
    )
    stored.available = False
    with pytest.raises(ExpertCorpusPromotionError) as issue_failure:
        authority.issue(request)
    assert issue_failure.value.kind is ExpertCorpusFailureKind.UNAVAILABLE

    stored.available = True
    capability = authority.issue(request)
    stored.available = False
    with pytest.raises(ExpertCorpusPromotionError) as consumption_failure:
        authority.require_current(capability)
    assert consumption_failure.value.kind is ExpertCorpusFailureKind.UNAVAILABLE


def _signed_evidence(
    request: VeterinaryExpertCorpusPromotionRequest | None = None,
) -> AuthenticatedVeterinaryExpertCorpus:
    exact_request = request if request is not None else _promotion_request()
    authority = _authority(
        _CurrentCorpusState(),
        stored_corpus_evidence=_SyntheticStoredCorpusEvidence(exact_request),
    )
    return authority.require_current(authority.issue(exact_request))


def _record_promotion_in_process(
    registry_root: str,
    evidence_json: str,
    evidence_payload_sha256: str,
) -> None:
    evidence = AuthenticatedVeterinaryExpertCorpus.model_validate_json(evidence_json)
    registry = SqliteExpertCorpusCurrentState(
        registry_root=Path(registry_root),
        mode=SqliteExpertCorpusCurrentStateOpenMode.OPEN_EXISTING,
        busy_timeout_milliseconds=5_000,
        lock_timeout_milliseconds=5_000,
        trusted_current_state=_ProcessTrustedCurrentState(),
    )
    registry.record_promotion(
        evidence,
        evidence_payload_sha256=evidence_payload_sha256,
    )


def _persistent_registry(
    root: Path,
    trusted_current_state: ExpertCorpusTrustedCurrentStatePort,
) -> SqliteExpertCorpusCurrentState:
    return SqliteExpertCorpusCurrentState(
        registry_root=root,
        mode=SqliteExpertCorpusCurrentStateOpenMode.INITIALIZE,
        busy_timeout_milliseconds=5_000,
        lock_timeout_milliseconds=5_000,
        trusted_current_state=trusted_current_state,
    )


def test_persistent_current_state_survives_restart_and_is_append_only(
    tmp_path: Path,
) -> None:
    root = tmp_path / "expert-corpus-state"
    root.mkdir()
    evidence = AuthenticatedVeterinaryExpertCorpus.model_validate(_signed_evidence())
    identity = canonical_expert_corpus_evidence_sha256(evidence)
    trusted_current_state = _TrustedCurrentState()
    registry = _persistent_registry(root, trusted_current_state)
    registry.record_promotion(evidence, evidence_payload_sha256=identity)
    registry.record_promotion(evidence, evidence_payload_sha256=identity)

    reopened = SqliteExpertCorpusCurrentState(
        registry_root=root,
        mode=SqliteExpertCorpusCurrentStateOpenMode.OPEN_EXISTING,
        busy_timeout_milliseconds=5_000,
        lock_timeout_milliseconds=5_000,
        trusted_current_state=trusted_current_state,
    )
    reopened.require_current(evidence, evidence_payload_sha256=identity)
    assert not hasattr(reopened, "delete")
    assert not hasattr(reopened, "release")
    assert not hasattr(reopened, "overwrite")


def test_promotion_authority_revalidates_persistent_state_after_restart(
    tmp_path: Path,
) -> None:
    root = tmp_path / "expert-corpus-state"
    root.mkdir()
    request = _promotion_request()
    trusted_current_state = _TrustedCurrentState()
    first_state = _persistent_registry(root, trusted_current_state)
    first_authority = _authority(
        first_state,
        stored_corpus_evidence=_SyntheticStoredCorpusEvidence(request),
    )
    evidence = first_authority.require_current(first_authority.issue(request))

    reopened_state = SqliteExpertCorpusCurrentState(
        registry_root=root,
        mode=SqliteExpertCorpusCurrentStateOpenMode.OPEN_EXISTING,
        busy_timeout_milliseconds=5_000,
        lock_timeout_milliseconds=5_000,
        trusted_current_state=trusted_current_state,
    )
    reopened_authority = _authority(
        reopened_state,
        stored_corpus_evidence=_SyntheticStoredCorpusEvidence(request),
    )
    capability = reopened_authority.verify(evidence)

    assert reopened_authority.require_current(capability) == evidence
    assert capability.production_eligible is False
    assert capability.publication_ready is False


def _current_state_event_kinds(
    registry: SqliteExpertCorpusCurrentState,
) -> tuple[str, ...]:
    with sqlite3.connect(registry.database_path) as connection:
        return tuple(
            str(row[0])
            for row in connection.execute(
                """
                SELECT event_kind
                FROM expert_corpus_current_state_events
                ORDER BY sequence
                """
            )
        )


def test_expiry_is_append_only_and_survives_restart(tmp_path: Path) -> None:
    root = tmp_path / "expert-corpus-state"
    root.mkdir()
    trusted_current_state = _TrustedCurrentState()
    registry = _persistent_registry(root, trusted_current_state)
    evidence = _signed_evidence()
    identity = canonical_expert_corpus_evidence_sha256(evidence)
    registry.record_promotion(evidence, evidence_payload_sha256=identity)

    trusted_current_state.epoch_seconds = evidence.request.expires_at_epoch_seconds
    with pytest.raises(ExpertCorpusPromotionError) as expired:
        registry.require_current(evidence, evidence_payload_sha256=identity)
    assert expired.value.kind is ExpertCorpusFailureKind.STALE
    assert expired.value.disposition is ExpertCorpusDisposition.NEEDS_REVIEW
    assert _current_state_event_kinds(registry) == ("promoted", "stale")

    reopened = SqliteExpertCorpusCurrentState(
        registry_root=root,
        mode=SqliteExpertCorpusCurrentStateOpenMode.OPEN_EXISTING,
        busy_timeout_milliseconds=5_000,
        lock_timeout_milliseconds=5_000,
        trusted_current_state=trusted_current_state,
    )
    with pytest.raises(ExpertCorpusPromotionError) as still_stale:
        reopened.require_current(evidence, evidence_payload_sha256=identity)
    assert still_stale.value.kind is ExpertCorpusFailureKind.STALE
    assert _current_state_event_kinds(reopened) == ("promoted", "stale")


def test_revocation_is_permanent_and_survives_restart(tmp_path: Path) -> None:
    root = tmp_path / "expert-corpus-state"
    root.mkdir()
    trusted_current_state = _TrustedCurrentState()
    registry = _persistent_registry(root, trusted_current_state)
    evidence = _signed_evidence()
    identity = canonical_expert_corpus_evidence_sha256(evidence)
    registry.record_promotion(evidence, evidence_payload_sha256=identity)
    registry.record_revocation(
        evidence,
        evidence_payload_sha256=identity,
        reason_code="external_authority_revoked",
    )

    with pytest.raises(ExpertCorpusPromotionError) as revoked:
        registry.require_current(evidence, evidence_payload_sha256=identity)
    assert revoked.value.kind is ExpertCorpusFailureKind.REVOKED
    assert revoked.value.disposition is ExpertCorpusDisposition.QUARANTINE
    assert revoked.value.reason_code == "external_authority_revoked"
    assert _current_state_event_kinds(registry) == ("promoted", "revoked")
    assert not hasattr(registry, "unrevoke")
    assert not hasattr(registry, "unquarantine")

    reopened = SqliteExpertCorpusCurrentState(
        registry_root=root,
        mode=SqliteExpertCorpusCurrentStateOpenMode.OPEN_EXISTING,
        busy_timeout_milliseconds=5_000,
        lock_timeout_milliseconds=5_000,
        trusted_current_state=trusted_current_state,
    )
    with pytest.raises(ExpertCorpusPromotionError) as restart_revocation:
        reopened.record_promotion(evidence, evidence_payload_sha256=identity)
    assert restart_revocation.value.kind is ExpertCorpusFailureKind.REVOKED


def test_trusted_current_state_unavailability_and_rollback_fail_closed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "expert-corpus-state"
    root.mkdir()
    trusted_current_state = _TrustedCurrentState()
    registry = _persistent_registry(root, trusted_current_state)
    evidence = _signed_evidence()
    identity = canonical_expert_corpus_evidence_sha256(evidence)
    registry.record_promotion(evidence, evidence_payload_sha256=identity)

    trusted_current_state.available = False
    with pytest.raises(ExpertCorpusPromotionError) as unavailable:
        registry.require_current(evidence, evidence_payload_sha256=identity)
    assert unavailable.value.kind is ExpertCorpusFailureKind.UNAVAILABLE

    trusted_current_state.available = True
    trusted_current_state.epoch_seconds = 99
    with pytest.raises(ExpertCorpusPromotionError) as time_rollback:
        registry.require_current(evidence, evidence_payload_sha256=identity)
    assert time_rollback.value.kind is ExpertCorpusFailureKind.UNAVAILABLE

    trusted_current_state.epoch_seconds = 100
    trusted_current_state.sequence = 0
    with pytest.raises(ExpertCorpusPromotionError) as sequence_rollback:
        registry.require_current(evidence, evidence_payload_sha256=identity)
    assert sequence_rollback.value.kind is ExpertCorpusFailureKind.UNAVAILABLE


def test_initially_expired_promotion_is_never_recorded(tmp_path: Path) -> None:
    root = tmp_path / "expert-corpus-state"
    root.mkdir()
    evidence = _signed_evidence()
    trusted_current_state = _TrustedCurrentState(
        epoch_seconds=evidence.request.expires_at_epoch_seconds
    )
    registry = _persistent_registry(root, trusted_current_state)
    identity = canonical_expert_corpus_evidence_sha256(evidence)

    with pytest.raises(ExpertCorpusPromotionError) as stale:
        registry.record_promotion(evidence, evidence_payload_sha256=identity)
    assert stale.value.kind is ExpertCorpusFailureKind.STALE
    assert _current_state_event_kinds(registry) == ()


def test_persistent_current_state_requires_explicit_trusted_observer(
    tmp_path: Path,
) -> None:
    root = tmp_path / "expert-corpus-state"
    root.mkdir()
    with pytest.raises(TypeError, match="trusted_current_state"):
        SqliteExpertCorpusCurrentState(
            registry_root=root,
            mode=SqliteExpertCorpusCurrentStateOpenMode.INITIALIZE,
            busy_timeout_milliseconds=5_000,
            lock_timeout_milliseconds=5_000,
            trusted_current_state=object(),  # type: ignore[arg-type]
        )


def test_persistent_current_state_rejects_conflict_and_wrong_identity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "expert-corpus-state"
    root.mkdir()
    request = _promotion_request()
    evidence = AuthenticatedVeterinaryExpertCorpus.model_validate(
        _signed_evidence(request)
    )
    registry = _persistent_registry(root, _TrustedCurrentState())
    identity = canonical_expert_corpus_evidence_sha256(evidence)
    registry.record_promotion(evidence, evidence_payload_sha256=identity)

    changed_request = request.model_copy(update={"authority_sequence": 2})
    changed_evidence = AuthenticatedVeterinaryExpertCorpus.model_validate(
        _signed_evidence(changed_request)
    )
    changed_identity = canonical_expert_corpus_evidence_sha256(changed_evidence)
    with pytest.raises(ExpertCorpusPromotionError) as conflict:
        registry.record_promotion(
            changed_evidence,
            evidence_payload_sha256=changed_identity,
        )
    assert conflict.value.kind is ExpertCorpusFailureKind.CONFLICTING

    foreign_authority = _authority(
        _CurrentCorpusState(),
        authority_id="foreign-corpus-authority",
        key_label="foreign",
        stored_corpus_evidence=_SyntheticStoredCorpusEvidence(request),
    )
    foreign_evidence = foreign_authority.require_current(
        foreign_authority.issue(request)
    )
    with pytest.raises(ExpertCorpusPromotionError) as cross_trust_conflict:
        registry.record_promotion(
            foreign_evidence,
            evidence_payload_sha256=canonical_expert_corpus_evidence_sha256(
                foreign_evidence
            ),
        )
    assert cross_trust_conflict.value.kind is ExpertCorpusFailureKind.CONFLICTING

    with pytest.raises(ExpertCorpusPromotionError) as substituted:
        registry.require_current(
            evidence,
            evidence_payload_sha256=_digest("wrong-persisted-identity"),
        )
    assert substituted.value.kind is ExpertCorpusFailureKind.SUBSTITUTED


def test_persistent_current_state_is_process_safe(tmp_path: Path) -> None:
    root = tmp_path / "expert-corpus-state"
    root.mkdir()
    _persistent_registry(root, _ProcessTrustedCurrentState())
    evidence_items: list[AuthenticatedVeterinaryExpertCorpus] = []
    for index in range(4):
        request = _promotion_request()
        manifest = request.corpus_manifest.model_copy(
            update={
                "corpus_id": f"synthetic-corpus-{index}",
                "corpus_version": f"synthetic-version-{index}",
            }
        )
        request = request.model_copy(
            update={
                "promotion_id": f"synthetic-promotion-{index}",
                "corpus_manifest": manifest,
                "authority_sequence": index + 1,
            }
        )
        evidence_items.append(
            AuthenticatedVeterinaryExpertCorpus.model_validate(
                _signed_evidence(request)
            )
        )
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(
            target=_record_promotion_in_process,
            args=(
                str(root),
                evidence.model_dump_json(),
                canonical_expert_corpus_evidence_sha256(evidence),
            ),
        )
        for evidence in evidence_items
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    reopened = SqliteExpertCorpusCurrentState(
        registry_root=root,
        mode=SqliteExpertCorpusCurrentStateOpenMode.OPEN_EXISTING,
        busy_timeout_milliseconds=5_000,
        lock_timeout_milliseconds=5_000,
        trusted_current_state=_ProcessTrustedCurrentState(),
    )
    for evidence in evidence_items:
        reopened.require_current(
            evidence,
            evidence_payload_sha256=canonical_expert_corpus_evidence_sha256(evidence),
        )


@pytest.mark.parametrize("damage", ("corruption", "truncation", "rollback"))
def test_persistent_current_state_detects_storage_damage(
    tmp_path: Path,
    damage: str,
) -> None:
    root = tmp_path / damage
    root.mkdir()
    trusted_current_state = _TrustedCurrentState()
    registry = _persistent_registry(root, trusted_current_state)
    evidence = AuthenticatedVeterinaryExpertCorpus.model_validate(_signed_evidence())
    identity = canonical_expert_corpus_evidence_sha256(evidence)
    original_database = registry.database_path.read_bytes()
    registry.record_promotion(evidence, evidence_payload_sha256=identity)

    if damage == "corruption":
        payload = bytearray(registry.database_path.read_bytes())
        payload[:32] = b"x" * 32
        registry.database_path.write_bytes(payload)
    elif damage == "truncation":
        registry.database_path.write_bytes(registry.database_path.read_bytes()[:128])
    else:
        registry.database_path.write_bytes(original_database)

    with pytest.raises(ExpertCorpusPromotionError) as rejected:
        SqliteExpertCorpusCurrentState(
            registry_root=root,
            mode=SqliteExpertCorpusCurrentStateOpenMode.OPEN_EXISTING,
            busy_timeout_milliseconds=5_000,
            lock_timeout_milliseconds=5_000,
            trusted_current_state=trusted_current_state,
        )
    assert rejected.value.kind is ExpertCorpusFailureKind.UNAVAILABLE


def test_persistent_current_state_rejects_symlink_and_inode_substitution(
    tmp_path: Path,
) -> None:
    root = tmp_path / "expert-corpus-state"
    root.mkdir()
    trusted_current_state = _TrustedCurrentState()
    registry = _persistent_registry(root, trusted_current_state)
    symlink = tmp_path / "expert-corpus-state-link"
    symlink.symlink_to(root, target_is_directory=True)
    with pytest.raises((ValueError, ExpertCorpusPromotionError)):
        SqliteExpertCorpusCurrentState(
            registry_root=symlink,
            mode=SqliteExpertCorpusCurrentStateOpenMode.OPEN_EXISTING,
            busy_timeout_milliseconds=5_000,
            lock_timeout_milliseconds=5_000,
            trusted_current_state=trusted_current_state,
        )

    replacement = tmp_path / "replacement.sqlite3"
    replacement.write_bytes(registry.database_path.read_bytes())
    replacement.replace(registry.database_path)
    evidence = _signed_evidence()
    with pytest.raises(ExpertCorpusPromotionError) as substituted:
        registry.require_current(
            evidence,
            evidence_payload_sha256=canonical_expert_corpus_evidence_sha256(evidence),
        )
    assert substituted.value.kind is ExpertCorpusFailureKind.UNAVAILABLE

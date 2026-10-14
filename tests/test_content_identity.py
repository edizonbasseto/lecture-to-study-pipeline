from __future__ import annotations

import hashlib
import json

import pytest

from lecture_study.domain.clause_reconciliation import (
    ClauseReconciliation,
    ClauseReconciliationOutcome,
    FacetAssessment,
    FacetOutcome,
    HumanReviewRequirement as ClauseHumanReviewRequirement,
    SemanticFacet,
)
from lecture_study.domain.content_ledger import (
    AssertionPolarity,
    AtomicContentLedger,
    AtomicContentType,
    AtomicProposition,
    CertaintyLevel,
    ContentDimension,
    ContentImportance,
    EvidenceExcerpt,
    ClauseUtf8ByteRange,
    NumericBindingComponentState,
    NumericImplicitJustification,
    NumericSourceBinding,
    NumericSourceComponentBinding,
    RecommendationStrength,
    ReviewStatus,
)
from lecture_study.domain.human_review import (
    HumanReviewDecision,
    HumanReviewEvidence,
    HumanReviewPairScope,
    HumanReviewScope,
    NumericReviewSubject,
)
from lecture_study.domain.semantic_observation import (
    CandidateBatch,
    CandidateClauseObservation,
    CandidateProducerIdentity,
    ClauseDisposition,
)
from lecture_study.domain.source_content import SourceRecordProvenance
from lecture_study.domain.source_universe import EvidenceModality
from lecture_study.quality.content_identity import (
    canonical_atomic_content_ledger_sha256,
    canonical_candidate_batches_sha256,
    canonical_human_review_evidence_sha256,
    canonical_human_review_payload_base_sha256,
    canonical_human_review_scope_sha256,
    canonical_reconciliation_set_sha256,
    canonical_source_binding_sha256,
    canonical_source_content_aggregation_sha256,
)
from lecture_study.source_content.manifest import (
    SOURCE_MANIFEST_CRYPTOGRAPHIC_DOMAIN,
    SOURCE_MANIFEST_SIGNATURE_PURPOSE,
    AuthenticatedSourceManifest,
    HmacSourceManifestCatalog,
    SourceArtifactManifestEntry,
    SourceManifestHmacAuthority,
    SourceManifestHmacKey,
    SourceRecordManifestEntry,
    VerifiedSourceManifest,
)
from lecture_study.source_content.resolver import (
    SourceContentLimits,
    SourceContentResolutionError,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _source_record(suffix: str, **changes: object) -> SourceRecordProvenance:
    values: dict[str, object] = {
        "schema_version": 1,
        "manifest_relative_path": "manifests/source.json",
        "manifest_sha256": _digest("manifest"),
        "manifest_signature_sha256": _digest("manifest-signature"),
        "trust_key_id": _digest("source-trust-key"),
        "lecture_id": "lecture-001",
        "source_artifact_id": f"artifact-{suffix}",
        "artifact_relative_path": f"source/{suffix}.jsonl",
        "artifact_sha256": _digest(f"artifact-{suffix}"),
        "artifact_byte_size": 1_024,
        "source_record_id": f"record-{suffix}",
        "modality": EvidenceModality.TRANSCRIPT,
        "byte_offset": 0,
        "byte_length": 128,
        "content_sha256": _digest(f"record-content-{suffix}"),
        "media_type": "application/jsonl",
    }
    values.update(changes)
    return SourceRecordProvenance.model_validate(values)


class _ManifestReader:
    def __init__(self, *, relative_path: str, payload: bytes) -> None:
        self._relative_path = relative_path
        self._payload = payload

    def read_file(self, relative_path: str, *, maximum_bytes: int) -> bytes:
        if relative_path != self._relative_path:
            raise ValueError("unexpected manifest path")
        if len(self._payload) > maximum_bytes:
            raise ValueError("manifest exceeds test limit")
        return self._payload


def _verified_source_set(
    *declared_records: SourceRecordProvenance,
    key_material: bytes = b"unit-test-only-not-a-secret-value",
    manifest_id: str = "source-manifest-001",
) -> tuple[VerifiedSourceManifest, tuple[SourceRecordProvenance, ...]]:
    if not declared_records:
        raise ValueError("test manifest requires at least one record")
    manifest_relative_path = declared_records[0].manifest_relative_path
    lecture_id = str(declared_records[0].lecture_id)
    artifacts = tuple(
        SourceArtifactManifestEntry(
            artifact_id=record.source_artifact_id,
            relative_path=record.artifact_relative_path,
            artifact_sha256=record.artifact_sha256,
            byte_size=record.artifact_byte_size,
            records=(
                SourceRecordManifestEntry(
                    record_id=record.source_record_id,
                    modality=record.modality,
                    byte_offset=record.byte_offset,
                    byte_length=record.byte_length,
                    content_sha256=record.content_sha256,
                    media_type=record.media_type,
                ),
            ),
        )
        for record in declared_records
    )
    authority = SourceManifestHmacAuthority(SourceManifestHmacKey(key_material))
    unsigned = AuthenticatedSourceManifest(
        schema_version=1,
        manifest_id=manifest_id,
        manifest_relative_path=manifest_relative_path,
        lecture_id=lecture_id,
        artifacts=artifacts,
        cryptographic_domain=SOURCE_MANIFEST_CRYPTOGRAPHIC_DOMAIN,
        signature_purpose=SOURCE_MANIFEST_SIGNATURE_PURPOSE,
        trust_key_id=authority.key_id,
        signature_sha256="0" * 64,
    )
    manifest = unsigned.model_copy(
        update={"signature_sha256": authority.sign(unsigned.authentication_payload())}
    )
    payload = json.dumps(
        manifest.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    catalog = HmacSourceManifestCatalog(
        artifact_reader=_ManifestReader(
            relative_path=manifest_relative_path,
            payload=payload,
        ),
        expected_manifest_id=manifest_id,
        manifest_relative_path=manifest_relative_path,
        authority=authority,
        limits=SourceContentLimits(
            maximum_manifest_bytes=100_000,
            maximum_artifact_bytes=100_000,
            maximum_record_bytes=100_000,
            maximum_artifacts=100,
            maximum_records_per_artifact=100,
        ),
    )
    capability = catalog.verify_manifest(lecture_id=lecture_id)
    return capability, capability.complete_source_records()


def _candidate_batch(suffix: str) -> CandidateBatch:
    producer = CandidateProducerIdentity(
        producer_id=f"producer-{suffix}",
        manifest_sha256=_digest(f"manifest-{suffix}"),
        model_sha256=_digest(f"model-{suffix}"),
        prompt_sha256=_digest(f"prompt-{suffix}"),
        schema_sha256=_digest(f"schema-{suffix}"),
        implementation_sha256=_digest(f"implementation-{suffix}"),
        configuration_sha256=_digest(f"configuration-{suffix}"),
        engine_version="test-engine-1",
    )
    return CandidateBatch(
        schema_version=1,
        batch_id=f"batch-{suffix}",
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        source_universe_sha256=_digest("source-universe"),
        clause_inventory_sha256=_digest("clause-inventory"),
        producer=producer,
        observations=(
            CandidateClauseObservation(
                observation_id=f"observation-{suffix}",
                sequence=1,
                clause_id="clause-001",
                producer_id=producer.producer_id,
                disposition=ClauseDisposition.NON_PROPOSITIONAL,
                extraction_confidence=1.0,
            ),
        ),
    )


def _atomic_ledger(
    statement: str = "The finding has educational value.",
) -> AtomicContentLedger:
    proposition = AtomicProposition(
        schema_version=1,
        proposition_id="proposition-001",
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        sequence=1,
        content_type=AtomicContentType.DEFINITION,
        importance=ContentImportance.CORE,
        normalized_statement=statement,
        source_span_ids=("span-001",),
        evidence_excerpts=(
            EvidenceExcerpt(
                source_clause_id="clause-001",
                source_span_id="span-001",
                modality=EvidenceModality.TRANSCRIPT,
                source_content_sha256=_digest("source-record"),
                selector={
                    "kind": "text-range",
                    "start_byte": 0,
                    "end_byte": len(statement.encode("utf-8")),
                    "selected_sha256": _digest(statement),
                },
                verbatim_text=statement,
                text_sha256=_digest(statement),
            ),
        ),
        required_dimensions=frozenset({ContentDimension.CORE_STATEMENT}),
        polarity=AssertionPolarity.AFFIRMED,
        certainty=CertaintyLevel.CERTAIN,
        recommendation_strength=RecommendationStrength.NOT_APPLICABLE,
        safety_critical=False,
        extraction_confidence=1.0,
        review_status=ReviewStatus.VERIFIED,
    )
    return AtomicContentLedger(
        schema_version=1,
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        source_universe_sha256=_digest("source-universe"),
        propositions=(proposition,),
    )


def _reconciliation(suffix: str) -> ClauseReconciliation:
    return ClauseReconciliation(
        schema_version=1,
        reconciliation_id=f"reconciliation-{suffix}",
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        clause_id=f"clause-{suffix}",
        candidate_observation_ids=(
            f"observation-a-{suffix}",
            f"observation-b-{suffix}",
        ),
        source_proposition_count=0,
        facet_assessments=(
            FacetAssessment(
                facet=SemanticFacet.DISPOSITION,
                outcome=FacetOutcome.MATCHED,
            ),
        ),
        outcome=ClauseReconciliationOutcome.REPRESENTED,
        human_review=ClauseHumanReviewRequirement(required=False),
    )


def test_candidate_batch_identity_is_permutation_stable() -> None:
    first = _candidate_batch("a")
    second = _candidate_batch("b")

    assert canonical_candidate_batches_sha256((first, second)) == (
        canonical_candidate_batches_sha256((second, first))
    )


def test_candidate_batch_identity_rejects_duplicate_batch_ids() -> None:
    batch = _candidate_batch("a")

    with pytest.raises(ValueError, match="duplicate candidate batch"):
        canonical_candidate_batches_sha256((batch, batch))


def test_candidate_batch_identity_rejects_empty_or_unchecked_sets() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        canonical_candidate_batches_sha256(())

    unchecked = _candidate_batch("a").model_copy(update={"observations": ()})
    with pytest.raises(ValueError):
        canonical_candidate_batches_sha256((unchecked,))


def test_candidate_batch_omission_and_substitution_change_identity() -> None:
    first = _candidate_batch("a")
    second = _candidate_batch("b")
    substitute = _candidate_batch("c")

    complete = canonical_candidate_batches_sha256((first, second))

    assert complete != canonical_candidate_batches_sha256((first,))
    assert complete != canonical_candidate_batches_sha256((first, substitute))


def test_atomic_ledger_substitution_changes_identity() -> None:
    original = _atomic_ledger()
    substitute = _atomic_ledger("A substituted educational statement.")

    assert canonical_atomic_content_ledger_sha256(original) != (
        canonical_atomic_content_ledger_sha256(substitute)
    )


def test_reconciliation_identity_is_permutation_stable() -> None:
    first = _reconciliation("a")
    second = _reconciliation("b")

    assert canonical_reconciliation_set_sha256((first, second)) == (
        canonical_reconciliation_set_sha256((second, first))
    )


def test_reconciliation_identity_rejects_duplicate_reconciliation_ids() -> None:
    reconciliation = _reconciliation("a")

    with pytest.raises(ValueError, match="duplicate reconciliation"):
        canonical_reconciliation_set_sha256((reconciliation, reconciliation))


def test_reconciliation_identity_rejects_empty_or_unchecked_sets() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        canonical_reconciliation_set_sha256(())

    unchecked = _reconciliation("a").model_copy(
        update={
            "candidate_observation_ids": (
                "observation-a",
                "observation-a",
            )
        }
    )
    with pytest.raises(ValueError):
        canonical_reconciliation_set_sha256((unchecked,))


def test_reconciliation_omission_and_substitution_change_identity() -> None:
    first = _reconciliation("a")
    second = _reconciliation("b")
    substitute = _reconciliation("c")

    complete = canonical_reconciliation_set_sha256((first, second))

    assert complete != canonical_reconciliation_set_sha256((first,))
    assert complete != canonical_reconciliation_set_sha256((first, substitute))


def test_content_identity_domains_cannot_substitute_for_each_other() -> None:
    manifest, source_records = _verified_source_set(_source_record("a"))
    source_identity = canonical_source_content_aggregation_sha256(
        "lecture-001",
        manifest,
        source_records,
    )
    candidate_identity = canonical_candidate_batches_sha256((_candidate_batch("a"),))
    ledger_identity = canonical_atomic_content_ledger_sha256(_atomic_ledger())
    reconciliation_identity = canonical_reconciliation_set_sha256(
        (_reconciliation("a"),)
    )

    assert (
        len(
            {
                source_identity,
                candidate_identity,
                ledger_identity,
                reconciliation_identity,
            }
        )
        == 4
    )


def test_source_content_aggregation_identity_is_permutation_stable() -> None:
    manifest, records = _verified_source_set(
        _source_record("a"),
        _source_record("b"),
    )
    first, second = records

    assert canonical_source_content_aggregation_sha256(
        "lecture-001",
        manifest,
        (first, second),
    ) == canonical_source_content_aggregation_sha256(
        "lecture-001",
        manifest,
        (second, first),
    )


def test_source_content_aggregation_uses_documented_canonical_json() -> None:
    manifest, records = _verified_source_set(
        _source_record("b", byte_offset=128),
        _source_record("a"),
    )
    ordered = tuple(
        sorted(
            records,
            key=lambda record: (
                str(record.source_artifact_id),
                record.artifact_relative_path,
                str(record.source_record_id),
                record.byte_offset,
                record.byte_length,
                record.content_sha256,
            ),
        )
    )
    canonical_records = [
        {
            "lecture_id": str(record.lecture_id),
            "source_artifact_id": str(record.source_artifact_id),
            "artifact_relative_path": record.artifact_relative_path,
            "source_record_id": str(record.source_record_id),
            "modality": record.modality.value,
            "byte_offset": record.byte_offset,
            "byte_length": record.byte_length,
            "content_sha256": record.content_sha256,
        }
        for record in ordered
    ]
    canonical_bytes = json.dumps(
        {
            "domain": "lecture-study/source-content-aggregation/v1",
            "payload": canonical_records,
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    assert (
        canonical_source_content_aggregation_sha256(
            "lecture-001",
            manifest,
            tuple(reversed(records)),
        )
        == hashlib.sha256(canonical_bytes).hexdigest()
    )


def test_source_content_aggregation_rejects_duplicate_record_identities() -> None:
    manifest, records = _verified_source_set(_source_record("a"))
    record = records[0]

    with pytest.raises(ValueError, match="duplicate source record"):
        canonical_source_content_aggregation_sha256(
            "lecture-001",
            manifest,
            (record, record),
        )


def test_source_content_aggregation_rejects_empty_or_wrong_lecture_records() -> None:
    manifest, records = _verified_source_set(_source_record("a"))

    with pytest.raises(ValueError, match="must not be empty"):
        canonical_source_content_aggregation_sha256(
            "lecture-001",
            manifest,
            (),
        )

    with pytest.raises(ValueError, match="lecture"):
        canonical_source_content_aggregation_sha256(
            "lecture-001",
            manifest,
            (records[0].model_copy(update={"lecture_id": "lecture-002"}),),
        )


def test_source_content_omission_and_substitution_change_identity() -> None:
    manifest, records = _verified_source_set(
        _source_record("a"),
        _source_record("b"),
    )
    first, second = records
    substitute = _source_record("c")

    complete = canonical_source_content_aggregation_sha256(
        "lecture-001",
        manifest,
        (first, second),
    )

    with pytest.raises(ValueError, match="complete authenticated manifest"):
        canonical_source_content_aggregation_sha256(
            "lecture-001",
            manifest,
            (first,),
        )
    with pytest.raises(ValueError, match="complete authenticated manifest"):
        canonical_source_content_aggregation_sha256(
            "lecture-001",
            manifest,
            (first, substitute),
        )

    substitute_manifest, substitute_records = _verified_source_set(
        _source_record("a"),
        substitute,
        manifest_id="source-manifest-substituted",
    )
    assert complete != canonical_source_content_aggregation_sha256(
        "lecture-001",
        substitute_manifest,
        substitute_records,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"source_artifact_id": "artifact-substituted"},
        {"artifact_relative_path": "source/substituted.jsonl"},
        {"source_record_id": "record-substituted"},
        {"modality": EvidenceModality.OCR},
        {"byte_offset": 1},
        {"byte_length": 127},
        {"content_sha256": _digest("substituted-record-content")},
    ],
)
def test_source_content_bound_field_changes_identity(
    changes: dict[str, object],
) -> None:
    original_manifest, original = _verified_source_set(_source_record("a"))
    changed_manifest, changed = _verified_source_set(
        _source_record("a", **changes),
        manifest_id="source-manifest-changed",
    )

    assert canonical_source_content_aggregation_sha256(
        "lecture-001",
        original_manifest,
        original,
    ) != canonical_source_content_aggregation_sha256(
        "lecture-001",
        changed_manifest,
        changed,
    )


def test_source_content_excludes_trust_manifest_and_whole_file_fields() -> None:
    original_manifest, original = _verified_source_set(_source_record("a"))
    rotated_manifest, rotated_authority = _verified_source_set(
        _source_record(
            "a",
            manifest_relative_path="manifests/rotated.json",
            manifest_sha256=_digest("rotated-manifest"),
            manifest_signature_sha256=_digest("rotated-signature"),
            trust_key_id=_digest("rotated-trust-key"),
            artifact_sha256=_digest("rewritten-whole-artifact"),
            artifact_byte_size=2_048,
            media_type="application/x-ndjson",
        ),
        key_material=b"second-unit-test-only-not-a-secret-value",
        manifest_id="source-manifest-rotated",
    )

    assert canonical_source_content_aggregation_sha256(
        "lecture-001",
        original_manifest,
        original,
    ) == canonical_source_content_aggregation_sha256(
        "lecture-001",
        rotated_manifest,
        rotated_authority,
    )


def test_reviewed_subset_cannot_satisfy_full_source_content_identity() -> None:
    manifest, records = _verified_source_set(
        _source_record("a"),
        _source_record("b"),
    )
    reviewed_record, unreviewed_record = records

    full_lecture_identity = canonical_source_content_aggregation_sha256(
        "lecture-001",
        manifest,
        (reviewed_record, unreviewed_record),
    )

    assert len(full_lecture_identity) == 64
    with pytest.raises(ValueError, match="complete authenticated manifest"):
        canonical_source_content_aggregation_sha256(
            "lecture-001",
            manifest,
            (reviewed_record,),
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"manifest_relative_path": "manifests/substituted.json"},
        {"manifest_sha256": _digest("substituted-manifest")},
        {"manifest_signature_sha256": _digest("substituted-signature")},
        {"trust_key_id": _digest("substituted-key")},
        {"source_artifact_id": "artifact-substituted"},
        {"artifact_relative_path": "source/substituted.jsonl"},
        {"artifact_sha256": _digest("substituted-artifact")},
        {"artifact_byte_size": 2_048},
        {"source_record_id": "record-substituted"},
        {"modality": EvidenceModality.OCR},
        {"byte_offset": 1},
        {"byte_length": 127},
        {"content_sha256": _digest("substituted-content")},
        {"media_type": "application/x-ndjson"},
    ],
)
def test_source_content_aggregation_rejects_supplied_record_substitution(
    changes: dict[str, object],
) -> None:
    manifest, records = _verified_source_set(_source_record("a"))
    substituted = records[0].model_copy(update=changes)

    with pytest.raises(ValueError, match="authenticated manifest"):
        canonical_source_content_aggregation_sha256(
            "lecture-001",
            manifest,
            (substituted,),
        )


def test_source_content_aggregation_rejects_additional_record() -> None:
    manifest, records = _verified_source_set(_source_record("a"))

    with pytest.raises(ValueError, match="complete authenticated manifest"):
        canonical_source_content_aggregation_sha256(
            "lecture-001",
            manifest,
            (*records, _source_record("additional")),
        )


def test_source_content_aggregation_rejects_records_from_another_manifest() -> None:
    manifest, _ = _verified_source_set(_source_record("a"))
    _, other_records = _verified_source_set(
        _source_record("a"),
        key_material=b"second-unit-test-only-not-a-secret-value",
        manifest_id="source-manifest-other",
    )

    with pytest.raises(ValueError, match="substituted"):
        canonical_source_content_aggregation_sha256(
            "lecture-001",
            manifest,
            other_records,
        )


def test_source_content_aggregation_rejects_forged_manifest_capability() -> None:
    forged = object.__new__(VerifiedSourceManifest)

    with pytest.raises(
        SourceContentResolutionError,
        match="not authentically issued",
    ):
        canonical_source_content_aggregation_sha256(
            "lecture-001",
            forged,
            (_source_record("a"),),
        )


def _explicit_component(
    start_byte: int, end_byte: int
) -> NumericSourceComponentBinding:
    return NumericSourceComponentBinding(
        state=NumericBindingComponentState.EXPLICIT,
        selector=ClauseUtf8ByteRange(start_byte=start_byte, end_byte=end_byte),
    )


def _implicit_component(
    label: str,
    *,
    source_clause_id: str = "clause-001",
) -> NumericSourceComponentBinding:
    return NumericSourceComponentBinding(
        state=NumericBindingComponentState.IMPLICIT,
        implicit_justification=NumericImplicitJustification(
            justification_id=f"justification-{label}",
            source_clause_id=source_clause_id,
            rationale=f"Traceable rationale for {label}.",
        ),
    )


def _absent_component() -> NumericSourceComponentBinding:
    return NumericSourceComponentBinding(state=NumericBindingComponentState.ABSENT)


def _numeric_source_binding(**changes: object) -> NumericSourceBinding:
    source_clause_id = str(changes.get("source_clause_id", "clause-001"))
    values: dict[str, object] = {
        "source_clause_id": source_clause_id,
        "exact_expression": "[10,20) mg dose",
        "expression_selector": _explicit_component(0, 15),
        "lower_value_selector": _explicit_component(1, 3),
        "upper_value_selector": _explicit_component(4, 6),
        "comparator_selector": _explicit_component(3, 4),
        "unit_selector": _explicit_component(8, 10),
        "denominator_selector": _implicit_component(
            "denominator",
            source_clause_id=source_clause_id,
        ),
        "semantic_role_selector": _explicit_component(11, 15),
        "lower_boundary_selector": _explicit_component(0, 1),
        "upper_boundary_selector": _explicit_component(6, 7),
    }
    values.update(changes)
    return NumericSourceBinding.model_validate(values)


def _numeric_subject(suffix: str, **changes: object) -> NumericReviewSubject:
    values: dict[str, object] = {
        "proposition_id": f"proposition-{suffix}",
        "quantity_id": f"quantity-{suffix}",
        "source_clause_id": f"clause-{suffix}",
        "component": "denominator",
        "source_binding_sha256": _digest(f"source-binding-{suffix}"),
        "reason_code": "implicit-component-requires-review",
    }
    values.update(changes)
    return NumericReviewSubject.model_validate(values)


def _review_scope(
    *,
    review_pairs: tuple[HumanReviewPairScope, ...] = (),
    numeric_review_subjects: tuple[NumericReviewSubject, ...] = (),
) -> HumanReviewScope:
    return HumanReviewScope(
        lecture_id="lecture-001",
        clause_id="clause-001",
        review_pairs=review_pairs,
        numeric_review_subjects=numeric_review_subjects,
        source_content_aggregation_sha256=_digest("source-content"),
        clause_inventory_sha256=_digest("clause-inventory"),
        candidate_batches_sha256=_digest("candidate-batches"),
        atomic_ledger_sha256=_digest("atomic-ledger"),
        reconciliations_sha256=_digest("reconciliations"),
    )


def _review_evidence(scope: HumanReviewScope) -> HumanReviewEvidence:
    return HumanReviewEvidence(
        schema_version=1,
        scope=scope,
        decision=HumanReviewDecision.APPROVED,
        policy_sha256=_digest("policy"),
        calibration_sha256=_digest("calibration"),
        cryptographic_domain="lecture-study.human-review.v1",
        signature_purpose="human-review-scope-decision",
        authority_id="external-review-authority",
        authority_key_id=_digest("human-review-key"),
        authority_sequence=1,
        review_version=1,
        issued_at_epoch_seconds=1_000,
        expires_at_epoch_seconds=2_000,
        detached_signature="detached-signature",
    )


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("source_clause_id", "clause-002"),
        ("exact_expression", "[10,20) mg dosage"),
        ("expression_selector", _explicit_component(0, 16)),
        ("lower_value_selector", _explicit_component(1, 2)),
        ("upper_value_selector", _explicit_component(4, 5)),
        ("comparator_selector", _explicit_component(7, 8)),
        ("unit_selector", _explicit_component(8, 9)),
        (
            "denominator_selector",
            _implicit_component("changed-denominator"),
        ),
        ("semantic_role_selector", _explicit_component(11, 14)),
        ("lower_boundary_selector", _explicit_component(7, 8)),
        ("upper_boundary_selector", _absent_component()),
    ),
)
def test_numeric_source_binding_every_field_changes_identity(
    field_name: str,
    replacement: object,
) -> None:
    original = _numeric_source_binding()
    changes = {field_name: replacement}
    if field_name == "source_clause_id":
        changes["denominator_selector"] = _implicit_component(
            "denominator",
            source_clause_id=str(replacement),
        )
    mutated = _numeric_source_binding(**changes)

    assert canonical_source_binding_sha256(mutated) != (
        canonical_source_binding_sha256(original)
    )


def test_numeric_source_binding_nested_state_selector_and_justification_change_identity() -> (
    None
):
    original = _numeric_source_binding()
    changed_state = _numeric_source_binding(
        upper_boundary_selector=_absent_component(),
    )
    changed_selector = _numeric_source_binding(
        upper_boundary_selector=_explicit_component(7, 8),
    )
    changed_justification_id = _numeric_source_binding(
        denominator_selector=NumericSourceComponentBinding(
            state=NumericBindingComponentState.IMPLICIT,
            implicit_justification=NumericImplicitJustification(
                justification_id="justification-denominator-v2",
                source_clause_id="clause-001",
                rationale="Traceable rationale for denominator.",
            ),
        )
    )
    changed_rationale = _numeric_source_binding(
        denominator_selector=NumericSourceComponentBinding(
            state=NumericBindingComponentState.IMPLICIT,
            implicit_justification=NumericImplicitJustification(
                justification_id="justification-denominator",
                source_clause_id="clause-001",
                rationale="Independent traceable rationale for denominator.",
            ),
        )
    )
    original_identity = canonical_source_binding_sha256(original)

    assert canonical_source_binding_sha256(changed_state) != original_identity
    assert canonical_source_binding_sha256(changed_selector) != original_identity
    assert (
        canonical_source_binding_sha256(changed_justification_id) != original_identity
    )
    assert canonical_source_binding_sha256(changed_rationale) != original_identity


def test_numeric_source_binding_identity_is_deterministic_and_domain_separated() -> (
    None
):
    binding = _numeric_source_binding()

    assert canonical_source_binding_sha256(binding) == canonical_source_binding_sha256(
        NumericSourceBinding.model_validate(
            binding.model_dump(mode="python", warnings=False)
        )
    )
    assert canonical_source_binding_sha256(binding) != (
        canonical_atomic_content_ledger_sha256(_atomic_ledger("a"))
    )


def test_numeric_source_binding_identity_revalidates_forged_copies() -> None:
    forged = _numeric_source_binding().model_copy(
        update={"expression_selector": _absent_component()}
    )

    with pytest.raises(ValueError, match="expression selector must be explicit"):
        canonical_source_binding_sha256(forged)


def test_human_review_scope_accepts_pair_numeric_and_mixed_subjects() -> None:
    pair = HumanReviewPairScope(
        disagreement_id="disagreement-a",
        proposition_id="proposition-a",
    )
    numeric_subject = _numeric_subject("a")

    assert _review_scope(review_pairs=(pair,)).review_pairs == (pair,)
    assert _review_scope(
        numeric_review_subjects=(numeric_subject,)
    ).numeric_review_subjects == (numeric_subject,)
    mixed = _review_scope(
        review_pairs=(pair,),
        numeric_review_subjects=(numeric_subject,),
    )
    assert mixed.review_pairs == (pair,)
    assert mixed.numeric_review_subjects == (numeric_subject,)


def test_human_review_scope_rejects_empty_subject_set() -> None:
    with pytest.raises(ValueError, match="at least one review subject"):
        _review_scope()


@pytest.mark.parametrize(
    "subjects",
    (
        (_numeric_subject("a"), _numeric_subject("a")),
        (_numeric_subject("b"), _numeric_subject("a")),
    ),
)
def test_human_review_scope_rejects_duplicate_or_noncanonical_numeric_subjects(
    subjects: tuple[NumericReviewSubject, ...],
) -> None:
    with pytest.raises(ValueError, match="numeric-review subjects"):
        _review_scope(numeric_review_subjects=subjects)


def test_numeric_review_subject_permutation_is_stable_after_canonical_ordering() -> (
    None
):
    subject_a = _numeric_subject("a")
    subject_b = _numeric_subject("b")

    def canonical_key(subject: NumericReviewSubject) -> tuple[str, ...]:
        return (
            str(subject.proposition_id),
            str(subject.quantity_id),
            str(subject.source_clause_id),
            str(subject.component),
            subject.source_binding_sha256,
            str(subject.reason_code),
        )

    first = _review_scope(numeric_review_subjects=(subject_a, subject_b))
    reordered = _review_scope(
        numeric_review_subjects=tuple(sorted((subject_b, subject_a), key=canonical_key))
    )

    assert first == reordered
    assert (
        _review_evidence(first).signed_payload()
        == _review_evidence(reordered).signed_payload()
    )


def test_numeric_review_subject_omission_and_substitution_change_signed_identity() -> (
    None
):
    subject_a = _numeric_subject("a")
    subject_b = _numeric_subject("b")
    full = _review_evidence(
        _review_scope(numeric_review_subjects=(subject_a, subject_b))
    ).signed_payload()
    omitted = _review_evidence(
        _review_scope(numeric_review_subjects=(subject_a,))
    ).signed_payload()
    substituted = _review_evidence(
        _review_scope(
            numeric_review_subjects=(
                subject_a,
                _numeric_subject("c", proposition_id="proposition-b"),
            )
        )
    ).signed_payload()

    assert omitted != full
    assert substituted != full


def test_human_review_signed_payload_binds_domain_purpose_and_key() -> None:
    evidence = _review_evidence(
        _review_scope(numeric_review_subjects=(_numeric_subject("a"),))
    )
    payload = json.loads(evidence.signed_payload())

    assert payload["cryptographic_domain"] == "lecture-study.human-review.v1"
    assert payload["signature_purpose"] == "human-review-scope-decision"
    assert payload["authority_key_id"] == _digest("human-review-key")


def test_human_review_quarantine_identity_excludes_only_detached_signature() -> None:
    evidence = _review_evidence(
        _review_scope(numeric_review_subjects=(_numeric_subject("a"),))
    )
    resigned = evidence.model_copy(update={"detached_signature": "signature-v2"})
    revised = evidence.model_copy(update={"review_version": 2})

    assert canonical_human_review_evidence_sha256(resigned) == (
        canonical_human_review_evidence_sha256(evidence)
    )
    assert canonical_human_review_evidence_sha256(revised) != (
        canonical_human_review_evidence_sha256(evidence)
    )


def test_human_review_quarantine_payload_base_excludes_review_version() -> None:
    evidence = _review_evidence(
        _review_scope(numeric_review_subjects=(_numeric_subject("a"),))
    )
    revised = evidence.model_copy(update={"review_version": 2})
    changed_policy = evidence.model_copy(update={"policy_sha256": _digest("f")})

    assert canonical_human_review_payload_base_sha256(revised) == (
        canonical_human_review_payload_base_sha256(evidence)
    )
    assert canonical_human_review_payload_base_sha256(changed_policy) != (
        canonical_human_review_payload_base_sha256(evidence)
    )
    assert canonical_human_review_scope_sha256(evidence.scope) == (
        canonical_human_review_scope_sha256(revised.scope)
    )

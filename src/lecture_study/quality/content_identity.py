"""Domain-separated canonical identities for content-quality artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from enum import Enum

from pydantic import TypeAdapter

from lecture_study.domain.clause_reconciliation import ClauseReconciliation
from lecture_study.domain.content_ledger import (
    AtomicContentLedger,
    NumericSourceBinding,
)
from lecture_study.domain.human_review import HumanReviewEvidence, HumanReviewScope
from lecture_study.domain.models import SafeIdentifier
from lecture_study.domain.semantic_observation import (
    CandidateBatch,
    SourceClauseInventory,
)
from lecture_study.domain.source_content import SourceRecordProvenance
from lecture_study.source_content.manifest import VerifiedSourceManifest

_LECTURE_ID_ADAPTER = TypeAdapter(SafeIdentifier)
_SOURCE_CONTENT_DOMAIN = "lecture-study/source-content-aggregation/v1"
_CANDIDATE_BATCH_DOMAIN = "lecture-study/candidate-batches/v1"
_ATOMIC_LEDGER_DOMAIN = "lecture-study/atomic-content-ledger/v1"
_NUMERIC_SOURCE_BINDING_DOMAIN = "lecture-study/numeric-source-binding/v1"
_RECONCILIATION_SET_DOMAIN = "lecture-study/reconciliation-set/v1"
_HUMAN_REVIEW_QUARANTINE_EVIDENCE_DOMAIN = (
    "lecture-study/human-review-quarantine-evidence/v1"
)
_HUMAN_REVIEW_QUARANTINE_SCOPE_DOMAIN = "lecture-study/human-review-quarantine-scope/v1"
_HUMAN_REVIEW_QUARANTINE_PAYLOAD_BASE_DOMAIN = (
    "lecture-study/human-review-quarantine-payload-base/v1"
)


def canonical_source_content_aggregation_sha256(
    lecture_id: str,
    verified_manifest: VerifiedSourceManifest,
    records: Sequence[SourceRecordProvenance],
) -> str:
    """Hash the exact complete record set from one authenticated lecture manifest."""

    expected_lecture_id = str(_LECTURE_ID_ADAPTER.validate_python(lecture_id))
    if type(verified_manifest) is not VerifiedSourceManifest:
        raise ValueError(
            "source content aggregation requires an issuer-authenticated manifest"
        )
    expected = tuple(
        _validate_source_record(record)
        for record in verified_manifest.complete_source_records()
    )
    validated = tuple(_validate_source_record(record) for record in records)
    _require_non_empty(validated, "source content aggregation")
    if any(str(record.lecture_id) != expected_lecture_id for record in validated):
        raise ValueError("source content aggregation contains a wrong-lecture record")
    if any(str(record.lecture_id) != expected_lecture_id for record in expected):
        raise ValueError("authenticated source manifest belongs to a different lecture")
    _require_unique(
        tuple(
            (str(record.source_artifact_id), str(record.source_record_id))
            for record in validated
        ),
        "duplicate source record identities",
    )
    _require_exact_manifest_record_set(expected, validated)
    ordered = sorted(expected, key=_source_record_sort_key)
    return _domain_separated_sha256(
        _SOURCE_CONTENT_DOMAIN,
        [_source_record_identity(record) for record in ordered],
    )


def _require_exact_manifest_record_set(
    expected: Sequence[SourceRecordProvenance],
    supplied: Sequence[SourceRecordProvenance],
) -> None:
    expected_by_identity = {
        (str(record.source_artifact_id), str(record.source_record_id)): record
        for record in expected
    }
    supplied_by_identity = {
        (str(record.source_artifact_id), str(record.source_record_id)): record
        for record in supplied
    }
    if expected_by_identity.keys() != supplied_by_identity.keys():
        raise ValueError(
            "supplied source records do not equal the complete authenticated "
            "manifest record set"
        )
    if any(
        supplied_by_identity[identity].model_dump(mode="json")
        != expected_record.model_dump(mode="json")
        for identity, expected_record in expected_by_identity.items()
    ):
        raise ValueError(
            "supplied source record is substituted from the authenticated manifest"
        )


def canonical_clause_inventory_sha256(inventory: SourceClauseInventory) -> str:
    """Hash one canonical clause inventory in its explicit identity domain."""

    validated = SourceClauseInventory.model_validate(
        inventory.model_dump(mode="python", warnings=False)
    )
    payload = json.dumps(
        validated.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    domain_prefix = b"lecture-study:clause-inventory:v1\0"
    return hashlib.sha256(domain_prefix + payload).hexdigest()


def canonical_candidate_batches_sha256(
    batches: Sequence[CandidateBatch],
) -> str:
    """Hash one exact unordered set of independently produced candidate batches."""

    validated = tuple(_validate_candidate_batch(batch) for batch in batches)
    _require_non_empty(validated, "candidate batch set")
    _require_unique(
        tuple(str(batch.batch_id) for batch in validated),
        "duplicate candidate batch identities",
    )
    ordered = sorted(
        validated,
        key=lambda batch: (
            str(batch.lecture_id),
            str(batch.learning_unit_id),
            str(batch.batch_id),
            str(batch.producer.producer_id),
        ),
    )
    return _domain_separated_sha256(
        _CANDIDATE_BATCH_DOMAIN,
        [batch.model_dump(mode="python", warnings=False) for batch in ordered],
    )


def canonical_atomic_content_ledger_sha256(
    ledger: AtomicContentLedger,
) -> str:
    """Hash one exact atomic ledger under its own identity domain."""

    validated = AtomicContentLedger.model_validate(
        ledger.model_dump(mode="python", warnings=False)
    )
    return _domain_separated_sha256(
        _ATOMIC_LEDGER_DOMAIN,
        validated.model_dump(mode="python", warnings=False),
    )


def canonical_source_binding_sha256(binding: NumericSourceBinding) -> str:
    """Hash one complete numeric source binding in its exclusive domain."""

    validated = NumericSourceBinding.model_validate(
        binding.model_dump(mode="python", warnings=False)
    )
    return _domain_separated_sha256(
        _NUMERIC_SOURCE_BINDING_DOMAIN,
        validated.model_dump(mode="python", warnings=False),
    )


def canonical_human_review_evidence_sha256(evidence: HumanReviewEvidence) -> str:
    """Hash every signed evidence field under the quarantine identity domain."""

    validated = HumanReviewEvidence.model_validate(
        evidence.model_dump(mode="python", warnings=False)
    )
    return _domain_separated_sha256(
        _HUMAN_REVIEW_QUARANTINE_EVIDENCE_DOMAIN,
        validated.model_dump(
            mode="python",
            exclude={"detached_signature"},
            warnings=False,
        ),
    )


def canonical_human_review_scope_sha256(scope: HumanReviewScope) -> str:
    """Hash one exact review scope for scope-local freshness comparison."""

    validated = HumanReviewScope.model_validate(
        scope.model_dump(mode="python", warnings=False)
    )
    return _domain_separated_sha256(
        _HUMAN_REVIEW_QUARANTINE_SCOPE_DOMAIN,
        validated.model_dump(mode="python", warnings=False),
    )


def canonical_human_review_payload_base_sha256(
    evidence: HumanReviewEvidence,
) -> str:
    """Hash evidence without signature or version to require a genuinely new base."""

    validated = HumanReviewEvidence.model_validate(
        evidence.model_dump(mode="python", warnings=False)
    )
    return _domain_separated_sha256(
        _HUMAN_REVIEW_QUARANTINE_PAYLOAD_BASE_DOMAIN,
        validated.model_dump(
            mode="python",
            exclude={"detached_signature", "review_version"},
            warnings=False,
        ),
    )


def canonical_reconciliation_set_sha256(
    reconciliations: Sequence[ClauseReconciliation],
) -> str:
    """Hash one exact unordered set of clause reconciliations."""

    validated = tuple(
        _validate_reconciliation(reconciliation) for reconciliation in reconciliations
    )
    _require_non_empty(validated, "reconciliation set")
    _require_unique(
        tuple(str(reconciliation.reconciliation_id) for reconciliation in validated),
        "duplicate reconciliation identities",
    )
    ordered = sorted(
        validated,
        key=lambda reconciliation: (
            str(reconciliation.lecture_id),
            str(reconciliation.learning_unit_id),
            str(reconciliation.clause_id),
            str(reconciliation.reconciliation_id),
        ),
    )
    return _domain_separated_sha256(
        _RECONCILIATION_SET_DOMAIN,
        [
            reconciliation.model_dump(mode="python", warnings=False)
            for reconciliation in ordered
        ],
    )


def _validate_source_record(
    record: SourceRecordProvenance,
) -> SourceRecordProvenance:
    return SourceRecordProvenance.model_validate(
        record.model_dump(mode="python", warnings=False)
    )


def _source_record_sort_key(
    record: SourceRecordProvenance,
) -> tuple[str, str, str, int, int, str]:
    return (
        str(record.source_artifact_id),
        record.artifact_relative_path,
        str(record.source_record_id),
        record.byte_offset,
        record.byte_length,
        record.content_sha256,
    )


def _source_record_identity(record: SourceRecordProvenance) -> dict[str, object]:
    return {
        "lecture_id": str(record.lecture_id),
        "source_artifact_id": str(record.source_artifact_id),
        "artifact_relative_path": record.artifact_relative_path,
        "source_record_id": str(record.source_record_id),
        "modality": record.modality.value,
        "byte_offset": record.byte_offset,
        "byte_length": record.byte_length,
        "content_sha256": record.content_sha256,
    }


def _validate_candidate_batch(batch: CandidateBatch) -> CandidateBatch:
    return CandidateBatch.model_validate(
        batch.model_dump(mode="python", warnings=False)
    )


def _validate_reconciliation(
    reconciliation: ClauseReconciliation,
) -> ClauseReconciliation:
    return ClauseReconciliation.model_validate(
        reconciliation.model_dump(mode="python", warnings=False)
    )


def _require_non_empty(values: Sequence[object], label: str) -> None:
    if not values:
        raise ValueError(f"{label} must not be empty")


def _require_unique(values: Sequence[object], message: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(message)


def _domain_separated_sha256(domain: str, payload: object) -> str:
    identity = {
        "domain": domain,
        "payload": _canonical_value(payload),
    }
    serialized = json.dumps(
        identity,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _canonical_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_canonical_value(item) for item in value]
        return sorted(normalized, key=_canonical_sort_key)
    if isinstance(value, Enum):
        return value.value
    return value


def _canonical_sort_key(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )

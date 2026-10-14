"""Deterministic structural completeness audit for atomic content ledgers."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from lecture_study.domain.content_ledger import (
    AtomicContentLedger,
    AtomicProposition,
    DisagreementStatus,
    EvidenceExcerpt,
    ReviewStatus,
)
from lecture_study.domain.semantic_observation import (
    SourceClause,
    SourceClauseInventory,
)
from lecture_study.domain.source_universe import (
    SourceEvidenceSpan,
    SourceSpanClassification,
    SourceUniverse,
)
from lecture_study.quality.evidence_auditor import (
    ContentQualityError,
    QualityFinding,
)
from lecture_study.quality.numeric_source_binding import (
    NumericBindingDisposition,
    NumericSourceBindingAuditor,
)
from lecture_study.quality.source_universe import SourceUniverseAuditor

_NUMBER_PATTERN = re.compile(r"(?<![\w.])-?(?:0|[1-9]\d*)(?:[.,]\d+)?(?:[eE][+-]?\d+)?")
_MAX_NUMERIC_TOKEN_LENGTH = 128


@dataclass(frozen=True, slots=True)
class AtomicContentLedgerAuditReport:
    """Recomputed structural coverage metrics for one lecture inventory."""

    lecture_id: str
    expected_learning_unit_count: int
    observed_learning_unit_count: int
    proposition_count: int
    instructional_source_span_count: int
    represented_instructional_source_span_count: int
    missing_instructional_source_span_ids: tuple[str, ...]
    invalid_source_reference_count: int
    source_content_hash_mismatch_count: int
    invalid_evidence_excerpt_count: int
    reused_source_clause_count: int
    context_only_proposition_count: int
    unstructured_numeric_proposition_count: int
    invalid_numeric_source_binding_count: int
    unverifiable_numeric_source_binding_count: int
    unverified_proposition_count: int
    unresolved_disagreement_count: int
    unauthenticated_resolution_count: int
    unresolved_relation_count: int
    findings: tuple[QualityFinding, ...]

    @property
    def structurally_complete(self) -> bool:
        return not self.findings

    @property
    def ready_for_atomic_content_gate(self) -> bool:
        """Remain false until semantic support and review evidence are authenticated."""

        return False

    @property
    def structural_source_span_recall(self) -> float:
        if self.instructional_source_span_count == 0:
            return 0.0
        return (
            self.represented_instructional_source_span_count
            / self.instructional_source_span_count
        )

    def require_structurally_complete(self) -> None:
        if self.structurally_complete:
            return
        codes = ", ".join(finding.code for finding in self.findings)
        raise ContentQualityError(
            f"Atomic content ledger failed structural completeness gates: {codes}"
        )


def canonical_source_universe_sha256(universe: SourceUniverse) -> str:
    """Hash the strict canonical JSON representation consumed by ledgers."""

    validated = SourceUniverse.model_validate(
        universe.model_dump(mode="python", warnings=False)
    )
    payload = json.dumps(
        validated.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class AtomicContentLedgerAuditor:
    """Require complete structural closure from source spans to propositions."""

    def audit(
        self,
        *,
        source_universe: SourceUniverse,
        clause_inventories: Sequence[SourceClauseInventory],
        ledgers: Sequence[AtomicContentLedger],
    ) -> AtomicContentLedgerAuditReport:
        source_universe = SourceUniverse.model_validate(
            source_universe.model_dump(mode="python", warnings=False)
        )
        validated_clause_inventories = tuple(
            SourceClauseInventory.model_validate(
                inventory.model_dump(mode="python", warnings=False)
            )
            for inventory in clause_inventories
        )
        validated_ledgers = tuple(
            AtomicContentLedger.model_validate(
                ledger.model_dump(mode="python", warnings=False)
            )
            for ledger in ledgers
        )
        findings: list[QualityFinding] = []
        source_report = SourceUniverseAuditor().audit(source_universe)
        if not source_report.passed:
            findings.append(
                QualityFinding(
                    code="source_universe_not_authoritative",
                    message=(
                        "Atomic ledger auditing requires a source universe that passes "
                        "all structural completeness gates."
                    ),
                )
            )

        units_by_id = {
            str(unit.unit_id): unit for unit in source_universe.learning_units
        }
        spans_by_id = {
            str(span.span_id): span for span in source_universe.evidence_spans
        }
        ledgers_by_unit = self._index_ledgers(validated_ledgers, findings)
        expected_unit_ids = set(units_by_id)
        observed_unit_ids = set(ledgers_by_unit)
        self._audit_unit_set(expected_unit_ids, observed_unit_ids, findings)

        expected_universe_sha256 = canonical_source_universe_sha256(source_universe)
        clauses_by_id = self._index_clause_inventories(
            validated_clause_inventories,
            expected_unit_ids=expected_unit_ids,
            lecture_id=str(source_universe.lecture_id),
            source_universe_sha256=expected_universe_sha256,
            findings=findings,
        )
        proposition_ids = [
            str(proposition.proposition_id)
            for ledger in validated_ledgers
            for proposition in ledger.propositions
        ]
        duplicate_proposition_ids = self._duplicates(proposition_ids)
        if duplicate_proposition_ids:
            findings.append(
                QualityFinding(
                    code="duplicate_atomic_proposition_ids",
                    message=(
                        f"{len(duplicate_proposition_ids)} atomic proposition IDs are "
                        "duplicated across the lecture."
                    ),
                )
            )

        represented_primary_span_ids: set[str] = set()
        invalid_source_reference_count = 0
        source_content_hash_mismatch_count = 0
        invalid_evidence_excerpt_count = 0
        evidence_excerpts_by_clause_id: dict[str, EvidenceExcerpt] = {}
        conflicting_reused_source_clause_ids: set[str] = set()
        context_only_count = 0
        unstructured_numeric_count = 0
        invalid_numeric_binding_count = 0
        unverifiable_numeric_binding_count = 0
        numeric_binding_auditor = NumericSourceBindingAuditor()
        all_numeric_quantities = tuple(
            quantity
            for ledger in validated_ledgers
            for proposition in ledger.propositions
            for quantity in proposition.numeric_quantities
        )
        overlapping_quantity_ids = numeric_binding_auditor.overlapping_quantity_ids(
            all_numeric_quantities
        )
        invalid_numeric_binding_count += len(overlapping_quantity_ids)
        unverified_count = 0
        unresolved_disagreement_count = 0
        unauthenticated_resolution_count = 0
        normalized_statements_by_unit: dict[str, list[str]] = {}

        for ledger in validated_ledgers:
            unit_id = str(ledger.learning_unit_id)
            unit = units_by_id.get(unit_id)
            if (
                ledger.lecture_id != source_universe.lecture_id
                or ledger.source_universe_sha256 != expected_universe_sha256
            ):
                findings.append(
                    QualityFinding(
                        code="atomic_ledger_identity_mismatch",
                        message=(
                            f"Ledger {unit_id} does not match the lecture or source-"
                            "universe identity."
                        ),
                    )
                )
            expected_sequences = tuple(range(1, len(ledger.propositions) + 1))
            observed_sequences = tuple(
                proposition.sequence for proposition in ledger.propositions
            )
            if observed_sequences != expected_sequences:
                findings.append(
                    QualityFinding(
                        code="atomic_proposition_sequence_mismatch",
                        message=(
                            f"Ledger {unit_id} expected proposition sequences "
                            f"{expected_sequences}, observed {observed_sequences}."
                        ),
                    )
                )

            primary_ids = (
                {str(span_id) for span_id in unit.primary_source_span_ids}
                if unit is not None
                else set()
            )
            allowed_ids = (
                primary_ids | {str(span_id) for span_id in unit.context_source_span_ids}
                if unit is not None
                else set()
            )
            normalized_statements_by_unit[unit_id] = []
            for proposition in ledger.propositions:
                if (
                    proposition.lecture_id != ledger.lecture_id
                    or str(proposition.learning_unit_id) != unit_id
                ):
                    findings.append(
                        QualityFinding(
                            code="atomic_proposition_identity_mismatch",
                            message=(
                                f"Proposition {proposition.proposition_id} does not "
                                "belong to its containing ledger."
                            ),
                        )
                    )
                source_ids = {str(span_id) for span_id in proposition.source_span_ids}
                if not source_ids & primary_ids:
                    context_only_count += 1
                for span_id in source_ids:
                    span = spans_by_id.get(span_id)
                    invalid = (
                        span is None
                        or span.classification
                        is not SourceSpanClassification.INSTRUCTIONAL
                        or span_id not in allowed_ids
                    )
                    if invalid:
                        invalid_source_reference_count += 1
                validly_bound_span_ids: set[str] = set()
                for excerpt in proposition.evidence_excerpts:
                    clause_id = str(excerpt.source_clause_id)
                    prior_excerpt = evidence_excerpts_by_clause_id.get(clause_id)
                    if prior_excerpt is None:
                        evidence_excerpts_by_clause_id[clause_id] = excerpt
                    elif prior_excerpt != excerpt:
                        conflicting_reused_source_clause_ids.add(clause_id)
                    span_id = str(excerpt.source_span_id)
                    span = spans_by_id.get(span_id)
                    if (
                        span is not None
                        and excerpt.source_content_sha256 != span.content_sha256
                    ):
                        source_content_hash_mismatch_count += 1
                    clause = clauses_by_id.get(clause_id)
                    if (
                        clause is None
                        or not self._excerpt_matches_clause(
                            excerpt,
                            clause,
                            proposition,
                        )
                        or not self._excerpt_matches_authoritative_span(excerpt, span)
                    ):
                        invalid_evidence_excerpt_count += 1
                        continue
                    validly_bound_span_ids.add(span_id)
                represented_primary_span_ids.update(
                    validly_bound_span_ids & primary_ids
                )
                numeric_reports = tuple(
                    numeric_binding_auditor.audit(
                        quantity=quantity,
                        clauses_by_id=clauses_by_id,
                        evidence_excerpts=proposition.evidence_excerpts,
                        normalized_statement=proposition.normalized_statement,
                    )
                    for quantity in proposition.numeric_quantities
                )
                proposition_overlapping_quantity_ids = overlapping_quantity_ids & {
                    str(quantity.quantity_id)
                    for quantity in proposition.numeric_quantities
                }
                invalid_numeric_binding_count += sum(
                    report.disposition is NumericBindingDisposition.INVALID
                    for report in numeric_reports
                )
                unverifiable_numeric_binding_count += sum(
                    report.disposition is NumericBindingDisposition.UNVERIFIABLE
                    for report in numeric_reports
                )
                if (
                    proposition_overlapping_quantity_ids
                    or any(
                        report.disposition is not NumericBindingDisposition.VERIFIED
                        for report in numeric_reports
                    )
                    or not self._numeric_inventory_is_exact(proposition)
                ):
                    unstructured_numeric_count += 1
                if proposition.review_status is not ReviewStatus.VERIFIED:
                    unverified_count += 1
                unresolved_disagreement_count += sum(
                    disagreement.status is DisagreementStatus.UNRESOLVED
                    for disagreement in proposition.disagreements
                )
                unauthenticated_resolution_count += sum(
                    disagreement.status is DisagreementStatus.HUMAN_RESOLVED
                    for disagreement in proposition.disagreements
                )
                normalized_statements_by_unit[unit_id].append(
                    proposition.normalized_statement.casefold().strip()
                )

        unbound_numeric_clause_ids = (
            numeric_binding_auditor.clauses_with_unbound_numeric_tokens(
                clauses_by_id=clauses_by_id,
                quantities=all_numeric_quantities,
            )
        )
        unstructured_numeric_count += len(unbound_numeric_clause_ids)
        unverifiable_numeric_binding_count += len(unbound_numeric_clause_ids)

        self._append_metric_finding(
            source_content_hash_mismatch_count,
            "source_content_hash_mismatch",
            "evidence excerpts refer to a stale or substituted source record",
            findings,
        )
        self._append_metric_finding(
            invalid_evidence_excerpt_count,
            "invalid_atomic_evidence_excerpts",
            "evidence excerpts do not exactly match their source clauses",
            findings,
        )
        self._append_metric_finding(
            len(conflicting_reused_source_clause_ids),
            "reused_atomic_evidence_clauses",
            "source clause IDs are reused with conflicting evidence bindings",
            findings,
        )
        self._append_metric_finding(
            invalid_source_reference_count,
            "invalid_atomic_source_references",
            "atomic source references are unknown, non-instructional, or outside the "
            "owning Learning Unit",
            findings,
        )
        self._append_metric_finding(
            context_only_count,
            "context_only_atomic_propositions",
            "atomic propositions lack primary evidence from their owning Learning Unit",
            findings,
        )
        self._append_metric_finding(
            unstructured_numeric_count,
            "unstructured_numeric_propositions",
            "atomic propositions have missing, extra, or mismatched numeric quantities",
            findings,
        )
        self._append_metric_finding(
            invalid_numeric_binding_count,
            "invalid_numeric_source_bindings",
            "numeric bindings conflict with authenticated clause evidence",
            findings,
        )
        self._append_metric_finding(
            unverifiable_numeric_binding_count,
            "unverifiable_numeric_source_bindings",
            "numeric bindings require authenticated human review",
            findings,
        )
        self._append_metric_finding(
            unverified_count,
            "unverified_atomic_propositions",
            "atomic propositions are not independently verified",
            findings,
        )
        self._append_metric_finding(
            unresolved_disagreement_count,
            "unresolved_atomic_disagreements",
            "source or evaluator disagreements remain unresolved",
            findings,
        )
        self._append_metric_finding(
            unauthenticated_resolution_count,
            "unauthenticated_disagreement_resolutions",
            "declared human resolutions lack an authenticated artifact resolver",
            findings,
        )

        for unit_id, statements in normalized_statements_by_unit.items():
            duplicate_statements = self._duplicates(statements)
            if duplicate_statements:
                findings.append(
                    QualityFinding(
                        code="duplicate_atomic_statements",
                        message=(
                            f"Learning Unit {unit_id} contains "
                            f"{len(duplicate_statements)} unmerged normalized statements."
                        ),
                    )
                )

        propositions_by_id = {
            str(proposition.proposition_id): proposition
            for ledger in validated_ledgers
            for proposition in ledger.propositions
        }
        known_proposition_ids = set(propositions_by_id)
        relation_ids = [
            str(relation.relation_id)
            for ledger in validated_ledgers
            for relation in ledger.relations
        ]
        unresolved_relation_count = 0
        semantic_edges: list[tuple[str, str, str]] = []
        for ledger in validated_ledgers:
            local_proposition_ids = {
                str(proposition.proposition_id) for proposition in ledger.propositions
            }
            for relation in ledger.relations:
                source_id = str(relation.source_proposition_id)
                target_id = str(relation.target_proposition_id)
                source_proposition = propositions_by_id.get(source_id)
                target_proposition = propositions_by_id.get(target_id)
                endpoint_span_ids = (
                    {
                        *map(str, source_proposition.source_span_ids),
                        *map(str, target_proposition.source_span_ids),
                    }
                    if source_proposition is not None and target_proposition is not None
                    else set()
                )
                invalid_relation = (
                    source_id not in local_proposition_ids
                    or target_id not in known_proposition_ids
                    or source_proposition is None
                    or target_proposition is None
                    or set(map(str, relation.source_span_ids)) != endpoint_span_ids
                )
                if invalid_relation:
                    unresolved_relation_count += 1
                semantic_edges.append(
                    (source_id, relation.relation_type.value, target_id)
                )
        duplicate_relation_ids = self._duplicates(relation_ids)
        if duplicate_relation_ids:
            findings.append(
                QualityFinding(
                    code="duplicate_atomic_relation_ids",
                    message=(
                        f"{len(duplicate_relation_ids)} proposition relation IDs are "
                        "duplicated."
                    ),
                )
            )
        duplicate_semantic_edges = self._duplicates(
            tuple("|".join(edge) for edge in semantic_edges)
        )
        if duplicate_semantic_edges:
            findings.append(
                QualityFinding(
                    code="duplicate_atomic_relation_edges",
                    message=(
                        f"{len(duplicate_semantic_edges)} semantic relation edges are "
                        "duplicated under distinct IDs."
                    ),
                )
            )
        self._append_metric_finding(
            unresolved_relation_count,
            "unresolved_atomic_relations",
            "proposition relations have unknown endpoints, wrong ownership, or "
            "unbound evidence",
            findings,
        )

        instructional_ids = {
            span_id
            for span_id, span in spans_by_id.items()
            if span.classification is SourceSpanClassification.INSTRUCTIONAL
        }
        missing_span_ids = tuple(
            sorted(instructional_ids - represented_primary_span_ids)
        )
        if missing_span_ids:
            findings.append(
                QualityFinding(
                    code="missing_instructional_source_span_inventory",
                    message=(
                        f"{len(missing_span_ids)} instructional source spans have no "
                        "atomic proposition in their primary Learning Unit."
                    ),
                )
            )

        proposition_count = len(proposition_ids)
        return AtomicContentLedgerAuditReport(
            lecture_id=str(source_universe.lecture_id),
            expected_learning_unit_count=len(expected_unit_ids),
            observed_learning_unit_count=len(validated_ledgers),
            proposition_count=proposition_count,
            instructional_source_span_count=len(instructional_ids),
            represented_instructional_source_span_count=(
                len(instructional_ids) - len(missing_span_ids)
            ),
            missing_instructional_source_span_ids=missing_span_ids,
            invalid_source_reference_count=invalid_source_reference_count,
            source_content_hash_mismatch_count=(source_content_hash_mismatch_count),
            invalid_evidence_excerpt_count=invalid_evidence_excerpt_count,
            reused_source_clause_count=len(conflicting_reused_source_clause_ids),
            context_only_proposition_count=context_only_count,
            unstructured_numeric_proposition_count=unstructured_numeric_count,
            invalid_numeric_source_binding_count=invalid_numeric_binding_count,
            unverifiable_numeric_source_binding_count=(
                unverifiable_numeric_binding_count
            ),
            unverified_proposition_count=unverified_count,
            unresolved_disagreement_count=unresolved_disagreement_count,
            unauthenticated_resolution_count=unauthenticated_resolution_count,
            unresolved_relation_count=unresolved_relation_count,
            findings=tuple(findings),
        )

    def _index_clause_inventories(
        self,
        inventories: Sequence[SourceClauseInventory],
        *,
        expected_unit_ids: set[str],
        lecture_id: str,
        source_universe_sha256: str,
        findings: list[QualityFinding],
    ) -> dict[str, SourceClause]:
        inventory_unit_ids = [
            str(inventory.learning_unit_id) for inventory in inventories
        ]
        duplicate_unit_ids = self._duplicates(inventory_unit_ids)
        if duplicate_unit_ids or set(inventory_unit_ids) != expected_unit_ids:
            findings.append(
                QualityFinding(
                    code="atomic_clause_inventory_set_mismatch",
                    message=(
                        "Atomic ledger auditing requires exactly one clause inventory "
                        "for every Learning Unit."
                    ),
                )
            )

        clauses_by_id: dict[str, SourceClause] = {}
        duplicate_clause_ids: set[str] = set()
        for inventory in inventories:
            if (
                str(inventory.lecture_id) != lecture_id
                or inventory.source_universe_sha256 != source_universe_sha256
            ):
                findings.append(
                    QualityFinding(
                        code="atomic_clause_inventory_identity_mismatch",
                        message=(
                            f"Clause inventory {inventory.learning_unit_id} does not "
                            "match the lecture or source-universe identity."
                        ),
                    )
                )
            for clause in inventory.clauses:
                clause_id = str(clause.clause_id)
                if clause_id in clauses_by_id:
                    duplicate_clause_ids.add(clause_id)
                    continue
                clauses_by_id[clause_id] = clause
        if duplicate_clause_ids:
            findings.append(
                QualityFinding(
                    code="duplicate_source_clause_ids_across_inventories",
                    message=(
                        f"{len(duplicate_clause_ids)} source clause IDs are duplicated "
                        "across clause inventories."
                    ),
                )
            )
        return clauses_by_id

    @staticmethod
    def _excerpt_matches_clause(
        excerpt: EvidenceExcerpt,
        clause: SourceClause,
        proposition: AtomicProposition,
    ) -> bool:
        selector = clause.selector.model_dump(mode="json", warnings=False)
        generated_identity = (
            clause.generated_observation_identity.model_dump(
                mode="json",
                warnings=False,
            )
            if clause.generated_observation_identity is not None
            else None
        )
        return (
            excerpt.source_clause_id == clause.clause_id
            and excerpt.source_span_id == clause.source_span_id
            and excerpt.modality is clause.modality
            and excerpt.source_content_sha256 == clause.source_content_sha256
            and excerpt.selector == selector
            and excerpt.generated_observation_identity == generated_identity
            and excerpt.verbatim_text == clause.verbatim_text
            and excerpt.observed_text == clause.observed_text
            and excerpt.text_sha256 == clause.text_sha256
            and clause.lecture_id == proposition.lecture_id
            and clause.learning_unit_id == proposition.learning_unit_id
        )

    @staticmethod
    def _excerpt_matches_authoritative_span(
        excerpt: EvidenceExcerpt,
        span: SourceEvidenceSpan | None,
    ) -> bool:
        return (
            span is not None
            and excerpt.source_span_id == span.span_id
            and excerpt.modality is span.modality
            and excerpt.source_content_sha256 == span.content_sha256
        )

    @staticmethod
    def _numeric_inventory_is_exact(proposition: AtomicProposition) -> bool:
        statement = proposition.normalized_statement
        quantities = proposition.numeric_quantities
        mentioned_values: Counter[Decimal] = Counter()
        for match in _NUMBER_PATTERN.finditer(statement):
            token = match.group(0).replace(",", ".")
            if len(token) > _MAX_NUMERIC_TOKEN_LENGTH:
                return False
            try:
                mentioned_values[Decimal(token)] += 1
            except InvalidOperation:
                return False
        structured_values: Counter[Decimal] = Counter()
        for quantity in quantities:
            structured_values[Decimal(quantity.lower_value)] += 1
            if quantity.upper_value is not None:
                structured_values[Decimal(quantity.upper_value)] += 1
        return mentioned_values == structured_values

    @staticmethod
    def _index_ledgers(
        ledgers: Sequence[AtomicContentLedger],
        findings: list[QualityFinding],
    ) -> dict[str, AtomicContentLedger]:
        unit_ids = [str(ledger.learning_unit_id) for ledger in ledgers]
        duplicates = AtomicContentLedgerAuditor._duplicates(unit_ids)
        if duplicates:
            findings.append(
                QualityFinding(
                    code="duplicate_learning_unit_ledgers",
                    message=f"Duplicate atomic ledgers for Learning Units: {duplicates}.",
                )
            )
        return {str(ledger.learning_unit_id): ledger for ledger in ledgers}

    @staticmethod
    def _audit_unit_set(
        expected_ids: set[str],
        observed_ids: set[str],
        findings: list[QualityFinding],
    ) -> None:
        missing = expected_ids - observed_ids
        unexpected = observed_ids - expected_ids
        if missing:
            findings.append(
                QualityFinding(
                    code="missing_learning_unit_ledgers",
                    message=f"{len(missing)} Learning Units have no atomic ledger.",
                )
            )
        if unexpected:
            findings.append(
                QualityFinding(
                    code="unexpected_learning_unit_ledgers",
                    message=f"{len(unexpected)} atomic ledgers target unknown units.",
                )
            )

    @staticmethod
    def _append_metric_finding(
        count: int,
        code: str,
        description: str,
        findings: list[QualityFinding],
    ) -> None:
        if count:
            findings.append(
                QualityFinding(
                    code=code,
                    message=f"{count} {description}.",
                )
            )

    @staticmethod
    def _duplicates(values: Sequence[str]) -> tuple[str, ...]:
        counts = Counter(values)
        return tuple(sorted(value for value, count in counts.items() if count > 1))

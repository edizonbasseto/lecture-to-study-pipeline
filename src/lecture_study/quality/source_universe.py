"""Deterministic completeness audit for the authoritative source universe."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from lecture_study.domain.source_universe import (
    ConceptualLearningUnit,
    SourceEvidenceSpan,
    SourceSpanClassification,
    SourceUniverse,
    TimelineClassification,
    TimelineInterval,
)
from lecture_study.quality.evidence_auditor import (
    ContentQualityError,
    QualityFinding,
)


@dataclass(frozen=True, slots=True)
class SourceUniverseAuditReport:
    """Objective metrics and blocking findings for one source universe."""

    lecture_id: str
    media_duration_seconds: float
    timeline_interval_count: int
    source_span_count: int
    learning_unit_count: int
    timeline_gap_seconds: float
    timeline_overlap_seconds: float
    timeline_outside_media_seconds: float
    learning_unit_gap_seconds: float
    learning_unit_overlap_seconds: float
    learning_unit_outside_media_seconds: float
    unresolved_timeline_interval_count: int
    unresolved_source_span_count: int
    timeline_classification_mismatch_count: int
    unreferenced_source_span_count: int
    multiply_referenced_source_span_count: int
    source_span_boundary_violation_count: int
    instructional_span_count: int
    owned_instructional_span_count: int
    missing_primary_ownership_count: int
    duplicate_primary_ownership_count: int
    invalid_primary_reference_count: int
    out_of_window_primary_span_count: int
    out_of_window_context_span_count: int
    unjustified_duration_unit_count: int
    duration_exception_unit_count: int
    findings: tuple[QualityFinding, ...]

    @property
    def passed(self) -> bool:
        return not self.findings

    def require_passed(self) -> None:
        if self.passed:
            return
        codes = ", ".join(finding.code for finding in self.findings)
        raise ContentQualityError(
            f"Source universe {self.lecture_id} failed completeness gates: {codes}"
        )


@dataclass(frozen=True, slots=True)
class _PartitionMetrics:
    gap_seconds: float
    overlap_seconds: float
    outside_media_seconds: float


@dataclass(frozen=True, slots=True)
class _TimelineReferenceMetrics:
    unreferenced_span_count: int
    multiply_referenced_span_count: int
    boundary_violation_count: int


@dataclass(frozen=True, slots=True)
class _OwnershipMetrics:
    instructional_span_count: int
    owned_instructional_span_count: int
    missing_primary_ownership_count: int
    duplicate_primary_ownership_count: int
    invalid_primary_reference_count: int
    out_of_window_primary_span_count: int
    out_of_window_context_span_count: int


@dataclass(frozen=True, slots=True)
class _DurationMetrics:
    unjustified_unit_count: int
    exception_unit_count: int


class _SequencedInterval(Protocol):
    sequence: int
    start_time: float
    end_time: float


class SourceUniverseAuditor:
    """Requires exact timeline partitioning and instructional span ownership."""

    def __init__(
        self,
        *,
        minimum_unit_seconds: float = 300.0,
        maximum_unit_seconds: float = 600.0,
        boundary_tolerance_seconds: float = 0.001,
        maximum_context_extension_seconds: float = 0.0,
    ) -> None:
        if not math.isfinite(minimum_unit_seconds) or minimum_unit_seconds <= 0:
            raise ValueError("minimum_unit_seconds must be finite and positive")
        if (
            not math.isfinite(maximum_unit_seconds)
            or maximum_unit_seconds < minimum_unit_seconds
        ):
            raise ValueError(
                "maximum_unit_seconds must be finite and greater than or equal to "
                "minimum_unit_seconds"
            )
        if (
            not math.isfinite(boundary_tolerance_seconds)
            or boundary_tolerance_seconds < 0
        ):
            raise ValueError(
                "boundary_tolerance_seconds must be finite and not negative"
            )
        if (
            not math.isfinite(maximum_context_extension_seconds)
            or maximum_context_extension_seconds < 0
        ):
            raise ValueError(
                "maximum_context_extension_seconds must be finite and not negative"
            )
        self._minimum_unit_seconds = minimum_unit_seconds
        self._maximum_unit_seconds = maximum_unit_seconds
        self._boundary_tolerance_seconds = boundary_tolerance_seconds
        self._maximum_context_extension_seconds = maximum_context_extension_seconds

    def audit(self, universe: SourceUniverse) -> SourceUniverseAuditReport:
        universe = SourceUniverse.model_validate(
            universe.model_dump(mode="python", warnings=False)
        )
        findings: list[QualityFinding] = []
        spans_by_id = self._index_source_spans(universe.evidence_spans, findings)

        self._audit_lecture_identities(universe, findings)
        self._audit_sequences(universe.timeline_intervals, "timeline", findings)
        self._audit_sequences(universe.learning_units, "learning_unit", findings)
        self._audit_unique_ids(universe, findings)
        self._audit_source_bounds(universe, findings)
        self._audit_duplicate_links(spans_by_id, findings)

        timeline_partition = self._audit_partition(
            intervals=universe.timeline_intervals,
            expected_duration_seconds=universe.media_duration_seconds,
            finding_prefix="timeline",
            findings=findings,
        )
        unit_partition = self._audit_partition(
            intervals=universe.learning_units,
            expected_duration_seconds=universe.media_duration_seconds,
            finding_prefix="learning_unit_timeline",
            findings=findings,
        )
        timeline_references = self._audit_timeline_references(
            intervals=universe.timeline_intervals,
            spans_by_id=spans_by_id,
            findings=findings,
        )
        timeline_classification_mismatch_count = (
            self._audit_timeline_classification_coherence(
                intervals=universe.timeline_intervals,
                spans_by_id=spans_by_id,
                findings=findings,
            )
        )
        ownership = self._audit_primary_ownership(
            units=universe.learning_units,
            spans_by_id=spans_by_id,
            findings=findings,
        )
        duration = self._audit_unit_durations(universe.learning_units, findings)

        unresolved_intervals = sum(
            interval.classification
            in {TimelineClassification.AMBIGUOUS, TimelineClassification.INAUDIBLE}
            for interval in universe.timeline_intervals
        )
        if unresolved_intervals:
            findings.append(
                QualityFinding(
                    code="unresolved_timeline_classification",
                    message=(
                        f"{unresolved_intervals} timeline intervals remain ambiguous "
                        "or inaudible."
                    ),
                )
            )

        unresolved_spans = sum(
            span.classification
            in {
                SourceSpanClassification.AMBIGUOUS,
                SourceSpanClassification.INAUDIBLE,
            }
            for span in universe.evidence_spans
        )
        if unresolved_spans:
            findings.append(
                QualityFinding(
                    code="unresolved_source_span_classification",
                    message=(
                        f"{unresolved_spans} source spans remain ambiguous or inaudible."
                    ),
                )
            )

        return SourceUniverseAuditReport(
            lecture_id=str(universe.lecture_id),
            media_duration_seconds=universe.media_duration_seconds,
            timeline_interval_count=len(universe.timeline_intervals),
            source_span_count=len(universe.evidence_spans),
            learning_unit_count=len(universe.learning_units),
            timeline_gap_seconds=timeline_partition.gap_seconds,
            timeline_overlap_seconds=timeline_partition.overlap_seconds,
            timeline_outside_media_seconds=timeline_partition.outside_media_seconds,
            learning_unit_gap_seconds=unit_partition.gap_seconds,
            learning_unit_overlap_seconds=unit_partition.overlap_seconds,
            learning_unit_outside_media_seconds=unit_partition.outside_media_seconds,
            unresolved_timeline_interval_count=unresolved_intervals,
            unresolved_source_span_count=unresolved_spans,
            timeline_classification_mismatch_count=(
                timeline_classification_mismatch_count
            ),
            unreferenced_source_span_count=(
                timeline_references.unreferenced_span_count
            ),
            multiply_referenced_source_span_count=(
                timeline_references.multiply_referenced_span_count
            ),
            source_span_boundary_violation_count=(
                timeline_references.boundary_violation_count
            ),
            instructional_span_count=ownership.instructional_span_count,
            owned_instructional_span_count=(ownership.owned_instructional_span_count),
            missing_primary_ownership_count=(ownership.missing_primary_ownership_count),
            duplicate_primary_ownership_count=(
                ownership.duplicate_primary_ownership_count
            ),
            invalid_primary_reference_count=(ownership.invalid_primary_reference_count),
            out_of_window_primary_span_count=(
                ownership.out_of_window_primary_span_count
            ),
            out_of_window_context_span_count=(
                ownership.out_of_window_context_span_count
            ),
            unjustified_duration_unit_count=duration.unjustified_unit_count,
            duration_exception_unit_count=duration.exception_unit_count,
            findings=tuple(findings),
        )

    @staticmethod
    def _index_source_spans(
        spans: Sequence[SourceEvidenceSpan],
        findings: list[QualityFinding],
    ) -> dict[str, SourceEvidenceSpan]:
        span_ids = tuple(str(span.span_id) for span in spans)
        if len(span_ids) != len(set(span_ids)):
            findings.append(
                QualityFinding(
                    code="duplicate_source_span_ids",
                    message="Source evidence span identifiers must be unique.",
                )
            )
        source_records = tuple(
            (str(span.source_artifact_id), str(span.source_record_id)) for span in spans
        )
        if len(source_records) != len(set(source_records)):
            findings.append(
                QualityFinding(
                    code="duplicate_source_records",
                    message=(
                        "A source artifact record must not produce multiple evidence "
                        "spans."
                    ),
                )
            )
        return {str(span.span_id): span for span in spans}

    @staticmethod
    def _audit_lecture_identities(
        universe: SourceUniverse,
        findings: list[QualityFinding],
    ) -> None:
        mismatched_count = (
            sum(
                item.lecture_id != universe.lecture_id
                for item in universe.timeline_intervals
            )
            + sum(
                item.lecture_id != universe.lecture_id
                for item in universe.evidence_spans
            )
            + sum(
                item.lecture_id != universe.lecture_id
                for item in universe.learning_units
            )
        )
        if mismatched_count:
            findings.append(
                QualityFinding(
                    code="source_universe_lecture_identity_mismatch",
                    message=(
                        f"{mismatched_count} source-universe members belong to another "
                        "lecture."
                    ),
                )
            )

    @staticmethod
    def _audit_sequences(
        intervals: Sequence[_SequencedInterval],
        label: str,
        findings: list[QualityFinding],
    ) -> None:
        observed = tuple(interval.sequence for interval in intervals)
        expected = tuple(range(1, len(intervals) + 1))
        if observed != expected:
            findings.append(
                QualityFinding(
                    code=f"{label}_sequence_mismatch",
                    message=f"Expected {label} sequences {expected}, observed {observed}.",
                )
            )
        chronological = tuple(
            (interval.start_time, interval.end_time) for interval in intervals
        )
        if chronological != tuple(sorted(chronological)):
            findings.append(
                QualityFinding(
                    code=f"{label}_chronology_mismatch",
                    message=(
                        f"{label} collection order must match chronological order."
                    ),
                )
            )

    @staticmethod
    def _audit_unique_ids(
        universe: SourceUniverse,
        findings: list[QualityFinding],
    ) -> None:
        identifier_groups = (
            (
                "duplicate_timeline_interval_ids",
                tuple(str(item.interval_id) for item in universe.timeline_intervals),
            ),
            (
                "duplicate_learning_unit_ids",
                tuple(str(item.unit_id) for item in universe.learning_units),
            ),
        )
        for code, identifiers in identifier_groups:
            if len(identifiers) != len(set(identifiers)):
                findings.append(
                    QualityFinding(
                        code=code,
                        message="Identifiers must be unique within the source universe.",
                    )
                )

    def _audit_source_bounds(
        self,
        universe: SourceUniverse,
        findings: list[QualityFinding],
    ) -> None:
        outside_count = sum(
            span.start_time < -self._boundary_tolerance_seconds
            or span.end_time
            > universe.media_duration_seconds + self._boundary_tolerance_seconds
            for span in universe.evidence_spans
        )
        if outside_count:
            findings.append(
                QualityFinding(
                    code="source_spans_outside_media_timeline",
                    message=(
                        f"{outside_count} source spans fall outside the certified media "
                        "timeline."
                    ),
                )
            )

    @staticmethod
    def _audit_duplicate_links(
        spans_by_id: dict[str, SourceEvidenceSpan],
        findings: list[QualityFinding],
    ) -> None:
        unresolved_duplicate_ids = tuple(
            str(span.span_id)
            for span in spans_by_id.values()
            if span.classification is SourceSpanClassification.DUPLICATE
            and str(span.duplicate_of_span_id) not in spans_by_id
        )
        if unresolved_duplicate_ids:
            findings.append(
                QualityFinding(
                    code="unresolved_duplicate_source_spans",
                    message=(
                        f"{len(unresolved_duplicate_ids)} duplicate source spans point "
                        "to unknown originals."
                    ),
                )
            )
        chained_duplicate_ids = tuple(
            str(span.span_id)
            for span in spans_by_id.values()
            if span.classification is SourceSpanClassification.DUPLICATE
            and str(span.duplicate_of_span_id) in spans_by_id
            and spans_by_id[str(span.duplicate_of_span_id)].classification
            is SourceSpanClassification.DUPLICATE
        )
        if chained_duplicate_ids:
            findings.append(
                QualityFinding(
                    code="duplicate_source_span_chain",
                    message=(
                        f"{len(chained_duplicate_ids)} duplicate source spans do not "
                        "point directly to a non-duplicate original."
                    ),
                )
            )
        content_mismatch_ids = tuple(
            str(span.span_id)
            for span in spans_by_id.values()
            if span.classification is SourceSpanClassification.DUPLICATE
            and str(span.duplicate_of_span_id) in spans_by_id
            and span.content_sha256
            != spans_by_id[str(span.duplicate_of_span_id)].content_sha256
        )
        if content_mismatch_ids:
            findings.append(
                QualityFinding(
                    code="duplicate_source_span_content_mismatch",
                    message=(
                        f"{len(content_mismatch_ids)} duplicate source spans have "
                        "content hashes different from their declared originals."
                    ),
                )
            )

    def _audit_partition(
        self,
        *,
        intervals: Sequence[_SequencedInterval],
        expected_duration_seconds: float,
        finding_prefix: str,
        findings: list[QualityFinding],
    ) -> _PartitionMetrics:
        ordered = sorted(intervals, key=lambda interval: interval.start_time)
        cursor = 0.0
        gap_seconds = 0.0
        overlap_seconds = 0.0
        outside_media_seconds = 0.0
        for interval in ordered:
            outside_media_seconds += max(
                0.0,
                interval.end_time - max(interval.start_time, expected_duration_seconds),
            )
            clipped_start = min(interval.start_time, expected_duration_seconds)
            clipped_end = min(interval.end_time, expected_duration_seconds)
            if clipped_end <= clipped_start:
                continue

            delta = clipped_start - cursor
            if delta > 0:
                gap_seconds += delta
            elif delta < 0:
                overlap_seconds += min(cursor, clipped_end) - clipped_start
            cursor = max(cursor, clipped_end)

        tail_delta = expected_duration_seconds - cursor
        if tail_delta > 0:
            gap_seconds += tail_delta

        if gap_seconds > self._boundary_tolerance_seconds:
            findings.append(
                QualityFinding(
                    code=f"{finding_prefix}_gap",
                    message=(
                        f"The {finding_prefix} partition omits "
                        f"{gap_seconds:.6f} seconds."
                    ),
                )
            )
        if overlap_seconds > self._boundary_tolerance_seconds:
            findings.append(
                QualityFinding(
                    code=f"{finding_prefix}_overlap",
                    message=(
                        f"The {finding_prefix} partition overlaps by "
                        f"{overlap_seconds:.6f} seconds."
                    ),
                )
            )
        if outside_media_seconds > self._boundary_tolerance_seconds:
            findings.append(
                QualityFinding(
                    code=f"{finding_prefix}_outside_media",
                    message=(
                        f"The {finding_prefix} partition extends beyond the media by "
                        f"{outside_media_seconds:.6f} seconds."
                    ),
                )
            )
        return _PartitionMetrics(
            gap_seconds=gap_seconds,
            overlap_seconds=overlap_seconds,
            outside_media_seconds=outside_media_seconds,
        )

    def _audit_timeline_references(
        self,
        *,
        intervals: Sequence[TimelineInterval],
        spans_by_id: dict[str, SourceEvidenceSpan],
        findings: list[QualityFinding],
    ) -> _TimelineReferenceMetrics:
        reference_counts = Counter(
            str(span_id)
            for interval in intervals
            for span_id in interval.evidence_span_ids
        )
        known_ids = set(spans_by_id)
        referenced_ids = set(reference_counts)
        unknown_count = len(referenced_ids - known_ids)
        if unknown_count:
            findings.append(
                QualityFinding(
                    code="unknown_timeline_source_span_references",
                    message=(
                        f"{unknown_count} timeline references do not resolve to source "
                        "spans."
                    ),
                )
            )

        unreferenced_count = len(known_ids - referenced_ids)
        if unreferenced_count:
            findings.append(
                QualityFinding(
                    code="unreferenced_source_spans",
                    message=(
                        f"{unreferenced_count} source spans are absent from the "
                        "classified timeline."
                    ),
                )
            )

        multiply_referenced_ids = {
            span_id for span_id, count in reference_counts.items() if count > 1
        }
        if multiply_referenced_ids:
            findings.append(
                QualityFinding(
                    code="multiply_referenced_source_spans",
                    message=(
                        f"{len(multiply_referenced_ids)} source spans occur in more than "
                        "one timeline reference."
                    ),
                )
            )

        crossing_count = sum(
            not self._span_fits_interval(spans_by_id[span_id], interval)
            for interval in intervals
            for span_id in map(str, interval.evidence_span_ids)
            if span_id in spans_by_id
        )
        if crossing_count:
            findings.append(
                QualityFinding(
                    code="source_spans_cross_timeline_boundaries",
                    message=(
                        f"{crossing_count} source span references cross their timeline "
                        "interval boundary."
                    ),
                )
            )
        return _TimelineReferenceMetrics(
            unreferenced_span_count=unreferenced_count,
            multiply_referenced_span_count=len(multiply_referenced_ids),
            boundary_violation_count=crossing_count,
        )

    @staticmethod
    def _audit_timeline_classification_coherence(
        *,
        intervals: Sequence[TimelineInterval],
        spans_by_id: dict[str, SourceEvidenceSpan],
        findings: list[QualityFinding],
    ) -> int:
        mismatch_count = 0
        resolved_classifications = {
            TimelineClassification.INSTRUCTIONAL,
            TimelineClassification.NON_INSTRUCTIONAL,
        }
        for interval in intervals:
            if interval.classification not in resolved_classifications:
                continue
            contains_instructional_evidence = any(
                spans_by_id[span_id].classification
                is SourceSpanClassification.INSTRUCTIONAL
                for span_id in map(str, interval.evidence_span_ids)
                if span_id in spans_by_id
            )
            expected_classification = (
                TimelineClassification.INSTRUCTIONAL
                if contains_instructional_evidence
                else TimelineClassification.NON_INSTRUCTIONAL
            )
            if interval.classification is not expected_classification:
                mismatch_count += 1

        if mismatch_count:
            findings.append(
                QualityFinding(
                    code="timeline_source_classification_mismatch",
                    message=(
                        f"{mismatch_count} resolved timeline intervals contradict the "
                        "classifications of their referenced source spans."
                    ),
                )
            )
        return mismatch_count

    def _audit_primary_ownership(
        self,
        *,
        units: Sequence[ConceptualLearningUnit],
        spans_by_id: dict[str, SourceEvidenceSpan],
        findings: list[QualityFinding],
    ) -> _OwnershipMetrics:
        ownership_counts = Counter(
            str(span_id) for unit in units for span_id in unit.primary_source_span_ids
        )
        instructional_ids = {
            span_id
            for span_id, span in spans_by_id.items()
            if span.classification is SourceSpanClassification.INSTRUCTIONAL
        }
        missing_ids = {
            span_id for span_id in instructional_ids if ownership_counts[span_id] == 0
        }
        duplicate_ids = {
            span_id for span_id in instructional_ids if ownership_counts[span_id] > 1
        }
        if missing_ids:
            findings.append(
                QualityFinding(
                    code="missing_instructional_span_ownership",
                    message=(
                        f"{len(missing_ids)} instructional source spans have no primary "
                        "learning unit."
                    ),
                )
            )
        if duplicate_ids:
            findings.append(
                QualityFinding(
                    code="duplicate_instructional_span_ownership",
                    message=(
                        f"{len(duplicate_ids)} instructional source spans have multiple "
                        "primary learning units."
                    ),
                )
            )

        unknown_primary_count = sum(
            span_id not in spans_by_id for span_id in ownership_counts
        )
        non_instructional_primary_count = sum(
            span_id in spans_by_id
            and spans_by_id[span_id].classification
            is not SourceSpanClassification.INSTRUCTIONAL
            for span_id in ownership_counts
        )
        if unknown_primary_count:
            findings.append(
                QualityFinding(
                    code="unknown_primary_source_span",
                    message=(
                        f"{unknown_primary_count} primary source span references cannot "
                        "be resolved."
                    ),
                )
            )
        if non_instructional_primary_count:
            findings.append(
                QualityFinding(
                    code="non_instructional_primary_span",
                    message=(
                        f"{non_instructional_primary_count} primary source spans are not "
                        "classified as instructional."
                    ),
                )
            )

        unknown_context_count = sum(
            str(span_id) not in spans_by_id
            for unit in units
            for span_id in unit.context_source_span_ids
        )
        if unknown_context_count:
            findings.append(
                QualityFinding(
                    code="unknown_context_source_span",
                    message=(
                        f"{unknown_context_count} context source span references cannot "
                        "be resolved."
                    ),
                )
            )

        out_of_window_context_count = sum(
            not self._span_fits_context_window(spans_by_id[span_id], unit)
            for unit in units
            for span_id in map(str, unit.context_source_span_ids)
            if span_id in spans_by_id
        )
        if out_of_window_context_count:
            findings.append(
                QualityFinding(
                    code="context_spans_outside_learning_unit_policy",
                    message=(
                        f"{out_of_window_context_count} context source spans fall "
                        "outside the configured unit-boundary extension."
                    ),
                )
            )

        out_of_window_count = sum(
            not self._span_fits_interval(spans_by_id[span_id], unit)
            for unit in units
            for span_id in map(str, unit.primary_source_span_ids)
            if span_id in spans_by_id
        )
        if out_of_window_count:
            findings.append(
                QualityFinding(
                    code="primary_spans_outside_learning_units",
                    message=(
                        f"{out_of_window_count} primary source spans cross their owning "
                        "learning-unit boundary."
                    ),
                )
            )

        owned_instructional_count = sum(
            ownership_counts[span_id] == 1 for span_id in instructional_ids
        )
        return _OwnershipMetrics(
            instructional_span_count=len(instructional_ids),
            owned_instructional_span_count=owned_instructional_count,
            missing_primary_ownership_count=len(missing_ids),
            duplicate_primary_ownership_count=len(duplicate_ids),
            invalid_primary_reference_count=(
                unknown_primary_count + non_instructional_primary_count
            ),
            out_of_window_primary_span_count=out_of_window_count,
            out_of_window_context_span_count=out_of_window_context_count,
        )

    def _audit_unit_durations(
        self,
        units: Sequence[ConceptualLearningUnit],
        findings: list[QualityFinding],
    ) -> _DurationMetrics:
        outside_policy = tuple(
            unit
            for unit in units
            if not (
                self._minimum_unit_seconds
                <= unit.end_time - unit.start_time
                <= self._maximum_unit_seconds
            )
        )
        unjustified = tuple(
            unit for unit in outside_policy if unit.duration_exception_reason is None
        )
        if unjustified:
            findings.append(
                QualityFinding(
                    code="unjustified_learning_unit_duration",
                    message=(
                        f"{len(unjustified)} learning units fall outside the duration "
                        "policy without an explicit reason."
                    ),
                    affected_pack_sequences=tuple(
                        unit.sequence for unit in unjustified
                    ),
                )
            )
        return _DurationMetrics(
            unjustified_unit_count=len(unjustified),
            exception_unit_count=len(outside_policy) - len(unjustified),
        )

    def _span_fits_interval(
        self,
        span: SourceEvidenceSpan,
        interval: TimelineInterval | ConceptualLearningUnit,
    ) -> bool:
        starts_inside = (
            span.start_time >= interval.start_time - self._boundary_tolerance_seconds
        )
        ends_inside = (
            span.end_time <= interval.end_time + self._boundary_tolerance_seconds
        )
        return starts_inside and ends_inside

    def _span_fits_context_window(
        self,
        span: SourceEvidenceSpan,
        unit: ConceptualLearningUnit,
    ) -> bool:
        extension = (
            self._maximum_context_extension_seconds + self._boundary_tolerance_seconds
        )
        return (
            span.start_time >= unit.start_time - extension
            and span.end_time <= unit.end_time + extension
        )

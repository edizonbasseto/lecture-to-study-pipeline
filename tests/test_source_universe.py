from __future__ import annotations

import pytest
from pydantic import ValidationError

from lecture_study.domain.source_universe import (
    ConceptualLearningUnit,
    EvidenceModality,
    SourceEvidenceSpan,
    SourceSpanClassification,
    SourceUniverse,
    TimelineClassification,
    TimelineInterval,
)
from lecture_study.quality.source_universe import SourceUniverseAuditor

_DIGEST = "a" * 64


def _span(
    span_id: str,
    start_time: float,
    end_time: float,
    *,
    classification: SourceSpanClassification = SourceSpanClassification.INSTRUCTIONAL,
) -> SourceEvidenceSpan:
    return SourceEvidenceSpan(
        schema_version=1,
        span_id=span_id,
        lecture_id="lecture-001",
        source_artifact_id="transcript",
        source_record_id=f"record-{span_id}",
        modality=EvidenceModality.TRANSCRIPT,
        start_time=start_time,
        end_time=end_time,
        classification=classification,
        content_sha256=_DIGEST,
    )


def _interval(
    sequence: int,
    start_time: float,
    end_time: float,
    span_ids: tuple[str, ...],
    *,
    classification: TimelineClassification = TimelineClassification.INSTRUCTIONAL,
) -> TimelineInterval:
    return TimelineInterval(
        schema_version=1,
        interval_id=f"interval-{sequence:03d}",
        lecture_id="lecture-001",
        sequence=sequence,
        start_time=start_time,
        end_time=end_time,
        classification=classification,
        evidence_span_ids=span_ids,
    )


def _unit(
    sequence: int,
    start_time: float,
    end_time: float,
    primary_span_ids: tuple[str, ...],
    *,
    duration_exception_reason: str | None = None,
) -> ConceptualLearningUnit:
    return ConceptualLearningUnit(
        schema_version=1,
        unit_id=f"unit-{sequence:03d}",
        lecture_id="lecture-001",
        sequence=sequence,
        start_time=start_time,
        end_time=end_time,
        primary_source_span_ids=primary_span_ids,
        duration_exception_reason=duration_exception_reason,
    )


def _valid_universe() -> SourceUniverse:
    first_span = _span("span-001", 10.0, 20.0)
    second_span = _span("span-002", 310.0, 320.0)
    return SourceUniverse(
        schema_version=1,
        lecture_id="lecture-001",
        media_duration_seconds=600.0,
        timeline_intervals=(
            _interval(1, 0.0, 300.0, (first_span.span_id,)),
            _interval(2, 300.0, 600.0, (second_span.span_id,)),
        ),
        evidence_spans=(first_span, second_span),
        learning_units=(
            _unit(1, 0.0, 300.0, (first_span.span_id,)),
            _unit(2, 300.0, 600.0, (second_span.span_id,)),
        ),
    )


def test_source_universe_accepts_exact_timeline_and_primary_ownership() -> None:
    report = SourceUniverseAuditor().audit(_valid_universe())

    assert report.passed is True
    assert report.findings == ()
    assert report.instructional_span_count == 2
    assert report.owned_instructional_span_count == 2
    assert report.timeline_gap_seconds == 0.0
    assert report.timeline_overlap_seconds == 0.0
    report.require_passed()


def test_source_universe_rejects_missing_timeline_tail() -> None:
    universe = _valid_universe()
    intervals = (
        universe.timeline_intervals[0],
        universe.timeline_intervals[1].model_copy(update={"end_time": 590.0}),
    )

    report = SourceUniverseAuditor().audit(
        universe.model_copy(update={"timeline_intervals": intervals})
    )

    assert report.timeline_gap_seconds == 10.0
    assert "timeline_gap" in {finding.code for finding in report.findings}


def test_source_universe_rejects_duplicate_and_unreferenced_timeline_spans() -> None:
    universe = _valid_universe()
    intervals = (
        universe.timeline_intervals[0],
        universe.timeline_intervals[1].model_copy(
            update={"evidence_span_ids": ("span-001",)}
        ),
    )

    report = SourceUniverseAuditor().audit(
        universe.model_copy(update={"timeline_intervals": intervals})
    )

    assert report.multiply_referenced_source_span_count == 1
    assert report.unreferenced_source_span_count == 1
    assert {finding.code for finding in report.findings} >= {
        "multiply_referenced_source_spans",
        "unreferenced_source_spans",
    }


def test_source_universe_requires_exact_instructional_primary_ownership() -> None:
    universe = _valid_universe()
    units = (
        universe.learning_units[0].model_copy(
            update={"primary_source_span_ids": ("span-002",)}
        ),
        universe.learning_units[1].model_copy(
            update={"primary_source_span_ids": ("span-002",)}
        ),
    )

    report = SourceUniverseAuditor().audit(
        universe.model_copy(update={"learning_units": units})
    )

    assert report.missing_primary_ownership_count == 1
    assert report.duplicate_primary_ownership_count == 1
    assert {finding.code for finding in report.findings} >= {
        "missing_instructional_span_ownership",
        "duplicate_instructional_span_ownership",
    }


def test_source_universe_rejects_span_split_by_timeline_and_unit_boundary() -> None:
    universe = _valid_universe()
    crossing_span = _span("span-001", 295.0, 305.0)
    report = SourceUniverseAuditor().audit(
        universe.model_copy(
            update={
                "evidence_spans": (crossing_span, universe.evidence_spans[1]),
            }
        )
    )

    assert report.out_of_window_primary_span_count == 1
    assert {finding.code for finding in report.findings} >= {
        "source_spans_cross_timeline_boundaries",
        "primary_spans_outside_learning_units",
    }


def test_source_universe_blocks_ambiguous_or_inaudible_evidence() -> None:
    universe = _valid_universe()
    ambiguous_span = universe.evidence_spans[0].model_copy(
        update={"classification": SourceSpanClassification.AMBIGUOUS}
    )
    ambiguous_interval = universe.timeline_intervals[0].model_copy(
        update={"classification": TimelineClassification.INAUDIBLE}
    )
    report = SourceUniverseAuditor().audit(
        universe.model_copy(
            update={
                "evidence_spans": (ambiguous_span, universe.evidence_spans[1]),
                "timeline_intervals": (
                    ambiguous_interval,
                    universe.timeline_intervals[1],
                ),
            }
        )
    )

    assert report.unresolved_source_span_count == 1
    assert report.unresolved_timeline_interval_count == 1
    assert {finding.code for finding in report.findings} >= {
        "unresolved_source_span_classification",
        "unresolved_timeline_classification",
    }


def test_timeline_classification_must_match_instructional_spans() -> None:
    universe = _valid_universe()
    intervals = tuple(
        interval.model_copy(
            update={"classification": TimelineClassification.NON_INSTRUCTIONAL}
        )
        for interval in universe.timeline_intervals
    )

    report = SourceUniverseAuditor().audit(
        universe.model_copy(update={"timeline_intervals": intervals})
    )

    assert report.timeline_classification_mismatch_count == 2
    assert "timeline_source_classification_mismatch" in {
        finding.code for finding in report.findings
    }


def test_instructional_timeline_requires_instructional_evidence() -> None:
    universe = _valid_universe()
    non_instructional = universe.evidence_spans[0].model_copy(
        update={"classification": SourceSpanClassification.NON_INSTRUCTIONAL}
    )

    report = SourceUniverseAuditor().audit(
        universe.model_copy(
            update={"evidence_spans": (non_instructional, universe.evidence_spans[1])}
        )
    )

    assert report.timeline_classification_mismatch_count == 1
    assert "timeline_source_classification_mismatch" in {
        finding.code for finding in report.findings
    }


def test_source_universe_requires_reason_for_unit_outside_duration_policy() -> None:
    universe = _valid_universe()
    units = (
        _unit(1, 0.0, 200.0, ("span-001",)),
        _unit(2, 200.0, 600.0, ("span-002",)),
    )

    report = SourceUniverseAuditor().audit(
        universe.model_copy(update={"learning_units": units})
    )

    assert report.unjustified_duration_unit_count == 1
    assert "unjustified_learning_unit_duration" in {
        finding.code for finding in report.findings
    }


def test_source_universe_accepts_explicit_duration_exception() -> None:
    universe = _valid_universe()
    units = (
        _unit(
            1,
            0.0,
            200.0,
            ("span-001",),
            duration_exception_reason="Topic boundary occurs before five minutes.",
        ),
        _unit(2, 200.0, 600.0, ("span-002",)),
    )

    report = SourceUniverseAuditor().audit(
        universe.model_copy(update={"learning_units": units})
    )

    assert report.passed is True
    assert report.duration_exception_unit_count == 1


def test_source_universe_rejects_non_instructional_primary_span() -> None:
    universe = _valid_universe()
    non_instructional_span = universe.evidence_spans[0].model_copy(
        update={"classification": SourceSpanClassification.NON_INSTRUCTIONAL}
    )

    report = SourceUniverseAuditor().audit(
        universe.model_copy(
            update={
                "evidence_spans": (
                    non_instructional_span,
                    universe.evidence_spans[1],
                )
            }
        )
    )

    assert report.invalid_primary_reference_count == 1
    assert "non_instructional_primary_span" in {
        finding.code for finding in report.findings
    }


def test_source_universe_accepts_point_ocr_evidence() -> None:
    universe = _valid_universe()
    point_span = SourceEvidenceSpan(
        schema_version=1,
        span_id="span-003",
        lecture_id="lecture-001",
        source_artifact_id="ocr",
        source_record_id="frame-003",
        modality=EvidenceModality.OCR,
        start_time=350.0,
        end_time=350.0,
        classification=SourceSpanClassification.INSTRUCTIONAL,
        content_sha256=_DIGEST,
    )
    intervals = (
        universe.timeline_intervals[0],
        universe.timeline_intervals[1].model_copy(
            update={"evidence_span_ids": ("span-002", "span-003")}
        ),
    )
    units = (
        universe.learning_units[0],
        universe.learning_units[1].model_copy(
            update={"primary_source_span_ids": ("span-002", "span-003")}
        ),
    )

    report = SourceUniverseAuditor().audit(
        universe.model_copy(
            update={
                "timeline_intervals": intervals,
                "evidence_spans": (*universe.evidence_spans, point_span),
                "learning_units": units,
            }
        )
    )

    assert report.passed is True
    assert report.instructional_span_count == 3


def test_source_universe_rejects_learning_unit_tail_gap() -> None:
    universe = _valid_universe()
    units = (
        universe.learning_units[0],
        universe.learning_units[1].model_copy(update={"end_time": 590.0}),
    )

    report = SourceUniverseAuditor().audit(
        universe.model_copy(update={"learning_units": units})
    )

    assert report.learning_unit_gap_seconds == 10.0
    assert "learning_unit_timeline_gap" in {finding.code for finding in report.findings}


def test_duplicate_source_span_requires_a_distinct_resolvable_original() -> None:
    with pytest.raises(ValidationError, match="duplicate_of_span_id is required"):
        _span(
            "span-duplicate",
            30.0,
            40.0,
            classification=SourceSpanClassification.DUPLICATE,
        )

    universe = _valid_universe()
    duplicate_span = SourceEvidenceSpan(
        schema_version=1,
        span_id="span-duplicate",
        lecture_id="lecture-001",
        source_artifact_id="transcript",
        source_record_id="record-duplicate",
        modality=EvidenceModality.TRANSCRIPT,
        start_time=30.0,
        end_time=40.0,
        classification=SourceSpanClassification.DUPLICATE,
        duplicate_of_span_id="missing-original",
        content_sha256=_DIGEST,
    )
    intervals = (
        universe.timeline_intervals[0].model_copy(
            update={"evidence_span_ids": ("span-001", "span-duplicate")}
        ),
        universe.timeline_intervals[1],
    )

    report = SourceUniverseAuditor().audit(
        universe.model_copy(
            update={
                "timeline_intervals": intervals,
                "evidence_spans": (*universe.evidence_spans, duplicate_span),
            }
        )
    )

    assert "unresolved_duplicate_source_spans" in {
        finding.code for finding in report.findings
    }


def test_source_universe_revalidates_unchecked_models_at_audit_boundary() -> None:
    invalid = SourceUniverse.model_construct(
        schema_version=1,
        lecture_id="lecture-001",
        media_duration_seconds=float("nan"),
        timeline_intervals=(),
        evidence_spans=(),
        learning_units=(),
    )

    with pytest.raises(ValidationError):
        SourceUniverseAuditor().audit(invalid)


def test_source_universe_rejects_unbounded_persisted_sequence() -> None:
    universe = _valid_universe()

    with pytest.raises(ValidationError):
        TimelineInterval.model_validate(
            {
                **universe.timeline_intervals[0].model_dump(mode="python"),
                "sequence": 1 << 20_000,
            }
        )


def test_source_universe_accumulates_sub_tolerance_partition_gaps() -> None:
    universe = _valid_universe()
    intervals = (
        _interval(1, 0.0, 200.0, ("span-001",)),
        _interval(2, 200.00075, 400.0, ("span-002",)),
        _interval(3, 400.00075, 600.0, ()),
    )

    report = SourceUniverseAuditor().audit(
        universe.model_copy(update={"timeline_intervals": intervals})
    )

    assert report.timeline_gap_seconds == pytest.approx(0.0015)
    assert "timeline_gap" in {finding.code for finding in report.findings}


def test_source_universe_measures_nested_overlap_without_overcounting() -> None:
    universe = _valid_universe()
    intervals = (
        _interval(1, 0.0, 300.0, ("span-001",)),
        _interval(2, 100.0, 200.0, ()),
        _interval(3, 300.0, 600.0, ("span-002",)),
    )

    report = SourceUniverseAuditor().audit(
        universe.model_copy(update={"timeline_intervals": intervals})
    )

    assert report.timeline_overlap_seconds == 100.0
    assert "timeline_overlap" in {finding.code for finding in report.findings}


def test_source_universe_clips_partition_metrics_to_media_duration() -> None:
    universe = _valid_universe()
    intervals = (
        _interval(1, 0.0, 600.0, ("span-001", "span-002")),
        _interval(
            2,
            700.0,
            800.0,
            (),
            classification=TimelineClassification.NON_INSTRUCTIONAL,
        ),
    )

    report = SourceUniverseAuditor().audit(
        universe.model_copy(update={"timeline_intervals": intervals})
    )

    assert report.timeline_gap_seconds == 0.0
    assert report.timeline_overlap_seconds == 0.0
    assert report.timeline_outside_media_seconds == 100.0
    assert "timeline_outside_media" in {finding.code for finding in report.findings}


def test_source_universe_reports_learning_unit_duration_outside_media() -> None:
    universe = _valid_universe()
    units = (
        universe.learning_units[0],
        universe.learning_units[1].model_copy(update={"end_time": 650.0}),
    )

    report = SourceUniverseAuditor().audit(
        universe.model_copy(update={"learning_units": units})
    )

    assert report.learning_unit_gap_seconds == 0.0
    assert report.learning_unit_overlap_seconds == 0.0
    assert report.learning_unit_outside_media_seconds == 50.0
    assert "learning_unit_timeline_outside_media" in {
        finding.code for finding in report.findings
    }


def test_source_universe_binds_sequence_to_chronological_order() -> None:
    universe = _valid_universe()
    intervals = (
        universe.timeline_intervals[1].model_copy(update={"sequence": 1}),
        universe.timeline_intervals[0].model_copy(update={"sequence": 2}),
    )

    report = SourceUniverseAuditor().audit(
        universe.model_copy(update={"timeline_intervals": intervals})
    )

    assert "timeline_chronology_mismatch" in {
        finding.code for finding in report.findings
    }


def test_source_universe_rejects_duplicate_cycles_and_content_mismatch() -> None:
    universe = _valid_universe()
    first_duplicate = SourceEvidenceSpan(
        schema_version=1,
        span_id="duplicate-a",
        lecture_id="lecture-001",
        source_artifact_id="transcript",
        source_record_id="duplicate-record-a",
        modality=EvidenceModality.TRANSCRIPT,
        start_time=30.0,
        end_time=40.0,
        classification=SourceSpanClassification.DUPLICATE,
        duplicate_of_span_id="duplicate-b",
        content_sha256="b" * 64,
    )
    second_duplicate = first_duplicate.model_copy(
        update={
            "span_id": "duplicate-b",
            "source_record_id": "duplicate-record-b",
            "duplicate_of_span_id": "duplicate-a",
        }
    )
    mismatched_duplicate = first_duplicate.model_copy(
        update={
            "span_id": "duplicate-c",
            "source_record_id": "duplicate-record-c",
            "duplicate_of_span_id": "span-001",
        }
    )
    intervals = (
        universe.timeline_intervals[0].model_copy(
            update={
                "evidence_span_ids": (
                    "span-001",
                    "duplicate-a",
                    "duplicate-b",
                    "duplicate-c",
                )
            }
        ),
        universe.timeline_intervals[1],
    )

    report = SourceUniverseAuditor().audit(
        universe.model_copy(
            update={
                "timeline_intervals": intervals,
                "evidence_spans": (
                    *universe.evidence_spans,
                    first_duplicate,
                    second_duplicate,
                    mismatched_duplicate,
                ),
            }
        )
    )

    assert {finding.code for finding in report.findings} >= {
        "duplicate_source_span_chain",
        "duplicate_source_span_content_mismatch",
    }


def test_source_universe_rejects_remote_context_evidence() -> None:
    universe = _valid_universe()
    units = (
        universe.learning_units[0].model_copy(
            update={"context_source_span_ids": ("span-002",)}
        ),
        universe.learning_units[1],
    )

    report = SourceUniverseAuditor().audit(
        universe.model_copy(update={"learning_units": units})
    )

    assert report.out_of_window_context_span_count == 1
    assert "context_spans_outside_learning_unit_policy" in {
        finding.code for finding in report.findings
    }


def test_source_universe_accepts_context_with_explicit_boundary_extension() -> None:
    universe = _valid_universe()
    units = (
        universe.learning_units[0].model_copy(
            update={"context_source_span_ids": ("span-002",)}
        ),
        universe.learning_units[1],
    )

    report = SourceUniverseAuditor(maximum_context_extension_seconds=20.0).audit(
        universe.model_copy(update={"learning_units": units})
    )

    assert report.passed is True
    assert report.out_of_window_context_span_count == 0


def test_transcript_span_requires_positive_duration() -> None:
    with pytest.raises(ValidationError, match="transcript evidence"):
        _span("point-transcript", 10.0, 10.0)


def test_schema_version_rejects_boolean_alias() -> None:
    data = _valid_universe().model_dump(mode="python")
    data["schema_version"] = True

    with pytest.raises(ValidationError, match="schema_version"):
        SourceUniverse.model_validate(data)

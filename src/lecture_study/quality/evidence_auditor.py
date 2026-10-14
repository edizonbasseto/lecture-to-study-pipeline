"""Deterministic completeness checks for lecture evidence packs."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from lecture_study.domain.models import (
    EvidencePack,
    LectureTranscriptionResult,
    TranscriptionSegment,
)

_SegmentKey = tuple[float, float, str]


class ContentQualityError(RuntimeError):
    """Raised when canonical content fails a blocking quality gate."""


@dataclass(frozen=True, slots=True)
class QualityFinding:
    """One deterministic violation of the content completeness contract."""

    code: str
    message: str
    affected_pack_sequences: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceQualityReport:
    """Auditable result of comparing source transcription and evidence packs."""

    lecture_id: str
    expected_pack_sequences: tuple[int, ...]
    observed_pack_sequences: tuple[int, ...]
    source_segment_count: int
    assigned_segment_count: int
    missing_segment_count: int
    unexpected_segment_count: int
    findings: tuple[QualityFinding, ...]

    @property
    def passed(self) -> bool:
        return not self.findings

    def require_passed(self) -> None:
        if self.passed:
            return
        codes = ", ".join(finding.code for finding in self.findings)
        raise ContentQualityError(
            f"Lecture {self.lecture_id} failed content quality gates: {codes}"
        )


class EvidenceQualityAuditor:
    """Validates losslessness, unit duration, identity, and temporal coverage."""

    def __init__(
        self,
        *,
        minimum_unit_seconds: float = 300.0,
        maximum_unit_seconds: float = 600.0,
        media_duration_tolerance_seconds: float = 0.001,
        boundary_tolerance_seconds: float = 0.001,
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
            not math.isfinite(media_duration_tolerance_seconds)
            or media_duration_tolerance_seconds < 0
        ):
            raise ValueError(
                "media_duration_tolerance_seconds must be finite and not negative"
            )
        if (
            not math.isfinite(boundary_tolerance_seconds)
            or boundary_tolerance_seconds < 0
        ):
            raise ValueError(
                "boundary_tolerance_seconds must be finite and not negative"
            )

        self._minimum_unit_seconds = minimum_unit_seconds
        self._maximum_unit_seconds = maximum_unit_seconds
        self._media_duration_tolerance_seconds = media_duration_tolerance_seconds
        self._boundary_tolerance_seconds = boundary_tolerance_seconds

    def audit(
        self,
        *,
        transcription: LectureTranscriptionResult,
        packs: Sequence[EvidencePack],
        expected_pack_sequences: Sequence[int],
        expected_duration_seconds: float,
    ) -> EvidenceQualityReport:
        if (
            not math.isfinite(expected_duration_seconds)
            or expected_duration_seconds <= 0
        ):
            raise ValueError("expected_duration_seconds must be finite and positive")

        expected_sequences = tuple(expected_pack_sequences)
        observed_sequences = tuple(pack.pack_sequence for pack in packs)
        source_segments = Counter(
            self._segment_key(segment) for segment in transcription.segments
        )
        assigned_segments = Counter(
            self._segment_key(segment) for pack in packs for segment in pack.segments
        )

        findings: list[QualityFinding] = []
        self._audit_expected_sequences(expected_sequences, findings)
        self._audit_lecture_identity(transcription, packs, findings)
        self._audit_pack_sequences(expected_sequences, observed_sequences, findings)
        self._audit_source_segments(transcription, findings)
        self._audit_segment_coverage(source_segments, assigned_segments, findings)
        self._audit_pack_contents(packs, findings)
        self._audit_numeric_values(transcription, packs, findings)
        self._audit_segment_bounds(packs, expected_duration_seconds, findings)
        self._audit_unit_durations(packs, findings)
        self._audit_timeline(packs, expected_duration_seconds, findings)

        missing_segments = source_segments - assigned_segments
        unexpected_segments = assigned_segments - source_segments
        return EvidenceQualityReport(
            lecture_id=str(transcription.lecture_id),
            expected_pack_sequences=expected_sequences,
            observed_pack_sequences=observed_sequences,
            source_segment_count=sum(source_segments.values()),
            assigned_segment_count=sum(assigned_segments.values()),
            missing_segment_count=sum(missing_segments.values()),
            unexpected_segment_count=sum(unexpected_segments.values()),
            findings=tuple(findings),
        )

    @staticmethod
    def _segment_key(segment: TranscriptionSegment) -> _SegmentKey:
        return (segment.start_time, segment.end_time, segment.text)

    @staticmethod
    def _audit_expected_sequences(
        expected_sequences: tuple[int, ...],
        findings: list[QualityFinding],
    ) -> None:
        if not expected_sequences:
            findings.append(
                QualityFinding(
                    code="missing_expected_pack_manifest",
                    message="The authoritative pack manifest is empty.",
                )
            )
            return

        expected_contiguous = tuple(range(1, len(expected_sequences) + 1))
        if expected_sequences != expected_contiguous:
            findings.append(
                QualityFinding(
                    code="invalid_expected_pack_manifest",
                    message=(
                        "Expected pack sequences must be unique, ordered, and contiguous "
                        "from one."
                    ),
                )
            )

    @staticmethod
    def _audit_lecture_identity(
        transcription: LectureTranscriptionResult,
        packs: Sequence[EvidencePack],
        findings: list[QualityFinding],
    ) -> None:
        mismatched_sequences = tuple(
            pack.pack_sequence
            for pack in packs
            if pack.lecture_id != transcription.lecture_id
        )
        if mismatched_sequences:
            findings.append(
                QualityFinding(
                    code="lecture_identity_mismatch",
                    message="Evidence packs belong to a different lecture.",
                    affected_pack_sequences=mismatched_sequences,
                )
            )

    @staticmethod
    def _audit_pack_sequences(
        expected_sequences: tuple[int, ...],
        observed_sequences: tuple[int, ...],
        findings: list[QualityFinding],
    ) -> None:
        if expected_sequences == observed_sequences:
            return

        findings.append(
            QualityFinding(
                code="pack_sequence_mismatch",
                message=(
                    f"Expected pack sequences {expected_sequences}, observed "
                    f"{observed_sequences}."
                ),
            )
        )

    @staticmethod
    def _audit_source_segments(
        transcription: LectureTranscriptionResult,
        findings: list[QualityFinding],
    ) -> None:
        if not transcription.segments:
            findings.append(
                QualityFinding(
                    code="empty_source_transcription",
                    message=(
                        "Canonical material cannot be generated from an empty transcript."
                    ),
                )
            )
            return

        blank_segment_count = sum(
            1 for segment in transcription.segments if not segment.text.strip()
        )
        if blank_segment_count:
            findings.append(
                QualityFinding(
                    code="blank_transcript_segments",
                    message=(
                        f"{blank_segment_count} transcript segments contain no text."
                    ),
                )
            )

    @staticmethod
    def _audit_segment_coverage(
        source_segments: Counter[_SegmentKey],
        assigned_segments: Counter[_SegmentKey],
        findings: list[QualityFinding],
    ) -> None:
        missing_count = sum((source_segments - assigned_segments).values())
        unexpected_count = sum((assigned_segments - source_segments).values())

        if missing_count:
            findings.append(
                QualityFinding(
                    code="missing_transcript_segments",
                    message=f"{missing_count} source transcript segments were not assigned.",
                )
            )
        if unexpected_count:
            findings.append(
                QualityFinding(
                    code="unexpected_transcript_segments",
                    message=(
                        f"{unexpected_count} assigned segments are duplicated or absent "
                        "from the source transcript."
                    ),
                )
            )

    @staticmethod
    def _audit_pack_contents(
        packs: Sequence[EvidencePack],
        findings: list[QualityFinding],
    ) -> None:
        empty_sequences = tuple(
            pack.pack_sequence for pack in packs if not pack.segments
        )
        if empty_sequences:
            findings.append(
                QualityFinding(
                    code="empty_evidence_packs",
                    message="Every conceptual unit must contain source transcript evidence.",
                    affected_pack_sequences=empty_sequences,
                )
            )

        partially_contained_sequences = tuple(
            pack.pack_sequence
            for pack in packs
            if any(
                segment.start_time < pack.start_time or segment.end_time > pack.end_time
                for segment in pack.segments
            )
        )
        if partially_contained_sequences:
            findings.append(
                QualityFinding(
                    code="segments_not_contained_by_pack",
                    message=(
                        "Primary transcript ownership requires every segment to be fully "
                        "contained by its evidence pack."
                    ),
                    affected_pack_sequences=partially_contained_sequences,
                )
            )

    @staticmethod
    def _audit_numeric_values(
        transcription: LectureTranscriptionResult,
        packs: Sequence[EvidencePack],
        findings: list[QualityFinding],
    ) -> None:
        has_non_finite_value = any(
            not math.isfinite(value)
            for segment in transcription.segments
            for value in (segment.start_time, segment.end_time)
        ) or any(
            not math.isfinite(value)
            for pack in packs
            for value in (pack.start_time, pack.end_time)
        )
        if has_non_finite_value:
            findings.append(
                QualityFinding(
                    code="non_finite_timestamps",
                    message="All transcript and evidence timestamps must be finite.",
                )
            )

    def _audit_segment_bounds(
        self,
        packs: Sequence[EvidencePack],
        expected_duration_seconds: float,
        findings: list[QualityFinding],
    ) -> None:
        invalid_sequences = tuple(
            pack.pack_sequence
            for pack in packs
            if any(
                segment.start_time < -self._boundary_tolerance_seconds
                or segment.end_time
                > expected_duration_seconds + self._media_duration_tolerance_seconds
                for segment in pack.segments
            )
        )
        if invalid_sequences:
            findings.append(
                QualityFinding(
                    code="segments_outside_media_timeline",
                    message="Transcript segments exceed the certified media timeline.",
                    affected_pack_sequences=invalid_sequences,
                )
            )

    def _audit_unit_durations(
        self,
        packs: Sequence[EvidencePack],
        findings: list[QualityFinding],
    ) -> None:
        invalid_sequences = tuple(
            pack.pack_sequence
            for pack in packs
            if not self._unit_duration_is_acceptable(pack, packs)
        )
        if invalid_sequences:
            findings.append(
                QualityFinding(
                    code="unit_duration_out_of_range",
                    message=(
                        "Every conceptual unit must remain within the configured duration "
                        "range."
                    ),
                    affected_pack_sequences=invalid_sequences,
                )
            )

    def _unit_duration_is_acceptable(
        self,
        pack: EvidencePack,
        packs: Sequence[EvidencePack],
    ) -> bool:
        duration = pack.end_time - pack.start_time
        if self._minimum_unit_seconds <= duration <= self._maximum_unit_seconds:
            return True
        return (
            pack.duration_exception is not None
            and len(packs) == 1
            and duration < self._minimum_unit_seconds
        )

    def _audit_timeline(
        self,
        packs: Sequence[EvidencePack],
        expected_duration_seconds: float,
        findings: list[QualityFinding],
    ) -> None:
        if not packs:
            findings.append(
                QualityFinding(
                    code="missing_evidence_packs",
                    message="No evidence packs were produced for the lecture.",
                )
            )
            return

        ordered_packs = sorted(packs, key=lambda pack: pack.start_time)
        first_pack = ordered_packs[0]
        last_pack = ordered_packs[-1]

        if (
            abs(first_pack.start_time) > self._boundary_tolerance_seconds
            or abs(last_pack.end_time - expected_duration_seconds)
            > self._media_duration_tolerance_seconds
        ):
            findings.append(
                QualityFinding(
                    code="incomplete_lecture_timeline",
                    message=(
                        "Evidence packs do not cover the lecture from zero to the expected "
                        "media duration."
                    ),
                )
            )

        discontinuity_sequences = tuple(
            current.pack_sequence
            for previous, current in zip(ordered_packs, ordered_packs[1:])
            if abs(previous.end_time - current.start_time)
            > self._boundary_tolerance_seconds
        )
        if discontinuity_sequences:
            findings.append(
                QualityFinding(
                    code="evidence_timeline_discontinuity",
                    message="Evidence pack windows contain a temporal gap or overlap.",
                    affected_pack_sequences=discontinuity_sequences,
                )
            )

        ordered_sequences = tuple(pack.pack_sequence for pack in ordered_packs)
        expected_sequences = tuple(range(1, len(ordered_packs) + 1))
        if ordered_sequences != expected_sequences:
            findings.append(
                QualityFinding(
                    code="pack_sequence_timeline_mismatch",
                    message=(
                        "Pack sequence numbers must match strict chronological order."
                    ),
                )
            )

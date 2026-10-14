from __future__ import annotations

import pytest

from lecture_study.domain.models import (
    DurationException,
    DurationExceptionReason,
    EvidencePack,
    LectureTranscriptionResult,
    TranscriptionSegment,
)
from lecture_study.quality import (
    ArtifactFingerprint,
    ContentDimension,
    ContentDensityAuditor,
    ContentDensityPolicy,
    ContentQualityError,
    EvidenceQualityAuditor,
    EvaluatorIdentity,
    ManifestQualityAuditor,
    PromptBudget,
    PromptBudgetExceededError,
    PropositionCoverage,
    PropositionCoverageAuditor,
    PropositionRequirement,
    StageManifest,
    StageName,
)

_GENERATOR_IDENTITY = EvaluatorIdentity(
    model_sha256="a" * 64,
    prompt_sha256="b" * 64,
    engine_version="llama.cpp-1",
)
_EVALUATOR_IDENTITY = EvaluatorIdentity(
    model_sha256="c" * 64,
    prompt_sha256="d" * 64,
    engine_version="llama.cpp-1",
)


def _segment(start_time: float, end_time: float, text: str) -> TranscriptionSegment:
    return TranscriptionSegment(
        start_time=start_time,
        end_time=end_time,
        text=text,
    )


def _transcription(
    segments: list[TranscriptionSegment],
) -> LectureTranscriptionResult:
    return LectureTranscriptionResult(
        lecture_id="lecture-001",
        frames=[],
        segments=segments,
    )


def _pack(
    sequence: int,
    start_time: float,
    end_time: float,
    segments: list[TranscriptionSegment],
) -> EvidencePack:
    return EvidencePack(
        lecture_id="lecture-001",
        pack_sequence=sequence,
        start_time=start_time,
        end_time=end_time,
        segments=segments,
    )


def test_evidence_auditor_passes_lossless_contiguous_conceptual_units() -> None:
    first = _segment(10.0, 20.0, "First proposition.")
    second = _segment(310.0, 320.0, "Second proposition.")
    report = EvidenceQualityAuditor().audit(
        transcription=_transcription([first, second]),
        packs=[
            _pack(1, 0.0, 300.0, [first]),
            _pack(2, 300.0, 600.0, [second]),
        ],
        expected_pack_sequences=[1, 2],
        expected_duration_seconds=600.0,
    )

    assert report.passed is True
    assert report.findings == ()
    report.require_passed()


def test_evidence_auditor_rejects_missing_and_duplicated_segments() -> None:
    first = _segment(10.0, 20.0, "First proposition.")
    second = _segment(310.0, 320.0, "Second proposition.")
    report = EvidenceQualityAuditor().audit(
        transcription=_transcription([first, second]),
        packs=[
            _pack(1, 0.0, 300.0, [first, first]),
            _pack(2, 300.0, 600.0, []),
        ],
        expected_pack_sequences=[1, 2],
        expected_duration_seconds=600.0,
    )

    assert report.missing_segment_count == 1
    assert report.unexpected_segment_count == 1
    assert {finding.code for finding in report.findings} >= {
        "missing_transcript_segments",
        "unexpected_transcript_segments",
        "empty_evidence_packs",
    }
    with pytest.raises(ContentQualityError, match="missing_transcript_segments"):
        report.require_passed()


def test_evidence_auditor_rejects_missing_manifest_tail() -> None:
    first = _segment(10.0, 20.0, "First proposition.")
    report = EvidenceQualityAuditor().audit(
        transcription=_transcription([first]),
        packs=[_pack(1, 0.0, 600.0, [first])],
        expected_pack_sequences=[1, 2],
        expected_duration_seconds=600.0,
    )

    assert "pack_sequence_mismatch" in {finding.code for finding in report.findings}


def _short_lecture_audit(duration_exception: DurationException | None, pack_count: int):
    first = _segment(0.0, 120.0, "First proposition.")
    packs = [
        EvidencePack(
            lecture_id="lecture-001",
            pack_sequence=1,
            start_time=0.0,
            end_time=120.0,
            segments=[first],
            duration_exception=duration_exception,
        )
    ]
    segments = [first]
    if pack_count == 2:
        second = _segment(120.0, 240.0, "Second proposition.")
        segments.append(second)
        packs.append(_pack(2, 120.0, 240.0, [second]))
    return EvidenceQualityAuditor().audit(
        transcription=_transcription(segments),
        packs=packs,
        expected_pack_sequences=list(range(1, pack_count + 1)),
        expected_duration_seconds=packs[-1].end_time,
    )


_SHORT_LECTURE_EXCEPTION = DurationException(
    reason=DurationExceptionReason.LECTURE_SHORTER_THAN_MINIMUM_UNIT
)


def test_evidence_auditor_accepts_marked_single_short_lecture_pack() -> None:
    report = _short_lecture_audit(_SHORT_LECTURE_EXCEPTION, pack_count=1)

    assert report.passed is True


def test_evidence_auditor_rejects_unmarked_single_short_pack() -> None:
    report = _short_lecture_audit(None, pack_count=1)

    assert "unit_duration_out_of_range" in {f.code for f in report.findings}


def test_evidence_auditor_rejects_marker_on_pack_of_multi_pack_lecture() -> None:
    report = _short_lecture_audit(_SHORT_LECTURE_EXCEPTION, pack_count=2)

    assert "unit_duration_out_of_range" in {f.code for f in report.findings}


def test_evidence_auditor_rejects_short_long_and_discontinuous_units() -> None:
    first = _segment(10.0, 20.0, "First proposition.")
    second = _segment(710.0, 720.0, "Second proposition.")
    report = EvidenceQualityAuditor().audit(
        transcription=_transcription([first, second]),
        packs=[
            _pack(1, 0.0, 120.0, [first]),
            _pack(2, 700.0, 1400.0, [second]),
        ],
        expected_pack_sequences=[1, 2],
        expected_duration_seconds=1400.0,
    )

    findings_by_code = {finding.code: finding for finding in report.findings}
    assert findings_by_code["unit_duration_out_of_range"].affected_pack_sequences == (
        1,
        2,
    )
    assert findings_by_code[
        "evidence_timeline_discontinuity"
    ].affected_pack_sequences == (2,)


def test_prompt_budget_accepts_prompt_that_fits_with_margin() -> None:
    budget = PromptBudget(
        input_tokens=7000,
        output_reserve_tokens=900,
        context_window_tokens=8192,
        safety_margin_tokens=128,
    )

    assert budget.required_tokens == 8028
    assert budget.available_tokens == 164
    assert budget.fits is True
    budget.require_fits()


def test_prompt_budget_blocks_inference_before_context_overflow() -> None:
    budget = PromptBudget(
        input_tokens=7799,
        output_reserve_tokens=1024,
        context_window_tokens=4096,
        safety_margin_tokens=128,
    )

    assert budget.fits is False
    with pytest.raises(
        PromptBudgetExceededError,
        match="requires 8951 tokens",
    ):
        budget.require_fits()


def test_proposition_coverage_requires_every_claim_and_depth_dimension() -> None:
    requirements = [
        PropositionRequirement(
            proposition_id="p-001",
            required_dimensions=frozenset(
                {
                    ContentDimension.CORE_STATEMENT,
                    ContentDimension.MECHANISM,
                    ContentDimension.EXCEPTIONS,
                }
            ),
            source_evidence_references=("source-segment-001",),
        ),
        PropositionRequirement(
            proposition_id="p-002",
            required_dimensions=frozenset(
                {
                    ContentDimension.CORE_STATEMENT,
                    ContentDimension.SAFETY,
                }
            ),
            source_evidence_references=("source-segment-002",),
            safety_critical=True,
        ),
    ]
    coverage = [
        PropositionCoverage(
            proposition_id="p-001",
            covered_dimensions=frozenset(
                {
                    ContentDimension.CORE_STATEMENT,
                    ContentDimension.MECHANISM,
                    ContentDimension.EXCEPTIONS,
                }
            ),
            material_references=("unit-001#paragraph-2",),
        ),
        PropositionCoverage(
            proposition_id="p-002",
            covered_dimensions=frozenset(
                {
                    ContentDimension.CORE_STATEMENT,
                    ContentDimension.SAFETY,
                }
            ),
            material_references=("unit-001#warning-1",),
        ),
    ]

    report = PropositionCoverageAuditor().audit(
        requirements=requirements,
        coverage=coverage,
        known_source_evidence_references=(
            "source-segment-001",
            "source-segment-002",
        ),
        known_material_references=(
            "unit-001#paragraph-2",
            "unit-001#warning-1",
        ),
        generator_identity=_GENERATOR_IDENTITY,
        evaluator_identity=_EVALUATOR_IDENTITY,
    )

    assert report.passed is True
    assert report.proposition_recall == 1.0
    report.require_passed()


def test_proposition_coverage_rejects_grounded_but_shallow_summary() -> None:
    requirements = [
        PropositionRequirement(
            proposition_id="p-001",
            required_dimensions=frozenset(
                {
                    ContentDimension.CORE_STATEMENT,
                    ContentDimension.MECHANISM,
                    ContentDimension.EXCEPTIONS,
                }
            ),
            source_evidence_references=("source-segment-001",),
        ),
        PropositionRequirement(
            proposition_id="p-002",
            required_dimensions=frozenset(
                {
                    ContentDimension.CORE_STATEMENT,
                    ContentDimension.SAFETY,
                }
            ),
            source_evidence_references=("source-segment-002",),
            safety_critical=True,
        ),
    ]
    coverage = [
        PropositionCoverage(
            proposition_id="p-001",
            covered_dimensions=frozenset({ContentDimension.CORE_STATEMENT}),
            material_references=("unit-001#paragraph-1",),
        )
    ]

    report = PropositionCoverageAuditor().audit(
        requirements=requirements,
        coverage=coverage,
        known_source_evidence_references=(
            "source-segment-001",
            "source-segment-002",
        ),
        known_material_references=("unit-001#paragraph-1",),
        generator_identity=_GENERATOR_IDENTITY,
        evaluator_identity=_EVALUATOR_IDENTITY,
    )

    assert report.proposition_recall == 0.0
    assert report.missing_proposition_ids == ("p-002",)
    assert report.missing_dimensions_by_proposition == (
        (
            "p-001",
            (
                ContentDimension.EXCEPTIONS,
                ContentDimension.MECHANISM,
            ),
        ),
    )
    assert {finding.code for finding in report.findings} >= {
        "missing_source_propositions",
        "incomplete_analytical_depth",
        "missing_safety_critical_content",
    }
    with pytest.raises(ContentQualityError, match="missing_source_propositions"):
        report.require_passed()


def test_manifest_auditor_accepts_exact_artifacts_and_execution_identity() -> None:
    digest_a = "a" * 64
    digest_b = "b" * 64
    manifest = StageManifest(
        lecture_id="lecture-001",
        stage_name=StageName.SEMANTIC_LEDGER,
        implementation_version="2",
        code_revision="abc123",
        engine_version="llama.cpp-1",
        parent_manifest_sha256=digest_b,
        configuration_sha256=digest_a,
        input_artifacts=(ArtifactFingerprint("pack-001", digest_a),),
        output_artifacts=(ArtifactFingerprint("ledger-001", digest_b),),
        model_sha256=digest_b,
        prompt_sha256=digest_a,
        schema_sha256=digest_b,
    )

    report = ManifestQualityAuditor().audit(
        manifest=manifest,
        observed_input_artifacts=manifest.input_artifacts,
        observed_output_artifacts=manifest.output_artifacts,
        lecture_id="lecture-001",
        stage_name=StageName.SEMANTIC_LEDGER,
        implementation_version="2",
        code_revision="abc123",
        engine_version="llama.cpp-1",
        parent_manifest_sha256=digest_b,
        configuration_sha256=digest_a,
        model_sha256=digest_b,
        prompt_sha256=digest_a,
        schema_sha256=digest_b,
    )

    assert report.passed is True
    report.require_passed()


def test_manifest_auditor_rejects_stale_count_based_resume() -> None:
    digest_a = "a" * 64
    digest_b = "b" * 64
    manifest = StageManifest(
        lecture_id="lecture-001",
        stage_name=StageName.SEMANTIC_LEDGER,
        implementation_version="2",
        code_revision="abc123",
        engine_version="llama.cpp-1",
        parent_manifest_sha256=digest_b,
        configuration_sha256=digest_a,
        input_artifacts=(
            ArtifactFingerprint("pack-001", digest_a),
            ArtifactFingerprint("pack-002", digest_b),
        ),
        output_artifacts=(
            ArtifactFingerprint("ledger-001", digest_a),
            ArtifactFingerprint("ledger-002", digest_b),
        ),
        model_sha256=digest_a,
        prompt_sha256=digest_a,
        schema_sha256=digest_a,
    )
    observed_outputs = (
        ArtifactFingerprint("ledger-001", digest_a),
        ArtifactFingerprint("ledger-099", digest_b),
    )

    report = ManifestQualityAuditor().audit(
        manifest=manifest,
        observed_input_artifacts=manifest.input_artifacts,
        observed_output_artifacts=observed_outputs,
        lecture_id="lecture-001",
        stage_name=StageName.SEMANTIC_LEDGER,
        implementation_version="2",
        code_revision="abc123",
        engine_version="llama.cpp-1",
        parent_manifest_sha256=digest_b,
        configuration_sha256=digest_a,
        model_sha256=digest_a,
        prompt_sha256=digest_a,
        schema_sha256=digest_a,
    )

    finding_codes = {finding.code for finding in report.findings}
    assert finding_codes >= {
        "missing_manifest_output_artifacts",
        "unexpected_manifest_output_artifacts",
    }
    with pytest.raises(ContentQualityError, match="missing_manifest_output"):
        report.require_passed()


def test_manifest_auditor_rejects_changed_model_prompt_or_schema() -> None:
    digest_a = "a" * 64
    digest_b = "b" * 64
    manifest = StageManifest(
        lecture_id="lecture-001",
        stage_name=StageName.CRITIC,
        implementation_version="2",
        code_revision="abc123",
        engine_version="llama.cpp-1",
        parent_manifest_sha256=digest_b,
        configuration_sha256=digest_a,
        input_artifacts=(ArtifactFingerprint("draft-001", digest_a),),
        output_artifacts=(ArtifactFingerprint("review-001", digest_b),),
        model_sha256=digest_a,
        prompt_sha256=digest_a,
        schema_sha256=digest_a,
    )

    report = ManifestQualityAuditor().audit(
        manifest=manifest,
        observed_input_artifacts=manifest.input_artifacts,
        observed_output_artifacts=manifest.output_artifacts,
        lecture_id="lecture-001",
        stage_name=StageName.CRITIC,
        implementation_version="2",
        code_revision="abc123",
        engine_version="llama.cpp-1",
        parent_manifest_sha256=digest_b,
        configuration_sha256=digest_a,
        model_sha256=digest_b,
        prompt_sha256=digest_b,
        schema_sha256=digest_b,
    )

    assert {finding.code for finding in report.findings} == {
        "execution_identity_mismatch"
    }


def test_evidence_auditor_rejects_reordered_sequences_and_small_gap() -> None:
    first = _segment(10.0, 20.0, "First proposition.")
    second = _segment(320.0, 330.0, "Second proposition.")
    report = EvidenceQualityAuditor().audit(
        transcription=_transcription([first, second]),
        packs=[
            _pack(2, 0.0, 300.0, [first]),
            _pack(1, 300.5, 600.5, [second]),
        ],
        expected_pack_sequences=[1, 2],
        expected_duration_seconds=600.5,
    )

    assert {finding.code for finding in report.findings} >= {
        "pack_sequence_mismatch",
        "pack_sequence_timeline_mismatch",
        "evidence_timeline_discontinuity",
    }


def test_evidence_auditor_rejects_blank_text_and_media_overrun() -> None:
    blank = _segment(10.0, 20.0, "   ")
    overrun = _segment(595.0, 599.0, "Content after certified media end.")
    report = EvidenceQualityAuditor().audit(
        transcription=_transcription([blank, overrun]),
        packs=[
            _pack(1, 0.0, 300.0, [blank]),
            _pack(2, 300.0, 600.0, [overrun]),
        ],
        expected_pack_sequences=[1, 2],
        expected_duration_seconds=590.0,
    )

    assert {finding.code for finding in report.findings} >= {
        "blank_transcript_segments",
        "segments_outside_media_timeline",
    }


def test_evidence_auditor_rejects_non_finite_media_duration() -> None:
    segment = _segment(10.0, 20.0, "Proposition.")

    with pytest.raises(ValueError, match="finite and positive"):
        EvidenceQualityAuditor().audit(
            transcription=_transcription([segment]),
            packs=[_pack(1, 0.0, 300.0, [segment])],
            expected_pack_sequences=[1],
            expected_duration_seconds=float("nan"),
        )


def test_proposition_coverage_rejects_unresolved_or_non_independent_evidence() -> None:
    requirement = PropositionRequirement(
        proposition_id="p-001",
        required_dimensions=frozenset({ContentDimension.CORE_STATEMENT}),
        source_evidence_references=("missing-source-span",),
    )
    coverage = PropositionCoverage(
        proposition_id="p-001",
        covered_dimensions=frozenset({ContentDimension.CORE_STATEMENT}),
        material_references=("missing-material-span",),
    )

    report = PropositionCoverageAuditor().audit(
        requirements=[requirement],
        coverage=[coverage],
        known_source_evidence_references=(),
        known_material_references=(),
        generator_identity=_GENERATOR_IDENTITY,
        evaluator_identity=_GENERATOR_IDENTITY,
    )

    assert {finding.code for finding in report.findings} >= {
        "unresolved_source_evidence_references",
        "unresolved_material_references",
        "non_independent_evaluator",
    }


def test_manifest_auditor_rejects_wrong_lecture_stage_or_code_revision() -> None:
    digest_a = "a" * 64
    digest_b = "b" * 64
    manifest = StageManifest(
        lecture_id="lecture-001",
        stage_name=StageName.CRITIC,
        implementation_version="2",
        code_revision="abc123",
        engine_version="llama.cpp-1",
        parent_manifest_sha256=digest_b,
        configuration_sha256=digest_a,
        input_artifacts=(ArtifactFingerprint("draft-001", digest_a),),
        output_artifacts=(ArtifactFingerprint("review-001", digest_b),),
        model_sha256=digest_a,
        prompt_sha256=digest_a,
        schema_sha256=digest_a,
    )

    report = ManifestQualityAuditor().audit(
        manifest=manifest,
        observed_input_artifacts=manifest.input_artifacts,
        observed_output_artifacts=manifest.output_artifacts,
        lecture_id="lecture-999",
        stage_name=StageName.SEMANTIC_LEDGER,
        implementation_version="2",
        code_revision="changed",
        engine_version="llama.cpp-1",
        parent_manifest_sha256=digest_b,
        configuration_sha256=digest_a,
        model_sha256=digest_a,
        prompt_sha256=digest_a,
        schema_sha256=digest_a,
    )

    assert {finding.code for finding in report.findings} == {
        "execution_identity_mismatch"
    }


def test_two_hour_lecture_cannot_pass_as_two_short_paragraphs() -> None:
    source_text = " ".join(f"source{i}" for i in range(17_258))
    two_short_paragraphs = " ".join(f"summary{i}" for i in range(180))
    policy = ContentDensityPolicy(
        minimum_words_per_source_minute=10,
        minimum_source_to_material_word_ratio=0.1,
    )

    report = ContentDensityAuditor().audit(
        source_text=source_text,
        source_duration_seconds=7200.0,
        material_text=two_short_paragraphs,
        policy=policy,
    )

    assert report.passed is False
    assert report.minimum_material_word_count == 1726
    assert report.material_word_count == 180
    with pytest.raises(ContentQualityError, match="proportionality"):
        report.require_passed()


def test_evidence_auditor_rejects_non_finite_tolerances() -> None:
    with pytest.raises(ValueError, match="finite"):
        EvidenceQualityAuditor(boundary_tolerance_seconds=float("nan"))
    with pytest.raises(ValueError, match="finite"):
        EvidenceQualityAuditor(media_duration_tolerance_seconds=float("inf"))


def test_proposition_coverage_requires_every_known_instructional_span() -> None:
    requirement = PropositionRequirement(
        proposition_id="p-001",
        required_dimensions=frozenset({ContentDimension.CORE_STATEMENT}),
        source_evidence_references=("source-001",),
    )
    coverage = PropositionCoverage(
        proposition_id="p-001",
        covered_dimensions=frozenset({ContentDimension.CORE_STATEMENT}),
        material_references=("material-001",),
    )

    report = PropositionCoverageAuditor().audit(
        requirements=[requirement],
        coverage=[coverage],
        known_source_evidence_references=("source-001", "source-002"),
        known_material_references=("material-001", "material-002"),
        generator_identity=_GENERATOR_IDENTITY,
        evaluator_identity=_EVALUATOR_IDENTITY,
    )

    assert {finding.code for finding in report.findings} >= {
        "uninventoried_source_evidence",
        "unaudited_material_references",
    }


def test_proposition_coverage_compares_model_hashes_not_identity_labels() -> None:
    generator = EvaluatorIdentity(
        model_sha256="a" * 64,
        prompt_sha256="b" * 64,
        engine_version="generator-alias",
    )
    evaluator_alias = EvaluatorIdentity(
        model_sha256="a" * 64,
        prompt_sha256="c" * 64,
        engine_version="different-alias",
    )
    requirement = PropositionRequirement(
        proposition_id="p-001",
        required_dimensions=frozenset({ContentDimension.CORE_STATEMENT}),
        source_evidence_references=("source-001",),
    )
    coverage = PropositionCoverage(
        proposition_id="p-001",
        covered_dimensions=frozenset({ContentDimension.CORE_STATEMENT}),
        material_references=("material-001",),
    )

    report = PropositionCoverageAuditor().audit(
        requirements=[requirement],
        coverage=[coverage],
        known_source_evidence_references=("source-001",),
        known_material_references=("material-001",),
        generator_identity=generator,
        evaluator_identity=evaluator_alias,
    )

    assert "non_independent_evaluator" in {finding.code for finding in report.findings}


def test_llm_stage_manifest_requires_model_prompt_and_schema_hashes() -> None:
    digest = "a" * 64

    with pytest.raises(ValueError, match="model_sha256 is required"):
        StageManifest(
            lecture_id="lecture-001",
            stage_name=StageName.SEMANTIC_LEDGER,
            implementation_version="2",
            code_revision="abc123",
            engine_version="llama.cpp-1",
            parent_manifest_sha256=digest,
            configuration_sha256=digest,
            input_artifacts=(ArtifactFingerprint("pack-001", digest),),
            output_artifacts=(ArtifactFingerprint("ledger-001", digest),),
        )


def test_stage_manifest_rejects_unregistered_stage_alias() -> None:
    digest = "a" * 64

    with pytest.raises(ValueError, match="registered StageName"):
        StageManifest(
            lecture_id="lecture-001",
            stage_name="semantic_ledger",  # type: ignore[arg-type]
            implementation_version="2",
            code_revision="abc123",
            engine_version="llama.cpp-1",
            parent_manifest_sha256=digest,
            configuration_sha256=digest,
            input_artifacts=(ArtifactFingerprint("pack-001", digest),),
            output_artifacts=(ArtifactFingerprint("ledger-001", digest),),
        )

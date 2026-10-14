"""Master Pipeline Orchestrator (Phase 16): drives Phases 2-15 for one
lecture_id end to end (`run_all`), or a single named phase in isolation
(`run_phase`), with stage-specific resume predicates. Early phases inspect
complete artifact sets, certification and deterministic delivery phases
revalidate current authority, and generated derivative phases always run.

Unlike every phase module below it, this module is deliberately NOT
self-contained per ADR 0006's rule: ADR 0006 exists so that phases 2-15
never depend on each other, keeping each one's already-shipped behavior
isolated from a later phase's changes. This module is the composition
root sitting above all of them -- its entire purpose is to depend on
every phase's real orchestrator/generator/compiler class and its real
output-directory constant, not to reimplement or duplicate any of them.
See docs/decisions/0016-master-cli-orchestrator.md.

The LLM-backed stages (semantic extraction, drafting, critic review,
mock exam and podcast generation) run as Codex CLI generation scripts in
child processes (ADR 0038): this module invokes scripts/generate_codex_*
with the lecture_id and surfaces a nonzero exit, timeout or output
overflow as a PipelineExecutionError naming the phase. For those stages
the script's own exit contract is the phase contract -- this module does
not re-verify per-item outcomes, only the resume predicates below check
artifact completeness.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pydantic import TypeAdapter, ValidationError

from lecture_study.assembly.assembler import (
    CANONICAL_DOCUMENTS_OUTPUT_DIR,
    DocumentAssembler,
)
from lecture_study.domain.errors import PipelineExecutionError
from lecture_study.domain.models import LectureMetadata, MockExam, SafeIdentifier
from lecture_study.evidence.pipeline import (
    EVIDENCE_PACKS_OUTPUT_DIR,
    EvidencePackOrchestrator,
)
from lecture_study.exams.compiler import EXAMS_OUTPUT_DIR, ExamCompiler
from lecture_study.extraction.pipeline import (
    CANONICAL_OUTPUT_DIR,
    ExtractionOrchestrator,
)
from lecture_study.flashcards.generator import AnkiDeckGenerator
from lecture_study.metadata.aggregator import METADATA_OUTPUT_DIR, MetadataAggregator
from lecture_study.ops.llm_response_contract import (
    llm_output_diagnostic,
    summarize_validation_issues,
)
from lecture_study.ops.process_lifecycle import (
    ProcessOutputLimitExceeded,
    run_bounded_process,
)
from lecture_study.publishing.pipeline import PublishingOrchestrator
from lecture_study.quality import (
    DeliveryArtifactKind,
    QualityCertificationError,
    is_deterministic_delivery_current,
    is_publication_ready,
    issue_publication_certificate,
    require_publication_ready,
)
from lecture_study.transcription.pipeline import (
    TRANSCRIBED_OUTPUT_DIR,
    TranscriptionOrchestrator,
)
from lecture_study.tts.generator import PodcastAudioGenerator

QUEUE_PATH = Path("workspace/data/queue.json")

# Stage roots for the Codex-script-driven LLM phases (ADR 0038). The
# scripts/generate_codex_* scripts own the same values as their argparse
# defaults for standalone use; a wiring test pins the two together so
# they cannot drift silently.
SEMANTIC_LEDGERS_OUTPUT_DIR = Path("workspace/data/07_semantic_ledgers")
DRAFTS_OUTPUT_DIR = Path("workspace/data/08_drafts")
VALIDATED_UNITS_OUTPUT_DIR = Path("workspace/data/09_validated_units")

_GENERATION_SCRIPTS_ROOT = Path("scripts")
# A whole-lecture Codex generation pass (semantic ledgers, drafts, critic
# with its repair loop) legitimately runs for hours on dense lectures; the
# bound exists so a hung child cannot block the pipeline forever, not to
# pace the run. Output budget mirrors the scripts' own codex-call caps.
_CODEX_PHASE_TIMEOUT_SECONDS: float = 12 * 60 * 60
_CODEX_PHASE_OUTPUT_LIMIT_BYTES = 16 * 1024 * 1024
# The generation scripts consume exactly: PATH (their own subprocess
# resolution), HOME (codex's ~/.codex session, which they re-add to their
# codex children's sanitized env), the TEMP/TMPDIR family (scratch
# directories) and the locale variables. Passing the full ambient
# environment instead would transit every credential the sanitizer's
# blocklist does not recognize (e.g. credentialed proxy URLs).
_CODEX_PHASE_ENV_KEYS = (
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "TEMP",
    "TMP",
    "TMPDIR",
)
# Where a failed generation script's captured output is persisted for
# diagnosis. The message itself carries only digests, matching
# llm_output_diagnostic's never-echo-model-bytes convention.
_CODEX_PHASE_FAILURE_LOGS_DIR = Path("tmp/codex_phase_failures")


def _codex_phase_environment() -> dict[str, str]:
    return {
        name: os.environ[name] for name in _CODEX_PHASE_ENV_KEYS if name in os.environ
    }


_logger = logging.getLogger(__name__)

# Every phase's own real entry point (assemble_lecture, aggregate_lecture,
# publish_lecture, generate_deck, generate_exam, generate_script,
# generate_podcast) validates lecture_id via this same SafeIdentifier
# adapter as its own first statement, before any Path splice. run_phase/
# run_all apply it too, at this module's own single entry choke point --
# every _phaseN_artifact_exists function below also splices lecture_id
# into a Path for a resume check, which runs BEFORE any phase's own
# method (and its own validation) is ever reached.
_LECTURE_ID_ADAPTER: TypeAdapter[str] = TypeAdapter(SafeIdentifier)


@dataclass(frozen=True)
class OrchestratorContext:
    """Runtime configuration threaded through every phase action.

    The context binds the discovery queue and the local Whisper model path
    required by the transcription phase (the only stage that still runs a
    local model, per ADR 0038). The path defaults to None so a missing
    requirement fails loudly instead of selecting a guessed file.
    """

    queue_path: Path = QUEUE_PATH
    whisper_model_path: Path | None = None


@dataclass(frozen=True)
class PhaseResult:
    """Outcome of one phase's participation in a run_all/run_phase call:
    "skipped" when its resume predicate was satisfied, "ran" when this call
    actually executed it."""

    phase_number: int
    phase_name: str
    status: Literal["skipped", "ran"]


@dataclass(frozen=True)
class PhaseSpec:
    """One row of the phase registry: how to evaluate phase_number's
    stage-specific resume predicate, and how to run it."""

    number: int
    name: str
    supports_single_lecture: bool
    artifact_exists: Callable[[OrchestratorContext, str], bool]
    run: Callable[[OrchestratorContext, str], Awaitable[None]]


def _dir_has_files(directory: Path) -> bool:
    return directory.is_dir() and any(directory.iterdir())


def _artifact_sequences(directory: Path, prefix: str) -> frozenset[str]:
    if not directory.is_dir():
        return frozenset()
    suffix_length = len(".json")
    return frozenset(
        path.name[len(prefix) : -suffix_length]
        for path in directory.glob(f"{prefix}*.json")
        if path.is_file()
    )


def _artifact_sequences_match(
    *,
    expected_directory: Path,
    expected_prefix: str,
    actual_directory: Path,
    actual_prefix: str,
) -> bool:
    expected_sequences = _artifact_sequences(expected_directory, expected_prefix)
    actual_sequences = _artifact_sequences(actual_directory, actual_prefix)
    return bool(expected_sequences) and actual_sequences == expected_sequences


def _require_model_path(model_path: Path | None, flag_name: str) -> Path:
    if model_path is None:
        raise PipelineExecutionError(
            f"{flag_name} is required to run this phase, but no path was provided"
        )
    return model_path


def _build_single_lecture_queue(queue_path: Path, lecture_id: str) -> Path:
    """Filters the discovery queue (workspace/data/queue.json, written by
    the `discover` command) down to lecture_id's own LectureMetadata
    entry, written to a fresh temp file -- Phases 2-4's orchestrators each
    require a queue_path constructor argument, not a lecture_id, since
    their own scope is "every lecture currently enqueued". Callers must
    unlink the returned path once the orchestrator run completes."""
    if not queue_path.exists():
        raise PipelineExecutionError(
            f"no queue file at {queue_path}: run the 'discover' command "
            f"first to enqueue lecture {lecture_id!r}"
        )
    payload = json.loads(queue_path.read_text())
    lectures = [LectureMetadata.model_validate(item) for item in payload]
    matching = next((lecture for lecture in lectures if lecture.id == lecture_id), None)
    if matching is None:
        raise PipelineExecutionError(
            f"lecture {lecture_id!r} is not present in {queue_path}: run "
            "the 'discover' command first to enqueue it"
        )
    descriptor, raw_path = tempfile.mkstemp(suffix=".json")
    tmp_path = Path(raw_path)
    try:
        tmp_path.write_text(json.dumps([matching.model_dump(mode="json")]))
    finally:
        os.close(descriptor)
    return tmp_path


class _LectureOutcome(Protocol):
    """Structural shape shared by Phase 2-4's LectureExtractionOutcome /
    LectureTranscriptionOutcome / LectureEvidenceOutcome -- no common
    base class exists, but all three carry these two fields, so any of
    them satisfies this Protocol without needing three separate imports."""

    lecture_id: str
    error: str | None


def _find_outcome_error(
    outcomes: Sequence[_LectureOutcome], lecture_id: str
) -> str | None:
    for outcome in outcomes:
        if outcome.lecture_id == lecture_id:
            return outcome.error
    return f"no outcome reported for lecture {lecture_id!r}"


async def _run_phase_2(ctx: OrchestratorContext, lecture_id: str) -> None:
    queue_path = _build_single_lecture_queue(ctx.queue_path, lecture_id)
    try:
        outcomes = await ExtractionOrchestrator(queue_path=queue_path).run()
    finally:
        queue_path.unlink(missing_ok=True)
    error = _find_outcome_error(outcomes, lecture_id)
    if error is not None:
        raise PipelineExecutionError(f"phase 2 (extraction) failed: {error}")


async def _run_phase_3(ctx: OrchestratorContext, lecture_id: str) -> None:
    model_path = _require_model_path(ctx.whisper_model_path, "--whisper-model-path")
    queue_path = _build_single_lecture_queue(ctx.queue_path, lecture_id)
    try:
        outcomes = await TranscriptionOrchestrator(
            queue_path=queue_path, model_path=model_path
        ).run()
    finally:
        queue_path.unlink(missing_ok=True)
    error = _find_outcome_error(outcomes, lecture_id)
    if error is not None:
        raise PipelineExecutionError(f"phase 3 (transcription) failed: {error}")


async def _run_phase_4(ctx: OrchestratorContext, lecture_id: str) -> None:
    queue_path = _build_single_lecture_queue(ctx.queue_path, lecture_id)
    try:
        outcomes = await EvidencePackOrchestrator(queue_path=queue_path).run()
    finally:
        queue_path.unlink(missing_ok=True)
    error = _find_outcome_error(outcomes, lecture_id)
    if error is not None:
        raise PipelineExecutionError(f"phase 4 (evidence) failed: {error}")


def _run_codex_generation_script(
    script_filename: str,
    lecture_id: str,
    *,
    phase_number: int,
    phase_name: str,
) -> None:
    """Run one scripts/generate_codex_* stage for a lecture as a child
    process and surface any failure as a PipelineExecutionError naming the
    phase. The scripts resolve their workspace roots relative to the
    current working directory, so this inherits the caller's cwd -- the
    same repo-root assumption QUEUE_PATH already makes. The child env is
    the explicit operational allowlist from _codex_phase_environment()
    (PATH, HOME for codex's ~/.codex session, locale, TMPDIR family) --
    never the ambient environment."""
    command = [
        sys.executable,
        str(_GENERATION_SCRIPTS_ROOT / script_filename),
        str(lecture_id),
    ]
    try:
        completed = run_bounded_process(
            command,
            env=_codex_phase_environment(),
            timeout_seconds=_CODEX_PHASE_TIMEOUT_SECONDS,
            maximum_stdout_bytes=_CODEX_PHASE_OUTPUT_LIMIT_BYTES,
            maximum_stderr_bytes=_CODEX_PHASE_OUTPUT_LIMIT_BYTES,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        # Child output is persisted to a local file for diagnosis, never
        # inlined into the message: script stdout/stderr can carry
        # model-generated bytes and full tracebacks (which embed the whole
        # prompt as an argv element), so the message itself carries only
        # digests, per llm_output_diagnostic's convention.
        log_path = _persist_failed_phase_output(
            phase_number, lecture_id, error.stdout, error.stderr
        )
        raise PipelineExecutionError(
            f"phase {phase_number} ({phase_name}) failed for lecture "
            f"{lecture_id!r}: {script_filename} exited with code "
            f"{error.returncode}. stdout "
            f"{llm_output_diagnostic(_child_output_bytes(error.stdout or ''))}; "
            f"stderr "
            f"{llm_output_diagnostic(_child_output_bytes(error.stderr or ''))}; "
            f"full output persisted at {log_path}"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise PipelineExecutionError(
            f"phase {phase_number} ({phase_name}) timed out after "
            f"{_CODEX_PHASE_TIMEOUT_SECONDS}s running {script_filename} for "
            f"lecture {lecture_id!r}"
        ) from error
    except ProcessOutputLimitExceeded as error:
        raise PipelineExecutionError(
            f"phase {phase_number} ({phase_name}) exceeded the "
            f"{_CODEX_PHASE_OUTPUT_LIMIT_BYTES}-byte output budget running "
            f"{script_filename} for lecture {lecture_id!r}"
        ) from error
    if completed.stdout:
        for line in str(completed.stdout).strip().splitlines()[-5:]:
            _logger.info("%s: %s", script_filename, line)


def _child_output_bytes(stream: str | bytes) -> bytes:
    return stream.encode(errors="replace") if isinstance(stream, str) else stream


def _persist_failed_phase_output(
    phase_number: int, lecture_id: str, stdout: str | bytes, stderr: str | bytes
) -> Path:
    log_dir = _CODEX_PHASE_FAILURE_LOGS_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"phase-{phase_number}-{lecture_id}.log"
    log_path.write_bytes(
        b"--- stdout ---\n"
        + _child_output_bytes(stdout)
        + b"\n--- stderr ---\n"
        + _child_output_bytes(stderr)
    )
    return log_path


async def _run_codex_phase(
    script_filename: str,
    lecture_id: str,
    *,
    phase_number: int,
    phase_name: str,
) -> None:
    await asyncio.to_thread(
        _run_codex_generation_script,
        script_filename,
        lecture_id,
        phase_number=phase_number,
        phase_name=phase_name,
    )


async def _run_phase_5(_ctx: OrchestratorContext, lecture_id: str) -> None:
    await _run_codex_phase(
        "generate_codex_semantic_ledgers.py",
        lecture_id,
        phase_number=5,
        phase_name="semantic",
    )


async def _run_phase_6(_ctx: OrchestratorContext, lecture_id: str) -> None:
    await _run_codex_phase(
        "generate_codex_drafts.py", lecture_id, phase_number=6, phase_name="drafting"
    )


async def _run_phase_7(_ctx: OrchestratorContext, lecture_id: str) -> None:
    await _run_codex_phase(
        "generate_codex_critic_review.py",
        lecture_id,
        phase_number=7,
        phase_name="critic",
    )


async def _run_phase_8(ctx: OrchestratorContext, lecture_id: str) -> None:
    DocumentAssembler().assemble_lecture(lecture_id)


async def _run_phase_9(ctx: OrchestratorContext, lecture_id: str) -> None:
    MetadataAggregator().aggregate_lecture(lecture_id)


async def _run_phase_10(ctx: OrchestratorContext, lecture_id: str) -> None:
    issue_publication_certificate(lecture_id)


async def _run_phase_11(ctx: OrchestratorContext, lecture_id: str) -> None:
    PublishingOrchestrator().publish_lecture(lecture_id)


async def _run_phase_12(ctx: OrchestratorContext, lecture_id: str) -> None:
    AnkiDeckGenerator().generate_deck(lecture_id)


async def _run_phase_13(_ctx: OrchestratorContext, lecture_id: str) -> None:
    await _run_codex_phase(
        "generate_codex_mock_exam.py", lecture_id, phase_number=13, phase_name="exams"
    )
    try:
        exam_path = EXAMS_OUTPUT_DIR / lecture_id / "exam.json"
        exam = MockExam.model_validate_json(exam_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as error:
        # A script that exits zero but wrote no (or an invalid) exam must
        # fail the phase here, not surface as a raw filesystem/parser error
        # or let phase 8+ proceed as if an exam existed. The detail is
        # field paths and error types only: exam.json content is
        # model-generated and must not be echoed into the message.
        detail = (
            summarize_validation_issues(error, declared_type=MockExam)
            if isinstance(error, ValidationError)
            else str(error)
        )
        raise PipelineExecutionError(
            f"phase 13 (exams): generate_codex_mock_exam.py exited 0 but "
            f"no valid exam was found for lecture {lecture_id!r} at "
            f"{exam_path}: {detail}"
        ) from error
    ExamCompiler().compile(exam)


async def _run_phase_14(_ctx: OrchestratorContext, lecture_id: str) -> None:
    await _run_codex_phase(
        "generate_codex_podcast.py", lecture_id, phase_number=14, phase_name="podcast"
    )


async def _run_phase_15(ctx: OrchestratorContext, lecture_id: str) -> None:
    PodcastAudioGenerator().generate_podcast(lecture_id)


def _phase_2_artifact_exists(_ctx: OrchestratorContext, lecture_id: str) -> bool:
    return (CANONICAL_OUTPUT_DIR / f"{lecture_id}.json").exists()


def _phase_3_artifact_exists(_ctx: OrchestratorContext, lecture_id: str) -> bool:
    return (TRANSCRIBED_OUTPUT_DIR / f"{lecture_id}_transcription.json").exists()


def _phase_4_artifact_exists(_ctx: OrchestratorContext, lecture_id: str) -> bool:
    return _dir_has_files(EVIDENCE_PACKS_OUTPUT_DIR / lecture_id)


def _phase_5_artifact_exists(_ctx: OrchestratorContext, lecture_id: str) -> bool:
    return _artifact_sequences_match(
        expected_directory=EVIDENCE_PACKS_OUTPUT_DIR / lecture_id,
        expected_prefix="pack_",
        actual_directory=SEMANTIC_LEDGERS_OUTPUT_DIR / lecture_id,
        actual_prefix="ledger_",
    )


def _phase_6_artifact_exists(_ctx: OrchestratorContext, lecture_id: str) -> bool:
    return _artifact_sequences_match(
        expected_directory=EVIDENCE_PACKS_OUTPUT_DIR / lecture_id,
        expected_prefix="pack_",
        actual_directory=DRAFTS_OUTPUT_DIR / lecture_id,
        actual_prefix="draft_",
    )


def _phase_7_artifact_exists(_ctx: OrchestratorContext, lecture_id: str) -> bool:
    return _artifact_sequences_match(
        expected_directory=DRAFTS_OUTPUT_DIR / lecture_id,
        expected_prefix="draft_",
        actual_directory=VALIDATED_UNITS_OUTPUT_DIR / lecture_id,
        actual_prefix="unit_",
    )


def _phase_8_artifact_exists(_ctx: OrchestratorContext, lecture_id: str) -> bool:
    return (
        CANONICAL_DOCUMENTS_OUTPUT_DIR / lecture_id / "canonical_document.json"
    ).exists()


def _phase_9_artifact_exists(_ctx: OrchestratorContext, lecture_id: str) -> bool:
    return (METADATA_OUTPUT_DIR / lecture_id / "global_index.json").exists()


def _phase_10_artifact_exists(_ctx: OrchestratorContext, lecture_id: str) -> bool:
    return is_publication_ready(lecture_id)


def _phase_11_artifact_exists(_ctx: OrchestratorContext, lecture_id: str) -> bool:
    return is_deterministic_delivery_current(
        lecture_id,
        DeliveryArtifactKind.PUBLISHING,
    )


def _phase_12_artifact_exists(_ctx: OrchestratorContext, lecture_id: str) -> bool:
    return is_deterministic_delivery_current(
        lecture_id,
        DeliveryArtifactKind.FLASHCARDS,
    )


def _phase_13_artifact_exists(_ctx: OrchestratorContext, lecture_id: str) -> bool:
    return False


def _phase_14_artifact_exists(_ctx: OrchestratorContext, lecture_id: str) -> bool:
    return False


def _phase_15_artifact_exists(_ctx: OrchestratorContext, lecture_id: str) -> bool:
    return False


PHASE_REGISTRY: tuple[PhaseSpec, ...] = (
    PhaseSpec(2, "extraction", True, _phase_2_artifact_exists, _run_phase_2),
    PhaseSpec(3, "transcription", True, _phase_3_artifact_exists, _run_phase_3),
    PhaseSpec(4, "evidence", True, _phase_4_artifact_exists, _run_phase_4),
    PhaseSpec(5, "semantic", True, _phase_5_artifact_exists, _run_phase_5),
    PhaseSpec(6, "drafting", True, _phase_6_artifact_exists, _run_phase_6),
    PhaseSpec(7, "critic", True, _phase_7_artifact_exists, _run_phase_7),
    PhaseSpec(8, "assembly", True, _phase_8_artifact_exists, _run_phase_8),
    PhaseSpec(9, "metadata", True, _phase_9_artifact_exists, _run_phase_9),
    PhaseSpec(
        10,
        "quality-certification",
        True,
        _phase_10_artifact_exists,
        _run_phase_10,
    ),
    PhaseSpec(11, "publishing", True, _phase_11_artifact_exists, _run_phase_11),
    PhaseSpec(12, "flashcards", True, _phase_12_artifact_exists, _run_phase_12),
    PhaseSpec(13, "exams", True, _phase_13_artifact_exists, _run_phase_13),
    PhaseSpec(14, "podcast", True, _phase_14_artifact_exists, _run_phase_14),
    PhaseSpec(15, "tts", True, _phase_15_artifact_exists, _run_phase_15),
)

RUN_PHASE_CHOICES: tuple[int, ...] = tuple(spec.number for spec in PHASE_REGISTRY)

# ADR 0024: the uncertified personal-study route runs exactly these phases,
# from the Phase 8 canonical document through Phase 15 TTS, and never the
# certification chain (Phase 10) or the deterministic delivery phases
# (11, 12) that assume a certified publication.
PERSONAL_STUDY_PHASE_NUMBERS: tuple[int, ...] = (8, 9, 13, 14, 15)


def _find_spec(phase_number: int) -> PhaseSpec:
    for spec in PHASE_REGISTRY:
        if spec.number == phase_number:
            return spec
    raise PipelineExecutionError(
        f"unknown phase number {phase_number}: valid phases are "
        f"{list(RUN_PHASE_CHOICES)}"
    )


async def run_phase(
    ctx: OrchestratorContext, phase_number: int, lecture_id: str
) -> PhaseResult:
    """Runs exactly one phase for one lecture_id, or skips it if its
    output artifact already exists (resume)."""
    lecture_id = _LECTURE_ID_ADAPTER.validate_python(lecture_id)
    spec = _find_spec(phase_number)
    if not spec.supports_single_lecture:
        raise PipelineExecutionError(
            f"phase {spec.number} ({spec.name}) has no per-lecture entry "
            "point -- it processes every lecture currently pending at its "
            "input stage in one batch. Use run-all, which includes this "
            "phase as part of the full sequence."
        )
    if spec.number > 10:
        try:
            require_publication_ready(str(lecture_id))
        except QualityCertificationError as error:
            if spec.number in PERSONAL_STUDY_PHASE_NUMBERS:
                # 2026-09-15: found while investigating a session's own
                # confusion over this exact failure -- `run-phase` always
                # requires a certified publication for phases beyond 10,
                # by design, regardless of what execution_mode marker an
                # individual phase's own artifact carries. It is not a
                # bug; `run-personal-study` is the separate, correct entry
                # point for this phase's uncertified route (ADR 0024) and
                # never calls require_publication_ready at all. The hint
                # below only fires for a phase actually reachable through
                # that route -- every other phase beyond 10 keeps failing
                # exactly as before, with the original message.
                raise PipelineExecutionError(
                    f"phase {spec.number} ({spec.name}) has no publication "
                    f"certificate for {lecture_id!r}: {error}. If this is "
                    "the uncertified personal-study route (ADR 0024), use "
                    "`run-personal-study`, not `run-phase` -- `run-phase` "
                    "always requires a certified publication for phases "
                    "beyond 10, independent of any execution_mode marker "
                    "on the phase's own artifact."
                ) from error
            raise
    if spec.artifact_exists(ctx, lecture_id):
        _logger.info(
            "phase %d (%s) already done for %s, skipping",
            spec.number,
            spec.name,
            lecture_id,
        )
        return PhaseResult(spec.number, spec.name, "skipped")
    _logger.info("phase %d (%s) running for %s", spec.number, spec.name, lecture_id)
    await spec.run(ctx, lecture_id)
    _logger.info("phase %d (%s) done for %s", spec.number, spec.name, lecture_id)
    return PhaseResult(spec.number, spec.name, "ran")


async def run_all(ctx: OrchestratorContext, lecture_id: str) -> list[PhaseResult]:
    """Runs Phases 2-15 for one lecture_id in order, skipping any phase
    whose output artifact already exists. Stops at the first phase that
    raises: every later phase depends on an earlier phase's own output,
    so continuing past a failure would only surface a confusing
    downstream error instead of the real one."""
    lecture_id = _LECTURE_ID_ADAPTER.validate_python(lecture_id)
    results: list[PhaseResult] = []
    for spec in PHASE_REGISTRY:
        if spec.number > 10:
            require_publication_ready(str(lecture_id))
        if spec.artifact_exists(ctx, lecture_id):
            _logger.info(
                "phase %d (%s) already done for %s, skipping",
                spec.number,
                spec.name,
                lecture_id,
            )
            results.append(PhaseResult(spec.number, spec.name, "skipped"))
            continue
        _logger.info("phase %d (%s) running for %s", spec.number, spec.name, lecture_id)
        await spec.run(ctx, lecture_id)
        _logger.info("phase %d (%s) done for %s", spec.number, spec.name, lecture_id)
        results.append(PhaseResult(spec.number, spec.name, "ran"))
    return results


async def run_personal_study(
    ctx: OrchestratorContext, lecture_id: str
) -> list[PhaseResult]:
    """Runs Phases 8, 9, 13, 14 and 15 for one lecture_id through ADR 0024's
    uncertified personal-study route: a separate, explicitly named entry
    point that never consults a publication certificate and is not
    reachable from run_phase or run_all. Since ADR 0038 the exam and
    podcast phases run the same Codex generation scripts as every other
    route, so the uncertified/certified distinction lives in this entry
    point's phase selection and certification skipping, not in a per-phase
    execution-mode marker. Applies the same resume-and-log shape as
    run_all, restricted to PERSONAL_STUDY_PHASE_NUMBERS, and never calls
    require_publication_ready."""
    lecture_id = _LECTURE_ID_ADAPTER.validate_python(lecture_id)
    results: list[PhaseResult] = []
    for spec in PHASE_REGISTRY:
        if spec.number not in PERSONAL_STUDY_PHASE_NUMBERS:
            continue
        if spec.artifact_exists(ctx, lecture_id):
            _logger.info(
                "phase %d (%s) already done for %s, skipping",
                spec.number,
                spec.name,
                lecture_id,
            )
            results.append(PhaseResult(spec.number, spec.name, "skipped"))
            continue
        _logger.info("phase %d (%s) running for %s", spec.number, spec.name, lecture_id)
        await spec.run(ctx, lecture_id)
        _logger.info("phase %d (%s) done for %s", spec.number, spec.name, lecture_id)
        results.append(PhaseResult(spec.number, spec.name, "ran"))
    return results

"""CLI behavior tests: the Phase 16 master orchestrator (cli/orchestrator.py) and its argparse dispatch
(cli/main.py's run-all/run-phase). All phase classes are mocked; no phase
actually runs (no subprocess, no LLM inference, no real filesystem I/O
beyond tmp_path)."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from lecture_study.cli import main as cli_main
from lecture_study.cli import orchestrator as orchestrator_module
from lecture_study.cli.orchestrator import (
    PHASE_REGISTRY,
    OrchestratorContext,
    PhaseResult,
    PhaseSpec,
    run_all,
    run_phase,
)
from lecture_study.domain.errors import PipelineExecutionError
from lecture_study.domain.models import LectureMetadata, MockExam
from lecture_study.quality import QualityCertificationError

LECTURE = LectureMetadata(
    id="aula-001",
    title="Cardiologia Veterinaria I",
    subject="Cardiologia",
    url="https://example.com/aulas/aula-001",
    estimated_duration_sec=3600.0,
)


def _queue_with(tmp_path: Path, *lectures: LectureMetadata) -> Path:
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(
        json.dumps([lecture.model_dump(mode="json") for lecture in lectures])
    )
    return queue_path


class TestDirHasFiles:
    def test_returns_false_for_a_missing_directory(self, tmp_path: Path):
        assert orchestrator_module._dir_has_files(tmp_path / "missing") is False

    def test_returns_false_for_an_empty_directory(self, tmp_path: Path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        assert orchestrator_module._dir_has_files(empty_dir) is False

    def test_returns_true_when_a_file_is_present(self, tmp_path: Path):
        populated_dir = tmp_path / "populated"
        populated_dir.mkdir()
        (populated_dir / "pack_001.json").write_text("{}")
        assert orchestrator_module._dir_has_files(populated_dir) is True


class TestRequireModelPath:
    def test_raises_pipeline_execution_error_when_none(self):
        with pytest.raises(PipelineExecutionError, match="--llm-model-path"):
            orchestrator_module._require_model_path(None, "--llm-model-path")

    def test_returns_the_path_when_provided(self, tmp_path: Path):
        model_path = tmp_path / "qwen.gguf"
        assert (
            orchestrator_module._require_model_path(model_path, "--llm-model-path")
            == model_path
        )


class TestBuildSingleLectureQueue:
    def test_raises_when_queue_file_is_missing(self, tmp_path: Path):
        with pytest.raises(PipelineExecutionError, match="no queue file"):
            orchestrator_module._build_single_lecture_queue(
                tmp_path / "queue.json", "aula-001"
            )

    def test_raises_when_lecture_id_is_not_in_the_queue(self, tmp_path: Path):
        queue_path = _queue_with(tmp_path, LECTURE)

        with pytest.raises(PipelineExecutionError, match="aula-999"):
            orchestrator_module._build_single_lecture_queue(queue_path, "aula-999")

    def test_writes_a_temp_queue_containing_only_the_matching_lecture(
        self, tmp_path: Path
    ):
        other = LectureMetadata(
            id="aula-002",
            title="Farmacologia",
            subject="Farmacologia",
            url="https://example.com/aulas/aula-002",
            estimated_duration_sec=100.0,
        )
        queue_path = _queue_with(tmp_path, LECTURE, other)

        tmp_queue_path = orchestrator_module._build_single_lecture_queue(
            queue_path, LECTURE.id
        )

        try:
            payload = json.loads(tmp_queue_path.read_text())
            assert payload == [LECTURE.model_dump(mode="json")]
        finally:
            tmp_queue_path.unlink(missing_ok=True)


class TestFindOutcomeError:
    def test_returns_none_when_the_matching_outcome_has_no_error(self):
        outcomes = [SimpleNamespace(lecture_id="aula-001", error=None)]
        assert orchestrator_module._find_outcome_error(outcomes, "aula-001") is None

    def test_returns_the_error_string_when_the_matching_outcome_failed(self):
        outcomes = [SimpleNamespace(lecture_id="aula-001", error="boom")]
        assert orchestrator_module._find_outcome_error(outcomes, "aula-001") == "boom"

    def test_returns_a_message_when_no_outcome_matches_the_lecture(self):
        outcomes: list[SimpleNamespace] = []
        result = orchestrator_module._find_outcome_error(outcomes, "aula-001")
        assert result is not None
        assert "aula-001" in result


class TestArtifactExistsFileBased:
    @pytest.mark.parametrize(
        "fn_name,dir_const_name,relative_path",
        [
            ("_phase_2_artifact_exists", "CANONICAL_OUTPUT_DIR", "aula-001.json"),
            (
                "_phase_3_artifact_exists",
                "TRANSCRIBED_OUTPUT_DIR",
                "aula-001_transcription.json",
            ),
            (
                "_phase_8_artifact_exists",
                "CANONICAL_DOCUMENTS_OUTPUT_DIR",
                "aula-001/canonical_document.json",
            ),
            (
                "_phase_9_artifact_exists",
                "METADATA_OUTPUT_DIR",
                "aula-001/global_index.json",
            ),
        ],
    )
    def test_reflects_the_exact_file_each_phase_writes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        fn_name: str,
        dir_const_name: str,
        relative_path: str,
    ):
        monkeypatch.setattr(
            orchestrator_module,
            dir_const_name,
            tmp_path,
            raising=False,
        )
        artifact_exists = getattr(orchestrator_module, fn_name)

        assert artifact_exists(OrchestratorContext(), "aula-001") is False

        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")

        assert artifact_exists(OrchestratorContext(), "aula-001") is True


class TestArtifactExistsTwoFileBased:
    """Phases 11/13/14 each write two separate files atomically; the
    resume check must require both, not just the first, so a process
    killed mid-way is never mistaken for "already done"."""

    @pytest.mark.parametrize(
        "fn_name,dir_const_name,first_relative_path,second_relative_path",
        [
            (
                "_phase_11_artifact_exists",
                "PUBLISHED_OUTPUT_DIR",
                "aula-001/study_guide.md",
                "aula-001/study_guide.html",
            ),
            (
                "_phase_13_artifact_exists",
                "EXAMS_OUTPUT_DIR",
                "aula-001/exam.json",
                "aula-001/simulado.md",
            ),
            (
                "_phase_14_artifact_exists",
                "PODCAST_SCRIPTS_OUTPUT_DIR",
                "aula-001/script.json",
                "aula-001/script_legivel.md",
            ),
        ],
    )
    def test_delivery_files_are_not_resume_evidence_without_manifest(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        fn_name: str,
        dir_const_name: str,
        first_relative_path: str,
        second_relative_path: str,
    ):
        monkeypatch.setattr(
            orchestrator_module,
            dir_const_name,
            tmp_path,
            raising=False,
        )
        artifact_exists = getattr(orchestrator_module, fn_name)

        assert artifact_exists(OrchestratorContext(), "aula-001") is False

        first_target = tmp_path / first_relative_path
        first_target.parent.mkdir(parents=True, exist_ok=True)
        first_target.write_text("x")
        assert artifact_exists(OrchestratorContext(), "aula-001") is False

        (tmp_path / second_relative_path).write_text("x")
        assert artifact_exists(OrchestratorContext(), "aula-001") is False

    @pytest.mark.parametrize(
        "fn_name,dir_const_name,relative_path",
        [
            (
                "_phase_12_artifact_exists",
                "FLASHCARDS_OUTPUT_DIR",
                "aula-001/anki_deck.csv",
            ),
            (
                "_phase_15_artifact_exists",
                "AUDIO_OUTPUT_DIR",
                "aula-001/podcast_final.m4a",
            ),
        ],
    )
    def test_single_delivery_file_is_not_resume_evidence_without_manifest(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        fn_name: str,
        dir_const_name: str,
        relative_path: str,
    ):
        monkeypatch.setattr(
            orchestrator_module,
            dir_const_name,
            tmp_path,
            raising=False,
        )
        artifact_exists = getattr(orchestrator_module, fn_name)
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")

        assert artifact_exists(OrchestratorContext(), "aula-001") is False

    def test_deterministic_delivery_resume_requires_current_manifest(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        calls: list[tuple[str, str]] = []

        def verify_manifest(lecture_id: str, artifact_kind) -> bool:
            calls.append((lecture_id, artifact_kind.value))
            return True

        monkeypatch.setattr(
            orchestrator_module,
            "is_deterministic_delivery_current",
            verify_manifest,
        )

        assert (
            orchestrator_module._phase_11_artifact_exists(
                orchestrator_module.OrchestratorContext(), "aula-001"
            )
            is True
        )
        assert (
            orchestrator_module._phase_12_artifact_exists(
                orchestrator_module.OrchestratorContext(), "aula-001"
            )
            is True
        )
        assert calls == [
            ("aula-001", "publishing"),
            ("aula-001", "flashcards"),
        ]


class TestArtifactExistsDirectoryBased:
    def test_phase_4_reflects_whether_the_lecture_directory_has_any_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(orchestrator_module, "EVIDENCE_PACKS_OUTPUT_DIR", tmp_path)

        assert (
            orchestrator_module._phase_4_artifact_exists(
                orchestrator_module.OrchestratorContext(), "aula-001"
            )
            is False
        )

        lecture_dir = tmp_path / "aula-001"
        lecture_dir.mkdir()
        assert (
            orchestrator_module._phase_4_artifact_exists(
                orchestrator_module.OrchestratorContext(), "aula-001"
            )
            is False
        )

        (lecture_dir / "pack_001.json").write_text("{}")
        assert (
            orchestrator_module._phase_4_artifact_exists(
                orchestrator_module.OrchestratorContext(), "aula-001"
            )
            is True
        )


class TestArtifactExistsExactSequences:
    """Phases 5-7 require exact upstream and downstream sequence sets."""

    def test_phase_5_requires_a_ledger_for_every_pack(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        packs_dir = tmp_path / "packs"
        ledgers_dir = tmp_path / "ledgers"
        monkeypatch.setattr(orchestrator_module, "EVIDENCE_PACKS_OUTPUT_DIR", packs_dir)
        monkeypatch.setattr(
            orchestrator_module, "SEMANTIC_LEDGERS_OUTPUT_DIR", ledgers_dir
        )
        pack_dir = packs_dir / "aula-001"
        pack_dir.mkdir(parents=True)
        (pack_dir / "pack_001.json").write_text("{}")
        (pack_dir / "pack_002.json").write_text("{}")

        assert (
            orchestrator_module._phase_5_artifact_exists(
                orchestrator_module.OrchestratorContext(), "aula-001"
            )
            is False
        )

        ledger_dir = ledgers_dir / "aula-001"
        ledger_dir.mkdir(parents=True)
        (ledger_dir / "ledger_001.json").write_text("{}")
        assert (
            orchestrator_module._phase_5_artifact_exists(
                orchestrator_module.OrchestratorContext(), "aula-001"
            )
            is False
        )

        (ledger_dir / "ledger_002.json").write_text("{}")
        assert (
            orchestrator_module._phase_5_artifact_exists(
                orchestrator_module.OrchestratorContext(), "aula-001"
            )
            is True
        )

        (ledger_dir / "ledger_003.json").write_text("{}")
        assert (
            orchestrator_module._phase_5_artifact_exists(
                orchestrator_module.OrchestratorContext(), "aula-001"
            )
            is False
        )

    def test_phase_5_rejects_equal_counts_with_mismatched_sequences(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        packs_dir = tmp_path / "packs"
        ledgers_dir = tmp_path / "ledgers"
        monkeypatch.setattr(orchestrator_module, "EVIDENCE_PACKS_OUTPUT_DIR", packs_dir)
        monkeypatch.setattr(
            orchestrator_module, "SEMANTIC_LEDGERS_OUTPUT_DIR", ledgers_dir
        )
        pack_dir = packs_dir / "aula-001"
        ledger_dir = ledgers_dir / "aula-001"
        pack_dir.mkdir(parents=True)
        ledger_dir.mkdir(parents=True)
        (pack_dir / "pack_001.json").write_text("{}")
        (pack_dir / "pack_003.json").write_text("{}")
        (ledger_dir / "ledger_001.json").write_text("{}")
        (ledger_dir / "ledger_002.json").write_text("{}")

        assert (
            orchestrator_module._phase_5_artifact_exists(
                orchestrator_module.OrchestratorContext(), "aula-001"
            )
            is False
        )

    def test_phase_6_requires_a_draft_for_every_pack(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        packs_dir = tmp_path / "packs"
        drafts_dir = tmp_path / "drafts"
        monkeypatch.setattr(orchestrator_module, "EVIDENCE_PACKS_OUTPUT_DIR", packs_dir)
        monkeypatch.setattr(orchestrator_module, "DRAFTS_OUTPUT_DIR", drafts_dir)
        pack_dir = packs_dir / "aula-001"
        pack_dir.mkdir(parents=True)
        (pack_dir / "pack_001.json").write_text("{}")

        assert (
            orchestrator_module._phase_6_artifact_exists(
                orchestrator_module.OrchestratorContext(), "aula-001"
            )
            is False
        )

        draft_dir = drafts_dir / "aula-001"
        draft_dir.mkdir(parents=True)
        (draft_dir / "draft_001.json").write_text("{}")
        assert (
            orchestrator_module._phase_6_artifact_exists(
                orchestrator_module.OrchestratorContext(), "aula-001"
            )
            is True
        )

        (draft_dir / "draft_002.json").write_text("{}")
        assert (
            orchestrator_module._phase_6_artifact_exists(
                orchestrator_module.OrchestratorContext(), "aula-001"
            )
            is False
        )

    def test_phase_7_requires_a_unit_for_every_draft(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        drafts_dir = tmp_path / "drafts"
        units_dir = tmp_path / "units"
        monkeypatch.setattr(orchestrator_module, "DRAFTS_OUTPUT_DIR", drafts_dir)
        monkeypatch.setattr(
            orchestrator_module, "VALIDATED_UNITS_OUTPUT_DIR", units_dir
        )
        draft_dir = drafts_dir / "aula-001"
        draft_dir.mkdir(parents=True)
        (draft_dir / "draft_001.json").write_text("{}")

        assert (
            orchestrator_module._phase_7_artifact_exists(
                orchestrator_module.OrchestratorContext(), "aula-001"
            )
            is False
        )

        unit_dir = units_dir / "aula-001"
        unit_dir.mkdir(parents=True)
        (unit_dir / "unit_001.json").write_text("{}")
        assert (
            orchestrator_module._phase_7_artifact_exists(
                orchestrator_module.OrchestratorContext(), "aula-001"
            )
            is True
        )

        (unit_dir / "unit_002.json").write_text("{}")
        assert (
            orchestrator_module._phase_7_artifact_exists(
                orchestrator_module.OrchestratorContext(), "aula-001"
            )
            is False
        )


class TestRunPhase2(object):
    @pytest.mark.asyncio
    async def test_succeeds_when_the_outcome_reports_no_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        queue_path = _queue_with(tmp_path, LECTURE)
        ctx = OrchestratorContext(queue_path=queue_path)
        received_queue_paths = []

        class FakeExtractionOrchestrator:
            def __init__(self, queue_path: Path) -> None:
                received_queue_paths.append(queue_path)
                self._queue_path = queue_path

            async def run(self) -> list[SimpleNamespace]:
                payload = json.loads(self._queue_path.read_text())
                return [
                    SimpleNamespace(lecture_id=item["id"], error=None)
                    for item in payload
                ]

        monkeypatch.setattr(
            orchestrator_module, "ExtractionOrchestrator", FakeExtractionOrchestrator
        )

        await orchestrator_module._run_phase_2(ctx, LECTURE.id)

        assert not received_queue_paths[0].exists()

    @pytest.mark.asyncio
    async def test_raises_pipeline_execution_error_when_the_outcome_reports_an_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        queue_path = _queue_with(tmp_path, LECTURE)
        ctx = OrchestratorContext(queue_path=queue_path)

        class FakeExtractionOrchestrator:
            def __init__(self, queue_path: Path) -> None:
                pass

            async def run(self) -> list[SimpleNamespace]:
                return [SimpleNamespace(lecture_id=LECTURE.id, error="ffmpeg failed")]

        monkeypatch.setattr(
            orchestrator_module, "ExtractionOrchestrator", FakeExtractionOrchestrator
        )

        with pytest.raises(PipelineExecutionError, match="ffmpeg failed"):
            await orchestrator_module._run_phase_2(ctx, LECTURE.id)


class TestRunPhase3:
    @pytest.mark.asyncio
    async def test_raises_when_whisper_model_path_is_missing(self, tmp_path: Path):
        queue_path = _queue_with(tmp_path, LECTURE)
        ctx = OrchestratorContext(queue_path=queue_path, whisper_model_path=None)

        with pytest.raises(PipelineExecutionError, match="--whisper-model-path"):
            await orchestrator_module._run_phase_3(ctx, LECTURE.id)

    @pytest.mark.asyncio
    async def test_succeeds_and_passes_the_whisper_model_path_through(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        queue_path = _queue_with(tmp_path, LECTURE)
        model_path = tmp_path / "ggml-base.bin"
        ctx = OrchestratorContext(queue_path=queue_path, whisper_model_path=model_path)
        received_model_paths = []

        class FakeTranscriptionOrchestrator:
            def __init__(self, queue_path: Path, model_path: Path) -> None:
                received_model_paths.append(model_path)

            async def run(self) -> list[SimpleNamespace]:
                return [SimpleNamespace(lecture_id=LECTURE.id, error=None)]

        monkeypatch.setattr(
            orchestrator_module,
            "TranscriptionOrchestrator",
            FakeTranscriptionOrchestrator,
        )

        await orchestrator_module._run_phase_3(ctx, LECTURE.id)

        assert received_model_paths == [model_path]

    @pytest.mark.asyncio
    async def test_raises_pipeline_execution_error_when_the_outcome_reports_an_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        queue_path = _queue_with(tmp_path, LECTURE)
        ctx = OrchestratorContext(
            queue_path=queue_path, whisper_model_path=tmp_path / "m.bin"
        )

        class FakeTranscriptionOrchestrator:
            def __init__(self, queue_path: Path, model_path: Path) -> None:
                pass

            async def run(self) -> list[SimpleNamespace]:
                return [SimpleNamespace(lecture_id=LECTURE.id, error="whisper crashed")]

        monkeypatch.setattr(
            orchestrator_module,
            "TranscriptionOrchestrator",
            FakeTranscriptionOrchestrator,
        )

        with pytest.raises(PipelineExecutionError, match="whisper crashed"):
            await orchestrator_module._run_phase_3(ctx, LECTURE.id)


class TestRunPhase4:
    @pytest.mark.asyncio
    async def test_succeeds_when_the_outcome_reports_no_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        queue_path = _queue_with(tmp_path, LECTURE)
        ctx = OrchestratorContext(queue_path=queue_path)

        class FakeEvidencePackOrchestrator:
            def __init__(self, queue_path: Path) -> None:
                pass

            async def run(self) -> list[SimpleNamespace]:
                return [SimpleNamespace(lecture_id=LECTURE.id, error=None)]

        monkeypatch.setattr(
            orchestrator_module,
            "EvidencePackOrchestrator",
            FakeEvidencePackOrchestrator,
        )

        await orchestrator_module._run_phase_4(ctx, LECTURE.id)

    @pytest.mark.asyncio
    async def test_raises_pipeline_execution_error_when_the_outcome_reports_an_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        queue_path = _queue_with(tmp_path, LECTURE)
        ctx = OrchestratorContext(queue_path=queue_path)

        class FakeEvidencePackOrchestrator:
            def __init__(self, queue_path: Path) -> None:
                pass

            async def run(self) -> list[SimpleNamespace]:
                return [SimpleNamespace(lecture_id=LECTURE.id, error="chunking failed")]

        monkeypatch.setattr(
            orchestrator_module,
            "EvidencePackOrchestrator",
            FakeEvidencePackOrchestrator,
        )

        with pytest.raises(PipelineExecutionError, match="chunking failed"):
            await orchestrator_module._run_phase_4(ctx, LECTURE.id)


def _load_generation_script(script_filename: str):
    """Load scripts/generate_codex_* as a module the same way the sibling
    test_generate_codex_* files do, so wiring assertions run against the
    script's real argparse defaults instead of a copy of them."""
    import importlib.util

    script_path = Path(__file__).parents[1] / "scripts" / script_filename
    spec = importlib.util.spec_from_file_location(script_filename[:-3], script_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _capture_run_bounded_process(monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict = {}

    def fake_run_bounded_process(
        command,
        *,
        env,
        timeout_seconds,
        maximum_stdout_bytes,
        maximum_stderr_bytes,
        check,
    ):
        captured["command"] = list(command)
        captured["env"] = env
        captured["timeout_seconds"] = timeout_seconds
        captured["output_budget"] = (maximum_stdout_bytes, maximum_stderr_bytes)
        captured["check"] = check
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        orchestrator_module, "run_bounded_process", fake_run_bounded_process
    )
    return captured


@pytest.mark.parametrize(
    ("phase_runner", "phase_number", "phase_name", "script_filename"),
    [
        (
            orchestrator_module._run_phase_5,
            5,
            "semantic",
            "generate_codex_semantic_ledgers.py",
        ),
        (
            orchestrator_module._run_phase_6,
            6,
            "drafting",
            "generate_codex_drafts.py",
        ),
        (
            orchestrator_module._run_phase_7,
            7,
            "critic",
            "generate_codex_critic_review.py",
        ),
        (
            orchestrator_module._run_phase_14,
            14,
            "podcast",
            "generate_codex_podcast.py",
        ),
    ],
)
class TestCodexScriptPhases:
    @pytest.mark.asyncio
    async def test_runs_the_phases_codex_script_in_a_child_process(
        self,
        monkeypatch: pytest.MonkeyPatch,
        phase_runner,
        phase_number,
        phase_name,
        script_filename,
    ) -> None:
        captured = _capture_run_bounded_process(monkeypatch)

        await phase_runner(OrchestratorContext(), LECTURE.id)

        assert captured["command"] == [
            sys.executable,
            f"scripts/{script_filename}",
            LECTURE.id,
        ]
        assert set(captured["env"]) <= set(orchestrator_module._CODEX_PHASE_ENV_KEYS)
        assert captured["env"] == {
            name: os.environ[name]
            for name in orchestrator_module._CODEX_PHASE_ENV_KEYS
            if name in os.environ
        }
        assert captured["check"] is True
        assert (
            captured["timeout_seconds"]
            == orchestrator_module._CODEX_PHASE_TIMEOUT_SECONDS
        )
        assert captured["output_budget"] == (
            orchestrator_module._CODEX_PHASE_OUTPUT_LIMIT_BYTES,
            orchestrator_module._CODEX_PHASE_OUTPUT_LIMIT_BYTES,
        )

    @pytest.mark.asyncio
    async def test_surfaces_a_nonzero_exit_as_a_phase_error_naming_the_script(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        phase_runner,
        phase_number,
        phase_name,
        script_filename,
    ) -> None:
        def failing_run_bounded_process(command, **_kwargs):
            raise subprocess.CalledProcessError(
                3, command, output="progress line", stderr="boom"
            )

        monkeypatch.setattr(
            orchestrator_module, "run_bounded_process", failing_run_bounded_process
        )
        monkeypatch.setattr(
            orchestrator_module, "_CODEX_PHASE_FAILURE_LOGS_DIR", tmp_path / "failures"
        )

        with pytest.raises(PipelineExecutionError) as excinfo:
            await phase_runner(OrchestratorContext(), LECTURE.id)

        message = str(excinfo.value)
        assert f"phase {phase_number} ({phase_name})" in message
        assert script_filename in message
        assert "exited with code 3" in message
        # Model-adjacent child output is never inlined into the message:
        # the message carries digests and the persisted log's path, and
        # the raw bytes live only in that local file.
        assert "boom" not in message
        assert "progress line" not in message
        assert "sha256=" in message
        persisted = tmp_path / "failures" / f"phase-{phase_number}-{LECTURE.id}.log"
        assert "boom" in persisted.read_text()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("error_factory", "expected_detail"),
        [
            (
                lambda command: subprocess.TimeoutExpired(command, 1),
                "timed out",
            ),
            (
                lambda _command: orchestrator_module.ProcessOutputLimitExceeded(
                    "overflow", maximum_bytes=16
                ),
                "output budget",
            ),
        ],
    )
    async def test_surfaces_timeout_and_output_overflow_as_phase_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        phase_runner,
        phase_number,
        phase_name,
        script_filename,
        error_factory,
        expected_detail,
    ) -> None:
        def failing_run_bounded_process(command, **_kwargs):
            raise error_factory(command)

        monkeypatch.setattr(
            orchestrator_module, "run_bounded_process", failing_run_bounded_process
        )

        with pytest.raises(PipelineExecutionError, match=expected_detail):
            await phase_runner(OrchestratorContext(), LECTURE.id)


class TestSimplePassThroughPhases:
    @pytest.mark.asyncio
    async def test_run_phase_8_calls_assemble_lecture(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        calls = []

        class FakeDocumentAssembler:
            def assemble_lecture(self, lecture_id: str) -> None:
                calls.append(lecture_id)

        monkeypatch.setattr(
            orchestrator_module, "DocumentAssembler", FakeDocumentAssembler
        )

        await orchestrator_module._run_phase_8(OrchestratorContext(), LECTURE.id)

        assert calls == [LECTURE.id]

    @pytest.mark.asyncio
    async def test_run_phase_9_calls_aggregate_lecture(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        calls = []

        class FakeMetadataAggregator:
            def aggregate_lecture(self, lecture_id: str) -> None:
                calls.append(lecture_id)

        monkeypatch.setattr(
            orchestrator_module, "MetadataAggregator", FakeMetadataAggregator
        )

        await orchestrator_module._run_phase_9(OrchestratorContext(), LECTURE.id)

        assert calls == [LECTURE.id]

    @pytest.mark.asyncio
    async def test_run_phase_11_calls_publish_lecture(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        calls = []

        class FakePublishingOrchestrator:
            def publish_lecture(self, lecture_id: str) -> None:
                calls.append(lecture_id)

        monkeypatch.setattr(
            orchestrator_module, "PublishingOrchestrator", FakePublishingOrchestrator
        )

        await orchestrator_module._run_phase_11(OrchestratorContext(), LECTURE.id)

        assert calls == [LECTURE.id]

    @pytest.mark.asyncio
    async def test_run_phase_12_calls_generate_deck(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        calls = []

        class FakeAnkiDeckGenerator:
            def generate_deck(self, lecture_id: str) -> None:
                calls.append(lecture_id)

        monkeypatch.setattr(
            orchestrator_module, "AnkiDeckGenerator", FakeAnkiDeckGenerator
        )

        await orchestrator_module._run_phase_12(OrchestratorContext(), LECTURE.id)

        assert calls == [LECTURE.id]


class TestPhase13ExamCompile:
    @pytest.mark.asyncio
    async def test_generates_the_exam_then_compiles_the_readable_simulation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured: dict = {}
        compiled: list[MockExam] = []

        def fake_run_bounded_process(command, **_kwargs):
            captured["command"] = list(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        exam = MockExam(lecture_id=LECTURE.id, questions=[])
        exams_root = tmp_path / "14_mock_exams"
        exam_dir = exams_root / LECTURE.id
        exam_dir.mkdir(parents=True)
        (exam_dir / "exam.json").write_text(exam.model_dump_json(), encoding="utf-8")

        class FakeExamCompiler:
            def compile(self, observed: MockExam) -> None:
                compiled.append(observed)

        monkeypatch.setattr(
            orchestrator_module, "run_bounded_process", fake_run_bounded_process
        )
        monkeypatch.setattr(orchestrator_module, "EXAMS_OUTPUT_DIR", exams_root)
        monkeypatch.setattr(orchestrator_module, "ExamCompiler", FakeExamCompiler)

        await orchestrator_module._run_phase_13(OrchestratorContext(), LECTURE.id)

        assert captured["command"] == [
            sys.executable,
            "scripts/generate_codex_mock_exam.py",
            LECTURE.id,
        ]
        assert compiled == [exam]

    @pytest.mark.asyncio
    async def test_a_script_that_writes_no_exam_fails_loudly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def fake_run_bounded_process(command, **_kwargs):
            return subprocess.CompletedProcess(command, 0, "", "")

        monkeypatch.setattr(
            orchestrator_module, "run_bounded_process", fake_run_bounded_process
        )
        monkeypatch.setattr(
            orchestrator_module, "EXAMS_OUTPUT_DIR", tmp_path / "14_mock_exams"
        )

        with pytest.raises(PipelineExecutionError, match="phase 13 \\(exams\\)"):
            await orchestrator_module._run_phase_13(OrchestratorContext(), LECTURE.id)


class TestRunPhase15:
    @pytest.mark.asyncio
    async def test_calls_generate_podcast(self, monkeypatch: pytest.MonkeyPatch):
        calls = []

        class FakePodcastAudioGenerator:
            def generate_podcast(self, lecture_id: str) -> str:
                calls.append(lecture_id)
                return "workspace/data/16_podcast_audio/aula-001/podcast_final.m4a"

        monkeypatch.setattr(
            orchestrator_module, "PodcastAudioGenerator", FakePodcastAudioGenerator
        )

        await orchestrator_module._run_phase_15(OrchestratorContext(), LECTURE.id)

        assert calls == [LECTURE.id]


class TestFindSpec:
    def test_returns_the_matching_spec(self):
        spec = orchestrator_module._find_spec(15)
        assert spec.number == 15
        assert spec.name == "tts"

    def test_raises_pipeline_execution_error_for_an_unknown_phase_number(self):
        with pytest.raises(PipelineExecutionError, match="unknown phase number"):
            orchestrator_module._find_spec(99)


class TestPhaseRegistry:
    def test_contains_exactly_the_fourteen_runnable_phases(self):
        assert [spec.number for spec in PHASE_REGISTRY] == [
            2,
            3,
            4,
            5,
            6,
            7,
            8,
            9,
            10,
            11,
            12,
            13,
            14,
            15,
        ]

    def test_quality_certification_is_the_boundary_before_delivery(self):
        phase_names = {spec.number: spec.name for spec in PHASE_REGISTRY}

        assert phase_names[10] == "quality-certification"
        assert max(number for number in phase_names if number < 10) == 9
        assert min(number for number in phase_names if number > 10) == 11

    def test_all_registered_phases_support_single_lecture_invocation(self):
        for spec in PHASE_REGISTRY:
            assert spec.supports_single_lecture is True, spec.number


def _fake_spec(
    number: int,
    supports_single_lecture: bool = True,
    exists: bool = False,
    should_fail: bool = False,
) -> PhaseSpec:
    calls: list[str] = []

    async def run(ctx: OrchestratorContext, lecture_id: str) -> None:
        calls.append(lecture_id)
        if should_fail:
            raise PipelineExecutionError(f"phase {number} failed")

    spec = PhaseSpec(
        number=number,
        name=f"phase-{number}",
        supports_single_lecture=supports_single_lecture,
        artifact_exists=lambda _ctx, _lid: exists,
        run=run,
    )
    spec.run.calls = calls  # type: ignore[attr-defined]
    return spec


class TestRunPhaseDriver:
    @pytest.mark.asyncio
    async def test_skips_when_the_artifact_already_exists(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        spec = _fake_spec(2, exists=True)
        monkeypatch.setattr(orchestrator_module, "PHASE_REGISTRY", (spec,))

        result = await run_phase(OrchestratorContext(), 2, LECTURE.id)

        assert result == PhaseResult(2, "phase-2", "skipped")
        assert spec.run.calls == []  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_runs_when_the_artifact_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        spec = _fake_spec(2, exists=False)
        monkeypatch.setattr(orchestrator_module, "PHASE_REGISTRY", (spec,))

        result = await run_phase(OrchestratorContext(), 2, LECTURE.id)

        assert result == PhaseResult(2, "phase-2", "ran")
        assert spec.run.calls == [LECTURE.id]  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_rejects_a_phase_with_no_single_lecture_support(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        spec = _fake_spec(5, supports_single_lecture=False, exists=False)
        monkeypatch.setattr(orchestrator_module, "PHASE_REGISTRY", (spec,))

        with pytest.raises(PipelineExecutionError, match="no per-lecture entry"):
            await run_phase(OrchestratorContext(), 5, LECTURE.id)

    @pytest.mark.asyncio
    async def test_delivery_certificate_is_checked_before_existing_output_skip(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        spec = _fake_spec(11, exists=True)
        monkeypatch.setattr(orchestrator_module, "PHASE_REGISTRY", (spec,))

        def reject_uncertified(_lecture_id: str) -> None:
            raise QualityCertificationError("needs review")

        monkeypatch.setattr(
            orchestrator_module,
            "require_publication_ready",
            reject_uncertified,
        )

        with pytest.raises(QualityCertificationError, match="needs review"):
            await run_phase(OrchestratorContext(), 11, LECTURE.id)

        assert spec.run.calls == []  # type: ignore[attr-defined]


class TestRunAllDriver:
    @pytest.mark.asyncio
    async def test_runs_every_missing_phase_and_skips_existing_ones(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        spec_a = _fake_spec(2, exists=True)
        spec_b = _fake_spec(3, exists=False)
        monkeypatch.setattr(orchestrator_module, "PHASE_REGISTRY", (spec_a, spec_b))

        results = await run_all(OrchestratorContext(), LECTURE.id)

        assert results == [
            PhaseResult(2, "phase-2", "skipped"),
            PhaseResult(3, "phase-3", "ran"),
        ]
        assert spec_a.run.calls == []  # type: ignore[attr-defined]
        assert spec_b.run.calls == [LECTURE.id]  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_stops_at_the_first_phase_that_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        spec_a = _fake_spec(2, exists=False, should_fail=True)
        spec_b = _fake_spec(3, exists=False)
        monkeypatch.setattr(orchestrator_module, "PHASE_REGISTRY", (spec_a, spec_b))

        with pytest.raises(PipelineExecutionError, match="phase 2 failed"):
            await run_all(OrchestratorContext(), LECTURE.id)

        assert spec_b.run.calls == []  # type: ignore[attr-defined]


class TestLectureIdValidation:
    """P0 security regression: a malformed/traversal lecture_id must be
    rejected before _phaseN_artifact_exists ever splices it into a Path
    -- that resume check runs before spec.run's own (previously the
    only) SafeIdentifier guard, at every entry point."""

    @staticmethod
    def _probing_spec(probed: list[str]) -> PhaseSpec:
        def probing_artifact_exists(lecture_id: str) -> bool:
            probed.append(lecture_id)
            return False

        async def unreachable_run(ctx: OrchestratorContext, lecture_id: str) -> None:
            raise AssertionError("spec.run must not be reached")

        return PhaseSpec(2, "phase-2", True, probing_artifact_exists, unreachable_run)

    @pytest.mark.asyncio
    async def test_run_phase_rejects_a_path_traversal_lecture_id_before_any_check(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        probed: list[str] = []
        monkeypatch.setattr(
            orchestrator_module, "PHASE_REGISTRY", (self._probing_spec(probed),)
        )

        with pytest.raises(ValidationError):
            await run_phase(OrchestratorContext(), 2, "../../etc/passwd")

        assert probed == []

    @pytest.mark.asyncio
    async def test_run_all_rejects_a_path_traversal_lecture_id_before_any_check(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        probed: list[str] = []
        monkeypatch.setattr(
            orchestrator_module, "PHASE_REGISTRY", (self._probing_spec(probed),)
        )

        with pytest.raises(ValidationError):
            await run_all(OrchestratorContext(), "../../etc/passwd")

        assert probed == []


class TestCodexStageRootsMatchScriptDefaults:
    """ADR 0038: the orchestrator's resume predicates and the standalone
    scripts' argparse defaults duplicate the same workspace stage roots by
    design (scripts stay standalone-runnable). This pins the two together
    so they cannot drift silently."""

    @pytest.mark.parametrize(
        ("script_filename", "constant_name"),
        [
            (
                "generate_codex_semantic_ledgers.py",
                "SEMANTIC_LEDGERS_OUTPUT_DIR",
            ),
            ("generate_codex_drafts.py", "DRAFTS_OUTPUT_DIR"),
            ("generate_codex_critic_review.py", "VALIDATED_UNITS_OUTPUT_DIR"),
            ("generate_codex_mock_exam.py", "EXAMS_OUTPUT_DIR"),
        ],
    )
    def test_orchestrator_stage_roots_equal_the_script_defaults(
        self, script_filename: str, constant_name: str
    ) -> None:
        script_module = _load_generation_script(script_filename)

        assert (
            getattr(orchestrator_module, constant_name)
            == script_module.DEFAULT_OUTPUT_ROOT
        )

    def test_script_input_roots_match_pipeline_stage_roots(self) -> None:
        semantic = _load_generation_script("generate_codex_semantic_ledgers.py")
        drafts = _load_generation_script("generate_codex_drafts.py")
        critic = _load_generation_script("generate_codex_critic_review.py")
        exam = _load_generation_script("generate_codex_mock_exam.py")
        podcast = _load_generation_script("generate_codex_podcast.py")
        packs = orchestrator_module.EVIDENCE_PACKS_OUTPUT_DIR
        ledgers = orchestrator_module.SEMANTIC_LEDGERS_OUTPUT_DIR
        drafts_root = orchestrator_module.DRAFTS_OUTPUT_DIR

        assert semantic.DEFAULT_PACKS_ROOT == packs
        assert drafts.DEFAULT_PACKS_ROOT == packs
        assert drafts.DEFAULT_LEDGERS_ROOT == ledgers
        assert critic.DEFAULT_PACKS_ROOT == packs
        assert critic.DEFAULT_LEDGERS_ROOT == ledgers
        assert critic.DEFAULT_DRAFTS_ROOT == drafts_root
        assert exam.DEFAULT_LEDGERS_ROOT == ledgers
        assert exam.DEFAULT_DRAFTS_ROOT == drafts_root
        assert podcast.DEFAULT_DRAFTS_ROOT == drafts_root


class TestBuildArgParser:
    def test_parses_run_all_with_defaults(self):
        args = cli_main.build_arg_parser().parse_args(["run-all", "aula-001"])
        assert args.command == "run-all"
        assert args.lecture_id == "aula-001"
        assert args.whisper_model_path is None

    def test_parses_run_phase_with_an_explicit_whisper_model_path(self):
        args = cli_main.build_arg_parser().parse_args(
            [
                "run-phase",
                "3",
                "aula-001",
                "--whisper-model-path",
                "models/ggml-large-v3-turbo.bin",
            ]
        )
        assert args.command == "run-phase"
        assert args.phase_number == 3
        assert args.lecture_id == "aula-001"
        assert args.whisper_model_path == Path("models/ggml-large-v3-turbo.bin")

    def test_rejects_the_removed_llm_model_flags(self):
        # ADR 0038 removed the local LLM route; the flags went with it, so
        # a stale invocation fails at argparse instead of being silently
        # accepted and ignored.
        with pytest.raises(SystemExit):
            cli_main.build_arg_parser().parse_args(
                ["run-phase", "5", "aula-001", "--llm-model-path", "models/x.gguf"]
            )

    def test_rejects_a_phase_number_outside_the_registry(self):
        with pytest.raises(SystemExit):
            cli_main.build_arg_parser().parse_args(["run-phase", "1", "aula-001"])

    def test_requires_a_command(self):
        with pytest.raises(SystemExit):
            cli_main.build_arg_parser().parse_args([])


class TestDispatch:
    @pytest.mark.asyncio
    async def test_run_all_command_calls_run_all_with_context(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        received = []

        async def fake_run_all(
            ctx: OrchestratorContext, lecture_id: str
        ) -> list[object]:
            received.append((ctx, lecture_id))
            return []

        monkeypatch.setattr(cli_main, "run_all", fake_run_all)
        args = cli_main.build_arg_parser().parse_args(["run-all", "aula-001"])

        await cli_main._dispatch(args)

        assert received[0][1] == "aula-001"

    @pytest.mark.asyncio
    async def test_run_phase_command_calls_run_phase_with_context(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        received = []

        async def fake_run_phase(
            ctx: OrchestratorContext, phase_number: int, lecture_id: str
        ) -> object:
            received.append((phase_number, lecture_id))
            return None

        monkeypatch.setattr(cli_main, "run_phase", fake_run_phase)
        args = cli_main.build_arg_parser().parse_args(["run-phase", "15", "aula-001"])

        await cli_main._dispatch(args)

        assert received == [(15, "aula-001")]


class TestMain:
    def test_run_all_success_exits_cleanly(self, monkeypatch: pytest.MonkeyPatch):
        async def fake_run_all(
            ctx: OrchestratorContext, lecture_id: str
        ) -> list[object]:
            return []

        monkeypatch.setattr(cli_main, "run_all", fake_run_all)

        cli_main.main(["run-all", "aula-001"])

    def test_domain_exception_exits_with_code_1(self, monkeypatch: pytest.MonkeyPatch):
        async def fake_run_all(
            ctx: OrchestratorContext, lecture_id: str
        ) -> list[object]:
            raise PipelineExecutionError("lecture not enqueued")

        monkeypatch.setattr(cli_main, "run_all", fake_run_all)

        with pytest.raises(SystemExit) as excinfo:
            cli_main.main(["run-all", "aula-001"])

        assert excinfo.value.code == 1

    def test_unexpected_exception_propagates_uncaught(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        async def fake_run_all(
            ctx: OrchestratorContext, lecture_id: str
        ) -> list[object]:
            raise KeyError("not a domain exception")

        monkeypatch.setattr(cli_main, "run_all", fake_run_all)

        with pytest.raises(KeyError):
            cli_main.main(["run-all", "aula-001"])

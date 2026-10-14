"""Run record: metadata only, atomic, validated path, outside stage directories."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError

from lecture_study.llm.gateway import (
    AdapterName,
    GatewayIdentity,
    LlmCallError,
    LlmRequest,
    LlmResponse,
    Stage,
)
from lecture_study.llm import run_record
from lecture_study.llm.run_record import (
    LLM_RUNS_ROOT,
    GatewayUsage,
    LlmRunRecord,
    record_llm_run,
    run_record_path,
)
from lecture_study.quality import delivery

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROMPT_MARKER = "PROMPT-MARKER-5f3a"
RESPONSE_MARKER = "RESPONSE-MARKER-9c1d"
MODEL_BYTES = b"synthetic model bytes " * 100
TOP_LEVEL_KEYS = {
    "record_version",
    "lecture_id",
    "script_stage",
    "schema_name",
    "status",
    "scope",
    "started_at",
    "finished_at",
    "wall_seconds",
    "gateways",
}


class _InMemoryGateway:
    def __init__(
        self,
        model_path: Path | None,
        *,
        adapter_name: AdapterName = AdapterName.LLAMA_CPP,
        fails: bool = False,
    ) -> None:
        self._model_path = model_path
        self._adapter_name = adapter_name
        self._fails = fails

    def complete(self, request: LlmRequest) -> LlmResponse:
        if self._fails:
            raise LlmCallError(request.stage, self._adapter_name, "stub failure")
        return LlmResponse(RESPONSE_MARKER.encode())

    def describe(self) -> GatewayIdentity:
        return GatewayIdentity(
            adapter_name=self._adapter_name,
            model_identifier="stub-model",
            model_path=self._model_path,
            engine_version="stub-engine-1",
        )


@pytest.fixture
def model_file(tmp_path: Path) -> Path:
    path = tmp_path / "model-00001-of-00003.gguf"
    path.write_bytes(MODEL_BYTES)
    return path


@pytest.fixture
def runs_root(tmp_path: Path) -> Path:
    return tmp_path / "llm_runs"


def _load_record(root: Path, lecture_id: str, stage: Stage) -> dict[str, Any]:
    return json.loads(run_record_path(root, lecture_id, stage).read_text("utf-8"))


def _load_dashboard_module() -> ModuleType:
    path = REPOSITORY_ROOT / "scripts" / "generate_progress_dashboard.py"
    spec = importlib.util.spec_from_file_location("generate_progress_dashboard", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_successful_run_writes_metadata_only_record(
    runs_root: Path, model_file: Path
) -> None:
    with record_llm_run(
        lecture_id="lec-1",
        script_stage=Stage.SEMANTIC,
        schema_name="SemanticLedger",
        root=runs_root,
    ) as recorder:
        gateway = recorder.track(_InMemoryGateway(model_file), stage=Stage.SEMANTIC)
        gateway.complete(LlmRequest(Stage.SEMANTIC, PROMPT_MARKER))
        gateway.complete(LlmRequest(Stage.SEMANTIC, PROMPT_MARKER, is_retry=True))

    path = runs_root / "lec-1" / "semantic.json"
    assert path.is_file()
    record = _load_record(runs_root, "lec-1", Stage.SEMANTIC)
    assert set(record) == TOP_LEVEL_KEYS
    assert record["status"] == "succeeded"
    (usage,) = record["gateways"]
    assert usage["calls"] == 2
    assert usage["retry_calls"] == 1
    assert usage["model_sha256"] == hashlib.sha256(MODEL_BYTES).hexdigest()
    assert usage["adapter_name"] == "llama-cpp"
    assert usage["engine_version"] == "stub-engine-1"


def test_record_never_contains_prompt_or_response_text(
    runs_root: Path, model_file: Path
) -> None:
    with record_llm_run(
        lecture_id="lec-1",
        script_stage=Stage.DRAFTING,
        schema_name="Draft",
        root=runs_root,
    ) as recorder:
        gateway = recorder.track(_InMemoryGateway(model_file), stage=Stage.DRAFTING)
        gateway.complete(LlmRequest(Stage.DRAFTING, PROMPT_MARKER))

    text = run_record_path(runs_root, "lec-1", Stage.DRAFTING).read_text("utf-8")
    assert PROMPT_MARKER not in text
    assert RESPONSE_MARKER not in text


def test_failing_block_writes_failed_status_and_propagates(
    runs_root: Path, model_file: Path
) -> None:
    with pytest.raises(RuntimeError, match="block failed"):
        with record_llm_run(
            lecture_id="lec-1",
            script_stage=Stage.CRITIC,
            schema_name="Critic",
            root=runs_root,
        ) as recorder:
            recorder.track(_InMemoryGateway(model_file), stage=Stage.CRITIC)
            raise RuntimeError("block failed")

    assert _load_record(runs_root, "lec-1", Stage.CRITIC)["status"] == "failed"


def test_failed_run_never_hashes_the_model(
    runs_root: Path, model_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_hash(path: Path) -> str | None:
        raise AssertionError(f"model hashed on a failure path: {path}")

    monkeypatch.setattr(run_record, "_file_sha256", forbidden_hash)

    with pytest.raises(KeyboardInterrupt):
        with record_llm_run(
            lecture_id="lec-1",
            script_stage=Stage.CRITIC,
            schema_name="Critic",
            root=runs_root,
        ) as recorder:
            recorder.track(_InMemoryGateway(model_file), stage=Stage.CRITIC)
            raise KeyboardInterrupt

    record = _load_record(runs_root, "lec-1", Stage.CRITIC)
    assert record["status"] == "failed"
    assert record["gateways"][0]["model_sha256"] is None


def test_record_write_failure_does_not_mask_the_original_error(
    runs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_write(path: Path, record: LlmRunRecord) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(run_record, "_write_atomically", failing_write)

    with pytest.raises(LlmCallError, match="stub failure"):
        with record_llm_run(
            lecture_id="lec-1",
            script_stage=Stage.EXAMS,
            schema_name="Exam",
            root=runs_root,
        ) as recorder:
            gateway = recorder.track(
                _InMemoryGateway(None, fails=True), stage=Stage.EXAMS
            )
            gateway.complete(LlmRequest(Stage.EXAMS, PROMPT_MARKER))


def test_failed_gateway_call_is_still_counted(
    runs_root: Path, model_file: Path
) -> None:
    with pytest.raises(LlmCallError):
        with record_llm_run(
            lecture_id="lec-1",
            script_stage=Stage.EXAMS,
            schema_name="Exam",
            root=runs_root,
        ) as recorder:
            gateway = recorder.track(
                _InMemoryGateway(model_file, fails=True), stage=Stage.EXAMS
            )
            gateway.complete(LlmRequest(Stage.EXAMS, PROMPT_MARKER))

    record = LlmRunRecord.model_validate_json(
        run_record_path(runs_root, "lec-1", Stage.EXAMS).read_text("utf-8"),
        strict=False,
    )
    assert record.status == "failed"
    assert record.gateways[0].calls == 1
    assert record.gateways[0].call_seconds >= 0


def test_malformed_lecture_id_raises_before_anything_is_written(
    runs_root: Path,
) -> None:
    with pytest.raises(ValidationError):
        with record_llm_run(
            lecture_id="../x",
            script_stage=Stage.SEMANTIC,
            schema_name="SemanticLedger",
            root=runs_root,
        ):
            pytest.fail("the block must not run")

    assert not runs_root.exists()


def test_no_temporary_file_remains_and_a_second_run_replaces_the_first(
    runs_root: Path,
) -> None:
    with pytest.raises(RuntimeError):
        with record_llm_run(
            lecture_id="lec-1",
            script_stage=Stage.PODCAST,
            schema_name="Script",
            root=runs_root,
        ):
            raise RuntimeError("first run fails")
    with record_llm_run(
        lecture_id="lec-1",
        script_stage=Stage.PODCAST,
        schema_name="Script",
        root=runs_root,
    ):
        pass

    directory = runs_root / "lec-1"
    assert sorted(item.name for item in directory.iterdir()) == ["podcast.json"]
    assert _load_record(runs_root, "lec-1", Stage.PODCAST)["status"] == "succeeded"


def test_concurrent_writes_use_distinct_temporary_files(
    runs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temporary_names: list[str] = []
    original_replace = run_record.os.replace

    def recording_replace(source: Path, destination: Path) -> None:
        temporary_names.append(Path(source).name)
        original_replace(source, destination)

    monkeypatch.setattr(run_record.os, "replace", recording_replace)
    for _ in range(2):
        with record_llm_run(
            lecture_id="lec-1",
            script_stage=Stage.PODCAST,
            schema_name="Script",
            root=runs_root,
        ):
            pass

    assert len(set(temporary_names)) == 2
    assert sorted(item.name for item in (runs_root / "lec-1").iterdir()) == [
        "podcast.json"
    ]


def test_record_is_fsynced_before_it_replaces_the_previous_one(
    runs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    original_fsync = run_record.os.fsync
    original_replace = run_record.os.replace
    monkeypatch.setattr(
        run_record.os, "fsync", lambda fd: (events.append("fsync"), original_fsync(fd))
    )
    monkeypatch.setattr(
        run_record.os,
        "replace",
        lambda source, destination: (
            events.append("replace"),
            original_replace(source, destination),
        ),
    )

    with record_llm_run(
        lecture_id="lec-1",
        script_stage=Stage.PODCAST,
        schema_name="Script",
        root=runs_root,
    ):
        pass

    assert events[:2] == ["fsync", "replace"]


def test_failed_rerun_keeps_the_earlier_successful_record(runs_root: Path) -> None:
    with record_llm_run(
        lecture_id="lec-1",
        script_stage=Stage.SEMANTIC,
        schema_name="SemanticLedger",
        root=runs_root,
    ):
        pass
    with pytest.raises(RuntimeError):
        with record_llm_run(
            lecture_id="lec-1",
            script_stage=Stage.SEMANTIC,
            schema_name="SemanticLedger",
            root=runs_root,
            scope="pack_sequence=2",
        ):
            raise RuntimeError("rerun failed")

    record = _load_record(runs_root, "lec-1", Stage.SEMANTIC)
    assert record["status"] == "succeeded"
    assert record["scope"] is None


def test_partial_run_records_its_scope(runs_root: Path) -> None:
    with record_llm_run(
        lecture_id="lec-1",
        script_stage=Stage.SEMANTIC,
        schema_name="SemanticLedger",
        root=runs_root,
        scope="pack_sequence=3",
    ):
        pass

    assert (
        _load_record(runs_root, "lec-1", Stage.SEMANTIC)["scope"] == "pack_sequence=3"
    )


def test_malformed_scope_is_rejected(runs_root: Path) -> None:
    with pytest.raises(ValidationError):
        with record_llm_run(
            lecture_id="lec-1",
            script_stage=Stage.SEMANTIC,
            schema_name="SemanticLedger",
            root=runs_root,
            scope="has spaces\nand newline",
        ):
            pass


def test_two_tracked_gateways_produce_two_usages(
    runs_root: Path, model_file: Path
) -> None:
    with record_llm_run(
        lecture_id="lec-1",
        script_stage=Stage.CRITIC,
        schema_name="Critic",
        root=runs_root,
    ) as recorder:
        recorder.track(_InMemoryGateway(model_file), stage=Stage.CRITIC)
        recorder.track(
            _InMemoryGateway(None, adapter_name=AdapterName.CODEX), stage=Stage.DRAFTING
        )

    record = LlmRunRecord.model_validate_json(
        run_record_path(runs_root, "lec-1", Stage.CRITIC).read_text("utf-8"),
        strict=False,
    )
    assert [(usage.stage, usage.adapter_name) for usage in record.gateways] == [
        (Stage.CRITIC, AdapterName.LLAMA_CPP),
        (Stage.DRAFTING, AdapterName.CODEX),
    ]
    assert record.gateways[1].model_sha256 is None


def test_unreadable_model_file_records_no_digest_and_logs_the_path_only(
    runs_root: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    missing_model = tmp_path / "missing.gguf"

    with caplog.at_level(logging.WARNING, logger="lecture_study.llm.run_record"):
        with record_llm_run(
            lecture_id="lec-1",
            script_stage=Stage.FLASHCARDS,
            schema_name="Cards",
            root=runs_root,
        ) as recorder:
            recorder.track(_InMemoryGateway(missing_model), stage=Stage.FLASHCARDS)

    record = _load_record(runs_root, "lec-1", Stage.FLASHCARDS)
    (usage,) = record["gateways"]
    assert usage["model_sha256"] is None
    assert str(missing_model) in caplog.text


def test_shared_model_file_is_hashed_once_per_run(
    runs_root: Path, model_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hashed_paths: list[Path] = []
    original_hash = run_record._file_sha256

    def counting_hash(path: Path) -> str | None:
        hashed_paths.append(path)
        return original_hash(path)

    monkeypatch.setattr(run_record, "_file_sha256", counting_hash)

    with record_llm_run(
        lecture_id="lec-1",
        script_stage=Stage.CRITIC,
        schema_name="Critic",
        root=runs_root,
    ) as recorder:
        recorder.track(_InMemoryGateway(model_file), stage=Stage.CRITIC)
        recorder.track(_InMemoryGateway(model_file), stage=Stage.DRAFTING)

    assert hashed_paths == [model_file]


def test_model_digest_description_states_its_scope() -> None:
    description = GatewayUsage.model_fields["model_sha256"].description or ""

    assert "first shard" in description
    assert "model_path file only" in description


def test_runs_root_is_the_documented_workspace_location() -> None:
    assert LLM_RUNS_ROOT == Path("workspace/data/llm_runs")
    assert run_record_path(LLM_RUNS_ROOT, "lec-1", Stage.EXAMS) == Path(
        "workspace/data/llm_runs/lec-1/exams.json"
    )


def test_runs_root_is_not_a_dashboard_stage_directory() -> None:
    dashboard = _load_dashboard_module()

    stage_directories = {directory for directory, _ in dashboard._STAGES}

    assert LLM_RUNS_ROOT.name not in stage_directories


def test_runs_root_is_not_a_delivery_fingerprint_root() -> None:
    delivery_roots = {
        *delivery.DELIVERY_OUTPUT_ROOTS.values(),
        delivery.DELIVERY_MANIFESTS_ROOT,
        delivery.QUALITY_CERTIFICATES_ROOT,
        delivery.CANONICAL_DOCUMENTS_ROOT,
        delivery.METADATA_ROOT,
    }

    for root in delivery_roots:
        assert LLM_RUNS_ROOT != root
        assert root not in LLM_RUNS_ROOT.parents

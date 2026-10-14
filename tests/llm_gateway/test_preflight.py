"""Success criterion 2: a misconfigured gateway stops every entry point early.

The orchestrator is the first validation layer (D-12). These tests drive it
through ``cli main`` and through the three entry points, and prove that no
phase, no resume predicate and no child process is reached when the gateway
configuration is unknown, unusable or refused.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

import pytest

from lecture_study.cli import main as cli_main
from lecture_study.cli import orchestrator
from lecture_study.cli.orchestrator import (
    PHASE_REGISTRY,
    OrchestratorContext,
    PhaseSpec,
    run_all,
    run_personal_study,
    run_phase,
)
from lecture_study.llm.config import ADAPTER_OVERRIDE_ENV, CONFIG_PATH_ENV
from lecture_study.llm.gateway import Stage

LOCAL_LECTURE_ID = "925101"
EXPECTED_FAILURE_EXIT_CODE = 1
FLASHCARDS_PHASE_NUMBER = 12


class _ReachedPreflight(Exception):
    """Raised by the preflight spy so that no phase ever runs."""


class _PhaseProbe:
    """Records whether a fake phase's resume predicate or runner was reached."""

    def __init__(self) -> None:
        self.artifact_exists_calls = 0
        self.run_calls = 0

    def spec(self, number: int, llm_stages: tuple[Stage, ...]) -> PhaseSpec:
        async def run(_ctx: OrchestratorContext, _lecture_id: str) -> None:
            self.run_calls += 1

        def artifact_exists(_ctx: OrchestratorContext, _lecture_id: str) -> bool:
            self.artifact_exists_calls += 1
            return False

        return PhaseSpec(
            number=number,
            name=f"phase-{number}",
            supports_single_lecture=True,
            artifact_exists=artifact_exists,
            run=run,
            llm_stages=llm_stages,
        )


class _ProcessSpy:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *_args: object, **_kwargs: object) -> None:
        self.calls += 1


@pytest.fixture(autouse=True)
def isolated_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    working_directory = tmp_path / "work"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)
    monkeypatch.delenv(ADAPTER_OVERRIDE_ENV, raising=False)
    monkeypatch.delenv(CONFIG_PATH_ENV, raising=False)
    return working_directory


@pytest.fixture
def phase_two_probe() -> _PhaseProbe:
    return _PhaseProbe()


@pytest.fixture
def phase_five_probe() -> _PhaseProbe:
    return _PhaseProbe()


@pytest.fixture
def process_spy(monkeypatch: pytest.MonkeyPatch) -> _ProcessSpy:
    spy = _ProcessSpy()
    monkeypatch.setattr(orchestrator, "run_bounded_process", spy)
    return spy


@pytest.fixture
def two_phase_registry(
    monkeypatch: pytest.MonkeyPatch,
    phase_two_probe: _PhaseProbe,
    phase_five_probe: _PhaseProbe,
) -> None:
    registry = (
        phase_two_probe.spec(2, ()),
        phase_five_probe.spec(5, (Stage.SEMANTIC,)),
    )
    monkeypatch.setattr(orchestrator, "PHASE_REGISTRY", registry)


def _select_configuration(
    working_directory: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    configuration_path = working_directory / "gateway.yaml"
    configuration_path.write_text(body, encoding="utf-8")
    monkeypatch.setenv(CONFIG_PATH_ENV, str(configuration_path))


def _run_all_through_cli() -> int:
    with pytest.raises(SystemExit) as exit_information:
        cli_main.main(["run-all", LOCAL_LECTURE_ID])
    return int(exit_information.value.code)


def _assert_nothing_started(
    phase_two_probe: _PhaseProbe,
    phase_five_probe: _PhaseProbe,
    process_spy: _ProcessSpy,
) -> None:
    assert phase_two_probe.artifact_exists_calls == 0
    assert phase_two_probe.run_calls == 0
    assert phase_five_probe.artifact_exists_calls == 0
    assert phase_five_probe.run_calls == 0
    assert process_spy.calls == 0


def test_unknown_adapter_exits_before_the_first_phase(
    two_phase_registry: None,
    phase_two_probe: _PhaseProbe,
    phase_five_probe: _PhaseProbe,
    process_spy: _ProcessSpy,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(ADAPTER_OVERRIDE_ENV, "bogus")

    exit_code = _run_all_through_cli()

    assert exit_code == EXPECTED_FAILURE_EXIT_CODE
    assert ADAPTER_OVERRIDE_ENV in caplog.text
    _assert_nothing_started(phase_two_probe, phase_five_probe, process_spy)


def test_llama_cpp_with_a_missing_executable_exits_before_the_first_phase(
    two_phase_registry: None,
    phase_two_probe: _PhaseProbe,
    phase_five_probe: _PhaseProbe,
    process_spy: _ProcessSpy,
    llama_stubs,
    isolated_working_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    absent_executable = isolated_working_directory / "absent" / "llama-completion"
    _select_configuration(
        isolated_working_directory,
        monkeypatch,
        "default_adapter: llama-cpp\n"
        "llama_cpp:\n"
        f"  completion_executable: {absent_executable}\n"
        f"  tokenize_executable: {llama_stubs.tokenize_executable}\n"
        f"  model_path: {llama_stubs.model_path}\n"
        "  model_family: stub-family\n",
    )

    exit_code = _run_all_through_cli()

    assert exit_code == EXPECTED_FAILURE_EXIT_CODE
    assert llama_stubs.control.call_names() == []
    _assert_nothing_started(phase_two_probe, phase_five_probe, process_spy)


def test_codex_for_a_lecture_not_flagged_for_remote_use_exits_before_any_phase(
    two_phase_registry: None,
    phase_two_probe: _PhaseProbe,
    phase_five_probe: _PhaseProbe,
    process_spy: _ProcessSpy,
    tripwire_codex_on_path,
    isolated_working_directory: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry_path = isolated_working_directory / "config" / "local-lectures.local.yaml"
    registry_path.parent.mkdir()
    registry_path.write_text("lectures: []\n", encoding="utf-8")

    exit_code = _run_all_through_cli()

    assert exit_code == EXPECTED_FAILURE_EXIT_CODE
    assert "remote_llm_allowed" in caplog.text
    assert tripwire_codex_on_path.calls() == []
    _assert_nothing_started(phase_two_probe, phase_five_probe, process_spy)


@pytest.fixture
def recorded_preflight_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> list[list[Stage]]:
    recorded: list[list[Stage]] = []

    def spy(stages: Sequence[Stage], *, lecture_id: str) -> None:
        recorded.append(list(stages))
        raise _ReachedPreflight

    monkeypatch.setattr(orchestrator, "preflight_stage_gateways", spy)
    return recorded


@pytest.mark.asyncio
async def test_run_all_preflights_every_gateway_stage_in_phase_order(
    recorded_preflight_stages: list[list[Stage]],
) -> None:
    with pytest.raises(_ReachedPreflight):
        await run_all(OrchestratorContext(), LOCAL_LECTURE_ID)

    assert recorded_preflight_stages == [
        [Stage.SEMANTIC, Stage.DRAFTING, Stage.CRITIC, Stage.EXAMS, Stage.PODCAST]
    ]


@pytest.mark.asyncio
async def test_run_phase_preflights_only_the_stages_of_the_selected_phase(
    recorded_preflight_stages: list[list[Stage]],
) -> None:
    with pytest.raises(_ReachedPreflight):
        await run_phase(OrchestratorContext(), 7, LOCAL_LECTURE_ID)

    assert recorded_preflight_stages == [[Stage.CRITIC, Stage.DRAFTING]]


@pytest.mark.asyncio
async def test_run_personal_study_preflights_the_exam_and_podcast_stages(
    recorded_preflight_stages: list[list[Stage]],
) -> None:
    with pytest.raises(_ReachedPreflight):
        await run_personal_study(OrchestratorContext(), LOCAL_LECTURE_ID)

    assert recorded_preflight_stages == [[Stage.EXAMS, Stage.PODCAST]]


@pytest.mark.asyncio
async def test_a_phase_without_gateway_stages_triggers_no_preflight(
    recorded_preflight_stages: list[list[Stage]],
    monkeypatch: pytest.MonkeyPatch,
    phase_two_probe: _PhaseProbe,
) -> None:
    monkeypatch.setattr(orchestrator, "PHASE_REGISTRY", (phase_two_probe.spec(2, ()),))

    await run_phase(OrchestratorContext(), 2, LOCAL_LECTURE_ID)

    assert recorded_preflight_stages == []
    assert phase_two_probe.run_calls == 1


def test_flashcards_phase_is_not_mistaken_for_a_gateway_stage() -> None:
    row = next(
        spec for spec in PHASE_REGISTRY if spec.number == FLASHCARDS_PHASE_NUMBER
    )

    assert row.name == "flashcards"
    assert row.llm_stages == ()


def test_preflight_failure_names_the_lecture_and_carries_the_typed_message(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(ADAPTER_OVERRIDE_ENV, "bogus")

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit):
        cli_main.main(["run-phase", "5", LOCAL_LECTURE_ID])

    assert LOCAL_LECTURE_ID in caplog.text
    assert '"llama-cpp"' in caplog.text

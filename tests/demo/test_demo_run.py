"""Demo run driver: step order, acceptance rules and the egress confirmation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

DEMO_RUN_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "demo_run.py"
LECTURE_ID = "925100"
ORCHESTRATOR = "lecture_study.cli.orchestrator"
MODEL_PATH = Path("model.bin")


@pytest.fixture
def demo_run() -> ModuleType:
    spec = importlib.util.spec_from_file_location("demo_run", DEMO_RUN_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def log_line(clock: str, message: str) -> str:
    return f"2026-10-15 {clock} INFO {ORCHESTRATOR}: {message}"


def run_all_log(*, last_started_phase: int) -> str:
    lines = []
    for number in range(2, last_started_phase + 1):
        lines.append(
            log_line(
                f"10:{number:02d}:00,000",
                f"phase {number} (p) running for {LECTURE_ID}",
            )
        )
        if number < 10:
            lines.append(
                log_line(
                    f"10:{number:02d}:30,000",
                    f"phase {number} (p) done for {LECTURE_ID}",
                )
            )
    return "\n".join(lines)


def test_pipeline_steps_run_in_the_documented_order(demo_run: ModuleType) -> None:
    steps = demo_run.pipeline_steps(LECTURE_ID, MODEL_PATH)

    assert [step.name for step in steps] == [
        "run_all",
        "personal_study",
        "flashcards",
        "study_guide",
    ]
    assert [step.log_name for step in steps] == [
        "run-all.log",
        "personal-study.log",
        "flashcards.log",
        "guide.log",
    ]
    assert [step.acceptance for step in steps] == [
        "stopped_at_certification",
        "exit_zero",
        "exit_zero",
        "exit_zero",
    ]


def test_pipeline_cli_steps_carry_the_lecture_and_the_model_path(
    demo_run: ModuleType,
) -> None:
    run_all, personal_study = demo_run.pipeline_steps(LECTURE_ID, MODEL_PATH)[:2]

    assert run_all.argv[1:] == (
        "-m",
        "lecture_study.cli.main",
        "run-all",
        LECTURE_ID,
        "--whisper-model-path",
        str(MODEL_PATH),
    )
    assert personal_study.argv[3] == "run-personal-study"
    assert personal_study.required_phases == (8, 9, 13, 14, 15)


def test_study_guide_step_has_no_inference_flag_and_the_demo_schedule(
    demo_run: ModuleType,
) -> None:
    guide = demo_run.pipeline_steps(LECTURE_ID, MODEL_PATH)[3]

    assert Path(guide.argv[1]).name == "generate_personal_study_guides.py"
    assert guide.argv[2] == LECTURE_ID
    assert "--personal-study-inference" not in guide.argv
    assert guide.argv[-2:] == ("--schedule-path", "demo/site/academic-calendar.yaml")


def test_codex_without_confirmation_exits_before_any_preflight(
    demo_run: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("preflight must not run before the confirmation")

    monkeypatch.setattr(demo_run, "preflight_stage_gateways", fail_if_called)

    exit_code = demo_run.main(["--adapter", "codex", "--lecture-id", LECTURE_ID])

    assert exit_code == 2
    assert capsys.readouterr().out.startswith(f"adapter=codex lecture={LECTURE_ID}")


def test_adapter_line_is_printed_first(
    demo_run: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(demo_run, "preflight_stage_gateways", lambda *a, **k: None)

    exit_code = demo_run.main(
        ["--adapter", "llama-cpp", "--lecture-id", LECTURE_ID, "--preflight-only"]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == [
        f"adapter=llama-cpp lecture={LECTURE_ID}",
        "preflight=ok",
    ]


def test_refused_preflight_exits_one_and_prints_the_reason(
    demo_run: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise demo_run.LlmGatewayError("lecture not cleared")

    monkeypatch.setattr(demo_run, "preflight_stage_gateways", refuse)

    exit_code = demo_run.main(
        ["--adapter", "codex", "--lecture-id", LECTURE_ID, "--confirm-remote-egress"]
    )

    assert exit_code == 1
    assert "preflight=refused lecture not cleared" in capsys.readouterr().out


def test_child_environment_forces_the_adapter(
    demo_run: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LECTURE_STUDY_LLM_ADAPTER", "codex")

    environment = demo_run.child_environment(demo_run.AdapterName.LLAMA_CPP)

    assert environment["LECTURE_STUDY_LLM_ADAPTER"] == "llama-cpp"


def test_run_all_accepts_a_log_stopped_at_stage_ten(demo_run: ModuleType) -> None:
    run_all = demo_run.pipeline_steps(LECTURE_ID, MODEL_PATH)[0]

    reason = demo_run.rejection_reason(
        run_all,
        exit_code=1,
        log_text=run_all_log(last_started_phase=10),
        lecture_id=LECTURE_ID,
    )

    assert reason is None


def test_run_all_rejects_a_log_stopped_at_stage_seven(demo_run: ModuleType) -> None:
    run_all = demo_run.pipeline_steps(LECTURE_ID, MODEL_PATH)[0]

    reason = demo_run.rejection_reason(
        run_all,
        exit_code=1,
        log_text=run_all_log(last_started_phase=7),
        lecture_id=LECTURE_ID,
    )

    assert reason is not None
    assert "phases missing or incomplete" in reason


def test_run_all_rejects_an_unexpected_exit_code(demo_run: ModuleType) -> None:
    run_all = demo_run.pipeline_steps(LECTURE_ID, MODEL_PATH)[0]

    reason = demo_run.rejection_reason(
        run_all,
        exit_code=137,
        log_text=run_all_log(last_started_phase=10),
        lecture_id=LECTURE_ID,
    )

    assert reason == "exit code 137"


def test_personal_study_needs_its_five_phases_completed(demo_run: ModuleType) -> None:
    personal_study = demo_run.pipeline_steps(LECTURE_ID, MODEL_PATH)[1]
    complete = "\n".join(
        log_line(
            "11:00:00,000",
            f"phase {number} (p) already done for {LECTURE_ID}, skipping",
        )
        for number in (8, 9, 13, 14, 15)
    )

    accepted = demo_run.rejection_reason(
        personal_study, exit_code=0, log_text=complete, lecture_id=LECTURE_ID
    )
    rejected = demo_run.rejection_reason(
        personal_study, exit_code=0, log_text="", lecture_id=LECTURE_ID
    )

    assert accepted is None
    assert rejected is not None


def test_steps_from_keeps_the_named_step_and_the_ones_after_it(
    demo_run: ModuleType,
) -> None:
    steps = demo_run.pipeline_steps(LECTURE_ID, MODEL_PATH)

    remaining = demo_run.steps_from(steps, "personal_study")

    assert [step.name for step in remaining] == [
        "personal_study",
        "flashcards",
        "study_guide",
    ]


def test_steps_from_refuses_an_unknown_step(demo_run: ModuleType) -> None:
    steps = demo_run.pipeline_steps(LECTURE_ID, MODEL_PATH)

    with pytest.raises(ValueError, match="unknown step"):
        demo_run.steps_from(steps, "exams")


def test_a_rerun_keeps_the_log_of_the_earlier_attempt(
    demo_run: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(demo_run, "DEMO_LOGS_ROOT", tmp_path)

    demo_run._write_log(LECTURE_ID, "run-all.log", "first")
    demo_run._write_log(LECTURE_ID, "run-all.log", "second")
    final_path = demo_run._write_log(LECTURE_ID, "run-all.log", "third")

    log_directory = tmp_path / LECTURE_ID
    assert final_path.read_text() == "third"
    assert (log_directory / "run-all.attempt-1.log").read_text() == "first"
    assert (log_directory / "run-all.attempt-2.log").read_text() == "second"

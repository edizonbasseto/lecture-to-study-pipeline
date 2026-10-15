"""Per-stage wall time from orchestrator log lines and the run acceptance rules."""

import pytest

from lecture_study.demo.phase_timing import (
    PhaseLogError,
    PhaseTiming,
    parse_phase_log,
    require_completed,
    require_stopped_at_certification,
)

LECTURE_ID = "demo-local"
PREFIX = "lecture_study.cli.orchestrator"


def line(clock: str, message: str, *, logger: str = PREFIX) -> str:
    return f"2026-10-15 {clock} INFO {logger}: {message}"


def phase_lines(number: int, name: str, start: str, end: str) -> list[str]:
    return [
        line(start, f"phase {number} ({name}) running for {LECTURE_ID}"),
        line(end, f"phase {number} ({name}) done for {LECTURE_ID}"),
    ]


def run_all_log() -> str:
    lines = [
        line(
            "19:00:00,000", f"phase 2 (frames) already done for {LECTURE_ID}, skipping"
        )
    ]
    for number in range(3, 10):
        lines.extend(
            phase_lines(
                number,
                f"stage{number}",
                f"19:{number:02d}:00,250",
                f"19:{number:02d}:01,500",
            )
        )
    lines.append(line("19:10:00,000", f"phase 10 (certify) running for {LECTURE_ID}"))
    return "\n".join(lines)


def timings_by_number(text: str) -> dict[int, PhaseTiming]:
    return {timing.number: timing for timing in parse_phase_log(text, LECTURE_ID)}


def test_ran_phases_have_millisecond_wall_time() -> None:
    timings = timings_by_number(run_all_log())

    assert timings[3].status == "ran"
    assert timings[3].seconds == pytest.approx(1.25)


def test_skipped_phase_has_no_seconds() -> None:
    timings = timings_by_number(run_all_log())

    assert timings[2].status == "skipped"
    assert timings[2].seconds is None


def test_phase_without_a_done_line_is_incomplete() -> None:
    timings = timings_by_number(run_all_log())

    assert timings[10].status == "incomplete"
    assert timings[10].seconds is None


def test_phases_keep_the_order_of_first_appearance() -> None:
    numbers = [timing.number for timing in parse_phase_log(run_all_log(), LECTURE_ID)]

    assert numbers == list(range(2, 11))


def test_other_lectures_and_loggers_are_ignored() -> None:
    text = "\n".join(
        [
            line("19:00:00,000", "phase 3 (stage3) running for other-lecture"),
            line("19:00:01,000", "phase 3 (stage3) done for other-lecture"),
            line(
                "19:00:02,000",
                f"phase 4 (stage4) running for {LECTURE_ID}",
                logger="x.y",
            ),
            "not a log line at all",
        ]
    )

    assert parse_phase_log(text, LECTURE_ID) == ()


def test_done_without_running_is_refused() -> None:
    text = line("19:00:00,000", f"phase 4 (stage4) done for {LECTURE_ID}")

    with pytest.raises(PhaseLogError, match="phase 4"):
        parse_phase_log(text, LECTURE_ID)


def test_a_second_attempt_replaces_the_first() -> None:
    text = "\n".join(
        [
            line("19:00:00,000", f"phase 5 (stage5) running for {LECTURE_ID}"),
            line("19:01:00,000", f"phase 5 (stage5) running for {LECTURE_ID}"),
            line("19:01:03,000", f"phase 5 (stage5) done for {LECTURE_ID}"),
        ]
    )

    assert timings_by_number(text)[5].seconds == pytest.approx(3.0)


def test_complete_run_all_log_is_accepted() -> None:
    require_stopped_at_certification(parse_phase_log(run_all_log(), LECTURE_ID))


def test_missing_or_incomplete_early_phase_is_refused() -> None:
    without_phase_six = "\n".join(
        text for text in run_all_log().splitlines() if "phase 6 " not in text
    )
    unfinished_phase_six = "\n".join(
        text
        for text in run_all_log().splitlines()
        if "phase 6 (stage6) done" not in text
    )

    for text in (without_phase_six, unfinished_phase_six):
        with pytest.raises(PhaseLogError, match="6"):
            require_stopped_at_certification(parse_phase_log(text, LECTURE_ID))


def test_completed_certification_phase_is_refused() -> None:
    text = (
        run_all_log()
        + "\n"
        + line("19:10:05,000", f"phase 10 (certify) done for {LECTURE_ID}")
    )

    with pytest.raises(PhaseLogError, match="phase 10"):
        require_stopped_at_certification(parse_phase_log(text, LECTURE_ID))


def test_phase_after_certification_is_refused() -> None:
    text = (
        run_all_log()
        + "\n"
        + line("19:11:00,000", f"phase 11 (publish) running for {LECTURE_ID}")
    )

    with pytest.raises(PhaseLogError, match="after the certification gate"):
        require_stopped_at_certification(parse_phase_log(text, LECTURE_ID))


def test_certification_phase_that_never_started_is_refused() -> None:
    text = "\n".join(
        text for text in run_all_log().splitlines() if "phase 10 " not in text
    )

    with pytest.raises(PhaseLogError, match="never started"):
        require_stopped_at_certification(parse_phase_log(text, LECTURE_ID))


def test_require_completed_accepts_ran_and_skipped_phases() -> None:
    require_completed(parse_phase_log(run_all_log(), LECTURE_ID), [2, 3, 9])


def test_require_completed_refuses_missing_and_incomplete_phases() -> None:
    timings = parse_phase_log(run_all_log(), LECTURE_ID)

    with pytest.raises(PhaseLogError, match="10, 13"):
        require_completed(timings, [9, 10, 13])

"""Per-stage wall time read from the orchestrator log (ADR 0047, DEMO-03).

Only the three message shapes of ``lecture_study.cli.orchestrator`` for the
requested lecture id are read; every other line is ignored. ``run-all`` exits
non-zero by design at phase 10, because the certification gate refuses an
uncertified publication, so a log that stops there is the accepted outcome.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "PhaseLogError",
    "PhaseTiming",
    "parse_phase_log",
    "require_completed",
    "require_stopped_at_certification",
]

PhaseStatus = Literal["ran", "skipped", "incomplete"]

_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S,%f"
_ORCHESTRATOR_LINE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) \w+ "
    r"lecture_study\.cli\.orchestrator: "
    r"phase (?P<number>\d+) \((?P<name>[^)]+)\) "
    r"(?P<event>running|done|already done) for (?P<lecture_id>[^\s,]+)"
    r"(?:, skipping)?$"
)
_CERTIFICATION_PHASE = 10
_FIRST_PHASE = 2
_LAST_PHASE_BEFORE_CERTIFICATION = 9
_COMPLETED_STATUSES: tuple[PhaseStatus, ...] = ("ran", "skipped")


class PhaseLogError(RuntimeError):
    """The orchestrator log is inconsistent or does not show the expected run."""


class PhaseTiming(BaseModel):
    """One orchestrated phase and, when it ran to the end, its wall time."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    number: int = Field(ge=0)
    name: str
    status: PhaseStatus
    seconds: float | None = Field(default=None, ge=0)


@dataclass(slots=True)
class _PhaseState:
    name: str
    status: PhaseStatus
    started_at: datetime | None
    seconds: float | None = None


def parse_phase_log(text: str, lecture_id: str) -> tuple[PhaseTiming, ...]:
    """Phases of one lecture in order of first appearance; the latest attempt wins."""
    states: dict[int, _PhaseState] = {}
    for line in text.splitlines():
        match = _ORCHESTRATOR_LINE.match(line)
        if match is None or match["lecture_id"] != lecture_id:
            continue
        _apply_event(
            states,
            number=int(match["number"]),
            name=match["name"],
            event=match["event"],
            moment=datetime.strptime(match["timestamp"], _TIMESTAMP_FORMAT),
        )
    return tuple(
        PhaseTiming(
            number=number,
            name=state.name,
            status=state.status,
            seconds=state.seconds,
        )
        for number, state in states.items()
    )


def require_stopped_at_certification(timings: Sequence[PhaseTiming]) -> None:
    """Accept only a run-all that completed phases 2-9 and stopped inside phase 10."""
    if any(timing.number > _CERTIFICATION_PHASE for timing in timings):
        raise PhaseLogError("a phase after the certification gate appears in the log")
    require_completed(
        timings, range(_FIRST_PHASE, _LAST_PHASE_BEFORE_CERTIFICATION + 1)
    )
    certification = _timing_by_number(timings).get(_CERTIFICATION_PHASE)
    if certification is None:
        raise PhaseLogError("phase 10 never started")
    if certification.status != "incomplete":
        raise PhaseLogError("phase 10 completed although it must refuse")


def require_completed(
    timings: Sequence[PhaseTiming], phase_numbers: Iterable[int]
) -> None:
    """Every listed phase must have run or been skipped as already done."""
    by_number = _timing_by_number(timings)
    unfinished = [
        number
        for number in phase_numbers
        if number not in by_number
        or by_number[number].status not in _COMPLETED_STATUSES
    ]
    if unfinished:
        listed = ", ".join(str(number) for number in unfinished)
        raise PhaseLogError(f"phases missing or incomplete: {listed}")


def _timing_by_number(timings: Sequence[PhaseTiming]) -> dict[int, PhaseTiming]:
    return {timing.number: timing for timing in timings}


def _apply_event(
    states: dict[int, _PhaseState],
    *,
    number: int,
    name: str,
    event: str,
    moment: datetime,
) -> None:
    if event == "running":
        states[number] = _PhaseState(name, "incomplete", started_at=moment)
    elif event == "already done":
        states[number] = _PhaseState(name, "skipped", started_at=None)
    else:
        _close_phase(states, number, moment)


def _close_phase(states: dict[int, _PhaseState], number: int, moment: datetime) -> None:
    state = states.get(number)
    if state is None or state.started_at is None or state.status != "incomplete":
        raise PhaseLogError(f"phase {number} is done without a running line")
    seconds = (moment - state.started_at).total_seconds()
    if seconds < 0:
        raise PhaseLogError(f"phase {number} finished before it started")
    state.status = "ran"
    state.seconds = seconds

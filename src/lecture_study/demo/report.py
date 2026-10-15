"""Deterministic Markdown and JSON rendering of the demo measurements.

The models carry counters, timings, model identifiers and lecture ids only,
so the rendered report holds no prompt or response text and no path. Every
percentage is printed together with its denominator, because the demo lecture
fixes one evidence pack and a bare rate would overstate what was measured.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from lecture_study.demo.asr_measure import AsrMeasurement
from lecture_study.demo.phase_timing import PhaseTiming
from lecture_study.demo.run_metrics import CriticMetrics, StageMetrics
from lecture_study.llm.gateway import AdapterName, Stage

__all__ = [
    "AdapterResults",
    "DemoResults",
    "NotRunAdapter",
    "format_rate",
    "render_results_json",
    "render_results_markdown",
]

_METHOD_DECISION_LINK = "../decisions/0047-demo-lecture-measurement-method.md"
_CONDITION_LABELS = {
    "pipeline_prompt_on": "pipeline (vocabulary prompt on)",
    "ablation_prompt_off": "ablation (vocabulary prompt off)",
}
_CERTIFICATION_PHASE = 10
_FLASHCARDS_PHASE = 18
_STAGE_ORDER = {stage: position for position, stage in enumerate(Stage)}


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class AdapterResults(_FrozenStrictModel):
    """Everything measured for one adapter's run of the demo lecture."""

    adapter: AdapterName
    lecture_id: str
    asr: tuple[AsrMeasurement, ...]
    phases: tuple[PhaseTiming, ...]
    llm_stages: tuple[StageMetrics, ...]
    critic: CriticMetrics | None
    evidence_packs: int = Field(ge=0)
    notes: tuple[str, ...]


class NotRunAdapter(_FrozenStrictModel):
    """An adapter that was not run, with the reason stated."""

    adapter: AdapterName
    reason: str


class DemoResults(_FrozenStrictModel):
    """The measurements of every adapter that was run, and those that were not."""

    measured_on: date
    video_seconds: float = Field(ge=0)
    reference_words: int = Field(ge=0)
    adapters: tuple[AdapterResults, ...]
    adapters_not_run: tuple[NotRunAdapter, ...]


def format_rate(numerator: int, denominator: int, unit: str) -> str:
    """A percentage with its denominator, or `n/a` when nothing was counted."""
    if denominator == 0:
        return f"n/a (0 {unit})"
    percentage = 100 * numerator / denominator
    return f"{percentage:.1f}% ({numerator} of {denominator} {unit})"


def render_results_json(results: DemoResults) -> str:
    return results.model_dump_json(indent=2)


def render_results_markdown(results: DemoResults) -> str:
    adapters = tuple(sorted(results.adapters, key=lambda item: item.adapter.value))
    blocks = [
        _title_block(results),
        _lecture_block(results, adapters),
        _asr_block(adapters),
        _llm_stages_block(adapters),
        _critic_block(adapters),
        _wall_time_block(adapters),
    ]
    if results.adapters_not_run:
        blocks.append(_not_run_block(results.adapters_not_run))
    return "\n\n".join(blocks) + "\n"


def _title_block(results: DemoResults) -> str:
    return "\n".join(
        [
            "# Demo lecture results",
            "",
            f"Measured on {results.measured_on.isoformat()}. Every rate is printed "
            "with its denominator; the method, the word error rate definition and "
            "the limits of a single evidence pack are recorded in "
            f"[ADR 0047]({_METHOD_DECISION_LINK}).",
        ]
    )


def _lecture_block(results: DemoResults, adapters: tuple[AdapterResults, ...]) -> str:
    lines = [
        "## Lecture",
        "",
        f"- Video duration: {_seconds(results.video_seconds)}",
        f"- Reference words: {results.reference_words}",
    ]
    lines.extend(
        f"- Evidence packs ({item.adapter.value}): {item.evidence_packs}"
        for item in adapters
    )
    lines.extend(
        f"- Note ({item.adapter.value}): {note}"
        for item in adapters
        for note in item.notes
    )
    lines.extend(
        [
            "",
            "The demo lecture fixes one evidence pack per adapter (ADR 0047), so "
            "each critic rate rests on a small number of reviews.",
        ]
    )
    return "\n".join(lines)


def _asr_block(adapters: tuple[AdapterResults, ...]) -> str:
    lines = [
        "## ASR word error rate",
        "",
        "| Adapter | Lecture id | Condition | WER before dictionary "
        "| WER after dictionary | Corrections applied |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lines.extend(_asr_row(item, asr) for item in adapters for asr in item.asr)
    lines.extend(["", "### Planted terms", ""])
    lines.extend(
        [
            "| Adapter | Lecture id | Condition | Term | In reference "
            "| Before dictionary | After dictionary |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    lines.extend(
        row
        for item in adapters
        for asr in item.asr
        for row in _planted_term_rows(item, asr)
    )
    return "\n".join(lines)


def _asr_row(item: AdapterResults, asr: AsrMeasurement) -> str:
    return _table_row(
        item.adapter.value,
        item.lecture_id,
        _CONDITION_LABELS[asr.condition],
        format_rate(asr.before.errors(), asr.before.reference_words, "words"),
        format_rate(asr.after.errors(), asr.after.reference_words, "words"),
        str(asr.corrections_applied),
    )


def _planted_term_rows(item: AdapterResults, asr: AsrMeasurement) -> list[str]:
    return [
        _table_row(
            item.adapter.value,
            item.lecture_id,
            _CONDITION_LABELS[asr.condition],
            count.term,
            str(count.reference_occurrences),
            str(count.before_occurrences),
            str(count.after_occurrences),
        )
        for count in asr.planted_terms
    ]


def _llm_stages_block(adapters: tuple[AdapterResults, ...]) -> str:
    lines = ["## LLM stages"]
    for item in adapters:
        lines.extend(["", _adapter_heading(item), ""])
        lines.extend(_llm_stage_table(item.llm_stages))
    lines.extend(
        [
            "",
            "For the exams, podcast and flashcards stages the first-attempt "
            "contract also includes the student-facing text guard.",
        ]
    )
    return "\n".join(lines)


def _llm_stage_table(stages: tuple[StageMetrics, ...]) -> list[str]:
    if not stages:
        return ["No LLM stage record was harvested."]
    ordered = sorted(
        stages, key=lambda stage: (_STAGE_ORDER[stage.script_stage], stage.superseded)
    )
    return [
        "| Stage | Record status | First-attempt schema pass rate | Retries "
        "| Wall time | Model call time | Model | Engine |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        *(_llm_stage_row(stage) for stage in ordered),
    ]


def _llm_stage_row(stage: StageMetrics) -> str:
    label = stage.script_stage.value
    if stage.superseded:
        label += " (superseded)"
    return _table_row(
        label,
        stage.record_status,
        _pass_rate_cell(stage),
        str(stage.retries),
        _seconds(stage.wall_seconds),
        _seconds(stage.call_seconds),
        stage.model_identifier,
        stage.engine_version or "n/a",
    )


def _pass_rate_cell(stage: StageMetrics) -> str:
    if stage.record_status == "failed":
        return "failed"
    return format_rate(stage.first_attempt_passes(), stage.requests, "requests")


def _critic_block(adapters: tuple[AdapterResults, ...]) -> str:
    lines = [
        "## Critic rejection rate",
        "",
        "| Adapter | Record status | Rejection rate | Units | Redrafts "
        "| Finally rejected units |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lines.extend(_critic_row(item) for item in adapters)
    return "\n".join(lines)


def _critic_row(item: AdapterResults) -> str:
    critic = item.critic
    if critic is None:
        return _table_row(
            item.adapter.value, "not recorded", "n/a", "n/a", "n/a", "n/a"
        )
    rate = (
        "failed"
        if critic.record_status == "failed"
        else format_rate(critic.rejected_reviews, critic.reviews, "reviews")
    )
    return _table_row(
        item.adapter.value,
        critic.record_status,
        rate,
        str(critic.units),
        str(critic.redrafts),
        str(critic.final_rejected_units),
    )


def _wall_time_block(adapters: tuple[AdapterResults, ...]) -> str:
    lines = ["## Per-stage wall time"]
    for item in adapters:
        lines.extend(["", _adapter_heading(item), ""])
        lines.extend(
            [
                "| Phase | Name | Wall time |",
                "| --- | --- | --- |",
                *_wall_time_rows(item),
            ]
        )
    return "\n".join(lines)


def _wall_time_rows(item: AdapterResults) -> list[str]:
    rows = [(timing.number, timing.name, _phase_cell(timing)) for timing in item.phases]
    flashcards = _flashcards_row(item)
    if flashcards is not None and all(
        number != _FLASHCARDS_PHASE for number, _, _ in rows
    ):
        rows.append(flashcards)
    return [_table_row(str(number), name, cell) for number, name, cell in sorted(rows)]


def _flashcards_row(item: AdapterResults) -> tuple[int, str, str] | None:
    for stage in item.llm_stages:
        if stage.script_stage is Stage.FLASHCARDS and not stage.superseded:
            cell = (
                "failed"
                if stage.record_status == "failed"
                else _seconds(stage.wall_seconds)
            )
            return (_FLASHCARDS_PHASE, Stage.FLASHCARDS.value, cell)
    return None


def _phase_cell(timing: PhaseTiming) -> str:
    if timing.status == "skipped":
        return "skipped (resumed)"
    if timing.status == "incomplete":
        if timing.number == _CERTIFICATION_PHASE:
            return "refused by design (no certificate is issued)"
        return "incomplete"
    return _seconds(timing.seconds or 0.0)


def _not_run_block(not_run: tuple[NotRunAdapter, ...]) -> str:
    ordered = sorted(not_run, key=lambda item: item.adapter.value)
    lines = ["## Adapters not run", ""]
    lines.extend(f"- {item.adapter.value}: {item.reason}" for item in ordered)
    return "\n".join(lines)


def _adapter_heading(item: AdapterResults) -> str:
    return f"### {item.adapter.value} ({item.lecture_id})"


def _seconds(value: float) -> str:
    return f"{value:.1f} s"


def _table_row(*cells: str) -> str:
    escaped = (cell.replace("|", "\\|") for cell in cells)
    return "| " + " | ".join(escaped) + " |"

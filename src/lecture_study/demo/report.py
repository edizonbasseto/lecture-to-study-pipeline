"""Deterministic Markdown and JSON rendering of the demo measurements.

The models carry counters, timings, model identifiers and lecture ids only,
so the rendered report holds no prompt or response text and no path. Every
percentage is printed together with its denominator, because the demo lecture
fixes one evidence pack and a bare rate would overstate what was measured.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict

from lecture_study.demo.asr_measure import AsrMeasurement
from lecture_study.llm.gateway import AdapterName

__all__ = [
    "AdapterResults",
    "DemoResults",
    "format_rate",
    "render_results_json",
    "render_results_markdown",
]

_METHOD_DECISION_LINK = "../decisions/0047-demo-lecture-measurement-method.md"
_CONDITION_LABELS = {
    "pipeline_prompt_on": "pipeline (vocabulary prompt on)",
    "ablation_prompt_off": "ablation (vocabulary prompt off)",
}


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class AdapterResults(_FrozenStrictModel):
    """Everything measured for one adapter's run of the demo lecture."""

    adapter: AdapterName
    lecture_id: str
    asr: tuple[AsrMeasurement, ...]


class DemoResults(_FrozenStrictModel):
    """The measurements of every adapter that was run."""

    measured_on: date
    adapters: tuple[AdapterResults, ...]


def format_rate(numerator: int, denominator: int, unit: str) -> str:
    """A percentage with its denominator, or `n/a` when nothing was counted."""
    if denominator == 0:
        return f"n/a (0 {unit})"
    percentage = 100 * numerator / denominator
    return f"{percentage:.1f}% ({numerator} of {denominator} {unit})"


def render_results_json(results: DemoResults) -> str:
    return results.model_dump_json(indent=2)


def render_results_markdown(results: DemoResults) -> str:
    blocks = [_title_block(results), _asr_block(_sorted_adapters(results))]
    return "\n\n".join(blocks) + "\n"


def _sorted_adapters(results: DemoResults) -> tuple[AdapterResults, ...]:
    return tuple(sorted(results.adapters, key=lambda item: item.adapter.value))


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


def _table_row(*cells: str) -> str:
    return "| " + " | ".join(cells) + " |"

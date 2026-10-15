"""Complete demo report: sections, honest denominators and deterministic text."""

import re
from datetime import date

from lecture_study.demo.asr_measure import AsrMeasurement, PlantedTermCount
from lecture_study.demo.phase_timing import PhaseTiming
from lecture_study.demo.report import (
    AdapterResults,
    DemoResults,
    NotRunAdapter,
    format_rate,
    render_results_json,
    render_results_markdown,
)
from lecture_study.demo.run_metrics import CriticMetrics, StageMetrics
from lecture_study.demo.wer import WordErrorCounts
from lecture_study.llm.gateway import AdapterName, Stage

PERCENTAGE_WITHOUT_DENOMINATOR = re.compile(r"\d+\.\d%(?! \(\d+ of \d+ [a-z]+\))")
ABSOLUTE_PATH = re.compile(r"(?:^|\s)/\w")


def asr_measurement() -> AsrMeasurement:
    return AsrMeasurement(
        condition="pipeline_prompt_on",
        before=WordErrorCounts(
            reference_words=16, substitutions=2, deletions=0, insertions=0
        ),
        after=WordErrorCounts(
            reference_words=16, substitutions=0, deletions=0, insertions=0
        ),
        corrections_applied=2,
        planted_terms=(
            PlantedTermCount(
                term="histopatológico",
                reference_occurrences=2,
                before_occurrences=0,
                after_occurrences=2,
            ),
        ),
    )


def stage(
    script_stage: Stage,
    *,
    status: str = "succeeded",
    requests: int = 3,
    retries: int = 1,
    superseded: bool = False,
    wall_seconds: float = 41.26,
) -> StageMetrics:
    return StageMetrics.model_validate(
        {
            "script_stage": script_stage,
            "adapter_name": AdapterName.LLAMA_CPP,
            "model_identifier": "qwen-test",
            "engine_version": "engine-1",
            "record_status": status,
            "superseded": superseded,
            "requests": requests,
            "retries": retries,
            "wall_seconds": wall_seconds,
            "call_seconds": 40.0,
        }
    )


def phases() -> tuple[PhaseTiming, ...]:
    return (
        PhaseTiming(number=9, name="guide", status="ran", seconds=3.04),
        PhaseTiming(number=2, name="frames", status="skipped", seconds=None),
        PhaseTiming(number=10, name="certify", status="incomplete", seconds=None),
    )


def local_results(
    *, llm_stages: tuple[StageMetrics, ...] | None = None
) -> AdapterResults:
    return AdapterResults(
        adapter=AdapterName.LLAMA_CPP,
        lecture_id="demo-local",
        asr=(asr_measurement(),),
        phases=phases(),
        llm_stages=llm_stages
        if llm_stages is not None
        else (
            stage(Stage.EXAMS),
            stage(Stage.SEMANTIC, requests=1, retries=0),
            stage(Stage.FLASHCARDS, wall_seconds=8.04),
        ),
        critic=CriticMetrics(
            record_status="succeeded",
            reviews=2,
            redrafts=1,
            units=1,
            final_rejected_units=0,
            rejected_reviews=1,
        ),
        evidence_packs=1,
        notes=("Resumed from the stage 2 artifacts.",),
    )


def remote_results() -> AdapterResults:
    return AdapterResults(
        adapter=AdapterName.CODEX,
        lecture_id="demo-remote",
        asr=(),
        phases=(),
        llm_stages=(),
        critic=None,
        evidence_packs=1,
        notes=(),
    )


def two_adapter_results(
    local: AdapterResults | None = None,
) -> DemoResults:
    return DemoResults(
        measured_on=date(2026, 10, 15),
        video_seconds=241.5,
        reference_words=16,
        adapters=(local or local_results(), remote_results()),
        adapters_not_run=(),
    )


def test_every_section_is_rendered_in_order() -> None:
    results = two_adapter_results().model_copy(
        update={
            "adapters_not_run": (
                NotRunAdapter(adapter=AdapterName.CODEX, reason="no network egress"),
            )
        }
    )

    markdown = render_results_markdown(results)

    headings = re.findall(r"^## .*$", markdown, flags=re.MULTILINE)
    assert headings == [
        "## Lecture",
        "## ASR word error rate",
        "## LLM stages",
        "## Critic rejection rate",
        "## Per-stage wall time",
        "## Adapters not run",
    ]
    assert "- codex: no network egress" in markdown


def test_adapters_not_run_section_is_absent_when_everything_ran() -> None:
    assert "## Adapters not run" not in render_results_markdown(two_adapter_results())


def test_adapters_render_sorted_by_name() -> None:
    markdown = render_results_markdown(two_adapter_results())

    assert markdown.index("### codex (demo-remote)") < markdown.index(
        "### llama-cpp (demo-local)"
    )


def test_every_percentage_is_followed_by_its_denominator() -> None:
    markdown = render_results_markdown(two_adapter_results())

    assert "%" in markdown
    assert PERCENTAGE_WITHOUT_DENOMINATOR.search(markdown) is None
    assert "50.0% (1 of 2 reviews)" in markdown
    assert "66.7% (2 of 3 requests)" in markdown


def test_format_rate_prints_n_a_for_an_empty_denominator() -> None:
    assert format_rate(0, 0, "reviews") == "n/a (0 reviews)"
    assert format_rate(2, 16, "words") == "12.5% (2 of 16 words)"


def test_failed_stage_record_renders_failed_without_a_percentage() -> None:
    failed = stage(Stage.PODCAST, status="failed", requests=1, retries=0)

    markdown = render_results_markdown(
        two_adapter_results(local_results(llm_stages=(failed,)))
    )

    podcast_row = next(
        text for text in markdown.splitlines() if text.startswith("| podcast |")
    )
    assert "| failed | failed |" in podcast_row
    assert "%" not in podcast_row


def test_superseded_record_renders_as_an_extra_marked_row() -> None:
    stages = (
        stage(Stage.EXAMS, superseded=True),
        stage(Stage.EXAMS, status="failed"),
    )

    markdown = render_results_markdown(
        two_adapter_results(local_results(llm_stages=stages))
    )

    rows = [text for text in markdown.splitlines() if text.startswith("| exams")]
    assert [row.split(" | ")[0] for row in rows] == [
        "| exams",
        "| exams (superseded)",
    ]


def test_stages_follow_the_stage_enum_order() -> None:
    markdown = render_results_markdown(two_adapter_results())

    semantic = markdown.index("| semantic |")
    exams = markdown.index("| exams |")
    flashcards = markdown.index("| flashcards |")
    assert semantic < exams < flashcards


def test_certification_phase_renders_as_a_designed_refusal() -> None:
    markdown = render_results_markdown(two_adapter_results())

    assert "| 10 | certify | refused by design (no certificate is issued) |" in markdown
    assert "| 2 | frames | skipped (resumed) |" in markdown
    assert "| 9 | guide | 3.0 s |" in markdown


def test_stage_18_wall_time_comes_from_the_flashcards_record() -> None:
    markdown = render_results_markdown(two_adapter_results())

    assert "| 18 | flashcards | 8.0 s |" in markdown
    assert markdown.index("| 10 | certify") < markdown.index("| 18 | flashcards")


def test_phases_are_listed_by_number() -> None:
    markdown = render_results_markdown(two_adapter_results())

    assert (
        markdown.index("| 2 | frames")
        < markdown.index("| 9 | guide")
        < markdown.index("| 10 | certify")
    )


def test_adapter_without_records_says_so() -> None:
    markdown = render_results_markdown(two_adapter_results())

    assert "No LLM stage record was harvested." in markdown
    assert "| codex | not recorded | n/a |" in markdown


def test_lecture_section_states_duration_words_packs_and_notes() -> None:
    markdown = render_results_markdown(two_adapter_results())

    assert "- Video duration: 241.5 s" in markdown
    assert "- Reference words: 16" in markdown
    assert "- Evidence packs (llama-cpp): 1" in markdown
    assert "- Note (llama-cpp): Resumed from the stage 2 artifacts." in markdown


def test_rendering_twice_is_identical() -> None:
    results = two_adapter_results()

    assert render_results_markdown(results) == render_results_markdown(results)
    assert render_results_json(results) == render_results_json(results)


def test_markdown_contains_no_absolute_path() -> None:
    markdown = render_results_markdown(two_adapter_results())

    assert ABSOLUTE_PATH.search(markdown) is None
    assert "/Users" not in markdown


def test_json_round_trips() -> None:
    results = two_adapter_results()

    assert DemoResults.model_validate_json(render_results_json(results)) == results


def test_pipe_in_a_cell_does_not_break_the_table() -> None:
    odd = stage(Stage.SEMANTIC).model_copy(update={"model_identifier": "a|b"})

    markdown = render_results_markdown(
        two_adapter_results(local_results(llm_stages=(odd,)))
    )

    assert "a\\|b" in markdown

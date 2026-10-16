---
phase: 07-synthetic-demo-lecture-and-measured-results
plan: 03
subsystem: demo
tags: [wer, asr, run-records, critic, orchestrator-log, report, pydantic]
requires:
  - phase: 07-01
    provides: "lecture_study.demo package, DemoScript.reference_text() and planted_terms"
provides:
  - "lecture_study.demo.wer: normalized_words, WordErrorCounts, word_error_counts"
  - "lecture_study.demo.asr_measure: WER before/after the terminology dictionary, pipeline and ablation conditions"
  - "lecture_study.demo.run_metrics: StageMetrics, CriticMetrics, parse_run_record"
  - "lecture_study.demo.phase_timing: parse_phase_log and the run-all acceptance rules"
  - "lecture_study.demo.report: AdapterResults, DemoResults, NotRunAdapter, Markdown and JSON renderers"
affects: [07-06, 07-07, 07-08]
tech-stack:
  added: []
  patterns:
    - "pure typed measurement logic fed by parsed data; no file or process I/O"
    - "format_rate is the only percentage formatter, so every rate carries its denominator"
key-files:
  created:
    - src/lecture_study/demo/wer.py
    - src/lecture_study/demo/asr_measure.py
    - src/lecture_study/demo/run_metrics.py
    - src/lecture_study/demo/phase_timing.py
    - src/lecture_study/demo/report.py
    - tests/demo/test_demo_wer.py
    - tests/demo/test_demo_asr_measure.py
    - tests/demo/test_demo_run_metrics.py
    - tests/demo/test_demo_phase_timing.py
    - tests/demo/test_demo_report.py
  modified: []
key-decisions:
  - "First-attempt passes are requests minus retries, where requests = calls - retry_calls of the stage's own gateway entry"
  - "Critic rejected reviews = redrafts + units whose final review still rejected, never above the review count"
  - "A run-all log is accepted only when phases 2-9 ran or were skipped and phase 10 started but never completed"
patterns-established:
  - "Each measurement module owns its error class (AsrMeasurementError, RunMetricsError, PhaseLogError); validation errors expose field locations only"
requirements-completed: [DEMO-03]
duration: 25 min
completed: 2026-10-15
status: complete
commits: 3
plan_head_before: 93086ccea42907f89864d5924c687e9c65aa0288
plan_head_after: b4031d630952556829e2af45556257cfb9bd802c
actuals:
  tokens: 15200
  tasks: 3
  commits: 3
---

# Phase 7 Plan 03: Measurement logic and report renderer Summary

Pure, typed DEMO-03 measurement in `lecture_study.demo`: stdlib word error rate, ASR before/after the dictionary under two conditions, first-attempt schema pass and critic rejection counts from run records, per-phase wall time from orchestrator logs, and a deterministic Markdown/JSON report that prints N beside every rate.

## Tasks

| Task | Name | Commit |
| ---- | ---- | ------ |
| 1 | Tracer: transcript, corrections and reference to a rendered WER before/after section | da1c22a |
| 2 | Stage metrics from run records and per-stage wall time from orchestrator logs | 96d1995 |
| 3 | Complete report: LLM stages, critic, wall time, adapters not run, N beside every rate | b4031d6 |

`plan_head_before` is the parent of the first plan commit. Plans 07-02, 07-04 and 07-05 committed concurrently on the same branch, so `git rev-list --count plan_head_before..plan_head_after` is higher than 3; `commits: 3` counts the three `(07-03)` commits only.

## What was built

- `wer.py`: NFC-normalized, casefolded tokens matched by `\w+(?:[-']\w+)*`; dynamic-programming edit distance; backtrace order diagonal, deletion, insertion, so counts are deterministic. An empty reference raises `ValueError`. No `jiwer`.
- `asr_measure.py`: `uncorrected_segments` rebuilds the raw ASR text from the recorded corrections and fails (naming the start time) when a correction matches no segment or its `corrected_text` no longer equals the stored text. `measure_pipeline_transcript` (condition `pipeline_prompt_on`) and `measure_unprompted_transcript` (`ablation_prompt_off`, dictionary applied through `correct_specialist_terminology`). Planted-term occurrence counts for reference, before and after.
- `run_metrics.py`: `parse_run_record` (strict extra-forbid model, errors carry field locations only), `stage_metrics`, `critic_metrics`.
- `phase_timing.py`: only `lecture_study.cli.orchestrator` lines for the requested lecture id are read; millisecond timestamps; latest attempt of a phase wins; `require_stopped_at_certification` and `require_completed`.
- `report.py`: sections Lecture, ASR word error rate, LLM stages, Critic rejection rate, Per-stage wall time and, when present, Adapters not run. Adapters sorted by name, stages in `Stage` enum order, phases by number. A failed record renders `failed` with no percentage; a superseded record renders as an extra row marked `(superseded)`; phase 10 incomplete renders `refused by design (no certificate is issued)`; phase 18 comes from the non-superseded flashcards record. Table cells escape `|`.

## Assumption A3 check (one `complete_json` per request, at most one retry, no retry on gateway errors)

`complete_json` retries once on `retry_on` failures and re-raises `LlmGatewayError` without retrying. Result per script:

| Script | Result |
| ------ | ------ |
| `generate_codex_semantic_ledgers.py` | Holds. One `complete_json` per pack in `_generate_ledger` (`retry_on` also covers `LlmParsingError`, still one retry). |
| `generate_codex_drafts.py` | Holds. One `complete_json` in `_generate_draft`. |
| `generate_codex_critic_review.py` | Holds. Each review is one critic-stage `complete_json` (`_generate_review`); each repair redraft is one drafting-stage request through `_generate_draft` on the second tracked gateway, under the critic record. |
| `generate_codex_mock_exam.py` | Holds. `complete_json` only inside `_request_questions`, called once initially and once per supplemental `while` iteration; each call is a counted request. |
| `generate_codex_podcast.py` | Holds. One `complete_json`. |
| `generate_codex_personal_study_flashcards.py` | Holds. `complete_json` only inside `_request_cards`, same loop shape as exams. |

No deviation found, so the formulas were not adapted.

## Verification

- `uv run pytest tests/demo -q`: 158 passed, 1 skipped (includes other plans' tests present in the checkout).
- `ruff check .`, `ruff format --check .`, `mypy src` (strict): clean at the end.
- Acceptance greps: no `jiwer` in `src`, `tests` or `pyproject.toml`; no `subprocess|open(|read_text|write_text` in the five measurement modules; `pyproject.toml` and `uv.lock` untouched.
- Tracer gate: the Task 1 slice (transcript, corrections, reference to rendered Markdown and JSON round trip) passed before Task 2 started.

## Deviations from Plan

**1. [Rule 1 - Ambiguity] Pass-count wording**
- **Issue:** the must-have text says the first-attempt pass count is "calls minus retry_calls", which is the request count; the behavior block and action text define `first_attempt_passes()` as requests minus retries.
- **Fix:** followed the behavior block (calls 3, retry 1 gives requests 2, retries 1, passes 1). Documented here so 07-06 and 07-08 use the same definition.

**2. [Rule 2 - Missing validation] Inconsistent counters are refused**
- **Fix:** `stage_metrics` raises when retries exceed requests; `critic_metrics` raises when rejections exceed reviews; `require_stopped_at_certification` also raises when phase 10 never started. These guard against reporting an impossible rate.
- **Commit:** 96d1995

**3. [Plan detail] `AdapterResults` and `DemoResults` new fields are required**
- Task 3 fields (`phases`, `llm_stages`, `critic`, `evidence_packs`, `notes`, `video_seconds`, `reference_words`, `adapters_not_run`) have no defaults so a caller cannot omit them silently; the Task 1 tracer test was updated accordingly.
- **Commit:** b4031d6

Otherwise the plan was executed as written. A full-repo `ruff format --check` briefly flagged `scripts/demo_site.py`, an uncommitted file of a concurrent plan; it was not touched.

## Known Stubs

None.

## Threat Flags

None. T-07-08: strict extra-forbid models and typed errors with field locations only (tested, input values are not echoed). T-07-09: `format_rate` is the only percentage formatter and always prints the denominator; failed records render `failed`; adapters not run are listed with a reason. T-07-10: models hold counters, timings, identifiers and lecture ids only; a test asserts no absolute path in the Markdown.

## Self-Check: PASSED

All ten files exist and commits da1c22a, 96d1995 and b4031d6 are ancestors of HEAD.

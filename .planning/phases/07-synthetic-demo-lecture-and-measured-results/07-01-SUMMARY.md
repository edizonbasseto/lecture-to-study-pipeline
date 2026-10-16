---
phase: 07-synthetic-demo-lecture-and-measured-results
plan: 01
subsystem: demo
tags: [kokoro, tts, pydantic, strict-yaml, narration-lint, pt-br]
requires: []
provides:
  - "lecture_study.tts.generator.synthesize_kokoro_wav and KokoroVoice (one shared Kokoro helper)"
  - "lecture_study.demo.script: DemoScript, DemoSlide, load_demo_script, narration lint"
  - "demo/lecture/script.yaml: single versioned source of the demo lecture"
affects: [07-02, 07-03, 07-04, 07-05, 07-06, 07-07, 07-08]
tech-stack:
  added: []
  patterns:
    - "strict frozen pydantic models loaded through load_strict_yaml"
    - "named narration lint rules as small predicates"
key-files:
  created:
    - src/lecture_study/demo/__init__.py
    - src/lecture_study/demo/script.py
    - demo/lecture/script.yaml
    - tests/demo/test_demo_script.py
  modified:
    - src/lecture_study/tts/generator.py
    - tests/test_tts.py
decisions:
  - "Narrator voice pm_alex; the model also accepts pf_dora (D-03), a one-field edit"
  - "Duplicate-background check is a field validator on slides so the error names the slides field"
  - "Plan work committed on branch gsd/phase-07-synthetic-demo because main is a protected branch for the executor commit guard"
metrics:
  duration: 4 min
  completed: 2026-10-15
status: complete
commits: 2
plan_head_before: f7b9d51d301bd2b0f89638e68e7b210773eed6be
plan_head_after: da394fb277d8043b19468c8a4cc205cdb51e78fd
actuals:
  tokens: 7500
  tasks: 2
  commits: 2
requirements: [DEMO-01]
---

# Phase 7 Plan 01: Shared Kokoro helper, demo script model and authored lecture Summary

One public `synthesize_kokoro_wav` helper now serves both the podcast stage and the demo, and `demo/lecture/script.yaml` holds the authored pt-BR lecture (six slides, 620 narration words, 15 planted terms) behind a strict, linted model.

## Tasks

| Task | Name | Commit |
| ---- | ---- | ------ |
| 1 | Tracer: YAML slide to Kokoro wav through the shared helper | ed8089a |
| 2 | Narration lint and the authored pt-BR demo lecture | da394fb |

## What was built

- `synthesize_kokoro_wav(text, *, voice, wav_path, failure_subject)` extracted from `PodcastAudioGenerator._synthesize_chunks`; same argv, sanitized environment, timeout and output caps; non-wav path raises `ValueError` before any process starts; podcast error messages stay byte-identical. `KokoroVoice = Literal["pf_dora", "pm_alex"]`.
- `lecture_study.demo` package (empty `__all__`, nothing aggregated) with `script.py`: frozen strict `DemoSlide` and `DemoScript`, `reference_text()`, `narration_text()`, `load_demo_script` (strict YAML, errors name path and field locations only), `narration_violations` and `require_clean_narration` (nine named rules).
- `demo/lecture/script.yaml`: six slides alternating dark and light backgrounds of different hues; 620 words (about 3.9 minutes at 160 words per minute); 15 planted terms; 11 fascia lines with a depth anchor; "histopatológico" repeated 8 times in narration; exactly one `nervo facial` negative control. Slides 3 and 4 carry fascia and histopathology terms in their bullets.

## Verification

- `uv run pytest -q`: 2735 passed.
- `ruff check`, `ruff format --check`, `mypy src` (strict): clean.
- Acceptance greps: one `"mlx_audio.tts.generate"` site in `generator.py`; no `mlx_audio` text in `src/lecture_study/demo`; no pipeline import of `lecture_study.demo`; `pyproject.toml` and `uv.lock` unchanged; no digit in the YAML outside comments and colour values.
- Tracer gate: the YAML slide reaches the Kokoro argv through the shared helper (automated test, re-run green) before the expansion task.

## Deviations from Plan

### Execution environment

**1. [Rule 3 - Blocking] Branch created because main is protected**
- **Found during:** Task 1 commit
- **Issue:** the pre-commit guard (`gsd_run query git.base-branch --is-protected main` returned `true`) forbids committing on main, and no `.planning/config.json` override exists. The prompt asked for work on main.
- **Fix:** created branch `gsd/phase-07-synthetic-demo` from main HEAD `f7b9d51` and committed both tasks there; no configuration was changed. Main can be fast-forwarded to it by the operator or orchestrator.
- **Commits:** ed8089a, da394fb

### Plan details adjusted

**2. [Rule 1 - Bug] Duplicate-background validator moved to a field validator**
- **Issue:** a model-level validator reported the error at `<root>`, hiding the cause; the plan asks for field locations.
- **Fix:** `@field_validator("slides")` so the error names `slides`.
- **Commit:** ed8089a

Otherwise the plan was executed as written. `gsd_run` is not on PATH in this shell, so the SDK was invoked by its full path.

## Known Stubs

None.

## Threat Flags

None. T-07-01 (argv as one opaque `--text` argument, bounded process) is covered by the tracer and helper tests; T-07-02 by the provenance header and lint; T-07-03 by `load_strict_yaml` and strict extra-forbid frozen models.

## Self-Check: PASSED

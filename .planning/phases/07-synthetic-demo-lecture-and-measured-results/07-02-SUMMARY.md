---
phase: 07-synthetic-demo-lecture-and-measured-results
plan: 02
subsystem: demo
tags: [typst, ffmpeg, kokoro, ocr, make, pt-br]
requires:
  - "07-01: demo script model, narration lint, shared Kokoro helper"
provides:
  - "make demo-video: rebuilds workspace/demo/aula-demo.mp4 from script.yaml and slides.typ"
  - "lecture_study.demo.media_build: pure typst/ffmpeg/ffprobe command builders, duration band, heading coverage, BuildSummary"
  - "workspace/demo/build/build-summary.json: measured frames, headings, duration and midpoint slide"
affects: [07-03, 07-04, 07-05, 07-06, 07-07, 07-08]
tech-stack:
  added: []
  patterns:
    - "pure argument-list builders in src, thin process driver in scripts"
    - "build accepted only by the pipeline's own extraction and OCR"
key-files:
  created:
    - demo/lecture/slides.typ
    - src/lecture_study/demo/media_build.py
    - scripts/build_demo_lecture.py
    - tests/demo/test_demo_build.py
  modified:
    - Makefile
decisions:
  - "Slide segments are encoded with libx264 and AAC at 24 kHz, 25 fps, so the concat demuxer can join them with -c copy"
  - "The driver changes directory to the repository root so the relative build paths hold whatever the caller's cwd"
metrics:
  duration: 9 min
  completed: 2026-10-15
status: complete
commits: 3
plan_head_before: dbaec70f1067f25841c09e1a09cbb35175b5a0ca
plan_head_after: 4635bbd7e8cb9f7d0fe5023bbab6879f424e7342
actuals:
  tokens: 9000
  tasks: 2
  commits: 3
requirements: [DEMO-01]
---

# Phase 7 Plan 02: Demo lecture video build Summary

`make demo-video` now turns `script.yaml` and `slides.typ` into a narrated 243.6 second H.264/AAC video under `workspace/demo/`, accepted only after the repository's own `extract_frames` and Apple Vision OCR recover all six slide headings.

## Tasks

| Task | Name | Commits |
| ---- | ---- | ------- |
| 1 | Tracer: `make demo-video` composes the video | 93086cc |
| 2 | Accept the build through extraction and OCR (TDD) | 75fb64b (RED), 4635bbd (GREEN) |

## What was built

- `demo/lecture/slides.typ`: six 1920x1080 pages from `yaml("script.yaml")`, each with the slide background, foreground, large heading and bullet list. Compile-tested first (research assumption A1 holds on typst 0.15.1).
- `src/lecture_study/demo/media_build.py`: pure builders `slide_images_command`, `duration_probe_command`, `parse_probed_duration`, `slide_segment_command`, `concat_list_text`, `concat_command`; acceptance helpers `missing_headings`, `require_duration_in_band` (200 to 290 s), `midpoint_slide_number`; strict frozen `BuildSummary`. No process execution, no `subprocess` import.
- `scripts/build_demo_lecture.py`: thin driver. Resolves typst, ffmpeg and ffprobe with `resolve_trusted_executable`, runs every call through `run_bounded_process` (typst 120 s, ffprobe 30 s, ffmpeg 600 s, output caps), resets only `workspace/demo/build` after a containment check, narrates each slide with `synthesize_kokoro_wav`, concatenates into a partial file and renames it onto the video path, then runs `extract_frames`, `extract_text_supervised`, the duration band and writes the summary. Failures print one `error:` line and exit 1, leaving the video for inspection.
- `Makefile`: `demo-video` target and `.PHONY` entry.

## Measured result (real build)

`workspace/demo/build/build-summary.json`: `video_seconds` 243.65, per-slide seconds 37.4, 41.1, 36.9, 44.5, 46.6, 37.1, `frames_extracted` 9, `headings_found` 6 of 6, `midpoint_slide_number` 4 (a key term slide, so no warning). `ffprobe` lists `h264` and `aac`; the video path is gitignored and no `*.mp4`, `*.wav`, `*.png` or `workspace/` file is tracked. A full build takes about 30 to 40 seconds.

## TDD Gate Compliance (Task 2)

- RED (75fb64b): tests plus neutral stubs; six target tests failed on assertions for the planned behavior (`missing_headings`, `require_duration_in_band`, `midpoint_slide_number`), 21 others passed. An earlier collection-time import error was discarded as INVALID_RED before stubs were added. Semantic assessment: failures were assertion mismatches on the intended behavior, not import, fixture or syntax faults. The machine classifier `check tdd-red-evidence` was not run (pytest output is not one of its supported report formats), so the assessment is manual.
- GREEN (4635bbd): implementation of the three helpers plus the driver acceptance step; 27 tests pass. One test input was corrected at GREEN (the half-way point of the first midpoint case sat exactly on a slide boundary); the expected slide for the corrected input is unambiguous.
- REFACTOR: none needed.

## Verification

- `uv run pytest tests/demo/test_demo_build.py -q -rs`: 27 passed, nothing skipped (the real typst compile test ran).
- `make demo-video` exit 0; the jq acceptance expression returned true.
- `uv run ruff check .`, `ruff format --check .`, `mypy src` (strict): clean at the time of the final commit.
- Acceptance greps: no `shell=True`, `os.system`, `shlex` in the driver or builders; no `subprocess` or `process_lifecycle` in `media_build.py`.

## Deviations from Plan

None to the plan's behavior. Notes on execution:

- Branch `gsd/phase-07-synthetic-demo` was used as instructed; other executors (07-03, 07-04, 07-05) committed concurrently, so `git rev-list ${plan_head_before}..HEAD` counts their commits as well. `commits: 3` is the measured count of this plan's own commits (`git rev-list --count --grep='(07-02)'`), and `plan_head_after` is this plan's last commit, not the branch tip at write time.
- A repository-wide `ruff format src tests` was run once to settle formatting; it changed no file outside this plan's files (the visible `recovery.py` change belongs to 07-05).

## Human check pending

Listening to `workspace/demo/aula-demo.mp4` (intelligibility of the `pm_alex` voice, no clipped sentence at a slide cut, no third-party material) was not performed by the executor; the plan marks it as a human check. The automated checks prove extraction, OCR and duration only.

## Known Stubs

None.

## Threat Flags

None. T-07-04 (argument-list builders, trusted executables, bounded runs, grep-checked), T-07-05 (outputs only under gitignored `workspace/demo/`), T-07-06 (finite timeouts and output caps on every call) and T-07-07 (only `BUILD_DIR` removed, after a resolved-path containment check) are implemented as planned.

## Self-Check: PASSED

Files found: `demo/lecture/slides.typ`, `src/lecture_study/demo/media_build.py`, `scripts/build_demo_lecture.py`, `tests/demo/test_demo_build.py`, `Makefile` target. Commits 93086cc, 75fb64b, 4635bbd are ancestors of HEAD.

---
phase: 07-synthetic-demo-lecture-and-measured-results
verified: 2026-10-16T12:00:00Z
status: passed
score: 4/4 must-haves verified
covered_files:
  - ".planning/phases/07-synthetic-demo-lecture-and-measured-results/07-01-PLAN.md"
  - ".planning/phases/07-synthetic-demo-lecture-and-measured-results/07-01-SUMMARY.md"
  - ".planning/phases/07-synthetic-demo-lecture-and-measured-results/07-02-PLAN.md"
  - ".planning/phases/07-synthetic-demo-lecture-and-measured-results/07-02-SUMMARY.md"
  - ".planning/phases/07-synthetic-demo-lecture-and-measured-results/07-03-PLAN.md"
  - ".planning/phases/07-synthetic-demo-lecture-and-measured-results/07-03-SUMMARY.md"
  - ".planning/phases/07-synthetic-demo-lecture-and-measured-results/07-04-PLAN.md"
  - ".planning/phases/07-synthetic-demo-lecture-and-measured-results/07-04-SUMMARY.md"
  - ".planning/phases/07-synthetic-demo-lecture-and-measured-results/07-05-PLAN.md"
  - ".planning/phases/07-synthetic-demo-lecture-and-measured-results/07-05-SUMMARY.md"
  - ".planning/phases/07-synthetic-demo-lecture-and-measured-results/07-06-PLAN.md"
  - ".planning/phases/07-synthetic-demo-lecture-and-measured-results/07-06-SUMMARY.md"
  - ".planning/phases/07-synthetic-demo-lecture-and-measured-results/07-07-PLAN.md"
  - ".planning/phases/07-synthetic-demo-lecture-and-measured-results/07-07-SUMMARY.md"
  - ".planning/phases/07-synthetic-demo-lecture-and-measured-results/07-08-PLAN.md"
  - ".planning/phases/07-synthetic-demo-lecture-and-measured-results/07-08-SUMMARY.md"
  - "Makefile"
  - "demo/lecture/script.yaml"
  - "demo/lecture/slides.typ"
  - "demo/site/academic-calendar.yaml"
  - "docs/decisions/0047-demo-lecture-measurement-method.md"
  - "docs/results/demo-results.json"
  - "docs/results/demo-results.md"
  - "scripts/build_demo_lecture.py"
  - "scripts/demo_blocked_recovery.py"
  - "scripts/demo_results.py"
  - "scripts/demo_run.py"
  - "scripts/demo_site.py"
  - "src/lecture_study/demo/__init__.py"
  - "src/lecture_study/demo/asr_measure.py"
  - "src/lecture_study/demo/harvest.py"
  - "src/lecture_study/demo/lecture_id.py"
  - "src/lecture_study/demo/media_build.py"
  - "src/lecture_study/demo/phase_timing.py"
  - "src/lecture_study/demo/recovery.py"
  - "src/lecture_study/demo/report.py"
  - "src/lecture_study/demo/run_metrics.py"
  - "src/lecture_study/demo/script.py"
  - "src/lecture_study/demo/site_access.py"
  - "src/lecture_study/demo/tree_snapshot.py"
  - "src/lecture_study/demo/wer.py"
  - "src/lecture_study/tts/generator.py"
covered_digest: "v3:sha256:e4cee7c32ff46c0d4dd8b3a4595e05d3a043aa6ba6b4f3ffc72a10ccdcdf3cb5"
behavior_unverified: 0
overrides_applied: 1
overrides:
  - must_have: "after the ADR 0045 recovery the exam, podcast, flashcards and study guide scripts produce the exam, podcast, flashcards and study guide for the demo lecture"
    reason: "Partial recovery accepted as the DEMO-04 outcome: blocking proven in 5 of 5 runs and both scenarios (all four scripts exit 1, every output root unchanged, critic rerun exit 0); podcast and study guide recovered in 10 of 10 scenario runs; flashcards recovered in 6 of 6 at deck floor 5 (0 of 4 at the default floor of 12); mock exam recovered in 1 of 10 scenario runs (attempt 4, rejected scenario). Exam failures are contract failures of the local 14B Q4 model, not gate or critic outcomes. The last run did not pass in either scenario and the report says so."
    accepted_by: "Edizon Basseto (operator), answer 'Aceitar parcial e parar para revisao', relayed by the orchestrating agent"
    accepted_at: "2026-10-15T00:00:00Z"
re_verification:
  previous_status: gaps_found
  previous_score: 3/4
  gaps_closed:
    - "DEMO-04 recovery shortfall: carried as an accepted override (not as a fix); numbers unchanged and kept honest"
    - "DEMO-03 codex warning: the Codex adapter was measured on lecture 925101 and is now in the committed report"
    - "Review mediums MD-01 to MD-04: fixed in commit 9a11be7"
  gaps_remaining: []
  regressions: []
gaps: []
deferred: []
advisory:
  - finding: "ADR 0047 decision 4 (not the results report) mentions that the codex run used a temporary edit of the user-level codex configuration"
    category: other
    reason: "Review item IN-04 of 07-REVIEW-FIX.md; no secret and no model name of the default configuration remains in the report. Wording in the ADR describes the operator's machine setup in a public artifact"
    evidence_status: "gitleaks and regex scans in 07-SECURITY-FIX.md found no secret; grep of docs/results/demo-results.md finds no user-level configuration detail"
---

# Phase 7: Synthetic demo lecture and measured results Verification Report

**Phase Goal:** A lecture authored for this repository runs end to end to a served page, and the pipeline's quality is reported with real numbers
**Verified:** 2026-10-16T04:00:00Z
**Status:** passed (the two human checks were replaced by automated equivalents on 2026-10-16: ffprobe of the video, 1920x1080 h264 plus aac, 243.6 s, narration intelligible by the 0.5% ASR word error rate; HTTP signed-in checks for both lectures)
**Re-verification:** Yes (source digest refresh after ADR 0047 changed), after the Codex measurement, the MD-01 to MD-04 fixes and the operator acceptance of the partial DEMO-04 result
**Branch:** gsd/phase-07-synthetic-demo (HEAD a2b2c8c)

## Goal Achievement

### Observable Truths (ROADMAP success criteria)

| # | Truth | Status | Evidence |
| - | ----- | ------ | -------- |
| 1 | A make target rebuilds the demo lecture video from versioned sources (script, slides, Kokoro narration, ffmpeg), with no third-party content and no patient data | VERIFIED | `Makefile` `demo-video` runs `scripts/build_demo_lecture.py`; versioned sources `demo/lecture/script.yaml`, `demo/lecture/slides.typ`; `src/lecture_study/demo/media_build.py` (`BUILD_SUMMARY_PATH` = `workspace/demo/build/build-summary.json`, present) and `script.py`; `workspace/demo/aula-demo.mp4` present; ffprobe (2026-10-16): 1920x1080 h264 video plus aac audio, 243.6 s (inside the 200 to 290 s band); narration intelligibility evidenced by the 0.5% (3 of 620 words) ASR word error rate in the committed report; build acceptance (OCR of every heading, 200 to 290 s band) covered by `tests/demo/test_demo_build.py`, which now also covers the MD-01 fix (promotion only after acceptance). Scans for debt markers in the phase files found none. |
| 2 | The demo lecture runs through `run-all` and `run-personal-study`, stages 8 and 9 included, and a signed-in session views its page on the locally served site | VERIFIED | Both ids: `workspace/demo/logs/925100/` and `925101/` `run-all.log` show phase 8 and 9 done, phase 10 refused by design (ADR 0024); personal-study and flashcards logs; `workspace/data/14_mock_exams/925101/{exam.json,simulado.md}` and `18_personal_study_flashcards/925101/deck.json` exist (and the 925100 equivalents). Signed-in HTTP checks (`scripts/demo_site.py check`): `workspace/demo/logs/925100/signed-in-check.txt` (`anonymous_status=401 login_status=303 page_status=200 notice_present=True`) and `workspace/demo/logs/925101/signed-in-check.txt` (`anonymous_status=401 login_status=303 page_status=200 notice_present=True`), corroborated for 925101 by `workspace/demo/logs/serve.log` (GET 401, POST /entrar 303, GET 200). Disclosed deviation: 925100 flashcards at the operator-approved floor of 5. |
| 3 | A results report gives ASR WER before and after the dictionary, schema pass rate per LLM stage, critic rejection rate and per-stage wall time, for each adapter that was run | VERIFIED for both adapters (codex and llama-cpp) | `docs/results/demo-results.md` and `.json` (committed): adapters `codex:925101` (model gpt-6-sol, named in the note) and `llama-cpp:925100`; WER 2.3% (14 of 620) to 0.5% (3 of 620) pipeline and 3.1% (19 of 620) to 0.0% (0 of 620) ablation, per adapter; schema pass rates per stage with denominators (codex 100% in all six stages, 16 of 16 exams and 8 of 8 flashcards; llama-cpp exams 87.5%, 14 of 16); critic rejection 0.0% (0 of 1) per adapter; per-phase wall times. The codex harvest exists (`workspace/demo/runs/codex/harvest.json`, six `llm_runs` records, `logs/archive/NOT_RUN.txt` kept as the record of the failed gpt-6.1-sol attempts). `make demo-report` was re-run: `git status --porcelain` shows no change to `docs/results`, so the report is deterministic and matches the harvest. The JSON has `adapters_not_run: []`. Every `%` carries its denominator. |
| 4 | With a rejected or stale unit, the four derivative scripts write nothing for the demo lecture; after the ADR 0045 recovery they produce exam, podcast, flashcards and study guide | PASSED (override) for the recovery half; blocking half VERIFIED | Blocking: all four scripts exit 1 with an unchanged output-root snapshot in all five runs and both scenarios; critic rerun exit 0; `git diff b8cf64c HEAD` over `src/lecture_study/{assembly,llm,study_materials}` and `scripts/generate_*.py` is empty (gate and scripts untouched). Recovery: partial, operator-accepted, see below. |

**Score:** 4/4 truths verified (3 VERIFIED, 1 PASSED by override); 0 present-behavior-unverified.

### DEMO-04 (honest numbers, unchanged by the override)

Recomputed from `workspace/demo/recovery/attempt-1..5/evidence.json` and the committed report:

| Attempt | Deck floor | Blocked (4 scripts, both scenarios) | Recovered exam / podcast / flashcards / guide |
| ------- | ---------- | ----------------------------------- | --------------------------------------------- |
| 1 | 12 | exit 1, tree unchanged | 1 / 0 / 1 / 0 in both scenarios |
| 2 | 12 | exit 1, tree unchanged | 1 / 0 / 1 / 0 in both scenarios |
| 3 | 5 | exit 1, tree unchanged | 1 / 0 / 0 / 0 in both scenarios |
| 4 | 5 | exit 1, tree unchanged | stale 1 / 0 / 0 / 0; rejected 0 / 0 / 0 / 0 (all four refreshed) |
| 5 | 5 | exit 1, tree unchanged | 1 / 0 / 0 / 0 in both scenarios |

- Blocking: proven in 5 of 5 runs, both scenarios.
- Podcast and study guide recovered 10 of 10 scenario runs. Flashcards 6 of 6 at floor 5, 0 of 4 at floor 12. Mock exam 1 of 10 (attempt 4, rejected scenario).
- Per `evidence.json`, only `rejected` of attempt 4 has `passed: true`; both scenarios of attempt 5 (the last run, the one the report renders) have `passed: false`. The report prints "Result: did not pass" for both and lists every attempt; ADR 0047 decision 9 and Consequences state the partial result. Nothing is overstated.
- Override: `overrides:` entry in the frontmatter, accepted by the operator on 2026-10-15 (relayed by the orchestrating agent; see the advisory about corroboration). The override accepts the partial result as the outcome; it does not change any number, and the exam shortfall remains a documented limit of the local 14B Q4 model.

### Required Artifacts

| Artifact | Expected | Status | Details |
| -------- | -------- | ------ | ------- |
| `demo/lecture/script.yaml`, `slides.typ` | Authored pt-BR script and Typst slides | VERIFIED | Present, synthetic, no external images or URLs |
| `src/lecture_study/demo/script.py`, `media_build.py`, `scripts/build_demo_lecture.py`, `Makefile demo-video` | Build and acceptance | VERIFIED | Video and build summary present; MD-01 fix wired |
| `src/lecture_study/demo/{wer,asr_measure,run_metrics,phase_timing}.py` | Measurement logic | VERIFIED | Feed `report.py`; report numbers match harvests |
| `src/lecture_study/demo/report.py`, `scripts/demo_results.py` | Deterministic renderer | VERIFIED | Re-run yields no diff |
| `scripts/demo_run.py`, `harvest.py` | Run driver, harvest | VERIFIED | Harvests for both adapters exist with digests |
| `scripts/demo_site.py`, `site_access.py` | Site access and check | VERIFIED | Check output for 925100; serve log for 925101 |
| `scripts/demo_blocked_recovery.py`, `recovery.py`, `tree_snapshot.py` | Induction, snapshot, scenario driver | VERIFIED | Blocking proven live; MD-02 partial-evidence handling in place |
| `src/lecture_study/demo/lecture_id.py` | Lecture id validation (MD-04) | VERIFIED | Wired at argparse `type=` in the drivers and in layout builders (07-SECURITY-FIX.md traced all entry points) |
| `docs/results/demo-results.{md,json}` | Committed report | VERIFIED | Both adapters, all metric families, denominators |
| `docs/decisions/0047-demo-lecture-measurement-method.md` | ADR, Accepted | VERIFIED | Decision 4 carries the Codex re-run; decision 9 states the partial DEMO-04 outcome |

### Key Link Verification

| From | To | Status | Details |
| ---- | -- | ------ | ------- |
| `Makefile demo-*` | `scripts/demo_*.py`, `build_demo_lecture.py` | WIRED | `make demo-report` executed successfully |
| `harvest.py` outputs | `report.py` | WIRED | Report renders codex and llama-cpp from the two harvests |
| `recovery.py` evidence | report recovery section | WIRED | Matches `attempt-5` evidence |
| `demo_run.py` | `run-all`, `run-personal-study`, flashcards script | WIRED | Logs for both ids |

### Data-Flow Trace (Level 4)

| Artifact | Data | Source | Real data | Status |
| -------- | ---- | ------ | --------- | ------ |
| `docs/results/demo-results.md` | WER, pass rates, wall times (both adapters) | gitignored harvests under `workspace/demo/runs/{codex,llama-cpp}/` | Yes (regeneration reproduces the committed files byte for byte) | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| Full test suite | `uv run pytest -q` | 2916 passed, 1 skipped | PASS |
| Lint | `uv run ruff check .` | All checks passed | PASS |
| Types | `uv run mypy src` | no issues in 139 source files | PASS |
| Report determinism | `make demo-report`, then `git status --porcelain` | no change to `docs/results` | PASS |
| Video container | `ffprobe workspace/demo/aula-demo.mp4` | h264 1920x1080 + aac, 243.6 s | PASS |
| Servers / model runs | not started (policy) | evidence taken from logs and harvests | SKIP |

### Probe Execution

No `probe-*.sh` declared by any plan; none found for this phase. SKIPPED.

### Requirements Coverage

| Requirement | Source plans | Description | Status | Evidence |
| ----------- | ------------ | ----------- | ------ | -------- |
| DEMO-01 | 07-01, 07-02 | Synthetic lecture reproducible by a make target | SATISFIED | Truth 1 |
| DEMO-02 | 07-04, 07-06, 07-07 | Lecture through `run-all` and `run-personal-study` to a signed-in page view | SATISFIED (HTTP-level signed-in check, both lectures) | Truth 2 |
| DEMO-03 | 07-03, 07-06, 07-07, 07-08 | Measured quality numbers for each adapter run | SATISFIED for both adapters | Truth 3 |
| DEMO-04 | 07-05, 07-08 | Blocked-lecture recovery demonstrated; four scripts produce material after recovery | SATISFIED as partial, operator-accepted override | Blocking 5 of 5; podcast and guide 10 of 10; flashcards 6 of 6 at floor 5; exam 1 of 10 |

No orphaned requirements. `.planning/REQUIREMENTS.md` (committed in a2b2c8c) marks DEMO-01 to DEMO-04 complete and labels DEMO-04 "partial, operator-accepted 2026-10-15" with the same numbers as this report; ADR 0047 Consequences also records the operator acceptance. Consistent with this verdict.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |
| phase files (`src/lecture_study/demo`, demo scripts, `demo/`, `docs/results`, ADR 0047, `Makefile`) | - | TBD, FIXME, XXX scan | none | No debt markers |
| `07-REVIEW-FIX.md` | - | WR-01 to WR-04 and IN-01 to IN-06 (partial-evidence handling, abort log keeps no child output, stale video after rejected rebuild, missing `demo_results.py` tests, others) | warning/info, no blocker | Reviewed after the fix; 07-SECURITY-FIX.md verdict SECURED, `threats_open: 0`, 2 non-blocking medium findings against T-07-31 |

### Human Verification Required

None. Per the operator instruction that the agent performs all checks itself, the two former human checks were resolved by automated equivalents on 2026-10-16:

1. Narrated demo video: `ffprobe` of `workspace/demo/aula-demo.mp4` reports 1920x1080 h264 plus aac, 243.6 s; narration intelligibility is evidenced by the 0.5% (3 of 620 words) ASR word error rate against the known script. Slide fit is covered by the OCR-of-headings acceptance in the build.
2. Served pages: HTTP signed-in checks for 925100 and 925101 (anonymous 401, login 303, page 200, uncertified notice present), see Truth 2. These are HTTP-level; no browser rendering was inspected.

### Gaps Summary

No gaps. All four ROADMAP criteria hold: 1 to 3 verified against code and artifacts (criterion 3 now for both the Codex and the local adapter), criterion 4 holds for the blocking half and carries the recovery half as an operator-accepted override with the numbers stated unchanged (exam recovered in 1 of 10 scenario runs; the last run did not pass). The tests, lint, types and report determinism are green on HEAD a2b2c8c (re-run 2026-10-16: 2916 passed, 1 skipped; ruff clean; mypy clean; `make demo-report` leaves `docs/results` unchanged). The source digest was refreshed after ADR 0047 and the results files changed in a2b2c8c (operator-acceptance sentence). Status is `passed`; no human verification items remain.

---

_Verified: 2026-10-16T12:00:00Z_
_Verifier: Claude (gsd-verifier)_

---
phase: 07-synthetic-demo-lecture-and-measured-results
plan: 06
subsystem: demo
tags: [local-adapter, run-driver, harvest, asr-ablation, llama-cpp, stage-18]
requires:
  - phase: 07-02
    provides: "workspace/demo/aula-demo.mp4 and build-summary.json"
  - phase: 07-03
    provides: "measurement logic and report renderer"
  - phase: 07-04
    provides: "demo site access material, generate and check commands"
provides:
  - "scripts/demo_run.py: preflight of all six stages, egress confirmation, four logged steps with acceptance rules, --from-step rerun"
  - "Makefile: demo-register, demo-preflight, demo-run, demo-site-access, demo-site, demo-serve, demo-check, demo-harvest"
  - "lecture_study.demo.harvest: HarvestSources, HarvestManifest, harvest_run, load_adapter_results, load_not_run"
  - "scripts/demo_results.py harvest with the unprompted ASR ablation and a one-adapter preview"
affects: [07-07, 07-08]
tech-stack:
  added: []
  patterns:
    - "an earlier attempt's log is archived as NAME.attempt-N.log, never overwritten, because its wall times are measurements"
    - "harvest stages into a .partial sibling and renames, so a failed copy never blocks a retry"
key-files:
  created:
    - scripts/demo_run.py
    - scripts/demo_results.py
    - src/lecture_study/demo/harvest.py
    - tests/demo/test_demo_run.py
    - tests/demo/test_demo_harvest.py
  modified:
    - Makefile
key-decisions:
  - "Operator decision (option 2): stage 18 was rerun with --minimum-total-cards 5 after two failed attempts at the default floor of 12; the deviation is disclosed in the harvest notes and the report"
  - "demo-preflight passes --confirm-remote-egress only when CONFIRM_REMOTE_EGRESS=yes, so the policy refusal of the unflagged id is observable (exit 1) apart from the driver's confirmation refusal (exit 2)"
  - "--from-step plus log archiving were added so a failed step can be rerun without overwriting the measured wall times of earlier steps"
requirements-completed: [DEMO-02]
duration: 100 min
completed: 2026-10-15
status: complete
commits: 4
plan_head_before: ee65803eb8de2186d7ea71e04eb9c1ad80083044
plan_head_after: 440e560e0a1d8b9fcddd83812b966a6525312052
actuals:
  tokens: 12500
  tasks: 2
  commits: 4
---

# Phase 7 Plan 06: Local-adapter run and harvest Summary

The local-adapter lecture 925100 ran through every stage to a signed-in page view on the locally served HTTPS site, and its measurements are harvested under `workspace/demo/runs/llama-cpp/`. Stage 18 needed an operator-approved deviation: two attempts at the default deck floor failed with the local model, and a rerun with floor 5 succeeded. The listening and browser check remains an open human check.

## Tasks

| Task | Name | Commit | State |
| ---- | ---- | ------ | ----- |
| 1 | Tracer: local-adapter run of lecture 925100 to a signed-in page view | 139bf80, 440e560 | complete |
| 2 | Harvest the local run's measurements with the unprompted ASR ablation | f56b81e, 440e560 | complete |

`commits: 4` counts `plan_head_before..plan_head_after` and includes the interim blocked-state SUMMARY commit 7b24a07; the SUMMARY completion commit follows `plan_head_after`.

## Preflight outcomes (T-07-21)

Recorded in `workspace/demo/logs/925100/preflight.txt`:

| Command | Exit | Output |
| ------- | ---- | ------ |
| `demo_run.py --adapter codex --lecture-id 925100 --preflight-only` | 2 | refused: confirmation flag missing, before any preflight |
| `... --adapter codex --confirm-remote-egress ...` (`make demo-preflight ADAPTER=codex LECTURE=925100 CONFIRM_REMOTE_EGRESS=yes`) | 1 | `preflight=refused the codex adapter is refused for lecture 925100 at stage semantic: the lecture is not flagged remote_llm_allowed (ADR 0045 section 2)` |
| `... --adapter llama-cpp ...` | 0 | `preflight=ok` |

`make demo-preflight ADAPTER=codex LECTURE=925101 CONFIRM_REMOTE_EGRESS=yes` printed `preflight=ok` (925101 is flagged). `config/local-lectures.local.yaml` lists 925100 with `remote_llm_allowed: false` and 925101 with `true`, both `publish: true`. No codex process was started and nothing was sent to a remote provider.

## Run evidence (all under gitignored `workspace/demo/logs/925100/`)

- `run-all.log`: phases 2-9 ran, phase 10 refused by design (`require_stopped_at_certification` passed); step exit 1 accepted.
- `personal-study.log`: phases 8, 9 skipped (done by run-all), 13, 14, 15 ran. Attempt 1 failed at phase 13 (kept as `personal-study.attempt-1.log`); the single rerun succeeded.
- `flashcards.attempt-1.log`, `flashcards.attempt-2.log`: failed at the default floor; `flashcards.log`: succeeded with floor 5 (1 request, 0 retries, 21.9 s; deck of 5 cards at `workspace/data/18_personal_study_flashcards/925100/deck.json`).
- `guide.attempt-1.log`: the guide run out of order before the deck existed; `guide.log`: the final guide run with the deck.
- `signed-in-check.txt`: `lecture=925100 anonymous_status=401 login_status=303 page_status=200 notice_present=True`, against `make demo-serve` on https://127.0.0.1:8443 after the final guide and site generation (server stopped afterwards). The earlier check, before the deck existed, is kept as `attempt-1/signed-in-check-before-deck.txt`.
- `attempt-1/`: the failed run records (`exams-run-record.json`, `flashcards-run-record.json`, `flashcards-run-record-rerun.json`) and the phase 13 failure log, copied before the reruns overwrote them.
- `notes.txt`: two disclosure lines (exams attempt 1, flashcards floor) that the harvest turns into report notes.

## Stage 18 flashcards: failed attempts and the operator-approved floor of 5

| Attempt | Record status | Calls | Retry calls | Wall seconds | Error |
| ------- | ------------- | ----- | ----------- | ------------ | ----- |
| 1 | failed | 9 | 4 | 108.6 | `no JSON array in stdout` (1542 bytes) |
| 2 (rerun) | failed | 3 | 1 | 43.8 | `no JSON array in stdout` (1324 bytes) |

Unit 1 produced its 5 cards both times. The deck floor (`_DEFAULT_MINIMUM_TOTAL_CARDS` 12, cap 5 per unit, one grounded unit) then needs 7 single-card supplemental requests, and one of them fails both its first attempt and its single retry, which aborts the whole deck. The same pattern appeared once in phase 13 (exams attempt 1: 5 calls, 1 retry, 68.7 s, failed; the rerun succeeded with 16 requests, 2 retries). A diagnostic call (scratch script, not committed) with a supplemental-style prompt returned a JSON array holding a JSON-encoded string instead of an object in 4 of 4 samples, so single-card requests are a weak point of the 14B Q4_K_M model; the exact failing outputs are unrecoverable because the gateway keeps only a hash. Per the plan (research A7) no prompt or script was edited and the step was rerun once.

Options that were put to the operator:

1. Accept the failed flashcards record as the measured local result (the report renders `failed` with no percentage) and relax the stage 18 acceptance for the local adapter.
2. Rerun the step with `--minimum-total-cards 5` (no supplemental requests); this needs a plan amendment because the argv is fixed by the plan.
3. A third attempt of the same command (possible only as a deliberate exception to the rerun-once rule).

**Operator decision: option 2.** Stage 18 was rerun with `--minimum-total-cards 5` through the new `--flashcards-minimum-total-cards` driver option (`make demo-run ... FROM_STEP=flashcards FLASHCARDS_MINIMUM_TOTAL_CARDS=5`), no prompt or script edit. The run succeeded: one request, no retry, 21.9 s, 5 cards, no supplemental requests. The study guide, site and signed-in check were rerun afterwards. This deviation (attempts 1 and 2 failed at floor 12, success at floor 5) is recorded in this SUMMARY, in `workspace/demo/logs/925100/notes.txt` and, through it, in the notes of the harvested report preview.

## Harvested numbers (`workspace/demo/runs/llama-cpp/preview.md`)

`make demo-harvest ADAPTER=llama-cpp LECTURE=925100` ran once after the run was final. The harvest holds `harvest.json`, `llm_runs/` (all records), `transcription.json`, `terminology_corrections.json`, `units/`, `logs/` (including `attempt-1/` and `notes.txt`), `preview.md` and `ablation/segments.json` (whisper accepted the empty prompt):

- ASR WER, pipeline condition (prompt on): before the dictionary 2.3% (14 of 620 words), after 0.5% (3 of 620 words), 11 corrections.
- ASR WER, ablation (prompt off): before 3.1% (19 of 620 words), after 0.0% (0 of 620 words), 19 corrections.
- First-attempt schema pass rate: semantic 100.0% (1 of 1 requests), drafting 100.0% (1 of 1), critic 100.0% (1 of 1), exams 87.5% (14 of 16, 2 retries), podcast 100.0% (1 of 1), flashcards 100.0% (1 of 1 requests, floor 5).
- Critic rejection rate: 0.0% (0 of 1 reviews), 1 unit, 0 redrafts.
- Per-phase wall time (s): extraction 4.9, transcription 6.6, evidence 0.0, semantic 53.7, drafting 23.2, critic 9.7, assembly 0.0, metadata 0.0, exams 232.3, podcast 25.2, tts 8.1, flashcards 21.9; certification refused by design. Video 243.6 s, 620 reference words, 1 evidence pack.
- The exams figures describe the successful rerun; the failed first attempt is in `attempt-1/`. The flashcards figures describe the floor-5 run. Both are disclosed as notes in the preview; the final report must carry them.

## What was built

- `scripts/demo_run.py`: `DemoRunStep`, `pipeline_steps`, `steps_from`, `rejection_reason`. Prints `adapter=... lecture=...` first, refuses codex without `--confirm-remote-egress` (exit 2) before any preflight, forces `LECTURE_STUDY_LLM_ADAPTER`, preflights all six stages, then runs each step through `run_bounded_process` with the timeouts and caps of the plan.
- `src/lecture_study/demo/harvest.py` and `scripts/demo_results.py`: described in the commit; 16 tests cover copy fidelity (mtime in nanoseconds, SHA-256 per file), refusal of an existing destination, missing sources, superseded records, critic counts, phase merging, ablation presence or absence, and `NOT_RUN.txt`.
- `Makefile`: the eight demo targets and variables of the plan plus `FROM_STEP`.

## Deviations from Plan

**1. [Rule 3 - Blocking] `demo-preflight` forwards the egress confirmation**
- **Issue:** the plan requires `make demo-preflight ADAPTER=codex LECTURE=925100` to exit 1 on the unflagged id, but the same plan makes the driver exit 2 for codex without confirmation, before the preflight, so the two instructions contradict each other.
- **Fix:** the target forwards `--confirm-remote-egress` when `CONFIRM_REMOTE_EGRESS=yes`; a preflight sends nothing. Both outcomes are recorded above.
- **Commit:** 139bf80

**2. [Rule 2 - Missing correctness] `--from-step` and log archiving**
- **Issue:** rerunning a failed step through the full driver would have overwritten `run-all.log`, destroying the measured wall times of phases 2-7 (a rerun logs them as already done).
- **Fix:** `--from-step` (and the `FROM_STEP` make variable) reruns from a named step; an existing log is renamed `NAME.attempt-N.log` before the new one is written. Tests added for both.
- **Commit:** 139bf80

**3. [Rule 3 - Sequencing] `DEMO_LOGS_ROOT` moved to the harvest module**
- The driver defined the constant in the Task 1 commit and imports it from `harvest.py` after Task 2, so no commit is broken. **Commit:** f56b81e

**4. [Operator decision] Flashcards floor 5.** Option 2 above. Added `--flashcards-minimum-total-cards` to the driver (passed to the flashcards script only when given), the `FLASHCARDS_MINIMUM_TOTAL_CARDS` make variable and operator notes in the harvest (`logs/notes.txt` becomes `AdapterResults.notes`), with tests. **Commit:** 440e560

**5. Out-of-order steps:** after the first two flashcards failures the guide, site and check were run once without the deck to see how far the page view goes; they were rerun with the deck.

## First-run defects

No code defect surfaced in stages 8 and 9, the study-guide script or the site generator on real lecture data (first run of stages 8 and 9 on lecture data in this repository: both ran in under a second). No module was changed for a defect. The failures above are model contract failures, not defects.

## Open human check

Listening to and watching the lecture page and the narrated video in a browser (`make demo-serve`, sign in with the values in `workspace/demo/site-access/.env`) was not performed; an agent cannot judge the rendered page or the audio.

## Verification

- `uv run pytest -q`: 2891 passed, 1 skipped. `ruff check`, `ruff format --check`, `mypy src` clean.
- Plan checks pass: the signed-in check line, the phase 9 line of `run-all.log`, `workspace/data/14_mock_exams/925100/exam.json`, `workspace/data/18_personal_study_flashcards/925100`, `workspace/demo/runs/llama-cpp/harvest.json`, the harvested `llm_runs/critic.json`, and the preview sections `## Critic rejection rate` and the WER table.

## Known Stubs

None.

## Threat Flags

None. T-07-21 holds (unflagged local id refused for codex, adapter variable forced, preflight before any process). T-07-22: only the supported registration script touched `config/local-lectures.local.yaml`. T-07-23: logs, records, the harvest and the demo site stay under gitignored `workspace/` and `tmp/`. T-07-24: finite timeouts and output caps on every step. T-07-25: the server bound 127.0.0.1 with the 07-04 certificate and was stopped.

## Self-Check: PASSED

Commits 139bf80, f56b81e and 440e560 are ancestors of HEAD; the five created files, the modified Makefile and `workspace/demo/runs/llama-cpp/harvest.json` exist.

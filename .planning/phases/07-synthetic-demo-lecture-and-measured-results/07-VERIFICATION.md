---
phase: 07-synthetic-demo-lecture-and-measured-results
verified: 2026-10-15T20:00:00Z
status: gaps_found
score: 3/4 must-haves verified
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
covered_digest: "v3:sha256:1fd0c27158c01df2e92c4cc7a15967a26b3d77ba68c2e5f5cf8a81bee6ec993e"
behavior_unverified: 0
overrides_applied: 0
gaps:
  - truth: "ROADMAP criterion 4 / DEMO-04: after the ADR 0045 recovery, the exam, podcast, flashcards and study guide scripts produce the exam, podcast, flashcards and study guide for the demo lecture"
    status: partial
    reason: "Blocking is proven (all four scripts exit 1 and every output root is unchanged, in all five runs and both scenarios, with the gate untouched). Recovery is only partial: podcast and study guide recovered in 10 of 10 scenario runs and flashcards in 6 of 6 at deck floor 5 (0 of 4 at the default floor of 12), but the mock exam recovered in only 1 of 10 scenario runs (attempt 4, rejected scenario). The last run did not pass in either scenario. The failures are contract failures of the local 14B Q4 model (schema validation, student-facing text guard, no JSON array), not gate or critic outcomes."
    artifacts:
      - path: "docs/results/demo-results.md"
        issue: "Blocked-lecture recovery section reports 'Result: did not pass' for both scenarios of the last run"
      - path: "workspace/demo/recovery/attempt-5/evidence.json"
        issue: "recovered exam exit_code 1 in stale and rejected scenarios; passed false"
    missing:
      - "One recovery run (either scenario) in which exam, podcast, flashcards and study guide all exit 0 and all four outputs are refreshed, as the last committed run, with the report regenerated; or an operator-accepted override recording the partial result as the accepted outcome"
      - "A path to exam stage reliability: a model or adapter that satisfies the exam contract (for example the Codex adapter once its external model configuration is fixed, tracked as the open codex measurement), or a documented operator decision to accept the local-model limit"
deferred: []
human_verification:
  - test: "Listen to and watch the narrated demo video workspace/demo/aula-demo.mp4"
    expected: "Brazilian Portuguese narration (voice pm_alex) is intelligible and in sync with the six slides; no third-party or patient content is audible or visible"
    why_human: "Audio quality and slide/narration fit cannot be judged by an agent; only duration band, OCR of slide headings and the ASR word error rate are machine-checked"
  - test: "Run make demo-serve, sign in with the values in workspace/demo/site-access/.env, and open the lecture 925100 page in a browser"
    expected: "The page renders the lecture with video, the uncertified notice, the study guide, the flashcards deck (5 cards) and the mock exam, usable and readable"
    why_human: "The signed-in check (anonymous 401, login 303, page 200, notice present) was verified at the HTTP level only; rendered page and usability need a human"
advisory: []
---

# Phase 7: Synthetic demo lecture and measured results Verification Report

**Phase Goal:** A lecture authored for this repository runs end to end to a served page, and the pipeline's quality is reported with real numbers
**Verified:** 2026-10-15T20:00:00Z
**Status:** gaps_found
**Re-verification:** No, initial verification
**Branch:** gsd/phase-07-synthetic-demo (HEAD 55f8665)

## Goal Achievement

### Observable Truths (ROADMAP success criteria)

| # | Truth | Status | Evidence |
| - | ----- | ------ | -------- |
| 1 | A make target rebuilds the demo lecture video from versioned sources (script, slides, Kokoro narration, ffmpeg), with no third-party content and no patient data | VERIFIED | `Makefile` `demo-video` runs `scripts/build_demo_lecture.py`; versioned sources `demo/lecture/script.yaml` and `demo/lecture/slides.typ` exist; `src/lecture_study/demo/media_build.py` and `script.py` (strict model plus nine-rule narration lint) exist; `slides.typ` has no `image(` or URL references; script header declares synthetic content with no case data; `workspace/demo/aula-demo.mp4` (3.5 MB) and `build-summary.json` exist; shared Kokoro helper in `tts/generator.py`; build acceptance (OCR of every heading, duration band 200 to 290 s) covered by `tests/demo/test_demo_build.py` (passing). Audio and visual check is open (human). |
| 2 | The demo lecture runs through `run-all` and `run-personal-study`, stages 8 and 9 included, and a signed-in session views its page on the locally served site | VERIFIED (automated); human check open | `workspace/demo/logs/925100/run-all.log` shows phase 8 (assembly) and phase 9 (metadata) done, then phase 10 refused by design (fail-closed certification, ADR 0024); personal-study phases 13, 14, 15 and flashcards phase 18 ran (log `personal-study.log`, `flashcards.log`); `workspace/data/14_mock_exams/925100/exam.json`, `workspace/data/18_personal_study_flashcards/925100/deck.json` exist; `signed-in-check.txt`: `anonymous_status=401 login_status=303 page_status=200 notice_present=True` against the served HTTPS site. Deviation: stage 18 needed the operator-approved `--minimum-total-cards 5` (two failed attempts at the default 12), disclosed in the report notes. Rendered browser view not performed. |
| 3 | A results report gives ASR WER before and after the terminology dictionary, schema pass rate per LLM stage, critic rejection rate and per-stage wall time, for each adapter that was run | VERIFIED for the adapters that were run (llama-cpp); codex not run, disclosed | `docs/results/demo-results.md` and `.json` (committed): WER 2.3% (14 of 620) to 0.5% (3 of 620) pipeline condition and 3.1% (19 of 620) to 0.0% (0 of 620) ablation; first-attempt schema pass rate for semantic, drafting, critic, exams (87.5%, 14 of 16), podcast, flashcards; critic rejection 0.0% (0 of 1 reviews); per-phase wall times; every rate carries its denominator. `make demo-report` regenerated both files with an empty `git status`, so the report is deterministic and matches the harvest. Codex is listed under "Adapters not run" with the real cause (codex CLI user-level default model rejected for a ChatGPT account); N = 0. Literal reading of "each adapter that was run" holds; the Codex versus local comparison the milestone intended does not exist (ADR 0047 records it as pending). Critic rate rests on 1 review (stated in the report). |
| 4 | With a rejected or stale unit, the four derivative scripts write nothing for the demo lecture; after the ADR 0045 recovery they produce exam, podcast, flashcards and study guide | PARTIAL: blocking VERIFIED, recovery FAILED | See DEMO-04 analysis below. |

**Score:** 3/4 truths verified (criterion 4 partial; codex gap noted under criterion 3 as a warning, not a blocker)

### DEMO-04 analysis (strict)

Evidence from `workspace/demo/recovery/attempt-1..5/evidence.json`, `notes.txt` and the committed report:

| Attempt | Deck floor | Blocked (4 scripts, both scenarios) | Recovered exit codes exam / podcast / flashcards / guide |
| ------- | ---------- | ----------------------------------- | -------------------------------------------------------- |
| 1 | 12 | exit 1, tree unchanged | 1 / 0 / 1 / 0 in both scenarios |
| 2 | 12 | exit 1, tree unchanged | 1 / 0 / 1 / 0 in both scenarios |
| 3 | 5 | exit 1, tree unchanged | 1 / 0 / 0 / 0 in both scenarios |
| 4 | 5 | exit 1, tree unchanged | stale 1 / 0 / 0 / 0; rejected 0 / 0 / 0 / 0 (all four refreshed) |
| 5 | 5 | exit 1, tree unchanged | 1 / 0 / 0 / 0 in both scenarios |

- Blocking half: VERIFIED. All four scripts exit 1 with an unchanged filesystem snapshot in every scenario of every run; the critic rerun exits 0. `git diff b8cf64c HEAD` shows the gate and the four pipeline scripts untouched (only `Makefile`, `tts/generator.py` and the new demo code changed outside tests and docs).
- Recovery half: FAILED. The exam recovered in 1 of 10 scenario runs. One scenario run did achieve all four outputs once, which shows the recovery path can work, but it is not reproduced, and the report's own final state is "did not pass" in both scenarios. The ROADMAP wording requires that the four scripts produce their material after recovery; that holds for two of four reliably, for flashcards only with the floor-5 deviation, and for the exam essentially not.
- Cause is model contract failure on the exam stage of the local 14B Q4_K_M model, not the recovery mechanism. This is an honest, fully disclosed result (report Attempts section, ADR 0047), but the requirement as written is not met.
- The report and SUMMARY do not hide the shortfall: both state DEMO-04 is partly met. No override is recorded.

**This may be an intentional acceptance.** If the operator accepts the partial result (blocking proven, recovery demonstrated once end to end, exam limited by the local model), add to this file's frontmatter:

```yaml
overrides:
  - must_have: "after the ADR 0045 recovery they produce the exam, podcast, flashcards and study guide"
    reason: "Exam stage fails local-model contract in 9 of 10 scenario runs; full recovery demonstrated once (attempt 4, rejected scenario); operator accepts the limit pending the Codex measurement"
    accepted_by: "{name}"
    accepted_at: "{ISO timestamp}"
```

No such override exists; the verifier does not self-grant it.

### Required Artifacts

| Artifact | Expected | Status | Details |
| -------- | -------- | ------ | ------- |
| `demo/lecture/script.yaml` | Authored pt-BR script, 6 slides, 15 planted terms | VERIFIED | Present, synthetic header, planted terms listed |
| `demo/lecture/slides.typ` | Typst slides reading the script | VERIFIED | No external images or URLs |
| `src/lecture_study/demo/script.py` | Strict model and narration lint | VERIFIED | Wired through `media_build.py`; tests pass |
| `scripts/build_demo_lecture.py` + `Makefile demo-video` | Rebuild target | VERIFIED | Output video and build summary exist |
| `src/lecture_study/demo/{wer,asr_measure,run_metrics,phase_timing}.py` | Measurement logic | VERIFIED | Feed `report.py`; numbers in report match harvest |
| `src/lecture_study/demo/report.py`, `scripts/demo_results.py` | Deterministic report renderer | VERIFIED | Re-run yields no diff |
| `scripts/demo_run.py`, `src/lecture_study/demo/harvest.py` | Run driver, harvest | VERIFIED | Logs and `workspace/demo/runs/llama-cpp/harvest.json` exist; codex has `NOT_RUN.txt` and no harvest (exactly one, as designed) |
| `scripts/demo_site.py`, `src/lecture_study/demo/site_access.py` | Site access material, generate, check | VERIFIED | `signed-in-check.txt` produced by `demo-check` |
| `scripts/demo_blocked_recovery.py`, `recovery.py`, `tree_snapshot.py` | Induction, snapshot, scenario driver | VERIFIED (mechanism) | Blocking and critic rerun proven live |
| `docs/results/demo-results.{md,json}` | Committed report | VERIFIED | Contains all required metric families with denominators |
| `docs/decisions/0047-demo-lecture-measurement-method.md` | ADR, Accepted | VERIFIED | Present; states codex comparison pending |

### Key Link Verification

| From | To | Status | Details |
| ---- | -- | ------ | ------- |
| `Makefile demo-*` targets | `scripts/demo_*.py`, `build_demo_lecture.py` | WIRED | Targets read in the Makefile; `make -n demo-report` resolves |
| `harvest.py` outputs | `report.py` (`load_adapter_results`, `load_not_run`) | WIRED | Report shows llama-cpp numbers and the codex not-run text |
| `recovery.py` evidence | report "Blocked-lecture recovery" section | WIRED | Matches `attempt-5/evidence.json` and `notes.txt` |
| `demo_run.py` | pipeline `run-all`, `run-personal-study`, flashcards script | WIRED | Logs show phases executed |

### Data-Flow Trace (Level 4)

| Artifact | Data | Source | Real data | Status |
| -------- | ---- | ------ | --------- | ------ |
| `docs/results/demo-results.md` | WER, pass rates, wall times | gitignored harvest of real run records (`workspace/demo/runs/llama-cpp/`) | Yes (SUMMARY figures match the report and the preview) | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| Full test suite | `uv run pytest -q` | 2901 passed, 1 skipped | PASS |
| Demo tests | `uv run pytest tests/demo -q` | 200 passed, 1 skipped | PASS |
| Lint and types | `uv run ruff check .`; `uv run mypy src` | clean; no issues in 138 files | PASS |
| Report determinism | `make demo-report`, then `git status --porcelain` | empty | PASS |
| Servers / model runs | not started (policy) | evidence taken from logs | SKIP |

### Probe Execution

No `probe-*.sh` declared by any plan; none found for this phase. SKIPPED.

### Requirements Coverage

| Requirement | Source plans | Description | Status | Evidence |
| ----------- | ------------ | ----------- | ------ | -------- |
| DEMO-01 | 07-01, 07-02 | Synthetic lecture reproducible by a make target, no third-party or patient content | SATISFIED (human listen/watch open) | Truth 1 |
| DEMO-02 | 07-04, 07-06, 07-07 | Lecture through `run-all` and `run-personal-study` to a signed-in page view | SATISFIED for the local adapter (HTTP-level; browser view open); stage 18 floor deviation disclosed | Truth 2 |
| DEMO-03 | 07-03, 07-06, 07-07, 07-08 | Measured quality numbers for each adapter run | SATISFIED for llama-cpp; codex not run (N = 0), disclosed | Truth 3 |
| DEMO-04 | 07-05, 07-08 | Blocked-lecture recovery demonstrated; four scripts produce material after recovery | BLOCKED (partial) | Blocking proven; exam recovery 1 of 10 |

No orphaned requirements: REQUIREMENTS.md maps DEMO-01 to DEMO-04 to Phase 7 and all four appear in plan frontmatter. REQUIREMENTS.md still shows them "Pending"; the orchestrator should update it consistently with this verdict.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |
| (phase files) | - | TBD, FIXME, XXX, TODO, HACK scan over `src/lecture_study/demo`, demo scripts, `demo/`, `docs/results`, ADR 0047, `Makefile` | none found | No debt markers |
| `docs/results`, ADR 0047, `demo/` | - | leak scan for absolute home paths, private keys, password strings | none found | Clean |

### Warnings (non-blocking)

1. Codex adapter not measured: external codex configuration (`~/.codex/config.toml` model rejected for a ChatGPT account). The pluggable-gateway comparison that motivates the milestone is absent; ADR 0047 states it as pending. DEMO-03 is met only as "for each adapter that was run".
2. Stage 18 flashcards measured at deck floor 5 (operator-approved deviation from the default 12), so the flashcards figures are not at the default contract.
3. Small samples: one evidence pack, one critic review; rates are indicative only (stated in the report).
4. Interim evidence for the recovery runs, signed-in check and harvest lives in gitignored `workspace/`; only the report and ADR are versioned. The committed report is the durable record.
5. Operator-imposed limit of three runs at floor 5 ended the recovery attempts; attempts 1 and 2 were run before that decision (disclosed).

### Human Verification Required

1. **Narrated demo video.** Test: play `workspace/demo/aula-demo.mp4`. Expected: intelligible pt-BR narration synchronized with the six slides, no third-party or patient content. Why human: audio and visual quality.
2. **Served page in a browser.** Test: `make demo-serve`, sign in with `workspace/demo/site-access/.env` values, open lecture 925100. Expected: video, uncertified notice, study guide, 5-card flashcard deck and mock exam render and work. Why human: rendering and usability; only HTTP statuses were checked.

### Gaps Summary

Phase 7 delivers the demo lecture build (DEMO-01), the end-to-end run to a signed-in served page for the local adapter (DEMO-02), and a deterministic committed results report with denominators (DEMO-03, llama-cpp only, codex honestly recorded as not run). DEMO-04 is the single gap: blocking is proven live and repeatedly, but the post-recovery production of all four outputs holds for the exam in only 1 of 10 scenario runs, and the final committed run does not pass. Closing it needs either a reliable exam run on a stronger model (the Codex adapter once its configuration is fixed, which would also close the codex measurement warning) or an explicit operator override accepting the partial result. Status is `gaps_found`; the human checks above stay open regardless.

---

_Verified: 2026-10-15T20:00:00Z_
_Verifier: Claude (gsd-verifier)_

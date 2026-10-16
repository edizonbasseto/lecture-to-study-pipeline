# Phase 7: Review of the MD-01..MD-04 fixes

**Reviewed:** 2026-10-16
**Scope:** uncommitted diff (`scripts`, `src`, `tests`) plus untracked `src/lecture_study/demo/lecture_id.py`
**Depth:** standard
**Verification run:** `uv run pytest tests/demo` -> 215 passed, 1 skipped. No test touches the real `workspace/` (every path is `tmp_path` or monkeypatched; `os.chdir` is always preceded by `monkeypatch.chdir`).

## Verdict

All four findings are fixed in their main path. No BLOCKER. Four WARNINGs describe incomplete or misleading parts of the fixes, and six INFO items.

| Finding | Status |
| --- | --- |
| MD-01 | Fixed (gate runs on the partial file, promotion only after acceptance, `OcrError`/`MediaExtractionError` added). WR-03 residual. |
| MD-02 | Partly fixed (no traceback, status line, warning, partial evidence). WR-01 and WR-02 residual. |
| MD-03 | Fixed (absolute roots plus `os.chdir`, root itself refused). IN-01 side effect. |
| MD-04 | Fixed (`parse_lecture_id` at argparse `type=` and at `default_layout`/`for_lecture`). Verified: `"ab\n"`, `".."`, `"a/b"`, `""`, `"a b"` and non-ASCII are rejected; `925100` accepted. WR-04 test gap. |

## Warnings

### WR-01: Partial evidence from an aborted run reports "passed" and overwrites the previous complete evidence

**File:** `scripts/demo_blocked_recovery.py:150-170, 204-235`; `src/lecture_study/demo/recovery.py` (`RecoveryEvidence.passed`); `src/lecture_study/demo/report.py:353`
**Issue:** When scenario 2 aborts after scenario 1 passed, `evidence.json` is written with one passing scenario. `RecoveryEvidence.passed()` is `bool(scenarios) and all(...)`, so the committed report would print "the last run passed (ADR 0045)" for a run that did not complete. The process exit code is 1, but the artifact the report consumes says otherwise. The write also replaces any earlier complete `evidence.json` (measured data) with the partial or empty one.
**Fix:** Do not write over the existing file on an aborted run: write the partial evidence to `evidence.partial.json`, or skip the write. Alternatively add a `complete: bool` field to `RecoveryEvidence` (strict model, update `passed()` and the report verdict).

### WR-02: The abort log keeps no child output, so the measured wall times are still lost

**File:** `scripts/demo_run.py:204-235`; `scripts/demo_blocked_recovery.py:106-122`; `src/lecture_study/ops/process_lifecycle.py:203-206`
**Issue:** `run_bounded_process` raises `TimeoutExpired(command, timeout)` / `ProcessOutputLimitExceeded` without the captured stdout/stderr. The new log contains only `aborted: TimeoutExpired(...)`. MD-02 stated the step log is "the only record of the wall times this phase measures"; for a 4 h step that record (phase lines already printed by the child) is still discarded. The test name and docstring ("keeps a log") overstate this.
**Fix:** Either attach the captured output to the raised exceptions in `process_lifecycle` (`TimeoutExpired(..., output=..., stderr=...)`) and write `error.stdout`/`error.stderr` into the log, or state the limitation in the log text and in ADR 0047. Also guard the log write itself: `_write_log` in `_report_aborted_step` can raise `OSError` and reintroduce the traceback.

### WR-03: A rejected rebuild still leaves a stale video at the consumed path and destroys its build summary

**File:** `scripts/build_demo_lecture.py:120-130, 146-152, 279`
**Issue:** `reset_build_dir()` wipes `BUILD_DIR`, which contains `BUILD_SUMMARY_PATH`, before the gate runs. If the new build is rejected, the previous `workspace/demo/aula-demo.mp4` stays in place (the pipeline and `make demo-register` keep consuming it) while its `build-summary.json` is gone. Video and summary are no longer a pair. Also `promote_video` can fail (`OSError`, not in `BUILD_FAILURES`) after `accept_video` has already written the new summary.
**Fix:** In `build_demo_video`, remove `DEMO_VIDEO_PATH` before building (or fail loudly that an older video remains), and write the summary after `promote_video` succeeds (`accept_video` returns the summary, `build_demo_video` persists it). Add `OSError` to `BUILD_FAILURES` or catch it around promotion.

### WR-04: `demo_results.py` changes have no test

**File:** `scripts/demo_results.py:204-212, 231, 242`; `tests/demo/`
**Issue:** The `report` output-directory containment (`report refused`), the `harvest --lecture-id` validation and the `os.chdir` anchor have no test, while the other four drivers do. Containment is also only against the repository root, so `--output-dir src/lecture_study` is accepted and `demo-results.md`/`.json` are written there.
**Fix:** Add tests mirroring `test_driver_refuses_an_evidence_path_outside_the_workspace`. Consider restricting `--output-dir` to `docs/` or `workspace/`.

## Info

### IN-01: `os.chdir` before `parse_args` changes the meaning of user-supplied relative paths

**File:** `scripts/demo_run.py:272`, `scripts/demo_results.py:242`, `scripts/demo_site.py:268`, `scripts/demo_blocked_recovery.py:150`
**Issue:** `--whisper-model-path`, `--evidence-path`, `--output-dir`, `--directory` given as relative paths now resolve against the repository root, not the shell directory. Safe (the stated goal) but a silent behaviour change.
**Fix:** Document it in the `--help` text, or resolve user-supplied paths against the original cwd before `chdir`.

### IN-02: Tautological or weak assertions

**File:** `tests/demo/test_demo_site_access.py:126-131`; `tests/demo/test_demo_recovery.py` (`test_driver_warns_and_writes_partial_evidence...`)
**Issue:** `WORKSPACE_ROOT == REPOSITORY_ROOT / "workspace"` restates the constant. The last assertion `layout.units_dir.is_dir()` proves nothing about the abort path, and the written evidence content (empty `scenarios`) is not asserted. No test covers "scenario 1 passes, scenario 2 aborts" (see WR-01).
**Fix:** Assert the evidence content and `completed`-dependent exit code for the second-scenario abort; drop the constant test.

### IN-03: Makefile still interpolates `$(LECTURE)` unquoted

**File:** `Makefile:49,54,72,77`
**Issue:** The earlier review tied MD-04 to quoting `"$(LECTURE)"`. Argparse validation now rejects bad ids, but shell metacharacters are expanded before the script runs.
**Fix:** Quote the variable in the recipes.

### IN-04: Unrelated uncommitted results change travels with the fix

**File:** `docs/results/demo-results.json`, `docs/results/demo-results.md`
**Issue:** The working tree also holds a regenerated report (codex rows, new measurement date) that is not part of MD-01..MD-04. Commit it separately. It also widens IN-03 of the earlier review: the codex note names the user-level default model and a temporary change of the user-level codex configuration.
**Fix:** Split the commits; generalise the codex note wording.

### IN-05: Test-module loader leaks into `sys.modules`

**File:** `tests/demo/test_demo_build.py` (`build_script` fixture)
**Issue:** `sys.modules["build_demo_lecture"]` is set and never removed.
**Fix:** `monkeypatch.setitem(sys.modules, spec.name, module)`.

### IN-06: `ScriptAborted` handling catches all `OSError` of a scenario

**File:** `scripts/demo_blocked_recovery.py:217`
**Issue:** `except (ScriptAborted, OSError)` around `run_recovery_scenario` also swallows programming or filesystem faults from induction/snapshots, reported only as "aborted". Acceptable, but the warning text does not distinguish them.
**Fix:** Include `type(error).__name__` in `_aborted_warning`.

---

_Reviewed: 2026-10-16_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_

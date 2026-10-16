---
phase: 07-synthetic-demo-lecture-and-measured-results
reviewed: 2026-10-15T00:00:00Z
depth: standard
scope: git diff f7b9d51..HEAD -- src scripts tests Makefile demo docs/decisions/0047-demo-lecture-measurement-method.md docs/results
files_reviewed: 33
files_reviewed_list:
  - Makefile
  - demo/lecture/script.yaml
  - demo/lecture/slides.typ
  - demo/site/academic-calendar.yaml
  - docs/decisions/0047-demo-lecture-measurement-method.md
  - docs/results/demo-results.json
  - docs/results/demo-results.md
  - scripts/build_demo_lecture.py
  - scripts/demo_blocked_recovery.py
  - scripts/demo_results.py
  - scripts/demo_run.py
  - scripts/demo_site.py
  - src/lecture_study/demo/__init__.py
  - src/lecture_study/demo/asr_measure.py
  - src/lecture_study/demo/harvest.py
  - src/lecture_study/demo/media_build.py
  - src/lecture_study/demo/phase_timing.py
  - src/lecture_study/demo/recovery.py
  - src/lecture_study/demo/report.py
  - src/lecture_study/demo/run_metrics.py
  - src/lecture_study/demo/script.py
  - src/lecture_study/demo/site_access.py
  - src/lecture_study/demo/tree_snapshot.py
  - src/lecture_study/demo/wer.py
  - src/lecture_study/tts/generator.py
  - tests/test_tts.py
  - tests/demo (spot-checked; 225 passed, 1 skipped in tests/demo + tests/test_tts.py)
findings:
  critical: 0
  high: 0
  medium: 4
  low: 6
  info: 3
  total: 13
status: issues_found
---

# Phase 7: Code Review Report

**Reviewed:** 2026-10-15
**Depth:** standard (with cross-file tracing of the metric and secret paths)
**Status:** issues_found

## Summary

No critical or high findings. Verified clean on the requested focus areas:

- **Subprocess safety:** every child process in `scripts/build_demo_lecture.py`, `demo_run.py`,
  `demo_blocked_recovery.py` and `demo_site.py` is an argv list through `run_bounded_process`,
  with no `shell=True`. `run_bounded_process` re-sanitizes `env` itself
  (`process_lifecycle.py:176`), so passing `{**os.environ, ...}` in `demo_run.child_environment`
  and `ProcessScriptRunner` does not leak ambient credentials to children.
- **Secrets and TLS:** the password is written only to `.env` (0600), the allowlist holds a
  PBKDF2 hash, nothing is printed except paths, and `check` pins the throwaway certificate
  (`ssl.create_default_context(cafile=...)`) instead of disabling verification. The target
  directory (`workspace/demo/site-access`) is covered by `workspace/`, `.env` and
  `*.pem` in `.gitignore`. Files are opened with mode 0600 and `fchmod`ed before the write.
- **Metric definitions:**
  - WER (`wer.py`): the DP table and backtrace are correct, and S+D+I over N matches ADR 0047 section 2.
  - First-attempt pass rate (`run_metrics.py`): `(calls - retry_calls) - retry_calls` over
    `calls - retry_calls` is consistent with `RecordingGateway`, which counts `is_retry` calls.
  - Critic rate (`run_metrics.py:111-114`): redrafts plus finally rejected units over reviews, as in
    ADR 0047 section 6.
  - Failed records are rendered as `failed`, never as a percentage. The committed
    `docs/results/demo-results.md` numbers are internally consistent (14/620 minus 11 corrections
    gives 3/620; 14 of 16 gives 87.5%).
- **Kokoro helper refactor (`tts/generator.py:219-298`):** the argv, the `--join_audio` handling, the
  error text and class, and the "append chunk path before starting the subprocess" ordering in the
  podcast loop are all preserved. Deltas:
  - The sanitized environment is built per line instead of once per run.
  - A non-`.wav` path now raises `ValueError` before any process starts (the podcast template always
    ends in `.wav`).
  - `_VOICE_BY_SPEAKER` is now typed `KokoroVoice`.
- **Tooling:** `ruff check` is clean, `mypy` is clean on the demo and tts packages, and the 225 demo and
  tts tests pass.

Findings are mostly robustness gaps in the run drivers and a cwd-dependence of the secret-writing
path. They are proportionate to a study project.

## Medium

### MD-01: Failed acceptance leaves an unaccepted video in place, and OCR/extraction errors escape as tracebacks

**File:** `scripts/build_demo_lecture.py:74-82, 217-229, 232-260`

**Issue:**
- (a) `compose_video` moves the partial file to `DEMO_VIDEO_PATH` before `accept_video` runs the
  frame/OCR/duration gate. When the gate raises, the rejected video stays at
  `workspace/demo/aula-demo.mp4`, which is exactly what `make demo-register` and the pipeline consume.
  The ADR says the build "is accepted only when" the gate passes.
- (b) `BUILD_FAILURES` omits `OcrError` (`extraction/ocr_worker.py:28`) and `MediaExtractionError`
  (`extraction/media_workers.py:42`). Both are `RuntimeError` subclasses raised from `accept_video`,
  so an OCR hang or an ffmpeg failure ends in a raw traceback instead of `error: ...` and exit 1.

**Fix:**
```python
BUILD_FAILURES = (..., OcrError, MediaExtractionError)

def compose_video(...):
    ...
    run_tool(...)                      # writes BUILD_DIR/PARTIAL_VIDEO_NAME
    # do not promote here

def build_demo_video() -> BuildSummary:
    ...
    compose_video(toolchain, segments)
    summary = accept_video(toolchain, script, segments, video=BUILD_DIR / PARTIAL_VIDEO_NAME)
    DEMO_VIDEO_PATH.parent.mkdir(parents=True, exist_ok=True)
    (BUILD_DIR / PARTIAL_VIDEO_NAME).replace(DEMO_VIDEO_PATH)   # only after acceptance
    return summary
```
Alternatively delete `DEMO_VIDEO_PATH` in the `except` path of `main`.

### MD-02: A timeout or output-limit abort loses the step log, and the recovery driver can leave the lecture in an induced blocked state

**File:** `scripts/demo_run.py:196-218`, `scripts/demo_blocked_recovery.py:96-106`

**Issue:** `run_bounded_process(..., check=False)` still raises `TimeoutExpired`,
`ProcessOutputLimitExceeded` or `OSError` (`process_lifecycle.py:203-206`).
- `run_step` does not catch them, so for a 4-hour step no log is written and no `status=failed` line
  is printed. The log is the only record of the wall times this phase measures.
- `ProcessScriptRunner.__call__` has the same gap. An exception inside `run_recovery_scenario` (after
  `induce_*` mutated `draft_*.json` mtime or rewrote a unit's review) aborts with a traceback. The
  workspace stays blocked, `evidence.json` is never written, and nothing says the lecture was left
  induced.

**Fix:**
```python
try:
    completed = run_bounded_process(..., check=False)
except (subprocess.TimeoutExpired, ProcessOutputLimitExceeded, OSError) as error:
    _write_log(lecture_id, step.log_name, f"aborted: {error}\n")
    print(f"step={step.name} status=failed reason={type(error).__name__} ...")
    return False
```
In the recovery driver, catch the same set in `_run_scenarios`, print a warning that the lecture
may still be in the induced state (name the scenario), and write the partial evidence.

### MD-03: Secret-writing and destructive paths are cwd-relative, so a run from another directory writes outside the repository's `.gitignore`

**File:** `scripts/demo_site.py:57-58,151-171`, `src/lecture_study/demo/site_access.py:32,117-123`; the same pattern in `scripts/demo_blocked_recovery.py:41-42`, `scripts/demo_results.py:62-64`, `scripts/demo_run.py` (`DEMO_LOGS_ROOT`)

**Issue:** `require_inside(directory, Path("workspace"))` resolves both sides against the current
directory. From any other directory the check passes, and `.env` (plaintext password), the session key
and the private key land in `<cwd>/workspace/demo/site-access/`. That location is not covered by this
repository's ignore rules and may sit in another git repo. Only `build_demo_lecture.main` does
`os.chdir(REPOSITORY_ROOT)`. The other drivers rely on the operator remembering ("Run it from the
repository root").

**Fix:** anchor the roots to the repository, not the cwd.
```python
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = REPOSITORY_ROOT / "workspace"
...
def main(argv=None) -> int:
    os.chdir(REPOSITORY_ROOT)      # one line, same as build_demo_lecture.main
```
Also reject `directory == workspace_root` in `require_inside` (see LW-02).

### MD-04: `lecture_id` is unvalidated before the unlink and copy operations

**File:** `src/lecture_study/demo/recovery.py:82-101,121-133`, `src/lecture_study/demo/harvest.py:91-114`, `scripts/demo_run.py:221-226`, `scripts/demo_blocked_recovery.py:144,198-200`

**Issue:**
- `default_layout(lecture_id)` and `HarvestSources.for_lecture` join the raw CLI string into paths.
  `--lecture-id ../../x` makes `delete_units_without_draft` run `unlink()` on every `unit_*.json` in an
  arbitrary directory that has no matching `draft_*.json`. It also lets `induce_*` rewrite files there.
- `--evidence-path` and `--output-dir` are written without any containment check.
- The repo already has `SafeIdentifier` (`domain/models.py:28`, pattern `^[A-Za-z0-9_-]+$`) for exactly
  this.
- Practical exposure is low (operator-run local CLI), but the destructive path has no guard at all,
  unlike `reset_build_dir`.

**Fix:**
```python
from pydantic import TypeAdapter
from lecture_study.domain.models import SafeIdentifier
_LECTURE_ID = TypeAdapter(SafeIdentifier)

def default_layout(lecture_id: str, ...):
    lecture_id = _LECTURE_ID.validate_python(lecture_id)
    ...
```
Apply the same in `HarvestSources.for_lecture`, and use argparse `type=` for `--lecture-id`.

## Low

### LW-01: Signed-in check treats any non-200 anonymous answer as "refused"

**File:** `scripts/demo_site.py:80-86`

**Issue:** `anonymous_status != 200` also passes on 404, 500 or a connection-level oddity. The module
docstring says the anonymous request must be "refused". A broken server returning 500 for anonymous
requests, combined with a working signed-in page, gives a false pass.

**Fix:** assert the site's real refusal, for example `self.anonymous_status in {303, 401, 403}`. Use
the status the site app actually emits (the redirect to `/entrar`).

### LW-02: `_write_owner_only` follows symlinks, and `require_inside` accepts the root itself

**File:** `scripts/demo_site.py:160-162,187-192`, `src/lecture_study/demo/site_access.py:117-123`

**Issue:**
- `os.open(..., O_TRUNC)` without `O_NOFOLLOW` truncates the target of a pre-planted symlink
  (`.env`, `site-credentials.txt`).
- `prepare_access` also ignores the resolved path `require_inside` returns and uses the unresolved
  `directory`.
- `--directory workspace` passes containment and then `chmod 0700` is applied to the whole
  `workspace/` root.
- `openssl` creates `key.pem` first and the `chmod` follows. The 0700 parent directory mitigates the
  window.

**Fix:** `os.O_NOFOLLOW`, use the returned resolved path, require `resolved != root.resolve()`, and
optionally generate the key with `umask(0o077)` around the openssl call.

### LW-03: Report and harvest commands let non-`HarvestError` failures escape as tracebacks, and ablation files are outside the digest manifest

**File:** `scripts/demo_results.py:71-95,112-133,196-211`, `src/lecture_study/demo/harvest.py:148-165`

**Issue:**
- `run_report` catches only `HarvestError`. `AsrMeasurementError`, `RunMetricsError`, `PhaseLogError`
  and pydantic `ValidationError` raised by `load_adapter_results` surface raw.
- In `run_harvest` the same set can escape from `preview_markdown` after the harvest directory was
  already created.
- `record_asr_ablation` runs `ablation_directory.mkdir()` outside its `try`, so a pre-existing
  `ablation/` raises an unhandled `FileExistsError`.
- `ablation/*` and `preview.md` are written after `harvest.json` was computed, so the "digest per file"
  guarantee in ADR 0047 section 5 does not cover the ablation segments that feed the ASR numbers.

**Fix:** catch `(HarvestError, AsrMeasurementError, RunMetricsError, PhaseLogError, ValidationError)`
in both commands. Move the ablation write before `_harvested_files`, or re-write the manifest after
it.

### LW-04: Recovery harvest guard is hard-wired to the llama-cpp harvest and ignores the lecture id

**File:** `scripts/demo_blocked_recovery.py:42,128-130`

**Issue:** The guard checks only that `workspace/demo/runs/llama-cpp/llm_runs/critic.json` exists. It
does not check that the harvest belongs to `--lecture-id`, nor that the harvest manifest exists. A
stale or foreign harvest satisfies it, and the critic rerun then overwrites the real measurement.

**Fix:** read `HARVEST_ROOT/<ADAPTER>/harvest.json`, require `manifest.lecture_id == lecture_id` and the
critic record file.

### LW-05: ASR ablation "after dictionary" is recomputed at report time with the current dictionary, and the ADR wording on "reviews" is looser than the code

**File:** `src/lecture_study/demo/asr_measure.py:127-138`, `src/lecture_study/demo/run_metrics.py:108`, `docs/decisions/0047-demo-lecture-measurement-method.md` section 6 and Consequences

**Issue:**
- `measure_unprompted_transcript` calls `correct_specialist_terminology` on the stored raw segments.
  The committed ablation number (0 of 620) therefore changes if the dictionary changes, which
  contradicts "identical when regenerated from the same harvest".
- ADR section 6 defines reviews as "the critic stage's review calls". The code uses `calls -
  retry_calls`, which excludes schema retries. This is the better definition, but the ADR should say
  so.

**Fix:** persist the ablation's corrected text or corrections at harvest time (as the pipeline
condition does), or note the dependency in the ADR. Reword to "critic requests (calls minus retry
calls)".

### LW-06: First-attempt pass rate counts a first-attempt gateway error as a pass when the record still says `succeeded`

**File:** `src/lecture_study/demo/run_metrics.py:52-53,80-99`

**Issue:** `first_attempt_passes() = requests - retries`. A request whose first attempt raised a
gateway error is never retried (ADR section 6) and is counted as a pass. This is harmless while a
gateway error fails the whole script (status `failed`, rendered as `failed`). It becomes wrong the
moment a stage tolerates a per-request gateway error. The assumption is documented only in prose.

**Fix:** add an assertion test (or a comment at `stage_metrics`) tying the formula to "a gateway error
fails the run".

## Info

### IN-01: Duplicated driver constants and environment construction

**File:** `scripts/demo_run.py:42-47,176-179`, `scripts/demo_blocked_recovery.py:54-59,93`

**Issue:** `STUDY_GUIDE_ARGUMENTS`, the script path assembly and `{**os.environ, ADAPTER_OVERRIDE_ENV: ...}`
are duplicated. A change to the study-guide arguments needs two edits.

**Fix:** extract a shared module under `src/lecture_study/demo/` or `scripts/`.

### IN-02: Makefile interpolates `$(LECTURE)`, `$(ADAPTER)` and `$(FLASHCARDS_MINIMUM_TOTAL_CARDS)` unquoted

**File:** `Makefile:49-80`

**Issue:** `make demo-run LECTURE='x; ...'` is a shell injection by the invoking operator, which is not
a boundary. The same pattern already existed at `Makefile:28`. Mentioned for completeness; fix
together with MD-04 by quoting `"$(LECTURE)"`.

### IN-03: Committed report discloses the operator's local codex configuration

**File:** `docs/results/demo-results.md` ("Adapters not run"), `docs/results/demo-results.json`

**Issue:** The reason text names the model configured in the user-level codex config and a planning
checkpoint ("07-07"). It contains no credential, but it is local-environment detail in a public
artifact.

**Fix:** optionally generalize the wording ("the configured default model is not supported by the
account in use").

---

_Reviewed: 2026-10-15_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_

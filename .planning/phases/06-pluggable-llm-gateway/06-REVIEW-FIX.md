---
phase: 06-pluggable-llm-gateway
fixed_at: 2026-10-14T00:00:00Z
review_path: .planning/phases/06-pluggable-llm-gateway/06-REVIEW.md
iteration: 1
findings_in_scope: 11
fixed: 10
skipped: 0
not_reproduced: 1
status: all_fixed
---

# Phase 6: Code Review Fix Report

**Source review:** `.planning/phases/06-pluggable-llm-gateway/06-REVIEW.md`
**Iteration:** 1

**Summary:**
- Findings in scope: 11 (CR-01, WR-01 to WR-08, IN-05, and IN-07 requested together with WR-02)
- Fixed: 10 (see table)
- Skipped: 0
- Not reproduced: 1 (WR-06)

Edits and commits were made directly on the checked-out branch
`gsd/phase-06-pluggable-llm-gateway` as directed by the orchestrator (no
separate worktree). Verification ran in the main checkout.

## Fixed Issues

| Finding | Commit | Change |
|---------|--------|--------|
| CR-01 | 8d95a68 | `load_local_lectures` rejects duplicate ids (`LocalLectureError`); `RegistryRemoteEgressPolicy` raises `GatewayConfigurationError` for any id listed more than once, independent of the loader, and allows only when every row is true. `register_local_lecture` and `_upsert_local_lecture` read through the loader, so a duplicated file now stops registration instead of being hidden or rewritten. Regression tests cover (true,false), (false,true), (true,true) in the loader and in the policy. ADR 0046 section 6 gained one forward sentence on duplicate rows (no text deleted). |
| WR-02 | dd1a81d | Config path is parsed when `is_symlink() or exists()`, for default and explicit paths; a dangling symlink raises `GatewayConfigurationError` ("unreadable") instead of selecting Codex. |
| IN-07 (requested with WR-02) | aeb9c82 | The gateway YAML is composed first; any mapping repeating a key raises `GatewayConfigurationError` naming dotted key paths only. |
| WR-03 | bb01277 | A failure while writing a failed run record is logged and never replaces the propagated exception. The model digest is computed only on success; failure paths leave `model_sha256` null (documented in the field description), so Ctrl-C does not hash a multi-GB model. A write failure on a successful run still raises. |
| WR-04 | 78fdfcf | Record written through `tempfile.mkstemp` in the target directory, `fsync` of the file before `os.replace`, then best-effort `fsync` of the directory; the temp file is unlinked on failure. |
| WR-05 | 80b7948 | D-18 path and metadata-only content kept. Added optional `scope` (null means whole stage; the semantic script sets `pack_sequence=N` for `--pack-sequence`), and a failed run no longer replaces an existing `succeeded` record. A scoped successful run still replaces the previous record, with its scope recorded. |
| WR-07 | 90686dc | `run_adapter_process` also maps `RuntimeError` and `ValueError` (including `UnicodeEncodeError`) to `LlmCallError` with only the exception type name; the llama adapter encodes the prompt before any spawn and raises `LlmCallError` for lone surrogates; the factory maps `ValueError` (NUL in a configured path) to `GatewayConfigurationError`; the llama model path resolve also catches `ValueError`. |
| WR-08 | ab6d86f | Exam output build and write (new `_write_exam`), flashcard deck write, and podcast copy and compile now run inside the `record_llm_run` block, so the record is `succeeded` only after the artifact is written. |
| WR-01 | 0e0df46 | New `lecture_study.domain.lecture_ownership.require_lecture_ownership` (typed `LectureOwnershipError`, identifiers only). Applied to evidence packs (semantic, drafts, critic scripts) and semantic ledgers (drafts, critic, exam, flashcards scripts), before any gateway is built. See note below for drafts and validated units. |
| IN-05 | 88818a1 | Podcast `--help` description now says "gateway LLM". |

### WR-01 coverage note

- Validated units: `load_critic_approved_drafts` already rejects a unit whose
  `lecture_id` differs from the requested one (`_read_unit_filed_under`), so
  the exam, flashcard and podcast scripts are covered for units.
- Persisted first drafts (critic script) were not given an echoed-id check:
  the drafts are model output whose echoed `lecture_id` is documented as
  untrustworthy (ADR 0035; the critic script anchors identity on the pack), so a
  strict equality check would change existing behavior. The packs and ledgers
  checks are the identity anchors. This is a deliberate narrowing, flagged for
  the orchestrator.

## Not reproduced

### WR-06: Exam and flashcard scripts use an unvalidated `lecture_id`

Not reproduced. In both scripts the first statement of `_run` is
`load_critic_approved_drafts(..., args.lecture_id)`, whose first statement
validates the id through `SafeIdentifier` (`src/lecture_study/assembly/assembler.py`,
line 92). Validation therefore happens before `_load_semantic_ledgers`,
`open_stage_gateway`, `record_llm_run` and the output paths. The existing test
`test_main_refuses_a_malformed_lecture_id_before_opening_a_gateway` in
`tests/test_generate_codex_mock_exam.py` covers the exam script. No change made.

## Skipped Issues

None.

## Verification

Run in the main checkout after the last fix:

- `uv run pytest -q -rs`: 2527 passed, 1 warning (pre-existing starlette
  deprecation).
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 234 files already formatted.
- `uv run mypy src`: no issues in 125 source files.

One earlier full run showed a single transient failure in
`tests/llm_gateway/test_codex_adapter.py` (stub executable start failed with
"Operation not permitted" on the freshly written stub, a host exec-permission
race unrelated to these changes). It passed on every rerun, in isolation and in
the next full run.

---

## Round 2 (iteration-2 review)

**Source review:** `.planning/phases/06-pluggable-llm-gateway/06-REVIEW.md` (iteration 2)
**Scope:** WR-01..WR-06 of the iteration-2 report, plus IN-02 (trivial).
**Summary:** findings in scope 6; fixed 6; skipped 0; one Info item fixed.
Work was committed directly on the checked-out branch (no temporary worktree),
as instructed by the invoking workflow.

### Fixed

| Finding | Commit | Applied fix |
|---------|--------|-------------|
| WR-02 | `f3890a2` | New `src/lecture_study/ops/strict_yaml.py` holds the duplicate-key scan moved out of `llm/config.py`. `load_local_lectures` now rejects repeated keys (inside a row and repeated top-level blocks) with a `LocalLectureError` naming dotted key paths only, and maps `OSError` other than absence to a typed error instead of treating an unreadable file as an empty registry. `ops/` is imported by both `study_materials/` and `llm/` already; `site/` is untouched and `tests/test_llm_layering.py` allows the new module. Regression tests: false-then-true, true-then-false, repeated `publish`, two top-level `lectures` blocks, and a policy test asserting `GatewayConfigurationError` from `is_remote_allowed` and `require_remote_egress_allowed` for each. |
| WR-04 | `08f79eb` | `generate_codex_drafts.py` stamps the pack's `lecture_id` and `pack_sequence` on every draft it writes; `generate_codex_critic_review.py::_load_drafts` wraps the loader in `require_lecture_ownership(..., kind="drafts")`. The error names ids only. Tests for a foreign draft, a valid draft, and the stamping over a drifted model echo. Existing behavior for valid inputs is unchanged. Older drafts with a drifted echo fail loudly and are regenerated. |
| WR-05 | `29c9a13` | Codex adapter: lone-surrogate prompt (`UnicodeEncodeError` in the argv cost), scratch-directory creation `OSError` and response-read `OSError` now raise `LlmCallError` with the exception type name only (`from None`, no prompt text). Scratch cleanup errors are ignored. Three tests. |
| WR-03 | `61d0c4b` | `_configuration_file_present` uses `Path.lstat`: absence is exactly `FileNotFoundError`; any other `OSError` raises `GatewayConfigurationError`. Dangling symlinks still count as present and fail in the read. Tests: unreadable parent directory (skipped when running as root) and absent default file still resolving to defaults. |
| WR-01 | `8e32e12` | Design within D-18 (same path tree, metadata-only content, no schema change): `<stage>.json` always records the latest attempt with its true status and scope; when a failed or scoped attempt displaces a whole-stage `succeeded` record, that record is first kept as `<stage>.superseded.json` in the same `workspace/data/llm_runs/<lecture_id>/` directory, and the next whole-stage success removes it. Failing to preserve the sibling never blocks the latest record. Tests cover the failed-rerun-after-overwrite case, the scoped-success-overwrites-whole-stage case, a later attempt not replacing the preserved record, removal on a new whole-stage success, no sibling without an earlier whole-stage success, and the unpreservable-sibling case. The old test that asserted the stale record was kept was replaced. Logic-class finding: requires human verification that the sibling-file rule matches the intended provenance semantics. |
| WR-06 | `b3ca46c` | ADR 0046 section 7 amended with a forward clarification (latest-attempt semantics, null digest on failure, `scope`, the superseded sibling, the durable write protocol, error masking rule) and section 6 with the registry repeated-key rule and the configuration probe rule. No earlier text deleted. |
| IN-02 | `c1196dc` | Critic `--max-retries` help and podcast traceability message no longer say "Codex". |

IN-04 (finding id in a docstring, over-long line) was resolved incidentally by
the WR-01 rewrite of that function. Remaining Info items (IN-01, IN-03,
IN-05..IN-10) were not touched, as instructed.

### Skipped

None.

### Verification

Run in the main checkout after the last fix:

- `uv run pytest -q -rs`: 2548 passed, 1 warning (pre-existing starlette
  deprecation). Baseline before this round: 2527.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 235 files already formatted.
- `uv run mypy src`: no issues in 126 source files.

Transient host exec failures ("Operation not permitted" when starting the stub
executable, a `PermissionError` from `process_lifecycle.py`) appeared once in a
`tests/llm_gateway` run and once in one full run; each passed on immediate
rerun, classified as the same environmental exec race noted in Round 1.

---

_Fixer: Claude (gsd-code-fixer)_
_Iteration: 2_

---

## Round 3 (iteration-3 review and security-audit residuals)

**Source review:** `.planning/phases/06-pluggable-llm-gateway/06-REVIEW.md` (iteration 3)
**Scope:** iteration-3 WR-02, security-audit residuals W-1 and W-2, and a
documentation note for iteration-3 WR-01 (accepted limitation).
**Summary:** findings in scope 3; fixed 3; skipped 0; 1 accepted limitation.
Work was committed directly on the checked-out branch (no temporary worktree),
as instructed by the invoking workflow.

### Fixed

| Finding | Commit | Applied fix |
|---------|--------|-------------|
| WR-02 | `c40a4aa` | `_signal_process_tree` now treats a `PermissionError` from `os.killpg` (macOS answers EPERM for a group whose leader just exited) by signalling the leader directly (`terminate`/`kill`); a `ProcessLookupError` from that direct signal means the leader is gone, and the existing wait/reap and remaining-group sweep run. A refusal from the leader itself still raises, so a live child is never silently left running. `_signal_process_group` (the post-reap sweep) ignores EPERM. Regression tests stub `os.killpg` to raise `PermissionError`: two in `tests/test_process_lifecycle.py` (output cap and timeout, asserting the real error type, termination, kill and reap) and one in `tests/llm_gateway/test_codex_adapter.py` (the adapter reports "output exceeded 1024 bytes", not "could not start"). All three fail without the fix. Logic-class finding: requires human verification of the cleanup semantics. |
| W-1 | `caeab14` | `src/lecture_study/llm/config.py` maps `ValueError` and `RecursionError` from the YAML read to `GatewayConfigurationError`; `src/lecture_study/study_materials/local_lectures.py` maps them to `LocalLectureError` through the new `_parse_registry_yaml`. Messages carry the exception type name only. Tests for both loaders (calendar-invalid timestamp `2001-13-45`, 5000-level nesting) and for `RegistryRemoteEgressPolicy` refusing with `GatewayConfigurationError` through `is_remote_allowed` and `require_remote_egress_allowed`. Six new tests, all failing without the fix. |
| W-2 | `f16df70` | `LlamaCppAdapter.complete` creates its scratch directory through a typed context manager (`ignore_cleanup_errors=True`, like the Codex adapter) and maps prompt-file write and work-directory `OSError` to `LlmCallError` with the exception type name only (`from None`, no prompt text). Two tests. |
| WR-01 note | `63dad05` | One sentence added to ADR 0046 section 7: only the most recent displaced whole-stage success is preserved and per-pack provenance across several partial reruns is not reconstructed. No text deleted. |

### Accepted limitation

Iteration-3 WR-01: the run record keeps `<stage>.json` (latest attempt) and one
`<stage>.superseded.json` (the most recent displaced whole-stage success).
Several consecutive partial reruns therefore do not preserve per-pack
provenance. Accepted and documented in ADR 0046 section 7; no code change.

### Flake loop

`uv run pytest -q tests/llm_gateway/test_codex_adapter.py -k output_cap` was run
40 consecutive times after the fix: 40 passes, 0 failures. A direct loop of 400
`run_bounded_process` calls against a child that exceeds a 1024-byte cap (the
review reproduced 10 `PermissionError` escapes in 400 before the fix) produced 0
unexpected exceptions.

### Verification

Run in the main checkout after the last fix (no worktree was created):

- `uv run pytest -q -rs`: 2559 passed, 1 warning (pre-existing starlette
  deprecation). Baseline before this round: 2548.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 235 files already formatted.
- `uv run mypy src`: no issues in 126 source files.

## Round 4 (final)

### Fixed

- Commit `3a9efa1`: the typed-error mapping around `yaml.safe_load` now also
  catches `KeyError` and `AttributeError`, so explicit invalid tags (for example
  `x: !!bool .nan` and `x: !!timestamp 0x`) no longer escape as raw exceptions.
  Applied in `src/lecture_study/llm/config.py` (`GatewayConfigurationError`) and
  `src/lecture_study/study_materials/local_lectures.py` (`LocalLectureError`).
  Messages stay content-free (exception type name only). Regression cases were
  added to the configuration, local-registry and egress-policy tests; the
  egress case asserts `GatewayConfigurationError` for such a registry.

### Accepted, not fixed

Residuals from `06-REVIEW-ROUND3.md`:

- WR-01 (post-reap sweep ignores `EPERM`; reader-thread join is unbounded):
  pre-existing, low likelihood, accepted and tracked.
- IN-01 (`EPERM` read as "present" by one helper and "nothing left" by the
  next): the combination behaves correctly; only the naming of the contract is
  inconsistent.
- IN-02 (cleanup tests simulate `EPERM` instead of exercising a real zombie
  leader): coverage gap, not a defect; no behavior change at stake.

Residuals from the final security audit:

- `ignore_cleanup_errors` is silent: a failed temporary-directory removal is not
  reported; accepted as a residual, not fixed in this phase.
- `LocalLectureError` embeds a parser snippet: pre-existing behavior, not
  introduced by this phase; not fixed here.
- ADR 0046 section 1 and the `llm` package docstring drift on which `ops`
  modules are allowed; text-only drift, no runtime effect.

### Verification

Run in the main checkout (no worktree was created):

- `uv run pytest -q -p no:cacheprovider`: 2565 passed, 1 warning (pre-existing
  starlette deprecation). Baseline before this round: 2559.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 235 files already formatted.
- `uv run mypy src`: no issues in 126 source files.

---

_Fixer: Claude (gsd-code-fixer)_
_Iteration: 4_

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

_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_

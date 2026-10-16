---
phase: 06-pluggable-llm-gateway
reviewed: 2026-10-14T00:00:00Z
depth: deep
iteration: 3
files_reviewed: 26
files_reviewed_list:
  - config/llm-gateway.example.yaml
  - docs/decisions/0046-pluggable-llm-gateway-and-local-adapter.md
  - scripts/01_provision_engines.sh
  - scripts/generate_codex_critic_review.py
  - scripts/generate_codex_drafts.py
  - scripts/generate_codex_mock_exam.py
  - scripts/generate_codex_personal_study_flashcards.py
  - scripts/generate_codex_podcast.py
  - scripts/generate_codex_semantic_ledgers.py
  - scripts/register_local_lecture.py
  - src/lecture_study/cli/llm_gateway_wiring.py
  - src/lecture_study/cli/orchestrator.py
  - src/lecture_study/domain/lecture_ownership.py
  - src/lecture_study/ops/strict_yaml.py
  - src/lecture_study/llm/__init__.py
  - src/lecture_study/llm/bounded_call.py
  - src/lecture_study/llm/codex_adapter.py
  - src/lecture_study/llm/config.py
  - src/lecture_study/llm/egress.py
  - src/lecture_study/llm/factory.py
  - src/lecture_study/llm/gateway.py
  - src/lecture_study/llm/json_completion.py
  - src/lecture_study/llm/llama_cpp_adapter.py
  - src/lecture_study/llm/run_record.py
  - src/lecture_study/study_materials/local_lectures.py
  - src/lecture_study/study_materials/remote_llm_policy.py
findings:
  critical: 0
  warning: 2
  info: 9
  total: 11
status: issues_found
---

# Phase 6: Code Review Report (iteration 3)

**Reviewed:** 2026-10-14
**Depth:** deep
**Files Reviewed:** 26
**Status:** issues_found

## Summary

Final re-review of the phase-final diff after the Round 2 fixes (commits `d1988e5` to `ea7e4f1`). Every iteration-2 warning was re-checked in the source and, where behavior was claimed, by running the code. `ruff check`, `ruff format --check` and `mypy src` are green. No critical issue was found: the egress gate has no bypass, and the Round 2 changes introduce no regression in the gate, the registry loader or the configuration probe.

Two warnings remain. One is a residual of the iteration-2 provenance finding (the two-slot run record cannot represent more than one displacing attempt). The other is a macOS process-cleanup defect that surfaces through the Codex adapter as a misleading error and as a flaky test, reproduced in this review.

### Verification of iteration-2 findings

| ID | Verdict | Evidence |
|----|---------|----------|
| WR-01 | Fixed for the reproduced case; residual remains, see WR-01 | Succeeded(A) then failed(B): `<stage>.json` states `failed`, `<stage>.superseded.json` keeps A, the next whole-stage success removes the sibling (`src/lecture_study/llm/run_record.py:255-290`). A second displacing attempt overwrites the only record of the first one. Reproduced. |
| WR-02 | Fixed | `load_local_lectures` runs `duplicated_key_paths` on the exact text it parses (`src/lecture_study/study_materials/local_lectures.py:150-179`). Reproduced: `false` then `true` inside a row now fails closed through `RegistryRemoteEgressPolicy.is_remote_allowed` with `GatewayConfigurationError`. Quoted and `? key` duplicate spellings and a repeated top-level key are detected; a multi-document stream raises `ComposerError` (a `YAMLError`, mapped by both callers). An unreadable registry is a typed error, not an empty registry. Merge-key overrides (`<<`) are standard YAML override semantics and are not reported, which is acceptable. |
| WR-03 | Fixed | `_configuration_file_present` uses `lstat`; only `FileNotFoundError` means absent, every other `OSError` (including `NotADirectoryError`) raises `GatewayConfigurationError` (`src/lecture_study/llm/config.py:110-126`). A dangling symlink counts as present and fails in the read. |
| WR-04 | Fixed | The drafts script stamps `lecture_id` and `pack_sequence` from the pack on every draft it writes (`scripts/generate_codex_drafts.py:195-197,224-235`); the critic wraps the loader in `require_lecture_ownership(..., kind="drafts")` before any gateway is opened, and `LectureOwnershipError` is in the `main()` handler (`scripts/generate_codex_critic_review.py:89,290-297`). Repair-loop redrafts are not stamped, but the unit identity is anchored to the pack (`_write_validated_unit`) and `_read_unit_filed_under` re-stamps the content on read, so no foreign text reaches a model through this path. |
| WR-05 | Fixed | Lone-surrogate prompt (`_argv_cost_in_bytes`), scratch-directory `OSError` and response-read `OSError` all raise `LlmCallError` with the exception type name only, `from None` (`src/lecture_study/llm/codex_adapter.py:138-180`). |
| WR-06 | Fixed | ADR 0046 sections 6 and 7 now describe latest-attempt semantics, null digest on failure, `scope`, the superseded sibling, the durable write protocol and the masking rule; the earlier text is untouched. The statement that a record "never claims that a model produced outputs a later attempt altered" holds only for the latest file, see WR-01. |

### Egress gate end to end (re-checked)

Script (id validated by `SafeIdentifier` or by `load_critic_approved_drafts`) -> `open_stage_gateway` -> `load_gateway_configuration` (lstat probe, strict YAML) -> `RegistryRemoteEgressPolicy` (reserved ids decided only by the registry row, other ids only by the portal list, an id in both is an error, a repeated id or repeated key is an error) -> `build_gateway` -> `require_remote_egress_allowed` -> `CodexCliAdapter`. `CodexCliAdapter` is still constructed in exactly one place (`src/lecture_study/llm/factory.py:55`) and `codex exec` is spawned only from `src/lecture_study/llm/codex_adapter.py:74`. The registry is read lazily and every failure of that read becomes `GatewayConfigurationError`, so the gate fails closed. The orchestrator preflight and the child scripts resolve the same two environment names. Loaded packs, ledgers and persisted drafts are tied to the cleared id before any gateway is built. No bypass was found.

## Warnings

### WR-01: The run record keeps only one displaced success, so a second displacing attempt makes the provenance wrong again

**File:** `src/lecture_study/llm/run_record.py:255-290`
**Issue:** The Round 2 rule preserves a whole-stage `succeeded` record in `<stage>.superseded.json` when a failed or scoped attempt replaces it, and the next displacing attempt does not touch the sibling (`_preserve_whole_stage_success` returns early because the current file is no longer a whole-stage success). That handles one displacing attempt. A second one overwrites the only record of the first. Reproduced with scripted gateways: whole-stage run by `codex`, then a scoped run (`pack_sequence=2`) by `llama-cpp` model `llama-model`, then a scoped run (`pack_sequence=3`) by `llama-cpp` model `llama-model-b`. The end state is `semantic.json` = scope 3 / `llama-model-b` and `semantic.superseded.json` = whole stage / `codex`. No file records that ledger 2 was produced by `llama-model`; the sibling attributes it to `codex`. The same holds for repeated failed reruns that each overwrote part of the outputs. The sidecar exists to answer "which model produced this artifact" (D-13), and the sibling's caveat in ADR 0046 section 7 ("may since have been overwritten by the attempt in `SCRIPT_STAGE.json`") does not cover attempts that no longer have a record. The scoped semantic run (`--pack-sequence`) is the realistic trigger: re-running individual packs one after another is the normal use of that option.
**Fix:** Do not keep a single slot. Either append every attempt to a history file in the same directory, for example `<stage>.history.jsonl` (one metadata-only `LlmRunRecord` line per attempt, written with the same fsync protocol or `O_APPEND`), keeping `<stage>.json` as the latest attempt, or keep a per-output map (`outputs: {"ledger_002": {adapter_name, model_identifier, finished_at}}`) merged on each run. A minimal change within D-18: make `_preserve_whole_stage_success` also preserve a scoped success under `<stage>.<scope>.json` so each scope keeps its last record, and amend ADR 0046 section 7 to say that a record exists per scope.

### WR-02: A macOS `killpg` EPERM during cleanup aborts the Codex call cleanup and is reported as "could not start the process"; the output-cap test is flaky because of it

**File:** `src/lecture_study/llm/bounded_call.py:82-83`, root cause at `src/lecture_study/ops/process_lifecycle.py:454-466,490-495`
**Issue:** `describe_process_failure` describes every `OSError` as "could not start the process". `_signal_process_tree` and `_signal_process_group` catch only `ProcessLookupError`. On macOS, `os.killpg` on the group of a child that has just exited returns `EPERM`, which Python raises as `PermissionError`. Reproduced by running `run_bounded_process` 400 times against a child that writes 4096 bytes with a 1024-byte cap: 10 runs raised `PermissionError: [Errno 1] Operation not permitted` from `_terminate_and_reap_sync` -> `_signal_process_tree` while handling `ProcessOutputLimitExceeded`, replacing the real cause (output cap) and skipping the reap step that follows the signal. Through the adapter this becomes `codex call for stage critic failed: could not start the process: Operation not permitted`, which names the wrong cause, and `tests/llm_gateway/test_codex_adapter.py::test_output_cap_overflow_raises_a_call_error` failed in 1 of 6 repeated runs of the gateway test selection here with exactly that message. The Round 1 and Round 2 fix reports attributed the same symptom to a "host exec-permission race" and passed over it; the cause is in the cleanup path. A timeout in the same window (child exits as the deadline fires) takes the same path.
**Fix:** Treat `PermissionError` like `ProcessLookupError` in the two signal helpers when the leader has already exited, and let the reap step run.
```python
except ProcessLookupError:
    return False
except PermissionError:
    # macOS: EPERM for a process group whose leader has exited
    if process.poll() is not None:
        return False
    raise
```
and in `describe_process_failure` report `could not run the process` (or split `FileNotFoundError`/`PermissionError` at spawn from later failures) so a post-start failure is not called a start failure. Add a regression test that stubs `os.killpg` to raise `PermissionError` after the child has exited.

## Info

### IN-01: The policy's "unreadable" message drops the typed registry detail the Round 2 loader produces

**File:** `src/lecture_study/study_materials/remote_llm_policy.py:91-98`
**Issue:** `LocalLectureError` now carries precise, content-free causes (repeated key paths, repeated ids, unreadable file). `_load_registered_lectures` replaces all of them with "the local lecture registry at PATH is unreadable", chaining the cause only in `__cause__`. The scripts print `error: {error}` without the chain, so a registry with `remote_llm_allowed` repeated in a row is reported to the operator as unreadable. Reproduced.
**Fix:** Append the loader's message (it names key paths and ids only): `f"...is unusable ({error}); the codex adapter is refused for local lectures"`.

### IN-02: Success-path run-record write failure escapes as a raw `OSError` after the outputs exist (carried over, unchanged)

**File:** `src/lecture_study/llm/run_record.py:200`, `scripts/generate_codex_semantic_ledgers.py:67-80` (same shape in the other five)
**Issue:** Each `main()` catches only `FileNotFoundError` from the `OSError` family, so a full disk or permission error on the record prints a traceback and exits 1 although every artifact was written.
**Fix:** Catch `OSError` in `main()` and print one line naming the record path.

### IN-03: Blanket `RuntimeError`/`ValueError` mapping hides helper bugs behind a type name (carried over, unchanged)

**File:** `src/lecture_study/llm/bounded_call.py:57-67`
**Issue:** The mapping is `from None` and nothing is logged, so a programming error in the helper is indistinguishable from an operational failure (WR-02 above is an example of the cause being lost this way).
**Fix:** Keep the content-free reason and log the original traceback at DEBUG.

### IN-04: Dead and misleading configuration surface (carried over, unchanged)

**File:** `src/lecture_study/llm/llama_cpp_adapter.py:50,264-271`, `src/lecture_study/llm/config.py:74-86`
**Issue:** `_STRIPPED_VARIABLE_PREFIXES` filters an already allowlisted environment and removes nothing; `model_family` selects no code path; `require_llama_cpp_settings_when_selected` counts an unused `default_adapter` as selected.
**Fix:** Drop the prefix filter or document it as belt and braces; compute selection from reachable adapters.

### IN-05: A retry whose prompt outgrows the limit hides the first failure (carried over, unchanged)

**File:** `src/lecture_study/llm/json_completion.py:34-42`
**Issue:** A `PromptTooLargeError` on the retry discards the parse error that caused the retry.
**Fix:** Chain the first error into the retry-time message.

### IN-06: Provisioning download hardening (carried over, unchanged)

**File:** `scripts/01_provision_engines.sh:157-159`
**Issue:** `--proto '=https'` does not constrain redirects, and a failed `curl` or checksum leaves the multi-GB `.part` file behind.
**Fix:** Add `--proto-redir '=https'` and remove the `.part` file on failure.

### IN-07: Codex prompt passed as the last argv element without `--` (carried over, unchanged)

**File:** `src/lecture_study/llm/codex_adapter.py:104-118`
**Issue:** A prompt starting with `-` would be parsed as a flag; the 900 KiB budget ignores the Linux per-argument limit; the prompt is visible in `ps`.
**Fix:** Insert `"--"` if the CLI accepts it; note the per-argument limit in ADR 0046.

### IN-08: Flashcard deck failure message embeds model output (carried over, unchanged)

**File:** `scripts/generate_codex_personal_study_flashcards.py:288-292`
**Issue:** `f"...{error}"` interpolates a pydantic `ValidationError` including `input_value` of generated cards into the exception message that reaches stderr.
**Fix:** Build the message from `error.errors(include_input=False)` locations and types.

### IN-09: Codex provenance does not identify the model (carried over, unchanged)

**File:** `src/lecture_study/llm/codex_adapter.py:37,56-57`
**Issue:** `describe()` returns the constant `codex-cli`, so the record cannot show a Codex model or version change.
**Fix:** Record a bounded `codex --version`, or document the opacity in ADR 0046.

---

_Reviewed: 2026-10-14_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_

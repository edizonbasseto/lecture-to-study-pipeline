---
phase: 06-pluggable-llm-gateway
reviewed: 2026-10-14T00:00:00Z
depth: deep
files_reviewed: 24
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
  critical: 1
  warning: 8
  info: 8
  total: 17
status: issues_found
---

# Phase 6: Code Review Report

**Reviewed:** 2026-10-14
**Depth:** deep
**Files Reviewed:** 24
**Status:** issues_found

## Summary

Reviewed the gateway package, the composition wiring, the orchestrator preflight, the six migrated scripts (diffed against `8610946`), the registry/egress policy, the provisioning script and ADR 0046. Call chains traced: script -> `open_stage_gateway` -> `load_gateway_configuration` / `RegistryRemoteEgressPolicy` -> `build_gateway` -> adapter -> `run_adapter_process` -> `run_bounded_process`, and the `record_llm_run` / `complete_json` wrappers.

Verified clean: `ruff check`, `mypy` on the new modules and `pytest tests/llm_gateway tests/test_llm_layering.py` (192 passed). The Codex argv, scratch-directory and environment construction match the pre-phase scripts. Layering holds: `site/` does not import `llm/`, and `ops/` does not either. The tokenizer count is read from `--ids` output, so prompt text cannot spoof the `Total number of tokens:` line. Gateway error messages are built from stored attributes only.

The main defect is in the egress gate that the phase exists to deliver. A registry with contradictory duplicate rows for one lecture id is treated as allowed (reproduced). Several robustness gaps in the run record and in configuration discovery follow. Nothing here was found to leak prompt or model output through the new gateway error types.

## Critical Issues

### CR-01: Duplicate local-registry rows for one lecture id fail OPEN on `remote_llm_allowed`

**File:** `src/lecture_study/study_materials/remote_llm_policy.py:70-76`
**Issue:** `_remote_allowed_local_ids` builds the allowed set with "any row for this id has `remote_llm_allowed: true`". `load_local_lectures` (`src/lecture_study/study_materials/local_lectures.py:133-148`) does not reject duplicate `lecture_id` rows, and `register_local_lecture` only guarantees uniqueness when it writes. The registry is a hand-editable YAML file. An operator who appends a `remote_llm_allowed: false` row to revoke clearance (or leaves an old `true` row above a new `false` row) keeps the lecture routed to the remote Codex adapter. Reproduced with a two-row registry (`true`, then `false`) for id `925100`: `RegistryRemoteEgressPolicy(...).is_remote_allowed("925100")` returned `True`. This contradicts the fail-closed rule (D-15) and the sibling publish gate, which is conservative for duplicates (`unpublished_lecture_ids` treats any non-published row as unpublished).
**Fix:** Treat a duplicated id as a configuration error, or allow only when every row for the id is true.
```python
def _remote_allowed_local_ids(self) -> frozenset[str]:
    self.require_consistent_sources()
    rows_by_id: dict[str, list[bool]] = {}
    for lecture in self._registered_lectures():
        rows_by_id.setdefault(lecture.lecture_id, []).append(lecture.remote_llm_allowed)
    duplicated = sorted(i for i, flags in rows_by_id.items() if len(flags) > 1)
    if duplicated:
        raise GatewayConfigurationError(
            "local lecture registry lists ids more than once: " + ", ".join(duplicated)
        )
    return frozenset(i for i, flags in rows_by_id.items() if all(flags))
```
Also reject duplicate ids inside `load_local_lectures` so every consumer of the registry is protected.

## Warnings

### WR-01: Egress decision is keyed on the CLI argument, never on the lecture identity of the content actually sent

**File:** `scripts/generate_codex_semantic_ledgers.py:79-96`, `scripts/generate_codex_drafts.py:144-170`, `scripts/generate_codex_critic_review.py:91-130` (same shape in the exam, podcast and flashcards scripts)
**Issue:** `open_stage_gateway(..., lecture_id=lecture_id)` is cleared against the positional argument. The prompt is then built from `pack.lecture_id`, ledger, draft or unit files read from `--packs-root`, `--ledgers-root`, `--drafts-root` and `--validated-units-root`. Nothing asserts that the loaded content belongs to `lecture_id`. A misfiled pack directory, or a `--packs-root` override pointing at another lecture's tree, sends an uncleared lecture's text to the remote model under a cleared id. The semantic validation only checks the ledger against the pack, not the pack against the argument.
**Fix:** After loading, assert `pack.lecture_id == lecture_id` (and the same for drafts, ledgers and validated units) and exit non-zero on mismatch before the gateway is built.

### WR-02: A broken or looping default config symlink silently selects the built-in Codex default

**File:** `src/lecture_study/llm/config.py:99-106`
**Issue:** `path.exists()` follows symlinks and returns `False` for a dangling symlink. For the default path the loader then returns `GatewayConfiguration()` (Codex default, empty allow-list) instead of failing. Reproduced: a dangling `config/llm-gateway.local.yaml` yields `default_adapter == codex`. An operator who intended a local-only setup gets Codex; only the egress flag stands between the lecture and the remote model. A configuration that exists but cannot be read must be an error, not an absent file.
**Fix:**
```python
if path.is_symlink() or path.exists():
    return _parse_configuration_file(path)   # raises GatewayConfigurationError on OSError
```
Apply the same rule to the explicit path branch.

### WR-03: `record_llm_run` writes the record in `finally`; a failure there masks the real error, and the digest step blocks interruption

**File:** `src/lecture_study/llm/run_record.py:160-176`, `src/lecture_study/llm/run_record.py:133-138`, `src/lecture_study/llm/run_record.py:190-199`
**Issue:** The `finally` block calls `recorder.usages()` (a full SHA-256 of the model file, several GB for the example model), builds the record and writes it. (a) If any of that raises (disk full, unreadable root, pydantic error), the new exception replaces the original `LlmCallError` / `PromptTooLargeError` / `KeyboardInterrupt`, and the script's `main()` does not catch bare `OSError`, so the operator sees a traceback about the sidecar instead of the cause. (b) On Ctrl-C the process spends minutes hashing before it can exit, and a second Ctrl-C interrupts `finally` with no record written. (c) Hashing also runs on every failure, including a prompt-too-large refusal that never touched the model.
**Fix:** Wrap the write in `finally` so it can never replace the primary exception (log a warning instead), and skip or defer the digest unless the run produced at least one call and the exception is not a `BaseException` that is not an `Exception`. Compute the digest lazily once and cache it by `(path, mtime_ns, size)` if repeated runs matter.

### WR-04: Run-record "atomic" write uses a fixed temporary name and no fsync

**File:** `src/lecture_study/llm/run_record.py:202-210`
**Issue:** The temporary file is always `<stage>.json.tmp`. Two concurrent runs of the same lecture and stage (an orchestrator child plus a manual rerun, or a retried phase) write and truncate the same temporary file, and one `replace` can publish the other's partially written content. `replace` without `fsync` of the file and directory can leave an empty or zero-length record after a crash on APFS/ext4, which defeats the purpose of "atomic" in ADR 0046 section 7.
**Fix:** Create the temporary file with `tempfile.NamedTemporaryFile(dir=path.parent, delete=False)` (unique name), `flush()` and `os.fsync()` before `os.replace`, and unlink it on failure.

### WR-05: The provenance record is overwritten per script/stage and can misattribute outputs

**File:** `src/lecture_study/llm/run_record.py:141-142,176`, `scripts/generate_codex_semantic_ledgers.py:88-98`
**Issue:** One file per `(lecture_id, script_stage)` is replaced on every run, including a failed run and a partial run. `--pack-sequence N` regenerates a single ledger with whatever adapter is configured now, then overwrites `semantic.json`, so the record claims the whole `07_semantic_ledgers/<id>/` tree was produced by the last adapter and model although the other ledgers came from a different one. A failed rerun likewise replaces the record of the previous successful run whose outputs are still on disk. The sidecar therefore does not reliably answer "which model produced this artifact", which is its stated purpose (D-13).
**Fix:** Record the per-output provenance (the list of produced output names), keep a history (timestamped file or append-only list), or at minimum do not overwrite a `succeeded` record with a `failed` one for the same outputs.

### WR-06: Exam and flashcard scripts use an unvalidated `lecture_id` for file loading and the egress check

**File:** `scripts/generate_codex_mock_exam.py:164-185`, `scripts/generate_codex_personal_study_flashcards.py:180-201`
**Issue:** The four other scripts validate the argument with `SafeIdentifier` first. These two pass `args.lecture_id` straight into `load_critic_approved_drafts`, `_load_semantic_ledgers` (`ledgers_root / lecture_id`, so `..` segments traverse), `open_stage_gateway` and `output_root / lecture_id`. Validation only happens later inside `record_llm_run`, after the loaders have read files and the gateway has been built. With the Codex adapter the egress policy refuses such ids, so there is no remote bypass, but with the llama adapter the traversal reads and writes outside the intended trees, and the gateway error message echoes the raw string (including newlines) to stderr.
**Fix:** Add the same `_LECTURE_ID_ADAPTER.validate_python(args.lecture_id)` as the first statement of `_run` and use the validated value everywhere.

### WR-07: Several failure types escape `run_adapter_process` and the gateway hierarchy

**File:** `src/lecture_study/llm/bounded_call.py:50-58`, `src/lecture_study/llm/llama_cpp_adapter.py:273-278`
**Issue:** `run_bounded_process` raises `RuntimeError("failed to read subprocess output")` on a reader-thread failure and `ValueError` on invalid limits; neither is in the caught tuple. `_prepare_scratch` raises `UnicodeEncodeError` for a prompt containing a lone surrogate, and `resolve_trusted_executable` raises `ValueError` for an embedded NUL in a configured path (`src/lecture_study/llm/factory.py:79-89` only catches `OSError`). None of these is an `LlmGatewayError`, so every script's `main()` lets them escape as a traceback with exit code 1, and the typed-error contract of ADR 0046 section 1 ("every failure is a typed gateway error") does not hold.
**Fix:** Catch `(RuntimeError, ValueError, UnicodeError)` at the adapter boundary and map them to `LlmCallError` / `GatewayConfigurationError` with a fixed reason string.

### WR-08: Run record reports `succeeded` before the script's output is validated and written

**File:** `scripts/generate_codex_mock_exam.py:180-213`, `scripts/generate_codex_podcast.py:194-210`, `scripts/generate_codex_personal_study_flashcards.py:196-211`
**Issue:** The `with record_llm_run` block closes after the model calls. The exam shuffle and `MockExam(...)` construction, the podcast compile and the flashcard atomic write happen afterwards. A failure there (for example a duplicate-prompt `ValidationError` in the deck, or an I/O error) leaves a `succeeded` record next to missing or stale output. The semantic script writes inside the block and so records correctly; the behavior is inconsistent across the six scripts.
**Fix:** Move output construction and writing inside the `with` block (or mark the record failed explicitly), as the semantic and drafts scripts partly do.

## Info

### IN-01: Dead code and unused configuration surface

**File:** `src/lecture_study/llm/llama_cpp_adapter.py:50,263-270`, `src/lecture_study/llm/config.py:56-59,73-80`
**Issue:** `_STRIPPED_VARIABLE_PREFIXES` filters a dictionary that already comes from an allowlist (`sanitized_child_process_environment()` only inherits eight names), so it never removes anything and gives a false impression of defense in depth. `model_family` is validated but selects no code path. `require_llama_cpp_settings_when_selected` counts `default_adapter` as "selected" even when every one of the six stages overrides it, so an unused default of `llama-cpp` demands an unused `llama_cpp` section.
**Fix:** Drop the prefix filter (keep the `LLAMA_ARG_OFFLINE` assignment), or document it as belt-and-braces; compute `_selected()` from the adapters actually reachable.

### IN-02: A retry whose prompt grows past the limit hides the first failure; the podcast stage is effectively unusable locally

**File:** `src/lecture_study/llm/json_completion.py:34-42`, `src/lecture_study/llm/llama_cpp_adapter.py:79`
**Issue:** The retry prompt is the original plus feedback. A prompt near the limit passes on the first call and raises `PromptTooLargeError` on the retry, discarding the original parse error that explains why a retry was needed. Separately, `Stage.PODCAST` is budgeted at 8192 context with a 4096 completion reserve, leaving about 3.9k input tokens; a whole-lecture prompt practically always exceeds this, so the stage can only fail at run time and the preflight cannot warn.
**Fix:** Chain the first error into the message of a retry-time `PromptTooLargeError`, and document or expose the podcast context budget in the example configuration.

### IN-03: Provisioning download hardening

**File:** `scripts/01_provision_engines.sh:157-159`
**Issue:** `--proto '=https'` constrains only the initial URL; `--location` may still follow a redirect to plain HTTP (integrity is protected by the SHA-256 check, confidentiality of the request is not). A failed `curl` under `set -e` leaves the multi-GB `.part` file behind with no cleanup.
**Fix:** Add `--proto-redir '=https'` and a `trap 'rm -f "${shard_path}.part"' ERR` (or remove the partial before re-download).

### IN-04: Codex prompt passed as the last argv element without an option terminator

**File:** `src/lecture_study/llm/codex_adapter.py:104-118`
**Issue:** The prompt follows no `--`. Every current prompt starts with fixed instruction text, so this is not exploitable today, but the port accepts arbitrary strings and a future caller whose prompt starts with `-` would have it parsed as a flag. The 900 KiB budget is macOS-specific; Linux also enforces a 128 KiB per-argument limit, which would surface as a start failure rather than the typed prompt-too-large error. The prompt is also visible in `ps` to other local users (ADR 0046 already lists stdin delivery as a follow-up).
**Fix:** Insert `"--"` before the prompt if the Codex CLI accepts it, and note the per-argument limit in the budget comment.

### IN-05: Stale operator-facing text in the podcast script

**File:** `scripts/generate_codex_podcast.py:372-376`
**Issue:** The argparse description still says "via Codex CLI", while the other five descriptions were updated to "gateway LLM". With the local adapter selected, `--help` is misleading.
**Fix:** Change to "via gateway LLM" like the sibling scripts.

### IN-06: Flashcard deck failure message embeds model output (pre-existing, carried into the new flow)

**File:** `scripts/generate_codex_personal_study_flashcards.py:283-287`
**Issue:** `f"generated flashcard deck failed validation: {error}"` interpolates a pydantic `ValidationError`, whose text includes `input_value` of the model-generated cards. This conflicts with the project's convention (`llm_output_diagnostic`) of never echoing model bytes into messages, and the string goes to stderr and to the persisted phase failure log. It was already present before the phase; the gateway work did not introduce it, but the phase's "no model-output leakage" goal is not met on this path.
**Fix:** Build the message from `error.errors(include_input=False)` locations and types only.

### IN-07: Gateway YAML accepts duplicate keys with last-one-wins

**File:** `src/lecture_study/llm/config.py:109-122`, `config/llm-gateway.example.yaml`
**Issue:** `yaml.safe_load` silently keeps the last of two identical mapping keys, for example a second `remote_llm_allowed_lecture_ids:` or `default_adapter:`. The docstring and example advertise strict validation ("stops the run before any model call") but this class of mistake passes. Combined with the allow-list semantics it can silently widen or narrow egress.
**Fix:** Load with a loader subclass whose mapping constructor raises on duplicate keys.

### IN-08: Codex provenance does not identify the model

**File:** `src/lecture_study/llm/codex_adapter.py:36,55-56`
**Issue:** `describe()` returns the constant `codex-cli`. The run record therefore cannot distinguish Codex model or version changes between runs, which weakens the reproducibility goal of D-13 for the default adapter.
**Fix:** Record the CLI version (a bounded `codex --version` at record time) or document that the Codex model identifier is intentionally opaque.

---

_Reviewed: 2026-10-14_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_

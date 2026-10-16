---
phase: 06-pluggable-llm-gateway
reviewed: 2026-10-14T00:00:00Z
depth: deep
files_reviewed: 8
files_reviewed_list:
  - src/lecture_study/ops/process_lifecycle.py
  - src/lecture_study/llm/config.py
  - src/lecture_study/study_materials/local_lectures.py
  - src/lecture_study/llm/llama_cpp_adapter.py
  - docs/decisions/0046-pluggable-llm-gateway-and-local-adapter.md
  - tests/test_process_lifecycle.py
  - tests/llm_gateway/test_codex_adapter.py
  - tests/llm_gateway/test_llama_cpp_adapter.py
findings:
  critical: 0
  warning: 1
  info: 2
  total: 3
status: issues_found
---

# Phase 6: Round 3 Delta Code Review Report

**Reviewed:** 2026-10-14
**Depth:** deep (scoped to `git diff ea7e4f1..HEAD` over the listed files)
**Files Reviewed:** 8
**Status:** issues_found

## Summary

The Round 3 delta is correct for the cases it targets. No blocker was found.

Verified by reading and by probes on this Darwin host:

1. **Process cleanup (`process_lifecycle.py`).**
   - A probe confirmed the platform model behind the fix. `os.killpg` returns
     success while the group has any live member, even with an unreaped zombie
     leader. It returns `EPERM` only when every member is a zombie. It returns
     `ESRCH` once the leader is reaped. So an `EPERM` from the group kill means
     no live signalable member exists, and the leader fallback is sound.
   - Kill-on-timeout and output-cap hold. The fallback signals the leader
     through `Popen.terminate` or `Popen.kill`. `Popen.send_signal` polls first
     and skips the signal once the child is reaped, so a recycled pid is never
     signalled by the fallback.
   - A real refusal from the leader still propagates.
   - A `ProcessLookupError` from the fallback returns `False`, and the caller
     then waits and sweeps.
   - No caller depends on the old `PermissionError` escape. The callers are:
     `orchestrator`, `ocr_supervisor`, `tts.generator`, `llm.bounded_call`, the
     `scripts/generate_personal_study_guides.py` calls, and the async
     `communicate_bounded_and_reap` users.
2. **YAML loaders.** Both loaders map `ValueError` and `RecursionError` to a
   typed error whose message carries only the exception type name.
   `RegistryRemoteEgressPolicy` converts `LocalLectureError` into
   `GatewayConfigurationError`, so egress is still refused.
3. **Llama scratch mapping.**
   - The context manager propagates body exceptions unchanged.
   - Cleanup runs on every path.
   - Messages are built from the exception type name and use `from None`.
4. **ADR sentence.** It matches `_publish_record` and
   `_preserve_whole_stage_success` in `src/lecture_study/llm/run_record.py`. A
   scoped or failed attempt never overwrites the sibling unless the file it
   displaces is a whole-stage success.

The related suites (`tests/test_process_lifecycle.py`, `tests/llm_gateway`,
`tests/test_local_lectures.py`) pass: 326 passed.

## Warnings

### WR-01: Post-reap sweep swallows EPERM, so a live but unsignalable group member is neither killed nor reported

**File:** `src/lecture_study/ops/process_lifecycle.py:518-522` (also `504-511`)
**Issue:** `_signal_process_group` now ignores `PermissionError`. The assumption
that "EPERM means nothing is left" holds only when every member is a zombie.
`EPERM` is also returned when a live member exists that the caller may not
signal. One example is a descendant that changed uid by running a setuid
binary. In that case:
- the leader is killed by the fallback, and
- the sweep silently returns, so the survivor goes undetected, and
- the survivor still holds the stdout/stderr pipe write ends.

`run_bounded_process` then reaches `_join_reader_threads(reader_threads)` in its
`except BaseException` path. That join is unbounded, so it can block until the
survivor exits. This breaks the timeout and output-cap guarantee for this
narrow case. Before the change the cleanup raised loudly instead. Likelihood is
low, because it needs a descendant that escaped our uid. The trade-off is
undocumented and leaves no diagnostic.
**Fix:** Keep the tolerance, but make the residual visible and keep the join
bounded. For example, log a warning when the sweep is refused while the
existence probe reported the group present:
```python
def _signal_process_group(process_group_id: int, signal_number: signal.Signals) -> None:
    try:
        os.killpg(process_group_id, signal_number)
    except ProcessLookupError:
        pass
    except PermissionError:
        _logger.warning(
            "process group %d refused signal %s; members may be unsignalable",
            process_group_id,
            signal_number.name,
        )
```
Also bound `_join_reader_threads` in the cleanup path with a timeout, for
example the termination grace period, instead of an unbounded `join()`.

## Info

### IN-01: EPERM is treated as "present" by one helper and "nothing left" by the next

**File:** `src/lecture_study/ops/process_lifecycle.py:509-511` and `518-522`
**Issue:** `_process_group_exists` returns `True` on `PermissionError`. The
sweep it guards then swallows the same `PermissionError`. The two helpers
disagree on what `EPERM` means, and the new docstring ("EPERM means nothing is
left") contradicts the existence probe. The combination works, but only because
the second helper discards the result of the first.
**Fix:** Pick one meaning. For example, treat `EPERM` from the probe as "no
live member" and return `False`, then keep the sweep strict. Or drop the probe
and call the swallowing sweep directly. Document the chosen meaning once.

### IN-02: New cleanup tests simulate EPERM for every `killpg` call and never exercise a real zombie leader

**File:** `tests/test_process_lifecycle.py:163-171, 513-550`; `tests/llm_gateway/test_codex_adapter.py:118-133`
**Issue:** All three regression tests replace `os.killpg` with a function that
always raises, and the process-lifecycle tests use a fake pid (`4_242_424`).
They prove that the fallback and the swallowing exist. They do not prove the
guarantee the fix claims, which is that a live descendant is still killed when
the leader is a zombie. The existing real-process descendant test
(`test_sync_output_limit_remains_active_after_direct_child_exit`) does not
force the `EPERM` path.
**Fix:** Add one POSIX-only test that spawns `sh -c 'sleep 30 & echo $!; exit 0'`
with `start_new_session=True` and waits for the leader to become a zombie. It
then runs the cleanup helper and asserts the `sleep` pid is gone. Add a second
case with a zombie-only group, where a real `EPERM` occurs, and assert that no
exception escapes.

---

_Reviewed: 2026-10-14_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_

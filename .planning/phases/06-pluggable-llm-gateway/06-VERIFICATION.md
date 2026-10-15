---
phase: 06-pluggable-llm-gateway
verified: 2026-10-15T20:00:00Z
status: passed
score: 4/4 roadmap success criteria verified; 6/6 requirements satisfied (GW-01..GW-06)
covered_files:
  - .planning/phases/06-pluggable-llm-gateway/06-01-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-01-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-02-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-02-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-03-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-03-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-04-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-04-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-05-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-05-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-06-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-06-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-07-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-07-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-08-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-08-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-09-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-09-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-10-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-10-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-11-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-11-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-12-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-12-SUMMARY.md
  - config/llm-gateway.example.yaml
  - docs/decisions/0046-pluggable-llm-gateway-and-local-adapter.md
  - scripts/01_provision_engines.sh
  - scripts/generate_codex_critic_review.py
  - scripts/generate_codex_drafts.py
  - scripts/generate_codex_mock_exam.py
  - scripts/generate_codex_personal_study_flashcards.py
  - scripts/generate_codex_podcast.py
  - scripts/generate_codex_semantic_ledgers.py
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
  - src/lecture_study/ops/process_lifecycle.py
  - src/lecture_study/ops/strict_yaml.py
  - src/lecture_study/study_materials/local_lectures.py
  - src/lecture_study/study_materials/remote_llm_policy.py
covered_digest: "v3:sha256:b7ef42a55dded914bc33809191d0dcd387ca9f2c09bb6576358f0f38f0d871ec"
behavior_unverified: 0
overrides_applied: 0
re_verification:
  previous_status: human_needed
  previous_score: 4/4 roadmap success criteria (GW-03 engine run pending)
  gaps_closed:
    - "GW-03 engine run: ADR 0046 decision 10 record filled (2026-10-15) and UAT test 39 ran the real llama-completion/llama-tokenize at the pinned commit"
    - "CI on the pushed phase-final commit: run 38789108169 succeeded on b2e5f06 (origin/main)"
    - "R0.4 coverage of the Round 3 delta: operator recorded as acceptor in UAT test 45 (see advisory note)"
  gaps_remaining: []
  regressions: []
advisory:
  - finding: "The strict-YAML corrective delta (c84f0ad, 3eb1d60: ops/strict_yaml.py rewrite, local_lectures.py, llm/config.py) landed after 06-REVIEW.md, 06-REVIEW-FIX.md Round 4 and 06-SECURITY.md. Its review and security rounds are documented only in the c84f0ad commit message, not in a phase artifact. UAT test 45 passes on operator acceptance, but the cited AR-06-01 and AR-06-02 concern a registration-rerun flip and the absence of package installs, not this delta."
    category: other
    reason: "Global rule R0.4 expects the review and secure runs on the phase-final diff to be evidenced. The delta is covered by 2698 passing tests (tests/test_strict_yaml.py 267 lines, extended egress and local-lecture tests), ruff and mypy clean, and fails closed. Adding a one-paragraph review note to 06-REVIEW-FIX.md (or a Round 5 entry) would close the evidence gap."
    evidence_status: "commit message only; operator acceptance recorded in UAT test 45"
---

# Phase 6: Pluggable LLM gateway Verification Report

**Phase Goal:** Every LLM-backed stage (5 semantic ledgers, 6 drafts, 7 critic, 13 exam, 14 podcast, 18 personal-study flashcards) calls one `LlmGateway` port; a `CodexCliAdapter` keeps current behavior and a `LlamaCppAdapter` runs a local model with no network egress; the adapter is chosen by configuration.
**Verified:** 2026-10-15
**Status:** passed
**Re-verification:** Yes -- the previous report (2026-10-14, `human_needed`) is replaced. The three human items it carried are resolved by 06-UAT.md (46/46 pass) plus the evidence re-checked below.

## Goal Achievement

### Observable Truths (ROADMAP success criteria, the contract)

| # | Truth | Status | Evidence |
| - | ----- | ------ | -------- |
| 1 | Switching the configuration from codex to llama-cpp runs stages 5, 6 and 7 on a lecture with no Codex invocation | VERIFIED | `tests/llm_gateway/test_stages_without_codex.py` drives stages 5, 6, 7 through the real scripts as child processes with scripted `llama-completion`/`llama-tokenize` stubs and a tripwire `codex` first on `PATH`; two cases pass (YAML `default_adapter: llama-cpp`; env override over a codex YAML); run records report only `llama-cpp`; tripwire calls are empty. Engine-level evidence: ADR 0046 decision 10 record (2026-10-15) shows stages 5, 6, 7 exit 0, status `succeeded`, adapter `llama-cpp`, no Codex process, at the pinned commit `91d2fc3`; UAT test 39 ran the real binary (4.2 s, `--offline` in argv). Re-checked locally: `engines/llama.cpp/build/bin/{llama-completion,llama-tokenize,llama-mtmd-cli}` exist and `.built-commit` equals `LLAMA_REPO_COMMIT` in `scripts/01_provision_engines.sh`. |
| 2 | An unknown adapter name exits non-zero before any model call | VERIFIED | `tests/llm_gateway/test_preflight.py` (cli main, `LECTURE_STUDY_LLM_ADAPTER=bogus`: `SystemExit(1)`, zero phase runs, zero spawns), second layer in `test_semantic_stage_gateway.py` and `test_configuration.py`. Orchestrator calls `_preflight_llm_gateways` in `run_all`, `run_phase`, `run_personal_study`. Part of the 257 gateway/layering tests that pass in this run. |
| 3 | The contract tests pass for both adapters in CI | VERIFIED | `tests/llm_gateway/test_gateway_contract.py` is parametrized for `codex` and `llama_cpp` over real stub processes; `test_critic_repair_contract.py` runs the ADR 0036 loop through both. Run in this verification: `uv run pytest tests/llm_gateway tests/test_llm_layering.py` = 257 passed; full suite = 2698 passed, 1 pre-existing warning. CI evidence: `gh run list` shows `ci` run 38789108169 (push, main) completed with success on `b2e5f06`; the later commit `b8803eb` (UAT) is documentation only. |
| 4 | The new ADR is Accepted, and ADR 0038 and ADR 0045 carry amendment notes pointing to it | VERIFIED | `docs/decisions/0046-...md` line 5 `Accepted. 2026-10-14.`; ADR 0038 `Amended by ADR 0046` (line 7); ADR 0045 `Section 2 closed by ADR 0046` (line 12) plus in-body notes (lines 128, 215). |

**Score:** 4/4 truths verified; 0 behavior-unverified.

### Requirement-level truths (PLAN frontmatter and CONTEXT, merged; no scope reduction)

| Requirement | Plans claiming it | Status | Evidence |
| ----------- | ----------------- | ------ | -------- |
| GW-01 port; six stages call it | 06-01, 06-03, 06-05, 06-06, 06-07, 06-08, 06-09 | SATISFIED | All six `scripts/generate_codex_*.py` reference `open_stage_gateway`/`complete_json` (rg counts 4-5 each) and none references `subprocess`, `run_bounded_process` or `tempfile` (rg count empty). `tests/test_llm_layering.py` (AST) enforces the boundary; UAT tests 18, 22, 27, 31, 36. |
| GW-02 Codex adapter preserves behavior | 06-01, 06-05, 06-08, 06-09 | SATISFIED | `codex_adapter.py` (184 lines): argv asserted against a stub in `test_codex_adapter.py`; scratch cwd, 900 s / 16 MiB defaults, typed errors, argv-size guard. Orchestrator keeps failure capture under `tmp/codex_phase_failures` and the sanitized child environment (UAT test 35). |
| GW-03 LlamaCppAdapter, no egress | 06-02, 06-10, 06-11 | SATISFIED | `llama_cpp_adapter.py` (332 lines) uses `llama-completion` and `llama-tokenize` (rg finds no `llama-cli` in the adapter), flags `--offline --no-escape --single-turn --reasoning off` present, env `LLAMA_ARG_OFFLINE=1`, local-file model path only. Engine confirmed at the pinned commit by the filled decision 10 record and UAT test 39: all first-attempt flags accepted, `--single-turn` chat template worked, no `HOME` needed, split model first shard loaded, `Total number of tokens: N` parsed. Residual (recorded in ADR, not a gap): retry-only flags `--repeat-penalty`/`--repeat-last-n` were not exercised on the real engine, and the optional thinking-style model row was not run. |
| GW-04 configuration selection, fail closed | 06-03, 06-04, 06-05, 06-07, 06-09 | SATISFIED | `llm/config.py` plus `ops/strict_yaml.py` strict loader; precedence env > stage > default > built-in codex; double validation (preflight and per script). |
| GW-05 ADR amends 0038, closes 0045 section 2 | 06-04, 06-11, 06-12 | SATISFIED | See truth 4; egress gate in `remote_llm_policy.py` and `egress.py`, `require_remote_egress_allowed` before the Codex executable is resolved. REQUIREMENTS.md GW-05 carries the clarification. |
| GW-06 same contract tests, stub binary in CI | 06-01, 06-02, 06-06, 06-09 | SATISFIED | See truth 3. |

**Requirement ID cross-reference (PLAN frontmatter vs REQUIREMENTS.md):** every ID declared across the 12 plans (GW-01, GW-02, GW-03, GW-04, GW-05, GW-06) exists in REQUIREMENTS.md and in the Phase 6 traceability rows; no orphaned requirement (REQUIREMENTS.md maps no other ID to Phase 6).

### Required Artifacts

| Artifact | Status | Details |
| -------- | ------ | ------- |
| `src/lecture_study/llm/` (10 modules, 1509 lines) | VERIFIED | Substantive and wired through `cli/llm_gateway_wiring.py` into the six scripts and the orchestrator. |
| `src/lecture_study/ops/strict_yaml.py`, `study_materials/remote_llm_policy.py`, `local_lectures.py` | VERIFIED | Strict loader shared by registry and gateway config; 267-line `tests/test_strict_yaml.py` and extended policy tests pass. |
| `config/llm-gateway.example.yaml` | VERIFIED | Present, loaded by `test_configuration.py`. |
| `scripts/01_provision_engines.sh` | VERIFIED | Pins `91d2fc3`; UAT 40 and 41 passed; provisioning tests in the green full suite. |
| `docs/decisions/0046-...md` | VERIFIED | Accepted; decision 10 record table filled with real-run values; follow-ups (a)-(e) all marked not needed. |
| `tests/llm_gateway/` (13 modules + conftest) | VERIFIED | All pass. |

### Key Link Verification

| From | To | Via | Status |
| ---- | -- | --- | ------ |
| six scripts | `LlmGateway` | `open_stage_gateway` then `complete_json` | WIRED |
| orchestrator | gateway validation | `_preflight_llm_gateways` before resume predicates | WIRED |
| orchestrator | child scripts | exactly `LECTURE_STUDY_LLM_ADAPTER` and `LECTURE_STUDY_LLM_CONFIG` added to the allowlist (UAT 35) | WIRED |
| factory | egress policy | `require_remote_egress_allowed` before `resolve_trusted_executable` on the codex branch | WIRED |
| scripts | run record | `workspace/data/llm_runs/<lecture>/<stage>.json` | WIRED |
| `llm/` | `site/` | none (layering test) | CORRECTLY ABSENT |

### Data-Flow Trace (Level 4)

End-to-end stub tests carry real data from evidence pack through prompt, stubbed binary stdout, `complete_json` parse and validation, to ledger/draft/unit and run record. The real-engine run (decision 10) shows the same chain with a real model. No static or hollow source found.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| Gateway and layering tests | `uv run pytest tests/llm_gateway tests/test_llm_layering.py -q -p no:cacheprovider` | 257 passed | PASS |
| Full suite | `uv run pytest -q -p no:cacheprovider` | 2698 passed, 1 warning | PASS |
| Lint, format, types | `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` | all clean; 236 files formatted; 126 source files | PASS |
| Real engine present at the pin | `ls engines/llama.cpp/build/bin`, `.built-commit` | binaries present; commit equals pin | PASS |
| CI on phase-final commit | `gh run list` | run 38789108169 success on `b2e5f06` | PASS |

### Probe Execution

No `scripts/*/tests/probe-*.sh` and no probe declared in any PLAN: not applicable.

### Anti-Patterns Found

None. `rg "TBD|FIXME|XXX"` over `src/lecture_study/llm`, the wiring module, `strict_yaml.py`, `local_lectures.py`, `remote_llm_policy.py`, the six scripts, the provisioning script and the example YAML returned no match.

### Human Verification (resolved)

The three items of the previous report are resolved and no human item remains:

1. D-07 manual real-model run: recorded in ADR 0046 decision 10 (2026-10-15) and UAT tests 39 and 42; follow-ups (a)-(d) not needed, (e) not needed for first-attempt flags.
2. R0.4 on the Round 3 delta: scoped `06-REVIEW-ROUND3.md` exists; the operator marked UAT test 45 pass. See the advisory note in the frontmatter (not blocking).
3. CI on the pushed commit: green (run 38789108169).

### Advisory (non-blocking)

| # | Finding | Why advisory |
| - | ------- | ------------ |
| 1 | Review and security evidence for the strict-YAML delta (`c84f0ad`, `3eb1d60`) lives only in a commit message; UAT test 45 cites AR-06-01/AR-06-02, which do not address it. | Delta is test-covered (2698 passed), static checks clean, behavior fails closed; operator accepted in UAT. Recommend a short Round 5 note in `06-REVIEW-FIX.md`. |
| 2 | Retry-only llama flags and the thinking-style `--reasoning off` row were not exercised on the real engine (OPEN-1 / D-25 adequacy unvalidated). | Recorded honestly in ADR 0046; the row is marked optional. |

### Documentation drift for the orchestrator to reconcile (not goal-affecting)

- `.planning/REQUIREMENTS.md` still shows `- [ ] **GW-03**` (line 107) and traceability `GW-03 | Phase 6 | Pending` (line 253). The evidence above satisfies GW-03; both should be flipped to Complete at phase close.
- `.planning/ROADMAP.md` Phase 6 checkbox (line 22) is unchecked and the section header still says "open"; update at phase completion.
- `main` is one commit ahead of `origin/main` (`b8803eb`, documentation only).

### Gaps Summary

No gaps. All four roadmap success criteria and all six requirements hold in the code, backed by passing behavioral tests (stub processes), a real-binary run at the pinned llama.cpp commit, an Accepted ADR with its amendment chain, and a green CI run on the phase-final code.

---

_Verified: 2026-10-15_
_Verifier: Claude (gsd-verifier)_

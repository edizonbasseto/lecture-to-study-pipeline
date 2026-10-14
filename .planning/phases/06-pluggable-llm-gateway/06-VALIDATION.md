---
phase: "6"
slug: "pluggable-llm-gateway"
# status lifecycle: draft (seeded by plan-phase) → validated (set by validate-phase §6)
# audit-milestone §5.5 distinguishes NOT-VALIDATED (draft) from PARTIAL (validated + nyquist_compliant: false) (#2117)
status: draft
nyquist_compliant: false
wave_0_complete: false
created: "2026-10-14"
---

# Phase 6 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest >= 9.1.1 with pytest-asyncio; mypy strict on `src`; ruff check and format |
| **Config file** | `pyproject.toml` (`testpaths = ["tests"]`) |
| **Quick run command** | `uv run pytest tests/llm_gateway -q -x` |
| **Full suite command** | `uv run pytest -q -rs && uv run ruff check . && uv run ruff format --check . && uv run mypy src` |
| **Estimated runtime** | ~60 seconds (baseline suite 38 s plus the new gateway tests) |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/llm_gateway -q -x`
- **After every plan wave:** Run `uv run pytest -q -rs && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
- **Before `/gsd-verify-work`:** Full suite must be green on the phase-final diff, then `/gsd-code-review` and `/gsd-secure-phase` (operator rule R0.4)
- **Max feedback latency:** 60 seconds

---

## Per-Task Verification Map

Task IDs are `06-PLAN-TASK` (for example `06-01-02` is plan 06-01, task 2). Threat refs point to each plan's `<threat_model>` register.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 06-01-01 | 06-01 | 1 | GW-02 | T-06-01, T-06-03 | Codex argv byte-identical to the six scripts; scratch cwd never the repo root; failure is `LlmCallError` without prompt and with the cause suppressed | adapter (real stub process) | `uv run pytest tests/llm_gateway/test_codex_adapter.py -q` | ❌ W0 (created by this task) | ⬜ pending |
| 06-01-02 | 06-01 | 1 | GW-02 | T-06-02, T-06-04 | Timeout, output cap, spawn failure typed; argv budget raises `PromptTooLargeError` before spawn; HOME present, credentials absent | adapter | `uv run pytest tests/llm_gateway/test_codex_adapter.py -q` | ❌ W0 | ⬜ pending |
| 06-01-03 | 06-01 | 1 | GW-01, GW-06 | T-06-05 | `complete_json` single bounded retry, gateway errors never retried; codex contract suite; `site/` and `ops/` never import `llm/` | unit + contract + AST | `uv run pytest tests/llm_gateway tests/test_llm_layering.py -q` | ❌ W0 | ⬜ pending |
| 06-02-01 | 06-02 | 2 | GW-03 | T-06-08, T-06-11 | `llama-completion` (never `llama-cli`) with the D-17 flag set; prompt via file; tokenize before completion | adapter | `uv run pytest tests/llm_gateway/test_llama_cpp_adapter.py tests/llm_gateway/test_codex_adapter.py -q` | ❌ W0 | ⬜ pending |
| 06-02-02 | 06-02 | 2 | GW-03 | T-06-06, T-06-07, T-06-09, T-06-10 | No download, listener or URL argv; env only sanitized plus `LLAMA_ARG_OFFLINE=1`; model path validated; retry-only repeat penalty; oversized prompt refused before completion spawns | adapter + tripwire stub | `uv run pytest tests/llm_gateway/test_llama_cpp_adapter.py -q` | ❌ W0 | ⬜ pending |
| 06-02-03 | 06-02 | 2 | GW-06 | — | Same contract suite passes for `codex` and `llama_cpp` (criterion 3) | contract | `uv run pytest tests/llm_gateway -q -rs` | ❌ W0 | ⬜ pending |
| 06-03-01 | 06-03 | 2 | GW-04 | T-06-12, T-06-16 | Default codex without a file; per-stage overrides; env override clears per-stage entries; malformed input fails closed | unit | `uv run pytest tests/llm_gateway/test_configuration.py -q` | ❌ W0 | ⬜ pending |
| 06-03-02 | 06-03 | 2 | GW-01 | T-06-13, T-06-14, T-06-15 | Run record metadata-only, atomic, under `workspace/data/llm_runs/`; outside dashboard stage directories | unit | `uv run pytest tests/llm_gateway/test_run_record.py -q` | ❌ W0 | ⬜ pending |
| 06-04-01 | 06-04 | 2 | GW-04, GW-05 | T-06-17, T-06-18 | Codex refused for unflagged local or unlisted portal lectures; unreadable registry refuses | unit | `uv run pytest tests/llm_gateway/test_egress_policy.py tests/test_local_lectures.py -q` | ❌ W0 | ⬜ pending |
| 06-04-02 | 06-04 | 2 | GW-04 | T-06-19 | OPEN-2 operator decision (checkpoint) | decision | n/a (checkpoint:decision) | n/a | ⬜ pending |
| 06-04-03 | 06-04 | 2 | GW-04 | T-06-19, T-06-20 | OPEN-2 rule implemented; registry flag round trip and rerun toggle | unit | `uv run pytest tests/llm_gateway/test_egress_policy.py tests/test_local_lectures.py -q` | ❌ W0 | ⬜ pending |
| 06-05-01 | 06-05 | 3 | GW-01, GW-04 | T-06-21 | Stage 5 end to end through config, policy, factory, adapter, sidecar; unflagged lecture refused before any spawn | integration (stub process) | `uv run pytest tests/llm_gateway/test_semantic_stage_gateway.py -q` | ❌ W0 | ⬜ pending |
| 06-05-02 | 06-05 | 3 | GW-04 | T-06-22, T-06-23 | Factory fail-closed paths; resolver never called for refused lectures; script exits 1 on bogus adapter (D-12 second layer) | unit + integration | `uv run pytest tests/llm_gateway/test_factory.py tests/llm_gateway/test_semantic_stage_gateway.py -q` | ❌ W0 | ⬜ pending |
| 06-05-03 | 06-05 | 3 | GW-01 | T-06-24 | Semantic unit tests on the gateway; traceability retried; source boundary pinned | unit + AST | `uv run pytest tests/test_generate_codex_semantic_ledgers.py tests/llm_gateway -q` | rewrite | ⬜ pending |
| 06-06-01 | 06-06 | 4 | GW-01 | — | Stage 6 on the gateway with depth report and run record | unit | `uv run pytest tests/test_generate_codex_drafts.py -q` | rewrite | ⬜ pending |
| 06-06-02 | 06-06 | 4 | GW-01 | T-06-25, T-06-26 | Critic reviews via critic gateway, redrafts via drafting gateway (D-21); ADR 0036 loop unchanged | unit | `uv run pytest tests/test_generate_codex_critic_review.py tests/test_generate_codex_drafts.py -q` | rewrite | ⬜ pending |
| 06-06-03 | 06-06 | 4 | GW-06 | T-06-27 | Critic repair loop through both adapters; no repeat penalty on feedback redrafts | contract (stub processes) | `uv run pytest tests/llm_gateway/test_critic_repair_contract.py -q -rs` | ❌ W0 | ⬜ pending |
| 06-07-01 | 06-07 | 4 | GW-01 | T-06-29, T-06-30 | Stage 13 on the gateway; blocked lecture builds no gateway and writes nothing | unit | `uv run pytest tests/test_generate_codex_mock_exam.py -q` | rewrite | ⬜ pending |
| 06-07-02 | 06-07 | 4 | GW-01, GW-04 | T-06-28 | Stage 18 on the gateway; validates its own configuration (not orchestrated) | unit | `uv run pytest tests/test_generate_codex_personal_study_flashcards.py tests/test_generate_codex_mock_exam.py -q` | rewrite | ⬜ pending |
| 06-08-01 | 06-08 | 4 | GW-01 | T-06-33 | Stage 14 on the gateway; echo strip, normalization, traceability, listener guard kept | unit | `uv run pytest tests/test_generate_codex_podcast.py -q` | rewrite | ⬜ pending |
| 06-08-02 | 06-08 | 4 | GW-02 | T-06-31, T-06-32 | Process failure not retried (D-22); oversized prompt exits 1; timeout override | unit | `uv run pytest tests/test_generate_codex_podcast.py -q` | rewrite | ⬜ pending |
| 06-09-01 | 06-09 | 5 | GW-04, GW-06 | T-06-35 | Criterion 1: stages 5-7 through the orchestrator on llama-cpp (YAML and env) with a tripwire codex never called | integration (child processes + stubs) | `uv run pytest tests/llm_gateway/test_stages_without_codex.py -q -rs` | ❌ W0 | ⬜ pending |
| 06-09-02 | 06-09 | 5 | GW-04, GW-02, GW-01 | T-06-34, T-06-36, T-06-37 | Criterion 2 via `cli_main.main`; child env adds exactly two names; six-script boundary | unit + integration + AST | `uv run pytest tests/llm_gateway/test_preflight.py tests/test_cli.py tests/test_llm_layering.py tests/test_personal_study_mode.py -q` | ❌ W0 + edit | ⬜ pending |
| 06-10-01 | 06-10 | 1 | GW-03 | — | Provisioning refuses unknown args before side effects; gateway binaries verified after build | script test | `bash -n scripts/01_provision_engines.sh && shellcheck scripts/01_provision_engines.sh && uv run pytest tests/test_provision_engines.py -q` | ❌ W0 | ⬜ pending |
| 06-10-02 | 06-10 | 1 | GW-03 | T-06-38, T-06-39, T-06-40 | Opt-in example model, pinned revision, per-shard SHA-256, HTTPS only | script test | `bash -n scripts/01_provision_engines.sh && shellcheck scripts/01_provision_engines.sh && uv run pytest tests/test_provision_engines.py -q` | ❌ W0 | ⬜ pending |
| 06-11-01 | 06-11 | 6 | GW-05, GW-03 | T-06-41, T-06-42, T-06-43 | Criterion 4: ADR 0046 Accepted; ADR 0038 and 0045 point to it; GEN-07 amended, GW-03 reworded | doc check | `rg -n "^Accepted" docs/decisions/0046-pluggable-llm-gateway-and-local-adapter.md && rg -l "ADR 0046" docs/decisions/0038-*.md docs/decisions/0045-*.md && rg -n "Amended by ADR 0046" .planning/REQUIREMENTS.md` | ❌ W0 | ⬜ pending |
| 06-11-02 | 06-11 | 6 | GW-03 | — | OPEN-1 operator decision (checkpoint) | decision | n/a (checkpoint:decision) | n/a | ⬜ pending |
| 06-11-03 | 06-11 | 6 | GW-03 | — | D-07 manual run record defined in ADR 0046; run executed by the operator | doc check + manual | `rg -n "Manual real-model run" docs/decisions/0046-pluggable-llm-gateway-and-local-adapter.md` | ❌ W0 | ⬜ pending |
| 06-12-01 | 06-12 | 6 | GW-05 | T-06-44 | ADRs 0020, 0022, 0034 point to ADR 0046 | doc check | `rg -l "ADR 0046" docs/decisions/0020-*.md docs/decisions/0022-*.md docs/decisions/0034-*.md` | exists (edit) | ⬜ pending |
| 06-12-02 | 06-12 | 6 | GW-05 | T-06-44, T-06-45 | ADRs 0035, 0036, 0037 and PROJECT.md decisions point to ADR 0046 | doc check | `rg -l "ADR 0046" docs/decisions/0035-*.md docs/decisions/0036-*.md docs/decisions/0037-*.md .planning/PROJECT.md` | exists (edit) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Each Wave 0 item is created by the task that first needs it (tracer-first), so no separate scaffolding wave exists:

- [ ] `tests/llm_gateway/conftest.py` — stub `codex` (06-01-01), `llama-completion` and `llama-tokenize` (06-02-01), tripwire mode (06-02-02), rules mode and tripwire `codex` on PATH (06-09-01); separate regular files per name because `resolve_trusted_executable` resolves symlinks
- [ ] `tests/llm_gateway/test_*.py` — files listed in the map above, unique basenames across `tests/`
- [ ] `tests/conftest.py` `ScriptedGateway` double (06-05-03) used by the rewritten script tests
- [ ] Rewrite of the six `tests/test_generate_codex_*.py` private-name tests to the gateway shape (06-05-03, 06-06-01, 06-06-02, 06-07-01, 06-07-02, 06-08-01, 06-08-02)
- [ ] `tests/test_cli.py` — env-equality and preflight assertions updated (06-09-02)
- [ ] Manual-run record for D-07 in ADR 0046 decision 10 (06-11-03): llama.cpp commit, model SHA-256 values, command lines, outcome; no quality numbers

## Operator Decisions Pending (checkpoints)

| ID | Question | Where asked | Planned default |
|----|----------|-------------|-----------------|
| OPEN-1 | Does the unconditional `--reasoning off` of D-17 satisfy "reasoning explicitly off for Qwen3-style models", or must the D-07 run also exercise a thinking-capable model? | 06-11 Task 2 | None; the plan waits for the answer (no code change either way) |
| OPEN-2 | An id both registered locally and listed in `remote_llm_allowed_lecture_ids`: configuration error, or registry authoritative with a warning? | 06-04 Task 2 | None; the plan waits for the answer (egress stays fail-closed either way) |

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| One real-model run of stages 5, 6 and 7 on a single evidence pack with the example model (06-11-03 human-check) | GW-03 | No `llama-*` binary or GGUF model is available in CI; the stub proves the contract, not the engine | Follow ADR 0046 decision 10: `scripts/01_provision_engines.sh --with-example-llm-model`, copy the example gateway YAML to the local copy, run the three scripts with `LECTURE_STUDY_LLM_ADAPTER=llama-cpp` (semantic with `--pack-sequence 1`), fill the record table; note whether `--single-turn` needs the `-no-cnv` fallback (D-17), whether HOME was needed (A3), whether the first shard loaded the split model (A9); add the Qwen3-style row if OPEN-1 is answered option-b |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending

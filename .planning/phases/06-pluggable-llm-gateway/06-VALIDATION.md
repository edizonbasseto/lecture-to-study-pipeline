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

Requirement-level map seeded from RESEARCH.md; the planner replaces the Task ID column with concrete task IDs.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 6-XX-XX | TBD | TBD | GW-01 | — | Scripts reach the model only through `LlmGateway`; `site/` and `ops/` never import `llm/` | unit + AST scan | `uv run pytest tests/test_generate_codex_*.py tests/test_llm_layering.py -q` | ❌ W0 | ⬜ pending |
| 6-XX-XX | TBD | TBD | GW-02 | — | Codex argv, scratch cwd, sanitized env, bounded limits, typed errors preserved; argv-size guard before spawn | adapter contract | `uv run pytest tests/llm_gateway/test_codex_adapter.py -q` | ❌ W0 | ⬜ pending |
| 6-XX-XX | TBD | TBD | GW-03 | — | Llama argv carries `--offline --no-escape --no-display-prompt -n`, no download flag; model path validated; oversized prompt raises before `llama-completion` spawns | adapter | `uv run pytest tests/llm_gateway/test_llama_cpp_adapter.py -q` | ❌ W0 | ⬜ pending |
| 6-XX-XX | TBD | TBD | GW-04 | — | Unknown or misconfigured adapter exits non-zero before phase 2 and before any spawn; env override wins over per-stage entries | unit + integration | `uv run pytest tests/llm_gateway/test_preflight.py tests/llm_gateway/test_configuration.py tests/test_cli.py -q` | ❌ W0 | ⬜ pending |
| 6-XX-XX | TBD | TBD | GW-04 | — | Codex refused for any lecture without `remote_llm_allowed` (registry, portal id list, unreadable registry) before any call | unit | `uv run pytest tests/llm_gateway/test_egress_policy.py tests/test_local_lectures.py -q` | ❌ W0 | ⬜ pending |
| 6-XX-XX | TBD | TBD | GW-05 | — | ADR 0046 Accepted; ADR 0038 and ADR 0045 carry pointers; GEN-07 amended | doc check | `rg -n "^Accepted" docs/decisions/0046-*.md && rg -l "ADR 0046" docs/decisions/0038-*.md docs/decisions/0045-*.md` | ❌ W0 | ⬜ pending |
| 6-XX-XX | TBD | TBD | GW-06 | — | One parametrized suite passes for both adapters, including the critic repair loop; stages 5-7 run on the llama-cpp adapter with a tripwire `codex` never called | contract + integration | `uv run pytest tests/llm_gateway/test_gateway_contract.py tests/llm_gateway/test_stages_without_codex.py -q` | ❌ W0 | ⬜ pending |
| 6-XX-XX | TBD | TBD | GW-01 | — | Sidecar run record written atomically under `workspace/data/llm_runs/`; dashboard counts unchanged | unit | `uv run pytest tests/llm_gateway/test_run_record.py -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/llm_gateway/conftest.py` — stub `codex`, `llama-completion` and `llama-tokenize` builders, scripted-response and call-log helpers, tripwire `codex` (separate files, because `resolve_trusted_executable` resolves symlinks)
- [ ] `tests/llm_gateway/test_*.py` — files listed in the map above, unique basenames
- [ ] Rewrite of the six `tests/test_generate_codex_*.py` private-name tests to the gateway shape
- [ ] `tests/test_cli.py` — env-equality and preflight assertions updated
- [ ] Manual-run record template for D-07 (binary build hash, model SHA-256, command lines, outcome; no quality numbers)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| One real-model run of stages 5, 6 and 7 on a single evidence pack with the example model | GW-03 | No `llama-*` binary or GGUF model is available in CI; the stub proves the contract, not the engine | Provision llama.cpp and the example model, select the llama-cpp adapter, run stages 5-7 on one pack, fill the manual-run record template; also decide whether `--single-turn` needs the `-no-cnv` fallback (D-17) |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending

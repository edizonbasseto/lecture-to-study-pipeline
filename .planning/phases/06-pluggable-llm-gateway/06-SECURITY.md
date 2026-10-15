---
phase: "6"
slug: "pluggable-llm-gateway"
status: verified
# threats_open = count of OPEN threats at or above workflow.security_block_on severity (the blocking gate)
threats_open: 0
asvs_level: 1
created: "2026-10-14"
---

# Phase 6 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| Repository process to Codex CLI | Prompt built from lecture evidence is handed to a bounded `codex exec` child that talks to a remote model | Lecture text (may carry patient-identifying data), model output |
| Repository process to llama.cpp | Prompt handed to a local `llama-completion` child; no network by construction | Lecture text, model output |
| Operator configuration to gateway | `config/llm-gateway.local.yaml`, `LECTURE_STUDY_LLM_ADAPTER`, `LECTURE_STUDY_LLM_CONFIG` select the adapter | Adapter names, executable and model paths |
| Lecture registry to egress policy | `remote_llm_allowed` per local lecture and `remote_llm_allowed_lecture_ids` for portal lectures decide whether Codex may be used | Per-lecture consent flag |
| Orchestrator to child scripts | Allowlisted environment (ten names) crosses into `scripts/generate_codex_*.py` | Environment variables |
| Run record to disk | Metadata-only record under `workspace/data/llm_runs/` | Adapter, version, model digest, schema name, status |
| Provisioning script to Hugging Face | Opt-in download of the example model | Model weights (integrity pinned by SHA-256) |

---

## Threat Register

All 47 register entries (T-06-01..T-06-45 and T-06-SC, which appears with two dispositions) were verified against the implementation at the phase-final state.

| Threat ID | Category | Component | Severity | Disposition | Mitigation | Status |
|-----------|----------|-----------|----------|-------------|------------|--------|
| T-06-01 | Information disclosure | Codex adapter errors | high | mitigate | Failures mapped to `LlmCallError ... from None` with exit code or digest only (`llm/bounded_call.py`, `llm/codex_adapter.py`) | closed |
| T-06-02 | Information disclosure | Codex child environment | high | mitigate | Sanitized allowlist plus HOME, blocklist re-applied (`llm/codex_adapter.py`, `ops/process_lifecycle.py`) | closed |
| T-06-03 | Elevation of privilege | Codex child | high | mitigate | `--sandbox read-only`, scratch cwd (`llm/codex_adapter.py`) | closed |
| T-06-04 | Denial of service | Codex child | medium | mitigate | argv budget guard before spawn, timeout and output caps | closed |
| T-06-05 | Tampering | Retry feedback | medium | mitigate | One retry, fixed trusted feedback, 2 KiB cap (`llm/json_completion.py`) | closed |
| T-06-06 | Information disclosure | Llama adapter network | high | mitigate | `--offline`, `LLAMA_ARG_OFFLINE=1`, `HF_*` and other `LLAMA_ARG_*` stripped, no download flags | closed |
| T-06-07 | Tampering | Llama model path | medium | mitigate | URL rejected, strict resolve, regular file, absolute path | closed |
| T-06-08 | Tampering | Llama prompt | medium | mitigate | `--no-escape` on tokenize and completion | closed |
| T-06-09 | Denial of service | Llama child | medium | mitigate | Per-stage `-n` cap, bounded process | closed |
| T-06-10 | Tampering | Llama prompt size | medium | mitigate | Exact token count, `PromptTooLargeError`, no truncation | closed |
| T-06-11 | Elevation of privilege | Llama listener | medium | mitigate | `llama-completion` only, no `--port` or `--host` | closed |
| T-06-12 | Tampering | Gateway configuration | medium | mitigate | `safe_load`, strict models, repeated-key rejection, `lstat` probe, typed errors (ValueError and RecursionError mapped in Round 3) | closed |
| T-06-13 | Information disclosure | Run record | high | mitigate | Metadata-only model with `extra="forbid"`, counter-only recorder | closed |
| T-06-14 | Tampering | Run record path | high | mitigate | `SafeIdentifier` before path build, file name from `Stage` enum | closed |
| T-06-15 | Tampering | Run record write | low | mitigate | Unique temp file, fsync, `os.replace` | closed |
| T-06-16 | Elevation of privilege | Adapter selection | medium | mitigate | Unknown adapter and missing section fail closed, env override clears per-stage entries | closed |
| T-06-17 | Information disclosure | Egress gate | critical | mitigate | Per-lecture flag, default refused; repeated ids and repeated YAML keys rejected (`study_materials/remote_llm_policy.py`, `local_lectures.py`, `ops/strict_yaml.py`); loaded packs, ledgers and drafts tied to the cleared id (`domain/lecture_ownership.py`) | closed |
| T-06-18 | Elevation of privilege | Registry read | high | mitigate | Unreadable or malformed registry raises `GatewayConfigurationError` | closed |
| T-06-19 | Tampering | Flag sources | medium | mitigate | Id in both registry and portal list is a configuration error (OPEN-2 option A) | closed |
| T-06-20 | Tampering | Registration CLI | low | accept | Grant needs explicit `--remote-llm-allowed`; rerun changes only operator flags | closed (accepted) |
| T-06-21 | Information disclosure | Factory ordering | critical | mitigate | Egress check precedes executable resolution (`llm/factory.py`) | closed |
| T-06-22 | Elevation of privilege | Gateway construction | high | mitigate | Single construction point; scripts use `open_stage_gateway`; AST layering test | closed |
| T-06-23 | Tampering | Executable resolution | medium | mitigate | `resolve_trusted_executable` for codex, llama-completion, llama-tokenize | closed |
| T-06-24 | Tampering | Stage 5 validation | medium | mitigate | Traceability validation inside the single retry | closed |
| T-06-25 | Information disclosure | Stage 7 gateways | high | mitigate | Separate critic and drafting gateways, both preflighted | closed |
| T-06-26 | Tampering | Critic repair loop | high | mitigate | ADR 0036 loop shape kept, real last review persisted | closed |
| T-06-27 | Tampering | Validated unit identity | medium | mitigate | Identity anchored to the pack; drafts stamped and checked | closed |
| T-06-28 | Elevation of privilege | Stage 18 | high | mitigate | Script opens its own gateway and validates its own configuration | closed |
| T-06-29 | Information disclosure | Exam, flashcards, podcast ordering | medium | mitigate | Critic gate and id validation before any gateway | closed |
| T-06-30 | Tampering | Student-facing guard | medium | mitigate | Guard and count checks run inside the retry | closed |
| T-06-31 | Denial of service | Oversized prompt | medium | mitigate | `PromptTooLargeError` before spawn in both adapters | closed |
| T-06-32 | Information disclosure | Typed error messages | high | mitigate | Messages built from stored attributes only | closed |
| T-06-33 | Tampering | Podcast marker | low | mitigate | Echo stripped in parse, marker stamped after parse | closed |
| T-06-34 | Information disclosure | Child environment allowlist | high | mitigate | Exactly two names added, no prefix widening (`cli/orchestrator.py`) | closed |
| T-06-35 | Elevation of privilege | Preflight | high | mitigate | `PhaseSpec.llm_stages`; preflight in `run_phase`, `run_all`, `run_personal_study` before the first phase | closed |
| T-06-36 | Tampering | Phase 12 name clash | medium | mitigate | Explicit `llm_stages` per row; row 12 empty | closed |
| T-06-37 | Repudiation | Failure capture | low | mitigate | `tmp/codex_phase_failures/` unchanged | closed |
| T-06-38 | Tampering | Model download | high | mitigate | Pinned revision, per-shard SHA-256, `--proto '=https' --tlsv1.2`, `.part` then verify then `mv` | closed |
| T-06-39 | Information disclosure | Download scope | medium | mitigate | Download only behind `--with-example-llm-model`; runtime stays offline | closed |
| T-06-40 | Repudiation | Model provenance | medium | mitigate | License, revision and digests recorded in ADR 0046 section 5 | closed |
| T-06-41 | Repudiation | "No network egress" claim | medium | mitigate | ADR 0046 section 4 states what the guarantee does and does not mean | closed |
| T-06-42 | Information disclosure | Committed paths | high | mitigate | No absolute home paths; leak-gate hooks and CI scans active | closed |
| T-06-43 | Repudiation | Patient-data responsibility | medium | mitigate | ADR 0046 section 6 keeps detection with the operator | closed |
| T-06-44 | Repudiation | Amendment trail | low | mitigate | Forward notes in ADRs 0020, 0022, 0034-0038, 0045 and PROJECT.md, no deletions | closed |
| T-06-45 | Information disclosure | Documentation content | medium | mitigate | Same path scan and hooks as T-06-42 | closed |
| T-06-SC (plans 01-09, 11, 12) | Tampering | Supply chain | low | accept | No dependency manifest, lockfile or CI change in the phase diff | closed (accepted) |
| T-06-SC (plan 10) | Tampering | Supply chain | high | mitigate | Same controls as T-06-38; no package-manager install added | closed |

*Status: open · closed · open — below high threshold (non-blocking)*
*Severity: critical > high > medium > low — only open threats at or above workflow.security_block_on count toward threats_open*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-06-01 | T-06-20 | A registration rerun can flip `remote_llm_allowed`. This is an explicit action on the operator's own registry; revoking is always safe and granting requires the explicit flag (default false). | Edizon Basseto | 2026-10-14 |
| AR-06-02 | T-06-SC (accept, low) | Plans 01-09, 11 and 12 install no package. Confirmed: no manifest, lockfile or CI workflow changed. | Edizon Basseto | 2026-10-14 |

Design limitations documented in ADR 0046 and CONTEXT (not threats):
- The local adapter has no OS sandbox; its offline posture is shown by construction and tests.
- Detecting patient-identifying data is the operator's responsibility (D-14); the provider's retention and training terms are a procedural check (D-16).
- `model_sha256` covers the first shard of the split example model only.
- Only the most recent displaced whole-stage run record is preserved; per-pack provenance across several partial reruns is not reconstructed.
- The D-07 real-model run of stages 5-7 is pending the operator.

*Accepted risks do not resurface in future audit runs.*

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-10-14 (first audit) | 47 | 45 | 2 (1 blocking: T-06-17) | gsd-security-auditor |
| 2026-10-14 (after fix round 1) | 47 | 47 | 0 | gsd-security-auditor |
| 2026-10-14 (final state after fix rounds 1-3) | 47 | 47 | 0 | gsd-security-auditor |

Note: the Round 3 hardening commits (killpg EPERM handling, typed YAML errors, llama scratch errors, ADR note) followed the final audit. They narrow failure paths that already failed closed and add no network, process-spawn or environment surface; their tests and the full gate are green. Residual warnings from the final audit: untyped-parse and llama scratch gaps (fixed in Round 3); ADR 0046 section 1 and the `llm/__init__.py` docstring still list three allowed `ops` modules while the layering test also allows `ops/strict_yaml.py` (text drift, guard correct).

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log (accepted by Edizon Basseto)
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-10-14 (audit); risks AR-06-01 and AR-06-02 accepted by Edizon Basseto on 2026-10-15

# Phase 6: Pluggable LLM gateway - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-10-14
**Phase:** 6-Pluggable LLM gateway
**Areas discussed:** Port shape and layering, Local adapter source and model, Selection and fail-closed config, Egress enforcement (ADR 0045 s2)

---

## Port shape and layering

| Option | Description | Selected |
|--------|-------------|----------|
| Structured call | complete(prompt, response_model) -> validated model; gateway owns parse and retry | |
| Raw text call | complete(prompt) -> bytes; scripts keep parse and retry | |
| Both layers | Port is raw text; a thin shared helper does parse and retry on top | ✓ |

**User's choice:** Both layers
**Notes:** Location: new package `llm/` (recommended, selected) over inside `ops/`. Limits: adapter-owned defaults (selected) over request-carried.

---

## Local adapter source and model

| Option | Description | Selected |
|--------|-------------|----------|
| llama-cli per call | Rebuild ADR 0020/0022/0034 contract, one bounded process per call | ✓ |
| llama-server on loopback | Model stays loaded; adds a local listener | |

**User's choice:** llama-cli per call
**Notes:** Finding surfaced before the discussion: the llama.cpp text route is not in this repository's git history, so GW-03 "restored from history" means a fresh write. Model: path-configured, no pinned default (selected) over Qwen3-32B as before and a smaller pinned Qwen3. Done bar: stub in CI plus one manual real run (selected). Overflow: typed error, no chunking (selected).

---

## Selection and fail-closed config

| Option | Description | Selected |
|--------|-------------|----------|
| YAML file + env override | config/llm-gateway.example.yaml versioned, local copy git-ignored | ✓ |
| Env var only | Single variable, no per-stage override | |
| CLI flag on run-all | Not usable when a script runs standalone | |

**User's choice:** YAML file + env override
**Notes:** Per-stage override: yes, stage keys. Fail closed: preflight in the CLI plus re-validation at script start. Provenance: sidecar run record over a field in artifacts or none.

---

## Egress enforcement (ADR 0045 s2)

| Option | Description | Selected |
|--------|-------------|----------|
| Per-lecture egress flag | Registry field; gateway refuses Codex for lectures not marked allowed | ✓ |
| Procedural, documented | ADR states the rule; no code check | |
| Stage-level policy list | Gateway config lists allowed stages or lectures | |

**User's choice:** Per-lecture egress flag
**Notes:** Default for an unflagged lecture: the recommended "unflagged = remote allowed" was overridden by the operator. Final: unflagged = remote NOT allowed (fail closed); the demo catalog sets `remote_llm_allowed: true` explicitly. No-network proof: reject net flags, offline env and a test (selected) over an OS sandbox. The final "Done" prompt received an accidental paste of earlier answers; the override message was treated as the operator's closing input and no further areas were opened.

---

## Claude's Discretion

Module names inside `llm/`, env variable name, YAML keys, contract-test layout and stub binary, migration order of the six scripts, sidecar record location.

## Deferred Ideas

Prompt chunking, llama-server on loopback, adapter name inside artifact payloads, OS-level network sandbox.

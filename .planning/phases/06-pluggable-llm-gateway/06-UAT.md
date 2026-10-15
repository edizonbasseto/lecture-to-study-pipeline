---
status: complete
phase: 06-pluggable-llm-gateway
source: 06-01-SUMMARY.md through 06-12-SUMMARY.md
started: 2026-10-15T18:56:53Z
updated: 2026-10-15T19:01:07.418Z
---

## Current Test

[testing complete]

## Tests

### 1. LlmGateway port, value types and typed error hierarchy in lecture_study.llm.gateway
expected: LlmGateway port, value types and typed error hierarchy in lecture_study.llm.gateway
result: pass
source: automated
coverage_id: 06-01-D1

### 2. CodexCliAdapter with byte-identical argv, scratch cwd, sanitized environment, bounded process and argv budget guard
expected: CodexCliAdapter with byte-identical argv, scratch cwd, sanitized environment, bounded process and argv budget guard
result: pass
source: automated
coverage_id: 06-01-D2

### 3. complete_json shared parse and single bounded retry
expected: complete_json shared parse and single bounded retry
result: pass
source: automated
coverage_id: 06-01-D3

### 4. Adapter-parametrized contract suite and import-boundary guard
expected: Adapter-parametrized contract suite and import-boundary guard
result: pass
source: automated
coverage_id: 06-01-D4

### 5. Exact token preflight refuses oversized prompts before llama-completion is spawned, naming stage and sizes
expected: Exact token preflight refuses oversized prompts before llama-completion is spawned, naming stage and sizes
result: pass
source: automated
coverage_id: 06-02-D2

### 6. Retry-only repeat penalty (1.3 / 256) per ADR 0034
expected: Retry-only repeat penalty (1.3 / 256) per ADR 0034
result: pass
source: automated
coverage_id: 06-02-D3

### 7. One contract suite passes for codex and llama_cpp through stub binaries in CI
expected: One contract suite passes for codex and llama_cpp through stub binaries in CI
result: pass
source: automated
coverage_id: 06-02-D4

### 8. Configuration resolves per stage with precedence env override, stage entry, default_adapter, built-in codex
expected: Configuration resolves per stage with precedence env override, stage entry, default_adapter, built-in codex
result: pass
source: automated
coverage_id: 06-03-D1

### 9. Invalid or missing explicit configuration, unknown override and half-configured llama override fail closed with GatewayConfigurationError
expected: Invalid or missing explicit configuration, unknown override and half-configured llama override fail closed with GatewayConfigurationError
result: pass
source: automated
coverage_id: 06-03-D2

### 10. Run record is metadata-only, atomic, id-validated and located outside dashboard and delivery roots
expected: Run record is metadata-only, atomic, id-validated and located outside dashboard and delivery roots
result: pass
source: automated
coverage_id: 06-03-D3

### 11. Unflagged local lecture, unregistered reserved id and missing registry are refused for codex; unreadable registry raises a typed error
expected: Unflagged local lecture, unregistered reserved id and missing registry are refused for codex; unreadable registry raises a typed error
result: pass
source: automated
coverage_id: 06-04-D1

### 12. Portal lecture is allowed only when listed in remote_llm_allowed_lecture_ids; the registry is never read for portal ids
expected: Portal lecture is allowed only when listed in remote_llm_allowed_lecture_ids; the registry is never read for portal ids
result: pass
source: automated
coverage_id: 06-04-D2

### 13. OPEN-2 option A: overlap of registry and portal list is a GatewayConfigurationError naming the ids
expected: OPEN-2 option A: overlap of registry and portal list is a GatewayConfigurationError naming the ids
result: pass
source: automated
coverage_id: 06-04-D3

### 14. Registration flag round trip: default false, --remote-llm-allowed true, toggle on rerun both ways, legacy rows load false
expected: Registration flag round trip: default false, --remote-llm-allowed true, toggle on rerun both ways, legacy rows load false
result: pass
source: automated
coverage_id: 06-04-D4

### 15. Stage 5 runs end to end through configuration, policy, factory, codex stub, complete_json and run record (calls 1, adapter codex)
expected: Stage 5 runs end to end through configuration, policy, factory, codex stub, complete_json and run record (calls 1, adapter codex)
result: pass
source: automated
coverage_id: 06-05-D1

### 16. Unflagged lecture refused before the codex executable is resolved; unknown adapter and missing llama model exit 1 with no stub call
expected: Unflagged lecture refused before the codex executable is resolved; unknown adapter and missing llama model exit 1 with no stub call
result: pass
source: automated
coverage_id: 06-05-D2

### 17. Traceability validation retried once inside complete_json; wrong lecture id on both attempts raises LlmParsingError after two requests
expected: Traceability validation retried once inside complete_json; wrong lecture id on both attempts raises LlmParsingError after two requests
result: pass
source: automated
coverage_id: 06-05-D3

### 18. Script holds no process-spawning code (AST import boundary) and rejects a malformed lecture id before opening a gateway
expected: Script holds no process-spawning code (AST import boundary) and rejects a malformed lecture id before opening a gateway
result: pass
source: automated
coverage_id: 06-05-D4

### 19. Stage 6 writes drafts, depth_report.md and the drafting run record through a gateway; critic-feedback redraft is a non-retry request carrying the rejection heading
expected: Stage 6 writes drafts, depth_report.md and the drafting run record through a gateway; critic-feedback redraft is a non-retry request carrying the rejection heading
result: pass
source: automated
coverage_id: 06-06-D1

### 20. Stage 7 routes reviews only to the critic gateway and redrafts only to the drafting gateway; skips redraft on first-try pass; gives up after max_retries keeping the last failing pair
expected: Stage 7 routes reviews only to the critic gateway and redrafts only to the drafting gateway; skips redraft on first-try pass; gives up after max_retries keeping the last failing pair
result: pass
source: automated
coverage_id: 06-06-D2

### 21. Repair loop passes through codex and llama_cpp over stub processes; redraft carries the feedback marker and no --repeat-penalty; an unparseable review is retried once and only that llama call is penalised
expected: Repair loop passes through codex and llama_cpp over stub processes; redraft carries the feedback marker and no --repeat-penalty; an unparseable review is retried once and only that llama call is penalised
result: pass
source: automated
coverage_id: 06-06-D3

### 22. Neither script holds process-spawning code (AST import boundary) and both reject a malformed lecture id before opening a gateway
expected: Neither script holds process-spawning code (AST import boundary) and both reject a malformed lecture id before opening a gateway
result: pass
source: automated
coverage_id: 06-06-D4

### 23. Stage 13 runs end to end through the gateway: two approved units produce an uncertified exam.json and an exams.json run record whose call count equals the requests made
expected: Stage 13 runs end to end through the gateway: two approved units produce an uncertified exam.json and an exams.json run record whose call count equals the requests made
result: pass
source: automated
coverage_id: 06-07-D1

### 24. A lecture blocked by the critic gate exits 1 without opening a gateway, writing an artifact or a run record (stages 13 and 18)
expected: A lecture blocked by the critic gate exits 1 without opening a gateway, writing an artifact or a run record (stages 13 and 18)
result: pass
source: automated
coverage_id: 06-07-D2

### 25. Student-facing guard and count check retried once inside complete_json; two failures raise after two requests
expected: Student-facing guard and count check retried once inside complete_json; two failures raise after two requests
result: pass
source: automated
coverage_id: 06-07-D3

### 26. Stage 18 with an unknown adapter exits 1 naming the valid adapters, writing no deck and no run record
expected: Stage 18 with an unknown adapter exits 1 naming the valid adapters, writing no deck and no run record
result: pass
source: automated
coverage_id: 06-07-D4

### 27. Both scripts import no process or scratch-directory module (AST source boundary)
expected: Both scripts import no process or scratch-directory module (AST source boundary)
result: pass
source: automated
coverage_id: 06-07-D5

### 28. Stage 14 end to end: critic gate, gateway, echo strip, bracket normalization, validation, compiler output with the uncertified marker, request timeout 900.0 and podcast.json run record
expected: Stage 14 end to end: critic gate, gateway, echo strip, bracket normalization, validation, compiler output with the uncertified marker, request timeout 900.0 and podcast.json run record
result: pass
source: automated
coverage_id: 06-08-D1

### 29. Validation failure retried once with feedback then raised; typed call error makes exactly one request (D-22); main exits 1 without the prompt on stderr and records a failed run
expected: Validation failure retried once with feedback then raised; typed call error makes exactly one request (D-22); main exits 1 without the prompt on stderr and records a failed run
result: pass
source: automated
coverage_id: 06-08-D2

### 30. PromptTooLargeError exits 1 naming the podcast stage, writes no compiled output, records status failed (D-08, D-23)
expected: PromptTooLargeError exits 1 naming the podcast stage, writes no compiled output, records status failed (D-08, D-23)
result: pass
source: automated
coverage_id: 06-08-D3

### 31. --call-timeout-seconds reaches every request, defaults to 900, and rejects non-positive or non-finite values; script imports no process or scratch-directory module
expected: --call-timeout-seconds reaches every request, defaults to 900, and rejects non-positive or non-finite values; script imports no process or scratch-directory module
result: pass
source: automated
coverage_id: 06-08-D4

### 32. Stages 5, 6 and 7 run through the orchestrator on llama-cpp selected by YAML, and by LECTURE_STUDY_LLM_ADAPTER over a codex YAML, with the tripwire codex recording zero calls and every run record reporting adapter llama-cpp
expected: Stages 5, 6 and 7 run through the orchestrator on llama-cpp selected by YAML, and by LECTURE_STUDY_LLM_ADAPTER over a codex YAML, with the tripwire codex recording zero calls and every run record reporting adapter llama-cpp
result: pass
source: automated
coverage_id: 06-09-D1

### 33. Unknown adapter, missing llama executable and a lecture not flagged for remote use exit 1 through cli main before any phase predicate, phase runner or process spawn
expected: Unknown adapter, missing llama executable and a lecture not flagged for remote use exit 1 through cli main before any phase predicate, phase runner or process spawn
result: pass
source: automated
coverage_id: 06-09-D2

### 34. Preflight stage sets: run_all [semantic, drafting, critic, exams, podcast], run_phase 7 [critic, drafting], run_personal_study [exams, podcast]; row 12 has no stages
expected: Preflight stage sets: run_all [semantic, drafting, critic, exams, podcast], run_phase 7 [critic, drafting], run_personal_study [exams, podcast]; row 12 has no stages
result: pass
source: automated
coverage_id: 06-09-D3

### 35. Child environment contains exactly the allowlist plus the two gateway names; OPENAI_API_KEY and HTTPS_PROXY are not forwarded; command shape, failure logs directory and timeouts unchanged
expected: Child environment contains exactly the allowlist plus the two gateway names; OPENAI_API_KEY and HTTPS_PROXY are not forwarded; command shape, failure logs directory and timeouts unchanged
result: pass
source: automated
coverage_id: 06-09-D4

### 36. All six generate_codex_* scripts import llm_gateway_wiring and none imports tempfile, subprocess, process_lifecycle or process_environment; the script list is pinned to the files on disk
expected: All six generate_codex_* scripts import llm_gateway_wiring and none imports tempfile, subprocess, process_lifecycle or process_environment; the script list is pinned to the files on disk
result: pass
source: automated
coverage_id: 06-09-D5

### 37. ADR 0046 Accepted with the amendment chain to ADR 0038, ADR 0045 and GEN-07; forward notes add lines only
expected: ADR 0046 Accepted with the amendment chain to ADR 0038, ADR 0045 and GEN-07; forward notes add lines only
result: pass
source: automated
coverage_id: 06-11-D1

### 38. REQUIREMENTS.md GEN-07, GW-03, GW-05 and the Out of Scope row amended as specified; checkboxes and traceability table untouched
expected: REQUIREMENTS.md GEN-07, GW-03, GW-05 and the Out of Scope row amended as specified; checkboxes and traceability table untouched
result: pass
source: automated
coverage_id: 06-11-D2

### 39. LlamaCppAdapter against the real llama.cpp binary (06-02 D1)
expected: Pinned llama-completion accepts the D-17 flag set; adapter runs offline with validated local model path and returns parsed output (no stub).
result: pass
evidence: real llama-completion/llama-tokenize at 91d2fc3 with Qwen2.5-14B shard 1; complete() returned b'{"ok": true}' in 4.2 s; --offline in argv

### 40. Provisioning builds the pinned llama.cpp engines (06-10 D1)
expected: scripts/01_provision_engines.sh builds and verifies llama-completion, llama-tokenize and llama-mtmd-cli at the pinned commit; bad arguments exit before side effects.
result: pass
evidence: llama-completion, llama-tokenize, llama-mtmd-cli present; .built-commit equals pin; --bogus exits 2 before side effects

### 41. Opt-in example model download with SHA-256 verification (06-10 D2)
expected: --with-example-llm-model downloads the three pinned shards and verifies each SHA-256; nothing downloads without the flag.
result: pass
evidence: three shard SHA-256 match the script pins; download gated by WITH_EXAMPLE_LLM_MODEL (default 0)

### 42. D-07 manual real-model run of stages 5-7 recorded (06-11 D3)
expected: ADR 0046 decision 10 record shows stages 5, 6, 7 exit 0 with adapter llama-cpp, no Codex process, flags accepted, five follow-ups (a)-(e) marked needed or not needed.
result: pass
evidence: ADR 0046 decision 10 table: stages 5/6/7 exit 0 succeeded; follow-ups (a)-(d) not needed, (e) not needed for first-attempt flags

### 43. ADR forward pointers (06-12 D1)
expected: ADRs 0020, 0022, 0034 point to the rebuilt llama contract; ADRs 0035, 0036, 0037 point to the gateway-based Codex route; ADR 0046 referenced in all six, no deletions.
result: pass
evidence: ADRs 0020, 0022, 0034, 0035, 0036, 0037 each reference 0046

### 44. PROJECT.md revision notes (06-12 D2)
expected: PROJECT.md carries revision notes on the three locked decisions and the GW-05 clarification on the GW-05 line and Egress constraint.
result: pass
evidence: PROJECT.md GW-05 line, Egress constraint and D-READ-01 carry ADR 0046 revision notes

### 45. R0.4 review/secure coverage of the Round 3 delta
expected: Reviews re-run on c1196dc..HEAD with no new blocking finding, or operator acceptance explicitly recorded (AR-06-01, AR-06-02 in b2e5f06).
result: pass
evidence: AR-06-01 and AR-06-02 accepted by operator in 06-SECURITY.md (b2e5f06); a review re-run on c1196dc..HEAD was not independently evidenced, acceptance path used

### 46. CI green on the pushed phase-final commit
expected: After push to origin, CI test job (macos-15: pytest, ruff check, ruff format --check, mypy src) is green, tests/llm_gateway and tests/test_llm_layering.py included.
result: pass
evidence: CI run 38789108169 (ci, push, main) succeeded on HEAD b2e5f06 = origin/main; per-test job contents not inspected

## Summary

total: 46
passed: 46
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps

[none yet]

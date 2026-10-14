# 0022. llama-cli structured-output workaround: drop `--json-schema-file`, add `--single-turn`, add a repair retry

## Status

Accepted. **Historical record (ADR 0038):** this workaround applied to the local llama-cli route, now fully deleted; the Codex scripts parse JSON under ops/llm_response_contract.py instead. Supersedes ADR 0020's `--json-schema-file` decision for all 5
LLM-invoking modules (`semantic`, `critic`, `drafting`, `exams`, `podcast`).
ADR 0020, 0005, 0006, 0007, 0013, and 0014 each now carry a short erratum
note pointing here rather than being rewritten (accepted ADRs stay
immutable).

**Rebuilt by ADR 0046:** the schema stays embedded as literal prompt text and
`--single-turn` is kept, now on `llama-completion` with `--no-display-prompt`
and `--no-escape`; the parser contract is the 2026-07-28 operational
amendment (a single JSON candidate), so the earlier tolerance of banner or
echoed-prompt noise does not apply.

**Erratum (2026-07-28):** embedding schemas in prompts no longer relies on
byte-size heuristics or assumes the fixed context headroom described below.
Semantic, drafting, and critic preflight the exact file prompt with the
selected GGUF's `llama-tokenize` vocabulary. A bounded Qwen2 envelope is
rendered once, and the same binary prompt file is passed to non-conversation
`llama-completion` with escape processing disabled. Selection includes the
input, reserved output, terminal
token, and GGUF training context; unsupported architectures and prompts that
fit no supported context/output pair fail before inference. The original text
below is retained as the historical rationale.

## Context

ADR 0020 (2026-07-22) fixed the wrong-flag bug (`--json-schema` →
`--json-schema-file`) and added an `-n` completion cap, on the theory that
those two changes were sufficient to make every real llama-cli invocation
succeed. They were not: a real `make run LECTURE=10001` attempt against
real evidence pack 1 of 93, post-fix, still never completed a single
pack. Symptom looked like a Python-side memory leak (the calling
process's RSS grew ~11.6GB → ~22GB over ~17 minutes while llama-cli's own
RSS stayed flat).

Root-caused (2026-07-23) via direct llama-cli invocation, bypassing
Python entirely, to two independent bugs stacked on top of each other:

1. **The grammar sampler in this pinned llama-cli build (`91d2fc387`,
   2026-07-20) fails to initialize on ANY `--json-schema-file` request** —
   confirmed reproducible even with a trivial `{}` schema, not specific
   to any of this project's 5 schemas. Direct evidence:
   ```
   common_sampler_init: error initializing grammar sampler for grammar: ...
   E srv    send_error: task id = 0, error: Failed to initialize samplers: std::exception
   ```
   This is a known, actively recurring upstream llama.cpp defect, not a
   local misconfiguration: github.com/ggml-org/llama.cpp issues #21571
   (Apr 2026, "fixed" by merged PR #22302, then recurred), #23775 (May),
   #23990 (Jun), and #25923 (opened 2026-07-20 — the same day as this
   project's pinned commit — still OPEN as of this ADR, with an unmerged
   fix PR #25927). No merged fix exists at the pinned commit or since.
2. **llama-cli defaults to "conversation mode."** After answering a
   single-shot `-f <prompt>` completion, it does not exit — it drops into
   an interactive REPL waiting on stdin, which never arrives from an
   `asyncio.create_subprocess_exec` pipe. `process.communicate()` then
   blocks forever waiting for stdout to close. This is independent of bug
   1: it would hang every single-shot invocation regardless of whether
   grammar/json-schema was involved at all.

Bug 2 is why the "memory leak" was actually an infinite hang: when bug 1
also fires, the failed grammar init causes llama-cli's conversation-mode
fallback to spin printing blank prompt lines, and the growing Python RSS
was `asyncio`'s `communicate()` buffering that unbounded stdout — not a
real memory leak anywhere in this project's own code.

Confirmed via direct llama-cli invocation (outside Python) that dropping
`--json-schema-file` and adding `--single-turn` together produce a clean
exit with valid, schema-conformant JSON output, and confirmed again
through the real `lecture_study.semantic.llm_worker.extract_ledger()`
Python path against the real Qwen2.5-14B-Q4_K_M model and real evidence
pack `06_evidence_packs/10001/pack_001.json`: completed in 2.4s, exited
cleanly, valid `SemanticLedger`.

## Decision

**Every `_start_llama_process` call site (`semantic`, `critic`,
`drafting`, `exams`, `podcast`) drops `--json-schema-file` and adds
`--single-turn`.** The JSON schema (`<Model>.model_json_schema()`) is
instead serialized as literal text inside the prompt itself, with an
explicit "Respond with ONLY the JSON object... no markdown fences"
instruction. Each module's pre-existing `_parse_*` function (balanced
brace/array scan over stdout, tolerant of banner/echoed-prompt noise,
already validating each candidate span against the real Pydantic model)
now carries the schema-conformance burden that grammar-constrained
decoding used to enforce at the sampler level.

**`semantic.extract_ledger`, `critic.evaluate_draft`, and
`drafting.generate_draft` each gained a single retry-on-parse-failure**
(feeding trusted correction guidance derived from the first attempt's
`LlmParsingError` into the prompt), matching the retry pattern `exams.generate_exam`
and `podcast.generate_script` already had. Moving schema conformance from
an engine-enforced guarantee to an LLM-compliance one measurably raises
the odds of a shape-non-conforming single response; this closes the
resulting asymmetry (previously only 2 of 5 modules absorbed a bad first
attempt) rather than accepting it as a silent regression in the 3 that
didn't.

**Operational amendment (2026-07-28): retry prompts do not receive the
complete parsing exception.** The shared response contract maps failures to
trusted, static guidance. The raw-output diagnostic attachment contains only
the output digest and byte count; undeclared and duplicate field names and
schema-validation paths are omitted from terminal error reasons. When no valid
JSON candidate exists, multiple malformed containers of the expected response
kind are rejected as ambiguous; a single malformed expected-kind container
supplies only its decoder location. This prevents raw model output, field names,
prompt echoes, and trailing markers from becoming retry instructions or log
content.

Note for `drafting.generate_draft`: this retry is a distinct, internal
mechanism from the module's pre-existing `feedback` parameter (Phase 7's
critic-rejection regeneration hook, ADR 0007). Both can be in play at
once — a critic-rejected draft's regeneration attempt can itself fail to
parse and get its own single repair retry — via a second, internal-only
`parse_repair_feedback` prompt-text channel that composes with, rather
than replaces, the existing one.

## Consequences

- **Positive:** every LLM worker's subprocess invocation now actually
  exits, on both the happy path and every failure path tested — this was
  never true before this ADR (per ADR 0020's own log: "this repo had
  never successfully completed a real LLM phase before this fix", which
  remained true even after ADR 0020 shipped). Validated against the real
  model and real evidence data, not just the mocked test suite.
- **Positive:** the retry-on-parse-failure asymmetry between the 5
  modules (2 of 5 had it, 3 didn't) is closed.
- **Negative, accepted:** schema conformance is no longer an engine-level
  structural guarantee — a malformed or wrong-shape response is now
  possible in principle (bounded by one repair retry, then a hard
  failure) where grammar-constrained decoding would have made it
  structurally impossible. Given the clinical/medical domain, this is a
  real, deliberate trade, accepted because the alternative (the current
  llama-cli build) never produces any output at all.
- **Negative, accepted:** the schema, serialized as prompt text, now
  consumes real context-window budget (roughly 1-1.5KB per module, more
  for exams' array schema) inside the same `-c 4096` (`semantic`) /
  `-c 8192` (all others) budgets ADR 0020 sized against prompt-only
  headroom. `semantic` carries the tightest budget and was already
  reasoned as the smallest; this was not re-derived against a real
  long-transcript pack in this pass. If a real pack's prompt (transcript +
  OCR + embedded schema) is ever observed to approach or exceed its `-c`
  budget, that module's `-c` should be raised — flagged here as expected
  follow-up maintenance, not verified clear in this pass.
- **Neutral:** the upstream grammar-sampler defect (finding 1) is not
  fixed by this project and has no merged upstream fix to track yet
  (issue #25923 / PR #25927 as of this ADR). `--json-schema-file` should
  be re-evaluated for reintroduction once an upstream fix lands and this
  project's pinned llama-cli commit is bumped past it — this ADR's
  workaround is not intended as a permanent replacement for engine-level
  grammar constraints, only the current necessary one.

## Rationale

A shared per-call-site fix (same two flag changes, five files, plus a
repair retry added to the three modules missing it) was chosen over
extracting a shared `_start_llama_process`/`_build_prompt` helper in the
same pass, for the same reason ADR 0020 gave: ADR 0013/0014's explicit
"rule of three crossed, extraction deferred to V2.0" decision still
applies, and this is now the second bug-fix pass (after ADR 0020) to hit
this exact fivefold duplication. This strengthens the case for the V2.0
extraction further but does not, by itself, justify reopening that
decision mid-bug-fix.

Prompt-embedded schema + post-hoc parse/validate (rather than continuing
to fight the grammar sampler, e.g. by patching the vendored llama.cpp
source) was chosen because the exact upstream defect firing here does not
match either of the two specific bugs issue #25923's unmerged patch
targets (empty-object-properties GBNF and oversized-`maxLength`
repetition count) — this project's failure is a bare
`Failed to initialize samplers: std::exception` with no parse-specific
detail, reproduced even against a trivial `{}` schema, suggesting a
different failure inside `common_sampler_init` than either patched bug.
Patching blind against an unconfirmed root cause was rejected in favor of
a workaround verified to work end-to-end against the real model.

## Alternatives Considered

1. **Vendor issue #25923's unmerged local patch into `engines/llama.cpp`
   and rebuild.** Rejected: that patch targets two specific GBNF-generation
   bugs (empty-properties objects, oversized `maxLength`) that don't match
   this project's schemas or its bare, detail-free failure message —
   applying it would be patching blind, with no confirmation it addresses
   the actual defect hit here.
2. **Bisect to an older llama.cpp commit predating the structured-output
   regressions (pre-April 2026, before issue #21571).** Rejected for this
   pass: slow (bisection + rebuild cycles per candidate commit), and the
   same defect class recurred multiple times even after being "fixed"
   (#21571 → PR #22302 merged → recurred as #23775/#23990) — no single
   older commit is confidently known-stable, only confidently
   known-broken-later.
3. **Keep `--json-schema-file`, add only an application-level hard
   subprocess timeout as a backstop.** Rejected: this treats the symptom
   (unbounded hang) without restoring any output at all — every real
   invocation would still fail, just with a bounded failure time instead
   of an unbounded one. The pipeline would remain unable to produce a
   single real ledger/draft/review/exam/script.
4. **Drop the repair retry, accept the 2-of-5 asymmetry as a known,
   documented risk instead of closing it.** Rejected: the asymmetry was
   introduced (in relative severity, not in code) by this exact change —
   grammar-constrained decoding was the shared correctness backstop for
   all 5 modules; removing it without also giving the 3 modules that
   lacked a retry the same one-repair-retry the other 2 already had would
   leave those 3 measurably weaker than before this ADR, for no reason
   tied to their domain (semantic extraction, critic review, and draft
   synthesis are not inherently less retry-worthy than exam/podcast
   generation).

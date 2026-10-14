# 0020. llama-cli invocation contract correction: `--json-schema-file` and a mandatory `-n` completion cap

## Status

Accepted. **Historical record (ADR 0038):** the llama-cli invocation contract this ADR establishes governed the local route, now fully deleted; the bounded-subprocess discipline it introduced lives on in ops/process_lifecycle.py, which every surviving subprocess caller uses. Corrects the llama-cli invocation examples in ADR 0005, 0006,
0007, 0013, and 0014, each of which now carries a short erratum note
pointing here rather than being rewritten (accepted ADRs stay immutable).

**Erratum (ADR 0022):** the `--json-schema-file` flag this ADR's Decision
section mandates was itself subsequently dropped entirely -- this
llama-cli build's grammar sampler fails to initialize on any
structured-output request regardless of flag correctness, a known
upstream defect with no merged fix. The `-n` completion cap this ADR
established remains correct and unaffected. See ADR 0022 for the full
incident and the replacement contract.

**Erratum (ADR 0034):** the semantic and critic llama-cli workers'
shared invocation contract gained one addition -- their feedback-carrying
retry attempt now also passes `--repeat-penalty`/`--repeat-last-n`, to
mitigate a token-repetition loop found in real greedy (`--temp 0.0`)
decoding. The first attempt's invocation, and every other flag this ADR
established, are unaffected. See ADR 0034 for the full incident.

**Erratum (2026-07-28):** the table below remains the historical fixed-window
decision. Semantic, drafting, and critic now preflight the exact file prompt
with the selected GGUF's `llama-tokenize` vocabulary. The supported Qwen2
envelope is written once and the same binary prompt file is passed to
non-conversation `llama-completion` with escape processing disabled. Selection
chooses the smallest window
that fits input, output reserve, and one terminal token without exceeding the
GGUF training context. Semantic supports 4096/1024 and 12288/2048
context/output pairs; drafting supports 8192/2048 and 12288/2048; critic
supports 8192/1024 and 12288/1024. A prompt that fits no pair, or a non-Qwen2
model, fails before inference. Exams and podcast retain the historical fixed
limits and remain downstream of publication certification.

## Context

Every LLM-invoking module (`semantic`, `drafting`, `critic`, `exams`,
`podcast`) builds a near-identical llama-cli subprocess invocation
(`_start_llama_process`, duplicated by design per ADR 0013/0014's own
"defer shared-base extraction to V2.0" decision). Running the full 16-phase
pipeline end to end against a real 2-hour lecture for the first time
(2026-07-22) -- every existing unit test mocks the subprocess boundary, so
none of this was ever exercised for real before -- surfaced two bugs
shared identically across all 5 call sites, since they were all built from
the same original pattern:

1. **Wrong flag.** The argv built `"--json-schema", str(schema_path)`,
   passing the *path* to a temp file holding the JSON schema. Confirmed
   live via `llama-cli --help` and the vendored source
   (`engines/llama.cpp/common/arg.cpp`): `-j`/`--json-schema` expects the
   schema *content* inline as the argument value (e.g. `'{}'`); the
   file-based flag is `-jf`/`--json-schema-file`. Every real invocation
   failed identically: `llama.cpp exited with code 1: error while handling
   argument "--json-schema": ... invalid literal; last read: '/'`.
2. **No completion cap.** None of the 5 call sites passed `-n`/`--n-predict`.
   llama-cli's own default is `--n-predict -1` (unbounded), backstopped
   only by the `-c` context size filling up. Once bug 1 was fixed, Phase
   5's very first evidence pack -- a tiny, 839-byte pack that should
   produce a short, compact `SemanticLedger` -- ran for 65+ minutes
   producing zero output. Greedy (`--temp 0.0`), grammar-constrained
   decoding has no guarantee of halting at the "logical" end of the JSON
   object; without an explicit cap the only backstop was burning the
   entire 4096-token context, which for a 14B model is a very long,
   resource-heavy tail. The Python parent process's own memory grew
   unboundedly alongside it (`asyncio.subprocess.Process.communicate()`
   buffers the full stdout/stderr before returning), consistent with the
   child producing far more output than a compact structured ledger
   should ever need.

## Decision

**Every `_start_llama_process` call site now passes `--json-schema-file`
(not `--json-schema`) and an explicit `-n` completion cap, sized per
output type against that call's own `-c` context budget.**

| Module | `-c` | `-n` | Rationale |
|---|---|---|---|
| `semantic` (SemanticLedger, per pack) | 4096 | 1024 | Compact structured fact list; per-pack, smallest budget |
| `critic` (CriticReview, per pack) | 8192 | 1024 | Compact verdict + feedback text; per-pack |
| `drafting` (LearningUnitDraft, per pack) | 8192 | 2048 | Prose `body_markdown` section; per-pack, needs more room than ledger/review |
| `exams` (MockExam, whole lecture) | 8192 | 4096 | Every `MockQuestion` generated in one call, not per-pack -- largest per-pack-equivalent budget |
| `podcast` (PodcastScript, whole lecture) | 8192 | 4096 | Full two-speaker dialogue in one call -- same whole-lecture budget class as exams |

These values are **conservative safety bounds derived from context-budget
reasoning** (leave generous headroom under `-c` for the prompt itself,
size the completion budget to the schema's structural complexity), not
exhaustively tuned against real output-length distributions. If a real
run ever produces output that gets cut off mid-structure (`llama-cli`
itself still exits 0, but the downstream `_parse_*`/Pydantic validation
step would fail to find a complete/valid JSON object), that specific
module's `-n` should be raised -- this is expected maintenance, not a sign
the values chosen here were wrong in general.

## Consequences

- **Positive:** every llama-cli call now has a bounded worst-case
  duration and a bounded worst-case memory footprint (both in the child
  process and in the Python parent's `communicate()` buffer) --
  regardless of whether the underlying "why did greedy decoding not stop
  naturally" question is ever fully root-caused.
- **Positive:** the wrong-flag bug (`--json-schema`) is fixed identically
  across all 5 duplicated call sites in one pass, closing the exact
  failure mode ADR 0013/0014's "defer shared extraction" decision
  predicted: one bug, five places to fix, found only by luck of exercising
  all five real code paths in the same session.
- **Negative, accepted:** the `-n` values are reasoned estimates, not
  empirically validated against a corpus of real ledgers/drafts/reviews/
  exams/scripts (this repo had never successfully completed a real LLM
  phase before this fix). A future run producing truncated output for a
  legitimately large lecture unit is expected maintenance, not evidence
  this ADR's values were negligent.
- **Neutral:** does not address *why* greedy + grammar-constrained
  decoding failed to halt naturally on the observed runaway pack --
  that remains unexplained. The `-n` cap is a safety backstop, not a root-
  cause fix for that specific behavior. If runaway-to-`-n`-limit becomes a
  frequent (not one-off) occurrence, that deserves separate investigation
  into the JSON-schema-to-grammar compilation llama.cpp performs.

## Rationale

A shared per-call-site fix (same two changes, five files) was chosen over
extracting a shared `_start_llama_process` helper in the same pass:
consistent with ADR 0013/0014's explicit "rule of three has been crossed,
extraction deferred to V2.0, not reopened per-phase" decision, reaffirmed
by staff-architect review of this exact fix (2026-07-22) as still the
right boundary to respect -- a bug-fix pass is not the moment to also
restructure the duplication that made the bug fivefold, even though this
incident is more evidence for that eventual V2.0 extraction.

## Alternatives Considered

1. **Extract a shared `llm/subprocess_worker.py` helper now, fixing the
   bug once instead of five times.** Rejected for this pass per the
   Rationale above -- an explicit prior decision (ADR 0013/0014) already
   named this exact refactor and deferred it; this incident strengthens
   the case for doing it at V2.0, but doesn't justify reopening that
   decision mid-bug-fix.
2. **Investigate and fix the root cause of why greedy decoding didn't
   halt, instead of (or in addition to) adding an `-n` cap.** Deferred,
   not rejected: the `-n` cap is a correct, necessary safety bound
   regardless of root cause (any external subprocess should have a
   worst-case bound), and shipping it now unblocks the pipeline; root-
   causing the grammar/decoding behavior is a separate, deeper
   investigation with no clear urgency once the symptom is bounded.
3. **Raise `-c` (context size) instead of/alongside adding `-n`.** Rejected:
   would only raise the ceiling the runaway burns through before stopping,
   making a repeat of the 65-minute stall worse, not better -- the
   generation never needed anywhere near 4096/8192 tokens for the outputs
   these schemas actually produce.

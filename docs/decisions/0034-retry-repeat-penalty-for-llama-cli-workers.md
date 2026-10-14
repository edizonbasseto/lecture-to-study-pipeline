# 0034. Retry-only repeat-penalty for the shared llama-cli invocation contract

## Status

Accepted. **Historical record (ADR 0038):** the llama-cli workers this ADR's repeat-penalty escalation applied to are deleted with the local route; the Codex scripts handle retries under their own bounded contract. Extends ADR 0020's shared llama-cli invocation contract (that
ADR's own Decision section, and its erratum from ADR 0022, both still
apply unchanged) with one addition: the feedback-carrying retry attempt
now also passes `--repeat-penalty`/`--repeat-last-n`. The first attempt's
invocation is untouched.

**Carried forward by ADR 0046:** `--repeat-penalty 1.3 --repeat-last-n 256`
apply only to the feedback-carrying parse retry of the `LlamaCppAdapter`;
critic-feedback redrafts are first attempts and carry neither flag.

## Context

Running the pipeline end to end on new lectures (processing a later batch
of lectures (10004/10005/10006) after lecture 10003's approval) surfaced a
real failure: two of lecture 10004's evidence packs (7 and 11) made
`semantic/llm_worker.py`'s `extract_ledger` fail on both its first
attempt and its feedback-carrying retry. Direct reproduction against the
real `qwen3-32b` build (bypassing the pipeline's size-bounded error
diagnostic to capture full stdout) showed the cause: greedy (`--temp
0.0`) decoding fell into a token-repetition loop, re-emitting the same
clinical fact with minor timestamp variations indefinitely instead of
ever closing the JSON array, burning the entire `-n` output budget on an
unparseable, truncated array. The retry's own prompt differs from the
first attempt's (`build_llm_retry_feedback` appends the failure
description), so it did not reproduce the exact same failure -- but it
looped again anyway, on different content, equally unparseable.

`critic/llm_worker.py`'s `evaluate_draft` shares the identical shape:
`--temp 0.0`, a single feedback-carrying retry, the same
`_start_llama_process` argument list (confirmed byte-for-byte identical
before this change). ADR 0020 already exists specifically because this
codebase was burned once by the same fix needing five separate,
independently-discovered corrections across every llama-cli call site --
this decision applies the fix to both workers that share the exact
retry-shape exposure up front, rather than waiting for critic to
independently reproduce it on a future lecture's own draft evaluation.
`exams`/`podcast`/`drafting` workers run at `--temp 0.2` (materially
lower repetition risk, per llama.cpp's own decoding behavior at nonzero
temperature) and are not in scope for this decision.

## Decision

`_start_llama_process` in both `semantic/llm_worker.py` and
`critic/llm_worker.py` gains a keyword-only `is_retry: bool = False`
parameter. When true, it appends `--repeat-penalty 1.3 --repeat-last-n
256` to the llama-cli invocation -- llama.cpp's own standard mitigation
for a repetition loop, penalizing a token in proportion to how often it
already appeared in the last 256 tokens. `_attempt_extract_ledger`/
`_attempt_evaluate_draft` pass `is_retry=feedback is not None`: the
existing signal already distinguishing a first attempt (`feedback=None`)
from the repair retry (`feedback=build_llm_retry_feedback(...)`) at
both call sites -- no new state, no new parameter threaded through
`extract_ledger`/`evaluate_draft` themselves.

**Retry only, not both attempts.** The first attempt's sampling stays
exactly what it was before this decision: `--temp 0.0`, no repeat
penalty. This preserves the existing reproducibility contract for the
common (non-degenerate) case -- the same input pack still produces the
same first-attempt output byte-for-byte, since the vast majority of
packs never reach a retry at all. Applying the penalty unconditionally
would have traded a rare, already-mitigated-by-retry failure mode for
a permanent, unreviewed change to every successful first attempt's
sampling behavior.

**1.3 / 256, not llama.cpp's own defaults.** `llama-cli --help` reports
defaults of `repeat-penalty=1.00` (i.e., disabled) and
`repeat-last-n=64`. Both chosen values are standard, commonly-cited
starting points in the llama.cpp ecosystem for breaking a repetition
loop without materially degrading otherwise-correct output; they have
not been exhaustively tuned against a corpus of degenerate packs (only
two real packs, from one lecture, confirmed the fix), matching this
codebase's own established honesty standard for such constants (ADR
0020's own admission that its `-n` values are "reasoned estimates, not
exhaustively tuned").

## Alternatives considered

- **Raise `--temp` above 0.0 on the retry instead of adding a repeat
  penalty.** Rejected: a nonzero temperature changes sampling in a way
  that is harder to reason about for a schema-conformance retry (more
  ways to fail differently, not just fewer ways to loop) than a targeted
  anti-repetition penalty, which directly addresses the observed failure
  mode without otherwise perturbing the decoding distribution.
- **Apply the repeat penalty to the first attempt too, uniformly.**
  Rejected: would change every successful pack's first-attempt output
  (breaking today's reproducibility contract for the common case) to
  guard against a failure mode that already has a retry path; the retry
  is the correct place to spend this mitigation.
- **Leave `critic/llm_worker.py` unfixed until it independently
  reproduces the same failure.** Rejected (review finding, this
  decision's own drafting): ADR 0020 exists precisely because this
  codebase already paid the cost of finding "one bug, five places to
  fix" the hard way, one real-pipeline failure at a time. Both workers
  share the exact retry shape; fixing both now, with an explicit
  cross-reference in each module's docstring, is cheaper than waiting.

## Consequences

- `exams/generator.py`'s (Phase 13), `podcast/generator.py`'s (Phase
  14), and drafting's own llama-cli workers are explicitly out of scope
  (`--temp 0.2`, lower risk) -- if any of them independently reproduces
  a retry-attempt repetition loop, that is a new finding for its own
  ADR, not an oversight this one silently accepted.
- Both modules' regression suites (`tests/test_semantic.py`,
  `tests/test_critic.py`) each gained one test asserting
  `--repeat-penalty`/`--repeat-last-n` appear on the second
  (feedback-carrying) llama-cli invocation only, never the first.

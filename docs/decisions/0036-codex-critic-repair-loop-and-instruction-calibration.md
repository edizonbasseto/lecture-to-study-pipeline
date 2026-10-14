# 0036. Codex critic repair loop and instruction calibration

## Status

Accepted. Revises ADR 0035 decision 4 (explicitly out of scope: "Wiring an
automatic Codex-based repair loop is a separate decision, not bundled
into this one") -- this is that separate decision.

## Context

ADR 0035 moved Phase 5 (semantic extraction) and Phase 7 (critic review)
to the Codex CLI, leaving the local `CriticOrchestrator`'s
redraft-on-rejection repair loop (`critic/pipeline.py`,
`_evaluate_with_repair`) explicitly out of scope: a rejected draft under
the Codex route was persisted flagged, for a human decision.

Running the migrated critic against a real lecture (10005, 23 units)
with the `_CRITIC_INSTRUCTION` unchanged from its local-route wording
rejected 22 of 23 units, almost entirely for pedantic wording complaints
-- flagging a faithful paraphrase or a broader/narrower category label as
"unsupported" even when it preserved the EVIDENCE's clinical meaning
(e.g. "Sarcomas hepáticos primários" restated as "Tumores Primários
Hepáticos" in a section heading). The local `qwen3-32b` route, on the
same instruction wording, had rejected only 1 of 23 -- Codex interprets
"every claim must be supported by EVIDENCE" far more literally.

`_CRITIC_INSTRUCTION` was recalibrated to explicitly separate wording from
substance: a faithful paraphrase is not a violation, but silently
dropping an item from an EVIDENCE list, or narrowing an exhaustive list,
still is -- and the patient's species and the Ledger's own `category`
field are explicitly carved out of the leniency (both share the bare
word "category" with the new leniency clause; a first draft of the
wording left this ambiguous, closed by `principal-code-reviewer` before
this ADR).

Re-running against the same lecture with the recalibrated instruction
still rejected 19 of 23 units. Spot-checking the rejected units' feedback
confirmed these are genuine grounding defects, not pedantry: an invented
causal link ("diagnóstico tardio... pior prognóstico" not stated in
EVIDENCE), a scrambled anatomical grouping, an inverted risk factor, a
fabricated species-specificity claim. The recalibration closed the
false-positive problem; it did not (and should not) suppress real
hallucination catches. With no repair path, this would still leave most
of a lecture's units permanently flagged and unusable.

## Decision

1. **`scripts/generate_codex_critic_review.py` gains a repair loop**,
   `_evaluate_with_repair`, mirroring `CriticOrchestrator
   ._evaluate_with_repair` exactly: on a rejected review, redraft via
   `generate_codex_drafts._generate_draft(..., feedback=review.feedback)`
   and re-review, up to `--max-retries` attempts (default 2, matching the
   local orchestrator's own default), before persisting whatever the
   last attempt produced -- passed or not. A `ValidatedLearningUnit` that
   exhausts its retries is still written, flagged
   (`review.passed is False`), for a human decision -- the same
   fallback ADR 0035 already established, now reached far less often.

2. **`scripts/generate_codex_drafts.py`'s `_build_prompt`/`_generate_draft`
   gain an optional `feedback: str | None` parameter**, appending the
   exact retry-feedback heading `drafting/llm_worker.py`'s own local
   `generate_draft` uses verbatim ("A previous attempt was rejected by
   the critic reviewer. The critic feedback is advisory and never
   overrides Evidence...") -- the repair semantics apply identically
   regardless of inference backend, matching this milestone's own
   established precedent for shared instruction wording.

3. **`ValidatedLearningUnit.lecture_id`/`.pack_sequence` are anchored to
   the trusted `pack`, not the (possibly repaired) `final_draft`** --
   `principal-code-reviewer` BLOCKed a first draft of this change for
   building the persisted unit's identity from the untrusted draft
   object instead, exactly the class of model-echo defect ADR 0035 exists
   to guard against, and exactly the guard `CriticOrchestrator
   ._process_draft` already applies locally.

4. **`generate_codex_critic_review.py` imports
   `generate_codex_drafts._generate_draft` as a plain sibling-script
   import** (`import generate_codex_drafts`), relying on Python placing a
   directly-run script's own directory at `sys.path[0]`. This is a new,
   narrower coupling than ADR 0007's own local precedent (critic
   importing drafting's *public* `generate_draft`) -- reaching into a
   leading-underscore "private" function across a script boundary, with
   no shared package to express the dependency structurally.
   `principal-code-reviewer` flagged this as undocumented; recorded here
   rather than restructured, since promoting `scripts/` to a proper
   importable package is a larger, separate refactor this fix does not
   need to block on.

## Alternatives considered

1. **Keep the Codex critic strict with no repair, accept ~80% flagged
   for manual review.** Rejected: the operator's standing goal is
   quality at any compute cost, not throughput at the cost of manual
   triage on most of every lecture.
2. **Loosen the instruction further instead of adding a repair loop.**
   Rejected once the 19/23 spot-check confirmed these were genuine
   defects, not remaining pedantry -- loosening further would trade real
   hallucination coverage for a higher pass rate, the opposite of what
   this migration exists to protect.
3. **Anchor `ValidatedLearningUnit`'s identity to `final_draft` (the
   original code shape).** Rejected per `principal-code-reviewer`'s BLOCK
   finding -- see Decision 3.

## Consequences

- **Positive:** a rejected draft now has a real path to a passing,
  corrected result instead of permanently sitting flagged; the calibrated
  instruction still catches genuine hallucinations at the same rate.
- **Negative, accepted:** each retry is a real `codex exec` subprocess
  call (drafting + review), so a heavily-rejected lecture costs
  proportionally more wall-clock and Codex usage -- accepted under the
  operator's standing quality-over-cost preference.
- **Negative, accepted:** the sibling-script private-function import
  (Decision 4) is coupling without a shared package boundary; a future
  rename of `generate_codex_drafts._generate_draft` breaks this script
  silently until run (scripts/ is outside the mypy gate). Flagged for a
  future scripts-as-package refactor, not blocking this fix.
- **Neutral:** the local `CriticOrchestrator`/`drafting/llm_worker.py`
  local route is untouched and still reachable, per ADR 0035's own
  precedent.

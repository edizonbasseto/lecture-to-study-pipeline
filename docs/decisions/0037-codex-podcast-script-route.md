# 0037. Codex Route for Podcast Script Generation (Phase 14)

## Status

Accepted.

## Context

ADR 0035 moved Phase 5 (semantic extraction) and Phase 7 (critic) from the
local qwen3-32b/llama.cpp route to the Codex CLI, part of a wider,
operator-directed migration of every local-LLM phase to Codex ahead of the
local route's eventual full removal (memory:
`2026-09-16-codex-migration-phase5-7-and-pack12-defect.md`). ADR 0030
already gave Phase 13 (mock exam) a Codex alternative; a follow-up session
gave Phase 18 (personal-study flashcards) one too
(`scripts/generate_codex_personal_study_flashcards.py`). Phase 14 (podcast
script, ADR 0014's local `podcast/generator.py`) was the last
student-facing generator with no Codex alternative, and the operator's
agreed sequencing requires one to exist, validated, before the local
qwen3-32b/llama.cpp route can be deleted.

The new script, `scripts/generate_codex_podcast.py`, follows the same
family shape as the mock-exam/flashcard Codex scripts (`codex exec` in a
read-only sandboxed scratch directory, JSON-schema-in-prompt, a bounded
retry-once on `LlmResponseContractError`), but makes two decisions that
family's own pattern does not cover, recorded here rather than left only
in the new script's own docstring.

## Decisions

1. **One whole-lecture Codex call, not a per-unit loop.** The mock-exam
   and flashcard Codex scripts each ask Codex once per learning unit,
   because a question or a flashcard is self-contained. A podcast
   dialogue is not: `podcast/generator.py`'s own instruction (ADR 0014)
   already frames it as one coherent Host/Expert conversation spanning
   every unit of the lecture, and `PodcastScript` is a single object with
   one `title` and one ordered `dialogue` list, not an array of
   independent items. Splitting generation per unit would produce
   disconnected fragments with no cross-unit narrative, defeating the
   format. The new script therefore builds one evidence pack covering
   every unit (each tagged with its own `unit-{pack_sequence:03d}` id, so
   `source_unit_id` traceability still works exactly as ADR 0014
   specifies) and makes a single Codex call for the whole script.
2. **Grounding source is `workspace/data/08_drafts` (Phase 6
   `LearningUnitDraft.body_markdown`), not `workspace/data/
   10_canonical_documents` (Phase 8) that `podcast/generator.py` itself
   reads.** The later batch of lectures (10004/10005/10006) this route was
   built for has no Phase 8 canonical document at all -- the Codex mock-exam/flashcard scripts
   already established the precedent of reading directly from Phase 6/7
   artifacts for this batch, bypassing Phase 8 entirely, and this script
   follows that same precedent rather than introducing a third input
   convention. `LearningUnitDraft.body_markdown` is the same kind of
   per-unit prose `CanonicalLectureDocument.units[i].content.body_markdown`
   is, so the evidence-pack construction (`[unit-NNN] {body_markdown}`)
   is unchanged in shape, only in which pipeline stage it reads.
3. **Output is written through the existing `PodcastScriptCompiler`
   (`podcast/compiler.py`), not a bespoke writer.** That compiler already
   special-cases `ArtifactExecutionMode.UNCERTIFIED_PERSONAL_STUDY` to
   skip derivative-certificate verification, already writes both
   `script.json` and a readable `script_legivel.md` transcript, and
   already writes both atomically. Reusing it avoids duplicating that
   logic (R0.2) and gives this route the readable transcript the
   mock-exam/flashcard Codex scripts have no equivalent of.
4. **The ADR 0027 student-facing-text guard (`student_facing_text_defects`)
   is enforced on the parsed Codex response**, exactly as
   `podcast/generator.py`'s own `_reject_script_unfit_for_a_listener`
   enforces it on the local route's response. ADR 0027's own evidence is
   that a prompt instruction alone is not sufficient -- a prompt-only fix
   still produced referring-phrase violations in practice -- so this
   route runs the same post-parse guard the local route runs, inside the
   same bounded retry-once path already used for schema/traceability
   failures.

## Consequences

- Phase 14 now has a Codex alternative; per the operator's agreed
  sequencing, this closes the last precondition (podcast) before the
  qwen3-32b/llama.cpp removal audit (memory file, section 7) can begin.
- **Known output collision, unresolved**: this script and the
  still-live local `podcast/generator.py` + `podcast/compiler.py` write
  to the identical `workspace/data/15_podcast_scripts/{lecture_id}/`
  path with no provenance field distinguishing the two routes. Running
  both for the same `lecture_id` silently overwrites whichever ran
  first. Resolved only once the local route is actually deleted.
- **Known, accepted gap, not yet closed**: unlike the local route's
  per-attempt context/output-token escalation
  (`_QWEN2_INFERENCE_WINDOWS`/`_QWEN3_INFERENCE_WINDOWS`), this script's
  single whole-lecture prompt has no evidence-pack size check, and its
  retry resends the same size prompt unchanged. A lecture dense enough
  to truncate Codex's JSON response on the first attempt will likely
  fail identically on the retry. Not yet observed against a real
  lecture in this batch; revisit (chunking the evidence pack, or a
  lecture-density guard before the first call) if it is.
- Subprocess-level failures (`codex exec` non-zero exit, a timeout, or
  an output-limit breach) are now wrapped into `LlmResponseContractError`
  at the call site, so they participate in the existing retry-once path
  and surface through `main()`'s uniform `error: ...` message, instead of
  an unhandled Python traceback.

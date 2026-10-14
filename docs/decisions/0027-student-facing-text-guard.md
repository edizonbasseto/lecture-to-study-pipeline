# 0027. Student-facing text guard for generated study material

## Status

Accepted.

## Context

Lecture `10002`'s first real mock exam was unusable. Eleven of its twelve
questions were written in English for a Brazilian Portuguese lecture, and all
twelve addressed the student "according to the Evidence Pack" -- an internal
pipeline abstraction the student never hears of. Every upstream artifact was
clean: the 90 ledger facts, the drafts and the canonical document carried
neither defect, so the cause was localized in the exam generator's own prompt,
which named its input "Evidence Pack" and never demanded pt-BR output.

The same shape was present in two sibling generators that write directly to the
student: `flashcards/personal_study_generator.py` labeled its input the same
way, and `podcast/generator.py` both labeled it that way and carried no
language clause at all, while also exposing unit ids such as `[unit-001]` that
a spoken line could echo.

The critic cannot catch either defect. It verifies factual grounding against
the evidence, and a question written in English about a correctly grounded fact
is consistent with that evidence.

## Decision

Two changes, applied to all three student-facing generators.

1. **Prompt.** The instruction demands Brazilian Portuguese for every
   student-facing field, forbids referring to the source material or to the
   instruction itself, and no longer names its input "Evidence Pack" -- the
   prompt section is labeled "Lecture material". A run against the real lecture
   showed the model still produced referring phrases such as "segundo o
   material" in six of twelve questions, so the instruction names that
   construction explicitly as unusable.

2. **Guard.** `quality/student_facing_text.py` reports, without inference,
   three defect classes over generated passages: text naming an internal
   pipeline abstraction, text referring to the source material, and text not
   written in Brazilian Portuguese. Each generator checks its parsed output
   through this guard inside its existing bounded repair-retry path, so a first
   defective attempt is discarded and retried once and a second defective
   attempt fails the run. No defective artifact is written, and no defect is
   silently accepted.

   The retry carries the repository's existing closed-vocabulary
   response-contract feedback, deliberately not the offending text: echoing a
   generated sentence back into the next prompt would put model-controlled
   bytes into an instruction. For the same reason the guard's own evidence
   string -- the only substring of generated text this module keeps -- is
   collapsed to one line and capped before it reaches a failure message or the
   run log.

The language check counts English function words with no Portuguese homograph
and requires two distinct ones, so English-origin surgical vocabulary in
correct pt-BR prose is never flagged; a passage of eight words or fewer -- an
exam option, an episode title -- has too little room to accumulate two, so one
marker is enough there. The source-reference check requires an explicit
referring head ("segundo", "conforme", "de acordo com", "mencionado n-") before
the source noun, never a bare preposition, so surgical prose such as "a
contaminação do material cirúrgico" or "a drenagem do conteúdo purulento" is
untouched.

## Alternatives considered

1. **Prompt fix alone.** Rejected on evidence: the first run after the prompt
   fix still produced six questions containing "segundo o material". A prompt
   is an instruction to a sampler, not a guarantee, and this defect class is
   mechanically detectable.
2. **A diagnostic-only report, in the mold of `exams/echo_scan.py`.** Rejected
   for this defect class. `echo_scan` deliberately stays a finding for a human
   reading, because deciding whether a question tests reasoning is a judgement.
   English text and a leaked internal name are not judgements.
3. **Repairing the defective text after generation.** Rejected: rewriting
   generated study material outside the model's own grounded pass would create
   text no critic ever reviewed.

## Consequences

- **Positive:** the regenerated exam for `10002` has twelve pt-BR questions,
  zero internal-name references and zero source references, verified by the
  guard against the real artifact.
- **Positive:** the same protection now covers the flashcard deck and the
  podcast script, which shared the defect shape and had never been run.
- **Negative, accepted:** the guard consumes a generation attempt when it
  fires. A run that produces one leaking question in twelve pays a full retry,
  and a second defective attempt fails the run outright. This is the intended
  cost: the alternative is a defective artifact reaching a study bundle.
- **Negative, accepted:** the language check cannot see a short English phrase
  built only of content words ("Blue marking pen"), since it recognizes English
  by function words. It catches the observed defect shape -- English sentences
  and mixed-language sentences -- not every possible one.
- **Neutral:** the exam's `_EXAM_INSTRUCTION` participates in the derivative
  execution identity, so this change rotates `prompt_sha256` and
  `generator_sha256`. No quality certificate exists for any lecture and no
  digest is pinned anywhere, so nothing goes stale.

## Rationale

The ordering lesson from this phase's handoff applies directly: the defect was
found only when a real end-to-end output was finally read. A deterministic
guard over exactly the defect classes that reading exposed makes the next
occurrence fail the run instead of waiting for another human reading, while
leaving every judgement that genuinely needs a human -- clinical accuracy,
pedagogical value -- where it already is.

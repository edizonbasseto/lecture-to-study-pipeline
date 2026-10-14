# 0028. ASR vocabulary prompt and deterministic terminology correction

## Status

Accepted.

## Context

Lecture `10002`'s study material taught two wrong clinical terms, and the
pipeline was not the cause: it propagated faithfully what the ASR produced.
Measured across all 25 transcripts in `workspace/data/05_transcribed`:

| Transcribed | Correct term | Occurrences | Lectures |
|---|---|---|---|
| `cisional` | excisional | 4 | 1 (`10002`) |
| `estopatológic-` / `estopatologia` | histopatológic- | 55 | 16 of 25 |
| `facial` in a depth context | fascial | 27 | 2 (`10002`, `10007`) |

An earlier measurement of the second row reported 6 occurrences in one
lecture. That was wrong: the pattern used omitted the accent, so
`estopatológico` never matched. The corrected figure is 55 across 16
lectures, which is what makes a deterministic rewrite worth having beside
the prompt: the defect is not localized to the pilot lecture.

Evidence gathered by cutting the real audio and invoking whisper.cpp
large-v3-turbo directly:

- Supplying a domain vocabulary as `--prompt` with `--carry-initial-prompt`
  corrects `cisional` to `excisional` on the same audio. Without it, the
  same passage yields `incisional`, which inverts the clinical meaning.
- The same prompt does **not** correct `plano facial`, even with `plano
  fascial` and `camada fascial` spelled in it, while the speaker's own "a
  fáscia muscular" transcribes correctly. The audio says "facial".

A full re-transcription of both affected lectures under the prompt
confirmed it end to end: `10002` went from 4 `cisional` and 13 `facial` to
0 and 0, with 5 `excisional` and 13 `fascial`; `10007` likewise, with
identical audio coverage and equal-or-greater transcript length.

## Decision

Two mechanisms, each matched to what the evidence shows it can fix.

1. **A vocabulary prompt on the ASR pass.** `transcription/whisper_worker.py`
   passes `DOMAIN_VOCABULARY_PROMPT` as whisper.cpp's `--prompt` with
   `--carry-initial-prompt`. The prompt names no discipline and no fascia
   term: it is carried into every decoding window of every lecture of every
   discipline (the lecture catalog spans multiple disciplines), so a prompt
   asserting an oncology context, or spelling "fascial", would bias the
   decoder against the many lectures where "nervo facial", "paralisia
   facial" and "veia linguofacial" are the correct readings. It lists only
   terms whose observed defect is a dropped first syllable.

2. **A deterministic correction after the ASR pass**, in
   `transcription/terminology.py`, reported per lecture in
   `05_transcribed/{id}_terminology_corrections.json`:
   - `estopatológic-`/`estopatologia` is rewritten unconditionally. It is
     not a Portuguese word, so no context is needed.
   - The fascia adjective is rewritten after one of `camada`, `plano` or
     `margem`, including across a segment boundary, but only in a lecture
     that pairs that phrase with a depth context (`profundidade`,
     `abaixo`, `fáscia`, `músculo`, `musculatura`, `tumor`) at least once.
     The decision is taken per lecture rather than per occurrence because
     this speaker is segmented into fragments as short as "Camadas
     faciais.", which carry no anchor of their own; gating each occurrence
     separately left four of them uncorrected in `10007`, so the same
     sentence appeared in two spellings. Replayed over the corpus, this
     rewrites 26 segments across the two affected lectures, leaves no
     `facial` in either of them, and touches none of the 38 legitimate
     occurrences in the other 23 lectures.

The audit record is written **before** the transcript, because the
transcript is this phase's idempotency marker: the reverse order would let
a failure between the two writes leave a corrected transcript that every
later run skips, with no record of what was rewritten.

## Alternatives considered

1. **Prompt only.** Rejected on evidence: it does not correct the fascia
   term, and it fixes nothing already on disk.
2. **Correction only, no prompt.** Rejected: `cisional` versus `incisional`
   is a meaning inversion the prompt demonstrably prevents at the source,
   and a rewrite there would have to guess which of the two the speaker
   said.
3. **A larger or different ASR model.** Rejected for now: the fascia error
   is in the audio, not in the recognition, so a better model cannot fix
   the more consequential half.
4. **Re-transcribing the whole corpus.** Deferred, not rejected, and it is
   what will actually close the defect on the 25 transcripts already on
   disk: Phase 3 is idempotent on its artifact, so neither mechanism here
   touches a lecture that has already been transcribed. The operator's
   priority is the current semester's new lectures, which are transcribed
   from scratch and therefore get both mechanisms; re-transcribing the
   existing corpus follows.

## Consequences

- **Positive:** every lecture transcribed from now on carries the correct
  terminology, and the correction is deterministic rather than dependent on
  a sampler.
- **Negative, accepted:** the per-lecture gate trades precision for
  recall. In a lecture that does discuss fascial depth, a legitimate
  "margem facial do dente" in the same lecture would also be rewritten. No
  such lecture exists in the current corpus, and every rewrite is in the
  audit record with its original text, so the case is detectable and
  reversible; the alternative -- gating each occurrence -- left four
  fragments uncorrected in `10007` and produced two spellings of the same
  sentence.
- **Negative, accepted:** the fascia rewrite makes the transcript differ
  from the audio deliberately, on the operator's decision that study
  material must teach the correct term. Every rewrite is recorded with its
  timestamp and original text so it can be checked against the recording.
- **Negative, accepted:** transcripts already on disk are not rewritten by
  this change alone. Phase 3 is idempotent on the artifact, so an existing
  lecture must be re-run deliberately, and its downstream artifacts
  regenerated.
- **Neutral:** Phase 3 now writes a second per-lecture artifact next to the
  transcript, extending ADR 0003's output contract.

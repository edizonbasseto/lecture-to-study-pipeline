# 0024. Uncertified personal-study execution mode for Phases 13-15

## Status

Accepted. 2026-09-13. Amended by ADR 0045: the route reads critic-approved units from `09_validated_units` under the Phase 8 completeness rules instead of the Phase 8 document, and the audience bound is widened to the allowlisted site audience.

## Context

`QualityCertificateIssuer.issue()` (`src/lecture_study/quality/issuer.py:85-95`)
always executes the gate registry, always writes a `CertificationAttempt`, and
then unconditionally raises `QualityCertificationError`. There is no approval
branch in this method. Every `QualityGateName` currently receives a
`GateExecutionBlocker`, most carrying reason code
`authoritative-evaluator-not-implemented`. This is deliberate: ADR 0023
withholds production certification until an expert-annotated, promoted
veterinary reference corpus and a calibrated evaluation authority exist
(ADR 0023 sections 5.7 and 10). No such corpus is present in this repository.

`src/lecture_study/cli/orchestrator.py` calls `require_publication_ready(lecture_id)`
for every phase with `spec.number > 10`, both in `run_phase` (line 558) and in
`run_all` (line 581). Because `issue()` never returns an approval, this call
always raises. The consequence is that Phases 10 through 15 -- quality
certificate, publishing, flashcards, mock exam, podcast, and TTS -- are
unreachable for every lecture, permanently, under the current code.

`MockExamGenerator.generate_exam` (`src/lecture_study/exams/generator.py`)
adds a second, independent gate: `_verify_source` calls
`QualityCertificateVerifier.verify(lecture_id)`
(`src/lecture_study/quality/certification.py:246`), which reads an actual
`certificate.json` from `workspace/data/quality_certificates/<lecture_id>/`.
Because `issue()` never writes an approved certificate, this file never
represents an approval and `verify()` has nothing valid to recompute against.
The podcast generator was not separately confirmed in this pass to share the
same internal verification call, but the orchestrator gate alone already blocks
it identically to the mock-exam path; any internal certificate check it may
carry must be treated the same way as the exam generator's.

This means Phases 13 and 14 consume the Phase 8 `CanonicalLectureDocument`
(`CANONICAL_DOCUMENTS_INPUT_DIR`, `src/lecture_study/exams/generator.py:73,168-176`)
as their actual content source, not the quality certificate itself -- the
certificate is consulted only as a gate, not as an input to generation. This
is a meaningful distinction: nothing about the generation logic requires a
certified input, but the current code path to reach that logic requires one
to exist.

Phases 8 (canonical assembly, `src/lecture_study/assembly/assembler.py`) and 9
(metadata) are deterministic, require no certificate, and have never been
executed against real data: `workspace/data/10_canonical_documents/`,
`11_metadata/`, and `quality_certificates/` are empty for all 25 lectures
processed so far. Phase 8 requires a contiguous `pack_sequence` and
`review.passed` true on every validated unit before it will assemble a
canonical document.

The 24 study bundles delivered to date were produced outside this pipeline
entirely, by `scripts/generate_personal_study_guides.py`, which reads only
`07_semantic_ledgers` and `08_drafts` (lines 72-73) and performs no inference.
Its mock exam and flashcards are deterministic templates, which is why
generated questions echo section titles verbatim
(`src/lecture_study/study_materials/bundle.py:1390`). That script is a
stopgap outside the certified architecture and is not modified by this
decision; it is cited here only to establish that a lower-rigor personal-study
path already exists informally, alongside the fully gated production path.

There is currently no path between those two extremes: no way to run the
pipeline's real LLM-backed Phase 13/14/15 generation (as opposed to the
template script) against a real Phase 8 canonical document without a
certificate that ADR 0023 has deliberately made unobtainable pending the
reference corpus.

### Two additional choke points found during implementation

The gate inventory above, drawn from the orchestrator call and
`MockExamGenerator._verify_source`, was not exhaustive. Implementing this
decision's Phase 13 branch found two further fail-closed choke points on the
same path, both of which the uncertified route must also bypass rather than
attempt to satisfy:

- `_run_phase_13` (`src/lecture_study/cli/orchestrator.py`) calls
  `evaluate_mock_exam_quality`, which routes to
  `DerivativeQualityIssuer._evaluate` and raises `DerivativeQualityError`
  unconditionally, for the same reason `QualityCertificateIssuer.issue`
  does: the required derivative critics are not implemented. The equivalent
  call in `_run_phase_14` (`evaluate_podcast_script_quality`) shares this
  behavior for the podcast path, out of this plan's scope but subject to the
  same treatment.
- `ExamCompiler.compile` (`src/lecture_study/exams/compiler.py`) calls
  `DerivativeQualityCertificateVerifier.verify_exam` before writing any
  output, and that verifier requires a real derivative certificate that the
  fail-closed issuer above never produces. The podcast compiler's equivalent
  call is out of this plan's scope but subject to the same treatment.

Both are handled by the same discipline as the two gates identified above:
the uncertified route skips the call entirely for a payload carrying the
`UNCERTIFIED_PERSONAL_STUDY` execution-mode marker, and continues to make
the call, unmodified, for every other payload. Neither
`DerivativeQualityIssuer` nor `QualityCertificateIssuer` is edited to
accommodate this; both keep raising unconditionally for the certified path.

## Decision

An explicit, separately named **uncertified personal-study execution mode**
is introduced. When invoked, it allows Phases 13 (mock exam), 14 (podcast),
and 15 (TTS) to run directly from a lecture's Phase 8
`CanonicalLectureDocument`, without a publication certificate, for the
purpose of generating personal study material for a single student.

This mode does not touch Phase 10 or the certification chain in any way.
`QualityCertificateIssuer.issue()` keeps raising `QualityCertificationError`
unconditionally, exactly as it does today. `require_publication_ready` keeps
gating every phase numbered above 10 in the certified path. The uncertified
mode is a distinct, parallel invocation surface for Phases 13-15 specifically,
not a change to the meaning or behavior of `require_publication_ready` itself.

Because both the orchestrator gate and the exam generator's own
`QualityCertificateVerifier.verify()` call currently block on the same
nonexistent certificate, the uncertified mode's Phase 13/14/15 entry points
must construct their `VerifiedPublicationInputs`-equivalent input directly
from the Phase 8 canonical document and Phase 9 metadata, bypassing
`QualityCertificateVerifier` entirely for this path rather than attempting to
satisfy it. A generator invoked in this mode must not call
`QualityCertificateVerifier.verify()` at all; introducing a mode that tries to
make `verify()` pass without a real certificate would itself be a form of
certificate forgery and is explicitly rejected.

### Enforced consequences

- **The clinical certification boundary is not weakened, sampled, relabeled,
  or bypassed.** Phase 10 keeps raising `QualityCertificationError`
  unconditionally. No configuration, flag, or code path introduced by this
  mode may cause `QualityCertificateIssuer.issue()` to return an approval, and
  none may cause `require_publication_ready` to pass for the certified path.
  No artifact produced under this mode may be presented, labeled, or exported
  as certified, production-ready, or clinically approved.
- **The mode is explicit and opt-in at invocation, never the default.** It
  requires a distinct flag or subcommand (for example, a
  `--uncertified-personal-study` invocation form, exact naming left to
  implementation) that is absent from `run_phase` and `run_all`'s default
  behavior. Omitting it must continue to hit the existing fail-closed gate.
- **Uncertified artifacts are always distinguishable from certified ones.**
  Every artifact produced in this mode carries an explicit marker recording
  that it was generated without publication certification -- in its own
  payload (not only in an external manifest), so the marker survives export,
  copy, or file rename. The generation-receipt mechanism already used for
  derivative artifacts (`GenerationReceipt.issue`,
  `src/lecture_study/exams/generator.py:139-149`) is the natural place to
  record this, using a distinct receipt classification rather than a
  substitute `source_certificate_sha256`.
- **Phase 8's completeness contract is unchanged.** Assembly still requires a
  contiguous `pack_sequence` and `review.passed` true on every validated unit.
  A lecture with an unresolved clinical finding does not assemble, in this
  mode or any other, and stays visibly blocked at Phase 8 rather than being
  downgraded to a passing state to unblock uncertified generation.
- **The mode's authority is bounded.** It grants no clinical, academic, or
  publication authority of any kind. Its sole purpose is producing study
  material for the single student who requested it. It must not be exposed as
  a batch, multi-student, or distribution-facing capability, and it confers no
  standing toward eventual production certification.

## Consequences

- **Positive:** Phases 13-15's real LLM-backed generation becomes reachable
  against real Phase 8 output for personal use, closing the current gap where
  the only working alternative is the deterministic, non-inferential
  `generate_personal_study_guides.py` template script.
- **Positive:** the certification boundary established by ADR 0023 remains
  fully intact and independently testable; this ADR adds a parallel path
  around Phases 13-15 only, not a weakening of Phase 10 or of
  `require_publication_ready`.
- **Negative, accepted:** two execution paths for Phases 13-15 now exist --
  certified (currently unreachable, pending ADR 0023's reference corpus and
  calibration authority) and uncertified personal-study. Two paths that must
  produce the same artifact shapes from the same Phase 8 input create real
  risk of drift: a fix, schema change, or prompt change applied to one path
  and not the other would silently diverge behavior between "real" and
  personal-study output. This is contained by requiring both paths to share
  the same generation logic (prompt construction, parsing, retry behavior)
  and to differ only in which verification and receipt-issuance calls they
  make at the boundary -- the uncertified entry point must not fork or
  duplicate `MockExamGenerator`'s or the podcast generator's core generation
  code, only its source-verification step.
- **Negative, accepted:** the existence of an uncertified path is itself a
  standing temptation to relabel its output as certified once it is
  convenient to do so. This is mitigated by the mandatory, payload-embedded
  marker described above, which is designed to survive exactly that kind of
  relabeling attempt.
- **Neutral:** `scripts/generate_personal_study_guides.py` is unaffected by
  this decision and is not the mechanism being extended; it remains a
  separate, deterministic, non-LLM stopgap outside this pipeline.

## Rationale

Building a second execution mode was chosen over lowering or special-casing
the Phase 10 gate itself, because ADR 0023's fail-closed default exists
precisely to prevent exactly that kind of special-casing: any exception carved
into `require_publication_ready` risks becoming a de facto second definition
of "certified," undermining the single meaning ADR 0023 assigns to that gate.
Keeping Phase 10 entirely untouched and instead adding a differently gated
entry point for Phases 13-15 keeps the certified gate's semantics singular and
auditable.

Sharing generation logic between the certified and uncertified paths (rather
than writing a separate uncertified generator) was chosen to avoid the
opposite failure mode: a fork that drifts from the certified path's prompt,
schema, or parsing behavior over time would make the uncertified path an
unreliable preview of what certified generation will eventually produce, and
would double the maintenance surface for every future fix.

## Alternatives Considered

1. **Special-case `require_publication_ready` to accept an explicit
   "personal use" override flag.** Rejected: this would touch the same
   function that gates the certified path, creating exactly the kind of
   shared-code risk that could later allow an override flag to leak into a
   production invocation, whether by configuration error or by future
   refactor. Keeping the certified gate free of any conditional bypass was
   judged worth the cost of a second entry point.
2. **Extend `scripts/generate_personal_study_guides.py` instead of the real
   pipeline's Phase 13-15 generators.** Rejected: that script performs no
   inference and produces template-echoed questions
   (`src/lecture_study/study_materials/bundle.py:1390`); extending it would
   still not give the student LLM-backed exam, podcast, or TTS content, only
   a better template. It does not address the actual gap.
3. **Wait for ADR 0023's reference corpus and calibration authority before
   allowing any Phase 13-15 execution.** Rejected as the only path: the
   corpus and calibration authority are a substantial, open-ended effort with
   no committed timeline, and personal study material for a single student
   carries materially lower risk than clinical or published output. Blocking
   all use of Phases 13-15 until full certification exists was judged an
   unnecessary cost given that a bounded, clearly labeled, non-authoritative
   mode can be offered safely in the meantime.

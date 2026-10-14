# 0023. Content completeness, quality gates, and parallel execution

## Status

Accepted as the target architecture. Implementation is incremental and remains
incomplete until every mandatory gate in this ADR is enforced by executable
code and validated against the veterinary reference corpus.

**Stack note (ADR 0038):** the local-LLM baseline described below
(qwen3-32b/llama.cpp for semantic extraction, drafting, critic, exams,
podcast, flashcards) has been removed; those stages run as Codex CLI
generation scripts, and local execution survives only for Whisper/ASR,
the Kokoro-82M TTS voice (ADR 0032), and the llama-mtmd-cli vision
captioning binary. The completeness and
parallel-execution architecture itself is unaffected.


## Context

The primary product requirement is not short-form summarization. The system must
produce deep, analytical, study-ready material without silently omitting
instructional content from the source lecture.

The previous architecture focused on preventing unsupported claims. That is
necessary but insufficient: a short, fully grounded answer can still omit most
of a lecture. The existing Critic asks whether claims in a draft are supported,
but it does not ask whether every source concept that should have been retained
is present in the draft.

A real two-hour lecture in the current workspace exposes several blocking
failures:

- 1,952 transcription segments and 17,258 words were partitioned into 93
  frame-driven Evidence Packs.
- 90 packs are shorter than five minutes, only two fall in the required
  five-to-ten-minute range, one is 30.38 minutes long, and six contain no
  transcript segments.
- The largest pack contains 506 segments and 4,165 words. Its semantic prompt
  is approximately 7,799 Qwen tokens, while the worker is configured with a
  4,096-token context and reserves up to 1,024 tokens for generation.
- `SemanticFact.timestamp_ref` is required, but timestamps are not included in
  the semantic prompt.
- `SemanticLedger.facts`, `LearningUnitDraft.clinical_warnings`, and canonical
  document units may be empty under the current schemas.
- Assembly checks continuity relative to the units it happens to find, not
  relative to the complete upstream pack manifest. A missing tail can therefore
  look contiguous.
- Existing tests accept an empty canonical document and placeholder publication
  as successful behavior.

These are fail-open conditions. They permit structurally valid but academically
incomplete output.

A three-lecture pilot closed the narrow legacy prompt defect
without weakening the production boundary. Semantic prompts now retain all
transcript text while grouping it into 30-second blocks identified by an
absolute `source_sec`. Generated `timestamp_ref` values must fall inside a real
transcript segment or equal the anchor-frame timestamp. The prompt prohibits
using overlay clocks and dates as source timestamps, but the deterministic
validator establishes temporal membership only; it does not identify the
number's semantic origin. This supplies coarse legacy temporal traceability but
does not provide clause-level claim provenance, source completeness, or
production authority.

## Decision

### 1. Completeness is a blocking safety property

No lecture or derived artifact may be marked complete merely because a file
exists, JSON validates, or the text contains no unsupported claims.

Publication requires positive evidence for all of the following:

1. the source media is intact;
2. the complete media timeline has been classified;
3. every instructional source span belongs to a conceptual Learning Unit;
4. every atomic source content unit is represented in the canonical content
   ledger or explicitly classified as non-instructional, duplicate, ambiguous,
   or inaudible;
5. every final factual claim has at least one source citation;
6. every critical source content unit is present in the exhaustive study
   material;
7. no critic has an unresolved blocking finding;
8. every expected unit and artifact is present and matches its manifest.

Unknown completeness is failure, not success. The resulting state is
`needs_review`, never `complete`.

No automated system can truthfully guarantee perfect semantic completeness.
The architecture therefore provides a fail-closed evidence chain, multiple
independent evaluators, calibrated thresholds, and mandatory human review for
unresolved or high-risk cases.

### 2. Canonical content preserves both verbatim evidence and the atomic ledger

Prose is not the canonical representation of lecture content. Canonical content
has two coequal, immutable representations:

- content-addressed source evidence that preserves exact transcript and OCR
  bytes and the original encoded visual payloads; and
- a loss-resistant ledger of exhaustive atomic content units.

Neither representation may replace the other. Verbatim evidence preserves what
the source contained, while the ledger preserves the explicit analytical model
used for completeness accounting. Before producing study-guide prose, the
pipeline resolves the exact source evidence and creates the atomic ledger.

Each atomic content unit records at least:

- stable identifier;
- lecture and conceptual Learning Unit identifiers;
- content type: definition, mechanism, causal relation, diagnosis, differential
  diagnosis, sign, examination, treatment, procedure, dose, contraindication,
  red flag, prognosis, comparison, exception, example, practical observation,
  uncertainty, or conclusion;
- normalized statement;
- source transcript segment identifiers;
- exact start and end timestamps;
- source slide/frame identifiers;
- verbatim evidence excerpts where permitted;
- species, breed, life stage, and clinical context when present;
- medication, value, unit, route, frequency, and duration when present;
- polarity, negation, uncertainty, and recommendation strength;
- extraction confidence;
- disagreements between ASR, OCR, or evaluators;
- review status.

Deduplication may merge equivalent normalized statements, but it must retain all
distinct evidence links. Compression must never destroy provenance.

### 3. Learning Units are conceptual, not frame-driven

Frames are visual evidence. They do not define Learning Unit boundaries by
themselves.

Conceptual segmentation combines:

- transcript semantic coherence;
- discourse transitions;
- pauses;
- slide-title and OCR changes;
- visual scene candidates;
- duration constraints;
- overlap at boundaries to preserve cross-boundary reasoning.

The target duration is five to ten minutes. Shorter or longer units require an
explicit reason stored in the unit manifest. A unit that cannot fit its complete
evidence and required output inside the configured model context must be
subdivided before inference. Prompt truncation is forbidden.

The original transcription segments remain immutable. Boundary overlap may copy
context references into adjacent units, but each source segment has exactly one
primary owning unit.

### 4. Long-form generation uses evidence-aware hierarchical refinement

The pipeline is:

```text
source media
  -> verified transcript and OCR
  -> multimodal timeline
  -> conceptual Learning Units
  -> atomic source content ledger
  -> per-unit exhaustive synthesis
  -> per-unit independent critics
  -> cross-unit reconciliation
  -> canonical exhaustive study material
  -> shorter derivative products
```

The exhaustive study material is distinct from:

- exam-focused review;
- flashcards;
- podcast;
- executive overview.

A two-paragraph overview may be generated as a navigation aid. It must never
replace the exhaustive material.

Hierarchical processing preserves the atomic ledger at every level. A merger
does not recursively summarize summaries without access to the original
evidence. Per-unit synthesis and cross-unit reconciliation must be able to
retrieve the supporting transcript and slide evidence.

This follows evidence that long-input models do not use all context positions
uniformly and that multistage processing is more reliable than a single
overlong prompt:

- Liu et al., *Lost in the Middle: How Language Models Use Long Contexts*:
  <https://arxiv.org/abs/2307.03172>
- Zhang et al., *SummN: A Multi-Stage Summarization Framework for Long Input
  Dialogues and Documents*:
  <https://aclanthology.org/2022.acl-long.112/>
- Kim et al., *FABLES: Evaluating Faithfulness and Content Selection in
  Book-Length Summarization*:
  <https://arxiv.org/abs/2404.01261>

### 5. Automatic evaluation is bidirectional and plural

The same model may not be the sole generator and sole approver.

Independent critics execute in parallel after a unit draft is created:

#### 5.1 Source-to-material completeness

Every atomic content unit in the source ledger is searched for in the material.
This is a recall direction:

```text
source content -> generated material
```

The evaluator reports represented, partially represented, contradicted,
missing, and unverifiable units. Critical omissions always block publication.

Atomic Content Unit recall is based on the content-unit approach evaluated by
RoSE:

- Liu et al., *Revisiting the Gold Standard: Grounding Summarization
  Evaluation with Robust Human Evaluation*:
  <https://aclanthology.org/2023.acl-long.228/>

#### 5.2 Material-to-source faithfulness

Every factual claim in the material must be supported by its cited evidence.
This is a precision direction:

```text
generated material -> source content
```

At least two complementary methods are used:

- sentence/claim-level entailment;
- question-answer-based verification.

Relevant primary references:

- Laban et al., *SummaC*:
  <https://aclanthology.org/2022.tacl-1.10/>
- Fabbri et al., *QAFactEval*:
  <https://aclanthology.org/2022.naacl-main.187/>
- Zha et al., *AlignScore*:
  <https://aclanthology.org/2023.acl-long.634/>

#### 5.3 Bidirectional question answering

Questions generated from the source test whether the final material contains
the answers. Questions generated from the final material test whether those
answers exist in the source. The two directions must be reported separately;
one score must not hide low recall behind high precision.

#### 5.4 Veterinary clinical field audit

A deterministic structured critic compares:

- species, breed, and life stage;
- diseases and differential diagnoses;
- drugs and procedures;
- dose, unit, route, interval, and duration;
- reference values and thresholds;
- negations and contraindications;
- direction of comparisons and causal relations;
- certainty, exceptions, and red flags.

Terminology resources such as SNOMED CT Core, VetSCT, and UMLS may support
normalization. Term overlap is not proof that the clinical relationship,
negation, or dosage is correct.

#### 5.5 Traceability audit

Every factual sentence in exhaustive material carries one or more source
references. Every reference must resolve to an existing transcript interval or
slide artifact and fall inside the owning Learning Unit's evidence window.

#### 5.6 Density sanity check

Length is not a substitute for completeness. Nevertheless, gross
underproduction is a blocking anomaly.

The system records:

- instructional minutes;
- atomic units per minute;
- final words per instructional minute;
- final words per atomic content unit;
- unit-level and lecture-level compression ratios.

Thresholds are calibrated from the veterinary reference corpus. Until
calibrated, density is an alarm requiring review, not proof of quality. A
two-hour lecture producing two paragraphs must fail the alarm regardless of
other scores.

#### 5.7 Human calibration

Automatic judges must be calibrated against expert annotations before their
scores can approve high-risk content. Human review remains mandatory for:

- medication doses;
- contraindications;
- red flags;
- low-confidence ASR/OCR;
- evaluator disagreement;
- unresolved omissions;
- benchmark samples used to promote a new model or tool.

A pending, unavailable, rejected, or inconclusive human review must not abort
the lecture-wide execution process. It blocks certification, publication, and
derivative release only for the affected content scope, which moves to
`needs_review` or quarantine with complete provenance. Independent Learning
Units, lectures, deterministic validations, and other tasks whose dependency
graphs do not include the blocked scope continue to execute. No downstream
artifact may bypass or weaken the blocked review dependency.

Research on long-form and medical summarization shows that automatic metrics
and LLM judges do not reliably replace domain experts:

- Kim et al., FABLES:
  <https://arxiv.org/abs/2404.01261>
- Gao et al., *FactPICO*:
  <https://aclanthology.org/2024.acl-long.459/>

### 6. Publication gate

The publication gate consumes immutable completion certificates:

- `media_certificate`;
- `transcript_certificate`;
- `timeline_certificate`;
- one `unit_certificate` per expected Learning Unit;
- `lecture_certificate`;
- `delivery_certificate`.

Certificates are authorization artifacts, not self-asserted JSON envelopes. Every
publication, derivative, and delivery certificate must be authenticated by runtime
trust material that is not stored in the workspace. Verification must reject a
coordinated rewrite of reports, certificates, and manifests when the writer does not
possess that trust material.

The production issuer accepts only the subject identity or the typed candidate
artifact. It must not accept caller-provided approval booleans, report hashes,
calibration flags, or findings. Missing evaluator implementations produce a
non-authoritative diagnostic attempt and keep the pipeline closed.

LLM derivatives have an additional boundary. A valid lecture certificate does not
authorize an arbitrary exam or podcast script. The exact serialized derivative
requires independent approvals for:

- source coverage;
- source faithfulness;
- analytical depth;
- clinical safety;
- traceability; and
- critic resolution.

Only after those approvals may the compiler write the derivative. The final delivery
manifest then binds the source certificate, derivative certificate, execution
identity, ordered output set, byte sizes, and output hashes.

The generator-to-evaluator boundary is also authenticated. Each LLM-backed derivative
candidate carries a generation receipt that binds its exact canonical payload, artifact
kind, lecture identity, source certificate, canonical document, execution identity,
key identity, and signature. Public construction or payload mutation must not create a
candidate that can be evaluated as genuine.

A model path is not a sufficient execution identity. The production resolver must:

- reject symlinks, hardlinks, non-regular files, malformed GGUF metadata, and incomplete
  split-model sets;
- fingerprint the in-memory generator implementation and live prompt/schema constants;
- calculate the model identity from the opened source file; and
- execute inference against an immutable read-only snapshot of that same source.

Runtime quality secrets must be stripped from every external subprocess environment.
Derivative delivery manifests use a closed artifact-kind schema: deterministic outputs
must not claim derivative lineage, while exams, podcast scripts, and podcast audio must
bind the exact source and derivative certificate chain. Audio additionally binds the
exact certified script payload and audio runtime identity.

The lecture gate requires:

- complete expected unit set;
- no unclassified timeline gap;
- no prompt truncation;
- no missing or invalid evidence reference;
- no unsupported final claim;
- no missing critical source content unit;
- no unresolved critic;
- no stale artifact accepted under a different model, prompt, schema, code
  revision, or configuration;
- successful deterministic schema and hash validation.

Repair is targeted to failed units or claims. Passing units are not regenerated.
After bounded repair attempts, unresolved content moves to quarantine and
requires review.

### 7. Parallel execution is organized as a resource-aware DAG

Maximum safe parallelism is required, not maximum process count.

The execution DAG permits:

- media probe, audio extraction, frame extraction, and metadata work where
  dependencies allow;
- ASR and OCR on independent resources;
- per-unit ledger extraction after the multimodal timeline is complete;
- per-unit synthesis after its ledger passes;
- completeness, faithfulness, clinical-field, citation, and density critics in
  parallel;
- parallel repair of independent units;
- parallel derivative generation after the canonical document passes.

Resource pools are separate:

- I/O;
- CPU;
- Apple Vision;
- FFmpeg;
- Whisper/ANE;
- Metal LLM;
- publishing/TTS.

Every queue is bounded and uses backpressure. The dispatcher uses fair
round-robin scheduling across lectures so a long lecture cannot monopolize all
slots. Repairs receive bounded priority.

Initial safe limits for the current M5 Pro are benchmark hypotheses, not fixed
truth:

| Pool | Initial limit |
|---|---:|
| Active lectures | 4 |
| FFmpeg-heavy tasks | 2 |
| Apple Vision OCR | 4 |
| CPU analysis | 8 |
| Whisper | 1 |
| LLM generation | 1 |
| Publishing and TTS | 2 |

Candidates such as resident `llama-server`, continuous batching, two LLM slots,
Whisper Core ML/ANE, and concurrent Whisper/LLM execution are promoted only
when the golden corpus shows no quality regression, no prompt truncation, no
sustained swap growth, and a meaningful throughput improvement.

### 8. Workspace is lecture-centric and artifact-versioned

New writes use:

```text
workspace/
  catalog/
  lectures/
    <discipline_id>/
      <lecture_id>/
        lecture.json
        source/
          documents/
        extraction/
        transcription/
        evidence/
          documents/
        analysis/
          timeline/
          learning-units/
          content-ledger/
        synthesis/
          drafts/
          reviews/
          validated-units/
          canonical/
        quality/
          certificates/
          coverage/
          faithfulness/
          clinical/
          traceability/
          unresolved/
        outputs/
          study-guide/
          flashcards/
          exams/
          podcast/
        manifests/
          tasks/
          stages/
          runs/
        state/
          partial/
          quarantine/
  runs/
```

Artifacts are immutable and addressed by an idempotency key derived from:

```text
input hashes
+ stage schema version
+ code revision
+ engine version
+ model hash
+ prompt hash
+ canonical parameters
```

`Path.exists()` is not a resume contract. Reuse requires a committed manifest,
matching idempotency key, valid output hashes, schema validation, and successful
quality gates.

The current numbered `workspace/data/` tree remains untouched until a
non-destructive migration is tested. Migration proceeds per lecture through
inventory, shadow manifests, dual-read support, validation, cutover, and
rollback. No big-bang move or deletion is permitted.

### 9. Tool selection is empirical

The existing local-first stack remains the baseline:

- `whisper.cpp`;
- Apple Vision;
- FFmpeg;
- `llama.cpp`;
- Qwen2.5 14B.

Research supports retaining these tools as viable local components, but it does
not prove they are the best for Portuguese veterinary lectures on this
hardware.

The golden-corpus benchmark must compare at least:

- Whisper `large-v3` against `large-v3-turbo`;
- Apple Vision against an independent multilingual OCR challenger;
- FFmpeg scene candidates against adaptive or learned shot detection;
- multimodal conceptual segmentation variants;
- Qwen2.5 14B against at least one current 12B-14B multilingual challenger;
- `llama-cli` against resident `llama-server`;
- same-model Critic against an independent judge family.

Primary tool references:

- Whisper:
  <https://arxiv.org/abs/2212.04356>
- `whisper.cpp`:
  <https://github.com/ggml-org/whisper.cpp>
- Apple Vision text recognition:
  <https://developer.apple.com/documentation/vision/recognizetextrequest>
- TransNet V2:
  <https://arxiv.org/abs/2008.04838>
- Qwen2.5:
  <https://arxiv.org/abs/2412.15115>
- `llama.cpp`:
  <https://github.com/ggml-org/llama.cpp>

The winner is selected on the quality-throughput Pareto frontier with critical
quality thresholds treated as constraints. Faster output that loses content is
disqualified.

### 10. Reference corpus and acceptance metrics

The required future reference corpus must contain short, medium, and
approximately two-hour lectures, including slide-heavy, talking-head,
noisy-audio, terminology-dense, table-heavy, and incremental-slide examples.
No such promoted corpus exists in the current repository.

Veterinary experts annotate representative spans for:

- transcript text;
- conceptual boundaries;
- atomic content units;
- terminology;
- doses and units;
- red flags;
- evidence citations;
- required study-guide coverage.

Minimum metric families:

| Layer | Metrics |
|---|---|
| ASR | WER, CER, deletion rate, technical-term recall, number/unit recall, temporal gaps |
| OCR | CER, term recall, table/number recall, cross-engine disagreement |
| Segmentation | Pk, WindowDiff, hierarchical boundary quality, duration compliance |
| Content ledger | atomic-unit recall, critical-unit recall, evidence precision |
| Study material | atomic-unit coverage, claim faithfulness, QA precision/recall, clinical-field accuracy |
| Traceability | citation precision, citation recall, temporal correctness |
| Performance | wall time, throughput, queue latency, RSS, swap, retry cost |
| Recovery | idempotent reuse, kill/restart integrity, stale-artifact rejection |

No framework, model, or evaluator is promoted using generic benchmarks alone.

### 11. Authoritative source-universe contract

The first executable boundary of the target architecture is the immutable
`SourceUniverse`. It is a prerequisite for semantic extraction, not evidence
that semantic extraction is complete.

The source universe records:

- content-addressed transcript, OCR, and visual evidence spans;
- an explicit disposition for every span;
- an exact classified partition of the complete media timeline;
- conceptual Learning Units that tile the complete media timeline; and
- one primary owning Learning Unit for every instructional source span.

OCR and visual evidence may be point evidence with equal start and end
timestamps; transcript evidence must have positive duration. Timeline intervals
and Learning Units remain positive-duration windows. Boundary context does not
change primary ownership and is accepted only inside an explicitly configured
temporal extension. The fail-closed default extension is zero until a bound
policy defines another value.

A span classified as duplicate must point directly to a non-duplicate source
span with the same content hash. Duplicate chains, cycles, unresolved targets,
and distinct-content substitutions are blocking failures.

Resolved timeline classification is derived from the referenced source spans.
An interval is instructional if and only if at least one referenced span is
instructional; otherwise it is non-instructional. A declared interval
classification that contradicts this aggregation rule is a blocking failure.
Ambiguous and inaudible intervals remain unresolved and cannot pass this rule.

Partition gap and overlap metrics are calculated only inside the closed media
window. Duration beyond the media boundary is reported separately instead of
being misrepresented as an internal overlap. The declared classified duration
must also satisfy the partition identity:

```text
classified duration
  = expected duration - gap duration + overlap duration
    + outside-media duration
```

Gap duration must be strictly smaller than expected media duration because a
positive interval count implies positive internal coverage. Metric feasibility
also requires:

```text
overlap duration
  <= (interval count - 1) * (expected duration - gap duration)
```

This bound rejects metric combinations that satisfy the duration identity but
cannot describe any possible collection of intervals, even when a declared
numeric tolerance is large. Persisted integers use a signed 64-bit upper bound:
it is portable across storage implementations, sufficient for byte and count
metrics in this pipeline, and safely below Python's JSON integer-conversion
limit. The overlap bound is evaluated as an overlap-to-coverage ratio, which
also avoids unsafe integer-to-float multiplication.

The deterministic `SourceUniverseAuditor` blocks:

- timeline or Learning Unit gaps and overlaps;
- timeline or Learning Unit duration outside the media boundary;
- aggregate partition loss, even when every individual gap is below the numeric
  boundary tolerance;
- missing, duplicated, or unresolved timeline evidence references;
- resolved timeline classifications that contradict their referenced spans;
- ambiguous or inaudible evidence that still requires resolution;
- instructional spans with missing or duplicate primary ownership;
- non-instructional or unknown primary references;
- source spans that cross their timeline or primary-unit boundary; and
- context spans outside the configured boundary extension; and
- Learning Units outside the five-to-ten-minute policy without an explicit
  reason.

`TimelineClassificationEvidence` and `LearningUnitCompletenessEvidence` are
typed payloads in the gate-evidence envelope. The envelope can check only the
internal consistency of its self-declared metrics; it exposes no publication
eligibility property. Its presence does not authorize publication. The
production issuer remains fail-closed until it can resolve, authenticate, and
recompute the bound source-universe artifacts and trusted policy values.

### 12. Atomic source-content ledger contract

The atomic ledger is a new versioned domain boundary. It does not extend the
legacy `SemanticLedger`, whose entity-oriented, frame-pack contract remains
insufficient for exhaustive content analysis.

Each conceptual Learning Unit has one non-empty `AtomicContentLedger`. Its
propositions preserve:

- closed content type, importance, polarity, certainty, recommendation
  strength, safety classification, analytical dimensions, and review state;
- exact primary and contextual source-span references;
- exact source-clause evidence bindings with modality, selector, generated
  observation identity when applicable, excerpt hash, and authoritative
  source-record hash;
- mutually exclusive evidence text: authenticated transcript text uses
  `verbatim_text`, while OCR and generated visual observations use
  `observed_text` and can never be labeled verbatim;
- typed clinical fields and decimal-preserving numeric quantities with exactly
  one clause-bound `NumericSourceBinding` per quantity;
- unresolved or human-declared source/evaluator disagreements; and
- evidence-bearing typed relations between propositions.

The deterministic `AtomicContentLedgerAuditor` derives the expected universe
directly from a validated `SourceUniverse` and the exact
`SourceClauseInventory`. It requires the exact Learning Unit set, complete
primary instructional-span representation, valid lecture and unit identity,
current source hashes, primary evidence for every proposition, one exact
source binding for every numeric quantity, a verified review state, closed
relation endpoints, and no duplicate semantic edges. Every evidence binding must resolve
to one exact source clause and match that clause's source span, modality,
source-content hash, full-frame or regional selector, generator identity, and
text identity. Multiple distinct clauses from one source span may support one
proposition, and one exact clause may support multiple distinct atomic
propositions when every excerpt repeats the same authenticated binding. Every
declared proposition source span must have at least one bound clause. Reusing a
clause identifier with a different span, hash, selector, generator, or text is
a blocking substitution.

Observed OCR and visual evidence uses the modality-neutral
`GeneratedObservationIdentity`; authenticated transcript evidence has no
generator identity. The Learning Unit-local inventory audit report binds the
canonical `clause_inventory_sha256` in addition to the lecture, Learning Unit,
and source-universe identities. Reconciliation internally recomputes this
report from the current authoritative Source Universe and exact inventory; it
does not accept a report supplied by the caller. It rejects a stale,
substituted, mismatched, or structurally blocked dependency and independently
revalidates every ledger excerpt against the exact clause.

Decimal-token equality does not prove comparator, boundary, role, unit,
denominator, or range-expression identity. Every `NumericQuantity` therefore
requires exactly one `NumericSourceBinding` to an exact source clause.
`NumericQuantity` stores `lower_boundary`, `upper_boundary`, and
`source_binding`. `ClauseUtf8ByteRange`, `NumericImplicitJustification`, and
`NumericSourceComponentBinding` provide the value objects for that binding. It
preserves the exact expression and one component contract for the expression,
lower value, upper value, comparator, unit, denominator, semantic role, lower
boundary, and upper boundary, including `lower_boundary_selector` and
`upper_boundary_selector`. Every component carries:

- a `NumericBindingComponentState` of `explicit`, `implicit`, or `absent`;
- a clause-relative selector when and only when the component is explicit; and
- a traceable justification when and only when the component is implicit.

All selectors are non-empty, half-open byte ranges `[start, end)` relative to
the UTF-8 bytes of the clause's canonical text. They must remain wholly inside
the clause and select exactly the declared component bytes. An absent component
cannot carry a selector. An implicit component cannot invent source text and
cannot become authenticated proof merely because it has a justification.
Every explicit component selector must be contained by the complete
`expression_selector`; a token elsewhere in the same clause cannot demonstrate
the role, comparator, unit, denominator, value, or boundary of the bound
quantity. Selector overlap inside one binding is rejected unless it is
semantically unavoidable and explicitly validated by the numeric audit.
Inter-quantity selector overlap fails closed because no authenticated contract
currently exists to authorize an otherwise unavoidable overlap between two
quantities. The check is aggregate across every quantity in one independent
candidate batch and across every proposition in the lecture ledger audit; it
cannot be bypassed by splitting colliding quantities between candidates or
propositions.

`NumericBoundaryKind` preserves `inclusive`, `exclusive`, `unbounded`, and
`not_applicable` as distinct states. A range binds each present delimiter
independently, so `[10, 20)`, `(10, 20]`, `[10, 20]`, and `(10, 20)` cannot
collapse to one representation. A unilateral comparator keeps its exact
comparator selector and represents the missing side as unbounded. Exact values
use not-applicable boundaries. Inclusivity, exclusivity, conversion, unit
normalization, and semantic role cannot be inferred without demonstrable source
evidence.

The exact `EvidenceExcerpt` text must preserve the complete canonical clause
context. A coordinated transformation that changes a clinical subject,
population, treatment, condition, or other clause context while preserving the
numeric token is `UNVERIFIABLE`; numeric-token equality cannot conceal the
context change. Unit identity is case-sensitive, so clinically distinct source
tokens cannot be normalized through case folding. Comparator and semantic-role
recognition uses the explicitly versioned deterministic lexicon
`numeric-source-binding-en-ptbr-v1`, which contains supported English and
Brazilian Portuguese forms without weakening exact selected-byte identity.

The surrounding ledger and reconciliation audits first bind the exact
`SourceClauseInventory` to the current authoritative `SourceUniverse`.
`NumericSourceBindingAuditor` then resolves each numeric binding against that
inventory and the proposition's exact `EvidenceExcerpt`. It simultaneously
revalidates clause identity, source-span identity, modality,
`source_content_sha256`, canonical clause text, the clause's integral selector,
generated-observation identity when applicable, semantic role,
decimal-preserved values, comparator direction, lower and upper boundaries,
unit, denominator, and related clinical context. Transcript bindings use
authenticated `verbatim_text`; OCR and visual bindings use `observed_text` and
never become verbatim evidence. The original authenticated bytes remain
canonical.

`NumericSourceBindingAuditor` emits a `NumericSourceBindingAuditReport`.
`NumericBindingDisposition.VERIFIED` maps to
`NumericBindingScopeState.SATISFIED` and may satisfy only its local numeric
structural dependency. A necessary component that cannot be demonstrated emits
`NumericBindingDisposition.UNVERIFIABLE` and maps the dependent scope to
`NumericBindingScopeState.NEEDS_REVIEW`. Invalid, rejected, substituted, or
falsified numeric evidence emits `NumericBindingDisposition.INVALID` and maps
that scope to `NumericBindingScopeState.QUARANTINE`. Neither state aborts
independent Learning Units, lectures, deterministic validations, or DAG tasks.
An authenticated review cannot clear an invalid numeric binding from
quarantine; corrected source-bound evidence and a new audit are required.
Certification, publication, delivery, and derivatives whose dependency graph
includes the affected scope remain blocked, and no downstream consumer may
ignore or weaken that dependency.

The adversarial contract rejects value, lower-limit, upper-limit, scale, unit,
denominator, comparator-direction, interval-boundary, duration, frequency, and
semantic-role mutations; unsupported numeric conversions; selector
out-of-bounds, empty-selector, byte-mismatch, state/selector, and
implicit-justification violations; wrong clause, modality, hash, observation,
or source identity; extra unbound source spans; post-construction selector
overlap; and reuse across clauses or modalities. Clause-level numeric-token
closure also places a source number omitted from every candidate or ledger
quantity into `needs_review` instead of allowing coordinated context erasure.
Its coverage includes single values, open and closed ranges, unilateral limits,
decimal points and decimal commas, slash-, `per`-, and `por`-based compound
units, mass- and area-based doses, percentages, frequencies, intervals,
durations, textual comparators, implicit and genuinely absent components,
multiple quantities in one clause, allowed component-local overlaps,
fail-closed inter-quantity overlaps, transcript, OCR, and visual observations. Text equality alone cannot verify the
binding. This contract does not prove perfect numeric or semantic completeness,
and publication remains blocked.

This audit proves structural closure only. A valid span reference does not prove
that the referenced evidence entails the proposition or that every clause in
the span was extracted. Human-resolution identifiers and review states are also
declarations until an authenticated resolver verifies them. Consequently the
report exposes `structurally_complete` separately and
`ready_for_atomic_content_gate` remains unconditionally false. Publication
stays blocked until independent source-to-ledger reconciliation, source-content
resolution, reviewer authentication, and calibration exist.

### 13. Authenticated source resolution and independent clause reconciliation

Source-content resolution is an application boundary with explicit outward
adapters. The caller supplies all resource limits, the artifact root, the exact
manifest identity and path, and the source-manifest trust implementation. No
production default, path derivation, legacy-layout fallback, or alternate
artifact substitution is permitted.

The application boundary depends on the inward `SourceArtifactReader` port.
`RootedSourceArtifactReader` adapts the infrastructure-facing
`RootedArtifactReadStore` protocol to that port. Source-manifest and resolver
logic do not import the delivery storage implementation, so the existing rooted
store remains an injected outward detail rather than an inward dependency.

An authenticated source manifest binds each lecture, artifact, record,
modality, byte range, media type, whole-file digest, and exact record digest.
It uses a dedicated HMAC key and a source-manifest-only cryptographic domain and
purpose. That authority authenticates only source identity, location, and
integrity. Its key, signature, and decision cannot authorize certification,
publication, or delivery and cannot be substituted for publication trust.
Exactly one `AuthenticatedSourceManifest` is authoritative for one lecture in
this schema version. `HmacSourceManifestCatalog` is the only issuer of a
`VerifiedSourceManifest` capability. A signed manifest model constructed,
copied, substituted, or manually allocated by a caller carries no authority.
The capability preserves the exact verified manifest and is revalidated by its
issuer before every critical consumption. Zero manifests, multiple manifests
for one lecture, capability copying, manifest replacement after verification,
and cross-lecture or cross-manifest replay fail closed.
The resolver verifies the complete artifact before slicing the record, then
verifies the record bytes against both the manifest and the authoritative
`SourceEvidenceSpan`. Transcript and OCR records use strict UTF-8 without
normalization and retain their original bytes. Visual records retain their
original encoded bytes and do not claim decoded text. Descriptor-relative
reads reject traversal, aliases, links, non-regular files, concurrent mutation,
missing artifacts, and configured size or count limit violations.

The source-content aggregation identity covers every authenticated source
record in the lecture, not only records cited by a reviewed clause,
proposition, or batch. Each canonical record contains exactly:

- `lecture_id`;
- `source_artifact_id`;
- `artifact_relative_path`;
- `source_record_id`;
- `modality`;
- `byte_offset`;
- `byte_length`; and
- `content_sha256`.

Records are ordered by `source_artifact_id`, `artifact_relative_path`,
`source_record_id`, `byte_offset`, `byte_length`, and `content_sha256`, in that
order. The domain-versioned canonical JSON serialization uses sorted object
keys, UTF-8, no insignificant whitespace, and rejects non-finite or ambiguous
values. Manifest metadata, signatures, key identifiers, signature domain and
purpose, and the whole-file `artifact_sha256` remain outside this semantic
identity and retain their separate cryptographic identities. A reviewed subset
cannot satisfy the complete lecture aggregation identity. Before hashing,
composition projects the complete record universe from the current
`VerifiedSourceManifest` and requires exact set equality with the supplied
`SourceRecordProvenance` records. Missing, additional, duplicate, substituted,
wrong-lecture, wrong-manifest, path, record-identity, modality, byte-range, or
content-hash differences are rejected. Only the catalog-authenticated complete
set can produce a reviewable source aggregation.

Every textual source extent in a conceptual Learning Unit is partitioned into
ordered, non-overlapping clauses with no byte gaps. Every authoritative visual
span that is instructional and belongs to the Learning Unit's
`primary_source_span_ids` requires at least one generated full-frame observation
and may include additional region observations. Context-only visual spans are
not reprocessed. Ambiguous, inaudible, unresolved, or unclassified spans remain
blocked by the `SourceUniverse`. The original encoded frame bytes are the only
verbatim visual evidence. OCR text, descriptions, interpretations, and
generated observations are never labeled verbatim. Every generated visual
observation is bound to the original frame digest and, when region-scoped, to
an exact selector wholly inside the authenticated frame. Region observations
cannot replace the mandatory full-frame observation.

Every generated full-frame or regional visual observation additionally carries
one individual `AuthenticatedVisualObservationReceipt`. The receipt binds its
schema and receipt identities, invocation, lecture, Learning Unit, exact
`source_span_id`, original-frame digest, full-frame or regional selector,
generator identity, and observed-text digest. It is signed over the complete
canonical receipt payload, excluding only the detached signature, under the
`lecture-study.visual-observation.v1` domain and
`source-span-visual-observation` purpose. `VisualObservationHmacKey` and
`VisualObservationHmacAuthority` are dedicated to this boundary and are
separate from source-manifest, human-review, certification, publication,
delivery, and derivative trust.

A receipt-shaped model is not authentication. Only an issuer-created,
currently revalidated visual-observation capability is accepted at the
inventory boundary. Every observation has its own receipt; duplicate receipt
or invocation identifiers, missing or substituted receipts, copied or manually
forged capabilities, expired or unverifiable receipts, any receipt-field
mutation, replay across lectures, units, spans, identical-frame spans, frames,
regions, generators, observed text, or invocations, and trust-domain,
purpose, key, or authority confusion fail closed. A regional receipt remains
bound to the exact authenticated full frame and cannot replace the independently
receipted mandatory full-frame observation. The receipt authenticates only that
visual observation and cannot authorize review, certification, publication,
delivery, or a derivative.

HMAC authentication alone does not establish global receipt or invocation
uniqueness. `VisualObservationReceiptRegistry` is therefore a mandatory inward
port for the complete visual trust scope:
`(cryptographic_domain, signature_purpose, trust_key_id, authority_id)`. Within
that scope, `receipt_id` and `invocation_id` are one joint reservation. Both
identifiers must be reserved atomically and permanently before signing, and the
exact reservation must be confirmed after signing. Verification and capability
consumption require that same confirmed reservation. A conflict, missing
reservation, payload mismatch, unavailable registry, or interrupted confirmation
fails closed; it cannot be treated as an HMAC-valid observation.

The visual registry is append-only, persistent across authority reconstruction
and process restart, and authoritative across concurrent processes that share
the same trust scope. Reservation removal, identifier reuse, process-local
fallback, and reconstructing authority from HMAC material without the registry
are forbidden. The caller must inject the exact registry root. There is no
production default, artifact-root derivation, environment fallback, or implicit
temporary registry.

Invocation expiration is also an append-only trust-scoped registry event.
Reconstructed authorities must reject a receipt whose exact invocation was
expired after issuance; process-local expiration state is insufficient.

`ClauseReconciliationAuditor` exposes explicit injected ports for the dedicated
visual authority and verified receipt capabilities and passes them to its
mandatory clause-inventory dependency. Missing authority or receipts remain
fail-closed and cannot be bypassed by downstream reconciliation.

Each `SourceTextExtent` retains the exact canonical transcript or OCR text,
validates its UTF-8 byte length and SHA-256 identity, and is the authenticated
byte source for clause partitioning. Inventory validation slices those exact
bytes for every clause and rejects coordinated same-length substitutions even
when a caller changes every clause-local text and digest consistently.

The deterministic inventory auditor compares every declared text extent,
visual extent, and clause to the authoritative `SourceUniverse` span by
Learning Unit ownership, modality, and content hash. A missing mandatory visual
extent is `needs_review`. Invalid, substituted, rejected, or replayed evidence
is quarantined. An invalid inventory whose identity cannot be revalidated is
reported under an unresolved Learning Unit identity rather than attributing
quarantine to an untrusted identifier. Independent inventories continue.

At least two independently identified candidate batches must observe the exact
clause inventory. The batches must use distinct producer identifiers and
distinct model hashes; one producer cannot approve its own observations.
Candidate contracts preserve one ordinary predicate per atomic proposition,
explicit capture states, polarity, certainty, recommendation strength,
quantities and units, an indivisible dose regimen, comparison direction, causal
direction, conditions, exceptions, corrections, contraindications, red flags,
and exact relation provenance. Candidate, predicate, clinical-value, quantity,
comparison, and relation projections preserve their canonical
`source_clause_ids`. Candidate relations must declare the exact canonical union
of the source clauses carried by both endpoints. Final ledger relations must
declare the exact union of endpoint source spans independently derived from
their authenticated evidence. Endpoint-only, omitted, extra, or substituted
relation provenance is blocking.

Deterministic reconciliation accounts for every candidate observation, every
candidate proposition, every final-ledger proposition, and the complete final
relation graph. Every clause receives exactly one closed outcome:
`represented`, `partial`, `contradicted`, `missing`, or `unverifiable`. Any
outcome other than `represented`, any independent-candidate disagreement, any
accounting mismatch, or any semantic projection mismatch is blocking.

Human-review evidence is a separate external trust boundary. Critical review
authorization is required for every exact `(disagreement_id, proposition_id)`
pair. A signed aggregate decision is valid only when its canonical payload
enumerates and cryptographically binds every covered pair individually. Lecture,
clause, and batch scope never imply authorization for an omitted pair. The
evidence also binds separate source-content aggregation, clause-inventory,
candidate-batch, atomic-ledger, and reconciliation identities together with the
policy, calibration, authority sequence, review version, validity window, and
detached signature. No identity from one domain may be reused as the identity
of another. Before any decision is accepted, all five content identities are
recomputed from the complete current authoritative source-record set, exact
inventory, exact candidate batches, exact ledger, and exact reconciliation set.
A caller-provided digest without those current artifacts cannot authorize review.

`VerifiedHumanReview` and `HumanReviewScopeAssessment` are verifier-issued
results rather than publicly constructible approval records. At each downstream
dependency boundary, the assessor recomputes the exact current review scope and
revalidates the submitted raw evidence through the currently injected external
authority. A previously issued result cannot bypass a changed signature
decision, revocation state, authoritative artifact, or required pair set. Only
a previously satisfied assessment may be revalidated during dependency
consumption. A `needs_review` or quarantine assessment remains blocked even if
external authority state later changes; recovery requires a fresh assessment,
and quarantine is never released by review.

Durable quarantine uses
`lecture-study/human-review-quarantine-evidence/v1` as its exact identity
domain. `canonical_human_review_evidence_sha256` includes every validated
`HumanReviewEvidence` field except `detached_signature`; no other field is
excluded. Re-signing the same payload therefore does not rehabilitate
quarantined evidence. A subsequent assessment is fresh only when it has a
genuinely new canonical payload base, a new `review_version`, and a new detached
signature. Changing only the signature, only the version, or only the external
authority decision cannot release quarantine.

`HumanReviewQuarantineRegistry` is the mandatory inward port for this durable
negative trust state. A quarantine-eligible rejected, invalid, spoofed, or
falsified assessment appends the canonical evidence identity, exact review
scope and payload-base identities, observed-signature digest, reason, sequence,
and preceding-record identity. The append is permanent. There is no removal,
release, administrative overwrite, or authority-driven rehabilitation path.
Every new verification consults the registry before and after external
authentication, and every previously issued `VerifiedHumanReview` consults it
again at its consumption boundary. A matching quarantine record therefore
revokes the capability's ability to authorize dependent work even when the
capability was issued before the quarantine.

Caller-local requirement, artifact, or assessment inconsistency is not evidence
that the signed external payload is invalid. Such inconsistency blocks the
local assessment without appending the evidence identity to quarantine.
Verification and dependency consumption each load exactly one trusted
current-state and clock snapshot. Evidence validity is checked against that
snapshot before a caller-declared mismatch is classified; therefore truly
stale or revoked evidence remains quarantine-eligible, while a forged local
assessment or a one-second caller/clock boundary cannot poison authentic
evidence. Type, issuer-authenticity, and post-issuance-integrity failures of a
local `VerifiedHumanReview` capability are likewise local capability failures;
they block consumption without quarantining the authentic evidence embedded in
the forged or altered wrapper. Issuer identity is checked before any mutable
public field is read; subsequent structural or serialization failure is
translated to the dedicated capability error rather than escaping as an
untyped exception. The assessor applies the same protected extraction to all
locally issued assessment fields, so a missing state, requirement, reason, or
capability field becomes a local blocked assessment instead of an untyped
failure or durable evidence quarantine.

External authorities receive a validated defensive evidence copy and the
original signed payload, never the evidence object retained for issuance.
Dependency consumption obtains the issued evidence directly from the
issuer-only capability registry before comparing mutable assessment state.
Consequently, an attacker-selected submitted evidence cannot redirect a real
expiry or revocation quarantine, and authority-side mutation of an evidence or
capability wrapper cannot alter the issued evidence or decision snapshot.

The persistent adapter requires an explicit root, explicit
`initialize` or `open_existing` lifecycle, explicit SQLite busy timeout, and
explicit interprocess-lock timeout. Lock acquisition uses nonblocking attempts
against one monotonic deadline for the complete operation and returns the
typed unavailable failure when that deadline expires. Opening an absent or
incomplete established registry never creates an empty replacement. Freshness
is isolated by canonical review scope together with the exact review domain,
purpose, trust key, and authority, so foreign or spoofed trust identities
cannot poison the legitimate authority's review-version sequence.

Pending, unavailable, or inconclusive review maps to `needs_review`. Rejected,
invalid, spoofed, or falsified evidence maps to quarantine and remains preserved
for audit without becoming authorization. These states do not abort unrelated
work. Certification, publication, delivery, and derived outputs remain blocked
only for scopes whose dependency graph includes the affected review. Independent
Learning Units, lectures, deterministic validation, and DAG tasks continue. A
downstream consumer cannot ignore or weaken that blocked dependency. The
external authority must independently prove signature validity, current
authority status, non-revocation, authorized scope, and quorum. The unavailable
adapter fails closed for the affected scope.

The confirmed signed scope preserves every exact
`(disagreement_id, proposition_id)` pair and additionally carries
`numeric_review_subjects`. Each `NumericReviewSubject` binds exactly one
`proposition_id`, `quantity_id`, `source_clause_id`, component,
`source_binding_sha256`, and reason code. Numeric subjects use canonical order
by those six fields, reject duplicates and noncanonical input, and cannot be
confused with disagreement pairs. A scope must enumerate at least one pair or
numeric subject.

`canonical_source_binding_sha256` validates and hashes the complete
`NumericSourceBinding` model under the exclusive
`lecture-study/numeric-source-binding/v1` domain. The payload includes the exact
expression, every component state, every UTF-8 selector, every implicit
justification, and both boundary selectors. No source-content, ledger,
manifest, certification, or publication identity is reused.

Human-review evidence signs the complete mixed scope under the fixed
`lecture-study.human-review.v1` cryptographic domain and
`human-review-scope-decision` purpose, with an exact authority and authority-key
identity. The verifier recomputes current content identities and derives numeric
subjects only from current `UNVERIFIABLE` numeric-binding violations. Approval
satisfies only the exact enumerated subjects. A changed binding, component,
reason, proposition, quantity, clause, lecture, domain, purpose, key, or
authority invalidates the authorization. Invalid, rejected, substituted, or
falsified evidence remains quarantined and cannot be released by review.
Non-numeric review without an exact disagreement remains unsupported and
blocked until a separate domain contract is confirmed.

Both persistent registries use caller-injected explicit roots and perform the
complete read, validation, reservation or append, and commit under an
interprocess lock with an explicit bounded acquisition deadline. Canonical
records are sequence-bound and hash-chain-bound; immutable `O_EXCL` anchors bind
each committed sequence and record identity. A successful append requires a
committed SQLite transaction, full database synchronization, immutable anchor
creation, and parent-directory durability. Opening, reading, mutating, and
revalidating a registry require validation of its complete record chain and
anchor set.
Unsafe paths or file types, noncanonical or malformed state, chain or head
mismatch, truncation, duplicate identities, ambiguous crash state, persistence
failure, and partial rollback of only part of the registry fail closed. No
repair, record removal, empty-registry substitution, or process-local fallback
is inferred.

The hash chain and durable head detect corruption and partial rollback within
the registry root. They cannot, by themselves, detect coordinated replacement
of the entire mutable root with an older internally valid generation after a
restart. A claim of resistance to that whole-root rollback requires an
independent trusted monotonic anchor outside the registry root; no such anchor
is implied by this decision. The registry schemas are new contracts, and no
legacy registry or migration from a fictitious earlier persisted schema is
claimed.

The final immutable prompt bytes are checked before inference by a tokenizer
adapter that reports its live tokenizer, model, and chat-template identity.
Prompt bytes, response schema, limits, token count, output reserve, and safety
margin are bound in deterministic evidence. Truncation is forbidden, and every
retry is a new invocation that requires a new preflight. `VerifiedPrompt` is an
issuer-only capability registered by `PromptPreflight`; manual allocation,
copying, public construction, and post-issuance mutation cannot create a
current capability.

These contracts are diagnostic foundations only. Runtime inference, production
human-review composition, production source-to-inventory orchestration,
gate-evidence authentication, reference-corpus calibration, expert validation,
and publication authorization remain blocked. No current report claims perfect
semantic completeness. Neither registry grants publication authority, and
neither advances the complementary-document capability described in section 14.

### 14. Complementary lecture documents are a later bounded phase

The current discovery and acquisition implementation (part of the private
deployment and not included in this repository) does not provide general
ingestion for PDF, DOCX, PPTX, or other lecture attachments. This capability
must not be inferred from media discovery, page extraction, or the authenticated
source resolver implemented in the current phase.

A later bounded phase will:

1. enumerate complementary documents exposed by the authoritative lecture
   source;
2. download each document through the authenticated acquisition boundary;
3. preserve its exact original bytes, source URL identity, source association,
   media type, size, and content digest;
4. organize originals below `source/documents` and extracted evidence below
   `evidence/documents` inside the lecture-centric workspace;
5. extract text, layout, tables, images, speaker notes, and other supported
   structures without calling generated interpretation verbatim evidence;
6. register document evidence in an extended `SourceUniverse`;
7. include originals and derived evidence in dedicated authenticated manifests;
   and
8. require document clauses and propositions to participate in the same
   independent semantic reconciliation and downstream dependency gates as
   media-derived evidence.

Document formats, archive handling, macro behavior, parser isolation, resource
limits, and source attachment authentication require their own threat model and
acceptance fixtures. They are deliberately outside the current bounded phase.

### 15. Semantic runtime and calibration evidence remain diagnostic

The authenticated semantic-analysis runtime is an application boundary, not a
new publication authority. Its first executable vertical slice operates on one
lecture and isolates each Learning Unit so a local failure does not stop an
independent unit or lecture.

The runtime must:

1. revalidate the issuer-only source-manifest capability and require exact
   equality between its complete record universe and the records resolved for
   the lecture;
2. resolve every source span against the current authenticated artifact bytes;
3. construct and audit one complete `SourceClauseInventory` per Learning Unit;
4. preflight the exact final prompt bytes with the runtime tokenizer identity,
   explicit input and output budgets, and truncation disabled;
5. invoke at least two uniquely identified candidate producers with distinct
   model hashes;
6. invoke a separate reconciliation evaluator whose producer and model
   identities differ from every candidate producer;
7. require a terminal, non-truncated execution record for every invocation;
8. audit the complete candidate, atomic-ledger, and reconciliation sets; and
9. return scope-local diagnostic states without calling certification,
   publication, delivery, or derivative issuers.

The provider-neutral runtime ports are inward dependencies. Model process
launch, provider clients, tokenizer adapters, source-universe production,
visual-observation generation, and CLI composition remain outer adapters. A
production process adapter must revalidate the exact `VerifiedPrompt`
immediately before creating the process or request. A schema-valid response is
not semantic evidence by itself.

Calibration evidence is also non-authoritative in this phase. A structurally
versioned reference-corpus manifest may bind exact lecture, Learning Unit,
clause, source-content, inventory, candidate, ledger, reconciliation, rubric,
annotation, evaluator, prompt, model, implementation, configuration, and run
identities. A deterministic auditor may recompute missing, additional,
substituted, disagreed, and replayed cases. It must not call the evaluator
calibrated, select an acceptance threshold, authenticate expert provenance, or
authorize a gate.

No calibration cryptographic domain, authority, key, promoted corpus,
veterinary-expert quorum, annotation lifecycle, split policy, confidence
bound, density threshold, provider, model, tokenizer, or production runtime
root is inferred by this ADR. Missing current calibration state produces
`needs_review`; structurally invalid or substituted evidence produces
quarantine for the dependent scope. No human review can release quarantine.

Automatic criticism remains plural and directional:

- source-to-output coverage identifies omitted and low-salience clauses;
- output-to-source support identifies unsupported generated claims;
- typed semantic checks preserve polarity, certainty, comparisons, causal
  direction, conditions, exceptions, contraindications, red flags, numeric
  values, units, doses, routes, intervals, frequencies, and durations; and
- output-depth and density measurements are anomaly signals, not acceptance
  authority, until calibrated against the promoted expert corpus.

Primary evidence informing this boundary includes:

- [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12), which
  supports deterministic structural validation but makes no factuality claim;
- [Lost in the Middle](https://aclanthology.org/2024.tacl-1.9/), which shows
  that nominal long context does not guarantee uniform evidence use;
- [RoSE](https://aclanthology.org/2023.acl-long.228/) and
  [FActScore](https://aclanthology.org/2023.emnlp-main.741/), which support
  separate source-content recall and generated-claim support accounting;
- [QAFactEval](https://aclanthology.org/2022.naacl-main.187/) and
  [SummaC](https://aclanthology.org/2022.tacl-1.10/), which provide
  complementary rather than interchangeable factuality signals;
- [Judging LLM-as-a-Judge](https://papers.neurips.cc/paper_files/paper/2023/hash/91f18a1287b398d378ef22505bf41832-Abstract-Datasets_and_Benchmarks.html),
  which documents evaluator biases that preclude self-approval; and
- [On Calibration of Modern Neural Networks](https://proceedings.mlr.press/v70/guo17a.html),
  which demonstrates that confidence calibration is data-dependent and must
  be measured on held-out evidence.

Whisper, Apple Vision, `llama.cpp`, and candidate model families remain subject
to the project-specific expert-corpus benchmark already required by sections 9
and 10. Generic benchmarks and structured-output conformance do not prove
semantic completeness.

### 16. Production semantic execution is separate from production authorization

The first supported production semantic adapter boundary is local `llama.cpp`
through `llama-cli`. This decision selects an execution mechanism, not a model,
corpus, calibration result, certificate, publication decision, or production
content approval.

Production composition requires three independently authenticated semantic
actors:

- producer A;
- producer B; and
- one evaluator that is distinct from both producers.

Each actor identity binds the provider, exact executable bytes and reported
engine revision, exact GGUF bytes and metadata identity, tokenizer identity and
revision, chat-template bytes, canonical runtime profile, actor role, allowed
semantic stage, and complete runtime parameters. Each producer and evaluator
also binds exact nonempty instruction bytes through its prompt identity.
Producer A and producer B require different model-artifact hashes. The evaluator
requires a third distinct model-artifact hash. A role name, deployment alias,
copied label, different prompt, or separately launched process does not
establish independence. A copied instruction identity cannot move an actor into
another role or stage.

The effective runtime revision includes:

- the `llama-cli` executable SHA-256;
- the reported llama.cpp version or commit when available;
- the complete GGUF artifact SHA-256;
- the exact tokenizer identity;
- the exact chat-template SHA-256; and
- the canonical runtime-profile SHA-256.

Tokenizer and chat-template identity must be resolved from authenticated GGUF
metadata or from separately authenticated content-addressed artifacts. A model
name, guessed fallback, provider default, or unverified template is not
admissible evidence. An actor whose tokenizer or template identity cannot be
demonstrated is unavailable. The tokenizer adapter must execute through the
exact model-bound tokenizer boundary and return complete token-count evidence
for the original prompt bytes. Its GGUF, tokenizer revision, tokenizer content
hash, chat-template content hash, terminal disposition, and no-truncation
evidence must equal the actor profile before the count is admissible.

Every invocation uses a versioned canonical runtime profile that covers:

- context window;
- input budget;
- reserved and maximum output budgets;
- batch and thread parameters;
- temperature, top-p, top-k, and seed policy;
- stop sequences;
- timeout and cancellation policy;
- retry count and retry classification;
- concurrency and resource limits; and
- exact tokenizer and chat-template identities.

A diagnostic profile is explicitly non-production and cannot be promoted by
construction, naming, or caller declaration. A production profile requires
authenticated binding through the semantic invocation authority. Missing,
substituted, or unauthenticated production profile evidence fails closed.

Provider output remains untrusted generated content. The adapter must:

- preflight the exact final bytes before process creation;
- use the actor-specific GGUF-bound tokenizer for input and output accounting;
- keep reserved output capacity and the maximum accepted output as distinct,
  explicit profile values;
- forbid truncation and incomplete streaming results;
- reject model, tokenizer, template, executable, profile, and parameter drift;
- bind timeout and cancellation to a terminal lifecycle record;
- bound stdout, stderr, output bytes, and output tokens;
- treat source content as data rather than executable instructions;
- reject a response that has no confirmed pre-inference reservation; and
- never let a retry silently create another semantic authority.

A retry uses a new invocation identity inside the original run, links the failed
attempt, and preserves the failed disposition. Resume requires the exact
canonical run payload and a current issuer-only capability. Expiration,
cancellation, failure, timeout, revocation, and crash never release an identity.
The local adapter requires no provider credential. A later remote adapter may
load secrets only from protected `.env` configuration; secrets never enter code,
fixtures, logs, documentation, subprocess output, or canonical identity
payloads.

#### 16.1 Semantic invocation authority

Semantic invocation lifecycle records use the dedicated trust boundary:

- cryptographic domain:
  `lecture-study.semantic-invocation.v1`;
- signature purpose:
  `semantic-runtime-invocation`;
- key type:
  `SemanticInvocationHmacKey`; and
- issuer:
  `SemanticInvocationHmacAuthority`.

This authority authenticates only semantic run and invocation lifecycle
records. It cannot authenticate source manifests, human review, quarantine
release, expert-corpus promotion, calibration promotion, certification,
publication, delivery, or derivative generation.

Run and invocation identifiers are opaque authority-issued UUIDv4 values. A
caller-selected identifier carries no authority. Uniqueness is global inside
the exact `(cryptographic_domain, signature_purpose, trust_key_id,
authority_id)` namespace.

The persistent registry atomically binds:

- run and invocation identities;
- the complete canonical invocation-payload hash;
- runtime-profile hash;
- actor identity;
- lecture and Learning Unit identities;
- semantic stage;
- authenticated source identities;
- calibration identity; and
- expert-corpus identity.

The append-only lifecycle is closed:

```text
reserved
  -> started
  -> completed | failed | cancelled | expired | revoked
```

Transitions that skip, repeat, reverse, overwrite, release, recycle, or
destructively replace state fail closed. Every reservation, start, and terminal
record has a canonical signed payload. Only the record's own
`detached_signature` is excluded from the payload authenticated by that
signature. A valid HMAC without the exact current persistent reservation does
not constitute admissible invocation evidence.

The inward `SemanticInvocationRegistry` port has an explicitly injected root.
Initialization and opening an existing layout are separate operations. The
production composition has no default path, environment fallback, temporary
root, service locator, or in-memory backend.

The current supported topology is one explicit host with multiple processes
sharing one registry on a reliable local filesystem. The adapter uses bounded
interprocess locking, atomic append-only persistence, full synchronization,
immutable anchors, and complete state revalidation. Symlink or inode
substitution, corruption, truncation, ambiguous crash state, partial write, and
database-only rollback fail closed. Multi-host operation is unsupported.

`SemanticInvocationMonotonicAnchor` is a separate inward port. An external
monotonic implementation is required for production readiness. Local anchors
can detect database-only or partial rollback while they remain intact, but they
cannot detect coordinated replacement of the complete registry root with an
older internally valid generation. That limitation remains explicit and keeps
production readiness false when the external anchor is absent.

#### 16.2 Veterinary expert-corpus promotion

Veterinary corpus promotion uses a separate trust boundary:

- cryptographic domain:
  `lecture-study.veterinary-expert-corpus.v1`;
- signature purpose:
  `expert-corpus-promotion`;
- key type:
  `VeterinaryCorpusPromotionHmacKey`; and
- issuer:
  `VeterinaryCorpusPromotionHmacAuthority`.

The HMAC authenticates the promotion record; it does not prove professional
qualification. Production promotion requires separately authenticated,
current, non-revoked qualification evidence for either:

- a veterinarian licensed in the jurisdiction represented by the evidence; or
- a formally recognized veterinary specialist for the declared specialty.

Expert records use pseudonymous identifiers and contain no PII. Each record
binds its qualification authority, jurisdiction, professional category,
specialty, permitted clinical scope, validity interval, revocation state,
qualification-evidence hash, and verification authority. A name, e-mail
address, self-declaration, model output, or unsigned document is not
qualification evidence.

Every corpus record binds:

- source identity;
- ownership or custodianship;
- license;
- permitted training, evaluation, and derivative uses;
- provenance chain;
- exact content hash;
- annotation history; and
- expert-review history.

Unknown, ambiguous, incompatible, expired, or revoked permission blocks
promotion. Promotion requires exact equality between the declared inventory and
the stored records, provenance evidence, license evidence, annotations,
adjudications, and hashes.

The versioned authenticated annotation policy requires at least two independent
qualified annotators and one separate qualified adjudicator for disagreement.
An annotator cannot be the sole adjudicator of the same record. Clause-level
annotations, original disagreements, and adjudication evidence are retained.
Safety-critical or unresolved disagreement requires exact adjudication
evidence.

Missing or unverifiable evidence produces local `needs_review`. Invalid,
substituted, falsified, revoked, or conflicting evidence produces quarantine.
Promotion cannot release quarantine and cannot authorize certification,
publication, delivery, derivatives, or production content approval.

Corpus promotion additionally resolves the complete stored corpus through an
issuer-only inward capability. The resolved snapshot must equal the promotion
request across inventory, content, custody, provenance, license, annotation, and
review evidence. Professional category is closed to licensed veterinarians and
formally recognized veterinary specialists. Specialty and permitted clinical
scope must match exactly rather than through caller-selected labels.

Promoted-corpus current state is append-only and explicitly rooted. It records
promotion and restrictive revocation as separate immutable events, revalidates
logical corpus identity and exact evidence before every consumption, and
rejects unavailable, stale, conflicting, rolled-back, or revoked state. The
local SQLite adapter provides process-safe persistence, synchronization,
corruption and truncation detection, database-only rollback detection, and
restart revalidation. It has no release, overwrite, destructive replacement, or
silent default root.

#### 16.3 Calibration and short-material authority

Semantic calibration uses the dedicated boundary:

- cryptographic domain:
  `lecture-study.semantic-calibration.v1`;
- calibration purpose:
  `semantic-calibration-policy`;
- short-material purpose:
  `short-material-threshold-policy`;
- key type:
  `SemanticCalibrationHmacKey`; and
- issuer:
  `SemanticCalibrationHmacAuthority`.

The authority is separate from invocation, corpus, review, certification,
publication, delivery, and derivative trust.

Reference partitions are separated by authenticated source lineage. No lecture,
source artifact, source aggregation, near-duplicate clause, or transformed
derivative may cross from training or tuning into evaluation. Partition identity
binds corpus identity and version, lecture identities, source aggregations, record
identities, specialty, task taxonomy, split policy, and deduplication policy.
Detected leakage invalidates the calibration evidence.

The following clinical dimensions are individually blocking:

- source-clause coverage;
- safety-critical clause coverage;
- negation;
- uncertainty and certainty;
- comparison direction;
- causal direction;
- conditions;
- exceptions;
- contraindications;
- red flags;
- numeric values;
- units;
- doses;
- routes;
- intervals;
- frequencies;
- durations;
- denominators; and
- unsupported clinical claims.

Promoted calibration evidence requires:

- 100% terminal disposition of source clauses;
- 100% safety-critical clause recall;
- zero accepted semantic reversals;
- zero accepted unsupported safety-critical claims; and
- zero accepted unauthenticated numeric mutations.

These are deterministic safety gates, not a claim of perfect semantic
completeness. No universal sample size or statistical acceptance threshold is
selected. A promoted policy must authenticate the sample-size method, minimum
cases per task and specialty, uncertainty method, confidence or credible
interval, permitted exclusions, stopping rule, failure policy, and every
additional threshold.

Calibration outcomes are not caller attestations. A dedicated inward evaluator
port issues a non-copyable capability for the exact current reference cases,
expected dispositions, model identities, producer identities, evaluator
identity, runtime profile, tokenizer, corpus, and policy. The calibration
authority revalidates that evidence and recomputes sample, exclusion, failure,
dimension, and deterministic-gate results from the authenticated cases.
Generator self-approval and model-identity reuse fail closed.

Calibration and density evidence binds the complete final-material identity:

- complete final artifact hash;
- clause-inventory identity;
- atomic-ledger identity;
- reconciliation identity;
- source-content aggregation identity;
- runtime-profile identity;
- producer identities;
- evaluator identity;
- expert-corpus identity; and
- calibration-policy identity.

A fragment, normalized text alone, or caller label is insufficient.

Short-material assessment combines exact clause, proposition,
safety-critical, and numeric coverage with authenticated duration, transcript
and OCR byte counts, instructional-primary visual inventory, material type,
specialty, promoted corpus, calibration policy, and the calibrated density
distribution for that exact scope. Clause-by-clause coverage remains the hard
gate. Length, words per minute, and compression ratio remain anomaly signals and
can never approve semantic completeness.

The source profile and coverage counts are resolved through an issuer-only
source-measurement port over the authoritative source manifest, Source Universe,
clause inventory, atomic ledger, and reconciliation. Density is normalized by a
canonical multi-feature source-scale formula that includes authenticated
duration, transcript bytes, OCR bytes, instructional-primary visual spans,
clauses, propositions, numeric quantities, and safety-critical clauses. A raw
word minimum or caller-selected count cannot satisfy the policy.

Calibration policy, evidence, and short-material policy current state is
append-only, explicitly rooted, and revalidated before every consumption.
Persistent state preserves exact artifact identity, trusted sequence and time,
and restrictive revocation. Corruption, truncation, substitution, unavailable
state, conflicting replay, and database-only rollback fail closed. A
non-production policy or capability cannot produce a verified short-material
disposition.

Until an authenticated corpus and calibration policy are promoted, the detector
may report diagnostics but cannot approve proportional completeness. The
dependent scope remains `unverifiable` and `needs_review`. Invalid or
substituted threshold evidence produces quarantine. Independent DAG work
continues, while certification, publication, delivery, derivatives, and
production approval remain blocked.

## Implementation order

1. Add manifests, prompt-budget preflight, and deterministic completeness
   certificates.
2. Replace frame-driven packs with conceptual Learning Units while retaining
   frames as evidence.
3. Introduce the atomic source content ledger with timestamped evidence.
4. Add authenticated complementary-document discovery, acquisition, extraction,
   and source-universe integration.
5. Add source-to-material coverage and material-to-source faithfulness critics.
6. Add structured veterinary-field and citation critics.
7. Calibrate thresholds on the expert reference corpus.
8. Introduce resident inference and additional parallel slots only after
   quality gates exist.
9. Migrate workspace artifacts per lecture after dual-read validation.

Implementation status through 2026-07-27:

- the immutable source-universe models and deterministic auditor are
  implemented;
- an authenticated, content-addressed source resolver now preserves exact
  transcript, OCR, and visual record bytes with complete provenance and
  fail-closed filesystem and manifest validation;
- one `AuthenticatedSourceManifest` is authoritative per lecture;
  `HmacSourceManifestCatalog` issues an issuer-only `VerifiedSourceManifest`,
  revalidates it before critical consumption, and exact-set comparison against
  the manifest-projected `SourceRecordProvenance` universe precedes semantic
  aggregation;
- every full-frame or regional generated visual observation requires an
  individual authenticated receipt under the dedicated
  `lecture-study.visual-observation.v1` domain,
  `source-span-visual-observation` purpose, `VisualObservationHmacKey`, and
  `VisualObservationHmacAuthority`; issuer-only visual capabilities bind the
  exact invocation, span, frame, selector, generator, and observed-text digest;
- the immutable atomic-ledger models and structural source-to-ledger auditor
  are implemented, but deliberately cannot approve the atomic-content gate;
- independent candidate, clause-inventory, and deterministic reconciliation
  contracts are implemented, but deliberately cannot approve the clause gate;
- the Learning Unit-local clause-inventory audit binds its canonical inventory
  identity, verifies transcript, OCR, and visual modality and source hashes
  against the authoritative universe, distinguishes `needs_review` from
  quarantine, and is a mandatory fail-closed reconciliation dependency;
- atomic-ledger excerpts distinguish transcript verbatim text from OCR and
  visual observed text, require generated-observation identity for observed
  evidence, and cannot increase coverage metrics through an invalid binding;
- the exact-clause `NumericSourceBinding` schema and open/closed boundary model
  are implemented; every numeric component has an explicit, implicit, or absent
  state, clause-relative UTF-8 byte selector rules, and traceable
  implicit-justification rules; exact-expression containment, complete-clause
  context preservation, case-sensitive units, and the versioned English/pt-BR
  comparator and role lexicon are enforced; inter-quantity overlap fails closed
  pending an authorization contract; `NumericSourceBindingAuditor` maps
  verified, unverifiable, and invalid findings to satisfied, `needs_review`,
  and quarantine scope states, respectively, and review cannot clear invalid
  evidence from quarantine;
- candidate, ledger, and relation projections preserve exact provenance:
  candidate relations require the endpoint clause union and final ledger
  relations require the independently derived endpoint source-span union;
- authenticated text extents retain the exact canonical UTF-8 text and reject
  coordinated clause substitutions; reconciliation requires exact clause
  evidence rather than accepting a sibling clause from the same span;
- numeric selector overlap is evaluated across each complete candidate batch
  and across all propositions in the lecture ledger audit;
- source-content resolution uses an inward artifact-reader port and an injected
  rooted-store adapter rather than importing delivery infrastructure;
- verifier-issued human-review results cannot be publicly constructed and are
  revalidated against current artifacts and external authority at consumption;
  blocked assessments cannot recover during consumption, and require a fresh
  assessment; issuer-bound evidence snapshots remain internal, and any public
  evidence access returns a fresh validated defensive copy; presenting an
  authentic capability to another valid dependency scope is rejected as a
  caller-local assessment mismatch without quarantining the authentic evidence;
  nested local requirement models are reconstructed and validated before any
  scope field is consumed;
- human-review policy, calibration, trust identity, authority sequence, and
  review version are loaded from an injected trusted current-state port, while
  verification time is loaded from an injected trusted clock; caller-declared
  requirements must exactly match both trusted dependencies, which are queried
  again at critical dependency consumption;
- `HumanReviewQuarantineRegistry` durably records the complete canonical
  evidence identity under
  `lecture-study/human-review-quarantine-evidence/v1`, excluding only the
  detached signature. The SQLite adapter requires an explicit initialize or
  open-existing lifecycle, performs append-only hash-chained writes with
  immutable anchors and interprocess locking, and prevents a reassigned
  signature from rehabilitating the same payload. Fresh evidence for the same
  exact review and trust scope requires a new payload base, higher review
  version, and new authenticated signature;
- `VisualObservationReceiptRegistry` makes `receipt_id` and `invocation_id`
  jointly unique within the exact visual trust namespace. Reservation,
  issuance confirmation, and invocation expiration are append-only persistent
  events; HMAC validity without the exact current reservation is insufficient
  for verification or consumption after reconstruction or restart;
- prompt-preflight results are issuer-only capabilities and copied or manually
  allocated values are rejected at consumption;
- the durable-registry correction validation passes 584 focused tests and
  1,450 project
  tests with one existing Starlette/httpx warning; Ruff lint passes, Ruff
  format passes for 157 files, MyPy passes for 99 source files, and tracked
  plus phase-relevant untracked whitespace checks pass. The independent
  phase-final reviews against this corrected diff return completeness `PASS`
  for all 18 bounded requirements, Code Review `PASS` with no P0 or P1
  finding, and Code Secure `SECURED` with 22 of 22 modeled threats closed and
  `threats_open: 0`;
- exact disagreement/proposition review-pair and numeric-review-subject
  derivation, domain-separated binding identity, and scope-local `needs_review`
  or quarantine assessment are implemented without production review authority
  or publication wiring;
- detached external human-review verification and exact no-truncation prompt
  preflight contracts are implemented without production adapters;
- a provider-neutral `SemanticAnalysisRuntime` now executes the authenticated
  source, complete Source Universe, source resolution, Learning Unit inventory,
  prompt preflight, two-producer candidate, independent evaluator, atomic
  ledger, reconciliation, clause-disposition, source/output-accounting, and
  semantic-mutation graph through injected ports. Runtime-owned canonical
  prompt envelopes bind exact authenticated inputs and complete actor identity
  before issuer-only prompt preflight. Completion evidence binds the exact run,
  invocation, lecture, unit, stage, actor, model, prompt, bounded raw output,
  output-token reserve, and artifact. A mandatory global run/invocation
  registry port prevents cross-execution replay; replay, truncation, stale
  source, partial Source Universe, actor confusion, and unavailable dependencies
  fail closed without stopping an independent unit;
- clause inventory closure requires the exact instructional primary transcript
  and OCR span set as well as the existing visual closure. Missing primary text
  remains local `needs_review`; context substitution, additions, modality
  changes, and content substitution are quarantined;
- immutable reference-corpus and calibration-run contracts bind the exact
  corpus version, annotation schema, rubric, annotation, semantic artifact
  chain, generator, independent evaluator, evaluator output, and case set.
  Their deterministic auditor recomputes omissions, additions, substitutions,
  outcome and facet disagreements, and diagnostic ratios but permanently
  reports `calibrated` and `publication_ready` as false;
- deterministic semantic critics now separately account for source-to-output
  recall and output-to-source support. Exact mutation subjects for omission,
  low-salience omission, unsupported claims, and semantic comparisons are
  derived from authenticated artifacts; generator and evaluator identities
  must differ by producer and model. Public runtime and audit boundaries
  reconstruct strict models before use. Output-component identity binds the
  complete proposition, incident relations, reconciliation, and reference, so
  unchanged normalized text cannot conceal a structured semantic mutation.
  Long-lecture underproduction remains unavailable until a genuine issuer-only
  calibration and authenticated material-input contract exists;
- every structurally complete runtime unit remains in `needs_review` while
  authenticated calibration is unavailable, and every runtime readiness
  property for certification, publication, delivery, and derivatives remains
  false;
- the runtime/calibration focused suite passes 686 tests and the full project
  suite passes 1,552 tests with the existing Starlette/httpx warning; Ruff
  lint passes, Ruff format checks 164 files, MyPy checks 103 source files,
  `git diff --check` passes, and the twelve phase-relevant untracked files pass
  the additional whitespace check;
- the independent phase-final reviews against the corrected current diff return
  Completeness `PASS` for the bounded deterministic scope with 15 verified, 3
  partial material requirements, 0 absent, and no P0/P1, Code Review `PASS`
  with no P0/P1, and Code Secure `SECURED` with 15 of 15 modeled threats closed
  and `threats_open: 0`;
- no persisted atomic or semantic ledger schema-v1 artifacts were present in
  the inspected workspace; the current atomic-ledger schema is
  therefore corrected in place without a fictitious compatibility layer or
  data migration, while schema versioning remains mandatory for future
  persistence;
- timeline-classification and Learning Unit completeness have typed objective
  gate payloads;
- the production segmentation producer, production runtime adapters,
  authenticated runtime invocation authority, calibrated semantic evaluator,
  promoted expert reference corpus, and production issuer integration remain
  incomplete; and
- general complementary-document discovery, acquisition, extraction, and
  source-universe participation remain unimplemented; and
- the current frame-driven Evidence Packs remain uncertifiable.

### Production semantic runtime and promotion boundary

The first real semantic transport boundary is `llama-cli`. This selection does
not authorize a model artifact. Exact runtime identity binds the executable
content hash and reported revision, GGUF content hash and metadata identity,
tokenizer identity and revision, chat-template content hash, and the complete
canonical diagnostic runtime-profile identity. Production execution requires
three distinct GGUF hashes and three distinct effective actor identities for
producer A, producer B, and the evaluator. Aliases, roles, prompts, or copied
labels do not establish independence.

The transport receives only an issuer-owned `VerifiedPrompt`, disables
conversation and implicit template behavior, uses explicit canonical
parameters, inherits no caller credential environment, accepts no retry, and
returns output marked untrusted and non-production. The authenticated adapter
uses a dedicated `LlamaCppTokenizerAdapter` whose evidence must match the exact
GGUF, tokenizer revision, tokenizer bytes, chat-template bytes, and actor
profile. Producer and evaluator instructions are exact content-addressed bytes;
role and stage are enforced independently of prompt labels. The authenticated
adapter reserves and starts an authority-issued invocation before calling the
transport. The exact response is admissible only while its completed issuer-owned
capability remains current and bound to the canonical raw and parsed response
identity. The result exposes only defensive snapshots after current
terminal-state revalidation. Cancellation and failure permanently burn the
invocation identity. A retry uses a new invocation identity under the same run,
links to the failed attempt, and requires exact equality with the original
canonical invocation payload.

Semantic lifecycle trust uses only:

- cryptographic domain `lecture-study.semantic-invocation.v1`;
- signature purpose `semantic-runtime-invocation`;
- `SemanticInvocationHmacKey`;
- `SemanticInvocationHmacAuthority`; and
- an explicitly rooted, append-only, process-safe
`SqliteSemanticInvocationRegistry`.

Run and invocation identifiers are issuer-generated UUIDv4 values. Reservation,
start, completion, failure, cancellation, expiration, and revocation are signed
append-only transitions. The local registry detects local corruption,
truncation, substitution, database-only rollback, and conflicting concurrent
reservation. It does not claim detection of coordinated replacement of the
complete root. Production readiness therefore requires the confirmed inward
`SemanticInvocationMonotonicAnchor` port and a real external implementation.
Multi-host execution remains unsupported.

Production composition revalidates the complete invocation trust graph:
`SemanticInvocationHmacAuthority` must own the exact injected registry, all
three authenticated actor adapters must use that same authority, and every
actor must use the concrete bounded process runner and executable version probe.
A correct-root decoy registry, foreign authority, injected fake runner, or
injected version probe fails during startup.

Veterinary corpus promotion uses the dedicated
`lecture-study.veterinary-expert-corpus.v1` /
`expert-corpus-promotion` boundary. Promotion binds pseudonymous qualification
references, jurisdiction, specialty and clinical scope, validity and
revocation, provenance, ownership or custody, license permissions, immutable
record inventory, clause-level annotation history, two independent qualified
annotators, and a separate qualified adjudicator. The HMAC issuer authenticates
the promotion record; it does not prove professional qualification.
Qualification authentication therefore crosses a separate inward
`ExpertQualificationEvidencePort`. Its capability and issuer are non-constructible,
issuer-owned, bound to the exact external port, and revalidated before promotion
and every later corpus-capability consumption. The complete stored corpus also
crosses an issuer-only resolver boundary and must match all declared content,
inventory, custody, provenance, license, annotation, and review evidence.
Append-only persistent current state preserves promotion, logical identity, and
restrictive revocation across processes and restarts. No qualification
cryptographic authority was invented in this phase. Synthetic qualification
fixtures remain explicitly non-production, and every corpus capability reports
production eligibility as false. No real expert, corpus, license, or
qualification is promoted by this decision.

Calibration uses the separate `lecture-study.semantic-calibration.v1` boundary
with purposes `semantic-calibration-policy` and
`short-material-threshold-policy`. Leakage checks bind partitions by lecture,
corpus identity and version, source artifact, source aggregation, exact record
identities, specialty, task taxonomy, split-policy identity,
deduplication-policy identity, near-duplicate cluster, and derivative root.
The nineteen confirmed clinical dimensions remain individually blocking. Their
authenticated formula is recomputed from
immutable per-case outcomes delivered by an issuer-only independent evaluator;
caller-declared numerator, denominator, failure count, or point estimate cannot
contradict those cases. Producer, producer-model, evaluator, evaluator-model,
runtime-profile, tokenizer, corpus, and policy identities must match exactly.
The authenticated statistical policy also binds its sample-size method,
per-task/per-specialty minimums, uncertainty method, interval kind and level,
exclusion policy, stopping rule, and failure policy. The deterministic gates
require complete terminal source-clause disposition,
complete safety-critical recall, zero accepted semantic reversal, zero accepted
unsupported safety-critical claim, and zero accepted unauthenticated numeric
mutation. Other statistical thresholds, sample-size rules, uncertainty
methods, and density distributions require an authenticated policy and cannot
be inferred by the runtime.

Final-material calibration binds the complete final artifact, canonical text,
clause inventory, atomic ledger, reconciliation, source aggregation, runtime
profile, two independent producer identities, independent evaluator identity,
expert-corpus identity, and calibration-policy identity. Short-material source
profiles preserve transcript and OCR byte counts separately, instructional
primary visual count, clause, proposition, numeric and safety-critical counts,
specialty, and material type. Policy issuance and consumption resolve the exact
content-addressed artifact bytes and canonical text through injected resolver
and source-measurement ports. Counts are derived from the authoritative manifest,
Source Universe, clause inventory, atomic ledger, and reconciliation. The
authenticated versioned counter and normalized multi-feature source-scale
formula derive density from duration, transcript bytes, OCR bytes,
instructional-primary visual spans, clauses, propositions, numeric quantities,
and safety-critical clauses. Thresholds are derived only from promoted
calibration observations for the same specialty, material type, resolver,
counting policy, and full material identity. Append-only persistent current
state revalidates calibration policy, evidence, short-material policy, and
revocation across processes and restarts. These ports and all synthetic
calibration capabilities remain non-production until external trust evidence
is selected.

The explicit composition root injects source resolution, the authoritative
manifest catalog, a sealed production-execution binding, three authenticated
actor adapters, tokenizer and prompt issuer, invocation registry and authority,
final-material resolver and counter, expert-corpus and calibration providers,
quarantine and visual registries, current-state and time providers, and
resource limits. There is no default provider, model, registry root, trust
authority, or in-memory production fallback. No issuer for
`VerifiedProductionSemanticExecution` exists in this phase because three real
authenticated model artifacts and promoted runtime profiles do not exist.
Consequently, the legacy diagnostic runtime cannot masquerade as the production
route. A separate vertical synthetic non-production diagnostic verifies the
authenticated manifest, complete source aggregation, authority-owned run,
actor-specific producer A, producer B, evaluator, and prompt-preflight paths.
Synthetic corpus and calibration identities remain visibly unauthenticated,
short-material status remains `needs_review`, independent DAG work can continue,
and certification, publication, delivery, derivatives, production execution,
and production approval remain false.

The first independent review wave over this production-composition diff found
no P0 and identified blocking P1 gaps in output-capability encapsulation,
runtime/registry trust-graph binding, external qualification authentication,
calibration metric derivation, short-material input derivation, leakage
identity, and complete final-material identity. The corrected contracts above
close those deterministic gaps. The same correction wave also removes
capability-object weak-reference ancestry from calibration dependencies,
supports signed-evidence rehydration after restart, requires retry payload
equivalence, and converts descriptor-open failures to typed fail-closed
registry errors.

A second review wave returned Completeness `BLOCK`, Code Review `FINDINGS` with
nine P1 findings, and Code Secure `SECURED` with 22 of 22 modeled threats
closed. The subsequent deterministic correction added exact tokenizer
accounting, instruction and role/stage binding, typed production ports, a
vertical authenticated-actor diagnostic, complete stored-corpus governance,
durable corpus and calibration current state, independent calibration evidence,
complete statistical policy, authenticated source measurements, and normalized
multi-feature density. Phase-final validation and all three independent reviews
must examine that converged diff before the phase is closed.

A later independent review found five additional P1 defects and no P0. The
decision is therefore refined as follows:

- chat-template evidence extracted from GGUF metadata must name the same exact
  GGUF content hash as the actor; an independently stored template remains
  admissible only as a separately content-addressed authenticated artifact;
- source-scale normalization must not multiply correlated size features. The
  authenticated versioned projection uses the maximum of the target/reference
  ratios for duration, transcript bytes, OCR bytes, instructional visuals,
  clauses, propositions, numeric quantities, and safety-critical clauses. This
  preserves proportional scaling and prevents any feature from being averaged
  away or masked;
- a persistent-registry lock timeout must be an exact finite value in
  `(0, 60]` and must be rejected before any lock or storage access otherwise;
- the executable selected after authentication must be the same open file
  description launched by the process and revalidated after probe and
  inference. A mutable staging pathname is never an executable authority; and
- child processes receive a minimal explicit environment allowlist rather than
  a copy of the ambient environment. Credential, token, session, and
  project-secret variables are never propagated by this boundary.

Descriptor-backed executable launch fails closed on platforms that cannot
execute `/dev/fd`. On the inspected Darwin host this keeps live local inference
unavailable; no mutable-path fallback, production readiness, or publication
authority is inferred from the synthetic diagnostic.

## Consequences

- Existing V1 outputs are not considered complete merely because their files
  exist.
- The current two-hour workspace lecture is a regression fixture and is
  expected to fail the new segmentation and prompt-budget gates.
- The exhaustive study guide becomes the primary deliverable. Short summaries,
  flashcards, exams, and podcasts are derivatives.
- Throughput work may proceed in parallel with quality work, but no performance
  optimization may weaken a quality gate.
- Human expert review is part of the safety architecture, not an optional
  afterthought.

## Alternatives considered

### One long-context prompt for the entire lecture

Rejected. Nominal context length does not guarantee uniform use of all source
positions, and it provides poor fault isolation and weak omission diagnosis.

### Keep one Evidence Pack per slide

Rejected as the Learning Unit definition. Slide changes remain useful visual
signals, but the real workspace distribution proves that they do not produce
stable conceptual units.

### Use output length as the completeness metric

Rejected. Verbose text can still omit critical material. Length is retained only
as an anomaly detector alongside atomic-unit coverage.

### Let the same model generate and approve the material

Rejected as the only validation path. Self-review remains useful for repair, but
promotion requires independent deterministic and model-based signals calibrated
against expert review.

### Maximize all concurrency limits

Rejected. Apple Silicon uses unified memory; unbounded Metal concurrency can
reduce throughput, trigger swap, and destabilize inference. Resource-aware
admission control is required.

## Operational diagnostic evidence

A real lecture, identified here as activity `10001` of course C, discipline D,
established the following non-production evidence:

- authenticated source discovery resolved course C, discipline D, and activity
  `10001` (portal acquisition was part of the private deployment and is not
  included in this repository);
- 16 conceptual diagnostic packs allocated all 1,952 transcript segments
  exactly once and retained all 92 OCR and encoded visual frames;
- an authenticated ephemeral HMAC manifest resolved 200 source records after
  cutover;
- semantic extraction produced 124 legacy candidate facts, followed by 16
  drafts and 16 passing same-model critic results;
- parent-process maximum RSS remained approximately 40 MiB for semantic
  extraction and 36 MiB for drafting and criticism; local model children
  reached approximately 9.25-10.00 GiB; and
- the diagnostic derivatives include a visually inspected 16-page A4 Cornell
  PDF and a fully decoded 235.68-second AAC podcast.

The evidence also confirmed two fail-closed conditions. First, 184 OCR/visual
source spans remain explicitly ambiguous because no authenticated full-frame
visual observer exists. Second, the available Qwen 2.5 14B shards are one
logical model, not the two producers and third independent evaluator required
for production.

The prior high Python RSS observation was not an orchestrator memory leak. The
interactive `llama-cli` frontend repeated prompt content and flooded standard
output, while `communicate()` retained the stream without a byte limit. Every
local LLM caller now uses `--single-turn`, `--simple-io`, closed standard input,
bounded stream reads, a finite timeout, and deterministic reaping. Production
authority remains unchanged.

The evidence-pack quality audit remains failed because 13 legacy pack
boundaries expose natural Whisper pauses rather than contiguous window
boundaries. The authoritative diagnostic `SourceUniverse` assigns those pauses
to adjacent units and has zero timeline or learning-unit gaps, but the pack
identity was not rewritten after model execution. This discrepancy remains a
recorded quarantine reason rather than being hidden by retroactive mutation.

The operational closure correction preserves that package as historical
diagnostic evidence and changes future execution contracts instead:

- the source builder emits one exact transcript source span per segment,
  represents natural pauses as explicit ambiguous timeline intervals, rejects
  overlapping or non-positive segments, and fails closed on an unmergeable
  sub-five-minute final Learning Unit;
- semantic, drafting, and critic resume paths compare the exact expected
  sequence set, rejecting missing, additional, and substituted sequences;
- LLM response decoding rejects duplicate keys, non-finite values, additional
  schema fields, and multiple JSON documents, while retry diagnostics are
  bounded and content-addressed;
- execution identity includes the complete implementation surface of shared
  response, process-lifecycle, and environment modules, so a transitive helper
  change invalidates stale derivative identity;
- all external subprocess families use canonical executable resolution,
  sanitized child environments, isolated process groups, finite timeouts,
  bounded stream capture that remains enforced after direct-child exit, and
  cancellation-safe terminate-kill-reap cleanup; and
- the source-acquisition cookie jar (private deployment, not included in this
  repository) uses randomized same-directory temporary files,
  descriptor-relative exclusive creation without symlink following, mode
  `0600`, descriptor writes, file synchronization, atomic replacement, and
  parent-directory synchronization.

These corrections do not retroactively change the run manifest, source graph,
inventories, or derivatives under `workspace/lectures/<course>/10001`. The package
therefore remains non-production, quarantined, and auditable under the exact
identities recorded at generation time.

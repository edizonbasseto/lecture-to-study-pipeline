# Decisions intel

Synthesized from 42 classified ADRs (0001-0016, 0020-0045). Mode: new. Every ADR was classified `locked: true` (Status "Accepted"). One entry per ADR; amendment and supersession notes are copied from each ADR's own Status section and from later ADRs that name it. Paths written as `data/...` in ADRs 0002-0006 are rooted at `workspace/data/` since ADR 0007 decision 1. ADR numbers 0017-0019 are absent (ADR 0044 Status: those records described portal acquisition, which is not part of this repository).

Precedence note: ordering follows only the explicit amends/supersedes statements in each Status section (ADR 0045 section 6). See `.planning/INGEST-CONFLICTS.md` for the resolved ordering and the open items.

## ADR-0001: Use pyobjc-framework-Quartz, not pyobjc-framework-CoreImage, to feed the Apple Vision OCR pipeline
- source: docs/decisions/0001-apple-silicon-quartz.md
- status: locked (Accepted; amended by ADR 0045: the local-only mandate no longer binds the LLM phases)
- decision: Decode slide PNGs with CGImageSourceCreateWithURL + CGImageSourceCreateImageAtIndex into a CGImageRef and pass it to VNImageRequestHandler(cgImage=...). pyobjc-framework-CoreImage is not a dependency; it may be reconsidered only as an additive dependency for a future compositing step.
- scope: OCR image acquisition for Apple Vision; pyobjc dependencies; Apple Silicon Metal contention

## ADR-0002: Extraction orchestrator contracts: raw video resolution, failure isolation, and the Phase 3 handoff shape
- source: docs/decisions/0002-extraction-orchestrator-contracts.md
- status: locked (Accepted)
- decision: (1) The raw video is located by globbing data/01_raw/{lecture.id}.*; exactly one match is required, otherwise RawVideoNotFoundError; audio and slide output go in per-lecture subdirectories. (2) A per-lecture exception is recorded on LectureExtractionOutcome.error and does not abort the batch. (3) LectureExtractionResult (lecture_id, audio_path, frames: list[CanonicalFrame]) is persisted to data/04_canonical/{lecture_id}.json via tmp-then-replace. (4) LectureMetadata.id and LectureExtractionResult.lecture_id use SafeIdentifier, a strict str constrained to ^[A-Za-z0-9_-]+$. OCR runs via asyncio.to_thread under a second semaphore (ocr_concurrency_limit, default 4).
- scope: Phase 2 ExtractionOrchestrator; queue.json; SafeIdentifier path-traversal guard

## ADR-0003: Transcription orchestrator contracts: handoff filename, output directory, orchestrator inputs, and single-flight concurrency
- source: docs/decisions/0003-transcription-orchestrator-contracts.md
- status: locked (Accepted; Phase 3 output contract extended by ADR 0028)
- decision: Phase 3 reads data/04_canonical/{lecture_id}.json and writes data/05_transcribed/{lecture_id}_transcription.json. TranscriptionOrchestrator(queue_path, model_path, concurrency_limit=1) with asyncio.Semaphore; concurrency above 1 is supported by signature but contradicts the Metal/Neural Engine rationale. Per-lecture failure isolation and duplicate-id rejection mirror ADR 0002. LectureTranscriptionResult.lecture_id uses SafeIdentifier. WHISPER_BINARY is engines/whisper.cpp/build/bin/whisper-cli. TranscriptionSegment rejects end_time < start_time (zero-length allowed); the ephemeral -oj sidecar is deleted on every exit path; malformed whisper JSON raises WhisperTranscriptionError.
- scope: Phase 3 TranscriptionOrchestrator; whisper.cpp; data/05_transcribed

## ADR-0004: Evidence pack chunking contracts: output directory, EvidencePack schema, and segment allocation rules
- source: docs/decisions/0004-evidence-pack-chunking-contracts.md
- status: locked (Accepted)
- decision: Packs are written to data/06_evidence_packs/{lecture_id}/pack_{sequence:03d}.json; data/05_evidence_packs/ is removed and must not be resurrected. EvidencePack carries lecture_id (SafeIdentifier), pack_sequence, start_time, end_time, anchor_frame | None, segments. Each frame opens a window to the next frame; the last window ends at max(segment.end_time); a pre-first-frame window exists whenever the first frame is after 0. Cross-boundary segments go whole to the window with majority overlap (ties to the earlier window); zero-duration segments use half-open containment. UnboundedLectureEndTimeError and DegenerateWindowError are named failures. EvidencePackOrchestrator(queue_path, concurrency_limit=4) writes each lecture's pack set through a .staging directory and a .previous rename swap, and runs the build via asyncio.to_thread.
- scope: Phase 4 evidence packs; frame-driven chunking; atomic directory swap

## ADR-0005: Semantic ledger extraction contracts: SemanticLedger container schema, llama.cpp grammar contract, and orchestrator discovery model
- source: docs/decisions/0005-semantic-ledger-extraction-contracts.md
- status: locked (Accepted; superseded by ADR 0038 as to the local execution route; errata from ADR 0020 and ADR 0022 are historical)
- decision: SemanticFact (entity_name, category, clinical_context, timestamp_ref) is the per-fact model; SemanticLedger (lecture_id, pack_sequence >= 1, facts list, default empty) is the Phase 5 handoff. Echoed lecture_id/pack_sequence are verified against the source pack (LlmParsingError on mismatch). LlmParsingError is the single worker exception. Prompt and schema temp files are removed on every exit path. Stdout is scanned for balanced, string-aware top-level {...} spans, each validated in turn. The orchestrator discovers packs by directory scan filtered through SafeIdentifier (excluding .staging/.previous), isolates failures per pack, and writes ledger_NNN.json atomically per pack. The extraction contracts survive in ops/llm_instructions.py and scripts/generate_codex_semantic_ledgers.py (ADR 0038).
- scope: Phase 5 semantic extraction; data/07_semantic_ledgers

## ADR-0006: Learning Unit draft synthesis contracts: LearningUnitDraft schema, two-artifact grounding, and the accepted residual hallucination risk
- source: docs/decisions/0006-learning-unit-draft-synthesis-contracts.md
- status: locked (Accepted; superseded by ADR 0038 as to the local execution route; errata from ADR 0020 and ADR 0022 are historical)
- decision: LearningUnitDraft (lecture_id, pack_sequence >= 1, title, body_markdown, clinical_warnings default empty). The prompt embeds both the Evidence Pack and the Semantic Ledger and forbids inventing clinical data absent from the Ledger. Traceability is checked on LLM output and on the loaded ledger's own lecture_id/pack_sequence. Failures isolate per pack; draft_NNN.json is written atomically under data/08_drafts/{lecture_id}/. Each phase module is self-contained (imports only domain.models). Accepted residual risk: no structural grounding check on clinical_warnings/body_markdown; human review against the ledger is the closing control, and a future phase may add an automated grounding check.
- scope: Phase 6 drafting; Golden Rule (no invented clinical data); module self-containment rule

## ADR-0007: Workspace migration and the Critic & Repair Loop: workspace/data/ root, CriticReview/ValidatedLearningUnit contracts, and feedback-driven regeneration
- source: docs/decisions/0007-workspace-migration-and-critic-repair-loop.md
- status: locked (Accepted; superseded by ADR 0038 as to the local execution route; the repair loop lives on in scripts/generate_codex_critic_review.py per ADR 0036; errata from ADR 0020 and ADR 0022 are historical)
- decision: All execution data moves under gitignored workspace/data/. CriticReview (passed, hallucination_score in [0,1], feedback required) and ValidatedLearningUnit (lecture_id, pack_sequence, content: LearningUnitDraft, review: CriticReview) are added. The critic runs at temperature 0.0. critic/pipeline.py imports drafting generate_draft as a narrow, named exception to ADR 0006 self-containment. CriticOrchestrator runs with concurrency 1. The Repair Loop evaluates, then regenerates with the rejecting feedback up to max_retries (default 2), always persisting the last (draft, review) pair to workspace/data/09_validated_units/{lecture_id}/unit_{sequence:03d}.json, whether passed or exhausted. Intermediate drafts are not written back to 08_drafts.
- scope: Phase 7 critic and repair loop; workspace/data root; ValidatedLearningUnit

## ADR-0008: Document Assembly: canonical document contract, and a domain-level error taxonomy
- source: docs/decisions/0008-document-assembly-and-domain-error-taxonomy.md
- status: locked (Accepted)
- decision: CanonicalLectureDocument (lecture_id, total_units == len(units), units in pack_sequence order, assembled_at) is assembled deterministically by DocumentAssembler.assemble_lecture. Validation order: SafeIdentifier check, LectureNotFoundError if 09_validated_units/{lecture_id}/ is missing (an existing empty directory assembles to zero units), MissingSequenceError unless pack_sequence is exactly {1..n} (message distinguishes duplicate from gap), UnresolvedReviewError on any review.passed false. Output: workspace/data/10_canonical_documents/{lecture_id}/canonical_document.json. The three errors live in domain/errors.py; chunker.py errors are grandfathered as module-local. A partially assembled document is rejected ("Tolerancia Zero").
- scope: Phase 8 assembly; domain/errors.py taxonomy

## ADR-0009: Global Metadata Aggregation: deterministic text-normalization deduplication
- source: docs/decisions/0009-deterministic-metadata-deduplication.md
- status: locked (Accepted; Consequences superseded in part by ADR 0012 (glossary_terms shape) and ADR 0011 (persistence))
- decision: LectureGlobalIndex (lecture_id, glossary_terms, dosages, red_flags) replaces the instruction's name LectureMetadata, which already exists. MetadataAggregator.aggregate_lecture validates lecture_id, raises LectureNotFoundError if canonical_document.json is absent, and raises SemanticLedgersNotFoundError if 07_semantic_ledgers/{lecture_id}/ is absent (an empty existing directory is a valid zero-fact outcome). Deduplication is deterministic (no LLM): _normalize_text (lowercase, collapse whitespace, strip edge punctuation); glossary dedupes on the normalized entity_name, dosages/red_flags on the (entity, context) tuple; first-seen wins in pack_sequence order. Category routing is keyword substring matching; a fact matching both keyword sets goes to both lists. Deep semantic paraphrases are not merged (accepted risk).
- scope: Phase 9 metadata aggregation; deduplication; category routing

## ADR-0010: Local PII At-Rest Encryption: Risk Acceptance via macOS FileVault
- source: docs/decisions/0010-local-pii-encryption-risk-acceptance.md
- status: locked (Accepted, scoped to local storage; scoped by ADR 0044; ADR 0045 section 2 governs content sent to remote services and deployed to the site)
- decision: Accept the missing at-rest encryption risk for workspace/data/ and delegate at-rest protection to macOS FileVault. No lecture_study code encrypts or transforms workspace/data artifacts for confidentiality. The "no cloud egress" premise no longer holds for the whole pipeline (Status text); confidentiality of content sent to LLM services or deployed to the site is governed by other ADRs. Must be revisited for a shared workstation, cloud sync of workspace/data, or a second operator account.
- scope: workspace/data at-rest confidentiality; FileVault; PII risk acceptance

## ADR-0011: Markdown Compilation and Publishing Pipeline
- source: docs/decisions/0011-markdown-compilation-and-publishing-pipeline.md
- status: locked (Accepted)
- decision: MetadataAggregator.aggregate_lecture now persists workspace/data/11_metadata/{lecture_id}/global_index.json. MarkdownCompiler.compile(document, index) renders header, lecture body (one "##" per unit) and a clinical appendix (Glossario, Dosagens, Alertas Vermelhos) with explicit placeholders when empty. PublishingOrchestrator.publish_lecture reads canonical_document.json and global_index.json directly (LectureNotFoundError if missing) and writes workspace/data/12_published/{lecture_id}/study_guide.md and study_guide.html via MarkdownIt("commonmark", {"html": False}). markdown-it-py is a direct dependency. LLM-extracted free text is escaped by _escape_markdown (CommonMark punctuation plus newline collapsing); body_markdown is not escaped. PDF export is out of scope (DIRETRIZES names Typst). The .md and .html writes are individually atomic but not jointly atomic.
- scope: Phases 10-11 compilation and HTML publishing; HTML/Markdown injection controls

## ADR-0012: Deterministic Anki Flashcard Generation (CSV Export)
- source: docs/decisions/0012-deterministic-flashcard-generation.md
- status: locked (Accepted)
- decision: Flashcard (front, back, tags) is generated without an LLM by AnkiDeckGenerator.generate_deck from LectureGlobalIndex, in glossary/dosages/red_flags order, written to workspace/data/13_flashcards/{lecture_id}/anki_deck.csv with csv.writer (no header, three columns). LectureGlobalIndex.glossary_terms changes from list[str] to list[SemanticFact] so glossary Backs carry clinical_context. GlobalIndexNotFoundError subclasses LectureNotFoundError. Fields are sanitized: newlines become <br>; a field whose raw or whitespace-stripped form starts with =, +, -, @ or tab is prefixed with a single quote (CWE-1236).
- scope: Phase 12 Anki deck; CSV injection guard

## ADR-0013: LLM Mock Exam Generation (Forced JSON Extraction + Retry)
- source: docs/decisions/0013-llm-mock-exam-generation.md
- status: locked (Accepted; superseded by ADR 0038: the local exams/generator.py is deleted and exams come from scripts/generate_codex_mock_exam.py, the deterministic compiler survives; errata from ADR 0020, 0022, 0027)
- decision: MockQuestion (question_text, options with exactly keys A/B/C/D, correct_answer within options, explanation) and MockExam (lecture_id: SafeIdentifier, questions). MockExamGenerationError lives in domain/errors.py by explicit instruction despite the recorded domain-policy-versus-technical tension. Original design read canonical_document.json, asked for 5 questions, and used one repair retry on JSON/Pydantic failure only. ExamCompiler writes workspace/data/14_mock_exams/{lecture_id}/exam.json and simulado.md (sorted options, answer key after a divider, free text escaped).
- scope: Phase 13 mock exam models and compiler

## ADR-0014: LLM Podcast Script Generation (Two-Speaker Deep Dive, Unit Traceability)
- source: docs/decisions/0014-llm-podcast-script-generation.md
- status: locked (Accepted; superseded by ADR 0038: the local podcast/generator.py is deleted and scripts come from scripts/generate_codex_podcast.py per ADR 0037, the deterministic compiler survives; errata from ADR 0020 and ADR 0022)
- decision: PodcastLine (speaker Literal["Host","Expert"], non-blank text, source_unit_id) and PodcastScript (lecture_id: SafeIdentifier, non-blank title, dialogue min_length 1). Unit ids are "unit-{pack_sequence:03d}". Traceability check rejects a lecture_id mismatch and any source_unit_id absent from the document, collecting every offending line before raising. One repair retry. PodcastGenerationError sits in domain/errors.py. PodcastScriptCompiler writes workspace/data/15_podcast_scripts/{lecture_id}/script.json and script_legivel.md (title and text escaped, including a literal quote). The shared llama.cpp worker duplication stays through Phase 16; a "Shared LLM Worker Base" is a named V2.0 item.
- scope: Phase 14 podcast script models and compiler

## ADR-0015: Local TTS audio generation: argument-list subprocess invocation and hardcoded PT-BR voices
- source: docs/decisions/0015-local-tts-audio-generation.md
- status: locked (Accepted; amended by ADR 0045 as to the local-only mandate for the LLM phases; erratum from ADR 0022; decision 4 (voices) and the hardcoded-voice rationale revised by ADR 0032)
- decision: ScriptNotFoundError subclasses LectureNotFoundError; AudioGenerationError is a plain RuntimeError. PodcastAudioGenerator.generate_podcast validates lecture_id, reads 15_podcast_scripts/{lecture_id}/script.json directly, writes numbered chunk files plus podcast_final.m4a to workspace/data/16_podcast_audio/{lecture_id}/, deletes only the intermediate .wav and concat_list.txt in a finally block, records each chunk path before its subprocess call, and unlinks a pre-existing podcast_final.m4a before ffmpeg. Subprocesses use argument lists with shell=False; shell=True is forbidden. Original voice mapping Host=Luciana, Expert=Felipe (superseded by ADR 0032).
- scope: Phase 15 TTS; subprocess argument-vector safety

## ADR-0016: Master CLI orchestrator: composition-root registry, filesystem-artifact resume, and RuntimeError/ValueError as the CLI's error boundary
- source: docs/decisions/0016-master-cli-orchestrator.md
- status: locked (Accepted; amended 2026-07-28 (lecture-scoped LLM phases, 14-entry registry for Phases 2-15, model-family option); amended by ADR 0044 (discover command removed); the --llm-model-family option is removed by ADR 0038 decision 7)
- decision: cli/orchestrator.py is the composition root and is exempt from the ADR 0006 self-containment rule. PHASE_REGISTRY of PhaseSpec entries (artifact_exists, async run) drives run-all (stops at first failing phase, skips phases whose artifact exists) and run-phase. PipelineExecutionError (RuntimeError) covers orchestration failures. main() catches (RuntimeError, ValueError) only. lecture_id is validated as SafeIdentifier at the top of run_phase and run_all. Phases 2-4 use a per-lecture temp queue filtered from queue.json (now written by scripts/register_local_lecture.py). 2026-07-28 amendment: Phases 5-7 accept an optional validated lecture_id and the CLI always passes it; resume checks are stage-specific (Phases 2-9 exact files or sequence equality; Phase 10 revalidates publication readiness; Phases 11-12 revalidate deterministic delivery state; Phases 13-15 always execute). Original whole-backlog Phases 5-7 handling is historical. Accepted risk: no cross-process lock on Metal-bound inference.
- scope: Phase 16 master CLI; run-all/run-phase; PHASE_REGISTRY; error boundary

## ADR-0020: llama-cli invocation contract correction: --json-schema-file and a mandatory -n completion cap
- source: docs/decisions/0020-llama-cli-invocation-contract-correction.md
- status: locked (Accepted; historical record per ADR 0038; --json-schema-file superseded by ADR 0022; extended by ADR 0034)
- decision: Every llama-cli call site passes --json-schema-file (not --json-schema) and an explicit -n cap (semantic 4096/1024, critic 8192/1024, drafting 8192/2048, exams 8192/4096, podcast 8192/4096 for -c/-n). The values are conservative estimates, not tuned. The bounded-subprocess discipline survives in ops/process_lifecycle.py, used by every surviving subprocess caller.
- scope: historical llama-cli invocation; bounded subprocess discipline

## ADR-0021: Scene-change detection with a temporal safety interval
- source: docs/decisions/0021-scene-change-detection-threshold.md
- status: locked (Accepted; amended 2026-07-28; temporal interval amended by ADR 0025)
- decision: The FFmpeg filter is select='isnan(prev_selected_t)+gt(scene,0.3)+gte(t-prev_selected_t,150)',showinfo. The 0.3 scene threshold replaced 0.03 (which tracked an on-screen clock); the 150-second safety interval (half the 300-second minimum Learning Unit) was added after a continuous-motion recording produced zero frames. The interval is not a visual-recall guarantee and does not replace the authenticated full-frame visual-observation boundary required by ADR 0023. Constants are centralized in extraction/media_workers.py.
- scope: Phase 2 frame selection; scene threshold; temporal safety frame

## ADR-0022: llama-cli structured-output workaround: drop --json-schema-file, add --single-turn, add a repair retry
- source: docs/decisions/0022-llama-cli-structured-output-workaround.md
- status: locked (Accepted; supersedes ADR 0020's --json-schema-file decision; historical record per ADR 0038)
- decision: Drop --json-schema-file and add --single-turn at every call site. The schema is embedded as prompt text and the module's _parse_* function validates each candidate span. semantic, critic and drafting gain a single repair retry. 2026-07-28 operational amendment: retry prompts receive trusted static guidance from the shared response contract, never the raw parsing exception or model bytes. The Codex scripts parse JSON under ops/llm_response_contract.py.
- scope: historical llama-cli structured output; retry feedback contract

## ADR-0023: Content completeness, quality gates, and parallel execution
- source: docs/decisions/0023-content-completeness-quality-gates-and-parallel-execution.md
- status: locked (Accepted as the target architecture, implementation incremental; stack note by ADR 0038; sections 15 and 16 superseded by ADR 0038 as recorded in ADR 0045 section 4; amended by ADR 0045)
- decision: Completeness is a blocking safety property; unknown completeness is failure (needs_review). Canonical content keeps both verbatim evidence and an atomic content ledger. Learning Units are conceptual (target five to ten minutes), not frame-driven. Generation uses evidence-aware hierarchical refinement. Evaluation is bidirectional and plural (source-to-material recall, material-to-source faithfulness, bidirectional QA, veterinary field audit, traceability audit, density alarm, human calibration); the same model may not be the sole generator and sole approver. Publication requires immutable, runtime-authenticated certificates; LLM derivatives need independent approvals. Execution is a resource-aware DAG with bounded pools (LLM generation 1, Whisper 1). New writes target a lecture-centric workspace layout while workspace/data remains until a tested migration. Tool selection is empirical. Sections 11-14 define the SourceUniverse, atomic ledger, authenticated source resolution and a later complementary-documents phase. Sections 15 and 16 (semantic runtime, three authenticated local llama.cpp actors) are superseded and not a planning target. Section 6 still defines the certified publication path, which stays unreachable (ADR 0045 section 4).
- scope: completeness gates; certification architecture; parallel DAG; reference corpus; source-universe contracts

## ADR-0024: Uncertified personal-study execution mode for Phases 13-15
- source: docs/decisions/0024-uncertified-personal-study-execution-mode.md
- status: locked (Accepted 2026-09-13; amended by ADR 0045: input is critic-approved units from 09_validated_units under the Phase 8 completeness rules, and the audience bound is widened to the allowlisted site audience)
- decision: An explicit, opt-in, separately named uncertified personal-study execution mode lets Phases 13 (exam), 14 (podcast) and 15 (TTS) run without a publication certificate. Phase 10 (QualityCertificateIssuer.issue) keeps raising QualityCertificationError unconditionally and require_publication_ready keeps gating phases above 10 on the certified path. The mode must not call QualityCertificateVerifier.verify and must not forge a certificate; the derivative quality calls are skipped for payloads carrying the UNCERTIFIED_PERSONAL_STUDY marker. Every artifact carries the marker in its own payload through a distinct generation-receipt classification. Phase 8's completeness contract is unchanged. As written, the mode grants no clinical, academic or publication authority, must not be exposed as a batch, multi-student or distribution-facing capability, and confers no standing toward certification. Certified and uncertified paths must share generation logic and differ only in verification and receipt calls.
- scope: Phases 13-15 personal-study route; certification boundary; uncertified marker

## ADR-0025: Denser slide-frame extraction for lecture 10002
- source: docs/decisions/0025-denser-slide-frame-extraction.md
- status: locked (Accepted; amends ADR 0021's temporal safety interval without replacing it)
- decision: _MAXIMUM_VISUAL_FRAME_GAP_SECONDS is lowered from 150 to 37.5 seconds. The scene threshold gt(scene,0.3), the frame filename convention, the frame glob and the showinfo parsing contract are unchanged. A test binds the constant to the stated value. Lectures extracted before this record keep the 150-second density until explicitly re-extracted; only lecture 10002 is re-extracted under this record.
- scope: Phase 2 temporal safety interval

## ADR-0026: Dotenv resolution for the quality trust key
- source: docs/decisions/0026-dotenv-quality-trust-key-resolution.md
- status: locked (Accepted; amended by ADR 0044)
- decision: HmacQualityTrust.from_environment() resolves LECTURE_STUDY_QUALITY_HMAC_KEY_B64 from the process environment first, then (only when no explicit environ mapping was given) from a dotenv file, else fails closed with QualityTrustError. Amendment (ADR 0044): the dotenv step reads only that single key from the current working directory's own .env via dotenv_values, never searches parent directories and never writes to os.environ. sanitized_child_process_environment() keeps stripping LECTURE_STUDY_QUALITY_* variables from child processes. The registry-root prohibition of ADR 0023 is stated to govern a different artifact and is not relaxed.
- scope: quality trust key resolution; child-process environment sanitization

## ADR-0027: Student-facing text guard for generated study material
- source: docs/decisions/0027-student-facing-text-guard.md
- status: locked (Accepted)
- decision: Student-facing generators (exam, personal-study flashcards, podcast) demand Brazilian Portuguese, forbid referring to the source material, and label their input "Lecture material" instead of "Evidence Pack". quality/student_facing_text.py deterministically reports three defect classes (internal pipeline abstraction named, source-material reference, not pt-BR). Each generator runs the guard inside its bounded repair-retry path; a second defective attempt fails the run and no defective artifact is written. Retry carries closed-vocabulary feedback, not the offending text. The language check requires two distinct English function words (one for passages of eight words or fewer).
- scope: exam, flashcard and podcast generators; pt-BR and no-source-reference guard

## ADR-0028: ASR vocabulary prompt and deterministic terminology correction
- source: docs/decisions/0028-asr-vocabulary-prompt-and-terminology-correction.md
- status: locked (Accepted)
- decision: Two mechanisms. (1) whisper_worker.py passes DOMAIN_VOCABULARY_PROMPT as --prompt with --carry-initial-prompt; it names no discipline and no fascia term and lists only terms whose observed defect is a dropped first syllable. (2) transcription/terminology.py rewrites estopatologic-/estopatologia unconditionally, and the fascia adjective after camada/plano/margem only in a lecture that pairs that phrase with a depth context at least once; every rewrite is recorded in 05_transcribed/{id}_terminology_corrections.json, written before the transcript because the transcript is the idempotency marker. Transcripts already on disk are not rewritten; re-transcription of the existing corpus is deferred.
- scope: Phase 3 ASR prompt; terminology correction audit record

## ADR-0029: Study site scope, video delivery and access control
- source: docs/decisions/0029-study-site-scope-and-access.md
- status: locked (Accepted; access control amended by ADR 0039 and ADR 0044; amended by ADR 0045: every page that renders study material shows an uncertified notice)
- decision: Lecture videos are published with the lecture, amending SITE-03: workspace/site/ (generator output) and workspace/site-videos/ are separate trees served at / and /videos behind the same authentication middleware. Phase 6 is brought forward and generated from whatever lectures are processed (unprocessed lectures are listed as pending). Access is an email allowlist with a password per person in a credentials file mounted from outside the image and read on each request.
- scope: study site; video delivery; allowlist access

## ADR-0030: Critical-review document merge and Codex-based mock-exam route
- source: docs/decisions/0030-critical-review-merge-and-codex-exam-route.md
- status: locked (Accepted; superseded by ADR 0038 as to dual-route reachability; amended by ADR 0045: the Codex mock-exam script reads critic-approved units from 09_validated_units, not 08_drafts)
- decision: One merged document critical_review_document (resumo_critico) replaces the critical summary and the SUMMARY edition (apostila_resumida retired); the figure atlas is deleted. MockQuestion gains optional pack_sequence. Exam question count scales with content: one question per grounded ledger fact per unit, capped (--questions-per-unit-cap default 5) with a total floor (--minimum-total-questions default 20). scripts/generate_codex_mock_exam.py calls codex exec once per unit (ChatGPT Pro subscription, not the API), embeds the schema as prompt text, uses the shared ops.llm_response_contract parser with one retry, and runs each call in a fresh empty scratch directory (never the repository checkout).
- scope: Codex mock-exam route; critical review merge; question count policy

## ADR-0031: Standardized figure crop rectangle, applied by default
- source: docs/decisions/0031-per-lecture-figure-crop-rectangle.md
- status: locked (Accepted; amended by ADR 0044: the default is now no crop)
- decision: Historical decision: DEFAULT_FIGURE_CROP_RECT = 1940:1080:300:0 applied by default in scripts/generate_personal_study_guides.py, overridable with WIDTH:HEIGHT:X:Y or none. Current behavior per ADR 0044: --figure-crop-rect defaults to no crop; an explicit rectangle is still accepted.
- scope: figure cropping in personal study guides

## ADR-0032: Kokoro (mlx-audio) replaces macOS `say` as the podcast TTS engine
- source: docs/decisions/0032-kokoro-mlx-podcast-tts-engine.md
- status: locked (Accepted; amended by ADR 0045 as to the local-only mandate for the LLM phases; addendum 2026-09-17; revises ADR 0015 decision 4)
- decision: Kokoro-82M (mlx-community/Kokoro-82M-bf16) served through the mlx-audio CLI replaces say, invoked as python -m mlx_audio.tts.generate with argument list, shell=False, sanitized environment, finite timeout and bounded output. Voices: Host pf_dora, Expert pm_alex. --join_audio is mandatory. Added dependencies: mlx-audio, misaki, phonemizer-fork, espeakng-loader (no torch, no ONNX Runtime). ffmpeg concatenation is unchanged. ADR 0015 decisions 1-3 still govern.
- scope: Phase 15 TTS engine and voices

## ADR-0033: Exam questions illustrate with the same figure already selected for their unit
- source: docs/decisions/0033-exam-question-figure-reuse.md
- status: locked (Accepted)
- decision: inference_mock_exam_document accepts figures_by_pack_sequence and looks up question.pack_sequence, taking the first (only) candidate; no new selection heuristic, no MockQuestion or exam.json schema change. StudyDocumentSection gains optional item_figures aligned by index to items. Scope is the "Questoes" page only; the deterministic StudyQuestion route and critical_review_document are unaffected. A question without a figure renders as before.
- scope: exam figure rendering in study_materials/bundle.py

## ADR-0034: Retry-only repeat-penalty for the shared llama-cli invocation contract
- source: docs/decisions/0034-retry-repeat-penalty-for-llama-cli-workers.md
- status: locked (Accepted; historical record per ADR 0038; extends ADR 0020)
- decision: In semantic and critic workers, the feedback-carrying retry attempt appends --repeat-penalty 1.3 --repeat-last-n 256; the first attempt stays at --temp 0.0 with no penalty. Exams, podcast and drafting (temperature 0.2) are out of scope. The workers are deleted with the local route.
- scope: historical llama-cli retry sampling

## ADR-0035: Codex CLI replaces local qwen3-32b for semantic extraction (Phase 5) and critic review (Phase 7)
- source: docs/decisions/0035-codex-route-for-semantic-extraction-and-critic.md
- status: locked (Accepted; superseded by ADR 0038 as to dual-route reachability; decision 4 revised by ADR 0036)
- decision: Phases 5 and 7 move to the Codex CLI: one codex exec per pack/draft, self-contained prompt, --sandbox read-only, scratch cwd, shared ops.llm_response_contract parser and build_llm_retry_feedback, one retry. New scripts scripts/generate_codex_semantic_ledgers.py and scripts/generate_codex_critic_review.py import the instruction text from the former local workers. The automatic repair loop was out of scope here and is decided in ADR 0036. The local orchestrators were left in place at the time; ADR 0038 deletes them.
- scope: Phase 5 and 7 Codex route

## ADR-0036: Codex critic repair loop and instruction calibration
- source: docs/decisions/0036-codex-critic-repair-loop-and-instruction-calibration.md
- status: locked (Accepted; revises ADR 0035 decision 4)
- decision: scripts/generate_codex_critic_review.py gains _evaluate_with_repair: on rejection, redraft through generate_codex_drafts._generate_draft(feedback=review.feedback) and re-review up to --max-retries (default 2); the last result is persisted even if still failing. _build_prompt/_generate_draft gain optional feedback. ValidatedLearningUnit lecture_id/pack_sequence are anchored to the trusted pack, not the repaired draft. The critic instruction was recalibrated to separate wording from substance (a faithful paraphrase is not a violation; dropping or narrowing list items still is). The sibling-script private-function import is a recorded coupling.
- scope: Phase 7 Codex critic repair loop

## ADR-0037: Codex Route for Podcast Script Generation (Phase 14)
- source: docs/decisions/0037-codex-podcast-script-route.md
- status: locked (Accepted; decision 2 superseded by ADR 0045)
- decision: (1) One whole-lecture codex exec call (not per unit), each unit tagged unit-{pack_sequence:03d}. (2) [Superseded by ADR 0045] grounding source was workspace/data/08_drafts; it is now critic-approved units from 09_validated_units. (3) Output is written through the existing PodcastScriptCompiler. (4) The ADR 0027 student-facing text guard is enforced on the parsed response inside the retry-once path. Recorded gaps: output-path collision with the local route (resolved by deletion in ADR 0038) and no evidence-pack size check for the single whole-lecture prompt (open).
- scope: Phase 14 Codex podcast route

## ADR-0038: Orchestrator-Driven Codex Phases and Full Local LLM Route Removal
- source: docs/decisions/0038-orchestrator-codex-integration-and-local-llm-route-removal.md
- status: locked (Accepted)
- decision: _run_phase_5/6/7/13/14 in cli/orchestrator.py invoke scripts/generate_codex_{semantic_ledgers,drafts,critic_review,mock_exam,podcast}.py {lecture_id} as child processes through ops.process_lifecycle.run_bounded_process (12h bound, 16 MiB stdout cap, explicit environment allowlist); failures raise PipelineExecutionError with digests and a persisted output file under tmp/codex_phase_failures/. The script exit code is the phase contract; resume predicates remain the completeness check. Phase 13 compiles simulado.md in the orchestrator; Phase 14 writes through the compiler in the script. The certified-route exam/podcast quality gates retire with the local route. OrchestratorContext.execution_mode and the --llm-model-path/--llm-model-family flags are removed; run-personal-study selects phases 8/9/13/14/15 and never calls require_publication_ready. The local LLM route is deleted entirely, including the dual-route clauses of ADR 0013, 0014, 0030 and 0035; Whisper/ASR, the local TTS voice and llama-mtmd-cli vision captioning remain local. Accepted gap: a script that succeeds with a partial artifact set is caught only by the next run's resume predicate.
- scope: orchestrator Codex integration; local LLM route removal; engines split

## ADR-0039: Phone sessions, per-person revocation and edge-cache isolation
- source: docs/decisions/0039-phone-sessions-and-edge-cache-isolation.md
- status: locked (Accepted; amends ADR 0029; addendum 2026-09-17; amended by ADR 0044)
- decision: Every authorized response sends Cache-Control: private, no-store (the public landing page stays cacheable). Session tokens are signed with a key mixing a dedicated server secret with that person's stored password hash, and validation requires the address to be in the allowlist as read now. The allowlist is mounted as a directory (mode 700, files 600); the signing secret lives in a separate directory and is never synced with it. site.sessions never touches the filesystem. Password checking is serialized per address with a growing wait (never a lockout), a capped in-flight queue and a global concurrency limiter that sheds load with 429; waits are keyed by address attempted, never by network address; request bodies are bounded. Basic Auth stays for scripts, health check and deploy. Addendum: denial redirect carries a validated next parameter and GET /sair clears the cookie; single-device revocation is still impossible without rotating the password.
- scope: study site sessions; revocation; cache isolation; password throttling

## ADR-0040: A dedicated full-page video viewer, coexisting with the embedded player
- source: docs/decisions/0040-dedicated-video-watch-page.md
- status: locked (Accepted)
- decision: Generate assistir.html per lecture at site generation time next to index.html. The embedded player stays and gains an "Assistir em tela cheia" link. The page reuses the existing /videos/{lecture}/{video} route with no new route and inherits the same authentication, Cache-Control and Vary: Cookie. No captions or transcript track ships. A lecture with no video gets no page and no link.
- scope: study site watch page

## ADR-0041: Server-graded interactive mock exam
- source: docs/decisions/0041-server-graded-interactive-mock-exam.md
- status: locked (Accepted)
- decision: Grading is entirely server-side: site/quiz.py public_view strips correct_answer/explanation; GET /simulado/{lecture_id} renders only public questions; POST grades. exam.json is read directly from workspace/data/14_mock_exams/ via an environment-configured path and is never copied into or mounted from the served site tree. No signed token and no stored attempt state; the submission is graded by position against a fresh read of that lecture's exam.json. Routes reuse the session/Basic-Auth middleware unchanged. Submissions are throttled per authenticated email and lecture (key email:lecture_id) using AttemptDelay/WaitingRoom. The middleware stores the identity on request.state.pos_email. 2026-09-18 update: the lecture page links "Responder simulado" only when exam.json exists at generation time; the passive "Ler" link was dropped.
- scope: site interactive quiz; answer-key confidentiality; throttling

## ADR-0042: Local lectures and the private publication gate
- source: docs/decisions/0042-local-lectures-private-publication-gate.md
- status: locked (Accepted; extended by ADR 0043)
- decision: Local lectures use synthetic ids 925100-925199 (semester 1) and 925200-925299 (semester 2). A local lecture row carries publish: false by default; unpublished lectures compile into --private-output-root (default workspace/study-guides-local), a sibling tree never synced by deploy, and a private root nested in the public root is refused. Publishing requires --publish (or publish: true) and a recompile. generate_academic_catalog.py lists only published local lectures. Registration is transactional and idempotent, with a register-versus-register file lock; re-registration may change only the publish flag, and demotion is refused while a public bundle exists. scripts/list_withheld_lecture_ids.py excludes unpublished reserved ids from the study-guides and mock-exam rsync (deploy aborts if the list cannot be produced).
- scope: local lecture registration; private publication gate; deploy exclusion

## ADR-0043: Video-only local lectures
- source: docs/decisions/0043-video-only-local-lectures.md
- status: locked (Accepted; extends ADR 0042, the publication gate unchanged)
- decision: LocalLecture gains kind: study (default) or video-only (--video-only at registration). A video-only lecture compiles without reading evidence packs, ledgers, drafts or queue titles into a bundle holding exactly indice.md, .bundle.json and video/aula.mp4 (hard link), checked before atomic publication. It has no exam (list_withheld_lecture_ids.py exams lists published video-only ids). Registration creates the raw-video link and metadata row but no queue.json row, so batch download, extraction and Codex stages never see it. The site shows "Video sem material de estudo gerado." Source and public root must share a filesystem.
- scope: video-only lectures; site tolerance; deploy exclusion

## ADR-0044: Public extraction of the pipeline and study-site access hardening
- source: docs/decisions/0044-public-extraction-and-site-access-hardening.md
- status: locked (Accepted; amends ADR 0016, 0031, 0026, 0039; scopes ADR 0010 to local storage)
- decision: Portal discovery and acquisition, portal credentials and deploy tooling are excluded; lectures enter through scripts/register_local_lecture.py; config/targets.example.yaml documents the catalog; all lecture identifiers are synthetic. Leak gate: local hooks (pre-commit, pre-merge-commit, commit-msg, pre-push) with a private denylist kept outside the repository plus gitleaks, and CI jobs secret-scan, denylist-scan (fails closed without the DENYLIST secret) and test (ruff, mypy, pytest on macOS), with actions pinned by SHA. Site hardening: access decided on the decoded served path (400 on ?, #, backslash, NUL, dot segments), method-and-path-exact unauthenticated exemptions, SecurityHeadersMiddleware, hash-based CSP without unsafe-inline, __Host-pos_session cookie, signed double-submit login CSRF, pseudonymized authentication logging, non-root container uid/gid 10001. HmacQualityTrust reads only the single key from the working directory's own .env. Figure crop defaults to no crop; chapter plans come from an optional --chapter-plan YAML (default numbered "Parte N"); scripts/audit_semantic_ledgers.py, src/lecture_study/ingestion/ and ops.catalog_bridge.catalog_to_lectures are removed.
- scope: repository scope; leak gate; study-site access hardening; trust key resolution

## ADR-0045: Critic gate for derivative study material, remote-model egress, and site audience
- source: docs/decisions/0045-critic-gate-for-derivative-material-egress-and-audience.md
- status: locked (Accepted 2026-10-14; amends ADR 0024, 0030, 0001, 0010, 0015, 0032, 0023, 0029; supersedes ADR 0037 decision 2)
- decision: (1) Derivative scripts (generate_codex_mock_exam.py, generate_codex_podcast.py, generate_codex_personal_study_flashcards.py, generate_personal_study_guides.py) load units only through load_critic_approved_drafts in assembly/assembler.py, which reads 09_validated_units (post-repair content) and blocks the whole lecture when the Phase 6/7 directories or files are missing, the sequence has a gap or duplicate, any review.passed is false, drafts and units are not the same sequence set, a draft was modified after its unit, a draft's echoed pack_sequence disagrees with its file name, or a unit's identity disagrees with its storage location; a blocked lecture is never published partially; identity comes from the unit. Material generated before this decision must be regenerated or deleted. (2) The Codex route is used only for lecture content the operator has verified to carry no patient-identifying data; content that may carry such data is processed only by a local model route (the planned local adapter of the LLM gateway), and until it exists is not processed past Phase 4; the rule is the operator's responsibility and not enforced by code, and the provider's retention and training terms must be checked against the account. (3) ADR 0024's audience bound widens to the allowlisted site audience (ADRs 0029, 0039); the material stays uncertified and every page rendering study material carries UNCERTIFIED_NOTICE (src/lecture_study/site/renderer.py), including both mock-exam pages. (4) ADR 0023 sections 15 and 16 are superseded by ADR 0038 and not a planning target; section 6 still defines the certified path, which stays unreachable; any future certified route needs a new record on generator-evaluator independence. (5) Phase 18 is scripts/generate_codex_personal_study_flashcards.py, a standalone operator-run script outside the orchestrator, writing to workspace/data/18_personal_study_flashcards, independent of the Phase 12 Anki deck. (6) Citations between records are ordered only by explicit amends/supersedes statements in each Status section, which form an acyclic order; a citation in both directions is not a dependency cycle.
- scope: critic-approved input gate; remote-model egress rule; site audience and notice; ADR ordering rule

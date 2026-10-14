## Conflict Detection Report

### BLOCKERS (0)

### WARNINGS (3)

[WARNING] Remote-model egress rule depends on a local route that no record defines, and is not enforced by code
  Found: docs/decisions/0045-critic-gate-for-derivative-material-egress-and-audience.md section 2: "Content that may carry patient-identifying data is processed only by a local model route. The planned local adapter of the LLM gateway provides that route. Until it exists, such content is not processed past Phase 4." and "Applying this rule is the operator's responsibility; no technical control detects patient-identifying data before a call. The retention and training terms of the remote provider are not recorded here and must be checked against the account in use." The word "gateway" appears in no other ADR of the set (searched docs/decisions). docs/decisions/0038-orchestrator-codex-integration-and-local-llm-route-removal.md Context records the operator direction to "remove qwen3-32b and the llama.cpp LLM machinery from everything ... leaving local execution only for video transcription (Whisper/ASR), the local TTS voice, and vision captioning", and its decision 8 deletes the local route. docs/decisions/0010-local-pii-encryption-risk-acceptance.md Status delegates site-deployed confidentiality to "those ADRs"; ADR 0029 records access control (the allowlist) as the control for the site, and no ADR text read states a patient-data check for the lecture videos the site serves (ADR 0029 decision 1).
  Impact: The amendment of the local-only mandate (ADRs 0001, 0010, 0015, 0032) is explicit and resolves the earlier contradiction. What remains open: content that may carry patient-identifying data has no processing route past Phase 4 until an undefined component exists; the planned local adapter sits in tension with the ADR 0038 removal direction; the provider's retention and training terms are unrecorded; the rule is procedural only.
  → Confirm the rule as written is accepted for roadmapping, and either record the LLM gateway and its local adapter in a new ADR (or state none is planned) and record the check of the provider terms. The roadmapper must not plan a local LLM phase from ADR 0045 section 2 alone.

[WARNING] ADR 0023 text that restates the superseded runtime is not covered by the named supersession
  Found: docs/decisions/0045-critic-gate-for-derivative-material-egress-and-audience.md section 4: "Sections 15 and 16 were superseded by ADR 0038; this record states it explicitly. They are not a planning target." and "Section 6 still defines the certified publication path, which stays unreachable (ADR 0024)." docs/decisions/0023-content-completeness-quality-gates-and-parallel-execution.md Status carries the same two statements. In the same file, outside sections 15 and 16: (a) the unnumbered subsection "Production semantic runtime and promotion boundary" (line 1781), which says "Production execution requires three distinct GGUF hashes and three distinct effective actor identities for producer A, producer B, and the evaluator"; (b) the list "Implementation status through 2026-07-27" (line 1626), which describes the provider-neutral SemanticAnalysisRuntime, invocation registries and calibration authority; (c) section 6, which requires the production resolver to "reject symlinks, hardlinks, non-regular files, malformed GGUF metadata" and bind a derivative to a GGUF execution identity. docs/decisions/0038-orchestrator-codex-integration-and-local-llm-route-removal.md decision 5: "The certified-route exam/podcast quality gates retire with the local route", because they "took candidates bound to a llama.cpp model path".
  Impact: Section 6 is retained as the certified path while the model-path-bound derivative gates it relies on were retired. Blocks (a) and (b) are unmarked and read as live planning. A roadmapper reading ADR 0023 could plan the three-actor local runtime, the invocation registry or the GGUF execution identity.
  → Extend the supersession stamp (or ADR 0045 section 4) to name block (a), block (b) and the GGUF-bound paragraphs of section 6, or confirm they are to be read as historical. Until then the intel treats sections 15, 16, block (a), block (b) and the GGUF-bound part of section 6 as not a planning target.

[WARNING] ADR 0024 bounds on "batch" and "distribution-facing" use are not restated by the audience amendment
  Found: docs/decisions/0024-uncertified-personal-study-execution-mode.md: "Its sole purpose is producing study material for the single student who requested it. It must not be exposed as a batch, multi-student, or distribution-facing capability, and it confers no standing toward eventual production certification." docs/decisions/0045-critic-gate-for-derivative-material-egress-and-audience.md section 3: "ADR 0024's audience bound is widened from the single requesting student to the allowlisted site audience defined by ADRs 0029 and 0039." ADR 0045 Consequences also describes a multi-lecture run: "the study-guide script stops at the first blocked lecture in a multi-lecture run."
  Impact: The single-student contradiction with the study site is resolved by ADR 0045. The sentence on "batch" and "distribution-facing" capability is not named by ADR 0045, so it is unclear whether the bound was lifted with the audience or still limits multi-lecture runs and site delivery.
  → Confirm that "audience bound" in ADR 0045 covers the whole sentence quoted from ADR 0024 (batch, multi-student and distribution-facing), or amend ADR 0024 to say which parts remain. The intel treats the uncertified mode as usable by the allowlisted audience, and the "no standing toward certification" clause as still in force.

### INFO (19)

[INFO] Auto-resolved (prior BLOCKER): personal-study input source, ADR 0024 and ADR 0030 versus ADR 0037 decision 2
  Found: ADR 0024 required the uncertified route to build its input from the Phase 8 canonical document; ADR 0037 decision 2 grounded the podcast on workspace/data/08_drafts, and the Codex exam and flashcard scripts read drafts directly.
  Note: ADR 0045 section 1 states "Supersedes ADR 0037 decision 2", "amends ADR 0024: the input is no longer the Phase 8 document, but the Phase 8 gate still governs it", and amends the input of the ADR 0030 script. The Status sections now carry the same statements (ADR 0024: "Amended by ADR 0045"; ADR 0030: "Amended by ADR 0045: the Codex mock-exam script reads critic-approved units from `09_validated_units`, not `08_drafts`"; ADR 0037: "Decision 2 superseded by ADR 0045"). Winner: ADR 0045 (later, explicit supersession); not treated as a LOCKED-versus-LOCKED conflict. Current rule: units come from load_critic_approved_drafts over 09_validated_units; a rejected, stale or unreviewed unit blocks the whole lecture. Sources: docs/decisions/0045-critic-gate-for-derivative-material-egress-and-audience.md, 0024-uncertified-personal-study-execution-mode.md, 0030-critical-review-merge-and-codex-exam-route.md, 0037-codex-podcast-script-route.md.

[INFO] Auto-resolved (prior WARNING): citation cycles
  Found: Reciprocal citations between ADRs (for example ADR 0029 and ADR 0039, ADR 0016 and ADR 0044, ADR 0020 and ADR 0022). In the classifier cross_refs graph a cycle exists among ADR 0009, ADR 0011 and ADR 0012 (0009 cites 0011 and 0012; 0011 cites 0012; 0012 cites 0009 and 0011).
  Note: ADR 0045 section 6 states that "Precedence follows only the explicit amends and supersedes statements in each record's Status section, and those form an acyclic order. A citation in both directions is therefore not a dependency cycle." Verified: depth-first search over the amends, supersedes, revises and extends edges taken from the Status sections of all 42 records finds 0 cycles. The 0009/0011/0012 cycle consists of ADR 0009 Consequences notes pointing forward to the records that changed it (ADR 0012 and ADR 0011) and the back-citations those records make; the amends order is 0009, then 0011, then 0012. All 42 records were synthesized.

[INFO] Auto-resolved (prior WARNING): local-only mandate versus the Codex route
  Found: ADR 0001, ADR 0010, ADR 0015 and ADR 0032 cite a local-only mandate (no cloud egress for AI processing); ADRs 0030, 0035, 0037 and 0038 send lecture content to the Codex CLI.
  Note: ADR 0045 section 2 amends the mandate for the LLM phases, and the Status sections of ADR 0001, 0015 and 0032 carry "Amended by ADR 0045"; ADR 0010 Status limits itself to local storage and points to ADR 0045 section 2. Winner: ADR 0045. The residual open items are in the first WARNING.

[INFO] Auto-resolved (prior WARNING): ADR 0023 sections 15 and 16 after ADR 0038
  Found: ADR 0023 sections 15 and 16 describe a local llama.cpp runtime with three authenticated actors, which ADR 0038 deleted.
  Note: ADR 0023 Status now reads "Sections 15 and 16 were superseded by ADR 0038, as ADR 0045 section 4 records", and ADR 0045 section 4 says they are not a planning target. Winner: ADR 0038 as recorded by ADR 0045. The residual unmarked text is in the second WARNING.

[INFO] Auto-resolved (prior WARNING): single-student bound of ADR 0024 versus the study site
  Found: ADR 0024 limited the mode to the single student who requested the material; ADRs 0029, 0039, 0041 and 0042 deliver the material to an allowlisted audience.
  Note: ADR 0045 section 3 widens the audience to the allowlisted site audience, keeps the material uncertified, and requires the notice "Material de estudo gerado automaticamente a partir da aula, sem certificação nem revisão clínica humana. Confira sempre com a fonte original." on every page that renders study material (ADR 0029 Status carries "Amended by ADR 0045"; ADR 0024 Status carries the audience widening). The residual wording is in the third WARNING.

[INFO] Auto-resolved (prior WARNING): Phase 18 flashcard route undefined
  Found: ADR 0037 Context refers to "Phase 18 (personal-study flashcards)" with no definition.
  Note: ADR 0045 section 5 defines Phase 18 as scripts/generate_codex_personal_study_flashcards.py, a standalone operator-run script outside the orchestrator, writing to workspace/data/18_personal_study_flashcards, independent of the deterministic Phase 12 Anki deck (ADR 0012), and subject to the critic gate of section 1. Consistent with ADR 0016 (registry covers Phases 2-15) and ADR 0038 (run-all and run-personal-study do not call it). Phase 17 and the vision-captioning phase (ADR 0038 calls it "Phase-X") have no number or definition in the set; ADR 0044 records that ADR numbers 0017-0019 are absent.

[INFO] Auto-resolved: ADR 0038 supersedes the local LLM route
  Found: ADR 0005, 0006, 0007 (local worker route), ADR 0013, 0014 (local generators), ADR 0030 and 0035 ("local route stays reachable") describe the qwen3-32b/llama.cpp route; ADR 0020, 0022 and 0034 describe its invocation contract.
  Note: ADR 0038 decisions 1 and 8 replace the route with Codex scripts run as child processes and delete the local workers; the Status sections of those ADRs carry "Superseded by ADR 0038" or "Historical record (ADR 0038)". Three statements are not stamped but are overtaken by ADR 0038 and treated that way: ADR 0036 Consequences ("the local CriticOrchestrator ... local route is untouched and still reachable"), ADR 0037 Consequences ("Known output collision, unresolved" with the local podcast generator, resolved once the local route is deleted), and ADR 0016's 2026-07-28 amendments on lecture-scoped Phases 5-7 and the --llm-model-family option (ADR 0038 decision 7 removes the --llm-model-path and --llm-model-family flags). The deterministic compilers (ADR 0013, 0014) survive.

[INFO] Auto-resolved: ADR 0022 supersedes the ADR 0020 flag decision
  Found: ADR 0020 mandates --json-schema-file and -n caps; ADR 0005, 0006, 0007, 0013, 0014 carry ADR 0020 errata.
  Note: ADR 0022 Status: "Supersedes ADR 0020's `--json-schema-file` decision"; the -n cap of ADR 0020 and the --repeat-penalty extension of ADR 0034 stand only as historical record after ADR 0038. Winner: ADR 0022, then ADR 0038.

[INFO] Auto-resolved: ADR 0025 amends the ADR 0021 temporal safety interval
  Found: ADR 0021 fixes the safety interval at 150 seconds and derives it from half the 300-second minimum Learning Unit; ADR 0025 lowers it to 37.5 seconds.
  Note: ADR 0025 Status: "This record amends ADR 0021's temporal safety interval; it does not replace ADR 0021", and the scene threshold gt(scene,0.3) is unchanged. Current value 37.5 seconds, bound by a test. The ADR 0025 title says "lecture 10002" while its decision applies "for every lecture, starting with a re-extraction of lecture `10002`"; lectures extracted earlier keep 150-second density until re-extracted.

[INFO] Auto-resolved: ADR 0032 revises the ADR 0015 voice mapping
  Found: ADR 0015 decision 4 hardcodes the macOS voices Luciana and Felipe via say.
  Note: ADR 0032 Status: "Revises ADR 0015 decision 4 (voice mapping)"; current engine Kokoro-82M via mlx-audio with Host pf_dora and Expert pm_alex. ADR 0015 decisions 1-3 (error taxonomy, argument-list invocation, shell=False) still govern. The ADR 0015 Status does not carry the forward note; ordering follows ADR 0032 being later.

[INFO] Auto-resolved: ADR 0044 amendments
  Found: ADR 0016 (discover command), ADR 0031 (default crop rectangle), ADR 0026 (dotenv resolution), ADR 0039 (site access) and ADR 0010 (local-storage scope).
  Note: ADR 0044 Status: "Amends ADR 0016 ..., ADR 0031 ..., ADR 0026 ..., and ADR 0039 ... Scopes ADR 0010 to local storage." Current behavior: no discover command, no default crop, single-key working-directory .env read without os.environ mutation, __Host-pos_session cookie with CSRF and CSP hardening.

[INFO] Auto-resolved: ADR 0039 and ADR 0045 amend ADR 0029
  Found: ADR 0029 decision 3 (password per person, checked on every request); the later phone sessions and the uncertified notice.
  Note: ADR 0039 "Amends ADR 0029" (signed sessions, per-person revocation, Cache-Control: private, no-store, throttling); ADR 0045 adds the uncertified notice. ADR 0044 amends ADR 0039 in turn. Order: 0029, then 0039, then 0044, with 0045 on 0029.

[INFO] Auto-resolved: ADR 0036 revises ADR 0035 decision 4; ADR 0043 extends ADR 0042
  Found: ADR 0035 decision 4 leaves the Codex critic without a repair loop; ADR 0042 registration writes a queue row, while ADR 0043 states that registration of a video-only lecture "does NOT add a `queue.json` row".
  Note: ADR 0036 Status "Revises ADR 0035 decision 4" and ADR 0043 Status "Extends ADR 0042 (the publication gate is unchanged)". Winner: the later record in each pair. ADR 0043 also states that a video-only lecture compiles without reading drafts, so the critic gate of ADR 0045 section 1 does not apply to it.

[INFO] Auto-resolved: ADR 0009 Consequences, ADR 0003 open item, and path roots
  Found: ADR 0009 decision 1 types glossary_terms as list[str] and does not persist the index; ADR 0003 leaves data/05_evidence_packs renumbering open; ADRs 0002-0006 use data/ paths.
  Note: ADR 0009 Consequences mark both points superseded by ADR 0012 (list[SemanticFact]) and ADR 0011 (global_index.json persisted); ADR 0004 decision 1 resolves the numbering item by removing data/05_evidence_packs; ADR 0007 decision 1 moves every path under workspace/data/ and says the historical ADRs stay unchanged.

[INFO] Target architecture (ADR 0023) versus implemented state
  Found: ADR 0023 Status: "Accepted as the target architecture. Implementation is incremental". Section 8 says new writes use a lecture-centric layout while "The current numbered `workspace/data/` tree remains untouched until a non-destructive migration is tested"; all later ADRs (0024, 0028, 0037, 0041, 0045) read and write workspace/data/NN_* paths. Section 3 targets conceptual Learning Units and ADR 0023 states the frame-driven Evidence Packs "remain uncertifiable". Section 5 states "The same model may not be the sole generator and sole approver", while ADR 0035 and 0036 use the Codex CLI route for both drafting and critic review.
  Note: Not a conflict between accepted records: the layout migration and the certified path are not implemented, and the personal-study material is uncertified by ADR 0024. ADR 0045 section 4 defers generator-evaluator independence to a new record for any certified route. The intel uses workspace/data/NN_* paths as current.

[INFO] ADR 0006 residual-risk control versus uncertified delivery
  Found: ADR 0006 decision 6 and Consequences name human review of generated drafts, before they reach a learner, as the closing control for the unchecked-hallucination risk, and say a future phase may add an automated grounding check.
  Note: ADR 0007 added that automated check (the critic); ADR 0024 and ADR 0045 section 3 accept uncertified delivery with the notice "sem certificação nem revisão clínica humana". Read as ordered by the later records; no human clinical review is claimed for the site material.

[INFO] Entry points and the Phase 10 gate (ADR 0024, ADR 0038)
  Found: ADR 0024: "Omitting it must continue to hit the existing fail-closed gate" and Phase 10 "keeps raising QualityCertificationError unconditionally". ADR 0038 Consequences: "`run-all` remains the single end-to-end command".
  Note: ADR 0038 decision 6 places the uncertified distinction in the entry point (run-personal-study selects phases 8/9/13/14/15 and never calls require_publication_ready). run-all therefore stops at Phase 10 on the certified path; the operable personal-study path is run-personal-study. Treated as consistent.

[INFO] Classification review
  Found: 42 of 42 inputs are type ADR, all locked true. ADR 0013 carries confidence medium (Status "Accepted" plus "Superseded by ADR 0038"); no input is UNKNOWN or low confidence; no PRD, SPEC or DOC input exists.
  Note: No pair of locked ADRs was found whose decisions contradict on the same scope without an explicit later amend or supersede statement, so no LOCKED-versus-LOCKED blocker was raised. No competing acceptance variants exist (no PRD input).

[INFO] References to documents outside the ingest set
  Found: The ADRs cite SITE-03/04/06/07/08 (ADR 0029), D-10 (ADR 0030), D-14 and D-23 and plan 01-07 (ADR 0025), plans 01-13 and 01-17 (ADR 0026), .prompts/fase*.md and DIRETRIZES_ARQUITETURA.txt (ADRs 0001-0016, 0021), and memory notes by file name (ADRs 0035, 0037, 0038).
  Note: ADR 0044 states that .prompts/ and DIRETRIZES_ARQUITETURA.txt are not included in the repository. None of these sources was ingested; their content is not reproduced in the intel.

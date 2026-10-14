# 0038. Orchestrator-Driven Codex Phases and Full Local LLM Route Removal

## Status

Accepted.

**Amended by ADR 0046:** the local text-LLM route returns as the
`LlamaCppAdapter` behind the `LlmGateway` port; decisions 7-9 no longer
enumerate every local model runtime; the removed CLI flags stay removed.

## Context

The operator directed, emphatically and repeatedly: remove qwen3-32b and
the llama.cpp LLM machinery from everything — ADRs, infrastructure, code —
leaving local execution only for video transcription (Whisper/ASR), the local TTS voice, and vision captioning. The
agreed sequencing was to migrate every local-LLM phase to Codex first,
validate each, and only then delete the local route. As of ADR 0037 that
migration is complete: semantic extraction (0035), critic (0035/0036),
drafting, mock exam (0030), flashcards, and podcast (0037) all have Codex
generation scripts under `scripts/`.

Two decisions remained open before deletion, both resolved by the
operator on 2026-09-16:

1. What `run-phase`/`run-all`/`run-personal-study` should do for the
   LLM-backed phases once the local route is gone. **Chosen: integrate**
   — the orchestrator invokes the Codex generation scripts as child
   processes, so `run-all` remains a single end-to-end command, rather
   than leaving those phases as stub errors pointing at standalone
   scripts.
2. Whether the ADR 0013/0014/0030 dual-route decisions keep the local
   exam/podcast/flashcard generators alive. **Chosen: delete them all**
   — this ADR supersedes the "local route stays reachable" clauses of
   ADRs 0030 and 0035 and the dual-path reading of 0013/0014.

A dedicated audit (memory:
`2026-09-16-critic-validated-podcast-shipped-qwen-removal-started.md`,
section H) mapped the full deletion scope; two hidden-coupling passes
after that audit found further importers the audit missed
(`test_student_facing_text.py`, `test_delivery_certification.py`,
`semantic/runtime.py`), confirming the handoff's instruction to re-grep
before every deletion step.

## Decisions

1. **The orchestrator runs the Codex scripts as child processes.**
   `_run_phase_5/6/7/13/14` in `cli/orchestrator.py` invoke
   `scripts/generate_codex_{semantic_ledgers,drafts,critic_review,
   mock_exam,podcast}.py {lecture_id}` through
   `ops.process_lifecycle.run_bounded_process` (bounded time and output,
   sanitized environment, `check=True`), wrapped in `asyncio.to_thread`
   to keep the orchestrator's async contract. A nonzero exit, timeout or
   output overflow surfaces as a `PipelineExecutionError` naming the
   phase, the script, the exit code and output digests, with the captured
   output persisted to a local file under `tmp/codex_phase_failures/`
   (never inlined into the message).
2. **The script's exit contract is the phase contract.** The local route
   isolated per-item failures and the orchestrator re-verified every
   outcome after the run (`_raise_if_incomplete`). The Codex scripts
   already enforce their own completeness (per-pack ledgers, per-unit
   traceability, retry/repair loops) and exit nonzero on any failure, so
   the orchestrator no longer re-verifies outcomes; the existing
   resume predicates (`_phaseN_artifact_exists`, which require full
   artifact sequences) remain the completeness check between runs.
3. **The time bound is generous, not pacing.** A whole-lecture Codex
   pass legitimately runs for hours on dense lectures (the critic with
   its repair loop ran hours on lecture 10005). The orchestrator-level
   bound (12h stdout-capped at 16 MiB, mirroring the scripts' own codex
   call caps) exists so a hung child cannot block the pipeline forever —
   not to pace the run (operator preference: quality over cost,
   compute is never a constraint).
4. **Deterministic compiles stay in the pipeline, not the scripts.**
   Phase 13: the script writes `exam.json`; the orchestrator loads it
   (`MockExam.model_validate_json`) and runs `ExamCompiler().compile()`
   for the readable `simulado.md`, mirroring the old local branch.
   Phase 14: the podcast script already writes through
   `PodcastScriptCompiler` itself (ADR 0037), so the orchestrator runs
   nothing after it.
5. **The certified-route exam/podcast quality gates retire with the
   local route.** `evaluate_mock_exam_quality`/`evaluate_podcast_script_
   quality` took candidates bound to a llama.cpp model path through
   `PathBackedDerivativeExecutionIdentityResolver` — meaningless without
   a local model. Their only caller was the orchestrator's certified
   branch; the certified chain never fully passed for any lecture (the
   sole certificate attempt on disk, 10003, is a blocked attempt), and
   the operator's actual route is ADR 0024 personal-study throughout.
   The Codex scripts pin `UNCERTIFIED_PERSONAL_STUDY` on their
   artifacts themselves.
6. **`OrchestratorContext.execution_mode` is removed.** With phases
   13/14 no longer branching on it, the uncertified/certified
   distinction lives entirely in which entry point is used
   (`run-personal-study` selects phases 8/9/13/14/15 and never calls
   `require_publication_ready`), not in a per-phase marker.
7. **`--llm-model-path`/`--llm-model-family` CLI flags are removed**
   (user-facing contract change, accepted by the operator with this
   ADR). `--whisper-model-path` stays: transcription is the only local
   model left.
8. **Whole-file deletion of the local LLM route**, in self-consistent
   steps each gated by the project's review discipline: the Phase 5/6/7
   worker/pipeline modules and their route-only scripts
   (`probe_llamacpp_capabilities.py`, `diagnose_critic_defect.py`,
   `run_actor_bakeoff.py`, `generate_personal_study_flashcards.py`);
   the Phase 13/14/18 local generators; then the shared plumbing
   (`ops/llama_prompt_budget.py`, `semantic/llama_cli_adapter.py`,
   `authenticated_runtime.py`, `runtime_profile.py`, `composition.py`,
   `llama_tokenizer.py`, plus `semantic/runtime.py`, whose only
   consumers were the plumbing modules themselves); then the ADR status
   stamps (0005/0006/0007 superseded-by notes, 0020/0022/0034
   historical-record stamps, 0023 content edit); finally the
   `engines/` split (llama.cpp build kept only for the
   `llama-mtmd-cli` binary that Phase-X vision captioning uses;
   `engines/models/` split so Whisper's `ggml-large-v3-turbo.bin`
   survives the Qwen weights removal; `01_provision_engines.sh`
   split accordingly).
9. **Explicitly out of scope, kept untouched**: Whisper/ASR
   (transcription, Phase 3) and `captioning/vlm_worker.py` (vision
   captioning via `llama-mtmd-cli`, a different binary under the same
   build tree). The engine split in decision 8 exists precisely so
   removing qwen3-32b does not break either.

## Consequences

- `run-all` remains the single end-to-end command: phases 2-4, 8-12 and
  15 run in-process; phases 5/6/7/13/14 invoke the Codex scripts. Resume
  semantics unchanged (artifact-existence predicates).
- The child environment is an explicit operational allowlist (PATH, HOME,
  locale, TMPDIR family), not the ambient environment: the scripts need
  exactly those, and ambient secrets the general sanitizer's blocklist
  does not recognize (credentialed proxy URLs) never reach a child.
- A failed script's captured stdout/stderr is persisted under
  `tmp/codex_phase_failures/` and referenced by path in the error; the
  message itself carries only digests, matching
  `llm_output_diagnostic`'s never-echo-model-bytes convention (child
  output can embed model bytes and full tracebacks). The same convention
  now governs schema-failure diagnostics:
  `ops/llm_response_contract.py::summarize_validation_issues` names the
  failing field path and error type (previously only the bare type,
  which named nothing), but emits a loc segment only when it is an
  integer index or a field name declared in the validated type's graph
  — pydantic also places dict keys in `loc`, and those are
  model-authored bytes that render as `<key>`, never verbatim.
- Interrupt behavior inherits `run_bounded_process`'s existing semantics
  (child in a new session): Ctrl+C stops the CLI first; the child is
  reaped by its own bounds. Pre-existing pattern, unchanged here.
- The orchestrator's stage-root constants for phases 5/6/7 and the
  scripts' argparse defaults duplicate the same path values by design
  (scripts stay standalone-runnable); a wiring test pins them together
  so they cannot drift silently.
- Known gap, accepted: a script that succeeds but writes a partial
  artifact set is caught only by the next run's resume predicate, not
  immediately (the local route caught it in-run). The scripts' own
  internal completeness enforcement makes this the unlikely path.
- ADRs 0013/0014/0030/0035's "local route stays reachable" language is
  superseded by this ADR; their decision texts stay as historical
  record with a status-line stamp pointing here.
- The deletion shrinks the codebase by roughly a dozen modules and
  several thousand lines of tests, and frees ~53 GB of Qwen weights
  after the engines split (Whisper's model file excluded).

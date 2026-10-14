# 0003. Transcription orchestrator contracts: handoff filename, output directory, orchestrator inputs, and single-flight concurrency

## Status

Accepted.

## Context

Phase 3 (`TranscriptionOrchestrator`, `src/lecture_study/transcription/pipeline.py`)
reads each queued lecture's Phase 2 canonical handoff and drives
`whisper.cpp` (Metal) transcription. The originating instruction
(`.prompts/fase3_whisper.md`) specified a handoff filename, an output
directory, and an orchestrator constructor signature that each conflicted
with either the existing codebase or an internal inconsistency in the
instruction itself; all three were resolved with the operator before
implementation instead of guessed.

## Decisions

1. **Canonical handoff filename.** Phase 3 reads
   `data/04_canonical/{lecture_id}.json`, matching what
   `ExtractionOrchestrator._save_intermediate` actually writes (ADR 0002,
   decision 3) and what `LectureExtractionResult` round-trips. The
   originating instruction named `{lecture_id}_handoff.json`, a filename
   that does not exist anywhere in the codebase; the operator confirmed the
   real Phase 2 output filename is authoritative.
2. **Transcription output directory.** Phase 3 writes to
   `data/05_transcribed/{lecture_id}_transcription.json`, a new top-level
   stage directory, per the originating instruction. This numbers the same
   as the pre-scaffolded (empty) `data/05_evidence_packs/` reserved for
   Phase 4's `EvidencePack` output, so the `data/` stage-number prefixes are
   not sequential across Phase 3 and Phase 4 until a future phase
   renumbers the scaffold. The operator chose to keep the new directory
   rather than renumber the existing scaffold or fold transcription output
   into `data/04_canonical/`.
3. **Orchestrator inputs.** `TranscriptionOrchestrator(queue_path: Path,
   model_path: Path, concurrency_limit: int = 1)`. The originating
   instruction's constructor signature was only `(concurrency_limit: int =
   1)`, which cannot supply `run_transcription`'s required `model_path` nor
   determine which lecture ids to process. The operator chose
   `queue_path` + `model_path` as explicit constructor parameters, mirroring
   `ExtractionOrchestrator(queue_path, ...)` (ADR 0002) as the same
   `data/queue.json` source of truth driving every phase, rather than
   discovering lecture ids by globbing `data/04_canonical/`.
4. **Single-flight concurrency.** `concurrency_limit` defaults to 1 and is
   applied via `asyncio.Semaphore(concurrency_limit)`, per the originating
   instruction's explicit mandate: whisper.cpp's Metal backend saturates the
   M5 Pro's Neural Engine, so concurrency above 1 causes memory contention
   and local crashes, unlike the I/O-bound FFmpeg workers `Extraction
   Orchestrator` runs at `concurrency_limit=2`.
5. **Per-lecture failure isolation and duplicate-id rejection.**
   `TranscriptionOrchestrator._process_lecture` and `_read_queue` reuse the
   exact isolation and duplicate-id-rejection logic from
   `ExtractionOrchestrator` (ADR 0002, decisions 2 and 4's rationale): one
   lecture's failure (missing handoff, whisper.cpp error) is recorded on
   `LectureTranscriptionOutcome.error` instead of aborting the batch, and two
   queue entries sharing one `lecture_id` are rejected at read time instead
   of racing on the same `TRANSCRIBED_OUTPUT_DIR/{id}_transcription.json`
   output file.
6. **`LectureTranscriptionResult.lecture_id` uses `SafeIdentifier`.** The
   originating instruction specified `lecture_id: str`, but this value is
   spliced into `TRANSCRIBED_OUTPUT_DIR / f"{lecture_id}_transcription.json"`
   exactly as `LectureExtractionResult.lecture_id` is spliced into
   `CANONICAL_OUTPUT_DIR / f"{lecture_id}.json"` (ADR 0002, decision 4). The
   same `SafeIdentifier` constraint (`domain/models.py`) was applied for
   consistency, closing the same path-traversal risk at the same domain
   choke point rather than reopening it for a structurally identical field.
7. **whisper.cpp binary path.** `WHISPER_BINARY =
   "engines/whisper.cpp/build/bin/whisper-cli"`
   (`transcription/whisper_worker.py`), not the originating instruction's
   literal `./whisper.cpp/main`. `scripts/01_provision_engines.sh` (shipped
   in Phase 1, predating this phase) builds whisper.cpp's modern CMake
   target at exactly that path; no `./whisper.cpp` directory or `main`
   binary exists anywhere this pipeline provisions. The
   `principal-code-reviewer` review board pass on this phase's first commit
   caught the mismatch as a BLOCK-level finding (every real invocation
   would fail with `WhisperTranscriptionError`); the operator confirmed the
   provisioned path is authoritative over the instruction's literal string.

This decision, plus two additional Repair Loop fixes the same review board
pass required, were applied after the phase's first commit and are recorded
here rather than treated as silent rework: (a) `run_transcription`'s
ephemeral `-oj` JSON sidecar is now deleted on every exit path (success,
non-zero whisper.cpp exit, and parse failure) via an outer `try/finally`,
where it previously leaked on the non-zero-exit path; (b) malformed,
incomplete, or wrong-typed whisper.cpp JSON (missing `offsets`/`text` keys,
invalid JSON syntax, or a present-but-wrong-typed `text` value such as
`null`) now raises `WhisperTranscriptionError` instead of a raw
`KeyError`/`json.JSONDecodeError`/`AttributeError`, restoring
`run_transcription`'s documented single-failure-type contract; and (c)
`TranscriptionSegment` gained a `model_validator` rejecting `end_time <
start_time`. This uses the same same-file `model_validator` technique as
`EvidencePack`'s existing temporal-window guard, but is deliberately looser:
`EvidencePack` rejects `end_time <= start_time` (no zero-duration window),
while `TranscriptionSegment` allows `end_time == start_time` because
whisper.cpp can legitimately emit a zero-length segment, which a
non-degenerate Learning Unit window cannot be. Either way, a whisper.cpp
offset glitch cannot silently persist an inverted-duration segment into the
Phase 4 handoff.

## Rationale

Each of decisions 1-3 surfaced a real conflict between the originating
instruction and either the live codebase (decision 1) or an internal gap in
the instruction itself (decisions 2 and 3) — not an implementation detail
safely inferable from context. Per the project's standing rule to ask
rather than assume on field names, paths, and unstated architectural
decisions, all three were confirmed with the operator before any production
code was written. Decisions 4-6 apply established codebase precedent
(ADR 0002) to the structurally analogous Phase 3 artifacts without
reopening decisions already made there.

## Consequences

- A future Phase 4 (`EvidencePack` assembly) that needs to renumber
  `data/05_evidence_packs/` to `data/06_evidence_packs/` (or fold Phase 3's
  output into it) is an open decision, not resolved by this ADR.
- `TranscriptionOrchestrator.run()` callers must handle partial batch
  success, identical to `ExtractionOrchestrator.run()`.
- Raising `concurrency_limit` above 1 is a supported constructor argument
  but is explicitly against the M5 Pro Metal/Neural-Engine contention
  rationale in decision 4; no code prevents a caller from doing so.

## Alternatives Considered

- **Read `{lecture_id}_handoff.json` as the instruction specified** —
  rejected: no such file is ever written by Phase 2; this would only work
  after also renaming Phase 2's output, a breaking change to ADR 0002
  outside this phase's scope.
- **Discover lecture ids by globbing `data/04_canonical/*.json`** —
  rejected in favor of `queue_path`: keeps one source of truth
  (`data/queue.json`) for which lectures are in scope for every phase,
  instead of each phase independently inferring scope from what upstream
  artifacts happen to exist on disk.
- **Renumber the pre-scaffolded `data/05_evidence_packs/` to make way for
  `data/05_transcribed/`** — rejected for this phase: touches directories
  outside Phase 3's scope for no functional gain until Phase 4 is planned.

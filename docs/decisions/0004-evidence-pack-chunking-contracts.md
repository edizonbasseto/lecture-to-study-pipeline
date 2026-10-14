# 0004. Evidence pack chunking contracts: output directory, EvidencePack schema, and segment allocation rules

## Status

Accepted.

## Context

Phase 4 (`build_evidence_packs`, `src/lecture_study/evidence/chunker.py`;
`EvidencePackOrchestrator`, `src/lecture_study/evidence/pipeline.py`) turns
each queued lecture's Phase 3 `LectureTranscriptionResult` into
Frame-Driven Chunking `EvidencePack`s. The originating instruction
(`.prompts/fase4_evidence_packs.md`) specified an output directory rename,
a new `EvidencePack` schema, and a segmentation algorithm with two
"decide deterministically" edge cases left open by design; each is
recorded here rather than left as an implicit implementation choice.

## Decisions

1. **Output directory.** Evidence packs are written to
   `data/06_evidence_packs/{lecture_id}/pack_{sequence:03d}.json`,
   resolving the open item left by ADR 0003 (decision 2 and its
   Consequences section): the pre-scaffolded, empty, never-referenced
   `data/05_evidence_packs/` scaffold directory is removed rather than
   populated, since Phase 3 already claimed `data/05_transcribed/` and
   nothing in the codebase ever wrote to the old path. `data/06_typst_src/`
   and `data/07_output_pdf/` are left untouched: renumbering them is
   outside this phase's scope and has no functional effect until those
   phases are built.
2. **`EvidencePack` schema restructure.** The originating instruction
   requires `lecture_id`, `pack_sequence: int`, `start_time`, `end_time`,
   `anchor_frame: CanonicalFrame | None`, and `segments:
   list[TranscriptionSegment]`, replacing the Phase 1 scaffold's
   `learning_unit_id`, `frames: list[CanonicalFrame]`, and
   `transcript_segment: str`. This is a breaking change to a domain model
   with no external consumers yet (Phase 5+ does not exist), so it was
   applied in place rather than versioned. `lecture_id` uses the
   `SafeIdentifier` constraint (not a bare `str`), matching
   `LectureExtractionResult.lecture_id` and
   `LectureTranscriptionResult.lecture_id` (ADR 0003, decision 6): this
   value is spliced into `EVIDENCE_PACKS_OUTPUT_DIR / lecture_id` exactly
   as those precedents are spliced into their own output paths, so the
   same path-traversal guard applies. `segments` defaults to an empty list
   (a slide with no attributed audio is valid, not an error).
3. **Window boundary algorithm.** Each `CanonicalFrame.timestamp_sec`
   (frames sorted ascending first, in case upstream order is not
   guaranteed) opens one window that runs until the next frame's
   timestamp; the last frame's window runs until
   `max(segment.end_time for segment in segments)`, per the originating
   instruction. A window is always created for audio before the first
   frame (`anchor_frame=None`, `start_time=0.0`,
   `end_time=frames[0].timestamp_sec`) whenever the first frame's
   timestamp is greater than zero, regardless of whether any segment
   actually falls there — this keeps the window tiling total and
   predictable (matching `EvidencePack`'s own "no dead zone at the
   boundary" invariant) instead of making tiling conditional on which
   segments happen to exist.
4. **Cross-boundary segment allocation: majority overlap, not splitting.**
   The originating instruction offered two deterministic options for a
   `TranscriptionSegment` crossing a window boundary: allocate it whole to
   the window holding the majority of its duration, or split it. Splitting
   was rejected: a `TranscriptionSegment.text` string has no principled
   way to be partitioned by a time fraction (whisper.cpp does not supply
   word-level timestamps here), so any split would fabricate a text
   boundary that was never actually transcribed. Majority overlap
   (`_best_window_index` in `chunker.py`) keeps every segment's text
   intact. Ties (equal overlap duration in two adjacent windows) resolve
   to the earlier window, via `max()`'s first-encountered-wins semantics
   over ascending window order — deterministic and requires no extra
   tie-breaking code.
5. **Zero-duration segment allocation.** whisper.cpp can legitimately emit
   a zero-duration segment (ADR 0003, decision 6's Repair Loop note on
   `TranscriptionSegment` allowing `end_time == start_time`). Such a
   segment has zero overlap with every window by construction, so the
   majority-overlap rule cannot place it; it is instead assigned by
   half-open containment of its instant (`window.start_time <= t <
   window.end_time`), consistent with `EvidencePack`'s own boundary
   convention. A point exactly at the final window's `end_time` (outside
   every half-open interval) falls back to the last window rather than
   being silently dropped.
6. **`UnboundedLectureEndTimeError`.** Raised when a lecture has at least
   one `CanonicalFrame` but zero `TranscriptionSegment`s, so the last
   window's `end_time` (decision 3) cannot be derived. This replaces what
   would otherwise be a raw `ValueError: max() arg is an empty sequence`,
   matching the codebase's established pattern of a named exception per
   documented failure mode (`CanonicalHandoffNotFoundError`,
   `WhisperTranscriptionError`, `TranscriptionHandoffNotFoundError`)
   instead of letting an unrelated builtin error leak through.
7. **Frame-less lecture.** A `LectureTranscriptionResult` with no frames at
   all (audio exists but Phase 2 found no slide changes) produces a single
   window (`anchor_frame=None`, `start_time=0.0`,
   `end_time=max(segment.end_time...)`) holding every segment, generalizing
   decision 3's pre-first-frame window to the whole lecture. A lecture with
   neither frames nor segments produces an empty pack list.
8. **Orchestrator mirrors `TranscriptionOrchestrator`.**
   `EvidencePackOrchestrator(queue_path: Path, concurrency_limit: int = 4)`
   reuses `ExtractionOrchestrator`/`TranscriptionOrchestrator`'s exact
   shape (ADR 0002, ADR 0003): `queue_path`-driven lecture discovery,
   duplicate-id rejection at read time, and per-lecture failure isolation
   via `LectureEvidenceOutcome.error`. `concurrency_limit` defaults to 4
   (not Phase 3's 1) per the originating instruction's rationale: chunking
   is pure CPU/RAM work with no NPU/Metal/subprocess contention, unlike
   whisper.cpp. Unlike the siblings' single-file-per-lecture atomic
   `tmp`-then-`replace` write, this orchestrator writes one file per pack
   under a `{lecture_id}/` directory; see Repair Loop item (c) below for
   the directory-level equivalent of that same atomicity guarantee.

This decision, plus four Repair Loop fixes the same review board pass
required, were applied after the phase's first commit and are recorded
here rather than treated as silent rework: (a) `TranscriptionSegment`
gained a `model_validator` rejecting `start_time < 0` — `staff-architect`
and `principal-code-reviewer` independently flagged that a negative-time
segment has zero overlap with every window under the majority-overlap
rule (decision 4) and was silently defaulting to window 0 instead of
failing loud; closing it at this domain choke point, in the same style as
`SafeIdentifier` (ADR 0003, decision 6), is provably sufficient — windows
tile `[0, max(segment.end_time))` contiguously, so once every segment's
`start_time` is non-negative, its interval is a subset of that tiled
range and is guaranteed positive overlap with at least one window, making
a chunker-level "zero overlap everywhere" fallback branch unreachable
(and therefore not added, to avoid dead code under the 100%-coverage
gate). (b) `_build_windows` gained a `DegenerateWindowError` raised
whenever a computed window's `end_time <= start_time` — two frames
sharing a timestamp, or a frame occurring at or after the lecture's
derived end time, previously surfaced as an opaque `pydantic
ValidationError` from `EvidencePack`'s own guard instead of a named,
diagnosable failure mode, breaking the pattern decision 6 established for
the sibling edge case. (c) `EvidencePackOrchestrator._save_packs` now
builds each lecture's pack set in a `{lecture_id}.staging/` directory
instead of writing `pack_NNN.json` files in place: `principal-code-reviewer`
and `staff-architect` both found that a re-run producing fewer packs than
a previous run left higher-numbered `pack_NNN.json` files behind as
undetectable stale data, and a crash mid-write could leave a
partially-written pack set with no incompleteness marker. The first
attempt at this fix cut over with `shutil.rmtree(lecture_dir)` followed by
`staging_dir.replace(lecture_dir)`; a same-round re-review by
`principal-code-reviewer` BLOCKed that attempt (verifying, by reproducing
`Path.replace`'s `OSError` on a non-empty target, that the `rmtree` step
is unavoidable with a plain replace) because a crash between the `rmtree`
and the `replace` deletes `lecture_dir` with nothing yet in its place —
total, silent loss of that lecture's evidence packs, worse than the stale
files it replaced. The corrected mechanism instead renames the existing
`lecture_dir` to `{lecture_id}.previous/` (a single atomic `rename`, not a
delete) immediately before `staging_dir.replace(lecture_dir)` (a second
atomic `rename`), and only removes `{lecture_id}.previous/` after the
swap succeeds. `lecture_dir` is therefore always either the untouched
previous good state or the fully-built new state; a crash between the two
renames leaves both the old and new pack sets recoverable on disk under
`{lecture_id}.previous/` and `{lecture_id}.staging/` respectively, and the
next run's leftover-cleanup step (already required for finding (c)'s
original staging-directory case) clears whichever of the two is now
orphaned before rebuilding from scratch. (d)
`EvidencePackOrchestrator._process_lecture` now runs `_build_packs` via
`asyncio.to_thread` instead of calling it inline: `principal-code-reviewer`
found that with no other `await` point on the per-lecture path, every
task ran to completion before the next one started regardless of
`concurrency_limit`, making the Semaphore(4) default (decision 8) and its
stated rationale inert. `asyncio.to_thread` gives the synchronous
CPU/file-I/O work a real event-loop yield point, so `concurrency_limit`
now bounds genuine concurrent execution, verified by a test asserting
`max_in_flight > 1` under a tracked, artificially-delayed build call.

## Rationale

Decisions 4-6 each resolve a genuine open edge case the originating
instruction flagged as "decide deterministically" rather than specified
outright; each choice is recorded with the alternative it displaced so a
future phase does not silently re-litigate it. Decisions 1, 2, 3, 7, and 8
apply established codebase precedent (ADR 0002, ADR 0003) to the
structurally analogous Phase 4 artifacts.

## Consequences

- `EvidencePack.frames`, `.transcript_segment`, and `.learning_unit_id` no
  longer exist; any future phase consuming `EvidencePack` must read
  `.anchor_frame` and `.segments` instead.
- A lecture whose Phase 2 canonical handoff produced zero slide frames and
  whose Phase 3 transcription produced zero segments yields zero evidence
  packs — this is a valid, not an error, outcome.
- `data/05_evidence_packs/` no longer exists on disk or in any code path;
  a future phase must not resurrect it.

## Alternatives Considered

- **Split a crossing `TranscriptionSegment` at the boundary** — rejected:
  no word-level timestamp data exists to split `text` correctly; a
  character- or duration-proportional split would fabricate transcript
  content that whisper.cpp never produced.
- **Only create the pre-first-frame window when a segment actually falls
  before the first frame** — rejected in favor of always creating it
  (when `frames[0].timestamp_sec > 0`): conditional tiling would make
  window count and `pack_sequence` numbering depend on incidental data
  rather than purely on frame timestamps, complicating both the mental
  model and the test matrix for no real benefit.
- **Renumber `data/06_typst_src/` and `data/07_output_pdf/` to keep a
  gapless sequence** — rejected: outside this phase's scope, no code
  references those paths yet, and doing so now would be speculative
  rework for phases not yet planned.

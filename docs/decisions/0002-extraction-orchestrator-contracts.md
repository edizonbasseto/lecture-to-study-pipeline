# 0002. Extraction orchestrator contracts: raw video resolution, failure isolation, and the Phase 3 handoff shape

## Status

Accepted.

## Context

Phase 2 (`ExtractionOrchestrator`, `src/lecture_study/extraction/pipeline.py`)
reads `queue.json` and, for each queued lecture, drives audio extraction,
slide frame extraction, and OCR. Three points were not decidable from
existing code alone, because no download step exists yet
(`LectureDiscovery.discover_lectures` returns only remote metadata;
`data/01_raw/` is empty in the repository) and no prior handoff artifact for
Phase 3 (Whisper transcription) had been defined. A fourth decision was
added after the phase-gate review board flagged that a queue-supplied
identifier reaching filesystem paths unconstrained is a path-traversal risk
once a real (non-mocked) discovery/ingestion source populates it.

## Decisions

1. **Raw video resolution.** The orchestrator locates a queued lecture's
   source video by globbing `data/01_raw/{lecture.id}.*`. Exactly one match
   is required; zero or multiple matches raise `RawVideoNotFoundError`. This
   keeps the orchestrator decoupled from whichever container format a future
   `yt-dlp` download step produces, without introducing a new field on
   `LectureMetadata` before that step exists. Audio and slide output for a
   lecture are written under a `{lecture.id}` subdirectory of
   `AUDIO_OUTPUT_DIR`/`SLIDES_OUTPUT_DIR` rather than directly into them:
   FFmpeg names slide frames sequentially from `frame_0001.png` regardless
   of source video, so two lectures sharing one output directory under
   `concurrency_limit > 1` would otherwise collide.
2. **Per-lecture failure isolation.** `ExtractionOrchestrator._process_lecture`
   catches any exception raised while extracting one lecture (missing raw
   video, FFmpeg failure, OCR failure) and records it on that lecture's
   `LectureExtractionOutcome.error` instead of propagating it. The rest of
   the queue still runs. A batch of N lectures returns N outcomes; the
   caller inspects `outcome.succeeded` per lecture rather than the run
   failing atomically.
3. **Phase 3 handoff shape.** `LectureExtractionResult` (`domain/models.py`)
   is a new strict Pydantic model — `lecture_id`, `audio_path`, and
   `frames: list[CanonicalFrame]` — persisted as
   `data/04_canonical/{lecture_id}.json` via the same tmp-file-then-`replace`
   atomic write already used by `save_queue`. It intentionally holds no
   `transcript_segment`: that field belongs to `EvidencePack`, produced only
   after Phase 3 transcribes the audio.
4. **Lecture identifier constraint.** `LectureMetadata.id` and
   `LectureExtractionResult.lecture_id` both use the `SafeIdentifier` type
   (`domain/models.py`): a strict Pydantic `str` constrained to
   `^[A-Za-z0-9_-]+$`, non-empty. This is the single choke point where a
   lecture identifier enters the domain, before it is ever spliced into
   `RAW_VIDEO_DIR.glob(f"{lecture_id}.*")` or
   `CANONICAL_OUTPUT_DIR / f"{lecture_id}.json"`; an id containing `/` or
   `..` segments fails Pydantic validation at `_read_queue()` and never
   reaches either filesystem call.

## Rationale

The orchestrator's job is to turn one row of `queue.json` into extracted
media without either (a) coupling itself to an ingestion mechanism that
does not exist yet, or (b) trusting that row's contents beyond what the
domain model already guarantees. Decisions 1 and 3 keep the orchestrator
decoupled from the not-yet-built download step; decision 2 matches the
"protect the pipeline" domain-isolation intent in
`DIRETRIZES_ARQUITETURA.txt` section 6, where one domain's failure must not
cascade into another's; decision 4 closes the gap between "a lecture id is
presently CLI/mock-produced" and "a lecture id will eventually come from
scraped, external metadata" — the constraint is cheap now and is the
correct point to enforce it, rather than validating ad hoc at every call
site that builds a path from it.

## Consequences

- Adding the real `yt-dlp` download step later only needs to place files at
  `data/01_raw/{lecture.id}.<ext>`; no orchestrator change is required as
  long as exactly one file per lecture id is written.
- Callers of `ExtractionOrchestrator.run()` must handle partial batch
  success; a non-empty outcome list does not imply every lecture succeeded.
- OCR (`ocr_worker.extract_text_from_image`) runs via `asyncio.to_thread`
  per frame inside `_build_canonical_frames`, since Vision OCR is
  compute-bound, unlike the I/O-bound FFmpeg subprocess calls in
  `media_workers`; blocking the event loop there would have serialized OCR
  across concurrently-processing lectures, defeating the semaphore-bounded
  concurrency this orchestrator exists to provide. A second semaphore
  (`ocr_concurrency_limit`, default 4) is built once per
  `ExtractionOrchestrator` instance and shared across every lecture that
  instance processes, separately bounding total in-flight OCR requests
  across the whole run, since `concurrency_limit` alone only bounds
  concurrent lectures, not the frame fan-out within and across them.
- A future lecture id sourced from real scraped metadata that does not fit
  `SafeIdentifier`'s slug alphabet is rejected at queue-read time with a
  `ValidationError`, not silently accepted and later mishandled by a
  filesystem call.

## Alternatives Considered

- **Explicit `raw_video_path` field on the queue item** — rejected for
  Phase 2: it would require a download step to populate it, which does not
  exist yet, and would only add branching between "populated" and
  "not yet populated" queue entries.
- **Abort the batch on first lecture failure** — rejected: a single corrupt
  source video or missing FFmpeg binary would block every other queued
  lecture, which does not match the "protect the pipeline" isolation intent
  in `DIRETRIZES_ARQUITETURA.txt` section 6.
- **Sanitize/reject unsafe lecture ids at each filesystem call site in
  `pipeline.py`** — rejected in favor of the `SafeIdentifier` domain
  constraint: validating at every call site duplicates the check and risks
  a future call site forgetting it, whereas the Pydantic boundary makes an
  unsafe id impossible to construct in the first place.

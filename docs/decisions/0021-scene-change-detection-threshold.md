# 0021. Scene-change detection with a temporal safety interval

## Status

Accepted; amended on 2026-07-28 after the three-lecture pilot.

## Context

`extraction/media_workers.py`'s visual-frame filter drives FFmpeg's
scene-change detection for Phase 2's slide-frame extraction:
`select='gt(scene,N)',showinfo`, where FFmpeg's `scene` score is a
normalized 0.0-1.0 measure of frame-to-frame visual difference. The value
had been `0.03` since this filter was first written (`.prompts/
fase2_workers.md`), with no recorded rationale for that specific number.

Running Phase 2 against a real 2-hour lecture recording for the first time
(2026-07-22) produced 957 extracted frames -- for context, README's own
Frame-Driven Chunking description and typical lecture pacing put a
realistic range at roughly 15-25 real slide transitions for a 2-hour
recording. Inspecting the extracted frames' OCR text showed why: this
specific recording carries a live, on-screen clock/timestamp overlay in
one corner (visible in the OCR output as e.g. `21:03:51` → `21:03:52`
ticking second-by-second), and at `0.03` even that tiny single-region
pixel change was enough to register as a "scene change" -- one new frame
roughly every 7.5 seconds on average, essentially tracking the clock
rather than the slide content.

At 958 frames (via 06_evidence_packs' near-1:1 mapping at the time), every
downstream Phase 5-7 LLM step -- already individually slow due to
per-call model reloads (`concurrency_limit=1` by design) -- would have had
to run against roughly 40-60x more units than a real lecture needs,
turning a realistic multi-hour run into a multi-day one for no content
benefit (the extra "frames" carry no new slide information, only a
ticking clock).

## Decision

The original decision raised the scene threshold from `gt(scene,0.03)` to
`gt(scene,0.3)`.

Re-running Phase 2 against the same real video with `0.3` produced 92
frames -- inspected and consistent with real slide transitions, not clock
ticks. This is an empirical, single-video validation, not a swept/tuned
optimum; see Consequences for the explicit risk this accepts.

The 2026-07-28 three-lecture pilot supplied the missing second recording
setup. Lecture `10010`, a continuous-motion surgical recording, produced
zero frames at the `0.3` threshold. The filter is therefore amended to
retain that threshold while also selecting the first frame and a temporal
safety frame whenever 150 seconds have elapsed since the previous
selection:

```text
select='isnan(prev_selected_t)+gt(scene,0.3)+gte(t-prev_selected_t,150)',showinfo
```

The 150-second interval is half the canonical minimum Learning Unit
duration of 300 seconds. It prevents a qualifying unit from receiving no
visual candidate solely because the source contains gradual or continuous
motion. It is not a visual-recall guarantee and does not replace the
authenticated full-frame visual-observation boundary required by ADR
0023.

The amended filter produced seven frames for the 1,034-second surgical
lecture at timestamps 0, 150, 300, 450, 600, 750, and 900 seconds. All
seven passed through canonical OCR and each of the two reconstructed
Evidence Packs received an in-range anchor frame. The extracted OCR
contained only the recording overlay (`ID: 001` and a magnification
marker); therefore this result establishes deterministic visual coverage,
not semantic interpretation of the surgical procedure.

## Consequences

- **Positive:** Phase 2 output for this lecture went from 957 (mostly
  clock-tick noise) to 92 (real slide transitions), and the downstream
  evidence-pack count followed proportionally (958 → 93), making Phase
  5-7's per-pack, per-call-model-reload cost tractable for a real lecture
  instead of pathological.
- **Positive:** the temporal interval closes the observed zero-frame
  failure without lowering the scene threshold or reintroducing
  clock-overlay noise.
- **Negative, accepted risk:** a 150-second cadence can still omit a
  short-lived visual event. OCR-only downstream prompts can also preserve
  a frame while failing to interpret its instructional pixels. The
  pipeline must continue to fail closed for publication until the
  production full-frame visual observer and independent review gates are
  wired.
- **Neutral:** the constant remains a single, centralized module-level
  value (`extraction/media_workers.py`), not scattered -- one edit point
  if another recording setup surfaces a different failure mode.

## Rationale

Raising the single global threshold was originally chosen over a more surgical fix
(e.g. cropping out the clock-overlay region before scene-diffing,
`crop=W:H:X:Y,select='gt(scene,0.03)'`) because the crop region is itself
recording-specific (this lecture's overlay position, size, and even
presence are not guaranteed for any other recording) -- hard-coding a crop
region would be *more* narrowly tuned to this one video, not less, and
would need its own per-recording configuration to generalize, which is a
strictly bigger scope than this fix intends to cover. A single, somewhat
more conservative global threshold degrades gracefully (fewer, chunkier
Evidence Packs) rather than requiring per-recording calibration up front.

The temporal amendment was chosen instead of lowering the threshold after
the second setup because lowering it would regress the empirically verified
clock-overlay behavior. It also avoids a lecture-specific configuration:
the interval is derived from the existing Learning Unit contract rather
than tuned to either recording.

## Alternatives Considered

1. **Crop the clock-overlay region out before scene-diffing.** Rejected
   for this pass: more precise for *this* video, but trades one
   single-video-tuned magic number (a threshold) for another
   (a crop rectangle), with no more generality, and adds a second FFmpeg
   filter-graph stage to reason about. Worth revisiting if a future
   recording turns out to have a similar fixed-position overlay problem
   that a higher threshold alone doesn't solve.
2. **Make the threshold a per-lecture or per-course configuration value**
   (CLI flag, config file, or `targets.local.yaml` field) instead of a
   single hardcoded constant. Originally rejected under YAGNI when only
   one real video had been observed. The second setup demonstrated a
   general coverage failure that can be corrected from the existing
   Learning Unit duration contract, so it still does not justify a
   lecture-specific configuration surface.
3. **Leave `0.03` and instead de-duplicate near-identical frames
   downstream** (e.g. by OCR-text similarity, ignoring a small changing
   region like a clock). Rejected: more complex, moves the fix later in
   the pipeline (after the wasteful over-extraction and OCR cost has
   already been paid for all 957 frames), and doesn't address the root
   visual-diff sensitivity issue at all.

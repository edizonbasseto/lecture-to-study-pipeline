# 0025. Denser slide-frame extraction for lecture 10002

## Status

Accepted. This record amends ADR 0021's temporal safety interval; it does
not replace ADR 0021, whose scene-change threshold, first-frame selection,
and overall filter structure remain in force.

## Context

`extraction/media_workers.py`'s visual-frame filter (ADR 0021) selects the
first frame of a recording, any frame at which FFmpeg's scene score exceeds
`0.3`, and a temporal safety frame whenever `_MAXIMUM_VISUAL_FRAME_GAP_SECONDS`
have elapsed since the previous selection. ADR 0021 fixed that constant at
150 seconds and recorded, in its own Consequences section, the following
accepted risk:

> **Negative, accepted risk:** a 150-second cadence can still omit a
> short-lived visual event. OCR-only downstream prompts can also preserve
> a frame while failing to interpret its instructional pixels.

Lecture `10002` is exactly the case that risk describes. At the 150-second
interval it produced ten frames, at timestamps `0`, `150`, `300`, ..., `1350`
seconds, for a continuous-motion surgical recording covering margin marking,
incision, and dissection [VERIFIED:
`ls workspace/data/03_extracted_slides/10002/` before this decision's
re-extraction]. The recording's transcript spans roughly 1,345 seconds across
three Evidence Packs, so each of those distinct surgical states occurs well
inside a single 150-second window; ten frames spaced that far apart cannot
represent them.

D-23 leaves the visual sampling density explicitly open for this milestone.
D-14 removes storage and compute as objections to a denser extraction, so the
only real cost of sampling more densely is downstream captioning time, not
disk space or wall-clock budget.

## Decision

`_MAXIMUM_VISUAL_FRAME_GAP_SECONDS` is lowered from 150 seconds to 37.5
seconds -- a quarter of the ADR 0021 value -- for every lecture, starting
with a re-extraction of lecture `10002` under this milestone.

The scene-change threshold ADR 0021 fixed at `gt(scene,0.3)` does **not**
change. It is carried forward unmodified, at the same value, for the same
reason ADR 0021 recorded it: that threshold suppresses the clock-overlay
noise ADR 0021's own pilot observed, and relaxing it would reintroduce
exactly that noise. This decision touches only the temporal safety interval.

### Enforced consequences

- **Only the temporal interval moves; the scene threshold is unchanged.** No
  code change made under this decision may alter `gt(scene,0.3)`. Touching
  the threshold requires its own decision record amending ADR 0021 a second
  time, not a side effect of this one.
- **The frame filename convention, the sequential frame glob
  (`frame_[0-9][0-9][0-9][0-9].png`), and the showinfo log-parsing contract
  in `extract_frames` are unchanged.** Nothing downstream that depends on
  those contracts -- the timestamp-parsing rename step, the captioning
  worker introduced in plan `01-07`, or any other consumer -- observes a
  different frame identity scheme because of this decision.
- **A test binds `_MAXIMUM_VISUAL_FRAME_GAP_SECONDS` to the value this record
  states**, so the constant and this document cannot silently drift apart.
- **Lectures extracted before this record carry the older, 150-second
  density until they are explicitly re-extracted.** This decision does not
  retroactively re-run Phase 2 for any lecture other than `10002`. The 24
  previously delivered lectures and the as-yet-unprocessed scheduled lectures
  are re-extracted, if at all, under their own later phases or plans, not as
  a side effect of this record.

## Consequences

- **Positive:** lecture `10002`'s frame count roughly quadruples relative to
  the 150-second interval, which is far more likely to place a candidate
  frame inside each of the recording's distinct surgical states than the
  previous ten-frame set.
- **Positive:** the noise-suppression behavior ADR 0021 tuned into the scene
  threshold is fully preserved, because this decision does not touch it.
- **Negative, accepted:** a denser interval produces more near-duplicate
  frames to caption and to read for a recording with long static-camera
  segments. D-14 makes this an accepted cost, not a blocker, since compute
  and captioning time are explicitly not constraints for this operator.
- **Negative, accepted:** the same risk ADR 0021 accepted at 150 seconds
  still exists at 37.5 seconds in reduced form -- a short-lived visual event
  narrower than 37.5 seconds can still be omitted. This decision does not
  claim visual-recall coverage; it only narrows the previously accepted gap.
  Whether 37.5 seconds is adequate for lecture `10002` specifically is
  checked empirically against the frames the re-extraction actually produces,
  not assumed from the smaller number alone, per the research's assumption
  A6; if that inspection finds the density still visibly inadequate, that
  finding is reported rather than accepted silently, and this record permits
  a further amendment.
- **Neutral:** the constant remains the single, centralized module-level
  value it was under ADR 0021, still with one edit point.

## Rationale

Changing only the temporal interval, and leaving the scene threshold exactly
as ADR 0021 fixed it, keeps this decision attributable to a single variable:
if the resulting frame set is still inadequate for lecture `10002`, that
result is attributable to the interval alone, not to an entangled pair of
changes to interval and threshold together. The scene threshold exists
specifically to suppress the clock-overlay noise ADR 0021's own pilot
recording exhibited; relaxing it here would reintroduce that exact risk for
no benefit tied to the actual problem this decision addresses, which is
under-sampling caused by a continuous-motion recording, not by the threshold.

Quartering the interval, rather than halving it, was chosen over a smaller
change because it is far more likely to place a frame inside each of lecture
`10002`'s margin-marking, incision, and dissection states individually,
rather than only doubling the odds. Since D-14 removes storage and compute
as objections, the marginal cost of the denser interval is bounded to
captioning time, which is an acceptable trade for meaningfully better
coverage of a recording this milestone specifically singled out as
under-sampled.

## Alternatives Considered

1. **Halve the interval to 75 seconds, threshold unchanged.** Rejected as
   the primary choice: it is the smaller change and would leave the
   noise-suppression behavior untouched, but it was judged less likely to
   place a frame inside each of lecture `10002`'s distinct surgical states
   given that the recording's transitions occur well inside even a
   150-second window; a smaller reduction risked leaving the same
   under-sampling problem only partially addressed. Storage and compute are
   explicitly not constraints per D-14, so the larger reduction's only real
   cost -- more frames to caption -- was accepted instead.
2. **Quarter the interval and relax the scene-change threshold together.**
   Rejected: entangling two parameters in one change means a still-inadequate
   result cannot be attributed to either one individually, and the threshold
   exists specifically to suppress the clock-overlay noise ADR 0021's pilot
   observed; relaxing it reintroduces that risk for a benefit (catching
   gradual transitions) that the temporal interval, taken alone, was not
   shown to require solving in combination with the threshold.
3. **Leave the interval at 150 seconds and instead caption fewer, larger
   Evidence Packs' worth of frames.** Rejected: this does not address the
   root problem, which is that ADR 0021's own pilot already accepted that a
   150-second cadence can omit a short-lived visual event for exactly this
   kind of continuous-motion recording; captioning more thoroughly does not
   help if the frame set itself never captured the surgical states that
   needed representing.

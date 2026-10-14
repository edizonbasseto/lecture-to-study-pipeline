# 0031. Standardized figure crop rectangle, applied by default

## Status

Accepted; **amended by ADR 0044: the default is now no crop.** The rectangle
below was measured on one program's `2560x1080` recordings with a webcam
pillarbox. It is a property of that recording layout, not of lecture
recordings in general, and silently applying it to a different layout
mis-crops figures without any error (the risk this record's Consequences
already named). `--figure-crop-rect` now defaults to no crop; an explicit
`WIDTH:HEIGHT:X:Y` is still accepted for recordings whose layout is known. The
sections below are the historical rationale for the original default.

Originally accepted. Supersedes this record's own initial draft decision (opt-in,
single-lecture only) after operator confirmation the same day -- see
Context's final paragraph.

## Context

A direct review of lecture 10003's rendered PDFs (the operator opening the
actual apostila, not the code) found every embedded figure was a raw,
uncropped screen-recording frame: a black pillarbox bar on the left, the
instructor's webcam thumbnail baked into the top-right corner, a recording
timestamp overlay, and the presentation software's own black tile background
around them. This is the single largest driver of the "feio" (ugly)
complaint that started the review, since it appears on nearly every content
page.

Inspecting lecture 10003's own source frames (`workspace/data/03_extracted_
slides/10003/`, a `2560x1080` canvas) directly -- not guessed at from a
rendered PDF -- found the recording layout is a fixed composite: real slide
content occupies a stable rectangle in the middle, with the pillarbox,
webcam, and timestamp chrome confined outside it. Sampling frames across the
lecture's full timeline (start, middle, end) confirmed the same rectangle,
`crop=1940:1080:300:0`, isolates the content cleanly at every point checked.

The first version of this decision treated the rectangle as lecture-10003-
specific and opt-in only (`--figure-crop-rect`, no default, rejected outright
for a multi-lecture invocation), reasoning that nothing yet established
whether other recording sessions shared the same canvas or chrome layout.
The operator was asked directly and confirmed: every lecture this milestone
processes is recorded through the same standardized setup (same tool, same
webcam placement, same canvas). A per-lecture opt-in flag would only add
friction to the batch of 33 lectures still queued for processing, not
protect against a real risk.

## Decision

`DEFAULT_FIGURE_CROP_RECT = FigureCropRect(width=1940, height=1080, x=300,
y=0)`, the rectangle measured against lecture 10003 above, is the CLI's
default for `--figure-crop-rect` in `scripts/generate_personal_study_guides.
py` -- applied automatically to every lecture, including a multi-`lecture_id`
batch invocation, with no flag required. Passing a different `WIDTH:HEIGHT:
X:Y` overrides it for a lecture whose recording is confirmed to differ;
passing `none` embeds that lecture's frames uncropped.

## Alternatives considered

1. **Opt-in, single-lecture-only flag with no default** (this record's own
   first draft). Superseded by the operator's direct confirmation above:
   the risk it guarded against -- silently applying 10003's geometry to a
   differently-laid-out recording -- does not apply here, since the layout
   is genuinely standardized across the whole set.
2. **`ffmpeg cropdetect` for automatic per-lecture detection.** Considered
   and rejected for this specific chrome shape: `cropdetect` finds black
   borders, and the webcam thumbnail is not black, so it would not be
   excluded by that filter's own detection logic. Moot now that one
   rectangle is confirmed to cover every lecture; would only be worth
   revisiting if a future recording session turns out to differ.
3. **Crop at raw extraction time (`extraction/media_workers.py`'s ffmpeg
   filter) instead of at figure-staging time.** Rejected: it would also
   change the frames OCR and Qwen3-VL captioning see, requiring the
   already-run ~45-minute captioning pass to be redone for no captioning
   quality benefit (captions were already accurate against the uncropped
   frames). Cropping only the frames actually staged for inline embedding
   is far cheaper and does not disturb existing caption data.

## Consequences

- **Positive:** every lecture's apostila, including the 33 still queued
  for processing, gets clean figures by default with no per-invocation flag or
  per-lecture calibration step.
- **Negative, accepted:** if a future recording session turns out to use a
  different layout (a different tool, a moved webcam), its figures will be
  silently mis-cropped rather than erroring, since `ffmpeg`'s crop filter
  only fails when the rectangle does not fit inside the source frame at
  all. Nothing currently detects a layout that differs but still contains
  the rectangle. Revisit if a future lecture's rendered figures look wrong
  the same way 10003's originally did.
- **Neutral:** `none` still embeds a lecture's frames uncropped, and an
  explicit `WIDTH:HEIGHT:X:Y` still overrides the default outright, so a
  confirmed exception does not require touching this default.

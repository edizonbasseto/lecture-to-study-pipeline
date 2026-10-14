# 0001. Use pyobjc-framework-Quartz, not pyobjc-framework-CoreImage, to feed the Apple Vision OCR pipeline

## Status

Accepted. **Amended by ADR 0045:** the local-only mandate no longer binds the LLM phases; lecture content verified to carry no patient-identifying data may be sent to the remote model route.

## Context

`DIRETRIZES_ARQUITETURA.txt` mandates OCR via the Apple Vision Framework
(`VNRecognizeTextRequest`) and explicitly prohibits Tesseract. Vision's entry
point, `VNImageRequestHandler`, needs a decoded still image handle: a
`CGImage`, a `CIImage`, a `CVPixelBuffer`, or raw `Data`. The pipeline's own
input is a static slide frame written to disk by the scene-change extraction
step (`data/03_extracted_slides/`) — there is no live camera feed, no
compositing, and no filter chain involved.

Two pyobjc bindings can produce an image handle for Vision from that PNG on
disk: `pyobjc-framework-CoreImage` (`CIImage`/`CIContext`) and
`pyobjc-framework-Quartz` (`CoreGraphics`/`ImageIO`, i.e. `CGImage`).

## Decision

Bridge slide frames into Vision through `pyobjc-framework-Quartz`: decode the
PNG with `CGImageSourceCreateWithURL` + `CGImageSourceCreateImageAtIndex` into
a `CGImageRef`, and hand that directly to
`VNImageRequestHandler(cgImage=...)`. `pyobjc-framework-CoreImage` is not a
dependency of this codebase.

## Rationale

1. **Direct match for a static image.** `CGImage` is one of the handle types
   `VNImageRequestHandler` accepts natively — decode once, hand it to Vision,
   no intermediate compositing.
2. **CoreImage solves a different problem.** `CIImage`/`CIFilter`/`CIContext`
   exist for GPU-backed compositing and real-time filter graphs (color
   adjustment, blending, de-noising chains). None of that is exercised by
   "load one PNG, run one OCR pass" — pulling in CoreImage would add an
   unused filter/render pipeline for no functional gain.
3. **GPU contention on Apple Silicon.** Rendering a `CIImage` efficiently
   requires a `CIContext` backed by its own Metal device/command queue. This
   pipeline already runs `whisper.cpp` and `llama.cpp` compiled with
   `GGML_METAL=ON` (`scripts/01_provision_engines.sh`), each holding its own
   Metal context, and `DIRETRIZES_ARQUITETURA.txt` commits the whole
   pipeline to native, concurrent execution across the M5 Pro's cores. A
   second, unnecessary Metal context on the OCR path is avoidable GPU
   contention risk.
4. **Smaller, more direct binding surface.** `pyobjc-framework-Quartz`
   already bundles `CoreGraphics` and `ImageIO`, which is exactly the decode
   (`CGImageSource`) + handle (`CGImage`) pair Vision consumes. In pyobjc,
   this API surface is plain functions and structs; the CoreImage bindings
   are comparatively heavier, with block-based APIs and KVC-style filter
   parameter dictionaries that add incidental complexity here.

## Consequences

- `pyproject.toml` depends on `pyobjc-framework-quartz` and
  `pyobjc-framework-vision`; it does not depend on `pyobjc-framework-CoreImage`.
- The (future) OCR module reads a slide PNG via `CGImageSource`, obtains a
  `CGImageRef`, and passes it straight into `VNImageRequestHandler`. No
  `CIImage`/`CIContext` object should appear on that path.
- If a future requirement needs GPU compositing before OCR (e.g. de-skewing
  or denoising a slide), `pyobjc-framework-CoreImage` can be reconsidered as
  an *additive* dependency for that specific step — this decision does not
  block that, it only rejects CoreImage as the baseline image-acquisition
  path for Vision.

## Alternatives Considered

- **`pyobjc-framework-CoreImage`** — rejected per points 2–4 above.
- **`AppKit`/`NSImage` bridging** — rejected: heavier bridging surface than
  Quartz, and no primitive as direct as `CGImageSource` for extracting a
  `CGImageRef` from a file on disk.

# 0040. A dedicated full-page video viewer, coexisting with the embedded player

## Status

Accepted.

## Context

Each lecture page already embeds its video inline (`_render_video_section` in
`site/renderer.py`), among the material cards, streamed through the existing
`/videos/{lecture}/{video}` route (ADR 0029) with Range support native to
Starlette's static-file handling. That works for a quick check, but the
embedded player competes for width and attention with the study cards below
it, and there was no page whose only job is to play the recording full width.
The reading-page precedent (ADR 0039's era of work; see `render_reading_page`
in `site/renderer.py`) had already established the idiom for a material's own
dedicated page: a `reader-bar` header with a back link, the content, and the
site's shared serif-typography `_document()` wrapper.

## Decision

1. **A new page, `assistir.html`, is generated per lecture** next to its
   `index.html`, at the same build-time step (`_write_lecture` in
   `site/generator.py`) that already writes the reading pages and the lecture
   page itself. It follows the same static-generation pattern as every other
   page in this module: written once at `generate_site` time, never rendered
   per request.

2. **The embedded player stays.** `_render_video_section` is unchanged in
   effect -- it still renders the inline `<video>` on the lecture page -- and
   now also renders a small "Assistir em tela cheia" link to the new page.
   Both routes stay live; the dedicated page is an addition, not a
   replacement, so a link already shared to the lecture page keeps working
   exactly as before.

3. **The new page reuses the existing `/videos/{lecture}/{video}` streaming
   route.** No new streaming endpoint, no new FastAPI route registration: the
   page is a plain static file served through the same `StaticFiles` mount at
   `/` that already serves `index.html`, so it inherits the same ASGI
   authentication middleware, the same `Cache-Control: private, no-store`
   header (ADR 0039) and the same `Vary: Cookie`, with no per-route
   exemption to introduce or audit.

4. **No captions, VTT, or transcript track ships with this page.** The two
   authorized viewers already have the lecture's own text materials
   (apostila, resumo) alongside the video; a caption track would need a
   `.vtt` exporter this delivery does not build. Documented here as a
   follow-up candidate, not undertaken now.

5. **Visual convention matches the reading page, not a new idiom.** The new
   page's header (`_watch_bar`) is the same `reader-bar` header pattern the
   reading page uses (back link, title, shared `_document()` serif chrome),
   minus the PDF-download action a video page has no equivalent for. No
   prev/next navigation between lectures was added: the lecture page already
   carries that navigation, and a video-only page one link away from it does
   not need to duplicate it.

## Alternatives considered

1. **Replace the embedded player with a link to the dedicated page.**
   Rejected: the operator's decision locks the embedded section in place: a
   viewer scanning the lecture page should still see and be able to play the
   video without leaving it.
2. **A signed or time-limited URL for the video route.** Not addressed here;
   out of scope for this change, which reuses the streaming route exactly as
   ADR 0029 already established it.
3. **Client-side captions generated from the existing ASR transcript.**
   Rejected for this delivery: the transcript pipeline's output is not yet in
   a caption-track format (`.vtt`), and building that exporter is a separate
   piece of work with its own quality bar (terminology correction, timing
   alignment) this change does not take on.

## Consequences

- **Positive:** a viewer who wants to watch full width, uninterrupted by the
  study cards, has a direct, shareable link (`assistir.html`) per lecture.
- **Positive:** no new attack surface: no new route, no new auth code, no new
  cache-header logic -- the new page is served, gated and cached exactly as
  every other authenticated page already is.
- **Negative, accepted:** no captions or transcript track ships with this
  page; a follow-up would need a `.vtt` exporter over the existing ASR
  transcript, including the domain-dictionary correction pass that
  transcript already needs before any downstream use.
- **Neutral:** a lecture with no video yet (still processing) gets no
  `assistir.html` and no "Assistir em tela cheia" link, consistent with how
  the lecture page already omits the whole "Assistir" section when
  `lecture.video` is `None`.

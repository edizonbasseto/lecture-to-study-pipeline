# 0029. Study site scope, video delivery and access control

## Status

Accepted. Access control amended by ADR 0039 (phone sessions, per-person
revocation and edge-cache isolation).

## Context

Phase 6 delivers the only artifact in this milestone whose audience is a second
person rather than the operator: a private site where the processed lectures can
actually be studied. Three questions had to be settled before it could be built.

**Video.** SITE-03 originally read "Exclude lecture video files from the site and
publish only text and PDF materials". The operator's decision is
that the video belongs with the material: a study package whose text refers to a
technique is far more useful next to the recording of that technique. The
lectures are roughly 700 MB to 1.5 GB each, and there are dozens of them.

**Ordering.** The roadmap places Phase 6 after Phase 5, so the site would only
appear once every lecture had been processed. The operator needs the material
during the current semester, not after it.

**Access.** SITE-04 requires an explicit allowlist of authorized email addresses,
revocable without redeploying the content. The material is derived from licensed
course recordings; it must not be reachable by anyone outside the allowlist.

## Decision

1. **The video is published with the lecture**, amending SITE-03, but kept in a
   directory the site generator never writes into: `site.generator` produces
   `workspace/site/` (HTML, stylesheet, text and PDF materials -- what a container
   image build copies in) and `workspace/site-videos/` (every lecture's video)
   as two separate trees. `site.app` serves both, mounted at `/` and `/videos`
   respectively, behind the same authentication middleware, so the split is real
   in the served app today and stays real once the site ships as an image:
   the image carries the first tree, and the second is meant to live on a volume
   mounted at run time, so a lecture's video can be added or replaced without
   rebuilding it.

2. **Phase 6 is brought forward**, ahead of its stated dependency on Phase 5. The
   site is generated from whatever lectures are already processed and regenerated
   as each new one finishes; a lecture that has no package yet is listed as
   pending rather than hidden, which SITE-06 already requires for blocked
   lectures.

3. **Access is an email allowlist with a password per person**, held in a
   credentials file mounted from outside the image and read on each request, so
   revoking somebody is an edit to that file rather than a redeploy. The email is
   the identity SITE-04 names; the password is what proves it.

## Alternatives considered

1. **Magic-link sign-in by email.** Rejected for now: it needs an outbound mail
   service, a token store and a delivery path that can fail silently, for an
   audience of two people.
2. **A single shared password.** Rejected: SITE-04 requires an allowlist of
   individual addresses and revocation of one person without disturbing another.
3. **Videos inside the container image.** Rejected: tens of gigabytes in an image
   that must be rebuilt and pushed for every content update.
4. **Videos on third-party object storage with signed URLs.** Not chosen now, but
   the decision above keeps it open: the site refers to each video through its own
   route, so the storage behind that route can change without touching the pages.

## Consequences

- **Positive:** the second person gets text, PDF and the recording in one place,
  for the lectures that are ready, while the rest are still processing.
- **Negative, accepted:** the hosting server must carry enough disk for the videos, and
  uploading them is a separate, slower step than publishing the site itself.
- **Negative, accepted:** password authentication puts a credential in the
  operator's hands to distribute out of band. It is written to a file that is
  never committed, alongside the existing dotenv convention (ADR 0026).
- **Neutral:** SITE-07 and SITE-08 are unaffected -- the infrastructure is still
  declared as code and the site still ships as an OCI image with externalized
  configuration.

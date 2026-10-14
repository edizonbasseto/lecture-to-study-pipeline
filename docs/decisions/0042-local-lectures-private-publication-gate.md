# 0042. Local lectures and the private publication gate

## Status

Accepted.

## Context

Videos without a source-catalog id are registered by
`scripts/register_local_lecture.py` under synthetic ids 925100-925199 (calendar
semester 1) and 925200-925299 (calendar semester 2). In the private deployment,
a deploy script (not included in this repository) rsyncs the whole `workspace/study-guides/` tree with
`--delete`, and `site/catalog.py` publishes every directory found there.

## Decision

- A local lecture row in `config/local-lectures.local.yaml` carries
  `publish: false` by default (rows without the field load as false).
- `generate_personal_study_guides.py` compiles an unpublished local lecture into
  `--private-output-root` (default `workspace/study-guides-local`), a sibling
  tree that deploy never syncs. A private root nested inside the public root is
  refused (fail closed).
- Publishing is explicit: register with `--publish` (or set `publish: true`),
  then recompile; the bundle then lands in `workspace/study-guides/`.
- `generate_academic_catalog.py` lists only published local lectures.
- Registration is transactional (link, metadata, queue; rollback on failure)
  and idempotent for the same id and source. An exclusive file lock beside
  `queue.json` serializes register-versus-register only: the source-download
  path (`catalog_bridge.upsert_lecture_in_queue`) does not take it, so a
  simultaneous source download is not covered.
- Re-registering an existing id may change only the `publish` flag (same source
  and metadata required). Demoting `publish: true` to false is refused while a
  public bundle for the lecture exists under `workspace/study-guides`, because
  the bundle would otherwise stay deployed.
- Deploy channels (private deploy script, not included in this repository): `scripts/list_withheld_lecture_ids.py`
  lists every reserved id (derived from `RESERVED_ID_RANGES`) that is not
  published. Those ids are excluded, with `--delete-excluded`, from the
  study-guides rsync and from the mock-exam rsync (`exam.json` carries the
  answer key and `site/quiz.py` serves any exam directory present). The deploy
  aborts if the list cannot be produced. The quiz endpoint itself is not
  changed: the serving container cannot know the publish flags, and it only
  sees what the rsync delivered.
- Other synced channels carry no local lecture: credentials allowlist (no
  lecture data), `config/targets.local.yaml` and
  `config/academic-calendar.local.yaml` (source-catalog data only; local metadata is
  merged in memory at compile time and `local-lectures.local.yaml` is never
  synced). Site videos and the generated site derive from the synced guides.
- The batch download step never tries the remote source for local ids (source
  acquisition was part of the private deployment and is not included in this
  repository).

## Consequences

Local material cannot reach the public site by omission. Moving a bundle to the
public tree requires an explicit flag and a recompile.

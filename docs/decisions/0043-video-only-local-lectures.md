# 0043. Video-only local lectures

## Status

Accepted. Extends ADR 0042 (the publication gate is unchanged).

## Context

A set of 14 short video clips (suture and knot patterns) has no
speech. Transcription, Codex drafting, study guides, flashcards and exams add
nothing for them; the operator wants the videos alone on the study site.

## Decision

- `LocalLecture` gains `kind`: `study` (default; rows without the field load
  as `study`) or `video-only`. Registration takes `--video-only`. Re-registering
  an id with another kind is refused like any other metadata change.
- The kind changes only what is compiled. The gate of ADR 0042 is untouched:
  a video-only lecture with `publish: false` is compiled into the private root
  (`workspace/study-guides-local`), with `--publish` into the public tree.
- `generate_personal_study_guides.py` compiles a video-only id without reading
  evidence packs, ledgers, drafts or queue titles (the title comes from the
  local-lectures row). The bundle holds exactly `indice.md` (title, id, the
  notice "Vídeo sem material de estudo gerado: clipe sem narração." and the
  link), `.bundle.json` (identity) and `video/aula.mp4`, a hard link made by the
  same routine as compiled bundles. The staged tree is checked for exactly
  these files before it is published atomically at
  `<year>/<n>-semestre/<discipline>/<section>/<id>-<slug>/`.
- A video-only lecture has no exam: `list_withheld_lecture_ids.py exams` also
  lists published video-only ids, so no `exam.json` can reach the server for
  them. The guides channel follows ADR 0042 (published ids are synced).
- Site tolerance: `catalog.read_catalog` already accepts a bundle with only a
  video (`indice.md` and `.bundle.json` are ignored; the video is the "Vídeo da
  aula" material, served from the video library). `renderer` replaces the empty
  "Materiais" card grid with "Vídeo sem material de estudo gerado." when a
  lecture has neither cards nor extra materials.
- Registration of a video-only lecture creates the raw-video link and the
  metadata row but does NOT add a `queue.json` row, so batch download,
  extraction and Codex stages can never see these lectures. Compilation of a
  video-only id does not read `queue.json` and does not resolve typst, say or
  ffmpeg; those are loaded only when a study id is compiled in the same run.
- The notice on the site page is deliberately generic ("Vídeo sem material de
  estudo gerado."): it is accurate for any lecture that has only a video.
- The video is hard-linked, so the bundle shares the inode (and the mtime that
  the site uses for "recentes") with the raw file; touching the source changes
  the bundle's apparent date. Source and public root must share a filesystem;
  otherwise compilation fails with an explicit error naming both.

## Consequences

Publishing a clip needs three explicit acts: `--video-only --publish` at
registration, compilation, and deploy. The video is served from
`site-videos`, so each clip costs its file size on the server volume.

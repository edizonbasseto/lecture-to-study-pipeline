"""Minimal bundle of a video-only local lecture (ADR 0043).

A video-only lecture is a clip without narration: nothing is transcribed,
drafted or compiled. Its bundle holds only the index, the identity manifest
and the video, laid out the way compiled bundles expose the video.
"""

from __future__ import annotations

from pathlib import PurePosixPath

VIDEO_ONLY_NOTICE = "Vídeo sem material de estudo gerado: clipe sem narração."
INDEX_FILENAME = "indice.md"
IDENTITY_MANIFEST_FILENAME = ".bundle.json"


def render_video_only_index(
    lecture_id: str, title: str, video_relative_path: PurePosixPath
) -> str:
    """Markdown index: the title, the notice and the link to the video."""

    return (
        f"# {title}\n\n"
        f"Aula `{lecture_id}`.\n\n"
        f"{VIDEO_ONLY_NOTICE}\n\n"
        "## Aula original\n\n"
        f"- [Vídeo da aula]({video_relative_path.as_posix()})\n"
    )


def expected_video_only_files(video_relative_path: PurePosixPath) -> frozenset[str]:
    """Every file a staged video-only bundle must contain, and nothing else."""

    return frozenset(
        {INDEX_FILENAME, IDENTITY_MANIFEST_FILENAME, video_relative_path.as_posix()}
    )

"""Figure-caption domain model and traceable catalog store tests.

No local vision model is invoked in this suite -- only the domain models
and the filesystem-backed catalog store are exercised.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from lecture_study.captioning.catalog_store import (
    InvalidFrameFilenameError,
    discover_lecture_frames,
    frame_timestamp_seconds,
    read_figure_catalog,
    write_figure_catalog,
)
from lecture_study.domain.models import (
    ArtifactExecutionMode,
    FigureCaption,
    LectureFigureCatalog,
)


def _make_caption(
    frame_filename: str = "frame_0600.000.png",
    timestamp_seconds: float = 600.0,
    lecture_id: str = "10002",
    caption: str = "Uma incisao cirurgica com pontos de sutura visiveis.",
) -> FigureCaption:
    return FigureCaption(
        lecture_id=lecture_id,
        frame_filename=frame_filename,
        timestamp_seconds=timestamp_seconds,
        caption=caption,
    )


# --- FigureCaption -----------------------------------------------------


def test_figure_caption_defaults_to_pipeline_default_execution_mode() -> None:
    caption = _make_caption()
    assert caption.execution_mode is ArtifactExecutionMode.PIPELINE_DEFAULT


def test_figure_caption_rejects_unsafe_lecture_id() -> None:
    with pytest.raises(ValidationError):
        _make_caption(lecture_id="../../etc")


# --- LectureFigureCatalog -----------------------------------------------


def test_lecture_figure_catalog_accepts_distinct_frame_filenames() -> None:
    catalog = LectureFigureCatalog(
        lecture_id="10002",
        figures=[
            _make_caption(frame_filename="frame_0600.000.png"),
            _make_caption(frame_filename="frame_0637.500.png"),
        ],
        captioned_at=1_700_000_000.0,
    )
    assert len(catalog.figures) == 2


def test_lecture_figure_catalog_rejects_duplicate_frame_filenames() -> None:
    with pytest.raises(ValidationError):
        LectureFigureCatalog(
            lecture_id="10002",
            figures=[
                _make_caption(frame_filename="frame_0600.000.png"),
                _make_caption(frame_filename="frame_0600.000.png"),
            ],
            captioned_at=1_700_000_000.0,
        )


# --- frame_timestamp_seconds ---------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected_seconds"),
    [
        ("frame_0600.000.png", 600.0),
        ("frame_1350.000.png", 1350.0),
        ("frame_0000.000.png", 0.0),
        ("frame_0045.500.png", 45.5),
    ],
)
def test_frame_timestamp_seconds_parses_the_naming_convention(
    filename: str, expected_seconds: float
) -> None:
    assert frame_timestamp_seconds(Path(filename)) == expected_seconds


@pytest.mark.parametrize(
    "filename",
    [
        "frame_0600.png",
        "frame_0600.0.png",
        "slide_0600.000.png",
        "frame_0600.000.jpg",
        "not_a_frame_at_all.png",
        "frame_.000.png",
    ],
)
def test_frame_timestamp_seconds_rejects_non_conforming_filenames(
    filename: str,
) -> None:
    with pytest.raises(InvalidFrameFilenameError):
        frame_timestamp_seconds(Path(filename))


# --- discover_lecture_frames ----------------------------------------------


def test_discover_lecture_frames_returns_frames_sorted_by_timestamp(
    tmp_path: Path,
) -> None:
    lecture_dir = tmp_path / "10002"
    lecture_dir.mkdir()
    (lecture_dir / "frame_1350.000.png").write_bytes(b"")
    (lecture_dir / "frame_0000.000.png").write_bytes(b"")
    (lecture_dir / "frame_0600.000.png").write_bytes(b"")

    frames = discover_lecture_frames("10002", frames_root=tmp_path)

    assert [frame.name for frame in frames] == [
        "frame_0000.000.png",
        "frame_0600.000.png",
        "frame_1350.000.png",
    ]


def test_discover_lecture_frames_ignores_non_frame_files(tmp_path: Path) -> None:
    lecture_dir = tmp_path / "10002"
    lecture_dir.mkdir()
    (lecture_dir / "frame_0000.000.png").write_bytes(b"")
    (lecture_dir / "notes.txt").write_bytes(b"")

    frames = discover_lecture_frames("10002", frames_root=tmp_path)

    assert [frame.name for frame in frames] == ["frame_0000.000.png"]


def test_discover_lecture_frames_returns_empty_list_for_missing_lecture(
    tmp_path: Path,
) -> None:
    assert discover_lecture_frames("10002", frames_root=tmp_path) == []


def test_discover_lecture_frames_rejects_unsafe_lecture_id(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        discover_lecture_frames("../../etc", frames_root=tmp_path)


# --- write_figure_catalog / read_figure_catalog ---------------------------


def test_write_then_read_figure_catalog_round_trips(tmp_path: Path) -> None:
    catalog = LectureFigureCatalog(
        lecture_id="10002",
        figures=[_make_caption()],
        captioned_at=1_700_000_000.0,
    )

    write_figure_catalog(catalog, output_root=tmp_path)
    reloaded = read_figure_catalog("10002", output_root=tmp_path)

    assert reloaded == catalog


def test_write_figure_catalog_round_trips_byte_for_byte_on_reserialization(
    tmp_path: Path,
) -> None:
    catalog = LectureFigureCatalog(
        lecture_id="10002",
        figures=[_make_caption()],
        captioned_at=1_700_000_000.0,
    )

    write_figure_catalog(catalog, output_root=tmp_path)
    first_bytes = (tmp_path / "10002" / "figures.json").read_bytes()
    reloaded = read_figure_catalog("10002", output_root=tmp_path)
    write_figure_catalog(reloaded, output_root=tmp_path)
    second_bytes = (tmp_path / "10002" / "figures.json").read_bytes()

    assert first_bytes == second_bytes


def test_write_figure_catalog_uses_atomic_replace(tmp_path: Path) -> None:
    catalog = LectureFigureCatalog(
        lecture_id="10002",
        figures=[_make_caption()],
        captioned_at=1_700_000_000.0,
    )

    write_figure_catalog(catalog, output_root=tmp_path)

    output_path = tmp_path / "10002" / "figures.json"
    tmp_artifact = output_path.with_suffix(output_path.suffix + ".tmp")
    assert output_path.exists()
    assert not tmp_artifact.exists()


def test_write_figure_catalog_rejects_path_separator_lecture_id(
    tmp_path: Path,
) -> None:
    catalog = FigureCaption.model_construct(
        lecture_id="10002",
        frame_filename="frame_0600.000.png",
        timestamp_seconds=600.0,
        caption="x",
        execution_mode=ArtifactExecutionMode.PIPELINE_DEFAULT,
    )
    unsafe_catalog = LectureFigureCatalog.model_construct(
        lecture_id="../../etc",
        figures=[catalog],
        captioned_at=1_700_000_000.0,
    )

    with pytest.raises(ValidationError):
        write_figure_catalog(unsafe_catalog, output_root=tmp_path)

    assert not (tmp_path / "etc").exists()
    assert list(tmp_path.iterdir()) == []


def test_read_figure_catalog_rejects_unsafe_lecture_id(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        read_figure_catalog("../../etc", output_root=tmp_path)

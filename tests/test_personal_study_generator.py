"""Operational tests for the personal study bundle generator."""

import importlib.util
import inspect
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from typing import Any, Protocol, cast

import pytest

from lecture_study.domain.models import (
    ArtifactExecutionMode,
    EvidencePack,
    FigureCaption,
    LearningUnitDraft,
    LectureFigureCatalog,
    MockExam,
    MockQuestion,
    PersonalStudyFlashcard,
    PersonalStudyFlashcardDeck,
    PodcastLine,
    PodcastScript,
    SemanticFact,
    SemanticLedger,
)
from lecture_study.study_materials.academic_catalog import (
    ACADEMIC_BUNDLE_DOCUMENT_FILES,
    BUNDLE_FIGURES_SUBDIRECTORY,
)
from lecture_study.study_materials.bundle import (
    PersonalStudyBundle,
    StudyDocument,
    build_personal_study_bundle,
    critical_review_document,
    inference_mock_exam_document,
    inference_podcast_script_document,
    mock_exam_document,
    podcast_script_document,
    render_flashcards_csv,
    render_flashcards_markdown,
    render_generated_flashcards_csv,
    render_generated_flashcards_markdown,
    render_printable_generated_flashcards_typst,
)
from lecture_study.study_materials.compiler import (
    StudyEditionKind,
    build_personal_study_editions,
)
from lecture_study.study_materials.temporal_coverage import (
    CoverageStatus,
    TemporalCoverageReport,
)

VideoLinker = Callable[[Path, Path], Path]
OutputRootPreparer = Callable[[Path], Path]
ManifestWriter = Callable[[Path, str], None]
StagedBundleValidator = Callable[..., None]
PublicationLock = Callable[[Path, str], AbstractContextManager[None]]


class BundlePublisher(Protocol):
    def __call__(
        self,
        *,
        output_root: Path,
        lecture_id: str,
        target_relative_path: Path,
        staging_dir: Path,
    ) -> Path: ...


class PodcastDurationValidator(Protocol):
    def __call__(
        self,
        *,
        duration_seconds: float,
        podcast_text: str,
    ) -> None: ...


def _load_generator_module() -> ModuleType:
    script_path = (
        Path(__file__).parents[1] / "scripts" / "generate_personal_study_guides.py"
    )
    spec = importlib.util.spec_from_file_location(
        "generate_personal_study_guides",
        script_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load generator script from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_GENERATOR_MODULE = _load_generator_module()
_link_video_into_bundle = cast(
    VideoLinker,
    _GENERATOR_MODULE._link_video_into_bundle,
)
_prepare_trusted_output_root = cast(
    OutputRootPreparer,
    _GENERATOR_MODULE._prepare_trusted_output_root,
)
_publish_staged_bundle = cast(
    BundlePublisher,
    _GENERATOR_MODULE._publish_staged_bundle,
)
_write_bundle_manifest = cast(
    ManifestWriter,
    _GENERATOR_MODULE._write_bundle_manifest,
)
_validate_staged_bundle = cast(
    StagedBundleValidator,
    _GENERATOR_MODULE._validate_staged_bundle,
)
_validate_podcast_duration = cast(
    PodcastDurationValidator,
    _GENERATOR_MODULE._validate_podcast_duration,
)
_lecture_publication_lock = cast(
    PublicationLock,
    _GENERATOR_MODULE._lecture_publication_lock,
)
_bundle_documents = cast(
    "Callable[..., tuple[tuple[str, StudyDocument], ...]]",
    _GENERATOR_MODULE._bundle_documents,
)
_load_inference_mock_exam = cast(
    "Callable[[Path, str], MockExam]",
    _GENERATOR_MODULE._load_inference_mock_exam,
)
_load_inference_podcast_script = cast(
    "Callable[[Path, str], PodcastScript]",
    _GENERATOR_MODULE._load_inference_podcast_script,
)
_copy_inference_podcast_audio = cast(
    "Callable[..., None]",
    _GENERATOR_MODULE._copy_inference_podcast_audio,
)
_load_generated_flashcard_deck = cast(
    "Callable[[Path, str], PersonalStudyFlashcardDeck]",
    _GENERATOR_MODULE._load_generated_flashcard_deck,
)
_write_flashcards = cast(
    "Callable[..., None]",
    _GENERATOR_MODULE._write_flashcards,
)
_load_figure_catalog = cast(
    "Callable[[Path, str], LectureFigureCatalog]",
    _GENERATOR_MODULE._load_figure_catalog,
)
_write_figures = cast(
    "Callable[..., tuple[str, ...]]",
    _GENERATOR_MODULE._write_figures,
)
_write_bundle = cast(
    "Callable[..., tuple[Path, tuple[str, ...]]]",
    _GENERATOR_MODULE._write_bundle,
)


def _question_payload(
    question_text: str = "Cão de 8 anos apresenta FC 40bpm. Qual a conduta?",
) -> dict[str, object]:
    return {
        "question_text": question_text,
        "options": {
            "A": "Nenhuma conduta.",
            "B": "Monitorar e investigar causa.",
            "C": "Eutanasia imediata.",
            "D": "Aumentar a dose de anestesico.",
        },
        "correct_answer": "B",
        "explanation": "Bradicardia sinusal exige monitoramento contínuo do paciente.",
    }


def _make_inference_exam(lecture_id: str = "10001") -> MockExam:
    return MockExam(
        lecture_id=lecture_id,
        questions=[MockQuestion(**_question_payload())],
        execution_mode=ArtifactExecutionMode.UNCERTIFIED_PERSONAL_STUDY,
    )


def _make_inference_podcast_script(lecture_id: str = "10001") -> PodcastScript:
    return PodcastScript(
        lecture_id=lecture_id,
        title="Bradicardia em cães idosos",
        dialogue=[
            PodcastLine(
                speaker="Host",
                text="O que aconteceu com o paciente de 8 anos?",
                source_unit_id="unit-001",
            ),
            PodcastLine(
                speaker="Expert",
                text="A bradicardia sinusal exige monitoramento.",
                source_unit_id="unit-001",
            ),
        ],
        execution_mode=ArtifactExecutionMode.UNCERTIFIED_PERSONAL_STUDY,
    )


def _make_generated_flashcard_deck(
    lecture_id: str = "10001",
) -> PersonalStudyFlashcardDeck:
    return PersonalStudyFlashcardDeck(
        lecture_id=lecture_id,
        cards=[
            PersonalStudyFlashcard(
                prompt="Qual a conduta para bradicardia sinusal em cão idoso?",
                answer="Monitorar e investigar a causa subjacente.",
                source_unit_sequence=1,
            ),
            PersonalStudyFlashcard(
                prompt="Qual a dosagem correta de dipirona?",
                answer="Conforme protocolo da aula.",
                source_unit_sequence=1,
            ),
        ],
        execution_mode=ArtifactExecutionMode.UNCERTIFIED_PERSONAL_STUDY,
    )


def _make_figure_catalog(
    lecture_id: str = "10001",
    *,
    frame_filenames: tuple[str, ...] = ("frame_0600.000.png",),
) -> LectureFigureCatalog:
    return LectureFigureCatalog(
        lecture_id=lecture_id,
        captioned_at=1_726_000_000.0,
        figures=[
            FigureCaption(
                lecture_id=lecture_id,
                frame_filename=frame_filename,
                timestamp_seconds=float(index * 600),
                caption=f"Legenda da figura {index}.",
            )
            for index, frame_filename in enumerate(frame_filenames, start=1)
        ],
    )


def test_figure_selection_never_repeats_a_looped_slide_across_units() -> None:
    """Regression, lecture 10004 (2026-09-17, operator-reported on the live
    site): a recording that loops its intro slide produced 46
    near-identical captioned frames, and the intro caption -- long, it
    names every piece of chrome -- won per-window max-by-length in 7 of
    15 windows, so seven units illustrated themselves with the same
    slide. A caption signature may win only once per lecture."""
    intro_caption = (
        "Slide de apresentação com título 'Introdução' mostrando as "
        "dimensões das estruturas anatômicas, a imagem circular de uma "
        "mão com luva cirúrgica segurando instrumentos cirúrgicos, o "
        "logotipo da instituição no canto inferior esquerdo e no canto "
        "superior direito, junto com um ícone de câmera e a data/hora "
        "09:19:29 no canto inferior direito, fundo branco com círculos "
        "azuis e pretos."
    )
    later_intro_caption = (
        "Apresentação de abertura com o título 'Introdução', exibindo as "
        "medidas das estruturas anatômicas com os valores indicados, a "
        "imagem circular da mão enluvada segurando instrumentos, o "
        "logotipo da instituição nos cantos, o ícone de câmera e o "
        "registro de data e hora 09:36:22 no canto inferior direito do "
        "slide, sobre fundo branco com formas circulares."
    )
    shorter_content_caption = "Slide 'Tireoidectomia': exposição da tireoide."
    catalog = LectureFigureCatalog(
        lecture_id="10004",
        captioned_at=1_726_000_000.0,
        figures=[
            FigureCaption(
                lecture_id="10004",
                frame_filename="frame_0207.760.png",
                timestamp_seconds=207.0,
                caption=intro_caption,
            ),
            FigureCaption(
                lecture_id="10004",
                frame_filename="frame_1200.000.png",
                timestamp_seconds=1200.0,
                caption=later_intro_caption,
            ),
            FigureCaption(
                lecture_id="10004",
                frame_filename="frame_1500.000.png",
                timestamp_seconds=1500.0,
                caption=shorter_content_caption,
            ),
        ],
    )
    pack_one = EvidencePack(
        lecture_id="10004",
        pack_sequence=1,
        start_time=0.0,
        end_time=900.0,
        anchor_frame=None,
    )
    pack_two = EvidencePack(
        lecture_id="10004",
        pack_sequence=2,
        start_time=900.0,
        end_time=1800.0,
        anchor_frame=None,
    )

    def fake_pixel_hash(frame_filename: str, **_kwargs: object) -> int | None:
        # The intro frames are the SAME slide (identical pixels modulo the
        # ticking timestamp corner, which vanishes at hash scale); the
        # vision model still paraphrased them into caption variants, so
        # caption signatures alone do not catch the repeat -- pixels do.
        if frame_filename.startswith(("frame_02", "frame_12")):
            return 0b1111111111111111
        return None

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(_GENERATOR_MODULE, "_frame_pixel_signature", fake_pixel_hash)
    try:
        selection = _GENERATOR_MODULE._select_figures_by_pack_sequence(
            (pack_one, pack_two),
            catalog,
            slides_root=Path("workspace/data/03_extracted_slides"),
            ffmpeg_executable=Path("ffmpeg"),
            environment={},
        )
    finally:
        monkeypatch.undo()

    # Pack one: the intro caption is the longest candidate, it wins once.
    assert selection[1][0].caption.startswith("Slide de apresentação")
    # Pack two: the later intro paraphrase is LONGER than the content
    # frame and has a DIFFERENT caption signature, but its pixels are
    # spent -- the content frame wins instead of the lecture repeating
    # its intro slide.
    assert selection[2][0].caption.startswith("Slide 'Tireoidectomia'")


def test_figure_selection_falls_back_to_captions_when_frames_are_unhashable() -> None:
    """The caption layer is load-bearing on its own: when pixel hashing is
    unavailable (corrupt frame, ffmpeg failure -- hash None never blocks),
    identical caption signatures must still stop the repeat."""
    caption = (
        "Slide de apresentação com título 'Introdução', mão com luva "
        "cirúrgica, logotipo da instituição, data/hora 09:19:29."
    )
    same_slide_later = caption.replace("09:19:29", "09:36:22")
    catalog = LectureFigureCatalog(
        lecture_id="10004",
        captioned_at=1_726_000_000.0,
        figures=[
            FigureCaption(
                lecture_id="10004",
                frame_filename="frame_0207.760.png",
                timestamp_seconds=207.0,
                caption=caption,
            ),
            FigureCaption(
                lecture_id="10004",
                frame_filename="frame_1200.000.png",
                timestamp_seconds=1200.0,
                caption=same_slide_later,
            ),
        ],
    )
    pack_one = EvidencePack(
        lecture_id="10004", pack_sequence=1, start_time=0.0, end_time=900.0
    )
    pack_two = EvidencePack(
        lecture_id="10004", pack_sequence=2, start_time=900.0, end_time=1800.0
    )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        _GENERATOR_MODULE, "_frame_pixel_signature", lambda *_a, **_k: None
    )
    try:
        selection = _GENERATOR_MODULE._select_figures_by_pack_sequence(
            (pack_one, pack_two),
            catalog,
            slides_root=Path("workspace/data/03_extracted_slides"),
            ffmpeg_executable=Path("ffmpeg"),
            environment={},
        )
    finally:
        monkeypatch.undo()

    assert selection[1][0].caption.startswith("Slide de apresentação")
    assert selection[2] == ()


def test_a_corrupt_frame_degrades_to_unhashable_never_aborts(tmp_path: Path) -> None:
    """Contract of _frame_pixel_signature's docstring: a zero-byte or
    truncated frame makes ffmpeg exit non-zero with under-1KB stderr --
    CalledProcessError must be swallowed into None (unhashable), not
    escape and kill the whole lecture's bundle generation (review repro,
    2026-09-17)."""
    slides_root = tmp_path / "03_extracted_slides"
    (slides_root / "10004").mkdir(parents=True)
    (slides_root / "10004" / "frame_0000.000.png").write_bytes(b"")

    signature = _GENERATOR_MODULE._frame_pixel_signature(
        "frame_0000.000.png",
        lecture_id="10004",
        slides_root=slides_root,
        ffmpeg_executable=Path(
            subprocess.run(
                ["which", "ffmpeg"], capture_output=True, text=True
            ).stdout.strip()
        ),
        environment={"PATH": os.environ["PATH"]},
    )

    assert signature is None


def test_figure_signature_normalizes_ticking_timestamps() -> None:
    assert _GENERATOR_MODULE._figure_signature(
        "Slide 'Introdução' capturado às 09:19:29"
    ) == _GENERATOR_MODULE._figure_signature("Slide 'Introdução' capturado às 11:03:58")


def _bundle() -> PersonalStudyBundle:
    editions = build_personal_study_editions(
        lecture_id="10001",
        lecture_title="Cardiologia veterinária",
        drafts=(
            LearningUnitDraft(
                lecture_id="10001",
                pack_sequence=1,
                title="Bradicardia",
                body_markdown="Bradicardia sinusal exige monitoramento clínico.",
                clinical_warnings=[],
            ),
        ),
    )
    return build_personal_study_bundle(
        complete=editions[StudyEditionKind.COMPLETE],
        summary=editions[StudyEditionKind.SUMMARY],
    )


def test_video_is_a_real_file_inside_the_lecture_bundle(tmp_path: Path) -> None:
    source_video = tmp_path / "source" / "lecture.mp4"
    source_video.parent.mkdir()
    source_video.write_bytes(b"video-content")
    output_dir = tmp_path / "study-guides" / "lecture-001"

    relative_video_path = _link_video_into_bundle(output_dir, source_video)

    bundled_video = output_dir / relative_video_path
    assert relative_video_path == Path("video/aula.mp4")
    assert bundled_video.read_bytes() == b"video-content"
    assert bundled_video.samefile(source_video)


def test_video_linking_is_idempotent(tmp_path: Path) -> None:
    source_video = tmp_path / "source" / "lecture.mp4"
    source_video.parent.mkdir()
    source_video.write_bytes(b"video-content")
    output_dir = tmp_path / "study-guides" / "lecture-001"

    first_path = _link_video_into_bundle(output_dir, source_video)
    second_path = _link_video_into_bundle(output_dir, source_video)

    assert first_path == second_path
    assert (output_dir / second_path).samefile(source_video)


def test_video_linking_rejects_symlinked_video_directory(tmp_path: Path) -> None:
    source_video = tmp_path / "source" / "lecture.mp4"
    source_video.parent.mkdir()
    source_video.write_bytes(b"video-content")
    output_dir = tmp_path / "study-guides" / "lecture-001"
    external_dir = tmp_path / "external"
    output_dir.mkdir(parents=True)
    external_dir.mkdir()
    (output_dir / "video").symlink_to(external_dir, target_is_directory=True)

    with pytest.raises(RuntimeError, match="link"):
        _link_video_into_bundle(output_dir, source_video)

    assert tuple(external_dir.iterdir()) == ()


def test_output_root_rejects_symlinked_path_components(tmp_path: Path) -> None:
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(external_dir, target_is_directory=True)

    with pytest.raises(RuntimeError, match="link simbólico"):
        _prepare_trusted_output_root(linked_root)


def test_publication_lock_serializes_concurrent_writers(tmp_path: Path) -> None:
    output_root = _prepare_trusted_output_root(tmp_path / "study-guides")
    acquired_marker = tmp_path / "second-writer-acquired"
    script_path = (
        Path(__file__).parents[1] / "scripts" / "generate_personal_study_guides.py"
    )
    child_code = """
import importlib.util
from pathlib import Path
import sys

script_path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("study_generator_child", script_path)
if spec is None or spec.loader is None:
    raise RuntimeError("generator module unavailable")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
with module._lecture_publication_lock(Path(sys.argv[2]), "10001"):
    Path(sys.argv[3]).write_text("acquired", encoding="utf-8")
"""
    contention_probe = """
import fcntl
import os
import re
import sys

descriptor = os.open(sys.argv[1], os.O_RDWR)
try:
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(23)
raise SystemExit(0)
"""
    with _lecture_publication_lock(output_root, "10001"):
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                contention_probe,
                str(output_root / ".locks/10001.lock"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert probe.returncode == 23, probe.stderr
        assert not acquired_marker.exists()

    child = subprocess.run(
        [
            sys.executable,
            "-c",
            child_code,
            str(script_path),
            str(output_root),
            str(acquired_marker),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert child.returncode == 0, f"{child.stdout}\n{child.stderr}"
    assert acquired_marker.read_text(encoding="utf-8") == "acquired"


def test_bundle_is_relocated_when_academic_metadata_changes(tmp_path: Path) -> None:
    output_root = _prepare_trusted_output_root(tmp_path / "study-guides")
    source_video = tmp_path / "source" / "lecture.mp4"
    source_video.parent.mkdir()
    source_video.write_bytes(b"video-content")
    current = (
        output_root
        / "2026/1-semestre/disciplina-original/secao-original/10001-titulo-original"
    )
    _write_legacy_bundle_shape(current, "10001", source_video)
    targets = (
        Path(
            "2026/2-semestre/disciplina-original/secao-original/10001-titulo-original"
        ),
        Path("2026/2-semestre/disciplina-nova/secao-original/10001-titulo-original"),
        Path("2026/2-semestre/disciplina-nova/secao-nova/10001-titulo-original"),
        Path("2026/2-semestre/disciplina-nova/secao-nova/10001-titulo-corrigido"),
    )

    for target in targets:
        staging = Path(tempfile.mkdtemp(prefix=".test-stage.", dir=output_root))
        _write_minimal_bundle(staging, "10001", source_video)
        current = _publish_staged_bundle(
            output_root=output_root,
            lecture_id="10001",
            target_relative_path=target,
            staging_dir=staging,
        )
        bundles = tuple(
            manifest.parent
            for manifest in output_root.rglob(".bundle.json")
            if not any(
                part.startswith(".")
                for part in manifest.parent.relative_to(output_root).parts
            )
        )

        assert bundles == (current,)
        assert (current / "indice.md").is_file()
        assert (current / "video/aula.mp4").samefile(source_video)


def test_numeric_hierarchy_directory_is_not_inferred_as_a_bundle(
    tmp_path: Path,
) -> None:
    output_root = _prepare_trusted_output_root(tmp_path / "study-guides")
    unrelated = output_root / "2026/1-semestre/10001/secao/conteudo"
    unrelated.mkdir(parents=True)
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"video-content")
    staging = Path(tempfile.mkdtemp(prefix=".test-stage.", dir=output_root))
    _write_minimal_bundle(staging, "10001", source_video)

    published = _publish_staged_bundle(
        output_root=output_root,
        lecture_id="10001",
        target_relative_path=Path("2026/1-semestre/disciplina/secao/10001-aula"),
        staging_dir=staging,
    )

    assert unrelated.is_dir()
    assert published.is_dir()


def test_spoofed_incomplete_legacy_index_cannot_authorize_deletion(
    tmp_path: Path,
) -> None:
    output_root = _prepare_trusted_output_root(tmp_path / "study-guides")
    unrelated = output_root / "unrelated-course"
    unrelated.mkdir()
    (unrelated / "indice.md").write_text(
        "# Aula\n\nAula `10001` - identidade forjada.\n",
        encoding="utf-8",
    )
    retained = unrelated / "retained.txt"
    retained.write_text("must remain", encoding="utf-8")
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"video-content")
    staging = Path(tempfile.mkdtemp(prefix=".test-stage.", dir=output_root))
    _write_minimal_bundle(staging, "10001", source_video)

    with pytest.raises(ValueError, match="legado incompleto"):
        _publish_staged_bundle(
            output_root=output_root,
            lecture_id="10001",
            target_relative_path=Path("2026/1-semestre/disciplina/secao/10001-aula"),
            staging_dir=staging,
        )

    assert unrelated.is_dir()
    assert retained.read_text(encoding="utf-8") == "must remain"


def test_publication_rejects_symlinked_target_component(tmp_path: Path) -> None:
    output_root = _prepare_trusted_output_root(tmp_path / "study-guides")
    external = tmp_path / "external"
    external.mkdir()
    (output_root / "2026").symlink_to(external, target_is_directory=True)
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"video-content")
    staging = Path(tempfile.mkdtemp(prefix=".test-stage.", dir=output_root))
    _write_minimal_bundle(staging, "10001", source_video)

    with pytest.raises(RuntimeError, match="link simbólico"):
        _publish_staged_bundle(
            output_root=output_root,
            lecture_id="10001",
            target_relative_path=Path("2026/1-semestre/disciplina/secao/10001-aula"),
            staging_dir=staging,
        )

    assert tuple(external.iterdir()) == ()


def test_bundle_relocation_fails_closed_when_identity_is_duplicated(
    tmp_path: Path,
) -> None:
    output_root = _prepare_trusted_output_root(tmp_path / "study-guides")
    first = output_root / "2026/1-semestre/a/a/10001-primeiro"
    second = output_root / "2026/1-semestre/b/b/10001-segundo"
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"video-content")
    _write_minimal_bundle(first, "10001", source_video)
    _write_minimal_bundle(second, "10001", source_video)
    staging = Path(tempfile.mkdtemp(prefix=".test-stage.", dir=output_root))
    _write_minimal_bundle(staging, "10001", source_video)

    with pytest.raises(RuntimeError, match="mais de um pacote"):
        _publish_staged_bundle(
            output_root=output_root,
            lecture_id="10001",
            target_relative_path=Path("2026/1-semestre/c/c/10001-terceiro"),
            staging_dir=staging,
        )


def test_incomplete_staging_does_not_replace_existing_bundle(
    tmp_path: Path,
) -> None:
    output_root = _prepare_trusted_output_root(tmp_path / "study-guides")
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"video-content")
    current = output_root / "2026/1-semestre/a/a/10001-atual"
    _write_minimal_bundle(current, "10001", source_video)
    staging = Path(tempfile.mkdtemp(prefix=".test-stage.", dir=output_root))
    (staging / "indice.md").write_text("incompleto", encoding="utf-8")

    with pytest.raises(RuntimeError, match="pacote de estudo incompleto"):
        _validate_staged_bundle(staging, Path("video/aula.mp4"))

    assert (current / "indice.md").is_file()
    assert (current / "video/aula.mp4").samefile(source_video)


@pytest.mark.parametrize(
    ("duration_seconds", "podcast_text"),
    (
        (0.0, "conteúdo clínico"),
        (10.0, "conteúdo " * 100),
        (float("nan"), "conteúdo clínico"),
    ),
)
def test_podcast_duration_validation_rejects_empty_or_truncated_audio(
    duration_seconds: float,
    podcast_text: str,
) -> None:
    with pytest.raises(ValueError, match="vazio ou truncado"):
        _validate_podcast_duration(
            duration_seconds=duration_seconds,
            podcast_text=podcast_text,
        )


def test_podcast_duration_validation_accepts_grounded_audio_length() -> None:
    _validate_podcast_duration(
        duration_seconds=30.0,
        podcast_text="conteúdo " * 100,
    )


def _write_minimal_bundle(
    directory: Path,
    lecture_id: str,
    source_video: Path,
) -> None:
    (directory / "video").mkdir(parents=True, exist_ok=True)
    (directory / "indice.md").write_text(
        f"# Aula\n\nAula `{lecture_id}` - pacote identificado.\n",
        encoding="utf-8",
    )
    os.link(source_video, directory / "video/aula.mp4")
    _write_bundle_manifest(directory, lecture_id)


class TestInferenceMockExamDocument:
    def test_first_section_holds_numbered_stems_with_four_options(self) -> None:
        exam = _make_inference_exam()

        document = inference_mock_exam_document(exam)

        assert isinstance(document, StudyDocument)
        questions_section = document.sections[0]
        assert questions_section.title == "Questões"
        assert questions_section.numbered is True
        question_item = questions_section.items[0]
        assert exam.questions[0].question_text in question_item
        for letter, text in exam.questions[0].options.items():
            assert f"{letter}) {text}" in question_item

    def test_second_section_holds_the_answer_key_with_explanations(self) -> None:
        exam = _make_inference_exam()

        document = inference_mock_exam_document(exam)

        answer_section = document.sections[1]
        assert answer_section.title == "Gabarito comentado"
        assert answer_section.page_break_before is True
        answer_item = answer_section.items[0]
        assert exam.questions[0].correct_answer in answer_item
        assert exam.questions[0].explanation in answer_item

    def test_subtitle_discloses_no_clinical_authority(self) -> None:
        exam = _make_inference_exam()

        document = inference_mock_exam_document(exam)

        assert "sem certificação" in document.subtitle

    def test_questions_section_has_no_fake_leading_question(self) -> None:
        """Regression (2026-09-15 document review): the disclaimer used to
        also be inserted as the numbered list's first item, rendering as a
        fake "Questão 1" and shifting every real question's number by one.
        It already renders as the document's own subtitle -- no need to
        repeat it inside the numbered list."""
        exam = _make_inference_exam()

        document = inference_mock_exam_document(exam)

        questions_section = document.sections[0]
        assert len(questions_section.items) == len(exam.questions)
        assert exam.questions[0].question_text in questions_section.items[0]
        assert not any("sem certificação" in item for item in questions_section.items)


class TestBundleDocumentsInferenceSplice:
    def test_flag_absent_produces_the_same_document_set_as_today(self) -> None:
        bundle = _bundle()

        documents = _bundle_documents(bundle)

        assert [stem for stem, _document in documents] == [
            "resumo_critico",
            "simulado",
            "roteiro_revisao",
            "podcast_roteiro",
        ]
        resumo_critico = dict(documents)["resumo_critico"]
        assert resumo_critico == critical_review_document(bundle)
        simulado = dict(documents)["simulado"]
        assert simulado == mock_exam_document(bundle)
        podcast_roteiro = dict(documents)["podcast_roteiro"]
        assert podcast_roteiro == podcast_script_document(bundle)

    def test_flag_present_sources_simulado_from_the_inference_payload(self) -> None:
        bundle = _bundle()
        inference_exam = _make_inference_exam()

        documents = _bundle_documents(bundle, inference_exam)

        simulado = dict(documents)["simulado"]
        assert simulado == inference_mock_exam_document(inference_exam)
        assert simulado != mock_exam_document(bundle)
        resumo_critico = dict(documents)["resumo_critico"]
        assert resumo_critico == critical_review_document(
            bundle, inference_exam=inference_exam
        )

    def test_flag_present_sources_podcast_roteiro_from_the_inference_payload(
        self,
    ) -> None:
        bundle = _bundle()
        inference_exam = _make_inference_exam()
        inference_script = _make_inference_podcast_script()

        documents = _bundle_documents(bundle, inference_exam, inference_script)

        podcast_roteiro = dict(documents)["podcast_roteiro"]
        assert podcast_roteiro == inference_podcast_script_document(inference_script)
        assert podcast_roteiro != podcast_script_document(bundle)


class TestInferencePodcastScriptDocument:
    def test_roteiro_section_lists_every_dialogue_line_in_speaking_order(
        self,
    ) -> None:
        script = _make_inference_podcast_script()

        document = inference_podcast_script_document(script)

        assert isinstance(document, StudyDocument)
        roteiro_section = document.sections[0]
        assert roteiro_section.title == "Roteiro"
        for line in script.dialogue:
            assert any(
                item == f"{line.speaker}: {line.text}" for item in roteiro_section.items
            )

    def test_subtitle_and_leading_item_disclose_no_clinical_authority(self) -> None:
        script = _make_inference_podcast_script()

        document = inference_podcast_script_document(script)

        assert "sem certificação" in document.subtitle
        assert "sem certificação" in document.sections[0].items[0]


class TestLoadInferenceMockExam:
    def test_loads_and_validates_the_compiled_exam_json(self, tmp_path: Path) -> None:
        exam = _make_inference_exam(lecture_id="10001")
        lecture_dir = tmp_path / "10001"
        lecture_dir.mkdir()
        (lecture_dir / "exam.json").write_text(exam.model_dump_json(), encoding="utf-8")

        loaded = _load_inference_mock_exam(tmp_path, "10001")

        assert loaded == exam

    def test_raises_naming_the_missing_path_instead_of_falling_back(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(RuntimeError, match=str(tmp_path / "10001" / "exam.json")):
            _load_inference_mock_exam(tmp_path, "10001")


class TestLoadInferencePodcastScript:
    def test_loads_and_validates_the_compiled_script_json(self, tmp_path: Path) -> None:
        script = _make_inference_podcast_script(lecture_id="10001")
        lecture_dir = tmp_path / "10001"
        lecture_dir.mkdir()
        (lecture_dir / "script.json").write_text(
            script.model_dump_json(), encoding="utf-8"
        )

        loaded = _load_inference_podcast_script(tmp_path, "10001")

        assert loaded == script

    def test_raises_naming_the_missing_path_instead_of_falling_back(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(RuntimeError, match=str(tmp_path / "10001" / "script.json")):
            _load_inference_podcast_script(tmp_path, "10001")


class TestCopyInferencePodcastAudio:
    def test_places_the_source_audio_under_the_bundles_existing_filename(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _make_inference_podcast_script(lecture_id="10001")
        source_root = tmp_path / "audio-source"
        lecture_dir = source_root / "10001"
        lecture_dir.mkdir(parents=True)
        (lecture_dir / "podcast_final.m4a").write_bytes(b"fake-m4a")
        output_dir = tmp_path / "staged"
        output_dir.mkdir()
        monkeypatch.setattr(
            _GENERATOR_MODULE,
            "_probe_media_duration",
            lambda *_args, **_kwargs: 30.0,
        )

        _copy_inference_podcast_audio(
            output_dir,
            source_root,
            script,
            ffprobe_executable=Path("/trusted/ffprobe"),
            environment={},
        )

        destination = output_dir / "podcast_revisao.m4a"
        assert destination.read_bytes() == b"fake-m4a"
        assert sorted(path.name for path in output_dir.iterdir()) == [
            "podcast_revisao.m4a"
        ]

    def test_raises_naming_the_missing_audio_path(self, tmp_path: Path) -> None:
        script = _make_inference_podcast_script(lecture_id="10001")
        source_root = tmp_path / "audio-source"
        output_dir = tmp_path / "staged"
        output_dir.mkdir()

        with pytest.raises(
            RuntimeError,
            match=str(source_root / "10001" / "podcast_final.m4a"),
        ):
            _copy_inference_podcast_audio(
                output_dir,
                source_root,
                script,
                ffprobe_executable=Path("/trusted/ffprobe"),
                environment={},
            )

    def test_rejects_a_truncated_audio_file_via_the_existing_duration_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _make_inference_podcast_script(lecture_id="10001")
        source_root = tmp_path / "audio-source"
        lecture_dir = source_root / "10001"
        lecture_dir.mkdir(parents=True)
        (lecture_dir / "podcast_final.m4a").write_bytes(b"truncated")
        output_dir = tmp_path / "staged"
        output_dir.mkdir()
        monkeypatch.setattr(
            _GENERATOR_MODULE,
            "_probe_media_duration",
            lambda *_args, **_kwargs: 0.001,
        )

        with pytest.raises(ValueError, match="vazio ou truncado"):
            _copy_inference_podcast_audio(
                output_dir,
                source_root,
                script,
                ffprobe_executable=Path("/trusted/ffprobe"),
                environment={},
            )

        assert not (output_dir / "podcast_revisao.m4a").exists()
        assert sorted(output_dir.iterdir()) == []


def _write_legacy_bundle_shape(
    directory: Path,
    lecture_id: str,
    source_video: Path,
) -> None:
    for relative_path in ACADEMIC_BUNDLE_DOCUMENT_FILES:
        file_path = directory / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            f"# Aula\n\nAula `{lecture_id}` - pacote legado.\n"
            if relative_path == "indice.md"
            else "artefato legado"
        )
        file_path.write_text(content, encoding="utf-8")
    video_path = directory / "video/aula.mp4"
    video_path.parent.mkdir()
    os.link(source_video, video_path)


class TestGeneratedFlashcardsMarkdown:
    def test_renders_every_question_then_every_answer(self) -> None:
        """Regression (2026-09-15): the operator asked for all questions
        listed first, then all answers -- a self-quiz read, not interleaved
        pairs where the next answer sits right under each question."""
        deck = _make_generated_flashcard_deck()

        markdown = render_generated_flashcards_markdown(deck)

        assert "sem certificação" in markdown
        questions_index = markdown.index("## Perguntas")
        answers_index = markdown.index("## Respostas")
        assert questions_index < answers_index
        for index, card in enumerate(deck.cards, start=1):
            question_line = f"{index}. {card.prompt}"
            answer_line = f"{index}. {card.answer}"
            assert questions_index < markdown.index(question_line) < answers_index
            assert markdown.index(answer_line) > answers_index


class TestGeneratedFlashcardsCsv:
    def test_renders_one_row_per_card_with_sanitized_fields(self) -> None:
        deck = PersonalStudyFlashcardDeck(
            lecture_id="10001",
            cards=[
                PersonalStudyFlashcard(
                    prompt="=SOMA(A1:A2)?",
                    answer="Resposta normal.",
                    source_unit_sequence=1,
                )
            ],
            execution_mode=ArtifactExecutionMode.UNCERTIFIED_PERSONAL_STUDY,
        )

        csv_text = render_generated_flashcards_csv(deck)

        assert csv_text.splitlines()[0] == "'=SOMA(A1:A2)?,Resposta normal.,"


class TestPrintableGeneratedFlashcardsTypst:
    def test_produces_the_same_sheet_layout_shape_as_the_deterministic_builder(
        self,
    ) -> None:
        deck = _make_generated_flashcard_deck()

        typst_source = render_printable_generated_flashcards_typst(deck)

        assert typst_source.count("#study-card(") >= len(deck.cards)
        for card in deck.cards:
            assert card.prompt in typst_source
            assert card.answer in typst_source


class TestLoadGeneratedFlashcardDeck:
    def test_loads_and_validates_the_generated_deck_json(self, tmp_path: Path) -> None:
        deck = _make_generated_flashcard_deck(lecture_id="10001")
        lecture_dir = tmp_path / "10001"
        lecture_dir.mkdir()
        (lecture_dir / "deck.json").write_text(deck.model_dump_json(), encoding="utf-8")

        loaded = _load_generated_flashcard_deck(tmp_path, "10001")

        assert loaded == deck

    def test_raises_naming_the_missing_path_instead_of_falling_back(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(RuntimeError, match=str(tmp_path / "10001" / "deck.json")):
            _load_generated_flashcard_deck(tmp_path, "10001")


class TestWriteFlashcardsFlagRouting:
    """D4/D-10 regression: the opt-in flag routes flashcards.{md,csv,typ}
    through the generated deck when supplied, and the deterministic
    builder still produces all three when it is absent -- matching
    TestBundleDocumentsInferenceSplice's own flag-present/flag-absent
    coverage for simulado/podcast_roteiro."""

    def test_flag_absent_renders_from_the_deterministic_bundle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bundle = _bundle()
        calls: dict[str, object] = {}
        monkeypatch.setattr(
            _GENERATOR_MODULE,
            "_compile_typst_atomically",
            lambda *_args, **_kwargs: calls.setdefault("compiled", True),
        )

        _write_flashcards(tmp_path, bundle, Path("/trusted/typst"), {})

        markdown = (tmp_path / "flashcards.md").read_text(encoding="utf-8")
        assert markdown == render_flashcards_markdown(bundle)
        csv_text = (tmp_path / "flashcards.csv").read_text(encoding="utf-8")
        assert csv_text == render_flashcards_csv(bundle)
        assert calls.get("compiled") is True

    def test_flag_present_renders_from_the_generated_deck(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bundle = _bundle()
        deck = _make_generated_flashcard_deck()
        monkeypatch.setattr(
            _GENERATOR_MODULE,
            "_compile_typst_atomically",
            lambda *_args, **_kwargs: None,
        )

        _write_flashcards(tmp_path, bundle, Path("/trusted/typst"), {}, deck=deck)

        markdown = (tmp_path / "flashcards.md").read_text(encoding="utf-8")
        assert markdown == render_generated_flashcards_markdown(deck)
        assert markdown != render_flashcards_markdown(bundle)
        with (tmp_path / "flashcards.csv").open(encoding="utf-8", newline="") as handle:
            csv_text = handle.read()
        assert csv_text == render_generated_flashcards_csv(deck)
        typst_text = (tmp_path / "flashcards.typ").read_text(encoding="utf-8")
        assert typst_text == render_printable_generated_flashcards_typst(deck)


class TestLoadFigureCatalog:
    def test_loads_and_validates_the_figure_catalog_json(self, tmp_path: Path) -> None:
        catalog = _make_figure_catalog(lecture_id="10001")
        lecture_dir = tmp_path / "10001"
        lecture_dir.mkdir()
        (lecture_dir / "figures.json").write_text(
            catalog.model_dump_json(), encoding="utf-8"
        )

        loaded = _load_figure_catalog(tmp_path, "10001")

        assert loaded == catalog

    def test_raises_naming_the_missing_path_instead_of_falling_back(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(
            RuntimeError, match=str(tmp_path / "10001" / "figures.json")
        ):
            _load_figure_catalog(tmp_path, "10001")


class TestWriteFigures:
    """T-01-03/T-01-21 regression: every staged image path is produced only
    when a catalog is supplied, each image passes the existing safe-path
    assertion and symlink-component rejection, and the returned relative
    paths match what was actually staged. (2026-09-15: the figure atlas
    document this used to also stage was removed -- figures are embedded
    inline in the complete edition instead, see critical_review_document's
    docstring; only the image-copy staging remains here.)"""

    def test_copies_every_frame_image(self, tmp_path: Path) -> None:
        catalog = _make_figure_catalog(
            lecture_id="10001",
            frame_filenames=("frame_0600.000.png", "frame_1200.000.png"),
        )
        extracted_slides_root = tmp_path / "extracted"
        lecture_frames_dir = extracted_slides_root / "10001"
        lecture_frames_dir.mkdir(parents=True)
        for figure in catalog.figures:
            (lecture_frames_dir / figure.frame_filename).write_bytes(b"frame-bytes")
        output_dir = tmp_path / "staged"
        output_dir.mkdir()

        staged_paths = _write_figures(
            output_dir, catalog, extracted_slides_root, Path("/trusted/ffmpeg"), {}
        )

        assert set(staged_paths) == {
            f"{BUNDLE_FIGURES_SUBDIRECTORY}/frame_0600.000.png",
            f"{BUNDLE_FIGURES_SUBDIRECTORY}/frame_1200.000.png",
        }
        for relative_path in staged_paths:
            assert (output_dir / relative_path).read_bytes() == b"frame-bytes"

    def test_raises_naming_a_missing_source_frame(self, tmp_path: Path) -> None:
        catalog = _make_figure_catalog(lecture_id="10001")
        extracted_slides_root = tmp_path / "extracted"
        output_dir = tmp_path / "staged"
        output_dir.mkdir()

        with pytest.raises(
            RuntimeError,
            match=str(extracted_slides_root / "10001" / "frame_0600.000.png"),
        ):
            _write_figures(
                output_dir, catalog, extracted_slides_root, Path("/trusted/ffmpeg"), {}
            )

    def test_rejects_an_absolute_frame_filename_via_the_safe_path_assertion(
        self, tmp_path: Path
    ) -> None:
        # An absolute frame_filename concatenated into
        # f"{BUNDLE_FIGURES_SUBDIRECTORY}/{frame_filename}" produces a
        # relative-looking, non-".."-bearing string ("figuras//etc/passwd"),
        # so only Path.__truediv__'s absolute-path-resets-the-join behavior
        # at the actual filesystem write catches it -- exactly what the
        # staging step's safe-path assertion exists to catch.
        catalog = LectureFigureCatalog(
            lecture_id="10001",
            captioned_at=1_726_000_000.0,
            figures=[
                FigureCaption(
                    lecture_id="10001",
                    frame_filename="/etc/passwd",
                    timestamp_seconds=600.0,
                    caption="Legenda.",
                )
            ],
        )
        extracted_slides_root = tmp_path / "extracted"
        output_dir = tmp_path / "staged"
        output_dir.mkdir()

        with pytest.raises(RuntimeError, match="fora da raiz de materiais"):
            _write_figures(
                output_dir, catalog, extracted_slides_root, Path("/trusted/ffmpeg"), {}
            )

    def test_crops_every_frame_when_a_crop_rect_is_given(self, tmp_path: Path) -> None:
        """2026-09-15 document review: a real crop, run through the actual
        ffmpeg on disk (not a stub), to catch a filter-syntax mistake that
        a mocked subprocess call would hide."""
        ffmpeg_executable = _resolve_ffmpeg_for_test()
        catalog = _make_figure_catalog(lecture_id="10001")
        extracted_slides_root = tmp_path / "extracted"
        lecture_frames_dir = extracted_slides_root / "10001"
        lecture_frames_dir.mkdir(parents=True)
        source_path = lecture_frames_dir / "frame_0600.000.png"
        _write_solid_png(source_path, width=40, height=20)
        output_dir = tmp_path / "staged"
        output_dir.mkdir()

        staged_paths = _write_figures(
            output_dir,
            catalog,
            extracted_slides_root,
            ffmpeg_executable,
            {},
            figure_crop_rect=_GENERATOR_MODULE.FigureCropRect(
                width=10, height=10, x=5, y=5
            ),
        )

        cropped_path = output_dir / staged_paths[0]
        assert _png_dimensions(cropped_path) == (10, 10)


def _resolve_ffmpeg_for_test() -> Path:
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        pytest.skip("ffmpeg not found on PATH")
    return Path(ffmpeg_path)


def _write_solid_png(path: Path, *, width: int, height: int) -> None:
    ffmpeg_executable = _resolve_ffmpeg_for_test()
    subprocess.run(
        [
            str(ffmpeg_executable),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=white:s={width}x{height}",
            "-frames:v",
            "1",
            "-update",
            "1",
            str(path),
        ],
        check=True,
    )


def _png_dimensions(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[16:24]
    width = int.from_bytes(header[0:4], "big")
    height = int.from_bytes(header[4:8], "big")
    return width, height


class TestParseFigureCropRect:
    def test_parses_the_documented_ffmpeg_crop_syntax(self) -> None:
        rect = _GENERATOR_MODULE._parse_figure_crop_rect("1940:1080:300:0")

        assert rect == _GENERATOR_MODULE.FigureCropRect(
            width=1940, height=1080, x=300, y=0
        )
        assert rect.as_ffmpeg_filter() == "crop=1940:1080:300:0"

    @pytest.mark.parametrize(
        "value",
        [
            "1940:1080:300",
            "1940:1080:300:0:extra",
            "1940:1080:x:0",
            "0:1080:300:0",
            "1940:-1:300:0",
            "1940:1080:-1:0",
        ],
    )
    def test_rejects_malformed_or_out_of_range_values(self, value: str) -> None:
        with pytest.raises(_GENERATOR_MODULE.argparse.ArgumentTypeError):
            _GENERATOR_MODULE._parse_figure_crop_rect(value)

    @pytest.mark.parametrize("value", ["none", "None", "NONE", "  none  "])
    def test_none_opts_out_of_cropping(self, value: str) -> None:
        assert _GENERATOR_MODULE._parse_figure_crop_rect(value) is None


class TestDefaultFigureCropRect:
    """Recording layouts differ between sources, so no crop is applied
    unless --figure-crop-rect is given explicitly."""

    def test_frames_are_uncropped_by_default(self) -> None:
        args = _GENERATOR_MODULE._build_argument_parser().parse_args(["10003"])

        assert args.figure_crop_rect is None

    def test_an_explicit_rectangle_is_parsed(self) -> None:
        args = _GENERATOR_MODULE._build_argument_parser().parse_args(
            ["10003", "--figure-crop-rect", "1940:1080:300:0"]
        )

        assert args.figure_crop_rect == _GENERATOR_MODULE.FigureCropRect(
            width=1940, height=1080, x=300, y=0
        )


class TestValidateStagedBundleFigures:
    def test_expected_set_includes_figure_files_only_when_paths_are_supplied(
        self, tmp_path: Path
    ) -> None:
        for relative_path in _GENERATOR_MODULE._EXPECTED_BUNDLE_FILES:
            if relative_path == _GENERATOR_MODULE._BUNDLE_MANIFEST_FILENAME:
                continue
            file_path = tmp_path / relative_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text("conteudo", encoding="utf-8")
        (tmp_path / "video").mkdir()
        (tmp_path / "video" / "aula.mp4").write_bytes(b"video-content")
        _write_bundle_manifest(tmp_path, "10001")
        video_relative_path = Path("video/aula.mp4")

        _validate_staged_bundle(tmp_path, video_relative_path)

        figures_dir = tmp_path / BUNDLE_FIGURES_SUBDIRECTORY
        figures_dir.mkdir()
        (figures_dir / "frame_0600.000.png").write_bytes(b"frame")

        with pytest.raises(RuntimeError, match="adicionais"):
            _validate_staged_bundle(tmp_path, video_relative_path)

        _validate_staged_bundle(
            tmp_path,
            video_relative_path,
            (f"{BUNDLE_FIGURES_SUBDIRECTORY}/frame_0600.000.png",),
        )


def _stub_compile_typst(
    _executable: Path,
    _source_path: Path,
    output_path: Path,
    _environment: dict[str, str],
) -> None:
    """Stand in for a real Typst compile in the end-to-end test below.
    Every other splice test in this module mocks _compile_typst_atomically
    the same way, because the script hardcodes `--root` to the process cwd
    (correct for the real entry point, which always stages under a
    repo-relative workspace/ path) -- a pytest tmp_path lives outside that
    root, so a real Typst invocation from here would fail on the sandbox
    boundary, not on anything this plan changed."""

    output_path.write_bytes(b"%PDF-1.4\n%stub\n")


def _make_real_podcast_audio(destination: Path) -> None:
    ffmpeg_executable = shutil.which("ffmpeg")
    if ffmpeg_executable is None:
        pytest.skip("ffmpeg is required to synthesize the end-to-end podcast audio")
    subprocess.run(
        [
            ffmpeg_executable,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            "2",
            "-c:a",
            "aac",
            str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


class TestFullBundleWithEveryInferenceArtifact:
    """Task 3 end-to-end proof: a bundle built with the exam, podcast,
    flashcard deck, and figure catalog all present validates and
    publishes, and its index lists the figure document."""

    def test_bundle_validates_publishes_and_indexes_the_figures(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ffprobe_executable = shutil.which("ffprobe")
        if ffprobe_executable is None:
            pytest.skip("ffprobe is required for the end-to-end podcast duration check")
        bundle = _bundle()
        coverage_report = TemporalCoverageReport(
            lecture_id="10001",
            lecture_title="Cardiologia veterinária",
            video_duration_seconds=120.0,
            covered_seconds=120.0,
            source_word_count=10,
            study_word_count=10,
            units=(),
            minutes=(),
            warnings=(),
            status=CoverageStatus.PROTECTED,
        )
        inference_exam = _make_inference_exam(lecture_id="10001")
        inference_podcast_script = _make_inference_podcast_script(lecture_id="10001")
        inference_flashcard_deck = _make_generated_flashcard_deck(lecture_id="10001")
        inference_figure_catalog = _make_figure_catalog(
            lecture_id="10001",
            frame_filenames=("frame_0600.000.png",),
        )

        source_video = tmp_path / "source" / "lecture.mp4"
        source_video.parent.mkdir()
        source_video.write_bytes(b"video-content")
        podcast_audio_root = tmp_path / "podcast-audio"
        (podcast_audio_root / "10001").mkdir(parents=True)
        _make_real_podcast_audio(podcast_audio_root / "10001" / "podcast_final.m4a")
        extracted_slides_root = tmp_path / "extracted"
        (extracted_slides_root / "10001").mkdir(parents=True)
        for figure in inference_figure_catalog.figures:
            (extracted_slides_root / "10001" / figure.frame_filename).write_bytes(
                b"frame-bytes"
            )
        executables = {
            "typst": Path("/trusted/typst"),
            "say": Path("/trusted/say"),
            "ffmpeg": Path("/trusted/ffmpeg"),
            "ffprobe": Path(ffprobe_executable),
        }
        output_root = _prepare_trusted_output_root(tmp_path / "study-guides")
        staging_dir = Path(tempfile.mkdtemp(prefix=".10001.staging.", dir=output_root))
        monkeypatch.setattr(
            _GENERATOR_MODULE,
            "_compile_typst_atomically",
            _stub_compile_typst,
        )

        bundled_video_path, figure_relative_paths = _write_bundle(
            output_dir=staging_dir,
            video_path=source_video,
            bundle=bundle,
            coverage_report=coverage_report,
            executables=executables,
            environment={},
            inference_exam=inference_exam,
            inference_podcast_script=inference_podcast_script,
            inference_flashcard_deck=inference_flashcard_deck,
            inference_figure_catalog=inference_figure_catalog,
            podcast_audio_root=podcast_audio_root,
            extracted_slides_root=extracted_slides_root,
        )

        assert figure_relative_paths == (
            f"{BUNDLE_FIGURES_SUBDIRECTORY}/frame_0600.000.png",
        )
        _write_bundle_manifest(staging_dir, "10001")
        _validate_staged_bundle(staging_dir, bundled_video_path, figure_relative_paths)

        index_text = (staging_dir / "indice.md").read_text(encoding="utf-8")
        assert "1 figura(s)" in index_text

        published = _publish_staged_bundle(
            output_root=output_root,
            lecture_id="10001",
            target_relative_path=Path("2026/1-semestre/disciplina/secao/10001-aula"),
            staging_dir=staging_dir,
        )

        assert (
            published / BUNDLE_FIGURES_SUBDIRECTORY / "frame_0600.000.png"
        ).is_file()
        assert (published / "indice.md").is_file()


class TestApostilaUsesDraftingProseDirectly:
    """Regression guard (2026-09-15): `main()` must build the apostila from
    the drafting stage's own prose, not from `build_ledger_grounded_drafts`,
    which discards that prose and rebuilds `body_markdown` as a bare bullet
    list of raw ledger facts -- the exact defect the operator rejected the
    whole corpus over. Nothing else in this test file exercises `main()`'s
    wiring, so a re-introduction of that call would otherwise go uncaught."""

    def test_main_does_not_import_the_ledger_grounding_function(self):
        assert not hasattr(_GENERATOR_MODULE, "build_ledger_grounded_drafts")

    def test_main_source_never_calls_the_ledger_grounding_function(self):
        source = inspect.getsource(_GENERATOR_MODULE.main)
        assert "build_ledger_grounded_drafts(" not in source
        assert "drafts=drafts" in source


class TestRaiseIfAnyDraftIsDegenerate:
    """Corruption guard (2026-09-15): a near-empty draft next to a
    well-populated ledger is a generation failure, not a legitimately
    short section -- must block, not silently publish."""

    @staticmethod
    def _ledger(pack_sequence: int, fact_count: int) -> SemanticLedger:
        return SemanticLedger(
            lecture_id="10003",
            pack_sequence=pack_sequence,
            facts=[
                SemanticFact(
                    entity_name=f"Achado {index}",
                    category="Anatomia",
                    clinical_context="Contexto clínico.",
                    timestamp_ref=float(index),
                )
                for index in range(fact_count)
            ],
        )

    @staticmethod
    def _draft(pack_sequence: int, body_markdown: str) -> LearningUnitDraft:
        return LearningUnitDraft(
            lecture_id="10003",
            pack_sequence=pack_sequence,
            title="Título",
            body_markdown=body_markdown,
            clinical_warnings=[],
        )

    def test_raises_when_a_well_populated_ledger_pairs_with_a_near_empty_draft(self):
        drafts = (self._draft(1, "Vazio."),)
        ledgers = (self._ledger(1, fact_count=5),)

        with pytest.raises(_GENERATOR_MODULE.DegenerateDraftError, match="pack 1"):
            _GENERATOR_MODULE._raise_if_any_draft_is_degenerate(
                "10003", drafts, ledgers
            )

    def test_does_not_raise_for_a_short_draft_from_a_sparse_ledger(self):
        drafts = (self._draft(1, "Introdução sem conteúdo clínico relevante."),)
        ledgers = (self._ledger(1, fact_count=0),)

        _GENERATOR_MODULE._raise_if_any_draft_is_degenerate("10003", drafts, ledgers)

    def test_does_not_raise_for_a_substantial_draft_from_a_rich_ledger(self):
        drafts = (self._draft(1, " ".join(["palavra"] * 200)),)
        ledgers = (self._ledger(1, fact_count=30),)

        _GENERATOR_MODULE._raise_if_any_draft_is_degenerate("10003", drafts, ledgers)


def _report_with_warnings(count: int) -> Any:
    return SimpleNamespace(warnings=tuple(f"Aviso {index}." for index in range(count)))


def test_coverage_notice_section_is_empty_without_warnings() -> None:
    assert _GENERATOR_MODULE._coverage_notice_section(_report_with_warnings(0)) == ""


def test_coverage_notice_section_lists_warnings_and_caps_the_noise() -> None:
    limit = _GENERATOR_MODULE.MAXIMUM_INDEX_COVERAGE_NOTICES

    section = _GENERATOR_MODULE._coverage_notice_section(
        _report_with_warnings(limit + 3)
    )

    assert section.startswith("\n## Avisos de cobertura\n\n- Aviso 0.\n")
    assert f"- Aviso {limit - 1}." in section
    assert f"- Aviso {limit}." not in section
    assert section.endswith("- Mais 3 aviso(s) no mapa temporal.\n")


class TestLoadChapterPlan:
    """--chapter-plan reads a YAML list of {title, first_unit, last_unit}
    entries and rejects any plan the compiler would render ambiguously."""

    @staticmethod
    def _write_plan(tmp_path: Path, content: str) -> str:
        plan_path = tmp_path / "chapters.yaml"
        plan_path.write_text(content, encoding="utf-8")
        return str(plan_path)

    def test_parses_entries_into_ordered_chapter_tuples(self, tmp_path: Path) -> None:
        plan_path = self._write_plan(
            tmp_path,
            "- title: '  Biópsias incisionais '\n"
            "  first_unit: 1\n"
            "  last_unit: 3\n"
            "- title: Biópsias excisionais\n"
            "  first_unit: 4\n"
            "  last_unit: 4\n",
        )

        plan = _GENERATOR_MODULE._load_chapter_plan(plan_path)

        assert plan == (
            ("Biópsias incisionais", 1, 3),
            ("Biópsias excisionais", 4, 4),
        )

    @pytest.mark.parametrize(
        ("content", "message"),
        [
            ("[]\n", "non-empty YAML list"),
            ("title: Capítulo\n", "non-empty YAML list"),
            ("- Capítulo\n", "entry 1: expected a mapping"),
            (
                "- {title: Capítulo, first_unit: 1}\n",
                "entry 1: expected exactly the keys",
            ),
            (
                "- {title: Capítulo, first_unit: 1, last_unit: 2, extra: 3}\n",
                "entry 1: expected exactly the keys",
            ),
            ("- {title: '  ', first_unit: 1, last_unit: 2}\n", "entry 1: title"),
            ("- {title: 7, first_unit: 1, last_unit: 2}\n", "entry 1: title"),
            ("- {title: A, first_unit: 0, last_unit: 2}\n", "entry 1: first_unit"),
            ("- {title: A, first_unit: '1', last_unit: 2}\n", "entry 1: first_unit"),
            ("- {title: A, first_unit: true, last_unit: 2}\n", "entry 1: first_unit"),
            ("- {title: A, first_unit: 1, last_unit: 1.5}\n", "entry 1: last_unit"),
            (
                "- {title: A, first_unit: 3, last_unit: 2}\n",
                "entry 1: first_unit 3 is greater than last_unit 2",
            ),
            (
                "- {title: A, first_unit: 1, last_unit: 3}\n"
                "- {title: B, first_unit: 3, last_unit: 5}\n",
                "entries 1 and 2 overlap",
            ),
            (
                "- {title: B, first_unit: 4, last_unit: 9}\n"
                "- {title: A, first_unit: 1, last_unit: 5}\n",
                "entries 2 and 1 overlap",
            ),
            ("- {title: A, first_unit: [1\n", "cannot read chapter plan"),
        ],
    )
    def test_rejects_an_invalid_plan_with_a_clear_message(
        self, tmp_path: Path, content: str, message: str
    ) -> None:
        plan_path = self._write_plan(tmp_path, content)

        with pytest.raises(
            _GENERATOR_MODULE.argparse.ArgumentTypeError, match=re.escape(message)
        ):
            _GENERATOR_MODULE._load_chapter_plan(plan_path)

    def test_rejects_a_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(
            _GENERATOR_MODULE.argparse.ArgumentTypeError,
            match="cannot read chapter plan",
        ):
            _GENERATOR_MODULE._load_chapter_plan(str(tmp_path / "absent.yaml"))

    def test_defaults_to_numbered_parts_when_the_flag_is_absent(self) -> None:
        args = _GENERATOR_MODULE._build_argument_parser().parse_args(["10003"])

        assert args.chapter_plan == ()

    def test_the_flag_loads_the_plan_file(self, tmp_path: Path) -> None:
        plan_path = self._write_plan(
            tmp_path, "- {title: Biópsias, first_unit: 1, last_unit: 2}\n"
        )

        args = _GENERATOR_MODULE._build_argument_parser().parse_args(
            ["10003", "--chapter-plan", plan_path]
        )

        assert args.chapter_plan == (("Biópsias", 1, 2),)


class _EditionsRequested(Exception):
    """Stops `main()` once it asks the compiler for editions."""


class TestMainPassesTheChapterPlanToTheCompiler:
    def test_main_forwards_the_parsed_plan(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        plan_path = tmp_path / "chapters.yaml"
        plan_path.write_text(
            "- {title: Biópsias, first_unit: 1, last_unit: 2}\n", encoding="utf-8"
        )
        _stub_main_inputs(monkeypatch, tmp_path)
        requested: dict[str, Any] = {}

        def capture_editions(**kwargs: Any) -> None:
            requested.update(kwargs)
            raise _EditionsRequested

        monkeypatch.setattr(
            _GENERATOR_MODULE, "build_personal_study_editions", capture_editions
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "generate_personal_study_guides.py",
                "10003",
                "--chapter-plan",
                str(plan_path),
            ],
        )

        with pytest.raises(_EditionsRequested):
            _GENERATOR_MODULE.main()

        assert requested["lecture_id"] == "10003"
        assert requested["chapter_plan"] == (("Biópsias", 1, 2),)


def _stub_main_inputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Replace every input `main()` reads before compiling editions."""

    stubs: dict[str, Callable[..., Any]] = {
        "load_academic_catalog": lambda path: None,
        "load_academic_schedule": lambda path: None,
        "load_local_lectures": lambda path: [],
        "merge_local_lectures": lambda catalog, schedule, lectures: (catalog, schedule),
        "unpublished_lecture_ids": lambda lectures: frozenset(),
        "video_only_lectures": lambda lectures: {},
        "academic_lecture_paths": lambda catalog, schedule, lecture_ids: {},
        "_prepare_trusted_output_root": lambda output_root: tmp_path,
        "_load_lecture_titles": lambda queue_path: {"10003": "Aula"},
        "sanitized_child_process_environment": lambda: {},
        "_resolve_executables": lambda environment: {},
        "_resolve_video_path": lambda root, lecture_id: tmp_path / "video.mp4",
        "_load_evidence_packs": lambda root, lecture_id: (),
        "_load_drafts": lambda root, lecture_id: (),
        "_load_semantic_ledgers": lambda root, lecture_id: (),
        "_raise_if_any_draft_is_degenerate": lambda lecture_id, drafts, ledgers: None,
    }
    for name, stub in stubs.items():
        monkeypatch.setattr(_GENERATOR_MODULE, name, stub)

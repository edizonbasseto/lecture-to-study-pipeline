#!/usr/bin/env python3
"""Generate complete pt-BR personal study bundles for local lectures.

The apostila is built only from critic-approved units (ADR 0045): the unit
prose comes from the post-repair content of
`workspace/data/09_validated_units/<lecture>/unit_*.json`, never from the
Phase 6 `08_drafts` text. A lecture with any unit whose critic review did
not pass, any Phase 6 draft the critic never reviewed, or a gap in its pack
sequence is blocked as a whole: the run aborts before any of that lecture's
bundle is staged or published.
"""

from __future__ import annotations

import argparse
import errno
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import math
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Final, Iterator, Mapping

from pydantic import TypeAdapter
import yaml

from lecture_study.assembly.assembler import load_critic_approved_drafts
from lecture_study.captioning.catalog_store import (
    EXTRACTED_SLIDES_INPUT_DIR,
    FIGURE_CAPTIONS_OUTPUT_DIR,
    read_figure_catalog,
)
from lecture_study.domain.models import (
    EvidencePack,
    LearningUnitDraft,
    LectureFigureCatalog,
    LectureMetadata,
    MockExam,
    PersonalStudyFlashcardDeck,
    PodcastScript,
    SafeIdentifier,
    SemanticLedger,
)
from lecture_study.ops.process_environment import (
    resolve_trusted_executable,
    sanitized_child_process_environment,
)
from lecture_study.ops.process_lifecycle import run_bounded_process
from lecture_study.study_materials.academic_catalog import (
    ACADEMIC_BUNDLE_DOCUMENT_FILES,
    BUNDLE_FIGURES_SUBDIRECTORY,
    AcademicBundleIdentityManifest,
    academic_lecture_paths,
    load_academic_catalog,
    load_academic_schedule,
    read_academic_bundle_identity,
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
    render_printable_flashcards_typst,
    render_printable_generated_flashcards_typst,
    render_study_document_markdown,
    render_study_document_typst,
    review_plan_document,
)
from lecture_study.study_materials.local_lectures import (
    LOCAL_LECTURES_PATH,
    PRIVATE_STUDY_GUIDES_ROOT,
    LocalLecture,
    load_local_lectures,
    merge_local_lectures,
    select_output_root,
    unpublished_lecture_ids,
    video_only_lectures,
)
from lecture_study.study_materials.video_only_bundle import (
    expected_video_only_files,
    render_video_only_index,
)
from lecture_study.study_materials.compiler import (
    ChapterPlan,
    SelectedFigure,
    StudyEdition,
    StudyEditionKind,
    build_personal_study_editions,
    render_study_edition_markdown,
    render_study_edition_typst,
)
from lecture_study.study_materials.temporal_coverage import (
    TemporalCoverageReport,
    build_temporal_coverage_report,
    render_temporal_coverage_csv,
    temporal_coverage_document,
)

MAXIMUM_INDEX_COVERAGE_NOTICES = 10
DEFAULT_QUEUE_PATH = Path("workspace/data/queue.json")
DEFAULT_VIDEOS_ROOT = Path("workspace/data/01_raw")
DEFAULT_EVIDENCE_PACKS_ROOT = Path("workspace/data/06_evidence_packs")
DEFAULT_LEDGERS_ROOT = Path("workspace/data/07_semantic_ledgers")
DEFAULT_DRAFTS_ROOT = Path("workspace/data/08_drafts")
DEFAULT_VALIDATED_UNITS_ROOT = Path("workspace/data/09_validated_units")
DEFAULT_OUTPUT_ROOT = Path("workspace/study-guides")
# Same directory ExamCompiler writes to (src/lecture_study/exams/compiler.py's
# EXAMS_OUTPUT_DIR) -- the opt-in inference splice reads exam.json from here.
DEFAULT_MOCK_EXAMS_ROOT = Path("workspace/data/14_mock_exams")
# Same directory PodcastScriptCompiler writes to (src/lecture_study/podcast/
# compiler.py's PODCAST_SCRIPTS_OUTPUT_DIR) -- the opt-in inference splice
# reads script.json from here.
DEFAULT_PODCAST_SCRIPTS_ROOT = Path("workspace/data/15_podcast_scripts")
# Same directory PodcastAudioGenerator writes to (src/lecture_study/tts/
# generator.py's AUDIO_OUTPUT_DIR) -- the opt-in inference splice copies
# podcast_final.m4a from here into the staged bundle.
DEFAULT_PODCAST_AUDIO_ROOT = Path("workspace/data/16_podcast_audio")
# Same directory the Codex flashcard script's DEFAULT_OUTPUT_ROOT points
# to (scripts/generate_codex_personal_study_flashcards.py, ADR 0038) --
# the opt-in inference splice reads deck.json from here.
DEFAULT_PERSONAL_STUDY_FLASHCARDS_ROOT = Path(
    "workspace/data/18_personal_study_flashcards"
)
# Same directory captioning/catalog_store.py's write_figure_catalog writes to
# (FIGURE_CAPTIONS_OUTPUT_DIR) -- the opt-in inference splice reads
# figures.json from here.
DEFAULT_FIGURE_CAPTIONS_ROOT = FIGURE_CAPTIONS_OUTPUT_DIR
# Same directory extraction/media_workers.py writes each lecture's extracted
# frames to (EXTRACTED_SLIDES_INPUT_DIR) -- the opt-in inference splice
# copies the captioned frames' own image files from here into the staged
# bundle's figure subdirectory.
DEFAULT_EXTRACTED_SLIDES_ROOT = EXTRACTED_SLIDES_INPUT_DIR


@dataclass(frozen=True, slots=True)
class FigureCropRect:
    """A crop rectangle (ffmpeg's own crop=W:H:X:Y syntax) applied to every
    embedded figure frame before staging, to remove recording chrome --
    a webcam thumbnail, pillarbox bars, a timestamp overlay -- outside the
    actual slide content (2026-09-15 document review).

    Example layout: a 2560x1080 recording whose slide sits between a
    webcam pillarbox on each side is cropped to its slide area with
    1940:1080:300:0. No crop is applied unless --figure-crop-rect is
    given; "none" states the uncropped default explicitly."""

    width: int
    height: int
    x: int
    y: int

    def as_ffmpeg_filter(self) -> str:
        return f"crop={self.width}:{self.height}:{self.x}:{self.y}"


def _parse_figure_crop_rect(value: str) -> FigureCropRect | None:
    if value.strip().lower() == "none":
        return None
    parts = value.split(":")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            "figure crop rect must be 'WIDTH:HEIGHT:X:Y', e.g. '1940:1080:300:0'"
        )
    try:
        width, height, x, y = (int(part) for part in parts)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "figure crop rect values must be integers"
        ) from error
    if width <= 0 or height <= 0 or x < 0 or y < 0:
        raise argparse.ArgumentTypeError(
            "figure crop rect width/height must be positive and x/y non-negative"
        )
    return FigureCropRect(width=width, height=height, x=x, y=y)


_CHAPTER_PLAN_KEYS: Final = frozenset({"title", "first_unit", "last_unit"})


def _load_chapter_plan(raw_path: str) -> ChapterPlan:
    """Read --chapter-plan: a YAML list of {title, first_unit, last_unit}
    entries naming each chapter and its inclusive range of learning-unit
    sequences. Units outside every range go to the compiler's trailing
    complementary chapter."""

    plan_path = Path(raw_path)
    try:
        entries = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise argparse.ArgumentTypeError(
            f"cannot read chapter plan {plan_path}: {error}"
        ) from error
    return _validated_chapter_plan(entries)


def _validated_chapter_plan(entries: object) -> ChapterPlan:
    if not isinstance(entries, list) or not entries:
        raise argparse.ArgumentTypeError(
            "chapter plan must be a non-empty YAML list of "
            "{title, first_unit, last_unit} entries"
        )
    plan = tuple(
        _chapter_from_entry(position, entry)
        for position, entry in enumerate(entries, start=1)
    )
    _require_disjoint_chapter_ranges(plan)
    return plan


def _chapter_from_entry(position: int, entry: object) -> tuple[str, int, int]:
    if not isinstance(entry, dict):
        raise argparse.ArgumentTypeError(
            f"chapter plan entry {position}: expected a mapping, got {entry!r}"
        )
    if set(entry) != _CHAPTER_PLAN_KEYS:
        raise argparse.ArgumentTypeError(
            f"chapter plan entry {position}: expected exactly the keys "
            f"{sorted(_CHAPTER_PLAN_KEYS)}, got {sorted(map(str, entry))}"
        )
    title = entry["title"]
    if not isinstance(title, str) or not title.strip():
        raise argparse.ArgumentTypeError(
            f"chapter plan entry {position}: title must be a non-empty string"
        )
    first_unit = _positive_unit(position, "first_unit", entry["first_unit"])
    last_unit = _positive_unit(position, "last_unit", entry["last_unit"])
    if first_unit > last_unit:
        raise argparse.ArgumentTypeError(
            f"chapter plan entry {position}: first_unit {first_unit} is "
            f"greater than last_unit {last_unit}"
        )
    return title.strip(), first_unit, last_unit


def _positive_unit(position: int, key: str, value: object) -> int:
    # bool is an int subclass, so YAML's true/false must be rejected explicitly.
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise argparse.ArgumentTypeError(
            f"chapter plan entry {position}: {key} must be a positive integer, "
            f"got {value!r}"
        )
    return value


def _require_disjoint_chapter_ranges(plan: ChapterPlan) -> None:
    by_first_unit = sorted(
        enumerate(plan, start=1), key=lambda numbered: numbered[1][1]
    )
    for (previous_position, previous), (position, current) in zip(
        by_first_unit, by_first_unit[1:]
    ):
        if current[1] <= previous[2]:
            raise argparse.ArgumentTypeError(
                f"chapter plan entries {previous_position} and {position} "
                f"overlap: units {previous[1]}-{previous[2]} and "
                f"{current[1]}-{current[2]}"
            )


_PODCAST_VOICE = "Luciana"
_PODCAST_FILENAME = "podcast_revisao.m4a"
_SUBPROCESS_TIMEOUT_SECONDS = 1_800
_MAXIMUM_PROCESS_OUTPUT_BYTES = 64 * 1024
_MINIMUM_PODCAST_SECONDS_PER_WORD: Final = 0.12
_BUNDLE_MANIFEST_FILENAME = ".bundle.json"
# ACADEMIC_BUNDLE_DOCUMENT_FILES stays frozen to the legacy archived-bundle
# shape (see its own docstring) -- apostila_resumida.* is subtracted here
# because a freshly generated bundle no longer writes it: resumo_critico now
# carries the merged content (2026-09-15 operator decision, see
# critical_review_document's docstring), so a standalone apostila_resumida
# would be exact duplication of resumo_critico again.
_APOSTILA_RESUMIDA_LEGACY_FILES: Final = frozenset(
    {"apostila_resumida.md", "apostila_resumida.pdf", "apostila_resumida.typ"}
)
_EXPECTED_BUNDLE_FILES = (
    ACADEMIC_BUNDLE_DOCUMENT_FILES - _APOSTILA_RESUMIDA_LEGACY_FILES
) | {_BUNDLE_MANIFEST_FILENAME}
_LECTURE_QUEUE_ADAPTER: TypeAdapter[list[LectureMetadata]] = TypeAdapter(
    list[LectureMetadata]
)
_SAFE_IDENTIFIER_ADAPTER: TypeAdapter[SafeIdentifier] = TypeAdapter(SafeIdentifier)


def _load_inference_mock_exam(root: Path, lecture_id: str) -> MockExam:
    """Load one lecture's inference-generated MockExam from ExamCompiler's
    exam.json (ADR 0024's uncertified personal-study route). Fails naming
    the missing path rather than falling back to the deterministic
    template -- an operator who asked for the inference splice must learn
    the artifact is missing, not silently receive the template instead."""

    canonical_id = _SAFE_IDENTIFIER_ADAPTER.validate_python(lecture_id, strict=True)
    exam_path = root / canonical_id / "exam.json"
    if not exam_path.is_file():
        raise RuntimeError(f"simulado gerado por inferência ausente: {exam_path}")
    return MockExam.model_validate_json(exam_path.read_text(encoding="utf-8"))


def _load_inference_podcast_script(root: Path, lecture_id: str) -> PodcastScript:
    """Load one lecture's inference-generated PodcastScript from
    PodcastScriptCompiler's script.json (ADR 0024's uncertified personal-
    study route). Fails naming the missing path rather than falling back
    to the deterministic template -- an operator who asked for the
    inference splice must learn the artifact is missing, not silently
    receive the template instead."""

    canonical_id = _SAFE_IDENTIFIER_ADAPTER.validate_python(lecture_id, strict=True)
    script_path = root / canonical_id / "script.json"
    if not script_path.is_file():
        raise RuntimeError(
            f"roteiro de podcast gerado por inferência ausente: {script_path}"
        )
    return PodcastScript.model_validate_json(script_path.read_text(encoding="utf-8"))


def _load_generated_flashcard_deck(
    root: Path, lecture_id: str
) -> PersonalStudyFlashcardDeck:
    """Load one lecture's generated flashcard deck (ADR 0024's uncertified
    personal-study route) from PersonalStudyFlashcardGenerator's
    deck.json. Fails naming the missing path rather than falling back to
    the deterministic builder -- an operator who asked for the inference
    splice must learn the artifact is missing, not silently receive the
    deterministic deck instead."""

    canonical_id = _SAFE_IDENTIFIER_ADAPTER.validate_python(lecture_id, strict=True)
    deck_path = root / canonical_id / "deck.json"
    if not deck_path.is_file():
        raise RuntimeError(
            f"deck de flashcards gerado por inferência ausente: {deck_path}"
        )
    return PersonalStudyFlashcardDeck.model_validate_json(
        deck_path.read_text(encoding="utf-8")
    )


def _load_figure_catalog(root: Path, lecture_id: str) -> LectureFigureCatalog:
    """Load one lecture's figure caption catalog (ADR 0024's uncertified
    personal-study route), produced by scripts/caption_lecture_figures.py.
    Fails naming the missing path rather than silently building a bundle
    without figures -- an operator who asked for the inference splice
    must learn the catalog is missing, not silently receive a bundle
    without figures instead."""

    canonical_id = _SAFE_IDENTIFIER_ADAPTER.validate_python(lecture_id, strict=True)
    catalog_path = root / canonical_id / "figures.json"
    if not catalog_path.is_file():
        raise RuntimeError(f"catálogo de legendas de figuras ausente: {catalog_path}")
    return read_figure_catalog(canonical_id, output_root=root)


# Corruption/degeneration guard, not a content-length target: the operator
# explicitly rejected word-count targets and ceilings on drafting output
# (2026-09-15) -- these two constants exist only to catch a draft that came
# back broken (near-empty) despite its own ledger having real extracted
# content, never to shape or cap a draft that is simply concise because its
# pack's evidence was thin.
_DEGENERATE_DRAFT_WORD_CEILING: Final = 20
_MEANINGFUL_LEDGER_FACT_FLOOR: Final = 3


class DegenerateDraftError(RuntimeError):
    """A draft's body is near-empty despite its ledger carrying enough
    facts to support real content -- almost certainly a generation
    failure, not a legitimately short section."""


def _raise_if_any_draft_is_degenerate(
    lecture_id: str,
    drafts: tuple[LearningUnitDraft, ...],
    ledgers: tuple[SemanticLedger, ...],
) -> None:
    ledgers_by_sequence = {ledger.pack_sequence: ledger for ledger in ledgers}
    for draft in drafts:
        ledger = ledgers_by_sequence.get(draft.pack_sequence)
        if ledger is None:
            continue
        word_count = len(draft.body_markdown.split())
        has_enough_facts = len(ledger.facts) >= _MEANINGFUL_LEDGER_FACT_FLOOR
        if has_enough_facts and word_count < _DEGENERATE_DRAFT_WORD_CEILING:
            raise DegenerateDraftError(
                f"aula {lecture_id!r} pack {draft.pack_sequence}: rascunho "
                f"com {word_count} palavra(s), mas o ledger tem "
                f"{len(ledger.facts)} fato(s) extraído(s) -- a geração "
                "provavelmente degenerou; reprocesse este pack antes de "
                "publicar"
            )


# Figures embedded in the complete apostila (2026-09-15): the operator
# rejected a separate figures-only atlas as not making sense -- a screenshot
# belongs next to the text that explains it.
#
# One figure per unit, not more: the same lecture slide stays on screen for
# minutes while the lecturer talks over it, and every extracted frame gets
# captioned regardless of content -- so a pack's candidate frames are
# mostly repeated captures of the same one slide with slightly different
# caption wording each time (confirmed empirically: neither a caption-text
# fingerprint nor a fixed-length prefix reliably deduplicated them, since
# the vision model does not caption two frames of a static slide
# identically). Picking exactly one candidate per unit sidesteps the
# problem entirely instead of trying to detect duplicates.
#
# _JUNK_FIGURE_CAPTION_MARKERS is a mechanical selection heuristic, not a
# quality judgment: skip frames whose caption itself says there is nothing
# to see (a black transition frame, a bare timestamp overlay, an empty
# slide) -- the only signal available to tell a real slide from a junk one
# without another vision-model pass.
_JUNK_FIGURE_CAPTION_MARKERS: Final = (
    "tela preta",
    "imagem preta",
    "fundo preto",
    "sem conteúdo",
    "sem informação visual",
    "não há conteúdo",
    "nenhuma informação",
    "tela vazia",
    "tela em branco",
    "webcam",
    "câmera do apresentador",
    "câmera da apresentadora",
    "tela de espera",
    "transição",
)
_FIGURE_CAPTION_BOILERPLATE_SENTENCE_MARKERS: Final = (
    "data e hora",
    "janela de vídeo",
    "pequena janela",
    "canto superior direito, há uma",
    "webcam",
)


_FRAME_HASH_PIXELS = 32
_FRAME_HASH_HAMMING_LIMIT = 10


def _figure_signature(caption: str) -> str:
    """Near-duplicate captions (the same slide captured repeatedly while a
    ticking on-screen timestamp makes every frame 'new') share a
    signature: digits normalized away, whitespace collapsed, first 100
    characters."""
    normalized = re.sub(r"\d+", "#", caption.casefold())
    return " ".join(normalized.split())[:100]


def _frame_pixel_signature(
    frame_filename: str,
    *,
    lecture_id: str,
    slides_root: Path,
    ffmpeg_executable: Path,
    environment: dict[str, str],
) -> int | None:
    """992-bit horizontal-gradient (dHash) signature of the frame
    downscaled to 32x32 grayscale: one bit per pixel pair, set when the
    left pixel is brighter than its right neighbor.

    Scale/threshold evidence, measured on lectures 10004 and 10003:
    static-slide recaptures (a looping intro slide, a title slide
    re-shown minutes later) sit at distance 0-10 -- the ticking on-screen
    timestamp corner lands in a handful of gradient bits; genuinely
    different slides sit at 12+ even in the hardest case (divider slides
    that differ by ONE word: 'Tireoide' vs 'Adrenais', 12) and at 62-135
    for same-template content series. Average hashing cannot make this
    separation at any scale (those one-word dividers hashed at distance
    0-1). Captions alone cannot either -- the vision model paraphrases
    one looping intro slide into three wording families. Divider slides
    carrying a LIVE person move between recaptures (distance 32+), so
    pixels deliberately do NOT merge them; their near-identical captions
    make the caption-signature layer catch those repeats instead.
    Returns None on any subprocess/file failure so a hashing problem can
    never suppress a figure: an unhashable frame is treated as unique."""
    frame_path = slides_root / lecture_id / frame_filename
    if not frame_path.is_file():
        return None
    pixels = _downscale_to_gray_bytes(
        frame_path, ffmpeg_executable=ffmpeg_executable, environment=environment
    )
    size = _FRAME_HASH_PIXELS
    expected = size * size
    if pixels is None or len(pixels) != expected:
        return None
    signature = 0
    bit_index = 0
    for row in range(size):
        for column in range(size - 1):
            if pixels[row * size + column] > pixels[row * size + column + 1]:
                signature |= 1 << bit_index
            bit_index += 1
    return signature


def _downscale_to_gray_bytes(
    frame_path: Path,
    *,
    ffmpeg_executable: Path,
    environment: dict[str, str],
) -> bytes | None:
    """The 1024 raw grayscale bytes of the frame at 32x32, via ffmpeg
    (run_bounded_process decodes stdout as text, so the bytes go through
    a scratch file, never the pipe)."""
    try:
        with tempfile.TemporaryDirectory(prefix="frame-hash-") as raw_dir:
            raw_path = Path(raw_dir) / "gray.raw"
            run_bounded_process(
                [
                    str(ffmpeg_executable),
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(frame_path),
                    "-vf",
                    f"scale={_FRAME_HASH_PIXELS}:{_FRAME_HASH_PIXELS}",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "gray",
                    str(raw_path),
                ],
                env=environment,
                timeout_seconds=30,
                maximum_stdout_bytes=1024,
                maximum_stderr_bytes=1024,
                check=True,
            )
            return raw_path.read_bytes()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # SubprocessError covers CalledProcessError/TimeoutExpired (a
        # zero-byte frame exits rc=183 with under-1KB stderr); RuntimeError
        # covers run_bounded_process's own ProcessOutputLimitExceeded and
        # pipe-reader errors. One corrupt frame must degrade to an
        # unhashable frame, never abort the whole lecture's bundle.
        return None


def _hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _select_figures_by_pack_sequence(
    packs: tuple[EvidencePack, ...],
    catalog: LectureFigureCatalog,
    *,
    slides_root: Path,
    ffmpeg_executable: Path,
    environment: dict[str, str],
) -> dict[int, tuple[SelectedFigure, ...]]:
    selection: dict[int, tuple[SelectedFigure, ...]] = {}
    used_signatures: set[str] = set()
    used_pixel_hashes: list[int] = []
    pixel_cache: dict[str, int | None] = {}

    def cached_pixel_hash(frame_filename: str) -> int | None:
        if frame_filename not in pixel_cache:
            pixel_cache[frame_filename] = _frame_pixel_signature(
                frame_filename,
                lecture_id=catalog.lecture_id,
                slides_root=slides_root,
                ffmpeg_executable=ffmpeg_executable,
                environment=environment,
            )
        return pixel_cache[frame_filename]

    for pack in packs:
        candidates = [
            figure
            for figure in catalog.figures
            if pack.start_time <= figure.timestamp_seconds <= pack.end_time
            and not _is_junk_figure_caption(figure.caption)
        ]
        # A slide may win only once per LECTURE, not once per window: some
        # recordings loop an intro/pause slide for minutes, and its
        # caption is long (it names every piece of chrome), so
        # per-window max-by-length made it many units' figure (real
        # defect, lecture 10004: 46 near-identical intro frames winning 7
        # of 15 windows). Dedup is by frame PIXELS first (captions
        # paraphrase the same slide), by caption signature second. Greedy
        # in pack order; a window whose candidates are all spent ships no
        # figure rather than a repeat.
        fresh = []
        for figure in candidates:
            if _figure_signature(figure.caption) in used_signatures:
                continue
            pixel_hash = cached_pixel_hash(figure.frame_filename)
            if pixel_hash is not None and any(
                _hamming_distance(pixel_hash, used) <= _FRAME_HASH_HAMMING_LIMIT
                for used in used_pixel_hashes
            ):
                continue
            fresh.append(figure)
        best = max(fresh, key=lambda figure: len(figure.caption), default=None)
        if best is not None:
            used_signatures.add(_figure_signature(best.caption))
            best_hash = cached_pixel_hash(best.frame_filename)
            if best_hash is not None:
                used_pixel_hashes.append(best_hash)
        selection[pack.pack_sequence] = (
            (
                SelectedFigure(
                    relative_path=f"{BUNDLE_FIGURES_SUBDIRECTORY}/{best.frame_filename}",
                    caption=_clean_figure_caption(best.caption),
                ),
            )
            if best is not None
            else ()
        )
    return selection


def _clean_figure_caption(caption: str) -> str:
    """Drop sentences that only describe the recording chrome (the
    presenter's webcam picture-in-picture, the on-screen timestamp
    overlay) -- noise the vision model captions on every single frame,
    never something a student reading the apostila needs explained."""

    sentences = re.split(r"(?<=[.!?])\s+", caption.strip())
    kept = [
        sentence
        for sentence in sentences
        if not any(
            marker in sentence.casefold()
            for marker in _FIGURE_CAPTION_BOILERPLATE_SENTENCE_MARKERS
        )
    ]
    cleaned = " ".join(kept).strip()
    return cleaned or caption


def _is_junk_figure_caption(caption: str) -> bool:
    lowered = caption.casefold()
    return any(marker in lowered for marker in _JUNK_FIGURE_CAPTION_MARKERS)


def main() -> None:
    args = _build_argument_parser().parse_args()
    academic_catalog = load_academic_catalog(args.targets_path)
    academic_schedule = load_academic_schedule(args.schedule_path)
    local_lectures = load_local_lectures(args.local_lectures_path)
    academic_catalog, academic_schedule = merge_local_lectures(
        academic_catalog, academic_schedule, local_lectures
    )
    unpublished_ids = unpublished_lecture_ids(local_lectures)
    video_only_by_id = video_only_lectures(local_lectures)
    lecture_paths = academic_lecture_paths(
        academic_catalog,
        academic_schedule,
        lecture_ids=args.lecture_ids,
    )
    output_root = _prepare_trusted_output_root(args.output_root)
    for lecture_id in args.lecture_ids:
        if lecture_id in video_only_by_id:
            _compile_video_only_lecture(
                video_only_by_id[lecture_id],
                video_path=_resolve_video_path(args.videos_root, lecture_id),
                relative_path=_require_academic_path(lecture_paths, lecture_id),
                lecture_root=_prepare_trusted_output_root(
                    select_output_root(
                        lecture_id,
                        unpublished_ids,
                        output_root,
                        args.private_output_root,
                    )
                ),
            )
    study_ids = [i for i in args.lecture_ids if i not in video_only_by_id]
    if not study_ids:
        return
    titles = _load_lecture_titles(args.queue_path)
    environment = sanitized_child_process_environment()
    executables = _resolve_executables(environment)
    for lecture_id in study_ids:
        lecture_title = titles.get(lecture_id)
        if lecture_title is None:
            raise ValueError(f"aula {lecture_id!r} não encontrada na fila")
        video_path = _resolve_video_path(args.videos_root, lecture_id)
        packs = _load_evidence_packs(args.evidence_packs_root, lecture_id)
        drafts = load_critic_approved_drafts(
            args.validated_units_root, args.drafts_root, lecture_id
        )
        # Use the critic-approved, post-repair prose directly: the drafting prompt
        # already requires every clinical claim to be grounded in the
        # Ledger/Evidence (see ops/llm_instructions.py's DRAFT_COVERAGE_*), so
        # routing through build_ledger_grounded_drafts here discarded that
        # prose and rebuilt the apostila as a bare bullet list of ledger
        # facts -- the exact "lista de bullets em vez de prosa contínua"
        # defect the operator rejected the corpus over (2026-09-15 handoff).
        ledgers = _load_semantic_ledgers(args.ledgers_root, lecture_id)
        _raise_if_any_draft_is_degenerate(lecture_id, drafts, ledgers)
        inference_exam = (
            _load_inference_mock_exam(args.mock_exams_root, lecture_id)
            if args.personal_study_inference
            else None
        )
        inference_podcast_script = (
            _load_inference_podcast_script(args.podcast_scripts_root, lecture_id)
            if args.personal_study_inference
            else None
        )
        inference_flashcard_deck = (
            _load_generated_flashcard_deck(
                args.personal_study_flashcards_root, lecture_id
            )
            if args.personal_study_inference
            else None
        )
        inference_figure_catalog = (
            _load_figure_catalog(args.figure_captions_root, lecture_id)
            if args.personal_study_inference
            else None
        )
        figures_by_pack_sequence = (
            _select_figures_by_pack_sequence(
                packs,
                inference_figure_catalog,
                slides_root=args.extracted_slides_root,
                ffmpeg_executable=executables["ffmpeg"],
                environment=environment,
            )
            if inference_figure_catalog is not None
            else {}
        )
        editions = build_personal_study_editions(
            lecture_id=lecture_id,
            lecture_title=lecture_title,
            drafts=drafts,
            figures_by_pack_sequence=figures_by_pack_sequence,
            chapter_plan=args.chapter_plan,
        )
        bundle = build_personal_study_bundle(
            complete=editions[StudyEditionKind.COMPLETE],
            summary=editions[StudyEditionKind.SUMMARY],
        )
        coverage_report = build_temporal_coverage_report(
            video_duration_seconds=_probe_media_duration(
                video_path,
                executables["ffprobe"],
                environment,
            ),
            packs=packs,
            drafts=drafts,
            complete=bundle.complete,
        )
        relative_path = _require_academic_path(lecture_paths, lecture_id)
        lecture_root = _prepare_trusted_output_root(
            select_output_root(
                lecture_id, unpublished_ids, output_root, args.private_output_root
            )
        )
        with _lecture_publication_lock(lecture_root, lecture_id):
            with tempfile.TemporaryDirectory(
                prefix=f".{lecture_id}.staging.",
                dir=lecture_root,
            ) as raw_staging_dir:
                staging_dir = Path(raw_staging_dir)
                bundled_video_path, figure_relative_paths = _write_bundle(
                    output_dir=staging_dir,
                    video_path=video_path,
                    bundle=bundle,
                    coverage_report=coverage_report,
                    executables=executables,
                    environment=environment,
                    inference_exam=inference_exam,
                    inference_podcast_script=inference_podcast_script,
                    inference_flashcard_deck=inference_flashcard_deck,
                    inference_figure_catalog=inference_figure_catalog,
                    figures_by_pack_sequence=figures_by_pack_sequence,
                    podcast_audio_root=args.podcast_audio_root,
                    extracted_slides_root=args.extracted_slides_root,
                    figure_crop_rect=args.figure_crop_rect,
                )
                _write_bundle_manifest(staging_dir, lecture_id)
                _validate_staged_bundle(
                    staging_dir,
                    bundled_video_path,
                    figure_relative_paths,
                )
                _publish_staged_bundle(
                    output_root=lecture_root,
                    lecture_id=lecture_id,
                    target_relative_path=relative_path,
                    staging_dir=staging_dir,
                )


def _require_academic_path(lecture_paths: Mapping[str, Path], lecture_id: str) -> Path:
    relative_path = lecture_paths.get(lecture_id)
    if relative_path is None:
        raise ValueError(f"videoaula ausente do catálogo acadêmico: {lecture_id}")
    return relative_path


def _compile_video_only_lecture(
    lecture: LocalLecture,
    *,
    video_path: Path,
    relative_path: Path,
    lecture_root: Path,
) -> None:
    """Publish the minimal bundle of a video-only lecture (ADR 0043).

    No evidence, draft or Codex artifact is read: the bundle is the index,
    the identity manifest and the hard-linked video.
    """

    with _lecture_publication_lock(lecture_root, lecture.lecture_id):
        with tempfile.TemporaryDirectory(
            prefix=f".{lecture.lecture_id}.staging.", dir=lecture_root
        ) as raw_staging_dir:
            staging_dir = Path(raw_staging_dir)
            video_relative_path = _link_video_or_explain(staging_dir, video_path)
            posix_video_path = PurePosixPath(video_relative_path.as_posix())
            _write_text_atomically(
                staging_dir / "indice.md",
                render_video_only_index(
                    lecture.lecture_id, lecture.title, posix_video_path
                ),
            )
            _write_bundle_manifest(staging_dir, lecture.lecture_id)
            _require_exact_files(
                staging_dir, expected_video_only_files(posix_video_path)
            )
            _publish_staged_bundle(
                output_root=lecture_root,
                lecture_id=lecture.lecture_id,
                target_relative_path=relative_path,
                staging_dir=staging_dir,
            )


def _link_video_or_explain(staging_dir: Path, video_path: Path) -> Path:
    try:
        return _link_video_into_bundle(staging_dir, video_path)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        raise RuntimeError(
            f"não foi possível criar o link físico do vídeo {video_path} no pacote; "
            "o vídeo e a raiz de materiais devem estar no mesmo sistema de "
            f"arquivos: {error}"
        ) from error


def _require_exact_files(staging_dir: Path, expected: frozenset[str]) -> None:
    actual = {
        path.relative_to(staging_dir).as_posix()
        for path in staging_dir.rglob("*")
        if path.is_file()
    }
    if actual != expected:
        raise RuntimeError(
            f"pacote de vídeo incompleto; esperado {sorted(expected)}, "
            f"encontrado {sorted(actual)}"
        )


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gera o pacote completo de estudo pessoal em pt-BR."
    )
    parser.add_argument("lecture_ids", nargs="+")
    parser.add_argument(
        "--queue-path",
        type=Path,
        default=DEFAULT_QUEUE_PATH,
    )
    parser.add_argument(
        "--videos-root",
        type=Path,
        default=DEFAULT_VIDEOS_ROOT,
    )
    parser.add_argument(
        "--evidence-packs-root",
        type=Path,
        default=DEFAULT_EVIDENCE_PACKS_ROOT,
    )
    parser.add_argument(
        "--ledgers-root",
        type=Path,
        default=DEFAULT_LEDGERS_ROOT,
    )
    parser.add_argument(
        "--validated-units-root",
        type=Path,
        default=DEFAULT_VALIDATED_UNITS_ROOT,
        help=(
            "Unidades aprovadas pelo crítico (Fase 7), fonte do conteúdo "
            "pós-reparo usado na apostila."
        ),
    )
    parser.add_argument(
        "--drafts-root",
        type=Path,
        default=DEFAULT_DRAFTS_ROOT,
        help=(
            "Rascunhos da Fase 6, usados apenas para conferir que cada um "
            "foi revisado pelo crítico na versão atual."
        ),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
    )
    parser.add_argument(
        "--targets-path",
        type=Path,
        default=Path("config/targets.local.yaml"),
    )
    parser.add_argument(
        "--private-output-root",
        type=Path,
        default=PRIVATE_STUDY_GUIDES_ROOT,
        help="raiz de aulas locais sem publish (nunca sincronizada no deploy)",
    )
    parser.add_argument(
        "--local-lectures-path",
        type=Path,
        default=LOCAL_LECTURES_PATH,
    )
    parser.add_argument(
        "--schedule-path",
        type=Path,
        default=Path("config/academic-calendar.local.yaml"),
    )
    parser.add_argument(
        "--personal-study-inference",
        action="store_true",
        default=False,
        dest="personal_study_inference",
        help=(
            "Splice the inference-generated MockExam (ADR 0024) into "
            "simulado.md/.pdf/.typ instead of the deterministic template"
        ),
    )
    parser.add_argument(
        "--mock-exams-root",
        type=Path,
        default=DEFAULT_MOCK_EXAMS_ROOT,
        dest="mock_exams_root",
    )
    parser.add_argument(
        "--podcast-scripts-root",
        type=Path,
        default=DEFAULT_PODCAST_SCRIPTS_ROOT,
        dest="podcast_scripts_root",
    )
    parser.add_argument(
        "--podcast-audio-root",
        type=Path,
        default=DEFAULT_PODCAST_AUDIO_ROOT,
        dest="podcast_audio_root",
    )
    parser.add_argument(
        "--personal-study-flashcards-root",
        type=Path,
        default=DEFAULT_PERSONAL_STUDY_FLASHCARDS_ROOT,
        dest="personal_study_flashcards_root",
    )
    parser.add_argument(
        "--figure-captions-root",
        type=Path,
        default=DEFAULT_FIGURE_CAPTIONS_ROOT,
        dest="figure_captions_root",
    )
    parser.add_argument(
        "--extracted-slides-root",
        type=Path,
        default=DEFAULT_EXTRACTED_SLIDES_ROOT,
        dest="extracted_slides_root",
    )
    parser.add_argument(
        "--chapter-plan",
        type=_load_chapter_plan,
        default=(),
        dest="chapter_plan",
        help=(
            "YAML file listing the chapters of the complete edition, each "
            "entry as {title, first_unit, last_unit} with inclusive, "
            "non-overlapping learning-unit ranges; units outside every "
            "range go to a trailing complementary chapter. Defaults to "
            "numbered parts of ten units ('Parte N')."
        ),
    )
    parser.add_argument(
        "--figure-crop-rect",
        type=_parse_figure_crop_rect,
        default=None,
        dest="figure_crop_rect",
        help=(
            "Crop every embedded figure frame to WIDTH:HEIGHT:X:Y (ffmpeg "
            "crop filter syntax) before staging it -- removes recording "
            "chrome (webcam thumbnail, pillarbox bars, a timestamp "
            "overlay) outside the actual slide content. For example, "
            "'1940:1080:300:0' keeps the slide area of a 2560x1080 "
            "recording with a webcam pillarbox on each side. Defaults to "
            "'none', which embeds frames uncropped."
        ),
    )
    return parser


def _load_lecture_titles(queue_path: Path) -> dict[str, str]:
    lectures = _LECTURE_QUEUE_ADAPTER.validate_json(queue_path.read_text())
    return {lecture.id: lecture.title.strip() for lecture in lectures}


def _prepare_trusted_output_root(output_root: Path) -> Path:
    trusted_root = output_root.absolute()
    _reject_symlink_components(trusted_root)
    trusted_root.mkdir(parents=True, exist_ok=True)
    _reject_symlink_components(trusted_root)
    if not trusted_root.is_dir():
        raise RuntimeError(f"raiz de materiais não é um diretório: {trusted_root}")
    return trusted_root


@contextmanager
def _lecture_publication_lock(
    output_root: Path,
    lecture_id: str,
) -> Iterator[None]:
    canonical_id = _SAFE_IDENTIFIER_ADAPTER.validate_python(lecture_id, strict=True)
    lock_dir = output_root / ".locks"
    _assert_safe_path(output_root, lock_dir)
    lock_dir.mkdir(exist_ok=True)
    _assert_safe_path(output_root, lock_dir)
    lock_path = lock_dir / f"{canonical_id}.lock"
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _publish_staged_bundle(
    *,
    output_root: Path,
    lecture_id: str,
    target_relative_path: Path,
    staging_dir: Path,
) -> Path:
    """Commit a complete staged bundle while preserving the prior bundle."""

    canonical_id = _SAFE_IDENTIFIER_ADAPTER.validate_python(lecture_id, strict=True)
    if target_relative_path.is_absolute() or ".." in target_relative_path.parts:
        raise ValueError(f"destino acadêmico inválido: {target_relative_path}")
    target = output_root / target_relative_path
    _assert_safe_path(output_root, staging_dir)
    existing = _existing_bundle_directories(
        output_root,
        canonical_id,
        excluded_directories=(staging_dir,),
    )
    if len(existing) > 1:
        locations = ", ".join(path.as_posix() for path in existing)
        raise RuntimeError(
            f"mais de um pacote encontrado para a aula {canonical_id}: {locations}"
        )
    current = existing[0] if existing else None
    if target.exists() and current != target:
        raise RuntimeError(f"destino acadêmico já pertence a outra estrutura: {target}")
    _assert_safe_path(output_root, target.parent)
    target.parent.mkdir(parents=True, exist_ok=True)
    _assert_safe_path(output_root, target.parent)

    backup = _unused_hidden_path(output_root, canonical_id, "backup")
    if current is not None:
        _assert_safe_path(output_root, current)
        current.replace(backup)
    try:
        staging_dir.replace(target)
    except BaseException:
        if current is not None and backup.exists() and not current.exists():
            backup.replace(current)
        raise
    if current is not None:
        shutil.rmtree(backup)
        if current.parent != target.parent:
            _remove_empty_academic_parents(current.parent, output_root)
    return target


def _existing_bundle_directories(
    output_root: Path,
    lecture_id: str,
    *,
    excluded_directories: tuple[Path, ...] = (),
) -> tuple[Path, ...]:
    excluded = {path.absolute() for path in excluded_directories}
    candidates: set[Path] = set()
    for raw_dir, directory_names, filenames in os.walk(
        output_root,
        followlinks=False,
    ):
        directory = Path(raw_dir)
        safe_names: list[str] = []
        for name in directory_names:
            child = directory / name
            if child.is_symlink():
                raise RuntimeError(
                    f"link simbólico não permitido na árvore acadêmica: {child}"
                )
            if child.absolute() not in excluded and not name.startswith("."):
                safe_names.append(name)
        directory_names[:] = safe_names
        if directory.absolute() in excluded:
            directory_names.clear()
            continue
        if _BUNDLE_MANIFEST_FILENAME in filenames or "indice.md" in filenames:
            identity = read_academic_bundle_identity(directory)
            if identity == lecture_id:
                candidates.add(directory)
    return tuple(sorted(candidates))


def _write_bundle_manifest(output_dir: Path, lecture_id: str) -> None:
    manifest = AcademicBundleIdentityManifest(
        schema_version=1,
        lecture_id=lecture_id,
    )
    _write_text_atomically(
        output_dir / _BUNDLE_MANIFEST_FILENAME,
        manifest.model_dump_json(indent=2) + "\n",
    )


def _unused_hidden_path(output_root: Path, lecture_id: str, role: str) -> Path:
    path = Path(
        tempfile.mkdtemp(
            prefix=f".{lecture_id}.{role}.",
            dir=output_root,
        )
    )
    path.rmdir()
    return path


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise RuntimeError(f"componente de caminho é link simbólico: {current}")


def _assert_safe_path(output_root: Path, path: Path) -> None:
    absolute_path = path.absolute()
    try:
        absolute_path.relative_to(output_root)
    except ValueError as exc:
        raise RuntimeError(
            f"caminho fora da raiz de materiais: {absolute_path}"
        ) from exc
    _reject_symlink_components(absolute_path)


def _remove_empty_academic_parents(parent: Path, output_root: Path) -> None:
    while parent != output_root:
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent


def _load_semantic_ledgers(
    ledgers_root: Path,
    lecture_id: str,
) -> tuple[SemanticLedger, ...]:
    lecture_dir = ledgers_root / lecture_id
    ledger_paths = sorted(lecture_dir.glob("ledger_*.json"))
    if not ledger_paths:
        raise FileNotFoundError(f"nenhum ledger semântico encontrado em {lecture_dir}")
    return tuple(
        SemanticLedger.model_validate_json(path.read_text()) for path in ledger_paths
    )


def _load_evidence_packs(
    evidence_packs_root: Path,
    lecture_id: str,
) -> tuple[EvidencePack, ...]:
    lecture_dir = evidence_packs_root / lecture_id
    pack_paths = sorted(lecture_dir.glob("pack_*.json"))
    if not pack_paths:
        raise FileNotFoundError(
            f"nenhum bloco de evidência encontrado em {lecture_dir}"
        )
    return tuple(
        EvidencePack.model_validate_json(path.read_text()) for path in pack_paths
    )


def _resolve_video_path(videos_root: Path, lecture_id: str) -> Path:
    matches = sorted(videos_root.glob(f"{lecture_id}.*"))
    if len(matches) != 1:
        raise FileNotFoundError(
            f"esperado exatamente um vídeo para a aula {lecture_id!r} em "
            f"{videos_root}; encontrados {len(matches)}"
        )
    return matches[0]


def _resolve_executables(environment: dict[str, str]) -> dict[str, Path]:
    search_path = environment.get("PATH")
    return {
        name: Path(
            resolve_trusted_executable(
                name,
                search_path=search_path,
            )
        )
        for name in ("typst", "say", "ffmpeg", "ffprobe")
    }


def _probe_media_duration(
    media_path: Path,
    ffprobe_executable: Path,
    environment: dict[str, str],
) -> float:
    completed = run_bounded_process(
        [
            str(ffprobe_executable),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(media_path),
        ],
        env=environment,
        timeout_seconds=120,
        maximum_stdout_bytes=_MAXIMUM_PROCESS_OUTPUT_BYTES,
        maximum_stderr_bytes=_MAXIMUM_PROCESS_OUTPUT_BYTES,
        check=True,
    )
    payload = json.loads(completed.stdout)
    try:
        duration = float(payload["format"]["duration"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            f"ffprobe não retornou duração válida para {media_path}"
        ) from error
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"duração inválida para {media_path}: {duration}")
    return duration


def _write_bundle(
    *,
    output_dir: Path,
    video_path: Path,
    bundle: PersonalStudyBundle,
    coverage_report: TemporalCoverageReport,
    executables: dict[str, Path],
    environment: dict[str, str],
    inference_exam: MockExam | None = None,
    inference_podcast_script: PodcastScript | None = None,
    inference_flashcard_deck: PersonalStudyFlashcardDeck | None = None,
    inference_figure_catalog: LectureFigureCatalog | None = None,
    figures_by_pack_sequence: Mapping[int, tuple[SelectedFigure, ...]] | None = None,
    podcast_audio_root: Path = DEFAULT_PODCAST_AUDIO_ROOT,
    extracted_slides_root: Path = DEFAULT_EXTRACTED_SLIDES_ROOT,
    figure_crop_rect: FigureCropRect | None = None,
) -> tuple[Path, tuple[str, ...]]:
    # Figures must be staged before the editions are compiled: the
    # complete edition's Typst source now embeds them inline (2026-09-15),
    # and Typst fails to compile an #image() reference that does not exist
    # on disk yet.
    figure_relative_paths = (
        _write_figures(
            output_dir,
            inference_figure_catalog,
            extracted_slides_root,
            executables["ffmpeg"],
            environment,
            figure_crop_rect=figure_crop_rect,
        )
        if inference_figure_catalog is not None
        else ()
    )
    # bundle.summary itself stays in the bundle (compiler.py still uses its
    # chapters to derive flashcards and the resumo_critico document below),
    # but it is no longer rendered as its own standalone "apostila_resumida"
    # document (2026-09-15): that was pure duplication of resumo_critico
    # (operator decision, see critical_review_document's docstring).
    _write_edition(output_dir, bundle.complete, executables["typst"], environment)
    for stem, document in _bundle_documents(
        bundle,
        inference_exam,
        inference_podcast_script,
        figures_by_pack_sequence=figures_by_pack_sequence,
    ):
        _write_document(
            output_dir,
            stem,
            document,
            executables["typst"],
            environment,
        )
    _write_temporal_coverage(
        output_dir,
        coverage_report,
        executables["typst"],
        environment,
    )
    _write_flashcards(
        output_dir,
        bundle,
        executables["typst"],
        environment,
        deck=inference_flashcard_deck,
    )
    if inference_podcast_script is not None:
        _copy_inference_podcast_audio(
            output_dir,
            podcast_audio_root,
            inference_podcast_script,
            ffprobe_executable=executables["ffprobe"],
            environment=environment,
        )
    else:
        _write_podcast_audio(
            output_dir,
            bundle,
            say_executable=executables["say"],
            ffmpeg_executable=executables["ffmpeg"],
            ffprobe_executable=executables["ffprobe"],
            environment=environment,
        )
    bundled_video_path = _link_video_into_bundle(output_dir, video_path)
    _write_index(
        output_dir,
        bundle,
        coverage_report,
        bundled_video_path,
        figure_count=(
            len(inference_figure_catalog.figures)
            if inference_figure_catalog is not None
            else 0
        ),
    )
    return bundled_video_path, figure_relative_paths


def _write_figures(
    output_dir: Path,
    catalog: LectureFigureCatalog,
    extracted_slides_root: Path,
    ffmpeg_executable: Path,
    environment: dict[str, str],
    *,
    figure_crop_rect: FigureCropRect | None = None,
) -> tuple[str, ...]:
    """Copy each captioned frame's own image file into the bundle's figure
    subdirectory, so the complete edition's inline #image() references
    resolve when its Typst is compiled (2026-09-15: figures are embedded
    inline in the complete edition; the former separate figure-atlas
    document that used to live here was removed as duplication the
    operator had already rejected). Every destination path is checked by
    the script's existing safe-path assertion and symlink-component
    rejection before anything is written, mirroring the video link step's
    own dynamically named staged path. When figure_crop_rect is given,
    every staged frame is cropped to it first (2026-09-15 document review
    -- see FigureCropRect's own docstring). Returns the staged image
    relative paths so the caller can fold them into the expected-file
    validation."""

    canonical_lecture_id = _SAFE_IDENTIFIER_ADAPTER.validate_python(
        catalog.lecture_id, strict=True
    )
    figures_dir = output_dir / BUNDLE_FIGURES_SUBDIRECTORY
    _assert_safe_path(output_dir, figures_dir)
    figures_dir.mkdir(exist_ok=True)
    _assert_safe_path(output_dir, figures_dir)
    staged_relative_paths: list[str] = []
    for figure in catalog.figures:
        source_path = (
            extracted_slides_root / canonical_lecture_id / figure.frame_filename
        )
        if not source_path.is_file():
            raise RuntimeError(f"quadro de figura ausente: {source_path}")
        destination_path = figures_dir / figure.frame_filename
        _assert_safe_path(output_dir, destination_path)
        _copy_figure_image(
            source_path,
            destination_path,
            crop_rect=figure_crop_rect,
            ffmpeg_executable=ffmpeg_executable,
            environment=environment,
        )
        _assert_safe_path(output_dir, destination_path)
        staged_relative_paths.append(
            destination_path.relative_to(output_dir).as_posix()
        )
    return tuple(staged_relative_paths)


def _copy_figure_image(
    source_path: Path,
    destination_path: Path,
    *,
    crop_rect: FigureCropRect | None,
    ffmpeg_executable: Path,
    environment: dict[str, str],
) -> None:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{destination_path.name}.",
        suffix=".tmp",
        dir=destination_path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(raw_path)
    try:
        if crop_rect is None:
            shutil.copyfile(source_path, temporary_path)
        else:
            _crop_figure_image(
                source_path,
                temporary_path,
                crop_rect,
                ffmpeg_executable,
                environment,
            )
        temporary_path.replace(destination_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _crop_figure_image(
    source_path: Path,
    destination_path: Path,
    crop_rect: FigureCropRect,
    ffmpeg_executable: Path,
    environment: dict[str, str],
) -> None:
    run_bounded_process(
        [
            str(ffmpeg_executable),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source_path),
            "-vf",
            crop_rect.as_ffmpeg_filter(),
            "-frames:v",
            "1",
            "-update",
            "1",
            # The destination is a tempfile.mkstemp() path ending in
            # ".tmp" (for the same atomic-write-then-replace pattern the
            # plain-copy branch uses), so ffmpeg cannot infer the muxer or
            # codec from the extension the way it could from a real
            # ".png" path: with no extension hint, "-f image2" alone
            # silently defaulted to JPEG output instead of failing loudly
            # (caught by this change's own test asserting exact output
            # dimensions) -- both the muxer and the codec are forced
            # explicitly so the source frame's own PNG format is
            # preserved regardless of the temp path's extension.
            "-f",
            "image2",
            "-c:v",
            "png",
            str(destination_path),
        ],
        env=environment,
        timeout_seconds=_SUBPROCESS_TIMEOUT_SECONDS,
        maximum_stdout_bytes=_MAXIMUM_PROCESS_OUTPUT_BYTES,
        maximum_stderr_bytes=_MAXIMUM_PROCESS_OUTPUT_BYTES,
        check=True,
    )


def _validate_staged_bundle(
    staging_dir: Path,
    bundled_video_path: Path,
    figure_relative_paths: tuple[str, ...] = (),
) -> None:
    expected = _EXPECTED_BUNDLE_FILES | {bundled_video_path.as_posix()}
    if figure_relative_paths:
        expected |= set(figure_relative_paths)
    actual = {
        path.relative_to(staging_dir).as_posix()
        for path in staging_dir.rglob("*")
        if path.is_file()
    }
    if actual != expected:
        missing = ", ".join(sorted(expected - actual)) or "nenhum"
        additional = ", ".join(sorted(actual - expected)) or "nenhum"
        raise RuntimeError(
            "pacote de estudo incompleto; "
            f"ausentes: {missing}; adicionais: {additional}"
        )


def _bundle_documents(
    bundle: PersonalStudyBundle,
    inference_exam: MockExam | None = None,
    inference_podcast_script: PodcastScript | None = None,
    *,
    figures_by_pack_sequence: Mapping[int, tuple[SelectedFigure, ...]] | None = None,
) -> tuple[tuple[str, StudyDocument], ...]:
    simulado_document = (
        inference_mock_exam_document(
            inference_exam, figures_by_pack_sequence=figures_by_pack_sequence
        )
        if inference_exam is not None
        else mock_exam_document(bundle)
    )
    podcast_document = (
        inference_podcast_script_document(inference_podcast_script)
        if inference_podcast_script is not None
        else podcast_script_document(bundle)
    )
    return (
        (
            "resumo_critico",
            critical_review_document(bundle, inference_exam=inference_exam),
        ),
        ("simulado", simulado_document),
        ("roteiro_revisao", review_plan_document(bundle)),
        ("podcast_roteiro", podcast_document),
    )


def _write_edition(
    output_dir: Path,
    edition: StudyEdition,
    typst_executable: Path,
    environment: dict[str, str],
) -> None:
    stem = (
        "apostila_completa"
        if edition.kind is StudyEditionKind.COMPLETE
        else "apostila_resumida"
    )
    markdown_path = output_dir / f"{stem}.md"
    typst_path = output_dir / f"{stem}.typ"
    pdf_path = output_dir / f"{stem}.pdf"
    _write_text_atomically(
        markdown_path,
        render_study_edition_markdown(edition),
    )
    _write_text_atomically(
        typst_path,
        render_study_edition_typst(edition),
    )
    _compile_typst_atomically(
        typst_executable,
        typst_path,
        pdf_path,
        environment,
    )


def _write_document(
    output_dir: Path,
    stem: str,
    document: StudyDocument,
    typst_executable: Path,
    environment: dict[str, str],
) -> None:
    markdown_path = output_dir / f"{stem}.md"
    typst_path = output_dir / f"{stem}.typ"
    pdf_path = output_dir / f"{stem}.pdf"
    _write_text_atomically(
        markdown_path,
        render_study_document_markdown(document),
    )
    _write_text_atomically(
        typst_path,
        render_study_document_typst(document),
    )
    _compile_typst_atomically(
        typst_executable,
        typst_path,
        pdf_path,
        environment,
    )


def _write_flashcards(
    output_dir: Path,
    bundle: PersonalStudyBundle,
    typst_executable: Path,
    environment: dict[str, str],
    *,
    deck: PersonalStudyFlashcardDeck | None = None,
) -> None:
    markdown = (
        render_generated_flashcards_markdown(deck)
        if deck is not None
        else render_flashcards_markdown(bundle)
    )
    csv_text = (
        render_generated_flashcards_csv(deck)
        if deck is not None
        else render_flashcards_csv(bundle)
    )
    typst_text = (
        render_printable_generated_flashcards_typst(deck)
        if deck is not None
        else render_printable_flashcards_typst(bundle)
    )
    _write_text_atomically(output_dir / "flashcards.md", markdown)
    _write_text_atomically(output_dir / "flashcards.csv", csv_text)
    typst_path = output_dir / "flashcards.typ"
    _write_text_atomically(typst_path, typst_text)
    _compile_typst_atomically(
        typst_executable,
        typst_path,
        output_dir / "flashcards.pdf",
        environment,
    )


def _write_temporal_coverage(
    output_dir: Path,
    report: TemporalCoverageReport,
    typst_executable: Path,
    environment: dict[str, str],
) -> None:
    _write_document(
        output_dir,
        "mapa_temporal",
        temporal_coverage_document(report),
        typst_executable,
        environment,
    )
    _write_text_atomically(
        output_dir / "mapa_temporal.csv",
        render_temporal_coverage_csv(report),
    )


def _write_index(
    output_dir: Path,
    bundle: PersonalStudyBundle,
    coverage_report: TemporalCoverageReport,
    video_relative_path: Path,
    *,
    figure_count: int = 0,
) -> None:
    complete = bundle.complete
    content = (
        f"# {complete.lecture_title.replace(chr(10), ' ').strip()}\n\n"
        f"Aula `{complete.lecture_id}` - "
        f"{complete.source_unit_count} unidades clínicas.\n\n"
        f"Cobertura temporal estrutural: "
        f"{coverage_report.coverage_percent:.2f}% - "
        f"{coverage_report.status.pt_br_label}.\n\n"
        "## Aula original\n\n"
        f"- [Vídeo da aula]({video_relative_path.as_posix()})\n\n"
        "## Apostilas\n\n"
        "- [Apostila completa em PDF](apostila_completa.pdf)\n"
        "- [Apostila completa em Markdown](apostila_completa.md)\n"
        "\n## Revisão e avaliação\n\n"
        "- [Resumo crítico em PDF](resumo_critico.pdf)\n"
        "- [Resumo crítico em Markdown](resumo_critico.md)\n"
        "- [Flashcards para leitura](flashcards.md)\n"
        "- [Flashcards para impressão frente/verso](flashcards.pdf)\n"
        "- [Flashcards para Anki](flashcards.csv)\n"
        "- [Simulado em PDF](simulado.pdf)\n"
        "- [Simulado em Markdown](simulado.md)\n"
        "- [Roteiro de revisão em PDF](roteiro_revisao.pdf)\n"
        "- [Roteiro de revisão em Markdown](roteiro_revisao.md)\n"
        "\n## Rastreabilidade temporal\n\n"
        "- [Mapa temporal em PDF](mapa_temporal.pdf)\n"
        "- [Mapa temporal em Markdown](mapa_temporal.md)\n"
        "- [Mapa temporal em CSV](mapa_temporal.csv)\n"
        "\n## Áudio\n\n"
        "- [Podcast de revisão](podcast_revisao.m4a)\n"
        "- [Roteiro do podcast em PDF](podcast_roteiro.pdf)\n"
        "- [Roteiro do podcast em Markdown](podcast_roteiro.md)\n"
    )
    content += _coverage_notice_section(coverage_report)
    if figure_count:
        content += (
            f"\n{figure_count} figura(s) legendada(s) da aula, geradas por "
            "inferência sem certificação clínica, embutidas diretamente na "
            "apostila completa.\n"
        )
    _write_text_atomically(output_dir / "indice.md", content)


def _coverage_notice_section(coverage_report: TemporalCoverageReport) -> str:
    if not coverage_report.warnings:
        return ""
    shown = coverage_report.warnings[:MAXIMUM_INDEX_COVERAGE_NOTICES]
    section = "\n## Avisos de cobertura\n\n" + "".join(f"- {w}\n" for w in shown)
    hidden = len(coverage_report.warnings) - len(shown)
    if hidden:
        section += f"- Mais {hidden} aviso(s) no mapa temporal.\n"
    return section


def _link_video_into_bundle(
    output_dir: Path,
    source_video_path: Path,
) -> Path:
    if not source_video_path.is_file():
        raise FileNotFoundError(f"vídeo de origem não encontrado: {source_video_path}")
    if not source_video_path.suffix:
        raise ValueError(f"vídeo sem extensão: {source_video_path}")
    video_dir = output_dir / "video"
    if video_dir.is_symlink():
        raise RuntimeError(f"diretório de vídeo não pode ser link: {video_dir}")
    video_dir.mkdir(parents=True, exist_ok=True)
    if video_dir.is_symlink():
        raise RuntimeError(f"diretório de vídeo não pode ser link: {video_dir}")
    destination = video_dir / f"aula{source_video_path.suffix.casefold()}"
    stale_videos = tuple(
        path for path in video_dir.glob("aula.*") if path != destination
    )
    if stale_videos:
        stale_names = ", ".join(path.name for path in stale_videos)
        raise ValueError(f"vídeos antigos encontrados em {video_dir}: {stale_names}")
    if (
        destination.exists()
        and not destination.is_symlink()
        and destination.samefile(source_video_path)
    ):
        return destination.relative_to(output_dir)
    with tempfile.TemporaryDirectory(prefix=".video.", dir=video_dir) as raw_dir:
        temporary_link = Path(raw_dir) / destination.name
        os.link(source_video_path, temporary_link)
        temporary_link.replace(destination)
    return destination.relative_to(output_dir)


def _write_podcast_audio(
    output_dir: Path,
    bundle: PersonalStudyBundle,
    *,
    say_executable: Path,
    ffmpeg_executable: Path,
    ffprobe_executable: Path,
    environment: dict[str, str],
) -> None:
    podcast_text = "\n\n".join(
        turn.text for turn in bundle.podcast_turns if turn.text.strip()
    )
    with tempfile.TemporaryDirectory(
        prefix=".podcast.",
        dir=output_dir,
    ) as raw_directory:
        temporary_dir = Path(raw_directory)
        text_path = temporary_dir / "roteiro.txt"
        audio_path = temporary_dir / "podcast.aiff"
        encoded_path = temporary_dir / _PODCAST_FILENAME
        text_path.write_text(podcast_text, encoding="utf-8")
        _run_say(
            say_executable,
            text_path,
            audio_path,
            environment,
        )
        _run_ffmpeg(
            ffmpeg_executable,
            audio_path,
            encoded_path,
            environment,
        )
        duration_seconds = _probe_media_duration(
            encoded_path,
            ffprobe_executable,
            environment,
        )
        _validate_podcast_duration(
            duration_seconds=duration_seconds,
            podcast_text=podcast_text,
        )
        encoded_path.replace(output_dir / _PODCAST_FILENAME)


def _validate_podcast_duration(
    *,
    duration_seconds: float,
    podcast_text: str,
) -> None:
    word_count = len(podcast_text.split())
    minimum_duration_seconds = max(
        1.0,
        word_count * _MINIMUM_PODCAST_SECONDS_PER_WORD,
    )
    if (
        not math.isfinite(duration_seconds)
        or duration_seconds < minimum_duration_seconds
    ):
        raise ValueError(
            "áudio do podcast está vazio ou truncado: "
            f"{duration_seconds:.3f}s para {word_count} palavras; "
            f"mínimo esperado {minimum_duration_seconds:.3f}s"
        )


def _copy_inference_podcast_audio(
    output_dir: Path,
    podcast_audio_root: Path,
    podcast_script: PodcastScript,
    *,
    ffprobe_executable: Path,
    environment: dict[str, str],
) -> None:
    """Place Phase 15's narrated audio (ADR 0024's uncertified personal-
    study route) into the staged bundle under the deterministic path's own
    filename, so the expected file set _validate_staged_bundle checks does
    not change. Fails naming the missing path rather than silently
    skipping the audio. Routed through the same mkstemp-then-replace
    atomic write discipline as every other staged file (see
    _compile_typst_atomically/_write_text_atomically), and the existing
    duration validation still runs against whichever audio file was
    placed, so a truncated or empty inference audio file is still caught.
    """

    canonical_id = _SAFE_IDENTIFIER_ADAPTER.validate_python(
        podcast_script.lecture_id, strict=True
    )
    source_path = podcast_audio_root / canonical_id / "podcast_final.m4a"
    if not source_path.is_file():
        raise RuntimeError(
            f"áudio de podcast gerado por inferência ausente: {source_path}"
        )
    destination_path = output_dir / _PODCAST_FILENAME
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{_PODCAST_FILENAME}.",
        suffix=".tmp",
        dir=output_dir,
    )
    os.close(descriptor)
    temporary_path = Path(raw_path)
    try:
        shutil.copyfile(source_path, temporary_path)
        duration_seconds = _probe_media_duration(
            temporary_path,
            ffprobe_executable,
            environment,
        )
        podcast_text = "\n\n".join(
            line.text for line in podcast_script.dialogue if line.text.strip()
        )
        _validate_podcast_duration(
            duration_seconds=duration_seconds,
            podcast_text=podcast_text,
        )
        temporary_path.replace(destination_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _run_say(
    executable: Path,
    text_path: Path,
    audio_path: Path,
    environment: dict[str, str],
) -> None:
    run_bounded_process(
        [
            str(executable),
            "-v",
            _PODCAST_VOICE,
            "-f",
            str(text_path),
            "-o",
            str(audio_path),
        ],
        env=environment,
        timeout_seconds=_SUBPROCESS_TIMEOUT_SECONDS,
        maximum_stdout_bytes=_MAXIMUM_PROCESS_OUTPUT_BYTES,
        maximum_stderr_bytes=_MAXIMUM_PROCESS_OUTPUT_BYTES,
        check=True,
    )


def _run_ffmpeg(
    executable: Path,
    audio_path: Path,
    encoded_path: Path,
    environment: dict[str, str],
) -> None:
    run_bounded_process(
        [
            str(executable),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(audio_path),
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            str(encoded_path),
        ],
        env=environment,
        timeout_seconds=_SUBPROCESS_TIMEOUT_SECONDS,
        maximum_stdout_bytes=_MAXIMUM_PROCESS_OUTPUT_BYTES,
        maximum_stderr_bytes=_MAXIMUM_PROCESS_OUTPUT_BYTES,
        check=True,
    )


def _write_text_atomically(path: Path, content: str) -> None:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _compile_typst_atomically(
    executable: Path,
    source_path: Path,
    output_path: Path,
    environment: dict[str, str],
) -> None:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{output_path.stem}.",
        suffix=".pdf",
        dir=output_path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(raw_path)
    try:
        run_bounded_process(
            [
                str(executable),
                "compile",
                "--root",
                str(Path.cwd().resolve()),
                str(source_path.resolve()),
                str(temporary_path.resolve()),
            ],
            env=environment,
            timeout_seconds=120,
            maximum_stdout_bytes=_MAXIMUM_PROCESS_OUTPUT_BYTES,
            maximum_stderr_bytes=_MAXIMUM_PROCESS_OUTPUT_BYTES,
            check=True,
        )
        temporary_path.replace(output_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    main()

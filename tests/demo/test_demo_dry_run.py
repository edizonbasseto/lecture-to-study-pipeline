"""Opt-in dry run of the late pipeline stages on hand-authored fixtures.

Stage 8 (document assembly), stage 9 (metadata aggregation), the study-guide
script and the site generator have never run on lecture data. This test runs
them once on schema-valid fixtures for one scratch lecture and ends in a real
sign-in against the site app, so first-run defects surface before any LLM time
is spent (ADR 0047, research Pitfall 10).

It needs macOS plus typst, say, ffmpeg and ffprobe, so it runs only when
LECTURE_STUDY_DEMO_TOOLS is "1"; once opted in, a missing tool is a failure,
never a skip.
"""

from __future__ import annotations

import importlib.util
import json
import os
import secrets
import shutil
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient
from starlette.types import ASGIApp

from lecture_study.assembly import assembler as assembler_module
from lecture_study.assembly.assembler import DocumentAssembler
from lecture_study.domain.models import (
    CanonicalFrame,
    CriticReview,
    DurationException,
    DurationExceptionReason,
    EvidencePack,
    LearningUnitDraft,
    SemanticFact,
    SemanticLedger,
    TranscriptionSegment,
    ValidatedLearningUnit,
)
from lecture_study.metadata import aggregator as aggregator_module
from lecture_study.metadata.aggregator import MetadataAggregator
from lecture_study.ops.process_environment import (
    resolve_trusted_executable,
    sanitized_child_process_environment,
)
from lecture_study.ops.process_lifecycle import run_bounded_process
from lecture_study.site.access import hash_password
from lecture_study.site.app import create_app
from lecture_study.site.generator import generate_site
from lecture_study.study_materials.local_lectures import (
    LocalLecture,
    register_local_lecture,
)

TOOLS_ENVIRONMENT_VARIABLE = "LECTURE_STUDY_DEMO_TOOLS"
REQUIRED_TOOLS = ("typst", "say", "ffmpeg", "ffprobe")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GUIDE_SCRIPT = REPOSITORY_ROOT / "scripts" / "generate_personal_study_guides.py"
DEMO_SITE_SCRIPT = REPOSITORY_ROOT / "scripts" / "demo_site.py"
TARGETS_EXAMPLE = REPOSITORY_ROOT / "config" / "targets.example.yaml"
DEMO_SCHEDULE = REPOSITORY_ROOT / "demo" / "site" / "academic-calendar.yaml"

SCRATCH_LECTURE_ID = "925199"
SCRATCH_TITLE = "Aula sintética de ensaio"
VIDEO_SECONDS = 20
DEMO_EMAIL = "aluno.demo@example.com"
PROCESS_OUTPUT_LIMIT_BYTES = 1024 * 1024

pytestmark = pytest.mark.skipif(
    os.environ.get(TOOLS_ENVIRONMENT_VARIABLE) != "1",
    reason=(
        f"set {TOOLS_ENVIRONMENT_VARIABLE}=1 on macOS with typst, say, ffmpeg "
        "and ffprobe installed to run the fixture dry run"
    ),
)


@dataclass(frozen=True)
class DryRunWorkspace:
    """Every directory the dry run touches, all inside one temporary root."""

    root: Path

    @property
    def raw_videos(self) -> Path:
        return self.root / "01_raw"

    @property
    def queue(self) -> Path:
        return self.root / "queue.json"

    @property
    def registry(self) -> Path:
        return self.root / "local-lectures.yaml"

    @property
    def evidence_packs(self) -> Path:
        return self.root / "06_evidence_packs"

    @property
    def ledgers(self) -> Path:
        return self.root / "07_semantic_ledgers"

    @property
    def drafts(self) -> Path:
        return self.root / "08_drafts"

    @property
    def validated_units(self) -> Path:
        return self.root / "09_validated_units"

    @property
    def canonical_documents(self) -> Path:
        return self.root / "10_canonical_documents"

    @property
    def metadata(self) -> Path:
        return self.root / "11_metadata"

    @property
    def mock_exams(self) -> Path:
        return self.root / "14_mock_exams"

    @property
    def podcast_scripts(self) -> Path:
        return self.root / "15_podcast_scripts"

    @property
    def podcast_audio(self) -> Path:
        return self.root / "16_podcast_audio"

    @property
    def flashcards(self) -> Path:
        return self.root / "17_flashcards"

    @property
    def figure_captions(self) -> Path:
        return self.root / "18_figure_captions"

    @property
    def extracted_slides(self) -> Path:
        return self.root / "19_extracted_slides"

    @property
    def guides(self) -> Path:
        return self.root / "study-guides"

    @property
    def private_guides(self) -> Path:
        return self.root / "study-guides-local"

    @property
    def site(self) -> Path:
        return self.root / "site"

    @property
    def site_videos(self) -> Path:
        return self.root / "site-videos"

    @property
    def credentials(self) -> Path:
        return self.root / "site-credentials.txt"

    @property
    def session_key(self) -> Path:
        return self.root / "site-session-key"


def test_fixtures_reach_a_signed_in_page_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    require_tools()
    workspace = DryRunWorkspace(tmp_path)
    environment = sanitized_child_process_environment()

    register_scratch_lecture(workspace, build_scratch_video(tmp_path, environment))
    write_stage_fixtures(workspace)
    run_stage_eight_and_nine(workspace, monkeypatch)
    run_study_guide_script(workspace, environment)
    generate_site_from_guides(workspace)

    password = secrets.token_urlsafe(16)
    write_site_access(workspace, password)
    demo_site = load_demo_site_script()
    # HTTPS base URL so the Secure, __Host- prefixed cookies flow back.
    client = TestClient(signed_in_app(workspace), base_url="https://testserver")

    view = demo_site.check_signed_in_view(
        client,
        email=DEMO_EMAIL,
        password=password,
        page_path=demo_site.lecture_page_path(workspace.guides, SCRATCH_LECTURE_ID),
    )

    assert view.passed(), view


def require_tools() -> None:
    assert sys.platform == "darwin", "the fixture dry run needs macOS (say, Luciana)"
    missing = [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]
    assert not missing, f"missing tools for the fixture dry run: {missing}"


def build_scratch_video(tmp_path: Path, environment: dict[str, str]) -> Path:
    video_path = tmp_path / "scratch-source.mp4"
    ffmpeg = resolve_trusted_executable("ffmpeg", search_path=environment.get("PATH"))
    run_bounded_process(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x203040:s=320x180:r=10:d={VIDEO_SECONDS}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={VIDEO_SECONDS}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(video_path),
        ],
        env=environment,
        timeout_seconds=120,
        maximum_stdout_bytes=PROCESS_OUTPUT_LIMIT_BYTES,
        maximum_stderr_bytes=PROCESS_OUTPUT_LIMIT_BYTES,
        check=True,
    )
    return video_path


def register_scratch_lecture(workspace: DryRunWorkspace, video_path: Path) -> None:
    lecture = LocalLecture(
        lecture_id=SCRATCH_LECTURE_ID,
        title=SCRATCH_TITLE,
        discipline="Disciplina de ensaio",
        section="Tópico de ensaio",
        scheduled_date=date(2026, 1, 1),
        publish=True,
        remote_llm_allowed=False,
    )
    register_local_lecture(
        lecture,
        video_path,
        portal_ids=frozenset(),
        local_lectures_path=workspace.registry,
        raw_video_dir=workspace.raw_videos,
        queue_path=workspace.queue,
        public_root=workspace.guides,
    )


def write_stage_fixtures(workspace: DryRunWorkspace) -> None:
    write_json(
        workspace.evidence_packs / SCRATCH_LECTURE_ID / "pack_001.json",
        scratch_pack().model_dump_json(),
    )
    write_json(
        workspace.ledgers / SCRATCH_LECTURE_ID / "ledger_001.json",
        scratch_ledger().model_dump_json(),
    )
    draft = scratch_draft()
    draft_path = workspace.drafts / SCRATCH_LECTURE_ID / "draft_001.json"
    unit_path = workspace.validated_units / SCRATCH_LECTURE_ID / "unit_001.json"
    write_json(draft_path, draft.model_dump_json())
    write_json(unit_path, scratch_unit(draft).model_dump_json())
    make_older(draft_path, seconds=10)


def scratch_pack() -> EvidencePack:
    return EvidencePack(
        lecture_id=SCRATCH_LECTURE_ID,
        pack_sequence=1,
        start_time=0.0,
        end_time=float(VIDEO_SECONDS),
        anchor_frame=CanonicalFrame(
            timestamp_sec=1.0,
            ocr_text="Fáscia superficial e profunda",
            image_path=Path("frames/frame-001.png"),
        ),
        segments=[
            TranscriptionSegment(
                start_time=0.0,
                end_time=10.0,
                text="A fáscia superficial reveste o músculo e acompanha o plano cirúrgico.",
            ),
            TranscriptionSegment(
                start_time=10.0,
                end_time=float(VIDEO_SECONDS),
                text="O exame histopatológico confirma o diagnóstico depois da biópsia.",
            ),
        ],
        duration_exception=DurationException(
            reason=DurationExceptionReason.LECTURE_SHORTER_THAN_MINIMUM_UNIT
        ),
    )


def scratch_ledger() -> SemanticLedger:
    return SemanticLedger(
        lecture_id=SCRATCH_LECTURE_ID,
        pack_sequence=1,
        facts=[
            SemanticFact(
                entity_name="fáscia superficial",
                category="anatomia",
                clinical_context="reveste o músculo e orienta o plano cirúrgico",
                timestamp_ref=2.0,
            ),
            SemanticFact(
                entity_name="exame histopatológico",
                category="diagnóstico",
                clinical_context="confirma o diagnóstico após a biópsia",
                timestamp_ref=12.0,
            ),
            SemanticFact(
                entity_name="biópsia incisional",
                category="tratamento",
                clinical_context="coleta de fragmento para análise",
                timestamp_ref=13.0,
            ),
            SemanticFact(
                entity_name="sangramento",
                category="alerta",
                clinical_context="risco no plano profundo da fáscia",
                timestamp_ref=5.0,
            ),
        ],
    )


def scratch_draft() -> LearningUnitDraft:
    return LearningUnitDraft(
        lecture_id=SCRATCH_LECTURE_ID,
        pack_sequence=1,
        title="Fáscia e exame histopatológico",
        body_markdown=(
            "## Fáscia superficial\n\n"
            "A fáscia superficial reveste o músculo e orienta o plano cirúrgico. "
            "O cirurgião reconhece o plano profundo pela resistência do tecido "
            "e evita o sangramento ao respeitar essa camada.\n\n"
            "## Exame histopatológico\n\n"
            "O exame histopatológico confirma o diagnóstico depois da biópsia "
            "incisional, que coleta um fragmento representativo do tecido para "
            "análise em laboratório."
        ),
        clinical_warnings=["Risco de sangramento no plano profundo da fáscia."],
    )


def scratch_unit(draft: LearningUnitDraft) -> ValidatedLearningUnit:
    return ValidatedLearningUnit(
        lecture_id=SCRATCH_LECTURE_ID,
        pack_sequence=1,
        content=draft,
        review=CriticReview(
            passed=True, hallucination_score=0.0, feedback="Fundamentado no ledger."
        ),
    )


def write_json(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_older(path: Path, *, seconds: int) -> None:
    status = path.stat()
    os.utime(path, (status.st_atime - seconds, status.st_mtime - seconds))


def run_stage_eight_and_nine(
    workspace: DryRunWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        assembler_module, "VALIDATED_UNITS_INPUT_DIR", workspace.validated_units
    )
    monkeypatch.setattr(
        assembler_module,
        "CANONICAL_DOCUMENTS_OUTPUT_DIR",
        workspace.canonical_documents,
    )
    monkeypatch.setattr(
        aggregator_module, "SEMANTIC_LEDGERS_INPUT_DIR", workspace.ledgers
    )
    monkeypatch.setattr(
        aggregator_module,
        "CANONICAL_DOCUMENTS_INPUT_DIR",
        workspace.canonical_documents,
    )
    monkeypatch.setattr(aggregator_module, "METADATA_OUTPUT_DIR", workspace.metadata)

    DocumentAssembler().assemble_lecture(SCRATCH_LECTURE_ID)
    MetadataAggregator().aggregate_lecture(SCRATCH_LECTURE_ID)

    canonical = (
        workspace.canonical_documents / SCRATCH_LECTURE_ID / "canonical_document.json"
    )
    global_index = workspace.metadata / SCRATCH_LECTURE_ID / "global_index.json"
    assert json.loads(canonical.read_text(encoding="utf-8"))["total_units"] == 1
    assert json.loads(global_index.read_text(encoding="utf-8"))["glossary_terms"]


def run_study_guide_script(
    workspace: DryRunWorkspace, environment: dict[str, str]
) -> None:
    # The script points Typst's --root at the working directory, so the
    # working directory is the temporary root that holds every staged source.
    completed = run_bounded_process(
        [
            sys.executable,
            str(GUIDE_SCRIPT),
            SCRATCH_LECTURE_ID,
            "--queue-path",
            str(workspace.queue),
            "--videos-root",
            str(workspace.raw_videos),
            "--evidence-packs-root",
            str(workspace.evidence_packs),
            "--ledgers-root",
            str(workspace.ledgers),
            "--validated-units-root",
            str(workspace.validated_units),
            "--drafts-root",
            str(workspace.drafts),
            "--output-root",
            str(workspace.guides),
            "--private-output-root",
            str(workspace.private_guides),
            "--targets-path",
            str(TARGETS_EXAMPLE),
            "--schedule-path",
            str(DEMO_SCHEDULE),
            "--local-lectures-path",
            str(workspace.registry),
            "--mock-exams-root",
            str(workspace.mock_exams),
            "--podcast-scripts-root",
            str(workspace.podcast_scripts),
            "--podcast-audio-root",
            str(workspace.podcast_audio),
            "--personal-study-flashcards-root",
            str(workspace.flashcards),
            "--figure-captions-root",
            str(workspace.figure_captions),
            "--extracted-slides-root",
            str(workspace.extracted_slides),
        ],
        env=environment,
        timeout_seconds=600,
        maximum_stdout_bytes=PROCESS_OUTPUT_LIMIT_BYTES,
        maximum_stderr_bytes=PROCESS_OUTPUT_LIMIT_BYTES,
        cwd=workspace.root,
    )
    assert completed.returncode == 0, completed.stderr


def generate_site_from_guides(workspace: DryRunWorkspace) -> None:
    generate_site(
        study_guides_root=workspace.guides,
        output_dir=workspace.site,
        video_library_dir=workspace.site_videos,
        mock_exams_dir=workspace.mock_exams,
        academic_schedule_path=DEMO_SCHEDULE,
    )


def write_site_access(workspace: DryRunWorkspace, password: str) -> None:
    workspace.credentials.write_text(
        f"{DEMO_EMAIL}:{hash_password(password)}\n", encoding="utf-8"
    )
    workspace.session_key.write_bytes(secrets.token_bytes(48))


def signed_in_app(workspace: DryRunWorkspace) -> ASGIApp:
    return create_app(
        workspace.site,
        workspace.credentials,
        workspace.site_videos,
        workspace.session_key,
        workspace.mock_exams,
    )


def load_demo_site_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("demo_site", DEMO_SITE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

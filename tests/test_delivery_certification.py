from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Never

import pytest

from lecture_study.exams import compiler as exam_compiler_module
from lecture_study.exams.compiler import ExamCompiler
from lecture_study.flashcards import generator as flashcard_generator_module
from lecture_study.flashcards.generator import AnkiDeckGenerator
from lecture_study.podcast import compiler as podcast_compiler_module
from lecture_study.podcast.compiler import PodcastScriptCompiler
from lecture_study.publishing import pipeline as publishing_pipeline_module
from lecture_study.publishing.pipeline import PublishingOrchestrator
from lecture_study.quality import DerivativeQualityError, QualityCertificationError
from lecture_study.tts import generator as tts_generator_module
from lecture_study.tts.generator import PodcastAudioGenerator
from lecture_study.domain.models import (
    MockExam,
    PodcastLine,
    PodcastScript,
)

pytestmark = pytest.mark.real_certification

_LECTURE_ID = "lecture-001"


def _rejecting_gate(calls: list[str]) -> Callable[[str], Never]:
    def reject(lecture_id: str) -> Never:
        calls.append(lecture_id)
        raise QualityCertificationError("material needs review")

    return reject


def _rejecting_derivative_gate(
    calls: list[str],
) -> Callable[[MockExam | PodcastScript], Never]:
    def reject(artifact: MockExam | PodcastScript) -> Never:
        calls.append(str(artifact.lecture_id))
        raise DerivativeQualityError("derivative needs review")

    return reject


class _RejectingSourceVerifier:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def verify(self, lecture_id: str) -> Never:
        self._calls.append(lecture_id)
        raise QualityCertificationError("publication needs review")


class _RejectingDerivativeVerifier:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def verify_exam(self, exam: MockExam) -> Never:
        self._reject(str(exam.lecture_id))

    def verify_podcast_script(self, script: PodcastScript) -> Never:
        self._reject(str(script.lecture_id))

    def _reject(self, lecture_id: str) -> Never:
        self._calls.append(lecture_id)
        raise DerivativeQualityError("derivative needs review")


def test_publishing_rejects_before_creating_output_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    output_root = tmp_path / "published"
    monkeypatch.setattr(
        publishing_pipeline_module,
        "require_publication_ready",
        _rejecting_gate(calls),
    )
    monkeypatch.setattr(
        publishing_pipeline_module,
        "PUBLISHED_OUTPUT_DIR",
        output_root,
    )

    with pytest.raises(QualityCertificationError, match="needs review"):
        PublishingOrchestrator().publish_lecture(_LECTURE_ID)

    assert calls == [_LECTURE_ID]
    assert not output_root.exists()


def test_flashcards_reject_before_creating_output_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    output_root = tmp_path / "flashcards"
    monkeypatch.setattr(
        flashcard_generator_module,
        "require_publication_ready",
        _rejecting_gate(calls),
    )
    monkeypatch.setattr(
        flashcard_generator_module,
        "FLASHCARDS_OUTPUT_DIR",
        output_root,
    )

    with pytest.raises(QualityCertificationError, match="needs review"):
        AnkiDeckGenerator().generate_deck(_LECTURE_ID)

    assert calls == [_LECTURE_ID]
    assert not output_root.exists()


def test_exam_compilation_rejects_before_creating_output_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    output_root = tmp_path / "exams"
    monkeypatch.setattr(exam_compiler_module, "EXAMS_OUTPUT_DIR", output_root)

    with pytest.raises(DerivativeQualityError, match="needs review"):
        ExamCompiler(
            quality_certificate_verifier=_RejectingDerivativeVerifier(calls)
        ).compile(MockExam(lecture_id=_LECTURE_ID, questions=[]))

    assert calls == [_LECTURE_ID]
    assert not output_root.exists()


def test_podcast_script_compilation_rejects_before_creating_output_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    output_root = tmp_path / "podcast-scripts"
    monkeypatch.setattr(
        podcast_compiler_module,
        "PODCAST_SCRIPTS_OUTPUT_DIR",
        output_root,
    )
    script = PodcastScript(
        lecture_id=_LECTURE_ID,
        title="Certified script",
        dialogue=[
            PodcastLine(
                speaker="Host",
                text="Introduction.",
                source_unit_id="unit-001",
            )
        ],
    )

    with pytest.raises(DerivativeQualityError, match="needs review"):
        PodcastScriptCompiler(
            quality_certificate_verifier=_RejectingDerivativeVerifier(calls)
        ).compile(script)

    assert calls == [_LECTURE_ID]
    assert not output_root.exists()


def test_tts_rejects_uncertified_script_before_output_or_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    output_root = tmp_path / "podcast-audio"
    monkeypatch.setattr(
        tts_generator_module,
        "require_podcast_script_ready",
        _rejecting_derivative_gate(calls),
    )
    script = PodcastScript(
        lecture_id=_LECTURE_ID,
        title="Certified-source candidate",
        dialogue=[
            PodcastLine(
                speaker="Host",
                text="Clinical content",
                source_unit_id="unit-1",
            )
        ],
    )
    monkeypatch.setattr(
        PodcastAudioGenerator,
        "_load_script",
        lambda _self, _lecture_id: script,
    )
    monkeypatch.setattr(tts_generator_module, "AUDIO_OUTPUT_DIR", output_root)

    with pytest.raises(DerivativeQualityError, match="needs review"):
        PodcastAudioGenerator().generate_podcast(_LECTURE_ID)

    assert calls == [_LECTURE_ID]
    assert not output_root.exists()

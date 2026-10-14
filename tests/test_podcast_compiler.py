"""PodcastScript contract and PodcastScriptCompiler tests (extracted from
tests/test_podcast.py when the local llama.cpp podcast generator was
removed under ADR 0038)."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from lecture_study.domain.models import (
    ArtifactExecutionMode,
    PodcastLine,
    PodcastScript,
)
from lecture_study.podcast import compiler as compiler_module
from lecture_study.podcast.compiler import PodcastScriptCompiler
from lecture_study.quality.derivative import DerivativeQualityError


class _AllowingPodcastQualityVerifier:
    def verify_podcast_script(self, _script: PodcastScript) -> None:
        return None


class _RejectingPodcastQualityVerifier:
    def verify_podcast_script(self, _script: PodcastScript) -> None:
        raise DerivativeQualityError("podcast certificate rejected")


def _podcast_script_compiler() -> PodcastScriptCompiler:
    return PodcastScriptCompiler(
        quality_certificate_verifier=_AllowingPodcastQualityVerifier()
    )


def _line_payload(
    speaker: str = "Host",
    text: str = "O que aconteceu com o paciente de 8 anos?",
    source_unit_id: str = "unit-001",
) -> dict[str, object]:
    return {"speaker": speaker, "text": text, "source_unit_id": source_unit_id}


def _make_script(lecture_id: str = "aula-001") -> PodcastScript:
    return PodcastScript(
        lecture_id=lecture_id,
        title="Bradicardia em cães idosos",
        dialogue=[
            PodcastLine(**_line_payload(speaker="Host")),
            PodcastLine(
                **_line_payload(
                    speaker="Expert",
                    text="A bradicardia sinusal exige monitoramento.",
                )
            ),
        ],
    )


class TestPodcastLineContract:
    def test_rejects_speaker_outside_host_expert_literal(self):
        with pytest.raises(ValidationError):
            PodcastLine(**_line_payload(speaker="Narrator"))

    def test_accepts_host_and_expert_speakers(self):
        assert PodcastLine(**_line_payload(speaker="Host")).speaker == "Host"
        assert PodcastLine(**_line_payload(speaker="Expert")).speaker == "Expert"

    def test_rejects_blank_text(self):
        with pytest.raises(ValidationError, match="blank"):
            PodcastLine(**_line_payload(text="   "))


class TestPodcastScriptContract:
    def test_rejects_empty_dialogue(self):
        with pytest.raises(ValidationError):
            PodcastScript(lecture_id="aula-001", title="Titulo", dialogue=[])

    def test_rejects_blank_title(self):
        with pytest.raises(ValidationError, match="blank"):
            PodcastScript(
                lecture_id="aula-001",
                title="   ",
                dialogue=[PodcastLine(**_line_payload())],
            )

    def test_accepts_a_single_dialogue_line_and_a_non_blank_title(self):
        script = PodcastScript(
            lecture_id="aula-001",
            title="Titulo",
            dialogue=[PodcastLine(**_line_payload())],
        )

        assert len(script.dialogue) == 1


class TestPodcastScriptCompiler:
    def test_compile_rejects_before_creating_output_directory(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        output_root = tmp_path / "podcast"
        monkeypatch.setattr(
            compiler_module,
            "PODCAST_SCRIPTS_OUTPUT_DIR",
            output_root,
        )
        compiler = PodcastScriptCompiler(
            quality_certificate_verifier=_RejectingPodcastQualityVerifier()
        )

        with pytest.raises(DerivativeQualityError, match="certificate rejected"):
            compiler.compile(_make_script())

        assert not output_root.exists()

    def test_compile_skips_derivative_verification_for_uncertified_marker(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # ADR 0024: the bypass is a branch on the payload's own execution
        # mode -- a rejecting verifier double proves the call is skipped
        # entirely, not merely made to pass.
        monkeypatch.setattr(compiler_module, "PODCAST_SCRIPTS_OUTPUT_DIR", tmp_path)
        script = _make_script().model_copy(
            update={"execution_mode": ArtifactExecutionMode.UNCERTIFIED_PERSONAL_STUDY}
        )
        compiler = PodcastScriptCompiler(
            quality_certificate_verifier=_RejectingPodcastQualityVerifier()
        )

        output_dir = compiler.compile(script)

        script_json = json.loads((output_dir / "script.json").read_text())
        assert (
            script_json["execution_mode"]
            == ArtifactExecutionMode.UNCERTIFIED_PERSONAL_STUDY.value
        )

    def test_compile_persists_raw_json_round_trippable_to_the_same_script(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(compiler_module, "PODCAST_SCRIPTS_OUTPUT_DIR", tmp_path)
        script = _make_script()

        output_dir = _podcast_script_compiler().compile(script)

        assert output_dir == tmp_path / "aula-001"
        persisted = PodcastScript.model_validate_json(
            (output_dir / "script.json").read_text()
        )
        assert persisted == script

    def test_compile_renders_title_and_host_expert_lines_with_source_annotation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(compiler_module, "PODCAST_SCRIPTS_OUTPUT_DIR", tmp_path)
        script = _make_script()

        output_dir = _podcast_script_compiler().compile(script)

        markdown = (output_dir / "script_legivel.md").read_text()
        assert markdown.startswith("# Bradicardia em cães idosos")
        assert '**Host:** "O que aconteceu' in markdown
        assert '**Expert:** "A bradicardia sinusal exige monitoramento."' in markdown
        # source_unit_id contains a "-", which _escape_markdown escapes
        # like any other special character (the same uniform-escaping
        # this compiler applies to every LLM-sourced field).
        assert "*(Fonte: unit\\-001)*" in markdown

    def test_compile_escapes_markdown_special_characters_in_free_text_fields(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        # title/text originate in Phase 14's LLM pass, not markdown this
        # compiler authors -- a stray "#"/blank line must not spoof a
        # heading or fracture script_legivel.md's structure, matching
        # Phase 10-11/13's markdown_compiler.py precedent.
        monkeypatch.setattr(compiler_module, "PODCAST_SCRIPTS_OUTPUT_DIR", tmp_path)
        script = PodcastScript(
            lecture_id="aula-001",
            title="Titulo\n\n## Fake Injected Heading",
            dialogue=[
                PodcastLine(
                    speaker="Host",
                    text="*itálico falso* e # cabeçalho falso",
                    source_unit_id="unit-001",
                )
            ],
        )

        output_dir = _podcast_script_compiler().compile(script)

        markdown = (output_dir / "script_legivel.md").read_text()
        assert "\n## Fake Injected Heading" not in markdown
        assert "\\#\\# Fake Injected Heading" in markdown
        assert "\\*itálico falso\\*" in markdown
        assert "\\# cabeçalho falso" in markdown

    def test_compile_escapes_a_quote_embedded_in_a_dialogue_line(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        # _render_line wraps text in a literal "..." pair; an unescaped
        # quote inside text would otherwise visually close that wrapper
        # early in the rendered transcript.
        monkeypatch.setattr(compiler_module, "PODCAST_SCRIPTS_OUTPUT_DIR", tmp_path)
        script = PodcastScript(
            lecture_id="aula-001",
            title="Titulo",
            dialogue=[
                PodcastLine(
                    speaker="Expert",
                    text='O tutor disse "ele parou de comer" ontem.',
                    source_unit_id="unit-001",
                )
            ],
        )

        output_dir = _podcast_script_compiler().compile(script)

        markdown = (output_dir / "script_legivel.md").read_text()
        assert 'O tutor disse \\"ele parou de comer\\" ontem.' in markdown

    def test_compile_collapses_embedded_newlines_in_free_text_fields(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(compiler_module, "PODCAST_SCRIPTS_OUTPUT_DIR", tmp_path)
        script = PodcastScript(
            lecture_id="aula-001",
            title="Titulo",
            dialogue=[
                PodcastLine(
                    speaker="Host",
                    text="Pergunta\n\n1. Item de lista falso",
                    source_unit_id="unit-001",
                )
            ],
        )

        output_dir = _podcast_script_compiler().compile(script)

        markdown = (output_dir / "script_legivel.md").read_text()
        assert "\n\n1." not in markdown

    def test_compile_creates_lecture_output_directory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(compiler_module, "PODCAST_SCRIPTS_OUTPUT_DIR", tmp_path)
        script = _make_script(lecture_id="aula-999")

        output_dir = _podcast_script_compiler().compile(script)

        assert output_dir.is_dir()
        assert output_dir == tmp_path / "aula-999"

    def test_compile_writes_atomically_leaving_no_tmp_files_behind(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(compiler_module, "PODCAST_SCRIPTS_OUTPUT_DIR", tmp_path)
        script = _make_script()

        output_dir = _podcast_script_compiler().compile(script)

        remaining = sorted(path.name for path in output_dir.iterdir())
        assert remaining == ["script.json", "script_legivel.md"]

    def test_compile_recovers_after_a_partial_previous_write(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(compiler_module, "PODCAST_SCRIPTS_OUTPUT_DIR", tmp_path)
        lecture_dir = tmp_path / "aula-001"
        lecture_dir.mkdir(parents=True)
        (lecture_dir / "script.json").write_text("stale content")
        script = _make_script()

        output_dir = _podcast_script_compiler().compile(script)

        assert "stale content" not in (output_dir / "script.json").read_text()

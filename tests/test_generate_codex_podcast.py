"""Unit tests for the personal-study podcast-script generator on the LLM
gateway (ADR 0046).

No model is ever called: a scripted gateway double replays canned responses.
The tests exercise evidence-pack construction, the lecture_id/unit-id
traceability check (collect-every-offender shape, mirroring
podcast/generator.py's own), the ADR 0027 student-facing-text guard, the
single retry boundary around parsing and validation, the typed call errors
that are never retried (D-22), the oversized-prompt refusal (D-08, D-23), the
per-call timeout override (D-03), the run record and the import boundary that
keeps process code out of the script."""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from lecture_study.domain.models import (
    ArtifactExecutionMode,
    CriticReview,
    LearningUnitDraft,
    PodcastLine,
    PodcastScript,
    ValidatedLearningUnit,
)
from lecture_study.llm import (
    Stage,
)


def _load_module() -> ModuleType:
    script_path = Path(__file__).parents[1] / "scripts" / "generate_codex_podcast.py"
    spec = importlib.util.spec_from_file_location(
        "generate_codex_podcast",
        script_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load generator script from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


def _draft(pack_sequence: int, body: str = "corpo") -> LearningUnitDraft:
    return LearningUnitDraft(
        lecture_id="lecture-001",
        pack_sequence=pack_sequence,
        title=f"Unidade {pack_sequence}",
        body_markdown=body,
        clinical_warnings=[],
    )


def _line(
    speaker: str = "Host", text: str = "Fala clínica.", source_unit_id: str = "unit-001"
) -> PodcastLine:
    return PodcastLine(speaker=speaker, text=text, source_unit_id=source_unit_id)


def _script(**overrides: object) -> PodcastScript:
    defaults: dict[str, object] = {
        "lecture_id": "lecture-001",
        "title": "Episódio",
        "dialogue": [_line()],
    }
    defaults.update(overrides)
    return PodcastScript(**defaults)


def test_build_evidence_pack_tags_each_draft_with_its_unit_id() -> None:
    drafts = (_draft(1, "corpo um"), _draft(2, "corpo dois"))
    unit_ids = (_MODULE._unit_id(1), _MODULE._unit_id(2))

    evidence_pack = _MODULE._build_evidence_pack(drafts, unit_ids)

    assert evidence_pack == "[unit-001] corpo um\n\n[unit-002] corpo dois"


def test_bracket_echoed_unit_ids_are_normalized_before_traceability() -> None:
    # Regression for the first real run (lecture 10004, 2026-09-16):
    # Codex echoed the evidence pack's "[unit-001]" label decoration into
    # source_unit_id and the whole run failed traceability. Normalization
    # strips only the brackets; a genuinely unknown id still fails.
    script = _script(
        dialogue=[
            _line(source_unit_id="[unit-001]"),
            _line(speaker="Expert", source_unit_id="unit-002"),
        ]
    )

    _MODULE._normalize_echoed_unit_ids(script)
    _MODULE._validate_traceability(script, "lecture-001", ("unit-001", "unit-002"))

    assert [line.source_unit_id for line in script.dialogue] == [
        "unit-001",
        "unit-002",
    ]


def test_normalization_does_not_rescue_an_unknown_unit_id() -> None:
    script = _script(dialogue=[_line(source_unit_id="[unit-999]")])

    _MODULE._normalize_echoed_unit_ids(script)

    with pytest.raises(
        _MODULE.LlmResponseContractError, match="not among this lecture"
    ):
        _MODULE._validate_traceability(script, "lecture-001", ("unit-001",))


def test_validate_traceability_accepts_a_correctly_grounded_script() -> None:
    script = _script(dialogue=[_line(source_unit_id="unit-001")])

    _MODULE._validate_traceability(script, "lecture-001", ("unit-001",))


def test_validate_traceability_rejects_a_lecture_id_mismatch() -> None:
    script = _script(lecture_id="lecture-002")

    with pytest.raises(_MODULE.LlmResponseContractError, match="lecture_id"):
        _MODULE._validate_traceability(script, "lecture-001", ("unit-001",))


def test_validate_traceability_collects_every_offending_line() -> None:
    script = _script(
        dialogue=[
            _line(source_unit_id="unit-999"),
            _line(source_unit_id="unit-001"),
            _line(source_unit_id="unit-888"),
        ]
    )

    with pytest.raises(_MODULE.LlmResponseContractError) as excinfo:
        _MODULE._validate_traceability(script, "lecture-001", ("unit-001",))

    message = str(excinfo.value)
    assert "unit-999" in message
    assert "unit-888" in message


def test_reject_text_unfit_for_a_listener_raises_on_a_referring_phrase() -> None:
    script = _script(dialogue=[_line(text="Segundo o material, isso é verdade.")])

    with pytest.raises(_MODULE.LlmResponseContractError, match="unfit for a listener"):
        _MODULE._reject_text_unfit_for_a_listener(script)


def test_reject_text_unfit_for_a_listener_accepts_clean_text() -> None:
    _MODULE._reject_text_unfit_for_a_listener(_script())


def test_an_echoed_execution_mode_member_is_stripped_before_validation() -> None:
    # Regression (lecture 10006, 2026-09-17): the schema embedded in the
    # prompt exposes the enum's existence, Codex echoed a member with an
    # invalid value, and the whole run failed 'execution_mode:
    # is_instance_of'. The field is pipeline-controlled and must never
    # reach validation from model output.
    payload = json.dumps(
        {
            "lecture_id": "lecture-001",
            "title": "Ep",
            "execution_mode": "uncertified personal study",
            "dialogue": [
                {
                    "speaker": "Host",
                    "text": "Fala.",
                    "source_unit_id": "unit-001",
                }
            ],
        }
    ).encode()

    script = _MODULE._parse_script(payload)

    assert script.lecture_id == "lecture-001"
    assert len(script.dialogue) == 1


def test_output_without_execution_mode_passes_through_unchanged() -> None:
    # pt-BR text matters here: a re-encode with default ensure_ascii would
    # alter the bytes, so byte-identity pins that echo-free output is
    # never round-tripped through json at all.
    payload = json.dumps(
        {
            "lecture_id": "lecture-001",
            "title": "Episódio clínico",
            "dialogue": [
                {
                    "speaker": "Host",
                    "text": "Fala clínica sobre bradicardia.",
                    "source_unit_id": "unit-001",
                }
            ],
        }
    ).encode()

    assert _MODULE._strip_execution_mode_echo(payload) == payload


def test_duplicate_members_still_fail_even_with_the_echo_present() -> None:
    # The strip must not become a duplicate-key collapse: a response that
    # echoes execution_mode AND repeats another member passes through as
    # original bytes and fails in the contract layer's own duplicate-key
    # rejection, exactly as it would without the echo.
    payload = (
        b'{"lecture_id": "lecture-001", "title": "Primeiro", '
        b'"title": "Segundo", "execution_mode": "x", "dialogue": []}'
    )

    with pytest.raises(_MODULE.LlmResponseContractError, match="duplicate JSON key"):
        _MODULE._parse_script(payload)


def test_unparseable_output_passes_through_for_its_own_diagnostic() -> None:
    assert _MODULE._strip_execution_mode_echo(b"{not valid json") == b"{not valid json"


_VALID_SCRIPT_BYTES = (
    b'{"lecture_id":"lecture-001","title":"Ep","dialogue":'
    b'[{"speaker":"Host","text":"Fala.","source_unit_id":"unit-001"}]}'
)
_UNIT_IDS = ("unit-001",)


def _generate(gateway: object, *, timeout_seconds: float = 900.0) -> PodcastScript:
    return _MODULE._generate_script(
        gateway=gateway,
        lecture_id="lecture-001",
        evidence_pack="[unit-001] corpo",
        unit_ids=_UNIT_IDS,
        timeout_seconds=timeout_seconds,
    )


def test_generate_script_normalizes_bracket_echoed_ids_end_to_end(
    make_scripted_gateway,
) -> None:
    """Wiring guard: the unit-id normalization inside _validate_script is
    load-bearing. A bracketed "[unit-001]" in the raw response must come
    back as the bare id, or the exact lecture-10004 failure mode returns
    while the unit tests (which call the normalizer directly) stay green."""
    gateway = make_scripted_gateway(
        [
            b'{"lecture_id":"lecture-001","title":"Ep","dialogue":'
            b'[{"speaker":"Host","text":"Fala.","source_unit_id":"[unit-001]"}]}'
        ]
    )

    script = _generate(gateway)

    assert [line.source_unit_id for line in script.dialogue] == ["unit-001"]
    assert len(gateway.requests) == 1


def test_generate_script_strips_an_echoed_execution_mode_inside_the_parse(
    make_scripted_gateway,
) -> None:
    gateway = make_scripted_gateway(
        [
            b'{"lecture_id":"lecture-001","title":"Ep","execution_mode":"x",'
            b'"dialogue":[{"speaker":"Host","text":"Fala.",'
            b'"source_unit_id":"unit-001"}]}'
        ]
    )

    script = _generate(gateway)

    assert script.lecture_id == "lecture-001"
    assert len(gateway.requests) == 1


def test_generate_script_retries_once_then_raises_the_second_failure(
    make_scripted_gateway,
) -> None:
    wrong_lecture = _VALID_SCRIPT_BYTES.replace(b"lecture-001", b"lecture-002")
    gateway = make_scripted_gateway([wrong_lecture, wrong_lecture])

    with pytest.raises(_MODULE.LlmResponseContractError, match="lecture_id"):
        _generate(gateway)

    assert [request.is_retry for request in gateway.requests] == [False, True]
    assert "Retry feedback:" in gateway.requests[1].prompt
    assert "Retry feedback:" not in gateway.requests[0].prompt


def test_generate_script_recovers_when_the_retry_is_valid(
    make_scripted_gateway,
) -> None:
    unfit = _VALID_SCRIPT_BYTES.replace(b"Fala.", b"Segundo o material, sim.")
    gateway = make_scripted_gateway([unfit, _VALID_SCRIPT_BYTES])

    script = _generate(gateway)

    assert script.dialogue[0].text == "Fala."
    assert len(gateway.requests) == 2


def test_the_call_timeout_reaches_every_request(make_scripted_gateway) -> None:
    unfit = _VALID_SCRIPT_BYTES.replace(b"Fala.", b"Segundo o material, sim.")
    gateway = make_scripted_gateway([unfit, _VALID_SCRIPT_BYTES])

    _generate(gateway, timeout_seconds=2700.0)

    assert [request.timeout_seconds for request in gateway.requests] == [2700.0] * 2


def test_the_podcast_stage_is_the_one_requested(make_scripted_gateway) -> None:
    gateway = make_scripted_gateway([_VALID_SCRIPT_BYTES])

    _generate(gateway)

    assert gateway.requests[0].stage is Stage.PODCAST


def test_the_call_timeout_defaults_to_nine_hundred_seconds() -> None:
    args = _MODULE._build_argument_parser().parse_args(["lecture-001"])

    assert args.call_timeout_seconds == 900.0
    assert args.codex_executable is None


def _open_scripted_gateway(
    monkeypatch: pytest.MonkeyPatch, gateway: object
) -> list[Stage]:
    """Replace the module's gateway opener; return the stages it was asked for."""
    opened_stages: list[Stage] = []

    def open_stage_gateway(stage: Stage, **_kwargs: object) -> object:
        opened_stages.append(stage)
        return gateway

    monkeypatch.setattr(_MODULE, "open_stage_gateway", open_stage_gateway)
    return opened_stages


def test_main_reports_an_invalid_lecture_id_cleanly_not_as_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    opened_stages = _open_scripted_gateway(monkeypatch, object())
    monkeypatch.setattr(sys, "argv", ["generate_codex_podcast.py", "../../etc/passwd"])

    exit_code = _MODULE.main()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert opened_stages == []
    assert captured.out == ""
    assert captured.err.startswith("error: ")
    assert "Traceback" not in captured.err


def _write_draft(drafts_root: Path, draft: LearningUnitDraft) -> None:
    lecture_dir = drafts_root / draft.lecture_id
    lecture_dir.mkdir(parents=True, exist_ok=True)
    draft_path = lecture_dir / f"draft_{draft.pack_sequence:03d}.json"
    draft_path.write_text(draft.model_dump_json())


def _write_validated_unit(
    validated_units_root: Path, content: LearningUnitDraft, *, passed: bool
) -> None:
    unit = ValidatedLearningUnit(
        lecture_id=content.lecture_id,
        pack_sequence=content.pack_sequence,
        content=content,
        review=CriticReview(
            passed=passed, hallucination_score=0.0, feedback="Fundamentado."
        ),
    )
    lecture_dir = validated_units_root / unit.lecture_id
    lecture_dir.mkdir(parents=True, exist_ok=True)
    unit_path = lecture_dir / f"unit_{unit.pack_sequence:03d}.json"
    unit_path.write_text(unit.model_dump_json())


def _write_approved_unit(tmp_path: Path, body: str = "corpo") -> None:
    _write_draft(tmp_path / "08_drafts", _draft(1, body))
    _write_validated_unit(tmp_path / "09_validated_units", _draft(1, body), passed=True)


def _command_line(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "generate_codex_podcast.py",
        "lecture-001",
        "--validated-units-root",
        str(tmp_path / "09_validated_units"),
        "--drafts-root",
        str(tmp_path / "08_drafts"),
        "--llm-runs-root",
        str(tmp_path / "llm_runs"),
        *extra,
    ]


def _run_record(tmp_path: Path) -> dict[str, object]:
    record_path = tmp_path / "llm_runs" / "lecture-001" / "podcast.json"
    return json.loads(record_path.read_text(encoding="utf-8"))


def _run_main_with_a_stubbed_generator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, gateway: object
) -> tuple[int, list[str], list[PodcastScript], list[Stage]]:
    """Runs main() on lecture-001 under tmp_path's roots, with the model
    call and the output write replaced; returns the exit code, the evidence
    packs the model would have received, the scripts handed to the compiler
    and the stages the gateway opener was asked for."""
    evidence_packs: list[str] = []
    compiled_scripts: list[PodcastScript] = []

    def fake_generate_script(*, evidence_pack: str, **_kwargs: object) -> PodcastScript:
        evidence_packs.append(evidence_pack)
        return _script()

    def fake_compile(_compiler: object, script: PodcastScript) -> Path:
        compiled_scripts.append(script)
        return tmp_path / "output"

    opened_stages = _open_scripted_gateway(monkeypatch, gateway)
    monkeypatch.setattr(sys, "argv", _command_line(tmp_path))
    monkeypatch.setattr(_MODULE, "_generate_script", fake_generate_script)
    monkeypatch.setattr(_MODULE.PodcastScriptCompiler, "compile", fake_compile)
    return _MODULE.main(), evidence_packs, compiled_scripts, opened_stages


def test_main_grounds_the_podcast_on_the_critic_approved_unit_content(
    make_scripted_gateway, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # ADR 0045: the repaired unit content is what the critic approved; the
    # Phase 6 first draft it replaced must never reach the prompt.
    _write_draft(tmp_path / "08_drafts", _draft(1, "texto do rascunho original"))
    _write_validated_unit(
        tmp_path / "09_validated_units",
        _draft(1, "texto reparado e aprovado"),
        passed=True,
    )

    exit_code, evidence_packs, compiled_scripts, opened_stages = (
        _run_main_with_a_stubbed_generator(
            monkeypatch, tmp_path, make_scripted_gateway([])
        )
    )

    assert exit_code == 0
    assert evidence_packs == ["[unit-001] texto reparado e aprovado"]
    assert len(compiled_scripts) == 1
    assert opened_stages == [Stage.PODCAST]


def test_main_blocks_a_lecture_with_a_unit_the_critic_rejected(
    make_scripted_gateway,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for pack_sequence, passed in ((1, True), (2, False)):
        _write_draft(tmp_path / "08_drafts", _draft(pack_sequence))
        _write_validated_unit(
            tmp_path / "09_validated_units", _draft(pack_sequence), passed=passed
        )

    exit_code, evidence_packs, compiled_scripts, opened_stages = (
        _run_main_with_a_stubbed_generator(
            monkeypatch, tmp_path, make_scripted_gateway([])
        )
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert evidence_packs == []
    assert compiled_scripts == []
    assert opened_stages == []
    assert not (tmp_path / "llm_runs").exists()
    assert captured.out == ""
    assert captured.err.startswith("error: aula bloqueada")
    assert "pack_sequence 2" in captured.err
    assert "Traceback" not in captured.err


def test_main_produces_the_uncertified_script_and_a_run_record(
    make_scripted_gateway,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Tracer: critic gate, gateway, parse with echo strip, validation chain,
    # compiler and run record, end to end with only the model scripted.
    _write_approved_unit(tmp_path)
    gateway = make_scripted_gateway(
        [
            b'{"lecture_id":"lecture-001","title":"Episodio clinico",'
            b'"execution_mode":"uncertified personal study","dialogue":'
            b'[{"speaker":"Host","text":"Qual a conduta inicial?",'
            b'"source_unit_id":"[unit-001]"},'
            b'{"speaker":"Expert","text":"Estabilizar o paciente primeiro.",'
            b'"source_unit_id":"unit-001"}]}'
        ]
    )
    opened_stages = _open_scripted_gateway(monkeypatch, gateway)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", _command_line(tmp_path))

    exit_code = _MODULE.main()

    assert exit_code == 0, capsys.readouterr().err
    assert opened_stages == [Stage.PODCAST]
    assert [request.timeout_seconds for request in gateway.requests] == [900.0]
    output_dir = tmp_path / "workspace" / "data" / "15_podcast_scripts" / "lecture-001"
    compiled = json.loads((output_dir / "script.json").read_text(encoding="utf-8"))
    assert compiled["execution_mode"] == (
        ArtifactExecutionMode.UNCERTIFIED_PERSONAL_STUDY.value
    )
    assert [line["source_unit_id"] for line in compiled["dialogue"]] == [
        "unit-001",
        "unit-001",
    ]
    assert (output_dir / "script_legivel.md").exists()
    record = _run_record(tmp_path)
    assert record["status"] == "succeeded"
    assert record["script_stage"] == "podcast"
    assert record["gateways"][0]["calls"] == 1


def _imported_module_names(source_text: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source_text)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names

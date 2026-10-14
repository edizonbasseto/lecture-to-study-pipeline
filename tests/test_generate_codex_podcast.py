"""Unit tests for the Codex-based personal-study podcast-script
generator.

The `codex` subprocess itself is never invoked here -- these tests stub
`run_bounded_process` and exercise the pure logic: evidence-pack
construction, the lecture_id/unit-id traceability check (collect-every-
offender shape, mirroring podcast/generator.py's own), the ADR 0027
student-facing-text guard, and the subprocess-failure-to-
LlmResponseContractError wrapping."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

from lecture_study.domain.models import LearningUnitDraft, PodcastLine, PodcastScript


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


def test_call_codex_for_script_wraps_a_nonzero_exit_as_a_contract_error() -> None:
    def fake_run_bounded_process(args, **_kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd=args)

    with (
        patch.object(
            _MODULE, "run_bounded_process", side_effect=fake_run_bounded_process
        ),
        pytest.raises(_MODULE.LlmResponseContractError, match="codex exec failed"),
    ):
        _MODULE._call_codex_for_script(
            codex_executable="codex",
            environment={},
            prompt="prompt",
            lecture_id="lecture-001",
            unit_ids=("unit-001",),
        )


def test_call_codex_for_script_wraps_a_timeout_as_a_contract_error() -> None:
    def fake_run_bounded_process(args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=args, timeout=kwargs.get("timeout_seconds", 1)
        )

    with (
        patch.object(
            _MODULE, "run_bounded_process", side_effect=fake_run_bounded_process
        ),
        pytest.raises(_MODULE.LlmResponseContractError, match="codex exec failed"),
    ):
        _MODULE._call_codex_for_script(
            codex_executable="codex",
            environment={},
            prompt="prompt",
            lecture_id="lecture-001",
            unit_ids=("unit-001",),
        )


def test_call_codex_for_script_wraps_an_output_limit_breach_as_a_contract_error() -> (
    None
):
    def fake_run_bounded_process(args, **_kwargs):
        raise _MODULE.ProcessOutputLimitExceeded("stdout", 16 * 1024 * 1024)

    with (
        patch.object(
            _MODULE, "run_bounded_process", side_effect=fake_run_bounded_process
        ),
        pytest.raises(_MODULE.LlmResponseContractError, match="codex exec failed"),
    ):
        _MODULE._call_codex_for_script(
            codex_executable="codex",
            environment={},
            prompt="prompt",
            lecture_id="lecture-001",
            unit_ids=("unit-001",),
        )


def test_call_codex_for_script_runs_with_a_scratch_cwd_not_the_repo_root() -> None:
    """Same security posture as generate_codex_mock_exam.py's own
    equivalent test: the subprocess must never run with the repository
    checkout (which holds .env) as its cwd/-C working root."""

    captured: dict[str, object] = {}

    def fake_run_bounded_process(args, *, cwd, **kwargs):
        captured["cwd"] = cwd
        captured["dash_c"] = args[args.index("-C") + 1]
        output_index = args.index("-o") + 1
        Path(args[output_index]).write_bytes(
            b'{"lecture_id":"lecture-001","title":"Ep","dialogue":'
            b'[{"speaker":"Host","text":"Fala.","source_unit_id":"unit-001"}]}'
        )

    with patch.object(
        _MODULE, "run_bounded_process", side_effect=fake_run_bounded_process
    ):
        script = _MODULE._call_codex_for_script(
            codex_executable="codex",
            environment={},
            prompt="prompt",
            lecture_id="lecture-001",
            unit_ids=("unit-001",),
        )

    repo_root = Path(__file__).parents[1].resolve()
    assert script.lecture_id == "lecture-001"
    assert Path(captured["cwd"]).resolve() != repo_root
    assert str(captured["cwd"]) == captured["dash_c"]


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


def test_call_codex_for_script_normalizes_bracket_echoed_ids_end_to_end() -> None:
    """Wiring guard: the _normalize_echoed_unit_ids call inside
    _call_codex_for_script is load-bearing. A bracketed "[unit-001]" in
    the raw codex response must come back as the bare id, or the exact
    lecture-10004 failure mode returns while the unit tests (which call
    the normalizer directly) stay green."""

    def fake_run_bounded_process(args, **_kwargs):
        output_index = args.index("-o") + 1
        Path(args[output_index]).write_bytes(
            b'{"lecture_id":"lecture-001","title":"Ep","dialogue":'
            b'[{"speaker":"Host","text":"Fala.","source_unit_id":"[unit-001]"}]}'
        )

    with patch.object(
        _MODULE, "run_bounded_process", side_effect=fake_run_bounded_process
    ):
        script = _MODULE._call_codex_for_script(
            codex_executable="codex",
            environment={},
            prompt="prompt",
            lecture_id="lecture-001",
            unit_ids=("unit-001",),
        )

    assert [line.source_unit_id for line in script.dialogue] == ["unit-001"]


def test_generate_script_retries_once_then_raises_the_second_failure() -> None:
    calls: list[str] = []

    def fake_call(*, prompt, **_kwargs):
        calls.append(prompt)
        raise _MODULE.LlmResponseContractError(
            "still broken", retry_category="contract"
        )

    with (
        patch.object(_MODULE, "_call_codex_for_script", side_effect=fake_call),
        pytest.raises(_MODULE.LlmResponseContractError, match="still broken"),
    ):
        _MODULE._generate_script(
            codex_executable="codex",
            environment={},
            lecture_id="lecture-001",
            evidence_pack="[unit-001] corpo",
            unit_ids=("unit-001",),
        )

    assert len(calls) == 2
    assert "Retry feedback:" in calls[1]


def test_main_reports_an_invalid_lecture_id_cleanly_not_as_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["generate_codex_podcast.py", "../../etc/passwd"])

    exit_code = _MODULE.main()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.startswith("error: ")
    assert "Traceback" not in captured.err

"""The private-denylist scanner behind the git hooks and the CI history
scan: it must catch disguised spellings and never echo a denylisted term."""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "denylist_scan.py"
_spec = importlib.util.spec_from_file_location("denylist_scan", _SCRIPT)
assert _spec is not None and _spec.loader is not None
denylist_scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(denylist_scan)

_TERMS = ["Instituto Exemplo", "segredo"]


@pytest.mark.parametrize(
    "text",
    [
        "texto com segredo no meio",
        "TEXTO COM SEGREDO",
        "texto com ségrêdo acentuado",
        "texto com seg​redo invisível",
        "o instituto\n+exemplo quebrado entre linhas",
        "Instituto-Exemplo com hífen",
        "texto com ｓｅｇｒｅｄｏ em largura total",
    ],
)
def test_disguised_spellings_are_matched(text: str) -> None:
    assert denylist_scan.matching_entry_numbers(text, _TERMS)


def test_clean_text_matches_nothing() -> None:
    assert denylist_scan.matching_entry_numbers("material de estudo", _TERMS) == []


def test_matches_are_reported_by_entry_number_only() -> None:
    assert denylist_scan.matching_entry_numbers("um segredo", _TERMS) == [2]


def test_an_empty_denylist_is_refused(tmp_path: Path) -> None:
    denylist = tmp_path / "denylist.txt"
    denylist.write_text("# only a comment\n\n", encoding="utf-8")

    with pytest.raises(denylist_scan.DenylistUnavailableError):
        denylist_scan.load_terms(denylist)


def test_a_missing_denylist_is_refused(tmp_path: Path) -> None:
    with pytest.raises(denylist_scan.DenylistUnavailableError):
        denylist_scan.load_terms(tmp_path / "absent.txt")


def test_main_exits_nonzero_without_echoing_the_term(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    denylist = tmp_path / "denylist.txt"
    denylist.write_text("segredo\n", encoding="utf-8")
    monkeypatch.setattr("sys.stdin", _StdinWithBuffer(b"um segredo aqui"))

    exit_code = denylist_scan.main([str(denylist)])

    captured = capsys.readouterr()
    assert exit_code == denylist_scan.MATCH_EXIT_CODE
    assert "segredo" not in captured.out + captured.err


class _StdinWithBuffer:
    def __init__(self, payload: bytes) -> None:
        import io

        self.buffer = io.BytesIO(payload)

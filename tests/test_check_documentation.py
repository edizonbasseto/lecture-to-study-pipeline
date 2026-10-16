"""DOC-02: the static checker that keeps the documented commands, paths, links,
ADR numbers and quoted ADR statements true to the repository."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_documentation.py"
_spec = importlib.util.spec_from_file_location("check_documentation", _SCRIPT)
assert _spec is not None and _spec.loader is not None
check_documentation = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = check_documentation
_spec.loader.exec_module(check_documentation)

_MAKEFILE = """\
.PHONY: build demo-run \\
\tclean

ADAPTER ?= llama-cpp
HOOKS := pre-commit commit-msg
DEMO_VIDEO := workspace/demo/video.mp4

build:
\techo "note: a colon in a recipe"
\tuv run python -m package.module: argument

demo-run: build
\tuv run python scripts/run.py --adapter $(ADAPTER)
"""

_FACTS = check_documentation.read_makefile_facts(_MAKEFILE)


def _fenced(language: str, *lines: str) -> str:
    return "\n".join([f"```{language}", *lines, "```", ""])


def _words(commands: Iterable[Any]) -> list[tuple[str, ...]]:
    return [command.words for command in commands]


def _write_repository(root: Path, readme_text: str, makefile_text: str) -> None:
    (root / "Makefile").write_text(makefile_text, encoding="utf-8")
    (root / "README.md").write_text(readme_text, encoding="utf-8")


def _findings_for(root: Path, readme_text: str) -> list[str]:
    _write_repository(root, readme_text, _MAKEFILE)
    report = check_documentation.check_documents(root, [Path("README.md")])
    return [finding.rule + " " + finding.detail for finding in report.findings]


class TestReadMakefileFacts:
    def test_targets_include_those_with_prerequisites(self) -> None:
        assert _FACTS.targets == {"build", "demo-run"}

    def test_variables_include_every_assignment_operator(self) -> None:
        assert _FACTS.variables == {"ADAPTER", "HOOKS", "DEMO_VIDEO"}

    def test_phony_lines_and_recipe_lines_are_not_facts(self) -> None:
        assert "clean" not in _FACTS.targets
        assert "note" not in _FACTS.targets
        assert "package.module" not in _FACTS.targets


class TestFencedCommandLines:
    def test_a_continuation_is_joined_and_reports_the_first_line(self) -> None:
        text = "intro\n" + _fenced("bash", "make demo-run \\", "  ADAPTER=codex")
        (command,) = check_documentation.fenced_command_lines(text)
        assert command.line_number == 3
        assert command.words == ("make", "demo-run", "ADAPTER=codex")

    def test_a_prompt_is_removed_and_comments_and_blank_lines_are_dropped(
        self,
    ) -> None:
        text = _fenced("sh", "# a comment", "", "$ make build")
        commands = check_documentation.fenced_command_lines(text)
        assert _words(commands) == [("make", "build")]

    def test_other_fences_are_ignored(self) -> None:
        text = _fenced("mermaid", "make build") + _fenced("", "make build")
        assert check_documentation.fenced_command_lines(text) == ()

    def test_a_console_fence_keeps_only_prompted_lines(self) -> None:
        text = _fenced("console", "$ make build", "build finished")
        commands = check_documentation.fenced_command_lines(text)
        assert _words(commands) == [("make", "build")]

    def test_chained_commands_become_separate_command_lines(self) -> None:
        text = _fenced("bash", "make build && make demo-run | tee log.txt")
        commands = check_documentation.fenced_command_lines(text)
        assert _words(commands) == [
            ("make", "build"),
            ("make", "demo-run"),
            ("tee", "log.txt"),
        ]

    def test_a_line_the_splitter_rejects_has_no_words(self) -> None:
        text = _fenced("bash", 'make "build')
        (command,) = check_documentation.fenced_command_lines(text)
        assert command.words == ()


class TestMakeRule:
    def test_existing_targets_and_variables_pass(self, tmp_path: Path) -> None:
        text = _fenced("bash", "make demo-run ADAPTER=codex")
        assert _findings_for(tmp_path, text) == []

    def test_an_unknown_target_is_reported_by_name(self, tmp_path: Path) -> None:
        text = _fenced("bash", "make absent-target")
        assert _findings_for(tmp_path, text) == ["make-target absent-target"]

    def test_an_unknown_variable_is_reported_by_name(self, tmp_path: Path) -> None:
        text = _fenced("bash", "make build MISSING=1")
        assert _findings_for(tmp_path, text) == ["make-variable MISSING"]

    def test_dash_options_are_ignored(self, tmp_path: Path) -> None:
        text = _fenced("bash", "make --dry-run -j4 build")
        assert _findings_for(tmp_path, text) == []

    def test_several_targets_on_one_line_are_each_checked(self, tmp_path: Path) -> None:
        text = _fenced("bash", "make build first-missing second-missing")
        assert _findings_for(tmp_path, text) == [
            "make-target first-missing",
            "make-target second-missing",
        ]

    def test_an_unparseable_command_is_reported(self, tmp_path: Path) -> None:
        text = _fenced("bash", 'make "build')
        assert _findings_for(tmp_path, text) == [
            "unparseable-command the command line cannot be split into words"
        ]


def _run_checker(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


class TestCheckerExitCodes:
    def test_exit_codes_end_to_end(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("build:\n\ttrue\n", encoding="utf-8")
        readme = tmp_path / "README.md"

        readme.write_text(_fenced("bash", "make build"), encoding="utf-8")
        clean = _run_checker(tmp_path)
        assert clean.returncode == 0
        assert "1 command line(s), 0 finding(s)" in clean.stdout

        readme.write_text(_fenced("bash", "make missing-target"), encoding="utf-8")
        findings = _run_checker(tmp_path)
        assert findings.returncode == 1
        assert "make-target: missing-target" in findings.stdout

        readme.unlink()
        unreadable = _run_checker(tmp_path)
        assert unreadable.returncode == 2
        assert "README.md" in unreadable.stderr
        assert str(tmp_path) not in unreadable.stderr

"""DOC-02: the static checker that keeps the documented commands, paths, links,
ADR numbers and quoted ADR statements true to the repository."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
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


_SAMPLE_TOOL = """\
parser.add_argument("--adapter")
parser.add_argument("--lecture-id")
"""
_SAMPLE_CLI = """\
COMMANDS = ["run-all", "single"]
parser.add_argument("--whisper-model-path")
"""
_SAMPLE_DECISION = """\
# 0045 Sample decision

The student-facing notice reads:

> Study material generated
> without certification.

A wrapped
sentence stays in the record.
"""
_REPOSITORY_FILES = {
    "scripts/tool.py": _SAMPLE_TOOL,
    "scripts/run.sh": "# flags: --verbose\n",
    "src/lecture_study/__init__.py": "",
    "src/lecture_study/cli/__init__.py": "",
    "src/lecture_study/cli/main.py": _SAMPLE_CLI,
    "docs/guide.md": "# Guide\n",
    "docs/decisions/0045-sample-decision.md": _SAMPLE_DECISION,
    "docs/decisions/0046-second-decision.md": "# 0046 Second\n",
    "docs/results/report.md": "# Report\n",
    "config/targets.example.yaml": "targets: []\n",
}


def _write_repository(root: Path, readme_text: str) -> None:
    (root / "Makefile").write_text(_MAKEFILE, encoding="utf-8")
    (root / "README.md").write_text(readme_text, encoding="utf-8")
    for relative, content in _REPOSITORY_FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _report_for(root: Path, readme_text: str, *documents: str) -> Any:
    _write_repository(root, readme_text)
    names = [Path(name) for name in documents] or [Path("README.md")]
    return check_documentation.check_documents(root, names)


def _findings_for(root: Path, readme_text: str) -> list[str]:
    report = _report_for(root, readme_text)
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


class TestScriptRule:
    def test_existing_scripts_pass_in_every_invocation_form(
        self, tmp_path: Path
    ) -> None:
        text = _fenced(
            "bash",
            "uv run python scripts/tool.py --adapter x",
            "python3 ./scripts/tool.py",
            "bash scripts/run.sh --verbose",
            "scripts/run.sh",
        )
        assert _findings_for(tmp_path, text) == []

    def test_an_absent_script_is_reported_once(self, tmp_path: Path) -> None:
        text = _fenced("bash", "uv run python scripts/absent.py")
        assert _findings_for(tmp_path, text) == ["script-missing scripts/absent.py"]

    def test_a_flag_absent_from_the_script_is_reported(self, tmp_path: Path) -> None:
        text = _fenced("bash", "python scripts/tool.py --adapter=a --nope=1")
        assert _findings_for(tmp_path, text) == ["flag-missing --nope"]

    def test_the_implicit_help_flag_passes(self, tmp_path: Path) -> None:
        text = _fenced("bash", "python scripts/tool.py --help")
        assert _findings_for(tmp_path, text) == []

    def test_leading_assignments_do_not_hide_the_script(self, tmp_path: Path) -> None:
        text = _fenced("bash", "ADAPTER=x uv run python scripts/gone.py")
        assert _findings_for(tmp_path, text) == ["script-missing scripts/gone.py"]


class TestModuleRule:
    def test_an_existing_module_subcommand_and_flag_pass(self, tmp_path: Path) -> None:
        text = _fenced(
            "bash",
            "uv run python -m lecture_study.cli.main run-all 925100 \\",
            "  --whisper-model-path model.bin",
        )
        assert _findings_for(tmp_path, text) == []

    def test_a_package_directory_counts_as_a_module(self, tmp_path: Path) -> None:
        text = _fenced("bash", "python -m lecture_study.cli")
        assert _findings_for(tmp_path, text) == []

    def test_an_absent_module_is_reported(self, tmp_path: Path) -> None:
        text = _fenced("bash", "python -m lecture_study.missing run-all")
        assert _findings_for(tmp_path, text) == ["module-missing lecture_study.missing"]

    def test_an_unquoted_subcommand_is_reported(self, tmp_path: Path) -> None:
        text = _fenced("bash", "python -m lecture_study.cli.main absent-step")
        assert _findings_for(tmp_path, text) == ["subcommand-missing absent-step"]

    def test_a_flag_absent_from_the_module_is_reported(self, tmp_path: Path) -> None:
        text = _fenced("bash", "python -m lecture_study.cli.main single --absent")
        assert _findings_for(tmp_path, text) == ["flag-missing --absent"]


class TestPathRule:
    def test_existing_paths_in_command_words_pass(self, tmp_path: Path) -> None:
        text = _fenced("bash", "cat docs/guide.md config/targets.example.yaml")
        assert _findings_for(tmp_path, text) == []

    def test_an_absent_path_is_reported(self, tmp_path: Path) -> None:
        text = _fenced("bash", "cat docs/gone.md")
        assert _findings_for(tmp_path, text) == ["path-missing docs/gone.md"]

    def test_a_path_in_a_script_argument_is_checked(self, tmp_path: Path) -> None:
        text = _fenced("bash", "python scripts/tool.py docs/gone.md")
        assert _findings_for(tmp_path, text) == ["path-missing docs/gone.md"]

    def test_placeholders_local_files_and_workspace_are_exempt(
        self, tmp_path: Path
    ) -> None:
        text = _fenced(
            "bash",
            "cat docs/<name>.md docs/*.md config/targets.local.yaml",
            "cat workspace/demo/out.txt tmp/log.txt engines/x.bin .venv/bin/x",
            "cat docs/$NAME.md",
        )
        assert _findings_for(tmp_path, text) == []

    def test_unchecked_programs_are_reported_by_name_and_never_fail(
        self, tmp_path: Path
    ) -> None:
        text = _fenced("bash", "git status", "uv run ruff check .", "make build")
        report = _report_for(tmp_path, text)
        assert report.findings == ()
        assert report.unchecked_programs == ("git", "ruff")


class TestLinkRule:
    def test_existing_links_pass(self, tmp_path: Path) -> None:
        text = '[guide](docs/guide.md) and [top](docs/guide.md#top "title")\n'
        assert _findings_for(tmp_path, text) == []

    def test_an_absent_link_target_is_reported(self, tmp_path: Path) -> None:
        assert _findings_for(tmp_path, "[x](docs/gone.md#part)\n") == [
            "path-missing docs/gone.md"
        ]

    def test_a_link_leaving_the_root_is_reported(self, tmp_path: Path) -> None:
        root = tmp_path / "repository"
        root.mkdir()
        (tmp_path / "outside.md").write_text("outside\n", encoding="utf-8")
        assert _findings_for(root, "[x](../outside.md)\n") == [
            "path-missing ../outside.md"
        ]

    def test_anchors_external_links_and_mail_links_are_skipped(
        self, tmp_path: Path
    ) -> None:
        text = "[a](#section) [b](https://example.org/x) [c](mailto:a@example.org)\n"
        assert _findings_for(tmp_path, text) == []

    def test_links_resolve_relative_to_the_document_directory(
        self, tmp_path: Path
    ) -> None:
        _write_repository(tmp_path, "")
        (tmp_path / "docs" / "page.md").write_text("[g](guide.md)\n", encoding="utf-8")
        report = check_documentation.check_documents(tmp_path, [Path("docs/page.md")])
        assert report.findings == ()


class TestInlineCodePathRule:
    def test_existing_inline_paths_pass(self, tmp_path: Path) -> None:
        assert _findings_for(tmp_path, "See `docs/guide.md` and `make build`.\n") == []

    def test_an_absent_inline_path_is_reported(self, tmp_path: Path) -> None:
        assert _findings_for(tmp_path, "See `docs/gone.md`.\n") == [
            "path-missing docs/gone.md"
        ]

    def test_inline_code_with_several_words_is_not_a_path(self, tmp_path: Path) -> None:
        assert _findings_for(tmp_path, "Run `docs/gone.md --flag`.\n") == []


class TestAdrRule:
    def test_singular_and_plural_references_pass(self, tmp_path: Path) -> None:
        text = "ADR 0045 and ADRs 0045, 0046 and 0045; ADR-0046.\n"
        assert _findings_for(tmp_path, text) == []

    def test_a_missing_number_in_a_list_is_reported(self, tmp_path: Path) -> None:
        assert _findings_for(tmp_path, "ADRs 0045 and 0099\n") == ["adr-missing 0099"]

    def test_a_reference_wrapped_across_lines_reports_its_line(
        self, tmp_path: Path
    ) -> None:
        report = _report_for(tmp_path, "intro\nsee ADR\n0099 here\n")
        assert [(f.line_number, f.rule) for f in report.findings] == [
            (2, "adr-missing")
        ]

    def test_other_four_digit_numbers_are_not_adr_references(
        self, tmp_path: Path
    ) -> None:
        assert _findings_for(tmp_path, "In 2026 the ADR process began.\n") == []


class TestQuoteRule:
    def test_a_quote_matching_a_wrapped_passage_passes(self, tmp_path: Path) -> None:
        text = "> Study material generated without\n> certification.\n"
        assert _findings_for(tmp_path, text) == []

    def test_a_quote_may_come_from_unquoted_adr_prose(self, tmp_path: Path) -> None:
        assert _findings_for(tmp_path, "> A wrapped sentence stays\n") == []

    def test_a_quote_that_reorders_adr_words_is_reported(self, tmp_path: Path) -> None:
        assert _findings_for(tmp_path, "> A sentence wrapped stays\n") == [
            "quote-not-in-adr the passage is not in docs/decisions"
        ]

    def test_an_invented_quote_is_reported(self, tmp_path: Path) -> None:
        text = "intro\n\n> Study material generated with clinical review.\n"
        report = _report_for(tmp_path, text)
        assert [(f.line_number, f.rule) for f in report.findings] == [
            (3, "quote-not-in-adr")
        ]

    def test_separate_blockquotes_are_checked_separately(self, tmp_path: Path) -> None:
        text = "> Study material generated\n\n> without certification.\n"
        assert _findings_for(tmp_path, text) == []

    def test_blockquote_lines_joined_without_a_gap_are_one_passage(
        self, tmp_path: Path
    ) -> None:
        text = "> without certification.\n> Study material generated\n"
        assert [f.rule for f in _report_for(tmp_path, text).findings] == [
            "quote-not-in-adr"
        ]


class TestMachinePathRule:
    def test_each_home_directory_prefix_is_reported_without_echo(
        self, tmp_path: Path
    ) -> None:
        for path in (
            "/Users/someone/project",
            "/home/someone/project",
            "C:\\Users\\someone\\project",
        ):
            report = _report_for(tmp_path, f"Open {path} now.\n")
            assert [f.rule for f in report.findings] == ["machine-path"]
            assert "someone" not in report.findings[0].render()

    def test_urls_and_relative_paths_are_not_home_paths(self, tmp_path: Path) -> None:
        text = "See https://example.org/home/page and docs/users/list.\n"
        assert _findings_for(tmp_path, text) == []


class TestDefaultDocuments:
    def test_readme_comes_first_then_top_level_pages_sorted(
        self, tmp_path: Path
    ) -> None:
        _write_repository(tmp_path, "")
        for name in ("operations.md", "architecture.md"):
            (tmp_path / "docs" / name).write_text("# Page\n", encoding="utf-8")
        names = [
            path.as_posix() for path in check_documentation.default_documents(tmp_path)
        ]
        assert names == [
            "README.md",
            "docs/architecture.md",
            "docs/guide.md",
            "docs/operations.md",
        ]

    def test_readme_is_listed_even_when_absent(self, tmp_path: Path) -> None:
        assert check_documentation.default_documents(tmp_path) == (Path("README.md"),)


_REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


class TestRepositoryDocumentation:
    def test_the_repository_documents_pass_the_check(self) -> None:
        documents = check_documentation.default_documents(_REPOSITORY_ROOT)
        report = check_documentation.check_documents(_REPOSITORY_ROOT, documents)
        rendered = "\n".join(finding.render() for finding in report.findings)
        assert report.findings == (), rendered

    def test_readme_is_among_the_default_documents(self) -> None:
        documents = check_documentation.default_documents(_REPOSITORY_ROOT)
        assert Path("README.md") in documents


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


class TestUnterminatedFence:
    def test_an_unclosed_fence_is_reported_at_its_opening_line(
        self, tmp_path: Path
    ) -> None:
        report = _report_for(tmp_path, "intro\n```bash\nmake nonexistent\n")
        rules = [(f.line_number, f.rule, f.detail) for f in report.findings]
        assert (2, "unterminated-fence", "the code fence is never closed") in rules
        assert any(rule == "make-target" for _, rule, _ in rules)

    def test_closed_fences_report_nothing(self, tmp_path: Path) -> None:
        assert _findings_for(tmp_path, _fenced("bash", "make build")) == []


class TestRedirectionsAndBackground:
    def test_a_descriptor_redirection_leaves_no_stray_word(self) -> None:
        text = _fenced("bash", "make build 2>&1 | tee out.log", "make build >out.log")
        commands = check_documentation.fenced_command_lines(text)
        assert _words(commands) == [
            ("make", "build"),
            ("tee", "out.log"),
            ("make", "build"),
        ]

    def test_a_lone_ampersand_separates_commands(self) -> None:
        commands = check_documentation.fenced_command_lines(
            _fenced("bash", "make build & make demo-run")
        )
        assert _words(commands) == [("make", "build"), ("make", "demo-run")]

    def test_a_target_after_a_background_command_is_still_checked(
        self, tmp_path: Path
    ) -> None:
        text = _fenced("bash", "make build & make missing-target")
        assert _findings_for(tmp_path, text) == ["make-target missing-target"]


class TestProgramsOutsideTheRepository:
    def test_a_third_party_python_module_is_not_a_repository_module(
        self, tmp_path: Path
    ) -> None:
        text = _fenced("bash", "python -m pytest -q", "python3 -m pip install x")
        report = _report_for(tmp_path, text)
        assert report.findings == ()
        assert report.unchecked_programs == ("pip", "pytest")

    def test_absolute_and_home_commands_are_not_repository_scripts(
        self, tmp_path: Path
    ) -> None:
        text = _fenced("bash", "/usr/bin/open README.md", "~/bin/tool --flag")
        assert _findings_for(tmp_path, text) == []

    def test_a_relative_repository_script_is_still_checked(
        self, tmp_path: Path
    ) -> None:
        assert _findings_for(tmp_path, _fenced("bash", "./scripts/gone.sh")) == [
            "script-missing ./scripts/gone.sh"
        ]


class TestUvRunOptions:
    def test_options_before_the_command_are_skipped(self, tmp_path: Path) -> None:
        text = _fenced("bash", "uv run --group dev python scripts/tool.py --adapter x")
        assert _findings_for(tmp_path, text) == []

    def test_the_command_after_options_is_still_checked(self, tmp_path: Path) -> None:
        text = _fenced("bash", "uv run --group dev python scripts/missing.py")
        assert _findings_for(tmp_path, text) == ["script-missing scripts/missing.py"]

    def test_a_flag_without_value_and_the_terminator_are_skipped(
        self, tmp_path: Path
    ) -> None:
        text = _fenced("bash", "uv run --frozen -- python scripts/tool.py")
        assert _findings_for(tmp_path, text) == []

    def test_the_launched_program_is_reported_as_unchecked(
        self, tmp_path: Path
    ) -> None:
        report = _report_for(tmp_path, _fenced("bash", "uv run --group dev ruff ."))
        assert report.unchecked_programs == ("ruff",)


class TestLinkBound:
    def test_a_long_unclosed_bracket_run_is_scanned_quickly(
        self, tmp_path: Path
    ) -> None:
        started = time.perf_counter()
        assert _findings_for(tmp_path, "[" * 20000 + "\n") == []
        assert time.perf_counter() - started < 2.0

    def test_an_ordinary_link_still_matches(self) -> None:
        assert check_documentation._LINK.findall("[a](docs/guide.md)") == [
            "docs/guide.md"
        ]


class TestUnprocessableInput:
    def test_a_nul_byte_in_a_link_exits_two_not_one(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("build:\n\ttrue\n", encoding="utf-8")
        (tmp_path / "README.md").write_text("[x](a\x00b.md)\n", encoding="utf-8")
        result = _run_checker(tmp_path)
        assert result.returncode == 2
        assert "could not be processed" in result.stderr
        assert "Traceback" not in result.stderr

    def test_an_unreadable_decision_file_exits_two(self, tmp_path: Path) -> None:
        _write_repository(tmp_path, "text\n")
        (tmp_path / "docs" / "decisions" / "0099-binary.md").mkdir()
        result = _run_checker(tmp_path)
        assert result.returncode == 2
        assert "Traceback" not in result.stderr


class TestDocumentConfinement:
    def test_a_document_outside_the_root_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "repository"
        root.mkdir()
        (root / "Makefile").write_text("build:\n\ttrue\n", encoding="utf-8")
        outside = tmp_path / "outside.md"
        outside.write_text("clean\n", encoding="utf-8")
        for argument in (str(outside), "../outside.md"):
            result = subprocess.run(
                [sys.executable, str(_SCRIPT), "--root", str(root), argument],
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 2
            assert "outside the root" in result.stderr

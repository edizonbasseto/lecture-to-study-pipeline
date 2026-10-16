"""Check statically that the commands a document names exist as written.

Reads the repository documents and the Makefile and reports every documented
make target or make variable that the Makefile does not define. The check is
read-only: no documented command is executed, no process is started and no
project package is imported. Findings name the document, the line, the rule
and the missing token only, never the surrounding document content.

Knowingly not checked: what a command does, the meaning of its options and
programs that are not make targets, repository scripts or project modules.

Exit codes: 0 clean, 1 findings, 2 unreadable input.
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

RULE_MAKE_TARGET = "make-target"
RULE_MAKE_VARIABLE = "make-variable"
RULE_UNPARSEABLE_COMMAND = "unparseable-command"

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_UNREADABLE = 2

DEFAULT_DOCUMENT = Path("README.md")
DOCS_DIRECTORY = Path("docs")
MAKEFILE_NAME = "Makefile"

SHELL_FENCES = frozenset({"bash", "sh", "shell", "zsh", "console"})
CONSOLE_FENCE = "console"
PROMPT = "$ "
COMMAND_SEPARATORS = frozenset({"&&", "||", ";", "|"})
REDIRECTION_CHARACTERS = frozenset("<>&")

_VARIABLE_LINE = re.compile(r"^([A-Za-z_][\w.-]*)[ \t]*(?:\?=|:=|\+=|=)")
_TARGET_LINE = re.compile(r"^([A-Za-z0-9][\w.-]*(?:[ \t]+[\w.-]+)*)[ \t]*:(?![=:])")
_FENCE_LINE = re.compile(r"^\s*(`{3,}|~{3,})\s*([\w-]*)")
_ASSIGNMENT_WORD = re.compile(r"^([A-Za-z_]\w*)=")


class UnreadableInputError(Exception):
    """Raised when the Makefile or a document cannot be read."""


@dataclass(frozen=True, slots=True)
class Finding:
    document: str
    line_number: int
    rule: str
    detail: str

    def render(self) -> str:
        return f"{self.document}:{self.line_number}: {self.rule}: {self.detail}"


@dataclass(frozen=True, slots=True)
class MakefileFacts:
    targets: frozenset[str]
    variables: frozenset[str]


@dataclass(frozen=True, slots=True)
class CommandLine:
    line_number: int
    words: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Report:
    findings: tuple[Finding, ...]
    document_count: int
    command_count: int
    unchecked_programs: tuple[str, ...]


def read_makefile_facts(makefile_text: str) -> MakefileFacts:
    targets: set[str] = set()
    variables: set[str] = set()
    for line in makefile_text.splitlines():
        variable = _VARIABLE_LINE.match(line)
        if variable:
            variables.add(variable.group(1))
            continue
        target = _TARGET_LINE.match(line)
        if target:
            targets.update(target.group(1).split())
    return MakefileFacts(frozenset(targets), frozenset(variables))


def fenced_command_lines(markdown_text: str) -> tuple[CommandLine, ...]:
    commands: list[CommandLine] = []
    for fence_name, numbered_lines in _shell_blocks(markdown_text):
        for line_number, text in _joined_continuations(numbered_lines):
            commands.extend(_commands_of(line_number, text, fence_name))
    return tuple(commands)


def check_document(root: Path, document: Path, facts: MakefileFacts) -> Report:
    text = _read_text(root / document, document)
    commands = fenced_command_lines(text)
    findings = tuple(_command_findings(document.as_posix(), commands, facts))
    return Report(findings, 1, len(commands), ())


def check_documents(root: Path, documents: Sequence[Path]) -> Report:
    facts = read_makefile_facts(_read_text(root / MAKEFILE_NAME, Path(MAKEFILE_NAME)))
    reports = [check_document(root, document, facts) for document in documents]
    return _combined(reports)


def default_documents(root: Path) -> tuple[Path, ...]:
    top_level_pages = sorted((root / DOCS_DIRECTORY).glob("*.md"))
    return (DEFAULT_DOCUMENT, *(page.relative_to(root) for page in top_level_pages))


def main(argv: list[str]) -> int:
    options = _parse_arguments(argv)
    root = options.root.resolve()
    documents = [Path(name) for name in options.documents] or default_documents(root)
    try:
        report = check_documents(root, documents)
    except UnreadableInputError as error:
        print(f"documentation check refused: {error}", file=sys.stderr)
        return EXIT_UNREADABLE
    _print_report(report)
    return EXIT_FINDINGS if report.findings else EXIT_CLEAN


def _parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check documented commands.")
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("documents", nargs="*", help="document paths under the root")
    return parser.parse_args(argv)


def _print_report(report: Report) -> None:
    for finding in report.findings:
        print(finding.render())
    print(
        f"documentation check: {report.document_count} document(s), "
        f"{report.command_count} command line(s), {len(report.findings)} finding(s)"
    )


def _read_text(path: Path, shown_as: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise UnreadableInputError(f"{shown_as.as_posix()} is not readable") from error


def _combined(reports: Sequence[Report]) -> Report:
    programs = {program for report in reports for program in report.unchecked_programs}
    return Report(
        tuple(finding for report in reports for finding in report.findings),
        sum(report.document_count for report in reports),
        sum(report.command_count for report in reports),
        tuple(sorted(programs)),
    )


def _shell_blocks(
    markdown_text: str,
) -> Iterator[tuple[str, list[tuple[int, str]]]]:
    marker, fence_name, block = "", "", []
    for number, text in enumerate(markdown_text.splitlines(), start=1):
        fence = _FENCE_LINE.match(text)
        if not marker:
            if fence:
                marker, fence_name, block = fence.group(1), fence.group(2).lower(), []
        elif _closes_fence(text, marker):
            if fence_name in SHELL_FENCES:
                yield fence_name, block
            marker = ""
        else:
            block.append((number, text))


def _closes_fence(text: str, marker: str) -> bool:
    stripped = text.strip()
    return stripped.startswith(marker) and not stripped.strip(marker[0])


def _joined_continuations(
    numbered_lines: Sequence[tuple[int, str]],
) -> Iterator[tuple[int, str]]:
    first_number, pending = 0, ""
    for number, text in numbered_lines:
        first_number = first_number or number
        if text.rstrip().endswith("\\"):
            pending += text.rstrip()[:-1] + " "
            continue
        yield first_number, pending + text
        first_number, pending = 0, ""
    if pending:
        yield first_number, pending


def _commands_of(line_number: int, text: str, fence_name: str) -> list[CommandLine]:
    stripped = text.strip()
    if fence_name == CONSOLE_FENCE and not stripped.startswith(PROMPT):
        return []
    stripped = stripped.removeprefix(PROMPT).strip()
    if not stripped or stripped.startswith("#"):
        return []
    try:
        tokens = _shell_tokens(stripped)
    except ValueError:
        return [CommandLine(line_number, ())]
    return [
        CommandLine(line_number, _without_redirections(words))
        for words in _split_on_separators(tokens)
    ]


def _shell_tokens(text: str) -> list[str]:
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _split_on_separators(tokens: Sequence[str]) -> Iterator[tuple[str, ...]]:
    words: list[str] = []
    for token in tokens:
        if token not in COMMAND_SEPARATORS:
            words.append(token)
            continue
        if words:
            yield tuple(words)
        words = []
    if words:
        yield tuple(words)


def _without_redirections(words: tuple[str, ...]) -> tuple[str, ...]:
    kept: list[str] = []
    skip_next = False
    for word in words:
        if skip_next:
            skip_next = False
        elif word and set(word) <= REDIRECTION_CHARACTERS:
            skip_next = True
        else:
            kept.append(word)
    return tuple(kept)


def _command_findings(
    document: str, commands: Sequence[CommandLine], facts: MakefileFacts
) -> Iterator[Finding]:
    for command in commands:
        if not command.words:
            yield Finding(
                document,
                command.line_number,
                RULE_UNPARSEABLE_COMMAND,
                "the command line cannot be split into words",
            )
        elif command.words[0] == "make":
            yield from _make_findings(document, command, facts)


def _make_findings(
    document: str, command: CommandLine, facts: MakefileFacts
) -> Iterator[Finding]:
    for word in command.words[1:]:
        if word.startswith("-"):
            continue
        assignment = _ASSIGNMENT_WORD.match(word)
        if assignment and assignment.group(1) in facts.variables:
            continue
        if assignment:
            yield Finding(
                document, command.line_number, RULE_MAKE_VARIABLE, assignment.group(1)
            )
        elif word not in facts.targets:
            yield Finding(document, command.line_number, RULE_MAKE_TARGET, word)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

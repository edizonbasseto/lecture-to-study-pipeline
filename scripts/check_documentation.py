"""Check statically that what a document names exists as written.

Reads the repository documents and the Makefile and reports a documented make
target, make variable, script, python module, subcommand, long option,
repository path, markdown link or ADR number that does not exist; a blockquote
that does not occur verbatim in docs/decisions; and a machine-specific home
path. The check is read-only: no documented command is executed, no process is
started and no project package is imported. A finding names the document, the
line, the rule and the missing token only, never the surrounding content.

Long options and subcommands are textual presence checks (the option or the
quoted subcommand name occurs in the script or module source), not argparse
introspection. Knowingly not checked: what a command does, the meaning of an
option's value and programs that are not make, repository scripts or modules.

Exit codes: 0 clean, 1 findings, 2 unreadable or unprocessable input (a command
line usage error also exits 2).
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

RULE_MAKE_TARGET = "make-target"
RULE_MAKE_VARIABLE = "make-variable"
RULE_SCRIPT_MISSING = "script-missing"
RULE_MODULE_MISSING = "module-missing"
RULE_SUBCOMMAND_MISSING = "subcommand-missing"
RULE_FLAG_MISSING = "flag-missing"
RULE_PATH_MISSING = "path-missing"
RULE_ADR_MISSING = "adr-missing"
RULE_QUOTE_NOT_IN_ADR = "quote-not-in-adr"
RULE_MACHINE_PATH = "machine-path"
RULE_UNPARSEABLE_COMMAND = "unparseable-command"
RULE_UNTERMINATED_FENCE = "unterminated-fence"

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_UNREADABLE = 2

DEFAULT_DOCUMENT = Path("README.md")
DOCS_DIRECTORY = Path("docs")
DECISIONS_DIRECTORY = DOCS_DIRECTORY / "decisions"
MAKEFILE_NAME = "Makefile"
SOURCE_DIRECTORY = Path("src")

SHELL_FENCES = frozenset({"bash", "sh", "shell", "zsh", "console"})
CONSOLE_FENCE = "console"
PROMPT = "$ "
COMMAND_SEPARATORS = frozenset({"&&", "||", ";", "|", "&"})
REDIRECTION_CHARACTERS = frozenset("<>&")
KIND_MAKE = "make"
KIND_SCRIPT = "script"
KIND_MODULE = "module"
KIND_OTHER = "other"
PYTHON_PROGRAMS = frozenset({"python", "python3"})
PROJECT_PACKAGE = "lecture_study"
UV_VALUE_OPTIONS = frozenset(
    {
        "--group",
        "--no-group",
        "--with",
        "--extra",
        "--python",
        "-p",
        "--package",
        "--project",
        "--directory",
        "--env-file",
    }
)
OPTION_TERMINATOR = "--"
SHELL_PROGRAMS = frozenset({"sh", "bash"})
IMPLICIT_FLAGS = frozenset({"--help"})
TRACKED_ROOTS = (
    "scripts/",
    "src/",
    "docs/",
    "config/",
    "demo/",
    "infra/",
    "tests/",
    ".githooks/",
    ".github/",
)
EXEMPT_PREFIXES = ("workspace/", "engines/", "tmp/", ".venv/")
EXEMPT_MARKER = ".local."
PLACEHOLDER_CHARACTERS = frozenset("<>{}*?$[]%")
QUOTE_SEPARATOR = "\x00"

_VARIABLE_LINE = re.compile(r"^([A-Za-z_][\w.-]*)[ \t]*(?:\?=|:=|\+=|=)")
_TARGET_LINE = re.compile(r"^([A-Za-z0-9][\w.-]*(?:[ \t]+[\w.-]+)*)[ \t]*:(?![=:])")
_FENCE_LINE = re.compile(r"^\s*(`{3,}|~{3,})\s*([\w-]*)")
_ASSIGNMENT_WORD = re.compile(r"^([A-Za-z_]\w*)=")
_LINK = re.compile(r"\[[^\]]{0,500}\]\(([^)\s]{1,2000})(?:\s+\"[^\"]{0,500}\")?\)")
_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_ADR_REFERENCE = re.compile(
    r"\bADRs?[\s-]+(\d{4}(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+|\s*&\s*)\d{4})*)(?!\d)"
)
_BLOCKQUOTE_MARKER = re.compile(r"^\s*(?:>\s?)+")
_HOME_PATH = re.compile(
    r"(?<![\w.~-])(?:/Users/|/home/)|(?<!\w)[A-Za-z]:[\\/]+Users[\\/]"
)


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
class _FenceBlock:
    name: str
    opening_line: int
    lines: tuple[tuple[int, str], ...]
    closed: bool

    @property
    def is_shell(self) -> bool:
        return self.name in SHELL_FENCES


@dataclass(frozen=True, slots=True)
class _Invocation:
    kind: str
    target: str
    arguments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _DocumentScope:
    root: Path
    document: Path
    facts: MakefileFacts

    def finding(self, line_number: int, rule: str, detail: str) -> Finding:
        return Finding(self.document.as_posix(), line_number, rule, detail)


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
    for block in _fence_blocks(markdown_text):
        if not block.is_shell:
            continue
        for line_number, text in _joined_continuations(block.lines):
            commands.extend(_commands_of(line_number, text, block.name))
    return tuple(commands)


def check_document(
    root: Path, document: Path, facts: MakefileFacts, decision_text: str
) -> Report:
    text = _read_document(root, document)
    scope = _DocumentScope(root, document, facts)
    commands = fenced_command_lines(text)
    invocations = _invocations(commands)
    findings = [
        *_unterminated_fence_findings(scope, text),
        *_unparseable_findings(scope, commands),
        *(f for pair in invocations for f in _invocation_findings(scope, *pair)),
        *_link_findings(scope, text),
        *_inline_path_findings(scope, text),
        *_adr_findings(scope, text),
        *_quote_findings(scope, text, decision_text),
        *_machine_path_findings(scope, text),
    ]
    findings.sort(key=lambda finding: finding.line_number)
    programs = {call.target for _, call in invocations if call.kind == KIND_OTHER}
    return Report(tuple(findings), 1, len(commands), tuple(sorted(programs)))


def check_documents(root: Path, documents: Sequence[Path]) -> Report:
    makefile_text = _read_text(root / MAKEFILE_NAME, Path(MAKEFILE_NAME))
    facts = read_makefile_facts(makefile_text)
    decision_text = folded_decision_text(root)
    return _combined(
        [check_document(root, doc, facts, decision_text) for doc in documents]
    )


def folded_decision_text(root: Path) -> str:
    """The decision files with blockquote markers removed and whitespace folded."""
    pages = sorted((root / DECISIONS_DIRECTORY).glob("*.md"))
    texts = [_read_lenient(page, page.relative_to(root)) for page in pages]
    return QUOTE_SEPARATOR.join(_folded_quote(text) for text in texts)


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
        return _refused(str(error))
    except (OSError, ValueError):
        return _refused("an input could not be processed")
    _print_report(report)
    return EXIT_FINDINGS if report.findings else EXIT_CLEAN


def _refused(reason: str) -> int:
    print(f"documentation check refused: {reason}", file=sys.stderr)
    return EXIT_UNREADABLE


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
    if report.unchecked_programs:
        print(f"not statically checked: {', '.join(report.unchecked_programs)}")


def _read_text(path: Path, shown_as: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise UnreadableInputError(f"{shown_as.as_posix()} is not readable") from error


def _read_document(root: Path, document: Path) -> str:
    if not _inside_root(root, root / document):
        raise UnreadableInputError(f"{document.as_posix()} is outside the root")
    return _read_text(root / document, document)


def _read_lenient(path: Path, shown_as: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise UnreadableInputError(f"{shown_as.as_posix()} is not readable") from error


def _combined(reports: Sequence[Report]) -> Report:
    programs = {program for report in reports for program in report.unchecked_programs}
    return Report(
        tuple(finding for report in reports for finding in report.findings),
        sum(report.document_count for report in reports),
        sum(report.command_count for report in reports),
        tuple(sorted(programs)),
    )


def _fence_blocks(markdown_text: str) -> Iterator[_FenceBlock]:
    marker, name, opening_line = "", "", 0
    lines: list[tuple[int, str]] = []
    for number, text in enumerate(markdown_text.splitlines(), start=1):
        fence = _FENCE_LINE.match(text)
        if not marker:
            if fence:
                marker, name, opening_line = (
                    fence.group(1),
                    fence.group(2).lower(),
                    number,
                )
                lines = []
        elif _closes_fence(text, marker):
            yield _FenceBlock(name, opening_line, tuple(lines), closed=True)
            marker = ""
        else:
            lines.append((number, text))
    if marker:
        yield _FenceBlock(name, opening_line, tuple(lines), closed=False)


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
    for index, word in enumerate(words):
        if skip_next:
            skip_next = False
        elif _is_redirection(word):
            skip_next = True
        elif not _is_descriptor_of_redirection(words, index):
            kept.append(word)
    return tuple(kept)


def _is_redirection(word: str) -> bool:
    return bool(word) and set(word) <= REDIRECTION_CHARACTERS


def _is_descriptor_of_redirection(words: tuple[str, ...], index: int) -> bool:
    following = words[index + 1 : index + 2]
    return words[index].isdigit() and any(_is_redirection(w) for w in following)


def _unterminated_fence_findings(scope: _DocumentScope, text: str) -> Iterator[Finding]:
    for block in _fence_blocks(text):
        if not block.closed:
            yield scope.finding(
                block.opening_line,
                RULE_UNTERMINATED_FENCE,
                "the code fence is never closed",
            )


def _unparseable_findings(
    scope: _DocumentScope, commands: Sequence[CommandLine]
) -> Iterator[Finding]:
    for command in commands:
        if not command.words:
            yield scope.finding(
                command.line_number,
                RULE_UNPARSEABLE_COMMAND,
                "the command line cannot be split into words",
            )


def _invocations(commands: Sequence[CommandLine]) -> list[tuple[int, _Invocation]]:
    pairs = []
    for command in commands:
        invocation = _classified(_without_launchers(command.words))
        if invocation:
            pairs.append((command.line_number, invocation))
    return pairs


def _without_launchers(words: tuple[str, ...]) -> tuple[str, ...]:
    start = 0
    while start < len(words) and _ASSIGNMENT_WORD.match(words[start]):
        start += 1
    rest = words[start:]
    return _without_uv_options(rest[2:]) if rest[:2] == ("uv", "run") else rest


def _without_uv_options(words: tuple[str, ...]) -> tuple[str, ...]:
    index = 0
    while index < len(words) and words[index].startswith("-"):
        if words[index] == OPTION_TERMINATOR:
            return words[index + 1 :]
        takes_value = words[index] in UV_VALUE_OPTIONS
        index += 2 if takes_value else 1
    return words[index:]


def _classified(words: tuple[str, ...]) -> _Invocation | None:
    if not words:
        return None
    program, arguments = words[0], words[1:]
    if program == "make":
        return _Invocation(KIND_MAKE, program, arguments)
    if program in PYTHON_PROGRAMS and arguments[:1] == ("-m",) and len(arguments) > 1:
        kind = KIND_MODULE if _is_project_module(arguments[1]) else KIND_OTHER
        return _Invocation(kind, arguments[1], arguments[2:])
    if program in PYTHON_PROGRAMS | SHELL_PROGRAMS and _starts_with_word(arguments):
        return _Invocation(KIND_SCRIPT, arguments[0], arguments[1:])
    if _is_repository_script_path(program):
        return _Invocation(KIND_SCRIPT, program, arguments)
    return _Invocation(KIND_OTHER, program, arguments)


def _is_project_module(dotted_name: str) -> bool:
    return dotted_name.split(".", 1)[0] == PROJECT_PACKAGE


def _is_repository_script_path(program: str) -> bool:
    is_machine_path = program.startswith(("/", "~"))
    return "/" in program and not is_machine_path and not _is_exempt_path(program)


def _starts_with_word(arguments: tuple[str, ...]) -> bool:
    return bool(arguments) and not arguments[0].startswith("-")


def _invocation_findings(
    scope: _DocumentScope, line_number: int, call: _Invocation
) -> Iterator[Finding]:
    if call.kind == KIND_MAKE:
        yield from _make_findings(scope, line_number, call.arguments)
    elif call.kind == KIND_SCRIPT:
        yield from _script_findings(scope, line_number, call)
    elif call.kind == KIND_MODULE:
        yield from _module_findings(scope, line_number, call)
    words = (
        (call.target, *call.arguments) if call.kind == KIND_OTHER else call.arguments
    )
    yield from _argument_path_findings(scope, line_number, words)


def _make_findings(
    scope: _DocumentScope, line_number: int, arguments: tuple[str, ...]
) -> Iterator[Finding]:
    for word in arguments:
        assignment = _ASSIGNMENT_WORD.match(word)
        if word.startswith("-"):
            continue
        if not assignment and word not in scope.facts.targets:
            yield scope.finding(line_number, RULE_MAKE_TARGET, word)
        elif assignment and assignment.group(1) not in scope.facts.variables:
            yield scope.finding(line_number, RULE_MAKE_VARIABLE, assignment.group(1))


def _script_findings(
    scope: _DocumentScope, line_number: int, call: _Invocation
) -> Iterator[Finding]:
    source = _source_text(scope.root, Path(call.target.removeprefix("./")))
    if source is None:
        yield scope.finding(line_number, RULE_SCRIPT_MISSING, call.target)
        return
    yield from _flag_findings(scope, line_number, call.arguments, source)


def _module_findings(
    scope: _DocumentScope, line_number: int, call: _Invocation
) -> Iterator[Finding]:
    source = _module_source(scope.root, call.target)
    if source is None:
        yield scope.finding(line_number, RULE_MODULE_MISSING, call.target)
        return
    subcommand = next((a for a in call.arguments if not a.startswith("-")), None)
    if (
        subcommand
        and f'"{subcommand}"' not in source
        and f"'{subcommand}'" not in source
    ):
        yield scope.finding(line_number, RULE_SUBCOMMAND_MISSING, subcommand)
    yield from _flag_findings(scope, line_number, call.arguments, source)


def _flag_findings(
    scope: _DocumentScope,
    line_number: int,
    arguments: tuple[str, ...],
    source: str,
) -> Iterator[Finding]:
    for argument in arguments:
        flag = argument.split("=", 1)[0]
        if flag.startswith("--") and flag not in IMPLICIT_FLAGS and flag not in source:
            yield scope.finding(line_number, RULE_FLAG_MISSING, flag)


def _module_source(root: Path, dotted_name: str) -> str | None:
    base = SOURCE_DIRECTORY.joinpath(*dotted_name.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        source = _source_text(root, candidate)
        if source is not None:
            return source
    return None


def _source_text(root: Path, relative: Path) -> str | None:
    path = root / relative
    if not (_inside_root(root, path) and path.is_file()):
        return None
    return _read_lenient(path, relative)


def _inside_root(root: Path, path: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _exists_inside_root(root: Path, path: Path) -> bool:
    return _inside_root(root, path) and path.exists()


def _is_exempt_path(word: str) -> bool:
    return (
        any(character in PLACEHOLDER_CHARACTERS for character in word)
        or EXEMPT_MARKER in word
        or word.startswith(EXEMPT_PREFIXES)
    )


def _is_tracked_path(word: str) -> bool:
    return word.startswith(TRACKED_ROOTS) and not _is_exempt_path(word)


def _argument_path_findings(
    scope: _DocumentScope, line_number: int, words: Sequence[str]
) -> Iterator[Finding]:
    for word in words:
        if _is_tracked_path(word) and not _exists_inside_root(
            scope.root, scope.root / word
        ):
            yield scope.finding(line_number, RULE_PATH_MISSING, word)


def _link_findings(scope: _DocumentScope, text: str) -> Iterator[Finding]:
    directory = scope.root / scope.document.parent
    for line_number, line in enumerate(text.splitlines(), start=1):
        for target in _LINK.findall(line):
            path = target.split("#", 1)[0]
            if path and not _URL_SCHEME.match(path):
                if not _exists_inside_root(scope.root, directory / path):
                    yield scope.finding(line_number, RULE_PATH_MISSING, path)


def _inline_path_findings(scope: _DocumentScope, text: str) -> Iterator[Finding]:
    for line_number, line in enumerate(text.splitlines(), start=1):
        tokens = [t for t in _INLINE_CODE.findall(line) if len(t.split()) == 1]
        yield from _argument_path_findings(scope, line_number, tokens)


def _adr_findings(scope: _DocumentScope, text: str) -> Iterator[Finding]:
    decisions = scope.root / DECISIONS_DIRECTORY
    for reference in _ADR_REFERENCE.finditer(text):
        line_number = text.count("\n", 0, reference.start()) + 1
        for number in re.findall(r"\d{4}", reference.group(1)):
            if not any(decisions.glob(f"{number}-*.md")):
                yield scope.finding(line_number, RULE_ADR_MISSING, number)


def _quote_findings(
    scope: _DocumentScope, text: str, decision_text: str
) -> Iterator[Finding]:
    for line_number, passage in _blockquote_passages(text):
        folded = _folded_quote(passage)
        if folded and folded not in decision_text:
            yield scope.finding(
                line_number,
                RULE_QUOTE_NOT_IN_ADR,
                "the passage is not in docs/decisions",
            )


def _blockquote_passages(text: str) -> Iterator[tuple[int, str]]:
    first_number, lines = 0, []
    for number, line in enumerate(text.splitlines(), start=1):
        if _BLOCKQUOTE_MARKER.match(line):
            first_number = first_number or number
            lines.append(line)
            continue
        if lines:
            yield first_number, "\n".join(lines)
        first_number, lines = 0, []
    if lines:
        yield first_number, "\n".join(lines)


def _folded_quote(text: str) -> str:
    unmarked = " ".join(_BLOCKQUOTE_MARKER.sub("", line) for line in text.splitlines())
    return " ".join(unicodedata.normalize("NFC", unmarked).split())


def _machine_path_findings(scope: _DocumentScope, text: str) -> Iterator[Finding]:
    for line_number, line in enumerate(text.splitlines(), start=1):
        if _HOME_PATH.search(line):
            yield scope.finding(line_number, RULE_MACHINE_PATH, "home-directory path")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

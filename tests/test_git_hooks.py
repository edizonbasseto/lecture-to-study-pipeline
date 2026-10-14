"""End-to-end regression tests for the versioned git hooks in `.githooks/`.

Each test builds a throwaway repository, copies the hooks and the denylist
scanner into it, and runs the hook scripts directly the way git would. The
denylist holds synthetic terms only. A blocked operation must never echo the
term that blocked it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is not installed"
)

# Skipped only on a developer machine without gitleaks; in CI (where the
# CI variable is always set) a missing gitleaks must fail these tests, not
# silently skip the hook regressions they guard.
requires_gitleaks = pytest.mark.skipif(
    shutil.which("gitleaks") is None and not os.environ.get("CI"),
    reason="gitleaks is not installed; the hook invokes it",
)

_REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
_ZERO_SHA = "0" * 40
_TERMS = ("São Paulo", "segredo", "Instituto Exemplo")
_ALLOWED = 0
_BLOCKED = 1
_UNAVAILABLE = 2


@dataclass(frozen=True)
class HookResult:
    exit_code: int
    output: str


@dataclass(frozen=True)
class GitSandbox:
    """A temporary repository wired to the real hooks and a private denylist."""

    root: Path
    denylist: Path

    def git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", *args],
            cwd=self.root,
            env=self._environment(),
            capture_output=True,
            text=True,
            check=True,
        )
        return completed.stdout.strip()

    def write(self, relative_path: str, content: str) -> None:
        (self.root / relative_path).write_text(content, encoding="utf-8")

    def commit_file(self, relative_path: str, content: str, message: str) -> str:
        self.write(relative_path, content)
        self.git("add", "--", relative_path)
        self.git("commit", "-q", "-m", message)
        return self.head()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def run_hook(self, name: str, *args: str, stdin: str = "") -> HookResult:
        completed = subprocess.run(
            [str(self.root / ".githooks" / name), *args],
            cwd=self.root,
            env=self._environment(),
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )
        return HookResult(completed.returncode, completed.stdout + completed.stderr)

    def _environment(self) -> dict[str, str]:
        inherited = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        return {
            **inherited,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "LECTURE_STUDY_DENYLIST": str(self.denylist),
        }


@pytest.fixture
def sandbox(tmp_path: Path) -> GitSandbox:
    denylist = tmp_path / "denylist.txt"
    denylist.write_text("\n".join(_TERMS) + "\n", encoding="utf-8")
    root = tmp_path / "repo"
    _copy_hooks_and_scanner(root)
    repository = GitSandbox(root=root, denylist=denylist)
    repository.git("init", "-q", "-b", "main")
    repository.git("config", "user.name", "Hook Test")
    repository.git("config", "user.email", "hook-test@example.invalid")
    repository.git("config", "commit.gpgsign", "false")
    repository.git("config", "tag.gpgsign", "false")
    repository.git("add", ".")
    repository.git("commit", "-q", "-m", "chore: add hooks and scanner")
    return repository


def _copy_hooks_and_scanner(root: Path) -> None:
    shutil.copytree(_REPOSITORY_ROOT / ".githooks", root / ".githooks")
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(
        _REPOSITORY_ROOT / "scripts" / "denylist_scan.py",
        root / "scripts" / "denylist_scan.py",
    )


def _assert_no_term_echoed(result: HookResult) -> None:
    folded_output = _fold(result.output)
    for term in _TERMS:
        for word in _fold(term).split():
            assert word not in folded_output


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text).casefold()
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _push_line(local_ref: str, local_sha: str, remote_sha: str) -> str:
    return f"{local_ref} {local_sha} {local_ref} {remote_sha}\n"


def _assert_blocked(result: HookResult) -> None:
    assert result.exit_code == _BLOCKED
    _assert_no_term_echoed(result)


def _assert_unavailable(result: HookResult) -> None:
    assert result.exit_code == _UNAVAILABLE
    _assert_no_term_echoed(result)


# pre-commit -----------------------------------------------------------------


@requires_gitleaks
def test_pre_commit_blocks_term_in_added_content(sandbox: GitSandbox) -> None:
    sandbox.write("notes.md", "linha sobre o segredo\n")
    sandbox.git("add", "notes.md")

    _assert_blocked(sandbox.run_hook("pre-commit"))


@requires_gitleaks
def test_pre_commit_blocks_term_only_in_non_ascii_file_name(
    sandbox: GitSandbox,
) -> None:
    sandbox.write("são-paulo.md", "conteúdo neutro\n")
    sandbox.git("add", "--", "são-paulo.md")

    _assert_blocked(sandbox.run_hook("pre-commit"))


@requires_gitleaks
def test_pre_commit_blocks_term_wrapped_across_indented_python_string(
    sandbox: GitSandbox,
) -> None:
    sandbox.write(
        "module.py",
        "def describe() -> str:\n"
        "    if True:\n"
        "        return (\n"
        '            "texto do Instituto "\n'
        '            "Exemplo de teste"\n'
        "        )\n",
    )
    sandbox.git("add", "module.py")

    _assert_blocked(sandbox.run_hook("pre-commit"))


@requires_gitleaks
@pytest.mark.parametrize("spelling", ["SEGREDO", "Ségrêdo", "SAO PAULO"])
def test_pre_commit_blocks_case_and_accent_variants(
    sandbox: GitSandbox, spelling: str
) -> None:
    sandbox.write("notes.md", f"linha com {spelling}\n")
    sandbox.git("add", "notes.md")

    _assert_blocked(sandbox.run_hook("pre-commit"))


@requires_gitleaks
def test_pre_commit_refuses_empty_denylist(sandbox: GitSandbox) -> None:
    sandbox.denylist.write_text("# only a comment\n\n", encoding="utf-8")
    sandbox.write("notes.md", "conteúdo neutro\n")
    sandbox.git("add", "notes.md")

    _assert_unavailable(sandbox.run_hook("pre-commit"))


@requires_gitleaks
def test_pre_commit_refuses_missing_denylist(sandbox: GitSandbox) -> None:
    sandbox.denylist.unlink()
    sandbox.write("notes.md", "conteúdo neutro\n")
    sandbox.git("add", "notes.md")

    _assert_unavailable(sandbox.run_hook("pre-commit"))


@requires_gitleaks
def test_pre_commit_allows_clean_change(sandbox: GitSandbox) -> None:
    sandbox.write("notes.md", "conteúdo neutro\n")
    sandbox.git("add", "notes.md")

    assert sandbox.run_hook("pre-commit").exit_code == _ALLOWED


@requires_gitleaks
def test_pre_commit_allows_removing_a_line_with_a_term(
    sandbox: GitSandbox,
) -> None:
    sandbox.commit_file("notes.md", "neutro\nlinha com segredo\n", "docs: seed")
    sandbox.write("notes.md", "neutro\n")
    sandbox.git("add", "notes.md")

    assert sandbox.run_hook("pre-commit").exit_code == _ALLOWED


# commit-msg -----------------------------------------------------------------


def _run_commit_msg(sandbox: GitSandbox, message: str) -> HookResult:
    message_file = sandbox.root.parent / "COMMIT_EDITMSG"
    message_file.write_text(message, encoding="utf-8")
    return sandbox.run_hook("commit-msg", str(message_file))


@pytest.mark.parametrize(
    "message",
    [
        "docs: mention segredo\n",
        "docs: MENTION SEGRÊDO\n",
        "docs: visit São Paulo\n",
        "docs: wrap the Instituto\nExemplo name\n",
    ],
)
def test_commit_msg_blocks_message_with_term(sandbox: GitSandbox, message: str) -> None:
    _assert_blocked(_run_commit_msg(sandbox, message))


def test_commit_msg_allows_clean_message(sandbox: GitSandbox) -> None:
    assert _run_commit_msg(sandbox, "docs: clarify setup\n").exit_code == _ALLOWED


def test_commit_msg_refuses_empty_denylist(sandbox: GitSandbox) -> None:
    sandbox.denylist.write_text("\n# nothing\n", encoding="utf-8")

    _assert_unavailable(_run_commit_msg(sandbox, "docs: clarify setup\n"))


def test_commit_msg_refuses_missing_denylist(sandbox: GitSandbox) -> None:
    sandbox.denylist.unlink()

    _assert_unavailable(_run_commit_msg(sandbox, "docs: clarify setup\n"))


# pre-push -------------------------------------------------------------------


@requires_gitleaks
def test_pre_push_blocks_term_added_only_by_merge_resolution(
    sandbox: GitSandbox,
) -> None:
    published = sandbox.commit_file("a.md", "a\n", "docs: add a")
    sandbox.git("switch", "-q", "-c", "topic")
    sandbox.commit_file("b.md", "b\n", "docs: add b")
    sandbox.git("switch", "-q", "main")
    sandbox.git("merge", "-q", "--no-ff", "--no-commit", "topic")
    sandbox.write("a.md", "a\nresolução com segredo\n")
    sandbox.git("add", "a.md")
    sandbox.git("commit", "-q", "-m", "Merge branch topic")

    result = sandbox.run_hook(
        "pre-push", stdin=_push_line("refs/heads/main", sandbox.head(), published)
    )

    _assert_blocked(result)


@requires_gitleaks
def test_pre_push_blocks_term_in_branch_name(sandbox: GitSandbox) -> None:
    sandbox.git("switch", "-q", "-c", "feature/segredo")

    result = sandbox.run_hook(
        "pre-push",
        stdin=_push_line("refs/heads/feature/segredo", sandbox.head(), _ZERO_SHA),
    )

    _assert_blocked(result)


@requires_gitleaks
def test_pre_push_blocks_term_in_annotated_tag_message(
    sandbox: GitSandbox,
) -> None:
    sandbox.git("tag", "-a", "v1.0.0", "-m", "release do Instituto Exemplo")
    tag_object = sandbox.git("rev-parse", "refs/tags/v1.0.0")

    result = sandbox.run_hook(
        "pre-push", stdin=_push_line("refs/tags/v1.0.0", tag_object, _ZERO_SHA)
    )

    _assert_blocked(result)


@requires_gitleaks
def test_pre_push_blocks_when_only_second_ref_is_dirty(
    sandbox: GitSandbox,
) -> None:
    clean_head = sandbox.head()
    sandbox.git("switch", "-q", "-c", "dirty")
    sandbox.commit_file("c.md", "linha com segredo\n", "docs: add c")

    result = sandbox.run_hook(
        "pre-push",
        stdin=_push_line("refs/heads/main", clean_head, _ZERO_SHA)
        + _push_line("refs/heads/dirty", sandbox.head(), clean_head),
    )

    _assert_blocked(result)


@requires_gitleaks
def test_pre_push_allows_clean_incremental_push(sandbox: GitSandbox) -> None:
    published = sandbox.head()
    sandbox.commit_file("d.md", "conteúdo neutro\n", "docs: add d")

    result = sandbox.run_hook(
        "pre-push", stdin=_push_line("refs/heads/main", sandbox.head(), published)
    )

    assert result.exit_code == _ALLOWED

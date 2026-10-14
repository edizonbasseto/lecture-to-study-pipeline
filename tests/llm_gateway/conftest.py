"""Stub-executable harness for the LLM gateway tests.

Stubs are real executable files written at test time (shebang plus the test
interpreter), so adapters exercise a genuine child process. No model or
binary is ever downloaded. Each stub is a distinct regular file: the trusted
executable resolver follows symlinks, which would lose the stub name.

Every invocation appends one JSON object to ``calls.jsonl`` in the control
directory (the stub's own parent directory) and then consumes the next step
of ``script-NAME.json``. A step may set ``sleep_seconds``,
``response_file_text`` (written to the path following ``-o``), ``stdout``,
``stderr`` and ``exit_code`` (default 0).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

_EXHAUSTED_EXIT_CODE = 97

_STUB_PROGRAM = """
import json
import os
import sys
import time
from pathlib import Path

control_dir = Path(__file__).resolve().parent
name = Path(__file__).name
arguments = sys.argv[1:]

with (control_dir / "calls.jsonl").open("a", encoding="utf-8") as call_log:
    call_log.write(
        json.dumps(
            {
                "name": name,
                "argv": arguments,
                "env": dict(os.environ),
                "cwd": os.getcwd(),
            }
        )
        + "\\n"
    )

script_path = control_dir / f"script-{name}.json"
cursor_path = control_dir / f"cursor-{name}.txt"
steps = json.loads(script_path.read_text()) if script_path.exists() else []
cursor = int(cursor_path.read_text()) if cursor_path.exists() else 0
if cursor >= len(steps):
    sys.stderr.write("stub script exhausted")
    sys.exit(%(exhausted_exit_code)d)
cursor_path.write_text(str(cursor + 1))
step = steps[cursor]

time.sleep(step.get("sleep_seconds", 0))
response_text = step.get("response_file_text")
if response_text is not None and "-o" in arguments:
    Path(arguments[arguments.index("-o") + 1]).write_text(response_text)
sys.stdout.write(step.get("stdout", ""))
sys.stdout.flush()
sys.stderr.write(step.get("stderr", ""))
sys.stderr.flush()
sys.exit(step.get("exit_code", 0))
""" % {"exhausted_exit_code": _EXHAUSTED_EXIT_CODE}


def write_stub_executable(directory: Path, name: str) -> Path:
    """Write one stub executable as a distinct regular file."""
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / name
    executable.write_text(f"#!{sys.executable}\n{_STUB_PROGRAM}", encoding="utf-8")
    executable.chmod(0o755)
    return executable


class StubControl:
    """Scripts the stubs of one control directory and reads their call log."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def script(self, name: str, steps: Sequence[dict[str, object]]) -> None:
        script_path = self._directory / f"script-{name}.json"
        script_path.write_text(json.dumps(list(steps)), encoding="utf-8")
        (self._directory / f"cursor-{name}.txt").unlink(missing_ok=True)

    def calls(self, name: str) -> list[dict[str, object]]:
        log_path = self._directory / "calls.jsonl"
        if not log_path.exists():
            return []
        entries = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
        ]
        return [entry for entry in entries if entry["name"] == name]


@dataclass(frozen=True, slots=True)
class CodexStub:
    """A scripted stand-in for the Codex CLI."""

    executable: Path
    control: StubControl


@pytest.fixture
def codex_stub(tmp_path: Path) -> CodexStub:
    directory = tmp_path / "codex-stub"
    executable = write_stub_executable(directory, "codex")
    return CodexStub(executable=executable, control=StubControl(directory))

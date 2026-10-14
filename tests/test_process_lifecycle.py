from __future__ import annotations

import asyncio
import io
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lecture_study.ops import process_lifecycle
from lecture_study.ops.process_lifecycle import (
    ProcessOutputLimitExceeded,
    communicate_and_reap,
    communicate_bounded_and_reap,
    run_bounded_process,
)


class _CancelledProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.wait_count = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        raise asyncio.CancelledError

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.wait_count += 1
        self.returncode = -15
        return self.returncode


class _HungProcess(_CancelledProcess):
    async def communicate(self) -> tuple[bytes, bytes]:
        raise RuntimeError("communication failed")

    async def wait(self) -> int:
        self.wait_count += 1
        if not self.killed:
            await asyncio.sleep(60)
        self.returncode = -9
        return self.returncode


class _RepeatedlyCancelledProcess(_CancelledProcess):
    def __init__(self) -> None:
        super().__init__()
        self.communication_started = asyncio.Event()
        self.termination_started = asyncio.Event()
        self.reaped = False

    async def communicate(self) -> tuple[bytes, bytes]:
        self.communication_started.set()
        await asyncio.Event().wait()
        return b"", b""

    def terminate(self) -> None:
        super().terminate()
        self.termination_started.set()

    async def wait(self) -> int:
        self.wait_count += 1
        if not self.killed:
            await asyncio.Event().wait()
        self.returncode = -9
        self.reaped = True
        return self.returncode


class _BoundedProcess(_CancelledProcess):
    def __init__(self, stdout: bytes, stderr: bytes) -> None:
        super().__init__()
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()

    async def wait(self) -> int:
        self.wait_count += 1
        self.returncode = 0
        return self.returncode


class _BlockedBoundedProcess(_CancelledProcess):
    def __init__(self) -> None:
        super().__init__()
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self._terminated = asyncio.Event()

    def terminate(self) -> None:
        super().terminate()
        self._terminated.set()

    async def wait(self) -> int:
        self.wait_count += 1
        await self._terminated.wait()
        self.returncode = -15
        return self.returncode


class _FloodingBoundedProcess(_BlockedBoundedProcess):
    def __init__(self) -> None:
        super().__init__()
        self.stdout.feed_data(b"x" * 65_537)
        self.stdout.feed_eof()
        self.stderr.feed_eof()


class _HungSyncProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        interrupt_polls: int = 0,
    ) -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.wait_count = 0
        self._interrupt_polls = interrupt_polls

    def poll(self) -> int | None:
        if self._interrupt_polls > 0:
            self._interrupt_polls -= 1
            raise KeyboardInterrupt
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.wait_count += 1
        if self.returncode is not None:
            return self.returncode
        if timeout is not None:
            raise subprocess.TimeoutExpired(["child"], timeout)
        raise AssertionError("unbounded wait before kill")


def _patch_sync_process(
    monkeypatch: pytest.MonkeyPatch,
    process: _HungSyncProcess,
) -> None:
    monkeypatch.setattr(
        process_lifecycle.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(
        process_lifecycle,
        "_PROCESS_POLL_INTERVAL_SECONDS",
        0.0001,
    )
    monkeypatch.setattr(
        process_lifecycle,
        "_PROCESS_TERMINATION_GRACE_SECONDS",
        0.001,
    )


@pytest.mark.asyncio
async def test_cancellation_terminates_reaps_and_reraises() -> None:
    process = _CancelledProcess()

    with pytest.raises(asyncio.CancelledError):
        await communicate_and_reap(process)

    assert process.terminated
    assert not process.killed
    assert process.wait_count == 1


@pytest.mark.asyncio
async def test_hung_process_is_killed_and_reaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _HungProcess()
    monkeypatch.setattr(
        process_lifecycle,
        "_PROCESS_TERMINATION_GRACE_SECONDS",
        0.001,
    )

    with pytest.raises(RuntimeError, match="communication failed"):
        await communicate_and_reap(process)

    assert process.terminated
    assert process.killed
    assert process.wait_count == 2


@pytest.mark.asyncio
async def test_repeated_cancellation_cannot_interrupt_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _RepeatedlyCancelledProcess()
    monkeypatch.setattr(
        process_lifecycle,
        "_PROCESS_TERMINATION_GRACE_SECONDS",
        0.001,
    )
    communication_task = asyncio.create_task(communicate_and_reap(process))
    await process.communication_started.wait()

    communication_task.cancel()
    await process.termination_started.wait()
    communication_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await communication_task

    assert process.terminated
    assert process.killed
    assert process.reaped


@pytest.mark.asyncio
async def test_bounded_communication_returns_complete_streams() -> None:
    process = _BoundedProcess(b"result", b"diagnostic")

    stdout, stderr = await communicate_bounded_and_reap(
        process,
        maximum_stdout_bytes=6,
        maximum_stderr_bytes=10,
        timeout_seconds=1.0,
    )

    assert stdout == b"result"
    assert stderr == b"diagnostic"
    assert process.wait_count == 1
    assert not process.terminated


@pytest.mark.asyncio
async def test_bounded_communication_terminates_output_flood() -> None:
    process = _FloodingBoundedProcess()

    with pytest.raises(ProcessOutputLimitExceeded, match="stdout exceeded 65536"):
        await communicate_bounded_and_reap(
            process,
            maximum_stdout_bytes=65_536,
            maximum_stderr_bytes=1,
            timeout_seconds=1.0,
        )

    assert process.terminated
    assert process.wait_count >= 1


@pytest.mark.asyncio
async def test_bounded_communication_terminates_after_timeout() -> None:
    process = _BlockedBoundedProcess()

    with pytest.raises(TimeoutError):
        await communicate_bounded_and_reap(
            process,
            maximum_stdout_bytes=1,
            maximum_stderr_bytes=1,
            timeout_seconds=0.001,
        )

    assert process.terminated
    assert process.wait_count == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("maximum_stdout_bytes", 0),
        ("maximum_stdout_bytes", True),
        ("maximum_stderr_bytes", 0),
        ("maximum_stderr_bytes", True),
        ("timeout_seconds", 0.0),
        ("timeout_seconds", float("nan")),
        ("timeout_seconds", float("inf")),
        ("timeout_seconds", True),
    ],
)
@pytest.mark.asyncio
async def test_bounded_communication_rejects_invalid_limits(
    field: str,
    value: int | float | bool,
) -> None:
    process = _BoundedProcess(b"", b"")
    arguments: dict[str, int | float | bool] = {
        "maximum_stdout_bytes": 1,
        "maximum_stderr_bytes": 1,
        "timeout_seconds": 1.0,
    }
    arguments[field] = value

    with pytest.raises(ValueError):
        await communicate_bounded_and_reap(process, **arguments)


def test_sync_output_flood_is_bounded_terminated_killed_and_reaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _HungSyncProcess(stdout=b"x" * 65_537)
    _patch_sync_process(monkeypatch, process)

    with pytest.raises(ProcessOutputLimitExceeded, match="stdout exceeded 65536"):
        run_bounded_process(
            ["child"],
            env={},
            timeout_seconds=1.0,
            maximum_stdout_bytes=65_536,
            maximum_stderr_bytes=1,
        )

    assert process.terminated
    assert process.killed
    assert process.wait_count >= 2


def test_sync_timeout_kills_child_that_ignores_termination_and_reaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _HungSyncProcess()
    _patch_sync_process(monkeypatch, process)

    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded_process(
            ["child"],
            env={},
            timeout_seconds=0.001,
            maximum_stdout_bytes=1,
            maximum_stderr_bytes=1,
        )

    assert process.terminated
    assert process.killed
    assert process.returncode == -9
    assert process.wait_count >= 2


def test_sync_keyboard_interrupt_cannot_skip_termination_and_reaping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _HungSyncProcess(interrupt_polls=2)
    _patch_sync_process(monkeypatch, process)

    with pytest.raises(KeyboardInterrupt):
        run_bounded_process(
            ["child"],
            env={},
            timeout_seconds=1.0,
            maximum_stdout_bytes=1,
            maximum_stderr_bytes=1,
        )

    assert process.terminated
    assert process.killed
    assert process.returncode == -9


def test_sync_timeout_terminates_descendant_process_group(
    tmp_path: Path,
) -> None:
    descendant_pid_path = tmp_path / "descendant.pid"
    child_program = (
        "import pathlib, signal, subprocess, sys, time\n"
        "descendant = subprocess.Popen([\n"
        "    sys.executable,\n"
        "    '-c',\n"
        "    'import signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)',\n"
        "])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(descendant.pid))\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(60)\n"
    )

    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded_process(
            [sys.executable, "-c", child_program, str(descendant_pid_path)],
            env={},
            timeout_seconds=1.0,
            maximum_stdout_bytes=1,
            maximum_stderr_bytes=1,
        )

    descendant_pid = int(descendant_pid_path.read_text())
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        process_state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(descendant_pid)],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if not process_state or process_state.startswith("Z"):
            break
        time.sleep(0.01)
    else:
        os.kill(descendant_pid, signal.SIGKILL)
        pytest.fail("descendant process survived process-group cleanup")


def test_sync_timeout_remains_active_after_direct_child_exit(
    tmp_path: Path,
) -> None:
    descendant_pid_path = tmp_path / "detached-descendant.pid"
    child_program = (
        "import pathlib, subprocess, sys\n"
        "descendant = subprocess.Popen([\n"
        "    sys.executable,\n"
        "    '-c',\n"
        "    'import signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)',\n"
        "])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(descendant.pid))\n"
    )
    started_at = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded_process(
            [sys.executable, "-c", child_program, str(descendant_pid_path)],
            env={},
            timeout_seconds=0.5,
            maximum_stdout_bytes=1,
            maximum_stderr_bytes=1,
        )

    assert time.monotonic() - started_at < 2.0
    descendant_pid = int(descendant_pid_path.read_text())
    process_state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(descendant_pid)],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if process_state and not process_state.startswith("Z"):
        os.kill(descendant_pid, signal.SIGKILL)
        pytest.fail("descendant process survived post-exit timeout cleanup")


def test_sync_output_limit_remains_active_after_direct_child_exit(
    tmp_path: Path,
) -> None:
    descendant_pid_path = tmp_path / "flooding-descendant.pid"
    child_program = (
        "import pathlib, subprocess, sys\n"
        "descendant = subprocess.Popen([\n"
        "    sys.executable,\n"
        "    '-c',\n"
        "    'import os, signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        'time.sleep(0.1); os.write(1, b"x" * 65537); time.sleep(60)\',\n'
        "])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(descendant.pid))\n"
    )
    started_at = time.monotonic()

    with pytest.raises(
        ProcessOutputLimitExceeded,
        match="stdout exceeded 65536",
    ):
        run_bounded_process(
            [sys.executable, "-c", child_program, str(descendant_pid_path)],
            env={},
            timeout_seconds=2.0,
            maximum_stdout_bytes=65_536,
            maximum_stderr_bytes=1,
        )

    assert time.monotonic() - started_at < 1.5
    descendant_pid = int(descendant_pid_path.read_text())
    process_state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(descendant_pid)],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if process_state and not process_state.startswith("Z"):
        os.kill(descendant_pid, signal.SIGKILL)
        pytest.fail("flooding descendant survived output-limit cleanup")

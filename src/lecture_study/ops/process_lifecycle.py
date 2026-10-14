"""Cancellation-safe lifecycle helpers for external subprocesses."""

from __future__ import annotations

import asyncio
import math
import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import BinaryIO, Protocol, cast

from lecture_study.ops.process_environment import sanitized_child_process_environment

_PROCESS_TERMINATION_GRACE_SECONDS = 5.0
_STREAM_READ_CHUNK_BYTES = 64 * 1024
_PROCESS_POLL_INTERVAL_SECONDS = 0.01


class ProcessOutputLimitExceeded(RuntimeError):
    """Raised when a subprocess emits more bytes than its declared limit."""

    def __init__(self, stream_name: str, maximum_bytes: int) -> None:
        super().__init__(f"subprocess {stream_name} exceeded {maximum_bytes} bytes")
        self.stream_name = stream_name
        self.maximum_bytes = maximum_bytes


class ManagedProcess(Protocol):
    @property
    def returncode(self) -> int | None: ...

    async def communicate(self) -> tuple[bytes, bytes]: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


class BoundedManagedProcess(ManagedProcess, Protocol):
    @property
    def stdout(self) -> asyncio.StreamReader | None: ...

    @property
    def stderr(self) -> asyncio.StreamReader | None: ...


class _BoundedPipeReader:
    def __init__(
        self,
        stream: BinaryIO,
        maximum_bytes: int,
        stream_name: str,
    ) -> None:
        self._stream = stream
        self._maximum_bytes = maximum_bytes
        self._stream_name = stream_name
        self.output = bytearray()
        self.error: BaseException | None = None
        self.exceeded = False

    @property
    def stream_name(self) -> str:
        return self._stream_name

    @property
    def maximum_bytes(self) -> int:
        return self._maximum_bytes

    def read_to_end(self) -> None:
        try:
            while chunk := _read_available_chunk(self._stream):
                self._record_or_discard(chunk)
        except BaseException as error:
            self.error = error

    def _record_or_discard(self, chunk: bytes) -> None:
        remaining_bytes = self._maximum_bytes - len(self.output)
        if len(chunk) <= remaining_bytes:
            self.output.extend(chunk)
            return
        if remaining_bytes > 0:
            self.output.extend(chunk[:remaining_bytes])
        self.exceeded = True


def _read_available_chunk(stream: BinaryIO) -> bytes:
    read_available = getattr(stream, "read1", None)
    if callable(read_available):
        return cast(bytes, read_available(_STREAM_READ_CHUNK_BYTES))
    return stream.read(_STREAM_READ_CHUNK_BYTES)


async def communicate_and_reap(
    process: ManagedProcess,
) -> tuple[bytes, bytes]:
    """Communicate with a child and guarantee cleanup after exceptional exit."""
    try:
        return await process.communicate()
    except BaseException:
        await terminate_process_tree_and_reap(process)
        raise


async def communicate_bounded_and_reap(
    process: BoundedManagedProcess,
    *,
    maximum_stdout_bytes: int,
    maximum_stderr_bytes: int,
    timeout_seconds: float,
) -> tuple[bytes, bytes]:
    """Read bounded process streams and guarantee terminal process cleanup."""
    _validate_communication_limits(
        maximum_stdout_bytes=maximum_stdout_bytes,
        maximum_stderr_bytes=maximum_stderr_bytes,
        timeout_seconds=timeout_seconds,
    )
    if process.stdout is None or process.stderr is None:
        await terminate_process_tree_and_reap(process)
        raise RuntimeError("bounded communication requires stdout and stderr pipes")

    stdout_task = asyncio.create_task(
        _read_bounded_stream(
            process.stdout,
            maximum_stdout_bytes,
            stream_name="stdout",
        )
    )
    stderr_task = asyncio.create_task(
        _read_bounded_stream(
            process.stderr,
            maximum_stderr_bytes,
            stream_name="stderr",
        )
    )
    wait_task = asyncio.create_task(process.wait())
    tasks = (stdout_task, stderr_task, wait_task)
    try:
        async with asyncio.timeout(timeout_seconds):
            stdout, stderr, _ = await asyncio.gather(*tasks)
    except BaseException:
        await terminate_process_tree_and_reap(process)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return stdout, stderr


def run_bounded_process(
    args: Sequence[str],
    *,
    env: Mapping[str, str],
    timeout_seconds: float,
    maximum_stdout_bytes: int,
    maximum_stderr_bytes: int,
    cwd: str | Path | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a synchronous child with finite time and output budgets."""
    _validate_communication_limits(
        maximum_stdout_bytes=maximum_stdout_bytes,
        maximum_stderr_bytes=maximum_stderr_bytes,
        timeout_seconds=timeout_seconds,
    )
    command = list(args)
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=sanitized_child_process_environment(env),
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout_reader, stderr_reader, reader_threads = _start_pipe_readers(
        process,
        maximum_stdout_bytes=maximum_stdout_bytes,
        maximum_stderr_bytes=maximum_stderr_bytes,
    )
    deadline = time.monotonic() + timeout_seconds
    try:
        returncode = _wait_for_bounded_process(
            process,
            command=command,
            timeout_seconds=timeout_seconds,
            deadline=deadline,
            readers=(stdout_reader, stderr_reader),
        )
        _join_reader_threads_before_deadline(
            reader_threads,
            readers=(stdout_reader, stderr_reader),
            command=command,
            timeout_seconds=timeout_seconds,
            deadline=deadline,
        )
    except BaseException:
        _complete_sync_cleanup_despite_interrupt(process)
        _join_reader_threads(reader_threads)
        raise

    _raise_reader_failure(stdout_reader, stderr_reader)
    _raise_output_limit(stdout_reader, stderr_reader)
    stdout = bytes(stdout_reader.output).decode(errors="replace")
    stderr = bytes(stderr_reader.output).decode(errors="replace")
    completed = subprocess.CompletedProcess(command, returncode, stdout, stderr)
    if check:
        completed.check_returncode()
    return completed


def _start_pipe_readers(
    process: subprocess.Popen[bytes],
    *,
    maximum_stdout_bytes: int,
    maximum_stderr_bytes: int,
) -> tuple[_BoundedPipeReader, _BoundedPipeReader, tuple[threading.Thread, ...]]:
    if process.stdout is None or process.stderr is None:
        _complete_sync_cleanup_despite_interrupt(process)
        raise RuntimeError("bounded process requires stdout and stderr pipes")
    stdout_reader = _BoundedPipeReader(
        cast(BinaryIO, process.stdout),
        maximum_stdout_bytes,
        "stdout",
    )
    stderr_reader = _BoundedPipeReader(
        cast(BinaryIO, process.stderr),
        maximum_stderr_bytes,
        "stderr",
    )
    reader_threads = (
        threading.Thread(target=stdout_reader.read_to_end, daemon=True),
        threading.Thread(target=stderr_reader.read_to_end, daemon=True),
    )
    for reader_thread in reader_threads:
        reader_thread.start()
    return stdout_reader, stderr_reader, reader_threads


def _wait_for_bounded_process(
    process: subprocess.Popen[bytes],
    *,
    command: list[str],
    timeout_seconds: float,
    deadline: float,
    readers: tuple[_BoundedPipeReader, _BoundedPipeReader],
) -> int:
    while True:
        _raise_output_limit(*readers)
        returncode = process.poll()
        if returncode is not None:
            return process.wait()
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(command, timeout_seconds)
        time.sleep(_PROCESS_POLL_INTERVAL_SECONDS)


def _join_reader_threads_before_deadline(
    reader_threads: tuple[threading.Thread, ...],
    *,
    readers: tuple[_BoundedPipeReader, _BoundedPipeReader],
    command: list[str],
    timeout_seconds: float,
    deadline: float,
) -> None:
    while any(reader_thread.is_alive() for reader_thread in reader_threads):
        _raise_reader_failure(*readers)
        _raise_output_limit(*readers)
        remaining_seconds = max(0.0, deadline - time.monotonic())
        if remaining_seconds == 0.0:
            raise subprocess.TimeoutExpired(command, timeout_seconds)
        join_seconds = min(
            _PROCESS_POLL_INTERVAL_SECONDS,
            remaining_seconds,
        )
        for reader_thread in reader_threads:
            reader_thread.join(timeout=join_seconds)
    _raise_reader_failure(*readers)
    _raise_output_limit(*readers)


def _join_reader_threads(reader_threads: tuple[threading.Thread, ...]) -> None:
    for reader_thread in reader_threads:
        reader_thread.join()


def _raise_reader_failure(
    stdout_reader: _BoundedPipeReader,
    stderr_reader: _BoundedPipeReader,
) -> None:
    for reader in (stdout_reader, stderr_reader):
        if reader.error is not None:
            raise RuntimeError("failed to read subprocess output") from reader.error


def _raise_output_limit(
    stdout_reader: _BoundedPipeReader,
    stderr_reader: _BoundedPipeReader,
) -> None:
    for reader in (stdout_reader, stderr_reader):
        if reader.exceeded:
            raise ProcessOutputLimitExceeded(
                reader.stream_name,
                reader.maximum_bytes,
            )


def _validate_communication_limits(
    *,
    maximum_stdout_bytes: int,
    maximum_stderr_bytes: int,
    timeout_seconds: float,
) -> None:
    if (
        isinstance(maximum_stdout_bytes, bool)
        or not isinstance(maximum_stdout_bytes, int)
        or maximum_stdout_bytes <= 0
    ):
        raise ValueError("maximum_stdout_bytes must be a positive integer")
    if (
        isinstance(maximum_stderr_bytes, bool)
        or not isinstance(maximum_stderr_bytes, int)
        or maximum_stderr_bytes <= 0
    ):
        raise ValueError("maximum_stderr_bytes must be a positive integer")
    if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds):
        raise ValueError("timeout_seconds must be a positive finite number")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a positive finite number")


async def _read_bounded_stream(
    stream: asyncio.StreamReader,
    maximum_bytes: int,
    *,
    stream_name: str,
) -> bytes:
    output = bytearray()
    while chunk := await stream.read(_STREAM_READ_CHUNK_BYTES):
        if len(output) + len(chunk) > maximum_bytes:
            raise ProcessOutputLimitExceeded(stream_name, maximum_bytes)
        output.extend(chunk)
    return bytes(output)


async def terminate_process_tree_and_reap(
    process: ManagedProcess,
) -> None:
    """Terminate one isolated process group and reap its direct child."""
    cleanup_task = asyncio.create_task(_terminate_and_reap(process))
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            continue
    await cleanup_task


async def _terminate_and_reap(process: ManagedProcess) -> None:
    process_group_id = _process_group_id(process)
    if process.returncode is not None:
        await process.wait()
        _terminate_remaining_process_group(process_group_id)
        return

    if not _signal_process_tree(
        process,
        process_group_id,
        signal.SIGTERM,
    ):
        await process.wait()
        _terminate_remaining_process_group(process_group_id)
        return

    try:
        await asyncio.wait_for(
            process.wait(),
            timeout=_PROCESS_TERMINATION_GRACE_SECONDS,
        )
    except TimeoutError:
        _signal_process_tree(
            process,
            process_group_id,
            signal.SIGKILL,
        )
        await process.wait()
    _terminate_remaining_process_group(process_group_id)


def _terminate_and_reap_sync(process: subprocess.Popen[bytes]) -> None:
    process_group_id = _process_group_id(process)
    if process.poll() is not None:
        _wait_sync_uninterruptibly(process)
        _terminate_remaining_process_group(process_group_id)
        return
    if not _signal_process_tree(
        process,
        process_group_id,
        signal.SIGTERM,
    ):
        _wait_sync_uninterruptibly(process)
        _terminate_remaining_process_group(process_group_id)
        return
    try:
        process.wait(timeout=_PROCESS_TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _signal_process_tree(
            process,
            process_group_id,
            signal.SIGKILL,
        )
        _wait_sync_uninterruptibly(process)
    _terminate_remaining_process_group(process_group_id)


def _complete_sync_cleanup_despite_interrupt(
    process: subprocess.Popen[bytes],
) -> None:
    while True:
        try:
            _terminate_and_reap_sync(process)
            return
        except KeyboardInterrupt:
            continue


def _wait_sync_uninterruptibly(process: subprocess.Popen[bytes]) -> int:
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            continue


def _process_group_id(process: object) -> int | None:
    process_id = getattr(process, "pid", None)
    if (
        isinstance(process_id, bool)
        or not isinstance(process_id, int)
        or process_id <= 0
    ):
        return None
    return process_id


def _signal_process_tree(
    process: ManagedProcess | subprocess.Popen[bytes],
    process_group_id: int | None,
    signal_number: signal.Signals,
) -> bool:
    try:
        if process_group_id is not None:
            os.killpg(process_group_id, signal_number)
        elif signal_number == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()
    except ProcessLookupError:
        return False
    return True


def _terminate_remaining_process_group(
    process_group_id: int | None,
) -> None:
    if process_group_id is None or not _process_group_exists(process_group_id):
        return
    _signal_process_group(process_group_id, signal.SIGKILL)


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_process_group(
    process_group_id: int,
    signal_number: signal.Signals,
) -> None:
    try:
        os.killpg(process_group_id, signal_number)
    except ProcessLookupError:
        pass

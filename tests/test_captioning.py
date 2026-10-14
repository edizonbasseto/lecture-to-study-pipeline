"""Vision captioning worker tests: the llama.cpp (Metal) async worker and
the lecture-scoped entry point. All subprocess calls are mocked; no real
llama-mtmd-cli invocation happens in this suite."""

import asyncio
import importlib.util
import types
from pathlib import Path

import pytest

from lecture_study.captioning.vlm_worker import (
    VlmCaptioningError,
    caption_frame,
    caption_lecture_figures,
)
from lecture_study.domain.models import ArtifactExecutionMode
from lecture_study.ops.model_attestation import attest_model_file, write_actor_inventory

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "caption_lecture_figures.py"
)


def _load_script_module() -> types.ModuleType:
    """Load scripts/caption_lecture_figures.py by file path.

    `scripts/` is not an importable package (no __init__.py, and
    pyproject.toml scopes pytest's own package discovery to `src/`), so the
    entry point is loaded directly from disk instead.
    """
    spec = importlib.util.spec_from_file_location(
        "caption_lecture_figures_script", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeProcess:
    """Stand-in for asyncio.subprocess.Process."""

    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self.returncode


def _caption_stdout(caption: str = "Uma incisao cirurgica com sutura.") -> bytes:
    return f'{{"caption": "{caption}"}}'.encode()


def _patch_subprocess_exec(monkeypatch: pytest.MonkeyPatch, process: FakeProcess):
    captured_args: list[str] = []

    async def fake_create_subprocess_exec(*args: str, **kwargs: object):
        captured_args.append(list(args))
        return FakeProcess(
            returncode=process.returncode,
            stdout=process._stdout,
            stderr=process._stderr,
        )

    monkeypatch.setattr(
        "lecture_study.captioning.vlm_worker.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    return captured_args


def _patch_subprocess_exec_sequence(
    monkeypatch: pytest.MonkeyPatch, processes: list[FakeProcess]
) -> list[list[str]]:
    calls: list[list[str]] = []

    async def fake_create_subprocess_exec(*args: str, **kwargs: object):
        calls.append(list(args))
        return processes[len(calls) - 1]

    monkeypatch.setattr(
        "lecture_study.captioning.vlm_worker.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    return calls


def _patch_subprocess_exec_raising(monkeypatch: pytest.MonkeyPatch, error: Exception):
    async def fake_create_subprocess_exec(*args: str, **kwargs: object):
        raise error

    monkeypatch.setattr(
        "lecture_study.captioning.vlm_worker.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )


_TRUSTED_VLM_EXECUTABLE = "/trusted/llama-mtmd-cli"


def _exception_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None:
        chain.append(current)
        current = current.__cause__
    return chain


@pytest.fixture(autouse=True)
def _vlm_binary_resolves_without_provisioned_engines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The real llama-mtmd-cli binary is provisioned outside version control
    # (scripts/01_provision_engines.sh); these tests fake the subprocess, so
    # they must not depend on the binary existing on the test machine. The
    # resolver returns a sentinel path that no other code path can produce,
    # so a worker that bypasses the resolver is detectable in argv[0].
    monkeypatch.setattr(
        "lecture_study.captioning.vlm_worker.resolve_trusted_executable",
        lambda executable, search_path=None: _TRUSTED_VLM_EXECUTABLE,
    )


# --- caption_frame --------------------------------------------------------


@pytest.mark.asyncio
async def test_caption_frame_builds_strict_vlm_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model_path = tmp_path / "qwen3-vl.gguf"
    mmproj_path = tmp_path / "mmproj-qwen3-vl.gguf"
    frame_path = tmp_path / "frame_0600.000.png"
    frame_path.write_bytes(b"")
    captured_args = _patch_subprocess_exec(
        monkeypatch, FakeProcess(returncode=0, stdout=_caption_stdout())
    )

    caption = await caption_frame(frame_path, "10002", model_path, mmproj_path)

    args = captured_args[0]
    assert args[0] == _TRUSTED_VLM_EXECUTABLE
    assert args[1:3] == ["-m", str(model_path)]
    assert args[3:5] == ["--mmproj", str(mmproj_path)]
    assert args[5:7] == ["--image", str(frame_path)]
    assert args[7:9] == ["-c", "4096"]
    assert args[9:11] == ["--temp", "0.0"]
    assert args[11:13] == ["-n", "512"]
    assert args[13:15] == ["--image-max-tokens", "3072"]
    assert args[15] == "-f"
    prompt_path = Path(args[16])
    assert not prompt_path.exists()
    assert caption.lecture_id == "10002"
    assert caption.frame_filename == "frame_0600.000.png"
    assert caption.caption == "Uma incisao cirurgica com sutura."
    assert caption.execution_mode is ArtifactExecutionMode.PIPELINE_DEFAULT


@pytest.mark.asyncio
async def test_caption_frame_timestamp_comes_from_filename_not_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model_path = tmp_path / "qwen3-vl.gguf"
    mmproj_path = tmp_path / "mmproj-qwen3-vl.gguf"
    frame_path = tmp_path / "frame_0637.500.png"
    frame_path.write_bytes(b"")
    # The model's JSON schema has no timestamp field at all -- even if a
    # response somehow smuggled a different number, the caption model
    # would reject it as an undeclared field. The timestamp asserted below
    # comes exclusively from the filename.
    _patch_subprocess_exec(
        monkeypatch, FakeProcess(returncode=0, stdout=_caption_stdout())
    )

    caption = await caption_frame(frame_path, "10002", model_path, mmproj_path)

    assert caption.timestamp_seconds == 637.5


@pytest.mark.asyncio
async def test_caption_frame_prompt_forbids_clinical_claims(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model_path = tmp_path / "qwen3-vl.gguf"
    mmproj_path = tmp_path / "mmproj-qwen3-vl.gguf"
    frame_path = tmp_path / "frame_0000.000.png"
    frame_path.write_bytes(b"")
    written_prompt = ""

    async def fake_create_subprocess_exec(*args: str, **kwargs: object):
        nonlocal written_prompt
        prompt_path = Path(args[args.index("-f") + 1])
        written_prompt = prompt_path.read_text()
        return FakeProcess(returncode=0, stdout=_caption_stdout())

    monkeypatch.setattr(
        "lecture_study.captioning.vlm_worker.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    await caption_frame(frame_path, "10002", model_path, mmproj_path)

    assert "pt-BR" in written_prompt or "portugues do Brasil" in written_prompt
    assert "diagnostico" in written_prompt
    assert "contraindicacao" in written_prompt


@pytest.mark.asyncio
async def test_caption_frame_retries_once_on_parse_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model_path = tmp_path / "qwen3-vl.gguf"
    mmproj_path = tmp_path / "mmproj-qwen3-vl.gguf"
    frame_path = tmp_path / "frame_0000.000.png"
    frame_path.write_bytes(b"")
    calls = _patch_subprocess_exec_sequence(
        monkeypatch,
        [
            FakeProcess(returncode=0, stdout=b"not json at all"),
            FakeProcess(returncode=0, stdout=_caption_stdout("segunda tentativa")),
        ],
    )

    caption = await caption_frame(frame_path, "10002", model_path, mmproj_path)

    assert len(calls) == 2
    retry_prompt = Path(calls[1][calls[1].index("-f") + 1])
    # Cleaned up by the time the assertion runs, but its path differs from
    # the first attempt's, proving a fresh prompt file was written.
    first_prompt = Path(calls[0][calls[0].index("-f") + 1])
    assert retry_prompt != first_prompt
    assert caption.caption == "segunda tentativa"


@pytest.mark.asyncio
async def test_caption_frame_raises_after_retry_also_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model_path = tmp_path / "qwen3-vl.gguf"
    mmproj_path = tmp_path / "mmproj-qwen3-vl.gguf"
    frame_path = tmp_path / "frame_0000.000.png"
    frame_path.write_bytes(b"")
    _patch_subprocess_exec_sequence(
        monkeypatch,
        [
            FakeProcess(returncode=0, stdout=b"not json at all"),
            FakeProcess(returncode=0, stdout=b"still not json"),
        ],
    )

    with pytest.raises(VlmCaptioningError):
        await caption_frame(frame_path, "10002", model_path, mmproj_path)


@pytest.mark.asyncio
async def test_caption_frame_raises_when_binary_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_subprocess_exec_raising(monkeypatch, FileNotFoundError("no llama-mtmd-cli"))

    with pytest.raises(VlmCaptioningError):
        await caption_frame(
            tmp_path / "frame_0000.000.png",
            "10002",
            tmp_path / "qwen3-vl.gguf",
            tmp_path / "mmproj-qwen3-vl.gguf",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resolver_error",
    [
        FileNotFoundError("executable not found on PATH: llama-mtmd-cli"),
        OSError("executable must be a regular file: /trusted/llama-mtmd-cli"),
    ],
)
async def test_caption_frame_raises_when_the_trusted_resolver_rejects_the_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, resolver_error: OSError
) -> None:
    def reject_executable(executable: str, search_path: str | None = None) -> str:
        raise resolver_error

    monkeypatch.setattr(
        "lecture_study.captioning.vlm_worker.resolve_trusted_executable",
        reject_executable,
    )
    captured_args = _patch_subprocess_exec(
        monkeypatch, FakeProcess(returncode=0, stdout=_caption_stdout())
    )

    with pytest.raises(VlmCaptioningError, match="missing or untrusted") as raised:
        await caption_frame(
            tmp_path / "frame_0000.000.png",
            "10002",
            tmp_path / "qwen3-vl.gguf",
            tmp_path / "mmproj-qwen3-vl.gguf",
        )

    assert resolver_error in _exception_chain(raised.value)
    assert captured_args == []


@pytest.mark.asyncio
async def test_caption_frame_raises_on_nonzero_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_subprocess_exec_sequence(
        monkeypatch,
        [
            FakeProcess(returncode=1, stderr=b"error: failed to load model"),
            FakeProcess(returncode=1, stderr=b"error: failed to load model"),
        ],
    )

    with pytest.raises(VlmCaptioningError) as raised:
        await caption_frame(
            tmp_path / "frame_0000.000.png",
            "10002",
            tmp_path / "missing.gguf",
            tmp_path / "missing-mmproj.gguf",
        )
    assert "failed to load model" not in str(raised.value)


@pytest.mark.asyncio
async def test_caption_frame_cleans_up_temp_file_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured_args = _patch_subprocess_exec(
        monkeypatch, FakeProcess(returncode=1, stderr=b"metal contention crash")
    )

    with pytest.raises(VlmCaptioningError):
        await caption_frame(
            tmp_path / "frame_0000.000.png",
            "10002",
            tmp_path / "qwen3-vl.gguf",
            tmp_path / "mmproj-qwen3-vl.gguf",
        )

    prompt_path = Path(captured_args[0][14])
    assert not prompt_path.exists()


# --- caption_lecture_figures ------------------------------------------------


@pytest.mark.asyncio
async def test_caption_lecture_figures_captions_every_frame_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lecture_dir = tmp_path / "10002"
    lecture_dir.mkdir()
    (lecture_dir / "frame_0600.000.png").write_bytes(b"")
    (lecture_dir / "frame_0000.000.png").write_bytes(b"")
    (lecture_dir / "frame_1350.000.png").write_bytes(b"")
    invocation_order: list[str] = []

    async def fake_create_subprocess_exec(*args: str, **kwargs: object):
        invocation_order.append(Path(args[args.index("--image") + 1]).name)
        return FakeProcess(returncode=0, stdout=_caption_stdout())

    monkeypatch.setattr(
        "lecture_study.captioning.vlm_worker.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    catalog = await caption_lecture_figures(
        "10002",
        tmp_path / "qwen3-vl.gguf",
        tmp_path / "mmproj-qwen3-vl.gguf",
        frames_root=tmp_path,
    )

    assert invocation_order == [
        "frame_0000.000.png",
        "frame_0600.000.png",
        "frame_1350.000.png",
    ]
    assert [figure.frame_filename for figure in catalog.figures] == invocation_order
    assert catalog.lecture_id == "10002"


@pytest.mark.asyncio
async def test_caption_lecture_figures_runs_serially_not_concurrently(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lecture_dir = tmp_path / "10002"
    lecture_dir.mkdir()
    (lecture_dir / "frame_0000.000.png").write_bytes(b"")
    (lecture_dir / "frame_0600.000.png").write_bytes(b"")
    concurrent_calls = 0
    max_concurrent_calls = 0

    async def fake_create_subprocess_exec(*args: str, **kwargs: object):
        nonlocal concurrent_calls, max_concurrent_calls
        concurrent_calls += 1
        max_concurrent_calls = max(max_concurrent_calls, concurrent_calls)
        await asyncio.sleep(0)
        concurrent_calls -= 1
        return FakeProcess(returncode=0, stdout=_caption_stdout())

    monkeypatch.setattr(
        "lecture_study.captioning.vlm_worker.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    await caption_lecture_figures(
        "10002",
        tmp_path / "qwen3-vl.gguf",
        tmp_path / "mmproj-qwen3-vl.gguf",
        frames_root=tmp_path,
    )

    assert max_concurrent_calls == 1


@pytest.mark.asyncio
async def test_caption_lecture_figures_reports_failing_frame_and_aborts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lecture_dir = tmp_path / "10002"
    lecture_dir.mkdir()
    (lecture_dir / "frame_0000.000.png").write_bytes(b"")
    (lecture_dir / "frame_0600.000.png").write_bytes(b"")

    async def fake_create_subprocess_exec(*args: str, **kwargs: object):
        image_path = Path(args[args.index("--image") + 1])
        if image_path.name == "frame_0600.000.png":
            return FakeProcess(returncode=1, stderr=b"boom")
        return FakeProcess(returncode=0, stdout=_caption_stdout())

    monkeypatch.setattr(
        "lecture_study.captioning.vlm_worker.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    with pytest.raises(VlmCaptioningError, match="frame_0600.000.png"):
        await caption_lecture_figures(
            "10002",
            tmp_path / "qwen3-vl.gguf",
            tmp_path / "mmproj-qwen3-vl.gguf",
            frames_root=tmp_path,
        )


# --- scripts/caption_lecture_figures.py -----------------------------------
#
# Only the script's non-inference behavior is unit-tested here (argument
# shape, attestation gating, the no-frames exit): the real, single-frame,
# end-to-end inference run this script also performs is a task action
# recorded directly in the plan summary, not a mocked unit test.


def test_script_help_lists_a_required_lecture_identifier_and_no_batch_option(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script_module()

    with pytest.raises(SystemExit) as raised:
        module._argument_parser().parse_args(["--help"])
    assert raised.value.code == 0
    help_text = capsys.readouterr().out
    assert "lecture_id" in help_text
    for batch_indicator in ("--all", "--batch", "--discover"):
        assert batch_indicator not in help_text


def test_verify_attested_passes_for_a_correctly_attested_model(
    tmp_path: Path,
) -> None:
    module = _load_script_module()
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"weights")
    inventory_path = tmp_path / "inventory.json"
    write_actor_inventory(
        inventory_path,
        [attest_model_file(model_path, "Qwen/Qwen3-VL-8B-Instruct-GGUF")],
    )

    module._verify_attested(model_path, inventory_path)  # must not raise


def test_verify_attested_raises_for_a_missing_entry(tmp_path: Path) -> None:
    module = _load_script_module()
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"weights")
    inventory_path = tmp_path / "inventory.json"
    write_actor_inventory(inventory_path, [])

    with pytest.raises(module.UnattestedModelError, match=str(model_path)):
        module._verify_attested(model_path, inventory_path)


def test_verify_attested_raises_for_a_digest_mismatch(tmp_path: Path) -> None:
    module = _load_script_module()
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"weights")
    inventory_path = tmp_path / "inventory.json"
    write_actor_inventory(
        inventory_path,
        [attest_model_file(model_path, "Qwen/Qwen3-VL-8B-Instruct-GGUF")],
    )
    model_path.write_bytes(b"tampered weights")

    with pytest.raises(module.UnattestedModelError, match="differs from recomputed"):
        module._verify_attested(model_path, inventory_path)


def test_main_stops_before_inference_for_an_unattested_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script_module()
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"weights")
    mmproj_path = tmp_path / "mmproj.gguf"
    mmproj_path.write_bytes(b"projector")
    inventory_path = tmp_path / "inventory.json"
    write_actor_inventory(inventory_path, [])

    async def fail_if_called(*args: object, **kwargs: object):
        raise AssertionError("inference must not run for an unattested model")

    monkeypatch.setattr(
        "lecture_study.captioning.vlm_worker.asyncio.create_subprocess_exec",
        fail_if_called,
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "caption_lecture_figures.py",
            "10002",
            "--model-path",
            str(model_path),
            "--mmproj-path",
            str(mmproj_path),
            "--inventory-path",
            str(inventory_path),
        ],
    )

    exit_code = module.main()

    assert exit_code == 1
    assert str(model_path) in capsys.readouterr().err


def test_main_exits_nonzero_and_names_the_lecture_when_it_has_no_frames(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script_module()
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"weights")
    mmproj_path = tmp_path / "mmproj.gguf"
    mmproj_path.write_bytes(b"projector")
    inventory_path = tmp_path / "inventory.json"
    write_actor_inventory(
        inventory_path,
        [
            attest_model_file(model_path, "Qwen/Qwen3-VL-8B-Instruct-GGUF"),
            attest_model_file(mmproj_path, "Qwen/Qwen3-VL-8B-Instruct-GGUF"),
        ],
    )
    lecture_id = "no-frames-lecture-for-this-test"

    async def fail_if_called(*args: object, **kwargs: object):
        raise AssertionError("inference must not run when there are no frames")

    monkeypatch.setattr(
        "lecture_study.captioning.vlm_worker.asyncio.create_subprocess_exec",
        fail_if_called,
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "caption_lecture_figures.py",
            lecture_id,
            "--model-path",
            str(model_path),
            "--mmproj-path",
            str(mmproj_path),
            "--inventory-path",
            str(inventory_path),
        ],
    )

    exit_code = module.main()

    assert exit_code == 1
    assert lecture_id in capsys.readouterr().err

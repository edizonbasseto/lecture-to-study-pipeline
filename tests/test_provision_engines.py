"""Network-free checks of the native engine provisioning script.

The script is executed only through argument handling paths that exit before
any side effect, and through ``bash -n`` syntax checking, so the tests never
reach brew, git, cmake or the network.
"""

from pathlib import Path
import re
import subprocess

REPOSITORY_ROOT = Path(__file__).parent.parent
SCRIPT = REPOSITORY_ROOT / "scripts" / "01_provision_engines.sh"
TIMEOUT_SECONDS = 30
FULL_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def _run_script(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *arguments],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )


def test_unknown_argument_exits_2_with_usage_on_stderr() -> None:
    result = _run_script("--bogus")

    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert result.stdout == ""


def test_help_exits_0_with_usage_on_stdout() -> None:
    result = _run_script("--help")

    assert result.returncode == 0
    assert "Usage:" in result.stdout
    assert "--help" in result.stdout


def test_help_documents_the_example_model_option() -> None:
    result = _run_script("--help")

    assert "--with-example-llm-model" in result.stdout


def test_unknown_argument_after_example_option_exits_2() -> None:
    result = _run_script("--with-example-llm-model", "--bogus")

    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert "==>" not in result.stdout


def test_script_has_valid_bash_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_script_verifies_the_gateway_and_vision_binaries() -> None:
    script_text = SCRIPT.read_text(encoding="utf-8")

    assert "require_llama_binaries" in script_text
    for binary_name in ("llama-completion", "llama-tokenize", "llama-mtmd-cli"):
        assert binary_name in script_text


def _script_assignment(variable_name: str) -> str:
    match = re.search(rf'^{variable_name}="([^"]*)"$', _script_text(), re.MULTILINE)
    assert match, f"{variable_name} assignment not found"
    return match.group(1)


def _script_text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _example_model_shard_entries() -> list[str]:
    block = re.search(
        r"^EXAMPLE_LLM_MODEL_SHARDS=\(\n(.*?)^\)",
        _script_text(),
        re.MULTILINE | re.DOTALL,
    )
    assert block, "EXAMPLE_LLM_MODEL_SHARDS array not found"
    return re.findall(r'"([^"]+)"', block.group(1))


def test_example_model_revision_is_a_full_commit_sha() -> None:
    revision = _script_assignment("EXAMPLE_LLM_MODEL_REVISION")

    assert FULL_COMMIT_SHA.fullmatch(revision)


def test_every_example_model_shard_pins_a_sha256() -> None:
    entries = _example_model_shard_entries()

    assert len(entries) == 3
    for entry in entries:
        file_name, _, sha256 = entry.partition(":")
        assert file_name.endswith(".gguf")
        assert SHA256_HEX.fullmatch(sha256)


def test_example_model_download_is_https_pinned_to_the_revision() -> None:
    script_text = _script_text()

    assert (
        "https://huggingface.co/${EXAMPLE_LLM_MODEL_REPOSITORY}/resolve/${EXAMPLE_LLM_MODEL_REVISION}/"
        in script_text
    )
    assert "/resolve/main/" not in script_text
    assert "--proto '=https'" in script_text
    assert "verify_sha256" in script_text

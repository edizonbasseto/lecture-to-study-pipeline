"""Network-free checks of the native engine provisioning script.

The script is executed only through argument handling paths that exit before
any side effect, and through ``bash -n`` syntax checking, so the tests never
reach brew, git, cmake or the network.
"""

from pathlib import Path
import subprocess

REPOSITORY_ROOT = Path(__file__).parent.parent
SCRIPT = REPOSITORY_ROOT / "scripts" / "01_provision_engines.sh"
TIMEOUT_SECONDS = 30


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

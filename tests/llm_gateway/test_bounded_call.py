"""run_adapter_process maps helper failures to typed errors without prompt text."""

from __future__ import annotations

from pathlib import Path

import pytest

from lecture_study.llm import bounded_call
from lecture_study.llm.bounded_call import run_adapter_process
from lecture_study.llm.gateway import AdapterName, LlmCallError, Stage

SECRET_TEXT = "PROMPT-MARKER-helper-failure"


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError(SECRET_TEXT),
        ValueError(SECRET_TEXT),
        UnicodeEncodeError("utf-8", SECRET_TEXT, 0, 1, SECRET_TEXT),
    ],
    ids=["runtime-error", "value-error", "unicode-encode-error"],
)
def test_helper_failures_become_call_errors_without_the_failure_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: Exception
) -> None:
    def failing_helper(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(bounded_call, "run_bounded_process", failing_helper)

    with pytest.raises(LlmCallError, match="process helper failed") as raised:
        run_adapter_process(
            ["child", SECRET_TEXT],
            stage=Stage.DRAFTING,
            adapter_name=AdapterName.CODEX,
            environment={},
            cwd=tmp_path,
            timeout_seconds=1.0,
            maximum_output_bytes=10,
        )

    assert SECRET_TEXT not in str(raised.value)
    assert raised.value.__cause__ is None

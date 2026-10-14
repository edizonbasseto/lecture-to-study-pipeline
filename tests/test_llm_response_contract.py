"""Contract-layer diagnostics: summarize_validation_issues must name the
failing field without ever echoing model-controlled bytes (values, or dict
keys pydantic v2 embeds in error loc segments)."""

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError, create_model

from lecture_study.ops.llm_response_contract import (
    LlmResponseContractError,
    parse_llm_json_response,
    summarize_validation_issues,
)


class _OptionHolder(BaseModel):
    options: dict[str, str]
    label: str


class _Wrapped(BaseModel):
    items: list[_OptionHolder]


_ADAPTER: TypeAdapter[_Wrapped] = TypeAdapter(_Wrapped)


def _validation_error(payload: object) -> ValidationError:
    try:
        _ADAPTER.validate_python(payload, strict=True)
    except ValidationError as error:
        return error
    raise AssertionError("payload was expected to fail validation")


def test_names_the_failing_declared_field_and_index() -> None:
    error = _validation_error({"items": [{"options": {}, "label": 7}]})

    summary = summarize_validation_issues(error, declared_type=_Wrapped)

    assert "items.0.label: string_type" in summary


def test_never_echoes_a_model_controlled_dict_key() -> None:
    # Regression (2026-09-16 review BLOCK): pydantic v2 puts dict keys in
    # loc, so a model-authored option key would land in the error message
    # verbatim. It must render as <key> instead.
    error = _validation_error(
        {"items": [{"options": {"A) PRIVATE_MODEL_KEY ": 123}, "label": "x"}]}
    )

    summary = summarize_validation_issues(error, declared_type=_Wrapped)

    assert "PRIVATE_MODEL_KEY" not in summary
    assert "items.0.options.<key>: string_type" in summary


def test_summary_is_byte_capped() -> None:
    # The cap must bind on DECLARED field names (the only long strings
    # that legitimately enter the summary): a schema-declared field name
    # longer than the cap truncates the summary, proving the slice is
    # load-bearing rather than vacuous.
    long_field_name = "f" * 2500
    long_field_model = create_model(
        "_LongField", **{long_field_name: (str, ...), "present": (int, ...)}
    )
    adapter: TypeAdapter = TypeAdapter(long_field_model)
    try:
        adapter.validate_python({"present": 1}, strict=True)
    except ValidationError as error:
        summary = summarize_validation_issues(error, declared_type=long_field_model)
    else:
        raise AssertionError("payload was expected to fail validation")

    assert len(summary) <= 2000
    assert long_field_name not in summary


class _Caption(BaseModel):
    caption: str


_CAPTION_ADAPTER: TypeAdapter[_Caption] = TypeAdapter(_Caption)


def test_repairs_a_bare_backslash_escape_the_vision_model_sometimes_emits() -> None:
    # Regression for lecture 10015, frame_1509.720.png: the vision model
    # emitted a LaTeX-style accent escape (`Epit\'elio`) instead of the
    # plain accented character, which json.loads rejects as "Invalid
    # \escape". The response must still decode, with the extra backslash
    # dropped and every other character preserved verbatim.
    raw_caption = "Epit" + chr(92) + "'elio juncional e out" + chr(92) + "'ro termo"
    stdout = ('{"caption": "' + raw_caption + '"}').encode()

    response = parse_llm_json_response(
        stdout,
        expected_kind="object",
        response_adapter=_CAPTION_ADAPTER,
        declared_type=_Caption,
    )

    assert response.caption == "Epit'elio juncional e out'ro termo"


def test_still_fails_closed_on_json_malformed_for_another_reason() -> None:
    # The repair must not paper over a genuinely different malformation
    # (here, a trailing comma) -- it only targets the one documented
    # invalid-escape defect.
    stdout = b'{"caption": "abc",}'

    with pytest.raises(LlmResponseContractError) as raised:
        parse_llm_json_response(
            stdout,
            expected_kind="object",
            response_adapter=_CAPTION_ADAPTER,
            declared_type=_Caption,
        )

    assert raised.value.retry_category == "malformed_json"


def test_a_legitimate_escaped_backslash_survives_next_to_the_invalid_one() -> None:
    # Regression for the BLOCK finding in code review: the repair must
    # consume each backslash together with the character right after it,
    # as one unit -- never split a legitimate `\\` pair apart while
    # walking past the one genuinely invalid escape next to it.
    raw_caption = "x" + chr(92) * 3 + "'y"  # \\\  followed by an apostrophe
    stdout = ('{"caption": "' + raw_caption + '"}').encode()

    response = parse_llm_json_response(
        stdout,
        expected_kind="object",
        response_adapter=_CAPTION_ADAPTER,
        declared_type=_Caption,
    )

    # The first (legitimate) backslash pair decodes to one literal
    # backslash; only the second, genuinely invalid backslash is dropped.
    assert response.caption == "x" + chr(92) + "'y"


def test_a_stray_backslash_outside_any_string_is_never_touched() -> None:
    # Regression for the other BLOCK finding: the repair must be scoped
    # to the string literal the reported error is inside of. A second,
    # unrelated backslash defect sitting OUTSIDE any string (here, in a
    # bare number) must still be rejected -- repairing the in-string
    # defect must never silently coerce a different part of the document.
    stdout = b'{"caption": "Epit\\\'elio", "score": 1\\9}'

    with pytest.raises(LlmResponseContractError) as raised:
        parse_llm_json_response(
            stdout,
            expected_kind="object",
            response_adapter=_CAPTION_ADAPTER,
            declared_type=_Caption,
        )

    assert raised.value.retry_category == "malformed_json"

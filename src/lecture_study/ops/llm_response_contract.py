"""Strict, bounded handling for legacy LLM JSON responses."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator, Sequence
from typing import Annotated, Literal, TypeVar, get_args, get_origin

from pydantic import BaseModel, TypeAdapter, ValidationError

JsonContainerKind = Literal["object", "array"]
LlmRetryCategory = Literal[
    "ambiguous_json",
    "contract",
    "duplicate_key",
    "malformed_json",
    "missing_json",
    "non_finite_number",
    "schema",
    "wrong_container",
]

_MAXIMUM_RETRY_SECTION_BYTES = 2 * 1024
_MAXIMUM_VALIDATION_ISSUES = 3
_MAXIMUM_VALIDATION_SUMMARY_CHARS = 2000
_TRUNCATED_PROMPT_ECHO_SUFFIX = "... (truncated)"
UNIQUE_JSON_OBJECT_MEMBERS_INSTRUCTION = (
    "Every JSON object member name MUST appear exactly once."
)

ResponseT = TypeVar("ResponseT")


class LlmResponseContractError(ValueError):
    """Raised when model output violates the strict JSON response contract."""

    def __init__(self, message: str, *, retry_category: LlmRetryCategory) -> None:
        super().__init__(message)
        self.retry_category = retry_category


def parse_llm_json_response(
    stdout: bytes,
    *,
    expected_kind: JsonContainerKind,
    response_adapter: TypeAdapter[ResponseT],
    declared_type: object,
) -> ResponseT:
    """Decode and validate exactly one strict JSON object or array response."""
    text = _discard_unclosed_truncated_json_echo_lines(stdout.decode(errors="replace"))
    candidates, last_decode_error, ambiguous_malformed_output = (
        _collect_json_candidates(
            text,
            expected_kind=expected_kind,
        )
    )
    if len(candidates) > 1:
        raise _contract_error(
            "ambiguous JSON output: expected exactly one response, "
            f"found {len(candidates)}",
            stdout,
            retry_category="ambiguous_json",
        )
    if not candidates:
        if ambiguous_malformed_output:
            reason = "ambiguous malformed JSON output"
            retry_category: LlmRetryCategory = "ambiguous_json"
        elif last_decode_error is not None:
            reason = (
                "malformed JSON output: "
                f"{last_decode_error.msg} at line {last_decode_error.lineno}, "
                f"column {last_decode_error.colno}"
            )
            retry_category = "malformed_json"
        else:
            reason = f"no JSON {expected_kind} in stdout"
            retry_category = "missing_json"
        raise _contract_error(reason, stdout, retry_category=retry_category)

    decoded = _strict_json_decode(candidates[0], stdout)
    if expected_kind == "object" and not isinstance(decoded, dict):
        raise _contract_error(
            "expected one JSON object response",
            stdout,
            retry_category="wrong_container",
        )
    if expected_kind == "array" and not isinstance(decoded, list):
        raise _contract_error(
            "expected one JSON array response",
            stdout,
            retry_category="wrong_container",
        )
    try:
        _reject_undeclared_fields(decoded, declared_type)
    except _UndeclaredFieldError as error:
        raise _contract_error(
            str(error),
            stdout,
            retry_category="schema",
        ) from error
    try:
        return response_adapter.validate_python(decoded, strict=True)
    except ValidationError as error:
        issues = summarize_validation_issues(error, declared_type=declared_type)
        raise _contract_error(
            f"JSON response failed schema validation: {issues}",
            stdout,
            retry_category="schema",
        ) from None


def build_bounded_retry_prompt_section(*, heading: str, feedback: str) -> str:
    """Build and byte-preflight a bounded retry-only prompt suffix."""
    prefix = f"\n\n{heading}\n"
    feedback_bytes = feedback.encode("utf-8")
    section = f"{prefix}{feedback}"
    if len(section.encode("utf-8")) <= _MAXIMUM_RETRY_SECTION_BYTES:
        return section

    digest = hashlib.sha256(feedback_bytes).hexdigest()
    suffix = f"\n[feedback truncated; sha256={digest}; bytes={len(feedback_bytes)}]"
    byte_budget = (
        _MAXIMUM_RETRY_SECTION_BYTES
        - len(prefix.encode("utf-8"))
        - len(suffix.encode("utf-8"))
    )
    excerpt = feedback_bytes[: max(0, byte_budget)].decode(
        "utf-8",
        errors="ignore",
    )
    bounded_section = f"{prefix}{excerpt}{suffix}"
    if len(bounded_section.encode("utf-8")) > _MAXIMUM_RETRY_SECTION_BYTES:
        raise ValueError("retry prompt section exceeds its byte preflight bound")
    return bounded_section


def build_llm_retry_feedback(error: Exception) -> str:
    """Map an LLM failure to trusted retry guidance without replaying output."""
    feedback_by_category: dict[LlmRetryCategory, str] = {
        "ambiguous_json": (
            "The previous response contained more than one JSON candidate."
        ),
        "contract": "The previous response violated the response contract.",
        "duplicate_key": (
            "The previous response contained a duplicate JSON key. "
            f"{UNIQUE_JSON_OBJECT_MEMBERS_INSTRUCTION}"
        ),
        "malformed_json": "The previous response contained malformed JSON.",
        "missing_json": (
            "The previous response did not contain the required JSON value."
        ),
        "non_finite_number": (
            "The previous response contained a non-finite JSON number."
        ),
        "schema": "The previous response did not satisfy the declared JSON schema.",
        "wrong_container": (
            "The previous response used the wrong JSON container type."
        ),
    }
    current_error: BaseException | None = error
    while current_error is not None:
        if isinstance(current_error, LlmResponseContractError):
            feedback = feedback_by_category[current_error.retry_category]
            return f"{feedback} Return exactly one JSON value matching the schema."
        current_error = current_error.__cause__
    return (
        "The previous response violated the response contract. "
        "Return exactly one JSON value matching the schema."
    )


def llm_output_diagnostic(output: bytes) -> str:
    """Return stable output identity without exposing model-controlled bytes."""
    return f"sha256={hashlib.sha256(output).hexdigest()}; bytes={len(output)}"


class _UndeclaredFieldError(ValueError):
    """Internal shape-walk failure before output diagnostics are attached."""


def _discard_unclosed_truncated_json_echo_lines(text: str) -> str:
    retained_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped_line = line.strip()
        if stripped_line.startswith(("{", "[")) and stripped_line.endswith(
            _TRUNCATED_PROMPT_ECHO_SUFFIX
        ):
            unmarked_line = stripped_line[
                : -len(_TRUNCATED_PROMPT_ECHO_SUFFIX)
            ].rstrip()
            candidates, _, _ = _collect_json_candidates(unmarked_line)
            if not candidates:
                continue
            retained_lines.extend(f"{candidate}\n" for candidate in candidates)
            continue
        retained_lines.append(line)
    return "".join(retained_lines)


_VALID_JSON_STRING_ESCAPE_CHARACTERS = frozenset('"\\/bfnrtu')


def _repair_invalid_backslash_escapes(span: str) -> str:
    """Drop a backslash JSON does not allow escaping with (observed from the
    vision model: it sometimes writes a LaTeX-style accent escape such as
    `Epit\\'elio`, where JSON only permits `\\"`, `\\\\`, `\\/`, `\\b`, `\\f`,
    `\\n`, `\\r`, `\\t`, `\\u`).

    Two properties keep this from correcting more than the one documented
    defect: (1) it only acts inside a JSON string literal -- a backslash
    outside any string (a different, unrelated malformation) is left
    untouched, so the repaired span still fails to decode and the caller
    still fails closed; (2) every backslash is consumed together with the
    character immediately after it, as one unit, valid or not -- so a
    legitimate escape pair (`\\\\`, `\\"`, ...) immediately before or
    after the invalid one is never split and reread as something else.
    Mechanical and lossless for every character it keeps: it never
    invents or alters model-authored content, it only removes the one
    extra backslash so the string decodes.
    """
    repaired_characters: list[str] = []
    index = 0
    length = len(span)
    in_string = False
    while index < length:
        character = span[index]
        if not in_string:
            repaired_characters.append(character)
            in_string = character == '"'
            index += 1
            continue
        if character == '"':
            in_string = False
            repaired_characters.append(character)
            index += 1
            continue
        if character != "\\":
            repaired_characters.append(character)
            index += 1
            continue
        following_index = index + 1
        following_character = span[following_index] if following_index < length else ""
        if following_character in _VALID_JSON_STRING_ESCAPE_CHARACTERS:
            repaired_characters.append(character)
            repaired_characters.append(following_character)
            index = following_index + 1
        else:
            index = following_index
    return "".join(repaired_characters)


def _collect_json_candidates(
    text: str,
    *,
    expected_kind: JsonContainerKind | None = None,
) -> tuple[list[str], json.JSONDecodeError | None, bool]:
    candidates: list[str] = []
    primary_decode_error: json.JSONDecodeError | None = None
    primary_malformed_span_length = -1
    expected_decode_errors: list[json.JSONDecodeError] = []
    expected_opener = (
        None if expected_kind is None else {"object": "{", "array": "["}[expected_kind]
    )
    for span in _iter_balanced_json_spans(text):
        try:
            json.loads(span)
        except json.JSONDecodeError as error:
            if "Invalid \\escape" in error.msg:
                repaired_span = _repair_invalid_backslash_escapes(span)
                try:
                    json.loads(repaired_span)
                except json.JSONDecodeError:
                    pass
                else:
                    candidates.append(repaired_span)
                    continue
            if expected_opener is not None and span.startswith(expected_opener):
                expected_decode_errors.append(error)
            if len(span) > primary_malformed_span_length:
                primary_decode_error = error
                primary_malformed_span_length = len(span)
            continue
        candidates.append(span)
    if len(expected_decode_errors) > 1:
        return candidates, None, True
    if expected_decode_errors:
        return candidates, expected_decode_errors[0], False
    return candidates, primary_decode_error, False


def _strict_json_decode(span: str, stdout: bytes) -> object:
    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _contract_error(
                    "duplicate JSON key",
                    stdout,
                    retry_category="duplicate_key",
                )
            result[key] = value
        return result

    def reject_non_finite_constant(value: str) -> None:
        raise _contract_error(
            "non-finite JSON number",
            stdout,
            retry_category="non_finite_number",
        )

    def parse_finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise _contract_error(
                "non-finite JSON number",
                stdout,
                retry_category="non_finite_number",
            )
        return parsed

    try:
        return json.loads(
            span,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_non_finite_constant,
            parse_float=parse_finite_float,
        )
    except LlmResponseContractError:
        raise
    except (ValueError, json.JSONDecodeError) as error:
        raise _contract_error(
            f"malformed JSON output: {type(error).__name__}",
            stdout,
            retry_category="malformed_json",
        ) from error


def _reject_undeclared_fields(
    value: object,
    declared_type: object,
    path: str = "$",
) -> None:
    origin = get_origin(declared_type)
    if origin is Annotated:
        annotated_type = get_args(declared_type)[0]
        _reject_undeclared_fields(value, annotated_type, path)
        return
    if isinstance(declared_type, type) and issubclass(declared_type, BaseModel):
        _reject_undeclared_model_fields(value, declared_type, path)
        return
    arguments = get_args(declared_type)
    if origin is list and isinstance(value, list) and arguments:
        for index, item in enumerate(value):
            _reject_undeclared_fields(item, arguments[0], f"{path}[{index}]")
    elif origin is dict and isinstance(value, dict) and len(arguments) == 2:
        for key, item in value.items():
            _reject_undeclared_fields(item, arguments[1], f"{path}.{key}")


def _reject_undeclared_model_fields(
    value: object,
    model_type: type[BaseModel],
    path: str,
) -> None:
    if not isinstance(value, dict):
        return
    declared_fields: dict[str, object] = {}
    for field_name, field in model_type.model_fields.items():
        declared_fields[field_name] = field.annotation
        if field.alias is not None:
            declared_fields[field.alias] = field.annotation
    undeclared = sorted(set(value).difference(declared_fields))
    if undeclared:
        raise _UndeclaredFieldError("undeclared JSON field")
    for field_name, field_value in value.items():
        _reject_undeclared_fields(
            field_value,
            declared_fields[field_name],
            f"{path}.{field_name}",
        )


def _iter_balanced_json_spans(text: str) -> Iterator[str]:
    expected_closers: list[str] = []
    start = -1
    in_string = False
    escape_next = False

    for index, char in enumerate(text):
        if not expected_closers:
            if char == "{":
                start = index
                expected_closers.append("}")
            elif char == "[":
                start = index
                expected_closers.append("]")
            continue

        if in_string:
            if escape_next:
                escape_next = False
            elif char == "\\":
                escape_next = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            expected_closers.append("}")
        elif char == "[":
            expected_closers.append("]")
        elif char in "}]":
            if char != expected_closers[-1]:
                expected_closers.clear()
                start = -1
                continue
            expected_closers.pop()
            if not expected_closers:
                yield text[start : index + 1]
                start = -1


def summarize_validation_issues(
    error: ValidationError, *, declared_type: object
) -> str:
    """Summarize a validation failure as field paths and error types only.

    A loc segment is emitted only when it is an integer index or a field
    name actually declared somewhere in declared_type's graph: pydantic
    also puts DICT KEYS in loc (e.g. a model-authored MockQuestion option
    key), and those are model-controlled bytes that must never reach an
    error message -- they render as `<key>`. Values never appear
    (include_input=False); naming the failing field lets a contract
    failure be diagnosed from the message alone, which the bare error
    type ("is_instance_of") never could."""
    declared_names = _declared_field_names(declared_type)
    issues = error.errors(include_input=False, include_url=False)[
        :_MAXIMUM_VALIDATION_ISSUES
    ]
    summary = ", ".join(
        f"{_safe_issue_loc(issue['loc'], declared_names)}: {issue['type']}"
        for issue in issues
    )
    return summary[:_MAXIMUM_VALIDATION_SUMMARY_CHARS]


def _safe_issue_loc(loc: Sequence[object], declared_names: frozenset[str]) -> str:
    parts = []
    for part in loc:
        if isinstance(part, int) or (isinstance(part, str) and part in declared_names):
            parts.append(str(part))
        else:
            parts.append("<key>")
    return ".".join(parts)


def _declared_field_names(declared_type: object) -> frozenset[str]:
    """Every field name declared on any model reachable from
    declared_type (through lists and dict values), cycle-safe."""
    names: set[str] = set()
    visited: set[int] = set()

    def visit(annotation: object) -> None:
        if id(annotation) in visited:
            return
        visited.add(id(annotation))
        origin = get_origin(annotation)
        if origin is None:
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                for field_name, field_info in annotation.model_fields.items():
                    names.add(field_name)
                    visit(field_info.annotation)
            return
        for argument in get_args(annotation):
            visit(argument)

    visit(declared_type)
    return frozenset(names)


def _contract_error(
    reason: str,
    stdout: bytes,
    *,
    retry_category: LlmRetryCategory,
) -> LlmResponseContractError:
    return LlmResponseContractError(
        f"{reason}; {llm_output_diagnostic(stdout)}",
        retry_category=retry_category,
    )

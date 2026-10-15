"""Strict YAML loading refuses every construct that could hide or invent a flag."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import date

import pytest

from lecture_study.ops.strict_yaml import (
    MAX_DOCUMENT_CHARACTERS,
    MAX_DOCUMENT_NODES,
    StrictYamlError,
    bounded_listing,
    load_strict_yaml,
    masked_location,
)

_GENEROUS_SECONDS = 10.0


def _finishes_within(seconds: float, work: Callable[[], object]) -> bool:
    """True when ``work`` returns or raises ``StrictYamlError`` in time; any
    other exception fails the calling test."""

    unexpected: list[BaseException] = []
    worker = threading.Thread(target=_recording(work, unexpected), daemon=True)
    worker.start()
    worker.join(seconds)
    assert not unexpected, f"unexpected {type(unexpected[0]).__name__}"
    return not worker.is_alive()


def _recording(
    work: Callable[[], object], unexpected: list[BaseException]
) -> Callable[[], None]:
    def run() -> None:
        try:
            work()
        except StrictYamlError:
            pass
        except BaseException as error:  # noqa: BLE001 - reported to the test
            unexpected.append(error)

    return run


def _billion_laughs(depth: int) -> str:
    lines = ["l0: &l0 [x, x]"]
    lines += [
        f"l{level}: &l{level} [*l{level - 1}, *l{level - 1}]"
        for level in range(1, depth)
    ]
    return "\n".join(lines) + "\n"


def test_plain_document_loads() -> None:
    loaded = load_strict_yaml("a: &unused 2025-07-01\nflag: true\nn: [1, 2.5, null]\n")

    assert loaded == {"a": date(2025, 7, 1), "flag": True, "n": [1, 2.5, None]}


@pytest.mark.parametrize(
    "text",
    [
        "a: &d 2025-07-01\nb: *d\n",
        "other: &granted true\nflag: *granted\n",
        "rows: [&r {k: v}, *r]\n",
        "ids: [&x '777']\nlisted: [*x]\n",
    ],
    ids=["scalar", "boolean", "whole-row", "identifier"],
)
def test_every_alias_is_rejected(text: str) -> None:
    with pytest.raises(StrictYamlError, match="aliases are not allowed"):
        load_strict_yaml(text)


def test_alias_fan_out_is_rejected_quickly() -> None:
    assert _finishes_within(
        _GENEROUS_SECONDS, lambda: load_strict_yaml(_billion_laughs(30))
    )


def test_self_referential_alias_is_rejected_quickly() -> None:
    assert _finishes_within(_GENEROUS_SECONDS, lambda: load_strict_yaml("a: &a [*a]\n"))


def test_long_alias_chain_is_rejected_quickly() -> None:
    links = 30_000
    text = "a0: &a0 {k: v}\n" + "".join(
        f"a{index}: &a{index} {{k: *a{index - 1}}}\n" for index in range(1, links)
    )
    assert len(text) <= MAX_DOCUMENT_CHARACTERS

    assert _finishes_within(_GENEROUS_SECONDS, lambda: load_strict_yaml(text))


def test_boolean_word_used_as_a_key_is_rejected() -> None:
    with pytest.raises(
        StrictYamlError, match="booleans must be written true or false: \\?"
    ):
        load_strict_yaml("yes: 1\n")


@pytest.mark.parametrize(
    "text",
    ["id: !!binary OTI1MTAw\n", "ids: !!set {a: null}\n", "x: !!omap [a: 1]\n"],
    ids=["binary", "set", "omap"],
)
def test_encoding_and_exotic_collection_tags_are_rejected(text: str) -> None:
    with pytest.raises(StrictYamlError, match="unsupported tags"):
        load_strict_yaml(text)


def test_document_over_the_size_cap_is_rejected_before_parsing() -> None:
    with pytest.raises(StrictYamlError, match="exceeds"):
        load_strict_yaml("a: " + "x" * MAX_DOCUMENT_CHARACTERS + "\n")


def test_parser_failure_keeps_no_exception_context() -> None:
    with pytest.raises(StrictYamlError, match="unparseable YAML") as raised:
        load_strict_yaml("secret-row: [unclosed\n")

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_masked_location_hides_control_characters_and_truncates() -> None:
    location = masked_location(["lectures", 0, "\x1b]0;title\x07" + "k" * 100])

    assert "\x1b" not in location
    assert "\x07" not in location
    assert location.startswith("lectures.0.?]0;title?")
    assert location.endswith("...")
    assert masked_location([]) == "<root>"


def test_masked_location_keeps_only_the_edges_of_a_deep_path() -> None:
    location = masked_location([f"k{depth}" for depth in range(400)])

    assert location == "k0.k1.....k398.k399"


def test_bounded_listing_describes_only_what_it_shows() -> None:
    described: list[int] = []

    def describe(item: int) -> str:
        described.append(item)
        return str(item)

    assert bounded_listing([1, 2], describe) == "1, 2"
    assert bounded_listing(list(range(8)), describe) == "0, 1, 2, 3, 4 (+3 more)"
    assert described == [1, 2, 0, 1, 2, 3, 4]


def test_many_deep_rejections_are_reported_quickly_and_briefly() -> None:
    depth = 300
    opening = "".join(f"{'k' * 30}{level}: {{" for level in range(depth))
    words = ", ".join(f"y{index}: on" for index in range(40_000))
    text = opening + words + "}" * depth + "\n"
    assert len(text) <= MAX_DOCUMENT_CHARACTERS
    messages: list[str] = []

    def load() -> None:
        try:
            load_strict_yaml(text)
        except StrictYamlError as error:
            messages.append(str(error))

    assert _finishes_within(_GENEROUS_SECONDS, load)
    assert messages and len(messages[0]) < 2_000


def test_alias_is_reported_at_its_own_line() -> None:
    with pytest.raises(StrictYamlError, match="aliases are not allowed: line 4$"):
        load_strict_yaml("a:\n  b:\n    c: &x 1\nd: *x\n")


@pytest.mark.parametrize(
    "text",
    ['flag: !!bool "\\x74rue"\n', 'id: "42\\x342"\n'],
    ids=["boolean", "identifier"],
)
def test_escape_sequences_are_rejected(text: str) -> None:
    with pytest.raises(StrictYamlError, match="escape sequences"):
        load_strict_yaml(text)


@pytest.mark.parametrize("value", ["1:0:0", "1:30.5", "9" * 31])
def test_sexagesimal_and_long_numbers_are_rejected(value: str) -> None:
    with pytest.raises(StrictYamlError, match="numbers must be plain"):
        load_strict_yaml(f"n: {value}\n")


def test_huge_sexagesimal_number_is_rejected_quickly() -> None:
    text = "n: 1" + ":0" * 400_000 + "\n"

    assert _finishes_within(_GENEROUS_SECONDS, lambda: load_strict_yaml(text))


def test_document_over_the_node_cap_is_rejected() -> None:
    text = "items: [" + ", ".join("0" for _ in range(MAX_DOCUMENT_NODES + 1)) + "]\n"

    with pytest.raises(StrictYamlError, match="nodes"):
        load_strict_yaml(text)


def test_escaped_non_ascii_letters_written_by_safe_dump_are_accepted() -> None:
    assert load_strict_yaml('title: "Fun\\xE7\\xE3o"\n') == {"title": "Função"}


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("a: &k x\nm:\n  q: 1\n  *k : 2\n", 4),
        ("a: &x\n  - 1\n  - *x\n", 3),
        ("r:\n  - &y 1\n  - 2\n  - *y\n", 4),
    ],
    ids=["alias-as-key", "self-reference", "sequence-item"],
)
def test_alias_line_is_where_the_alias_is_written(text: str, line: int) -> None:
    with pytest.raises(StrictYamlError, match=f"aliases are not allowed: line {line}$"):
        load_strict_yaml(text)


@pytest.mark.parametrize(
    "text",
    [
        't: "\\x1b[2J\\x74rue \\xE9"\n',
        'p: "C:\\\\llama"\n',
        't: "tr\\\n  ue"\n',
        't: "Aula \\"especial\\" \\xE9"\n',
    ],
    ids=[
        "ascii-escape-beside-non-ascii",
        "escaped-backslash",
        "line-join",
        "escaped-quote",
    ],
)
def test_any_escape_decoding_to_ascii_is_rejected(text: str) -> None:
    with pytest.raises(StrictYamlError, match="single-quote"):
        load_strict_yaml(text)


def test_printable_non_ascii_escapes_are_accepted() -> None:
    assert load_strict_yaml('t: "Fun\\xE7\\u00e3o \\U0001F600"\n') == {
        "t": "Função \U0001f600"
    }


@pytest.mark.parametrize(
    "escape", ["\\x9B", "\\uD800", "\\uFEFF", "\\uFFFE", "\\N", "\\_", "\\L", "\\P"]
)
def test_escapes_decoding_to_non_printable_characters_are_rejected(
    escape: str,
) -> None:
    with pytest.raises(StrictYamlError, match="printable non-ASCII"):
        load_strict_yaml(f't: "x{escape}y"\n')


def test_node_cap_stops_composition_before_a_later_syntax_error() -> None:
    items = ",".join("0" for _ in range(MAX_DOCUMENT_NODES + 10))

    with pytest.raises(StrictYamlError, match="nodes"):
        load_strict_yaml("items: [" + items + ", [unclosed\n")

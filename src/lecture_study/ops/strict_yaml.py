"""Strict YAML loading shared by the operator-owned configuration loaders.

``yaml.safe_load`` keeps the last value of a repeated mapping key and says
nothing, so a contradictory flag (``false`` then ``true``) silently resolves to
one of them. A merge key (``<<: *anchor``) lets a row inherit a flag it never
states, and any alias (``*granted``) lets a value appear where it was never
written and multiplies the work of every later validation step. YAML 1.1
reads ``yes``, ``on`` and their variants as booleans, so a word the operator
did not mean as a grant becomes one; tags such as ``!!binary`` and escapes
such as ``"\\x74rue"`` hide a value from a search for its literal text. The
loaders that carry egress or publication flags therefore parse through
``load_strict_yaml``, which refuses all of them. It also bounds the work a
document can cause: its size, its node count (checked while the node tree is
built), and numbers whose YAML 1.1 forms cost quadratic time to construct.

This module imports only PyYAML. Its errors never echo a value: they name an
exception type, line numbers, or at most a few dotted key paths, each masked
and shortened by ``masked_location``. No error raised here chains or keeps the
parser exception as its context.
"""

from __future__ import annotations

import re
import sys
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import cast

import yaml

MAX_DOCUMENT_CHARACTERS = 1_000_000
MAX_DOCUMENT_NODES = 50_000

_YAML_TAG_PREFIX = "tag:yaml.org,2002:"
_MERGE_TAG = _YAML_TAG_PREFIX + "merge"
_BOOL_TAG = _YAML_TAG_PREFIX + "bool"
_NUMBER_TAGS = frozenset({_YAML_TAG_PREFIX + "int", _YAML_TAG_PREFIX + "float"})
_ALLOWED_TAGS = frozenset(
    _YAML_TAG_PREFIX + name
    for name in ("null", "bool", "int", "float", "str", "timestamp", "seq", "map")
)
_CANONICAL_BOOLEANS = frozenset({"true", "false"})
_MAX_NUMBER_CHARACTERS = 30
_DOUBLE_QUOTED = '"'
# A double-quoted escape: \xHH, \uHHHH, \UHHHHHHHH, or one escaped character
# (a line break included, which joins the text around it).
_ESCAPE = re.compile(
    r"\\(?:x([0-9A-Fa-f]{2})|u([0-9A-Fa-f]{4})|U([0-9A-Fa-f]{8})|(.))", re.DOTALL
)
_FIRST_NON_ASCII_CODE_POINT = 0x80
_MAX_SEGMENT_CHARACTERS = 40
_KEPT_EDGE_SEGMENTS = 2
_MAX_REPORTED_LOCATIONS = 5
_UNLOCATED = "<root>"


class StrictYamlError(ValueError):
    """The text is not acceptable operator configuration.

    The message carries no document content.
    """


def load_strict_yaml(text: str) -> object:
    """Parse operator configuration once and refuse every ambiguous construct.

    Raises ``StrictYamlError`` for a document over ``MAX_DOCUMENT_CHARACTERS``
    or ``MAX_DOCUMENT_NODES``, unparseable text, any alias, a mapping key
    repeated as written, a merge key, a tag outside the plain scalar and
    collection types, a boolean not written ``true`` or ``false``, a long or
    base-60 number, or an escape that decodes to anything but a printable
    non-ASCII character.
    """

    if len(text) > MAX_DOCUMENT_CHARACTERS:
        raise StrictYamlError(f"document exceeds {MAX_DOCUMENT_CHARACTERS} characters")
    loader = _parsed_safely(_StrictLoader, text)
    root = _parsed_safely(loader.get_single_node)
    if loader.alias_lines:
        listing = bounded_listing(loader.alias_lines, lambda line: f"line {line}")
        raise StrictYamlError(f"aliases are not allowed: {listing}")
    if root is None:
        return None
    _reject_non_strict_nodes(root)
    return _parsed_safely(loader.construct_document, root)


def masked_location(parts: Iterable[object]) -> str:
    """A dotted location safe to print: non-printable characters masked, each
    segment truncated and a deep path reduced to its first and last segments,
    so a key cannot inject terminal or log control sequences nor copy long
    document text into a message."""

    segments = [str(part) for part in parts]
    if len(segments) > 2 * _KEPT_EDGE_SEGMENTS + 1:
        segments = [
            *segments[:_KEPT_EDGE_SEGMENTS],
            "...",
            *segments[-_KEPT_EDGE_SEGMENTS:],
        ]
    return ".".join(_masked_segment(segment) for segment in segments) or _UNLOCATED


def bounded_listing[T](items: list[T], describe: Callable[[T], str]) -> str:
    """Describe at most a few items, with a count of the rest; the rest are
    never described, so a long list costs nothing to report."""

    shown = ", ".join(describe(item) for item in items[:_MAX_REPORTED_LOCATIONS])
    hidden = len(items) - _MAX_REPORTED_LOCATIONS
    return f"{shown} (+{hidden} more)" if hidden > 0 else shown


def _masked_segment(segment: str) -> str:
    masked = "".join(
        character if character.isprintable() else "?" for character in segment
    )
    if len(masked) <= _MAX_SEGMENT_CHARACTERS:
        return masked
    return masked[:_MAX_SEGMENT_CHARACTERS] + "..."


class _StrictLoader(yaml.SafeLoader):
    """A safe loader that records the line of every alias and stops composing
    once the document holds more than ``MAX_DOCUMENT_NODES`` nodes."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self.alias_lines: list[int] = []
        self._composed_nodes = 0

    def compose_node(self, parent: yaml.Node | None, index: int) -> yaml.Node | None:
        # PyYAML's stub leaves ``peek_event`` untyped; its contract is to
        # return the next event without consuming it.
        event = cast(Callable[[], yaml.Event], self.peek_event)()
        if isinstance(event, yaml.AliasEvent):
            self.alias_lines.append(event.start_mark.line + 1)
        self._composed_nodes += 1
        if self._composed_nodes > MAX_DOCUMENT_NODES:
            raise StrictYamlError(f"document exceeds {MAX_DOCUMENT_NODES} nodes")
        return super().compose_node(parent, index)


def _parsed_safely[**P, T](
    parser_step: Callable[P, T], *args: P.args, **kwargs: P.kwargs
) -> T:
    # PyYAML raises many unrelated types for one malformed document
    # (YAMLError, ValueError, KeyError, AttributeError, IndexError,
    # RecursionError). The try covers only a PyYAML call, so every failure
    # there means the document is unparseable. The typed error is raised after
    # the handler so that it neither chains nor keeps as context the parser
    # error, which can hold the offending scalar or the whole document buffer.
    try:
        return parser_step(*args, **kwargs)
    except StrictYamlError:
        raise
    except Exception as error:
        failure = type(error).__name__
    raise StrictYamlError(f"unparseable YAML ({failure})")


@dataclass(frozen=True, eq=False, repr=False, slots=True)
class _Location:
    """A node's place in the document, linked to its parent so that a deep
    document costs one entry per node instead of one path copy per node."""

    segment: str
    parent: _Location | None = None

    def child(self, segment: str) -> _Location:
        return _Location(segment, self)

    def parts(self) -> list[str]:
        reversed_parts: list[str] = []
        location: _Location | None = self
        while location is not None and location.parent is not None:
            reversed_parts.append(location.segment)
            location = location.parent
        return reversed_parts[::-1]


_ROOT = _Location("")


class _RejectedNodes:
    def __init__(self) -> None:
        self._by_reason: dict[str, list[_Location]] = {}

    def add(self, reason: str, location: _Location) -> None:
        self._by_reason.setdefault(reason, []).append(location)

    def raise_first(self) -> None:
        for reason, locations in self._by_reason.items():
            listing = bounded_listing(
                locations, lambda location: masked_location(location.parts())
            )
            raise StrictYamlError(f"{reason}: {listing}")


def _reject_non_strict_nodes(root: yaml.Node) -> None:
    """Visit every node breadth first and without recursion, so deep nesting
    cannot exhaust the stack. The tree has no shared node: aliases were
    refused while it was built."""

    rejected = _RejectedNodes()
    pending: deque[tuple[yaml.Node, _Location]] = deque([(root, _ROOT)])
    while pending:
        node, location = pending.popleft()
        if node.tag not in _ALLOWED_TAGS and node.tag != _MERGE_TAG:
            rejected.add("unsupported tags", location)
        if isinstance(node, yaml.ScalarNode):
            _check_scalar(node, location, rejected)
        elif isinstance(node, yaml.SequenceNode):
            pending.extend(
                (item, location.child(str(index)))
                for index, item in enumerate(node.value)
            )
        elif isinstance(node, yaml.MappingNode):
            pending.extend(_scan_mapping(node, location, rejected))
    rejected.raise_first()


def _check_scalar(
    node: yaml.ScalarNode, location: _Location, rejected: _RejectedNodes
) -> None:
    if node.tag == _BOOL_TAG and node.value not in _CANONICAL_BOOLEANS:
        rejected.add("booleans must be written true or false", location)
    if node.tag in _NUMBER_TAGS and not _is_plain_number(node.value):
        rejected.add(
            f"numbers must be plain and at most {_MAX_NUMBER_CHARACTERS} characters",
            location,
        )
    if node.style == _DOUBLE_QUOTED and _has_disallowed_escape(_source_text(node)):
        rejected.add(
            "escape sequences are allowed only for printable non-ASCII "
            "characters (single-quote text that holds a backslash)",
            location,
        )


def _is_plain_number(value: str) -> bool:
    # YAML 1.1 reads ``1:0:0`` as a base-60 number, which PyYAML builds in
    # quadratic time; long digit runs cost the same way.
    return ":" not in value and len(value) <= _MAX_NUMBER_CHARACTERS


def _has_disallowed_escape(source: str) -> bool:
    """True when any escape decodes to an ASCII character (``"\\x74rue"``,
    ``"42\\x342"``, ``"\\e"``, an escaped quote or backslash, a line-joining
    backslash) or to a non-printable one (``"\\x9B"``, a lone surrogate,
    ``\\N``): a search for the literal text of a flag, an identifier or a
    control character would miss it, and PyYAML refuses most of those
    characters when written raw. Escaped printable non-ASCII letters, which
    ``yaml.safe_dump`` writes by default, stay accepted."""

    for match in _ESCAPE.finditer(source):
        *hex_groups, short_escape = match.groups()
        if short_escape is not None:
            return True
        hex_digits = next(group for group in hex_groups if group is not None)
        if not _is_printable_non_ascii(int(hex_digits, 16)):
            return True
    return False


def _is_printable_non_ascii(code_point: int) -> bool:
    if not _FIRST_NON_ASCII_CODE_POINT <= code_point <= sys.maxunicode:
        return False
    return chr(code_point).isprintable()


def _source_text(node: yaml.ScalarNode) -> str:
    buffer = node.start_mark.buffer
    if buffer is None:
        raise StrictYamlError("document source is unavailable for inspection")
    return str(buffer[node.start_mark.index : node.end_mark.index])


def _scan_mapping(
    node: yaml.MappingNode, location: _Location, rejected: _RejectedNodes
) -> list[tuple[yaml.Node, _Location]]:
    """Record the mapping's merge and repeated keys; return its child nodes."""

    children: list[tuple[yaml.Node, _Location]] = []
    seen: set[str] = set()
    for key_node, value_node in node.value:
        key = key_node.value if isinstance(key_node, yaml.ScalarNode) else None
        if key_node.tag == _MERGE_TAG:
            rejected.add("merge keys are not allowed", location.child("<<"))
        elif key is not None and key in seen:
            rejected.add("repeated keys", location.child(key))
        if key is not None:
            seen.add(key)
        children.append((key_node, location.child("?")))
        children.append((value_node, location.child(key or "?")))
    return children

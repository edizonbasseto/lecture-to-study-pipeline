"""Strict YAML helpers shared by the operator-owned configuration loaders.

``yaml.safe_load`` keeps the last value of a repeated mapping key and says
nothing, so a contradictory flag (``false`` then ``true``) silently resolves to
one of them. The loaders that carry egress or publication flags call
``duplicated_key_paths`` on the same text they parse and refuse any report.

This module imports only PyYAML. It never echoes a value: it returns dotted key
paths only.
"""

from __future__ import annotations

import yaml


def duplicated_key_paths(text: str) -> list[str]:
    """Dotted paths of mapping keys that occur twice in one mapping (keys only).

    Raises ``yaml.YAMLError`` when the text is not parseable YAML.
    """

    duplicated: list[str] = []
    _collect_duplicated_keys(
        yaml.compose(text, Loader=yaml.SafeLoader), (), duplicated, set()
    )
    return duplicated


def _collect_duplicated_keys(
    node: yaml.Node | None,
    parent_path: tuple[str, ...],
    duplicated: list[str],
    visited: set[int],
) -> None:
    if node is None or id(node) in visited:
        return
    visited.add(id(node))
    if isinstance(node, yaml.SequenceNode):
        for index, item in enumerate(node.value):
            _collect_duplicated_keys(
                item, (*parent_path, str(index)), duplicated, visited
            )
    elif isinstance(node, yaml.MappingNode):
        seen: set[str] = set()
        for key_node, value_node in node.value:
            key = _scalar_key(key_node)
            if key is not None and key in seen:
                duplicated.append(".".join((*parent_path, key)))
            if key is not None:
                seen.add(key)
            _collect_duplicated_keys(
                value_node, (*parent_path, key or "?"), duplicated, visited
            )


def _scalar_key(node: yaml.Node) -> str | None:
    return node.value if isinstance(node, yaml.ScalarNode) else None

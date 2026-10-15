"""Filesystem snapshots that prove a command wrote nothing (ADR 0045, ADR 0047).

A snapshot records, for every entry under a set of labelled roots, its kind,
size, SHA-256 digest and modification time. Directories are entries, so a
newly created empty directory is a difference. Symbolic links are recorded,
never followed. Comparing two snapshots tells which entries were created,
modified or deleted between them.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = [
    "TreeDifference",
    "TreeEntry",
    "TreeSnapshot",
    "difference",
    "snapshot_tree",
]

EntryKind = Literal["file", "directory", "symlink", "absent_root"]

_ROOT_RELATIVE_PATH = "."
_DIGEST_CHUNK_BYTES = 1024 * 1024
_KEY_SEPARATOR = ":"


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class TreeEntry(_FrozenStrictModel):
    """One file, directory, symlink or absent root under a labelled root."""

    root_label: str
    relative_path: str
    kind: EntryKind
    size: int
    sha256: str | None
    link_target: str | None = None
    mtime_ns: int

    def key(self) -> str:
        return f"{self.root_label}{_KEY_SEPARATOR}{self.relative_path}"


class TreeSnapshot(_FrozenStrictModel):
    """Every entry under the snapshotted roots, in a deterministic order."""

    entries: tuple[TreeEntry, ...]


class TreeDifference(_FrozenStrictModel):
    """Entry keys (``LABEL:RELATIVE_PATH``) that changed between snapshots."""

    created: tuple[str, ...]
    modified: tuple[str, ...]
    deleted: tuple[str, ...]

    def is_empty(self) -> bool:
        return not (self.created or self.modified or self.deleted)

    def changed(self) -> tuple[str, ...]:
        return tuple(sorted({*self.created, *self.modified, *self.deleted}))


def snapshot_tree(roots: Mapping[str, Path]) -> TreeSnapshot:
    """Record every entry under each labelled root without following links."""
    entries = [
        entry
        for label, root in sorted(roots.items())
        for entry in _root_entries(label, root)
    ]
    return TreeSnapshot(entries=tuple(entries))


def difference(before: TreeSnapshot, after: TreeSnapshot) -> TreeDifference:
    """Entries created, modified or deleted going from ``before`` to ``after``."""
    earlier = {entry.key(): entry for entry in before.entries}
    later = {entry.key(): entry for entry in after.entries}
    return TreeDifference(
        created=tuple(sorted(later.keys() - earlier.keys())),
        modified=tuple(
            sorted(
                key
                for key in earlier.keys() & later.keys()
                if earlier[key] != later[key]
            )
        ),
        deleted=tuple(sorted(earlier.keys() - later.keys())),
    )


def _root_entries(label: str, root: Path) -> Iterator[TreeEntry]:
    if not os.path.lexists(root):
        yield TreeEntry(
            root_label=label,
            relative_path=_ROOT_RELATIVE_PATH,
            kind="absent_root",
            size=0,
            sha256=None,
            mtime_ns=0,
        )
        return
    yield _entry_for(label, root, _ROOT_RELATIVE_PATH)
    if root.is_symlink() or not root.is_dir():
        return
    for relative_path in _descendant_paths(root):
        yield _entry_for(label, root, relative_path)


def _descendant_paths(root: Path) -> Iterator[str]:
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        for name in sorted(directory_names + file_names):
            yield os.path.relpath(os.path.join(directory, name), root)


def _entry_for(label: str, root: Path, relative_path: str) -> TreeEntry:
    path = root if relative_path == _ROOT_RELATIVE_PATH else root / relative_path
    status = path.lstat()
    if stat.S_ISLNK(status.st_mode):
        return _entry(
            label,
            relative_path,
            "symlink",
            status,
            link_target=os.readlink(path),
        )
    if stat.S_ISDIR(status.st_mode):
        return _entry(label, relative_path, "directory", status)
    return _entry(label, relative_path, "file", status, sha256=_file_digest(path))


def _entry(
    label: str,
    relative_path: str,
    kind: EntryKind,
    status: os.stat_result,
    *,
    sha256: str | None = None,
    link_target: str | None = None,
) -> TreeEntry:
    return TreeEntry(
        root_label=label,
        relative_path=relative_path,
        kind=kind,
        size=status.st_size,
        sha256=sha256,
        link_target=link_target,
        mtime_ns=status.st_mtime_ns,
    )


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_DIGEST_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()

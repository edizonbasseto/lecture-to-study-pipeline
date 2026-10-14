"""Filesystem adapters for the authenticated source-content use case."""

from __future__ import annotations

from typing import Protocol

from lecture_study.domain.source_content import (
    ResolvedSourceContent,
    relative_posix_parts,
)
from lecture_study.domain.source_universe import SourceEvidenceSpan
from lecture_study.source_content.manifest import (
    HmacSourceManifestCatalog,
    SourceManifestHmacAuthority,
)
from lecture_study.source_content.resolver import (
    ResolveSourceContent,
    SourceContentLimits,
    SourceContentResolutionError,
)


class RootedArtifactReadStore(Protocol):
    """Read exact files through a descriptor-relative rooted storage boundary."""

    def read_file(
        self,
        relative_parts: tuple[str, ...],
        *,
        maximum_bytes: int | None = None,
    ) -> bytes: ...


class RootedSourceArtifactReader:
    """Adapt safe descriptor-relative store reads to the inward reader port."""

    def __init__(self, store: RootedArtifactReadStore) -> None:
        self._store = store

    def read_file(self, relative_path: str, *, maximum_bytes: int) -> bytes:
        if (
            isinstance(maximum_bytes, bool)
            or not isinstance(maximum_bytes, int)
            or maximum_bytes <= 0
        ):
            raise ValueError("maximum_bytes must be a positive integer")
        relative_parts = relative_posix_parts(relative_path)
        try:
            return self._store.read_file(
                relative_parts,
                maximum_bytes=maximum_bytes,
            )
        except RuntimeError as error:
            raise SourceContentResolutionError(
                "source artifact could not be read safely within its maximum size"
            ) from error


class FilesystemSourceContentResolver:
    """Compose source-only authentication with one explicit root and manifest identity."""

    def __init__(
        self,
        *,
        store: RootedArtifactReadStore,
        expected_manifest_id: str,
        manifest_relative_path: str,
        authority: SourceManifestHmacAuthority,
        limits: SourceContentLimits,
    ) -> None:
        artifact_reader = RootedSourceArtifactReader(store)
        catalog = HmacSourceManifestCatalog(
            artifact_reader=artifact_reader,
            expected_manifest_id=expected_manifest_id,
            manifest_relative_path=manifest_relative_path,
            authority=authority,
            limits=limits,
        )
        self._resolver = ResolveSourceContent(
            catalog=catalog,
            artifact_reader=artifact_reader,
            limits=limits,
        )

    def resolve(self, span: SourceEvidenceSpan) -> ResolvedSourceContent:
        return self._resolver.resolve(span)

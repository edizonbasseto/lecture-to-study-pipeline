"""Authenticated content-addressed source resolution."""

from lecture_study.domain.source_content import (
    ResolvedSourceContent,
    SourceRecordProvenance,
)
from lecture_study.source_content.filesystem import (
    FilesystemSourceContentResolver,
    RootedSourceArtifactReader,
)
from lecture_study.source_content.manifest import (
    SOURCE_MANIFEST_CRYPTOGRAPHIC_DOMAIN,
    SOURCE_MANIFEST_SIGNATURE_PURPOSE,
    AuthenticatedSourceManifest,
    HmacSourceManifestCatalog,
    SourceArtifactManifestEntry,
    SourceManifestHmacAuthority,
    SourceManifestHmacKey,
    SourceManifestTrustError,
    SourceRecordManifestEntry,
    VerifiedSourceManifest,
    require_single_verified_source_manifest,
)
from lecture_study.source_content.resolver import (
    ResolveSourceContent,
    SourceArtifactReader,
    SourceContentLimits,
    SourceContentResolutionError,
    SourceContentResolver,
    SourceRecordCatalog,
)

__all__ = [
    "AuthenticatedSourceManifest",
    "FilesystemSourceContentResolver",
    "HmacSourceManifestCatalog",
    "ResolveSourceContent",
    "ResolvedSourceContent",
    "RootedSourceArtifactReader",
    "SOURCE_MANIFEST_CRYPTOGRAPHIC_DOMAIN",
    "SOURCE_MANIFEST_SIGNATURE_PURPOSE",
    "SourceArtifactManifestEntry",
    "SourceArtifactReader",
    "SourceContentLimits",
    "SourceContentResolutionError",
    "SourceContentResolver",
    "SourceManifestHmacAuthority",
    "SourceManifestHmacKey",
    "SourceManifestTrustError",
    "SourceRecordCatalog",
    "SourceRecordManifestEntry",
    "SourceRecordProvenance",
    "VerifiedSourceManifest",
    "require_single_verified_source_manifest",
]

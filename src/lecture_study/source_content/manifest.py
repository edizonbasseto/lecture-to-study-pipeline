"""Authenticated source-manifest contract and HMAC-backed catalog adapter."""

from __future__ import annotations

import hashlib
import hmac
import json
import weakref
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Final, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from lecture_study.domain.models import SafeIdentifier
from lecture_study.domain.source_content import (
    SourceRecordProvenance,
    require_canonical_relative_posix_path,
)
from lecture_study.domain.source_universe import EvidenceModality, SourceEvidenceSpan
from lecture_study.domain.versioning import SchemaVersionOne
from lecture_study.source_content.resolver import (
    SourceArtifactReader,
    SourceContentLimits,
    SourceContentResolutionError,
)

SOURCE_MANIFEST_CRYPTOGRAPHIC_DOMAIN: Final = "lecture-study.source-manifest.v1"
SOURCE_MANIFEST_SIGNATURE_PURPOSE: Final = "source-identity-location-integrity"
_SOURCE_MANIFEST_KEY_ID_CONTEXT = b"lecture-study.source-manifest.key-id.v1\x00"
_SOURCE_MANIFEST_SIGNATURE_CONTEXT = (
    SOURCE_MANIFEST_CRYPTOGRAPHIC_DOMAIN.encode("ascii")
    + b"\x00"
    + SOURCE_MANIFEST_SIGNATURE_PURPOSE.encode("ascii")
    + b"\x00"
)
_MINIMUM_SOURCE_MANIFEST_KEY_BYTES = 32
_SHA256_HEX_LENGTH = 64
_Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_NonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
_NonNegativeInteger = Annotated[int, Field(ge=0)]
_SAFE_IDENTIFIER_ADAPTER = TypeAdapter(SafeIdentifier)


class SourceManifestTrustError(RuntimeError):
    """Raised when dedicated source-manifest trust material or signatures fail."""


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class SourceManifestHmacKey:
    """Opaque key material accepted only by the source-manifest authority."""

    _material: bytes

    def __post_init__(self) -> None:
        if not isinstance(self._material, bytes):
            raise SourceManifestTrustError(
                "source-manifest trust material must be bytes"
            )
        if len(self._material) < _MINIMUM_SOURCE_MANIFEST_KEY_BYTES:
            raise SourceManifestTrustError(
                "source-manifest trust material must contain at least "
                f"{_MINIMUM_SOURCE_MANIFEST_KEY_BYTES} bytes"
            )


class SourceManifestHmacAuthority:
    """Authenticate source identity, location, and integrity only."""

    def __init__(self, key: SourceManifestHmacKey) -> None:
        if not isinstance(key, SourceManifestHmacKey):
            raise TypeError("key must be a SourceManifestHmacKey")
        self._key = key

    @property
    def key_id(self) -> str:
        return hashlib.sha256(
            _SOURCE_MANIFEST_KEY_ID_CONTEXT + self._key._material
        ).hexdigest()

    def sign(self, payload: bytes) -> str:
        if not isinstance(payload, bytes):
            raise TypeError("source-manifest authentication payload must be bytes")
        return hmac.new(
            self._key._material,
            _source_manifest_signature_message(payload),
            hashlib.sha256,
        ).hexdigest()

    def require_valid(
        self,
        *,
        payload: bytes,
        signature: str,
        key_id: str,
    ) -> None:
        if len(signature) != _SHA256_HEX_LENGTH:
            raise SourceManifestTrustError(
                "source-manifest signature has an invalid length"
            )
        if not hmac.compare_digest(self.key_id, key_id):
            raise SourceManifestTrustError(
                "source manifest was signed by a different source authority"
            )
        if not hmac.compare_digest(self.sign(payload), signature):
            raise SourceManifestTrustError("source-manifest signature is invalid")


class SourceRecordManifestEntry(BaseModel):
    """Authenticated byte range and content identity for one source record."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    record_id: SafeIdentifier
    modality: EvidenceModality
    byte_offset: _NonNegativeInteger
    byte_length: _NonNegativeInteger
    content_sha256: _Digest
    media_type: _NonBlankText


class SourceArtifactManifestEntry(BaseModel):
    """Whole-file identity and exact record inventory for one source artifact."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    artifact_id: SafeIdentifier
    relative_path: str
    artifact_sha256: _Digest
    byte_size: _NonNegativeInteger
    records: tuple[SourceRecordManifestEntry, ...] = Field(min_length=1)

    @field_validator("relative_path")
    @classmethod
    def _require_relative_path(cls, value: str) -> str:
        return require_canonical_relative_posix_path(value)

    @model_validator(mode="after")
    def _require_unambiguous_records(self) -> SourceArtifactManifestEntry:
        record_ids = tuple(record.record_id for record in self.records)
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("source artifact contains duplicate record identifiers")
        if any(
            record.byte_offset + record.byte_length > self.byte_size
            for record in self.records
        ):
            raise ValueError("source record byte range exceeds its artifact")
        return self


class AuthenticatedSourceManifest(BaseModel):
    """Authenticate source identity, location, and integrity without publication authority."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    manifest_id: SafeIdentifier
    manifest_relative_path: str
    lecture_id: SafeIdentifier
    artifacts: tuple[SourceArtifactManifestEntry, ...] = Field(min_length=1)
    cryptographic_domain: Literal["lecture-study.source-manifest.v1"]
    signature_purpose: Literal["source-identity-location-integrity"]
    trust_key_id: _Digest
    signature_sha256: _Digest

    @field_validator("manifest_relative_path")
    @classmethod
    def _require_manifest_relative_path(cls, value: str) -> str:
        return require_canonical_relative_posix_path(value)

    @model_validator(mode="after")
    def _require_unambiguous_artifacts(self) -> AuthenticatedSourceManifest:
        artifact_ids = tuple(artifact.artifact_id for artifact in self.artifacts)
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("source manifest contains duplicate artifact identifiers")
        relative_paths = tuple(artifact.relative_path for artifact in self.artifacts)
        if len(relative_paths) != len(set(relative_paths)):
            raise ValueError(
                "source manifest contains duplicate artifact relative paths"
            )
        return self

    def authentication_payload(self) -> bytes:
        payload = self.model_dump(mode="json", exclude={"signature_sha256"})
        return _canonical_json_bytes(payload)


@dataclass(
    frozen=True,
    slots=True,
    init=False,
    weakref_slot=True,
    repr=False,
    eq=False,
)
class VerifiedSourceManifest:
    """Issuer-only capability for one exact, currently authenticated manifest."""

    _manifest: AuthenticatedSourceManifest
    _manifest_sha256: str

    def __new__(cls) -> VerifiedSourceManifest:
        raise TypeError(
            "VerifiedSourceManifest is issued only by HmacSourceManifestCatalog"
        )

    def __copy__(self) -> VerifiedSourceManifest:
        raise TypeError("VerifiedSourceManifest capabilities cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> VerifiedSourceManifest:
        del memo
        raise TypeError("VerifiedSourceManifest capabilities cannot be copied")

    def require_current(self) -> AuthenticatedSourceManifest:
        """Reauthenticate the exact manifest bytes before critical consumption."""

        return _VERIFIED_SOURCE_MANIFEST_REGISTRY.require_current(self)

    def complete_source_records(self) -> tuple[SourceRecordProvenance, ...]:
        """Project every authenticated source record after current revalidation."""

        manifest = self.require_current()
        return _project_complete_source_records(
            manifest=manifest,
            manifest_sha256=self._manifest_sha256,
        )


@dataclass(frozen=True, slots=True)
class _VerifiedSourceManifestIssuance:
    reference: weakref.ReferenceType[VerifiedSourceManifest]
    catalog: HmacSourceManifestCatalog
    manifest: AuthenticatedSourceManifest
    manifest_bytes: bytes
    manifest_sha256: str
    canonical_manifest_bytes: bytes


class _VerifiedSourceManifestRegistry:
    """Retain issuer provenance outside caller-controlled capability fields."""

    def __init__(self) -> None:
        self._issuances: dict[int, _VerifiedSourceManifestIssuance] = {}

    def register(
        self,
        capability: VerifiedSourceManifest,
        *,
        catalog: HmacSourceManifestCatalog,
        manifest: AuthenticatedSourceManifest,
        manifest_bytes: bytes,
    ) -> None:
        capability_id = id(capability)
        reference = weakref.ref(
            capability,
            lambda expired: self._remove(capability_id, expired),
        )
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        self._issuances[capability_id] = _VerifiedSourceManifestIssuance(
            reference=reference,
            catalog=catalog,
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            manifest_sha256=manifest_sha256,
            canonical_manifest_bytes=_canonical_manifest_bytes(manifest),
        )

    def require_current(
        self,
        capability: VerifiedSourceManifest,
    ) -> AuthenticatedSourceManifest:
        issuance = self._require_authentic_issuance(capability)
        current_manifest, current_bytes = issuance.catalog._load_authenticated_manifest(
            lecture_id=str(issuance.manifest.lecture_id)
        )
        if not hmac.compare_digest(current_bytes, issuance.manifest_bytes):
            raise SourceContentResolutionError(
                "source manifest changed after capability issuance"
            )
        if (
            _canonical_manifest_bytes(current_manifest)
            != issuance.canonical_manifest_bytes
        ):
            raise SourceContentResolutionError(
                "source manifest identity changed after capability issuance"
            )
        return current_manifest

    def _require_authentic_issuance(
        self,
        capability: VerifiedSourceManifest,
    ) -> _VerifiedSourceManifestIssuance:
        issuance = self._issuances.get(id(capability))
        if issuance is None or issuance.reference() is not capability:
            raise SourceContentResolutionError(
                "source manifest capability was not authentically issued"
            )
        if (
            capability._manifest is not issuance.manifest
            or capability._manifest_sha256 != issuance.manifest_sha256
            or _canonical_manifest_bytes(capability._manifest)
            != issuance.canonical_manifest_bytes
        ):
            raise SourceContentResolutionError(
                "source manifest capability was altered after issuance"
            )
        return issuance

    def _remove(
        self,
        capability_id: int,
        expired: weakref.ReferenceType[VerifiedSourceManifest],
    ) -> None:
        issuance = self._issuances.get(capability_id)
        if issuance is not None and issuance.reference is expired:
            del self._issuances[capability_id]


_VERIFIED_SOURCE_MANIFEST_REGISTRY = _VerifiedSourceManifestRegistry()


class HmacSourceManifestCatalog:
    """Authenticate source identity, location, and integrity without publication authority."""

    def __init__(
        self,
        *,
        artifact_reader: SourceArtifactReader,
        expected_manifest_id: str,
        manifest_relative_path: str,
        authority: SourceManifestHmacAuthority,
        limits: SourceContentLimits,
    ) -> None:
        if not isinstance(authority, SourceManifestHmacAuthority):
            raise TypeError("authority must be a SourceManifestHmacAuthority")
        self._expected_manifest_id = _SAFE_IDENTIFIER_ADAPTER.validate_python(
            expected_manifest_id
        )
        self._manifest_relative_path = require_canonical_relative_posix_path(
            manifest_relative_path
        )
        self._artifact_reader = artifact_reader
        self._authority = authority
        self._limits = limits

    def locate(self, span: SourceEvidenceSpan) -> SourceRecordProvenance:
        manifest, manifest_bytes = self._load_authenticated_manifest(
            lecture_id=str(span.lecture_id)
        )
        artifact = self._find_artifact(manifest, str(span.source_artifact_id))
        record = self._find_record(artifact, str(span.source_record_id))
        return _project_source_record(
            manifest=manifest,
            manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            artifact=artifact,
            record=record,
        )

    def verify_manifest(self, *, lecture_id: str) -> VerifiedSourceManifest:
        """Issue a capability for the one configured authoritative manifest."""

        expected_lecture_id = _SAFE_IDENTIFIER_ADAPTER.validate_python(lecture_id)
        manifest, manifest_bytes = self._load_authenticated_manifest(
            lecture_id=str(expected_lecture_id)
        )
        return _issue_verified_source_manifest(
            catalog=self,
            manifest=manifest,
            manifest_bytes=manifest_bytes,
        )

    def _load_authenticated_manifest(
        self,
        *,
        lecture_id: str,
    ) -> tuple[AuthenticatedSourceManifest, bytes]:
        manifest_bytes = self._read_manifest()
        manifest = self._parse_manifest(manifest_bytes)
        self._require_authenticated(manifest)
        self._require_manifest_identity(manifest)
        self._require_within_limits(manifest)
        if manifest.lecture_id != lecture_id:
            raise SourceContentResolutionError(
                "source manifest belongs to a different lecture"
            )
        return manifest, manifest_bytes

    def _read_manifest(self) -> bytes:
        try:
            return self._artifact_reader.read_file(
                self._manifest_relative_path,
                maximum_bytes=self._limits.maximum_manifest_bytes,
            )
        except SourceContentResolutionError as error:
            raise SourceContentResolutionError(
                "source manifest could not be read safely within its maximum size"
            ) from error

    @staticmethod
    def _parse_manifest(manifest_bytes: bytes) -> AuthenticatedSourceManifest:
        try:
            parsed = _parse_strict_json_object(manifest_bytes)
            return AuthenticatedSourceManifest.model_validate_json(
                _canonical_json_bytes(parsed)
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValidationError,
            ValueError,
        ) as error:
            raise SourceContentResolutionError(
                "source manifest contains invalid or ambiguous JSON"
            ) from error

    def _require_authenticated(self, manifest: AuthenticatedSourceManifest) -> None:
        try:
            self._authority.require_valid(
                payload=manifest.authentication_payload(),
                signature=manifest.signature_sha256,
                key_id=manifest.trust_key_id,
            )
        except SourceManifestTrustError as error:
            raise SourceContentResolutionError(
                "source manifest authentication failed"
            ) from error

    def _require_manifest_identity(
        self,
        manifest: AuthenticatedSourceManifest,
    ) -> None:
        if (
            manifest.manifest_id != self._expected_manifest_id
            or manifest.manifest_relative_path != self._manifest_relative_path
        ):
            raise SourceContentResolutionError(
                "source manifest identity does not match the configured source location"
            )

    def _require_within_limits(self, manifest: AuthenticatedSourceManifest) -> None:
        if len(manifest.artifacts) > self._limits.maximum_artifacts:
            raise SourceContentResolutionError(
                "source manifest exceeds the injected artifact-count limit"
            )
        for artifact in manifest.artifacts:
            if artifact.byte_size > self._limits.maximum_artifact_bytes:
                raise SourceContentResolutionError(
                    "source artifact exceeds the injected maximum size limit"
                )
            if len(artifact.records) > self._limits.maximum_records_per_artifact:
                raise SourceContentResolutionError(
                    "source artifact exceeds the injected record-count limit"
                )
            if any(
                record.byte_length > self._limits.maximum_record_bytes
                for record in artifact.records
            ):
                raise SourceContentResolutionError(
                    "source record exceeds the injected maximum size limit"
                )

    @staticmethod
    def _find_artifact(
        manifest: AuthenticatedSourceManifest,
        artifact_id: str,
    ) -> SourceArtifactManifestEntry:
        for artifact in manifest.artifacts:
            if artifact.artifact_id == artifact_id:
                return artifact
        raise SourceContentResolutionError(
            "source artifact is absent from the authenticated manifest"
        )

    @staticmethod
    def _find_record(
        artifact: SourceArtifactManifestEntry,
        record_id: str,
    ) -> SourceRecordManifestEntry:
        for record in artifact.records:
            if record.record_id == record_id:
                return record
        raise SourceContentResolutionError(
            "source record is absent from the authenticated manifest"
        )


def require_single_verified_source_manifest(
    manifests: Sequence[VerifiedSourceManifest],
) -> VerifiedSourceManifest:
    """Require exactly one current issuer capability for review composition."""

    if len(manifests) != 1:
        raise SourceContentResolutionError(
            "exactly one authenticated source manifest is required per lecture"
        )
    capability = manifests[0]
    if type(capability) is not VerifiedSourceManifest:
        raise SourceContentResolutionError(
            "source manifest input is not an issuer-authenticated capability"
        )
    capability.require_current()
    return capability


def _issue_verified_source_manifest(
    *,
    catalog: HmacSourceManifestCatalog,
    manifest: AuthenticatedSourceManifest,
    manifest_bytes: bytes,
) -> VerifiedSourceManifest:
    capability = object.__new__(VerifiedSourceManifest)
    object.__setattr__(capability, "_manifest", manifest)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    object.__setattr__(capability, "_manifest_sha256", manifest_sha256)
    _VERIFIED_SOURCE_MANIFEST_REGISTRY.register(
        capability,
        catalog=catalog,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
    )
    return capability


def _project_complete_source_records(
    *,
    manifest: AuthenticatedSourceManifest,
    manifest_sha256: str,
) -> tuple[SourceRecordProvenance, ...]:
    entries = (
        (artifact, record)
        for artifact in manifest.artifacts
        for record in artifact.records
    )
    ordered_entries = sorted(
        entries,
        key=lambda entry: (
            str(entry[0].artifact_id),
            entry[0].relative_path,
            str(entry[1].record_id),
            entry[1].byte_offset,
            entry[1].byte_length,
            entry[1].content_sha256,
        ),
    )
    return tuple(
        _project_source_record(
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            artifact=artifact,
            record=record,
        )
        for artifact, record in ordered_entries
    )


def _project_source_record(
    *,
    manifest: AuthenticatedSourceManifest,
    manifest_sha256: str,
    artifact: SourceArtifactManifestEntry,
    record: SourceRecordManifestEntry,
) -> SourceRecordProvenance:
    return SourceRecordProvenance(
        schema_version=1,
        manifest_relative_path=manifest.manifest_relative_path,
        manifest_sha256=manifest_sha256,
        manifest_signature_sha256=manifest.signature_sha256,
        trust_key_id=manifest.trust_key_id,
        lecture_id=manifest.lecture_id,
        source_artifact_id=artifact.artifact_id,
        artifact_relative_path=artifact.relative_path,
        artifact_sha256=artifact.artifact_sha256,
        artifact_byte_size=artifact.byte_size,
        source_record_id=record.record_id,
        modality=record.modality,
        byte_offset=record.byte_offset,
        byte_length=record.byte_length,
        content_sha256=record.content_sha256,
        media_type=record.media_type,
    )


def _canonical_manifest_bytes(manifest: AuthenticatedSourceManifest) -> bytes:
    return _canonical_json_bytes(manifest.model_dump(mode="json"))


def _source_manifest_signature_message(payload: bytes) -> bytes:
    return (
        len(_SOURCE_MANIFEST_SIGNATURE_CONTEXT).to_bytes(4, "big")
        + _SOURCE_MANIFEST_SIGNATURE_CONTEXT
        + len(payload).to_bytes(8, "big")
        + payload
    )


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _parse_strict_json_object(file_bytes: bytes) -> dict[str, object]:
    decoded = file_bytes.decode("utf-8", errors="strict")

    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise json.JSONDecodeError(
                    f"duplicate JSON key: {key}",
                    decoded,
                    0,
                )
            result[key] = value
        return result

    def reject_non_finite(value: str) -> None:
        raise json.JSONDecodeError(
            f"non-finite JSON value: {value}",
            decoded,
            0,
        )

    parsed = json.loads(
        decoded,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_non_finite,
    )
    if not isinstance(parsed, dict):
        raise json.JSONDecodeError(
            "top-level JSON value must be an object",
            decoded,
            0,
        )
    return cast(dict[str, object], parsed)

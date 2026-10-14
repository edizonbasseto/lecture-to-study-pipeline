from __future__ import annotations

import ast
import copy
import hashlib
import hmac
import inspect
import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from lecture_study.domain.source_content import ResolvedSourceContent
from lecture_study.domain.source_universe import (
    EvidenceModality,
    SourceEvidenceSpan,
    SourceSpanClassification,
)
from lecture_study.quality.delivery import (
    DeliveryVerificationError,
    RootedArtifactStore,
)
from lecture_study.quality.trust import HmacQualityTrust
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
    SourceRecordManifestEntry,
    VerifiedSourceManifest,
    require_single_verified_source_manifest,
)
from lecture_study.source_content.resolver import (
    SourceContentLimits,
    SourceContentResolutionError,
)

_LECTURE_ID = "lecture-001"
_MANIFEST_ID = "source-manifest-001"
_MANIFEST_RELATIVE_PATH = "manifests/source.json"
_TRUST_KEY = b"unit-test-only-not-a-secret-value"


@pytest.mark.parametrize(
    "module_relative_path",
    (
        "src/lecture_study/source_content/filesystem.py",
        "src/lecture_study/source_content/manifest.py",
    ),
)
def test_source_content_adapters_do_not_depend_on_quality_delivery(
    module_relative_path: str,
) -> None:
    module_path = Path(__file__).parents[1] / module_relative_path
    syntax_tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "lecture_study.quality.delivery" not in imported_modules


@dataclass(frozen=True, slots=True)
class _ArtifactFixture:
    artifact_id: str
    relative_path: str
    modality: EvidenceModality
    record_id: str
    artifact_bytes: bytes
    record_offset: int
    record_bytes: bytes
    media_type: str

    @property
    def record_length(self) -> int:
        return len(self.record_bytes)


@dataclass(frozen=True, slots=True)
class _ResolverFixture:
    root: Path
    authority: SourceManifestHmacAuthority
    limits: SourceContentLimits
    artifacts: tuple[_ArtifactFixture, ...]
    manifest: AuthenticatedSourceManifest

    def catalog(self) -> HmacSourceManifestCatalog:
        return HmacSourceManifestCatalog(
            artifact_reader=RootedSourceArtifactReader(RootedArtifactStore(self.root)),
            expected_manifest_id=_MANIFEST_ID,
            manifest_relative_path=_MANIFEST_RELATIVE_PATH,
            authority=self.authority,
            limits=self.limits,
        )

    def resolver(
        self,
        *,
        expected_manifest_id: str = _MANIFEST_ID,
        manifest_relative_path: str = _MANIFEST_RELATIVE_PATH,
    ) -> FilesystemSourceContentResolver:
        return FilesystemSourceContentResolver(
            store=RootedArtifactStore(self.root),
            expected_manifest_id=expected_manifest_id,
            manifest_relative_path=manifest_relative_path,
            authority=self.authority,
            limits=self.limits,
        )

    def span(
        self,
        artifact: _ArtifactFixture,
        *,
        lecture_id: str = _LECTURE_ID,
        artifact_id: str | None = None,
        record_id: str | None = None,
        modality: EvidenceModality | None = None,
        content_sha256: str | None = None,
    ) -> SourceEvidenceSpan:
        return SourceEvidenceSpan(
            schema_version=1,
            span_id=f"span-{artifact.record_id}",
            lecture_id=lecture_id,
            source_artifact_id=artifact_id or artifact.artifact_id,
            source_record_id=record_id or artifact.record_id,
            modality=modality or artifact.modality,
            start_time=0.0,
            end_time=1.0,
            classification=SourceSpanClassification.INSTRUCTIONAL,
            content_sha256=(
                content_sha256 or hashlib.sha256(artifact.record_bytes).hexdigest()
            ),
        )

    def write_manifest(
        self,
        manifest: AuthenticatedSourceManifest,
        *,
        relative_path: str = _MANIFEST_RELATIVE_PATH,
    ) -> None:
        manifest_path = self.root / relative_path
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_bytes(_canonical_manifest_bytes(manifest))

    def sign_manifest(
        self,
        manifest: AuthenticatedSourceManifest,
    ) -> AuthenticatedSourceManifest:
        return manifest.model_copy(
            update={
                "signature_sha256": self.authority.sign(
                    manifest.authentication_payload()
                )
            }
        )


def _canonical_manifest_bytes(manifest: AuthenticatedSourceManifest) -> bytes:
    return json.dumps(
        manifest.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _limits(
    *,
    maximum_manifest_bytes: int = 64 * 1024,
    maximum_artifact_bytes: int = 64 * 1024,
    maximum_record_bytes: int = 16 * 1024,
    maximum_artifacts: int = 16,
    maximum_records_per_artifact: int = 64,
) -> SourceContentLimits:
    return SourceContentLimits(
        maximum_manifest_bytes=maximum_manifest_bytes,
        maximum_artifact_bytes=maximum_artifact_bytes,
        maximum_record_bytes=maximum_record_bytes,
        maximum_artifacts=maximum_artifacts,
        maximum_records_per_artifact=maximum_records_per_artifact,
    )


def _artifact_fixtures() -> tuple[_ArtifactFixture, ...]:
    transcript_record = "Coração\r\nsem normalização".encode()
    ocr_record = "Dose: 2,5 mg/kg\nVia oral".encode()
    visual_record = b"\x89PNG\r\n\x1a\n\x00\xffencoded"
    return (
        _ArtifactFixture(
            artifact_id="transcript-artifact",
            relative_path="transcript/source.bin",
            modality=EvidenceModality.TRANSCRIPT,
            record_id="segment-001",
            artifact_bytes=b"HEADER|" + transcript_record + b"|TRAILER",
            record_offset=len(b"HEADER|"),
            record_bytes=transcript_record,
            media_type="text/plain; charset=utf-8",
        ),
        _ArtifactFixture(
            artifact_id="ocr-artifact",
            relative_path="ocr/source.bin",
            modality=EvidenceModality.OCR,
            record_id="frame-001-ocr",
            artifact_bytes=b"prefix:" + ocr_record,
            record_offset=len(b"prefix:"),
            record_bytes=ocr_record,
            media_type="text/plain; charset=utf-8",
        ),
        _ArtifactFixture(
            artifact_id="visual-artifact",
            relative_path="visual/frame.png",
            modality=EvidenceModality.VISUAL,
            record_id="frame-001-visual",
            artifact_bytes=visual_record,
            record_offset=0,
            record_bytes=visual_record,
            media_type="image/png",
        ),
    )


def _manifest_entry(artifact: _ArtifactFixture) -> SourceArtifactManifestEntry:
    return SourceArtifactManifestEntry(
        artifact_id=artifact.artifact_id,
        relative_path=artifact.relative_path,
        artifact_sha256=hashlib.sha256(artifact.artifact_bytes).hexdigest(),
        byte_size=len(artifact.artifact_bytes),
        records=(
            SourceRecordManifestEntry(
                record_id=artifact.record_id,
                modality=artifact.modality,
                byte_offset=artifact.record_offset,
                byte_length=artifact.record_length,
                content_sha256=hashlib.sha256(artifact.record_bytes).hexdigest(),
                media_type=artifact.media_type,
            ),
        ),
    )


def _build_fixture(
    tmp_path: Path,
    *,
    limits: SourceContentLimits | None = None,
    artifacts: tuple[_ArtifactFixture, ...] | None = None,
) -> _ResolverFixture:
    selected_artifacts = artifacts or _artifact_fixtures()
    for artifact in selected_artifacts:
        artifact_path = tmp_path / artifact.relative_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(artifact.artifact_bytes)

    authority = SourceManifestHmacAuthority(SourceManifestHmacKey(_TRUST_KEY))
    unsigned_manifest = AuthenticatedSourceManifest(
        schema_version=1,
        manifest_id=_MANIFEST_ID,
        manifest_relative_path=_MANIFEST_RELATIVE_PATH,
        lecture_id=_LECTURE_ID,
        artifacts=tuple(_manifest_entry(artifact) for artifact in selected_artifacts),
        cryptographic_domain=SOURCE_MANIFEST_CRYPTOGRAPHIC_DOMAIN,
        signature_purpose=SOURCE_MANIFEST_SIGNATURE_PURPOSE,
        trust_key_id=authority.key_id,
        signature_sha256="0" * 64,
    )
    manifest = unsigned_manifest.model_copy(
        update={
            "signature_sha256": authority.sign(
                unsigned_manifest.authentication_payload()
            )
        }
    )
    fixture = _ResolverFixture(
        root=tmp_path,
        authority=authority,
        limits=limits or _limits(),
        artifacts=selected_artifacts,
        manifest=manifest,
    )
    fixture.write_manifest(manifest)
    return fixture


def test_resolver_preserves_exact_text_and_visual_bytes(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    resolver = fixture.resolver()

    for artifact in fixture.artifacts:
        result = resolver.resolve(fixture.span(artifact))

        assert isinstance(result, ResolvedSourceContent)
        assert result.raw_bytes == artifact.record_bytes
        assert result.provenance.byte_offset == artifact.record_offset
        assert result.provenance.byte_length == artifact.record_length
        assert (
            result.provenance.artifact_sha256
            == hashlib.sha256(artifact.artifact_bytes).hexdigest()
        )
        assert result.provenance.manifest_relative_path == _MANIFEST_RELATIVE_PATH
        assert (
            result.provenance.manifest_sha256
            == hashlib.sha256(
                (tmp_path / _MANIFEST_RELATIVE_PATH).read_bytes()
            ).hexdigest()
        )
        if artifact.modality is EvidenceModality.VISUAL:
            assert result.text is None
        else:
            assert result.text is not None
            assert result.text.encode("utf-8") == artifact.record_bytes


def test_all_source_content_limits_are_required_and_positive() -> None:
    parameters = inspect.signature(SourceContentLimits).parameters

    assert parameters
    assert all(
        parameter.default is inspect.Parameter.empty
        for parameter in parameters.values()
    )
    with pytest.raises(ValueError, match="positive integer"):
        _limits(maximum_record_bytes=0)
    with pytest.raises(ValueError, match="positive integer"):
        _limits(maximum_artifacts=True)


def test_resolver_rejects_unauthenticated_manifest(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    fixture.write_manifest(
        fixture.manifest.model_copy(update={"signature_sha256": "f" * 64})
    )

    with pytest.raises(SourceContentResolutionError, match="authentication failed"):
        fixture.resolver().resolve(fixture.span(fixture.artifacts[0]))


def test_source_manifest_key_and_signature_are_domain_separated() -> None:
    source_authority = SourceManifestHmacAuthority(SourceManifestHmacKey(_TRUST_KEY))
    publication_trust = HmacQualityTrust(_TRUST_KEY)
    payload = b"same-payload"

    assert source_authority.key_id != publication_trust.key_id
    assert source_authority.sign(payload) != publication_trust.sign(payload)


def test_publication_trust_signature_cannot_authenticate_source_manifest(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    publication_trust = HmacQualityTrust(_TRUST_KEY)
    confused_manifest = fixture.manifest.model_copy(
        update={
            "trust_key_id": fixture.authority.key_id,
            "signature_sha256": publication_trust.sign(
                fixture.manifest.authentication_payload()
            ),
        }
    )
    fixture.write_manifest(confused_manifest)

    with pytest.raises(SourceContentResolutionError, match="authentication failed"):
        fixture.resolver().resolve(fixture.span(fixture.artifacts[0]))


@pytest.mark.parametrize(
    "confused_context",
    [
        b"lecture-study.publication-certificate.v1\x00",
        b"lecture-study.source-manifest.v1\x00publication-approval\x00",
    ],
)
def test_source_manifest_rejects_domain_or_purpose_confusion(
    tmp_path: Path,
    confused_context: bytes,
) -> None:
    fixture = _build_fixture(tmp_path)
    confused_signature = hmac.new(
        _TRUST_KEY,
        confused_context + fixture.manifest.authentication_payload(),
        hashlib.sha256,
    ).hexdigest()
    fixture.write_manifest(
        fixture.manifest.model_copy(update={"signature_sha256": confused_signature})
    )

    with pytest.raises(SourceContentResolutionError, match="authentication failed"):
        fixture.resolver().resolve(fixture.span(fixture.artifacts[0]))


def test_source_manifest_rejects_replay_across_lecture_identity(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    replayed_span = fixture.span(
        fixture.artifacts[0],
        lecture_id="lecture-002",
    )

    with pytest.raises(SourceContentResolutionError, match="lecture"):
        fixture.resolver().resolve(replayed_span)


def test_source_manifest_rejects_replay_across_manifest_id(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)

    with pytest.raises(SourceContentResolutionError, match="manifest identity"):
        fixture.resolver(
            expected_manifest_id="source-manifest-002",
        ).resolve(fixture.span(fixture.artifacts[0]))


def test_source_manifest_rejects_replay_across_manifest_relative_path(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    replay_path = "manifests/replayed.json"
    fixture.write_manifest(fixture.manifest, relative_path=replay_path)

    with pytest.raises(SourceContentResolutionError, match="manifest identity"):
        fixture.resolver(
            manifest_relative_path=replay_path,
        ).resolve(fixture.span(fixture.artifacts[0]))


def test_source_manifest_exposes_no_publication_authority(tmp_path: Path) -> None:
    manifest = _build_fixture(tmp_path).manifest

    assert manifest.cryptographic_domain == SOURCE_MANIFEST_CRYPTOGRAPHIC_DOMAIN
    assert manifest.signature_purpose == SOURCE_MANIFEST_SIGNATURE_PURPOSE
    assert not hasattr(manifest, "status")
    assert not hasattr(manifest, "ready_for_publication")
    assert not hasattr(manifest, "approved")


def test_resolver_rejects_missing_manifest(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    (tmp_path / _MANIFEST_RELATIVE_PATH).unlink()

    with pytest.raises(SourceContentResolutionError, match="manifest"):
        fixture.resolver().resolve(fixture.span(fixture.artifacts[0]))


@pytest.mark.parametrize(
    ("span_update", "message"),
    [
        ({"lecture_id": "lecture-002"}, "lecture"),
        ({"source_artifact_id": "missing-artifact"}, "artifact"),
        ({"source_record_id": "missing-record"}, "record"),
        ({"modality": EvidenceModality.OCR}, "modality"),
        ({"content_sha256": "a" * 64}, "content hash"),
    ],
)
def test_resolver_rejects_wrong_span_binding(
    tmp_path: Path,
    span_update: dict[str, object],
    message: str,
) -> None:
    fixture = _build_fixture(tmp_path)
    artifact = fixture.artifacts[0]
    span = fixture.span(artifact).model_copy(update=span_update)

    with pytest.raises(SourceContentResolutionError, match=message):
        fixture.resolver().resolve(span)


def test_resolver_rejects_substituted_whole_artifact(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    artifact = fixture.artifacts[0]
    artifact_path = tmp_path / artifact.relative_path
    substituted_bytes = bytearray(artifact.artifact_bytes)
    substituted_bytes[-1] ^= 1
    artifact_path.write_bytes(bytes(substituted_bytes))

    with pytest.raises(SourceContentResolutionError, match="artifact hash"):
        fixture.resolver().resolve(fixture.span(artifact))


def test_resolver_rejects_stale_record_hash_in_signed_manifest(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    artifact = fixture.artifacts[0]
    original_entry = fixture.manifest.artifacts[0]
    stale_record = original_entry.records[0].model_copy(
        update={"content_sha256": "a" * 64}
    )
    stale_artifact = original_entry.model_copy(update={"records": (stale_record,)})
    unsigned = fixture.manifest.model_copy(
        update={
            "artifacts": (stale_artifact, *fixture.manifest.artifacts[1:]),
            "signature_sha256": "0" * 64,
        }
    )
    fixture.write_manifest(fixture.sign_manifest(unsigned))

    with pytest.raises(SourceContentResolutionError, match="content hash"):
        fixture.resolver().resolve(fixture.span(artifact))


def test_resolver_rejects_record_bytes_that_do_not_match_bound_hash(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    artifact = fixture.artifacts[0]
    authenticated_but_wrong_digest = "a" * 64
    original_entry = fixture.manifest.artifacts[0]
    stale_record = original_entry.records[0].model_copy(
        update={"content_sha256": authenticated_but_wrong_digest}
    )
    stale_artifact = original_entry.model_copy(update={"records": (stale_record,)})
    unsigned = fixture.manifest.model_copy(
        update={
            "artifacts": (stale_artifact, *fixture.manifest.artifacts[1:]),
            "signature_sha256": "0" * 64,
        }
    )
    fixture.write_manifest(fixture.sign_manifest(unsigned))
    span = fixture.span(
        artifact,
        content_sha256=authenticated_but_wrong_digest,
    )

    with pytest.raises(SourceContentResolutionError, match="record hash"):
        fixture.resolver().resolve(span)


def test_resolver_rejects_invalid_utf8_for_text_modality(tmp_path: Path) -> None:
    invalid_text = _ArtifactFixture(
        artifact_id="transcript-artifact",
        relative_path="transcript/source.bin",
        modality=EvidenceModality.TRANSCRIPT,
        record_id="segment-001",
        artifact_bytes=b"\xff\xfe",
        record_offset=0,
        record_bytes=b"\xff\xfe",
        media_type="text/plain; charset=utf-8",
    )
    fixture = _build_fixture(tmp_path, artifacts=(invalid_text,))

    with pytest.raises(SourceContentResolutionError, match="UTF-8"):
        fixture.resolver().resolve(fixture.span(invalid_text))


@pytest.mark.parametrize(
    "limits",
    [
        _limits(maximum_manifest_bytes=1),
        _limits(maximum_artifact_bytes=1),
        _limits(maximum_record_bytes=1),
        _limits(maximum_artifacts=1),
    ],
)
def test_resolver_rejects_injected_limit_violations(
    tmp_path: Path,
    limits: SourceContentLimits,
) -> None:
    fixture = _build_fixture(tmp_path, limits=limits)

    with pytest.raises(SourceContentResolutionError, match="limit|maximum|size"):
        fixture.resolver().resolve(fixture.span(fixture.artifacts[0]))


def test_resolver_rejects_record_count_limit(tmp_path: Path) -> None:
    fixture = _build_fixture(
        tmp_path,
        limits=_limits(maximum_records_per_artifact=1),
    )
    artifact = fixture.manifest.artifacts[0]
    second_record = artifact.records[0].model_copy(update={"record_id": "segment-002"})
    expanded_artifact = SourceArtifactManifestEntry.model_validate(
        {
            **artifact.model_dump(mode="python"),
            "records": (*artifact.records, second_record),
        }
    )
    unsigned = fixture.manifest.model_copy(
        update={
            "artifacts": (expanded_artifact, *fixture.manifest.artifacts[1:]),
            "signature_sha256": "0" * 64,
        }
    )
    fixture.write_manifest(fixture.sign_manifest(unsigned))

    with pytest.raises(SourceContentResolutionError, match="record-count limit"):
        fixture.resolver().resolve(fixture.span(fixture.artifacts[0]))


@pytest.mark.parametrize(
    "relative_path",
    [
        "",
        "/absolute/source.bin",
        "../source.bin",
        "source/../source.bin",
        "source\\source.bin",
        "source//source.bin",
        "source/\x00.bin",
    ],
)
def test_manifest_rejects_unsafe_or_noncanonical_artifact_path(
    relative_path: str,
) -> None:
    artifact = _artifact_fixtures()[0]

    with pytest.raises(ValidationError, match="relative_path"):
        SourceArtifactManifestEntry.model_validate(
            {
                **_manifest_entry(artifact).model_dump(mode="python"),
                "relative_path": relative_path,
            }
        )


@pytest.mark.parametrize(
    "manifest_relative_path",
    ["", "/manifest.json", "../manifest.json", "a/../manifest.json", "a\\b.json"],
)
def test_resolver_rejects_unsafe_manifest_path(
    tmp_path: Path,
    manifest_relative_path: str,
) -> None:
    fixture = _build_fixture(tmp_path)

    with pytest.raises(ValueError, match="relative POSIX path"):
        FilesystemSourceContentResolver(
            store=RootedArtifactStore(tmp_path),
            expected_manifest_id=_MANIFEST_ID,
            manifest_relative_path=manifest_relative_path,
            authority=fixture.authority,
            limits=fixture.limits,
        )


def test_manifest_rejects_duplicate_artifacts_paths_and_records() -> None:
    artifact = _manifest_entry(_artifact_fixtures()[0])
    duplicate_record = artifact.records[0].model_copy()

    with pytest.raises(ValidationError, match="record"):
        SourceArtifactManifestEntry.model_validate(
            {
                **artifact.model_dump(mode="python"),
                "records": (artifact.records[0], duplicate_record),
            }
        )
    with pytest.raises(ValidationError, match="artifact"):
        AuthenticatedSourceManifest(
            schema_version=1,
            manifest_id=_MANIFEST_ID,
            manifest_relative_path=_MANIFEST_RELATIVE_PATH,
            lecture_id=_LECTURE_ID,
            artifacts=(artifact, artifact.model_copy()),
            cryptographic_domain=SOURCE_MANIFEST_CRYPTOGRAPHIC_DOMAIN,
            signature_purpose=SOURCE_MANIFEST_SIGNATURE_PURPOSE,
            trust_key_id="a" * 64,
            signature_sha256="b" * 64,
        )
    with pytest.raises(ValidationError, match="relative path"):
        AuthenticatedSourceManifest(
            schema_version=1,
            manifest_id=_MANIFEST_ID,
            manifest_relative_path=_MANIFEST_RELATIVE_PATH,
            lecture_id=_LECTURE_ID,
            artifacts=(
                artifact,
                artifact.model_copy(update={"artifact_id": "other-artifact"}),
            ),
            cryptographic_domain=SOURCE_MANIFEST_CRYPTOGRAPHIC_DOMAIN,
            signature_purpose=SOURCE_MANIFEST_SIGNATURE_PURPOSE,
            trust_key_id="a" * 64,
            signature_sha256="b" * 64,
        )


def test_manifest_rejects_record_range_outside_artifact() -> None:
    artifact = _manifest_entry(_artifact_fixtures()[0])
    record = artifact.records[0].model_copy(update={"byte_offset": artifact.byte_size})

    with pytest.raises(ValidationError, match="byte range"):
        SourceArtifactManifestEntry.model_validate(
            {
                **artifact.model_dump(mode="python"),
                "records": (record,),
            }
        )


def test_manifest_reader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    manifest_path = tmp_path / _MANIFEST_RELATIVE_PATH
    manifest_path.write_text(
        json.dumps(fixture.manifest.model_dump(mode="json"))[:-1]
        + ',"lecture_id":"lecture-duplicate"}'
    )

    with pytest.raises(SourceContentResolutionError, match="invalid or ambiguous"):
        fixture.resolver().resolve(fixture.span(fixture.artifacts[0]))


def test_resolver_rejects_missing_artifact(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    artifact = fixture.artifacts[0]
    (tmp_path / artifact.relative_path).unlink()

    with pytest.raises(SourceContentResolutionError, match="read safely"):
        fixture.resolver().resolve(fixture.span(artifact))


@pytest.mark.parametrize("unsafe_kind", ["symlink", "hardlink", "directory"])
def test_resolver_rejects_unsafe_artifact_file_types(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    fixture = _build_fixture(tmp_path)
    artifact = fixture.artifacts[0]
    artifact_path = tmp_path / artifact.relative_path
    original_path = tmp_path / "original.bin"

    if unsafe_kind == "symlink":
        artifact_path.rename(original_path)
        artifact_path.symlink_to(original_path)
    elif unsafe_kind == "hardlink":
        os.link(artifact_path, original_path)
    else:
        artifact_path.unlink()
        artifact_path.mkdir()

    with pytest.raises(SourceContentResolutionError, match="read safely"):
        fixture.resolver().resolve(fixture.span(artifact))


def test_resolver_propagates_concurrent_mutation_as_safe_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path)
    original_check = RootedArtifactStore._require_unchanged
    invocation_count = 0

    def reject_artifact_mutation(
        descriptor: int,
        expected_status: os.stat_result,
    ) -> None:
        nonlocal invocation_count
        invocation_count += 1
        if invocation_count == 2:
            raise DeliveryVerificationError("artifact changed while being verified")
        original_check(descriptor, expected_status)

    monkeypatch.setattr(
        RootedArtifactStore,
        "_require_unchanged",
        staticmethod(reject_artifact_mutation),
    )

    with pytest.raises(SourceContentResolutionError, match="read safely"):
        fixture.resolver().resolve(fixture.span(fixture.artifacts[0]))


def test_catalog_issues_current_capability_for_complete_manifest(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)

    capability = fixture.catalog().verify_manifest(lecture_id=_LECTURE_ID)
    manifest = capability.require_current()
    records = capability.complete_source_records()

    assert manifest == fixture.manifest
    assert len(records) == sum(
        len(artifact.records) for artifact in fixture.manifest.artifacts
    )
    assert tuple(str(record.source_artifact_id) for record in records) == tuple(
        sorted(str(artifact.artifact_id) for artifact in fixture.manifest.artifacts)
    )
    assert {record.lecture_id for record in records} == {_LECTURE_ID}
    assert {record.manifest_signature_sha256 for record in records} == {
        fixture.manifest.signature_sha256
    }
    assert {record.trust_key_id for record in records} == {
        fixture.manifest.trust_key_id
    }


@pytest.mark.parametrize("capability_count", [0, 2])
def test_manifest_composition_requires_exactly_one_issuer_capability(
    tmp_path: Path,
    capability_count: int,
) -> None:
    fixture = _build_fixture(tmp_path)
    capability = fixture.catalog().verify_manifest(lecture_id=_LECTURE_ID)

    with pytest.raises(SourceContentResolutionError, match="exactly one"):
        require_single_verified_source_manifest((capability,) * capability_count)


def test_manifest_composition_accepts_one_current_issuer_capability(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    capability = fixture.catalog().verify_manifest(lecture_id=_LECTURE_ID)

    assert require_single_verified_source_manifest((capability,)) is capability


def test_signed_manifest_model_is_not_an_issuer_capability(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)

    with pytest.raises(SourceContentResolutionError, match="issuer-authenticated"):
        require_single_verified_source_manifest((fixture.manifest,))  # type: ignore[arg-type]

    assert not hasattr(fixture.manifest, "complete_source_records")


def test_verified_manifest_rejects_public_construction_and_model_shortcuts() -> None:
    with pytest.raises(TypeError, match="issued only"):
        VerifiedSourceManifest()

    assert not hasattr(VerifiedSourceManifest, "model_copy")
    assert not hasattr(VerifiedSourceManifest, "model_construct")


def test_forged_verified_manifest_allocation_has_no_authority(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    forged = object.__new__(VerifiedSourceManifest)
    object.__setattr__(forged, "_manifest", fixture.manifest)
    object.__setattr__(
        forged,
        "_manifest_sha256",
        hashlib.sha256(_canonical_manifest_bytes(fixture.manifest)).hexdigest(),
    )

    with pytest.raises(SourceContentResolutionError, match="not authentically issued"):
        forged.require_current()


def test_verified_manifest_copy_and_deepcopy_do_not_transfer_authority(
    tmp_path: Path,
) -> None:
    capability = (
        _build_fixture(tmp_path).catalog().verify_manifest(lecture_id=_LECTURE_ID)
    )

    with pytest.raises(TypeError, match="cannot be copied"):
        copy.copy(capability)
    with pytest.raises(TypeError, match="cannot be copied"):
        copy.deepcopy(capability)


def test_verified_manifest_rejects_capability_field_substitution(
    tmp_path: Path,
) -> None:
    capability = (
        _build_fixture(tmp_path).catalog().verify_manifest(lecture_id=_LECTURE_ID)
    )
    object.__setattr__(capability, "_manifest_sha256", "0" * 64)

    with pytest.raises(SourceContentResolutionError, match="altered after issuance"):
        capability.require_current()


def test_verified_manifest_rejects_manifest_change_after_issuance(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    capability = fixture.catalog().verify_manifest(lecture_id=_LECTURE_ID)
    reordered = fixture.manifest.model_copy(
        update={"artifacts": tuple(reversed(fixture.manifest.artifacts))}
    )
    fixture.write_manifest(fixture.sign_manifest(reordered))

    with pytest.raises(
        SourceContentResolutionError,
        match="changed after capability issuance",
    ):
        capability.require_current()


def test_catalog_rejects_capability_replay_for_another_lecture(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)

    with pytest.raises(SourceContentResolutionError, match="different lecture"):
        fixture.catalog().verify_manifest(lecture_id="lecture-002")


def test_non_source_authority_cannot_issue_manifest_capability(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)

    with pytest.raises(TypeError, match="SourceManifestHmacAuthority"):
        HmacSourceManifestCatalog(
            artifact_reader=RootedSourceArtifactReader(
                RootedArtifactStore(fixture.root)
            ),
            expected_manifest_id=_MANIFEST_ID,
            manifest_relative_path=_MANIFEST_RELATIVE_PATH,
            authority=HmacQualityTrust(_TRUST_KEY),  # type: ignore[arg-type]
            limits=fixture.limits,
        )

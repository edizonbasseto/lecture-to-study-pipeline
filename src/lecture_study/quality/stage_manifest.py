"""Immutable lineage contracts for safe pipeline resume decisions."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from lecture_study.quality.evidence_auditor import (
    ContentQualityError,
    QualityFinding,
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class StageName(StrEnum):
    """Closed registry of pipeline stages that may own a manifest."""

    AUTHENTICATION = "authentication"
    DISCOVERY = "discovery"
    ACQUISITION = "acquisition"
    EXTRACTION = "extraction"
    TRANSCRIPTION = "transcription"
    EVIDENCE = "evidence"
    SEMANTIC_LEDGER = "semantic-ledger"
    DRAFTING = "drafting"
    CRITIC = "critic"
    ASSEMBLY = "assembly"
    METADATA = "metadata"
    QUALITY_CERTIFICATION = "quality-certification"
    PUBLISHING = "publishing"
    FLASHCARDS = "flashcards"
    MOCK_EXAM = "mock-exam"
    PODCAST = "podcast"
    TTS = "tts"


_LLM_STAGE_NAMES = frozenset(
    {
        StageName.SEMANTIC_LEDGER,
        StageName.DRAFTING,
        StageName.CRITIC,
        StageName.MOCK_EXAM,
        StageName.PODCAST,
    }
)


@dataclass(frozen=True, slots=True, order=True)
class ArtifactFingerprint:
    """Stable identity and content digest for one pipeline artifact."""

    artifact_id: str
    sha256: str

    def __post_init__(self) -> None:
        if not self.artifact_id.strip():
            raise ValueError("artifact_id must not be blank")
        if _SHA256_PATTERN.fullmatch(self.sha256) is None:
            raise ValueError("sha256 must contain 64 lowercase hexadecimal characters")

    @classmethod
    def from_file(cls, artifact_id: str, path: Path) -> ArtifactFingerprint:
        digest = hashlib.sha256()
        with path.open("rb") as artifact_file:
            for chunk in iter(lambda: artifact_file.read(1024 * 1024), b""):
                digest.update(chunk)
        return cls(artifact_id=artifact_id, sha256=digest.hexdigest())


@dataclass(frozen=True, slots=True)
class StageManifest:
    """Authoritative inputs, outputs, and execution identity for one stage."""

    lecture_id: str
    stage_name: StageName
    implementation_version: str
    code_revision: str
    engine_version: str
    parent_manifest_sha256: str
    configuration_sha256: str
    input_artifacts: tuple[ArtifactFingerprint, ...]
    output_artifacts: tuple[ArtifactFingerprint, ...]
    model_sha256: str | None = None
    prompt_sha256: str | None = None
    schema_sha256: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "lecture_id",
            "implementation_version",
            "code_revision",
            "engine_version",
        ):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} must not be blank")
        if not isinstance(self.stage_name, StageName):
            raise ValueError("stage_name must be a registered StageName")

        self._validate_digest("configuration_sha256", self.configuration_sha256)
        self._validate_digest("parent_manifest_sha256", self.parent_manifest_sha256)
        self._validate_optional_digest("model_sha256", self.model_sha256)
        self._validate_optional_digest("prompt_sha256", self.prompt_sha256)
        self._validate_optional_digest("schema_sha256", self.schema_sha256)
        if self.stage_name in _LLM_STAGE_NAMES:
            for field_name in ("model_sha256", "prompt_sha256", "schema_sha256"):
                if getattr(self, field_name) is None:
                    raise ValueError(
                        f"{field_name} is required for LLM-backed stage "
                        f"{self.stage_name}"
                    )
        self._validate_unique_artifact_ids("input_artifacts", self.input_artifacts)
        self._validate_unique_artifact_ids("output_artifacts", self.output_artifacts)
        if not self.input_artifacts:
            raise ValueError("input_artifacts must not be empty")
        if not self.output_artifacts:
            raise ValueError("output_artifacts must not be empty")

    @staticmethod
    def _validate_digest(field_name: str, value: str) -> None:
        if _SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError(
                f"{field_name} must contain 64 lowercase hexadecimal characters"
            )

    @classmethod
    def _validate_optional_digest(cls, field_name: str, value: str | None) -> None:
        if value is not None:
            cls._validate_digest(field_name, value)

    @staticmethod
    def _validate_unique_artifact_ids(
        field_name: str,
        artifacts: tuple[ArtifactFingerprint, ...],
    ) -> None:
        artifact_ids = [artifact.artifact_id for artifact in artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError(f"{field_name} must not contain duplicate artifact IDs")


@dataclass(frozen=True, slots=True)
class ManifestQualityReport:
    """Result of validating current artifacts against an immutable manifest."""

    findings: tuple[QualityFinding, ...]

    @property
    def passed(self) -> bool:
        return not self.findings

    def require_passed(self) -> None:
        if self.passed:
            return
        codes = ", ".join(finding.code for finding in self.findings)
        raise ContentQualityError(f"Stage manifest validation failed: {codes}")


class ManifestQualityAuditor:
    """Rejects stale, missing, unexpected, or modified stage artifacts."""

    def audit(
        self,
        *,
        manifest: StageManifest,
        observed_input_artifacts: Sequence[ArtifactFingerprint],
        observed_output_artifacts: Sequence[ArtifactFingerprint],
        lecture_id: str,
        stage_name: StageName,
        implementation_version: str,
        code_revision: str,
        engine_version: str,
        parent_manifest_sha256: str,
        configuration_sha256: str,
        model_sha256: str | None = None,
        prompt_sha256: str | None = None,
        schema_sha256: str | None = None,
    ) -> ManifestQualityReport:
        findings: list[QualityFinding] = []
        self._compare_artifacts(
            artifact_role="input",
            expected=manifest.input_artifacts,
            observed=observed_input_artifacts,
            findings=findings,
        )
        self._compare_artifacts(
            artifact_role="output",
            expected=manifest.output_artifacts,
            observed=observed_output_artifacts,
            findings=findings,
        )
        self._compare_execution_identity(
            manifest=manifest,
            lecture_id=lecture_id,
            stage_name=stage_name,
            implementation_version=implementation_version,
            code_revision=code_revision,
            engine_version=engine_version,
            parent_manifest_sha256=parent_manifest_sha256,
            configuration_sha256=configuration_sha256,
            model_sha256=model_sha256,
            prompt_sha256=prompt_sha256,
            schema_sha256=schema_sha256,
            findings=findings,
        )
        return ManifestQualityReport(findings=tuple(findings))

    @staticmethod
    def _compare_artifacts(
        *,
        artifact_role: str,
        expected: Sequence[ArtifactFingerprint],
        observed: Sequence[ArtifactFingerprint],
        findings: list[QualityFinding],
    ) -> None:
        expected_counts = Counter(expected)
        observed_counts = Counter(observed)
        missing_count = sum((expected_counts - observed_counts).values())
        unexpected_count = sum((observed_counts - expected_counts).values())

        if missing_count:
            findings.append(
                QualityFinding(
                    code=f"missing_manifest_{artifact_role}_artifacts",
                    message=(
                        f"{missing_count} expected {artifact_role} artifacts are absent "
                        "or have a different digest."
                    ),
                )
            )
        if unexpected_count:
            findings.append(
                QualityFinding(
                    code=f"unexpected_manifest_{artifact_role}_artifacts",
                    message=(
                        f"{unexpected_count} observed {artifact_role} artifacts are "
                        "untracked or stale."
                    ),
                )
            )

    @staticmethod
    def _compare_execution_identity(
        *,
        manifest: StageManifest,
        lecture_id: str,
        stage_name: StageName,
        implementation_version: str,
        code_revision: str,
        engine_version: str,
        parent_manifest_sha256: str,
        configuration_sha256: str,
        model_sha256: str | None,
        prompt_sha256: str | None,
        schema_sha256: str | None,
        findings: list[QualityFinding],
    ) -> None:
        expected_identity = (
            manifest.lecture_id,
            manifest.stage_name,
            manifest.implementation_version,
            manifest.code_revision,
            manifest.engine_version,
            manifest.parent_manifest_sha256,
            manifest.configuration_sha256,
            manifest.model_sha256,
            manifest.prompt_sha256,
            manifest.schema_sha256,
        )
        observed_identity = (
            lecture_id,
            stage_name,
            implementation_version,
            code_revision,
            engine_version,
            parent_manifest_sha256,
            configuration_sha256,
            model_sha256,
            prompt_sha256,
            schema_sha256,
        )
        if expected_identity != observed_identity:
            findings.append(
                QualityFinding(
                    code="execution_identity_mismatch",
                    message=(
                        "Implementation, configuration, model, prompt, or schema differs "
                        "from the manifest."
                    ),
                )
            )

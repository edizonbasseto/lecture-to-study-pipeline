"""Fail-closed production entry point for publication certification."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    model_validator,
)

from lecture_study.domain.models import SafeIdentifier
from lecture_study.domain.versioning import SchemaVersionOne
from lecture_study.quality.certification import (
    REQUIRED_QUALITY_GATES,
    QualityCertificationError,
    QualityGateName,
)

CERTIFICATION_ATTEMPTS_ROOT = Path("workspace/data/quality_certificates")
_LECTURE_ID_ADAPTER = TypeAdapter(SafeIdentifier)


def _require_non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


_NonBlankText = Annotated[str, AfterValidator(_require_non_blank)]


class GateExecutionBlocker(BaseModel):
    """Diagnostic evidence that a gate could not execute authoritatively."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    gate_name: QualityGateName
    reason_code: SafeIdentifier
    detail: _NonBlankText
    expected_evidence_relative_path: _NonBlankText


class CertificationAttempt(BaseModel):
    """Complete gate-execution diagnosis; never an authorization artifact."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    lecture_id: SafeIdentifier
    blockers: tuple[GateExecutionBlocker, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _require_exact_gate_set(self) -> CertificationAttempt:
        observed = tuple(blocker.gate_name for blocker in self.blockers)
        if len(observed) != len(set(observed)):
            raise ValueError("certification attempt contains duplicate gates")
        if frozenset(observed) != REQUIRED_QUALITY_GATES:
            raise ValueError("certification attempt must cover the exact gate set")
        return self

    @property
    def ready_for_publication(self) -> bool:
        return False


class QualityCertificateIssuer:
    """Executes the closed production gate registry and fails on missing authority."""

    def __init__(
        self,
        *,
        attempts_root: Path = CERTIFICATION_ATTEMPTS_ROOT,
    ) -> None:
        self._attempts_root = attempts_root

    def issue(self, lecture_id: str) -> None:
        lecture_id = str(_LECTURE_ID_ADAPTER.validate_python(lecture_id))
        attempt = self._execute_gate_registry(lecture_id)
        self._write_attempt_atomically(attempt)
        blocked_names = ", ".join(
            blocker.gate_name.value for blocker in attempt.blockers
        )
        raise QualityCertificationError(
            "publication certification remains fail-closed; authoritative evidence "
            f"is unavailable for: {blocked_names}"
        )

    @staticmethod
    def _execute_gate_registry(lecture_id: str) -> CertificationAttempt:
        concrete_blockers = {
            QualityGateName.SOURCE_MEDIA_INTEGRITY: (
                "source-baseline-not-integrated",
                "Acquisition-time media baseline and decode probe are not integrated.",
            ),
            QualityGateName.PROMPT_BUDGET: (
                "prompt-invocation-manifest-not-integrated",
                "Tokenizer-derived prompt invocation manifests are not integrated.",
            ),
            QualityGateName.ARTIFACT_LINEAGE: (
                "stage-lineage-not-integrated",
                "A complete chain of stage manifests is not integrated.",
            ),
            QualityGateName.TIMELINE_CLASSIFICATION: (
                "source-universe-evidence-not-integrated",
                "The authoritative source-universe timeline is not integrated.",
            ),
            QualityGateName.LEARNING_UNIT_COMPLETENESS: (
                "source-universe-evidence-not-integrated",
                "Conceptual-unit ownership evidence is not integrated.",
            ),
        }
        blockers: list[GateExecutionBlocker] = []
        for gate_name in QualityGateName:
            reason_code, detail = concrete_blockers.get(
                gate_name,
                (
                    "authoritative-evaluator-not-implemented",
                    "The gate requires evidence and a calibrated evaluator from a "
                    "later content-quality phase.",
                ),
            )
            blockers.append(
                GateExecutionBlocker(
                    schema_version=1,
                    gate_name=gate_name,
                    reason_code=reason_code,
                    detail=detail,
                    expected_evidence_relative_path=(
                        f"quality_evidence/{lecture_id}/{gate_name.value}.json"
                    ),
                )
            )
        return CertificationAttempt(
            schema_version=1,
            lecture_id=lecture_id,
            blockers=tuple(blockers),
        )

    def _write_attempt_atomically(self, attempt: CertificationAttempt) -> None:
        lecture_dir = self._attempts_root / str(attempt.lecture_id)
        lecture_dir.mkdir(parents=True, exist_ok=True)
        target = lecture_dir / "latest_attempt.json"
        temporary = lecture_dir / ".latest_attempt.json.tmp"
        payload = json.dumps(
            attempt.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        try:
            with temporary.open("x", encoding="utf-8") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def issue_publication_certificate(lecture_id: str) -> None:
    """Run the production issuer without accepting caller-provided gate results."""

    QualityCertificateIssuer().issue(lecture_id)

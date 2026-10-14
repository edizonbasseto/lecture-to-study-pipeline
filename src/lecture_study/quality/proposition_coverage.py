"""Bidirectional coverage and depth gates for generated learning material."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from lecture_study.domain.content_ledger import ContentDimension
from lecture_study.quality.evidence_auditor import (
    ContentQualityError,
    QualityFinding,
)

_SHA256_LENGTH = 64


@dataclass(frozen=True, slots=True)
class PropositionRequirement:
    """Source proposition and the depth dimensions its material must preserve."""

    proposition_id: str
    required_dimensions: frozenset[ContentDimension]
    source_evidence_references: tuple[str, ...]
    safety_critical: bool = False

    def __post_init__(self) -> None:
        if not self.proposition_id.strip():
            raise ValueError("proposition_id must not be blank")
        if ContentDimension.CORE_STATEMENT not in self.required_dimensions:
            raise ValueError("required_dimensions must include core_statement")
        if not self.source_evidence_references:
            raise ValueError("source_evidence_references must not be empty")
        if any(not reference.strip() for reference in self.source_evidence_references):
            raise ValueError("source_evidence_references must not contain blank values")
        if (
            self.safety_critical
            and ContentDimension.SAFETY not in self.required_dimensions
        ):
            raise ValueError(
                "safety-critical propositions must require the safety dimension"
            )


@dataclass(frozen=True, slots=True)
class PropositionCoverage:
    """Independent critic evidence that a draft covers one proposition."""

    proposition_id: str
    covered_dimensions: frozenset[ContentDimension]
    material_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.proposition_id.strip():
            raise ValueError("proposition_id must not be blank")
        if not self.material_references:
            raise ValueError("material_references must not be empty")
        if any(not reference.strip() for reference in self.material_references):
            raise ValueError("material_references must not contain blank values")


@dataclass(frozen=True, slots=True)
class EvaluatorIdentity:
    """Content-addressed identity for one generator or evaluator."""

    model_sha256: str
    prompt_sha256: str
    engine_version: str

    def __post_init__(self) -> None:
        for field_name in ("model_sha256", "prompt_sha256"):
            digest = str(getattr(self, field_name))
            if len(digest) != _SHA256_LENGTH or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError(
                    f"{field_name} must contain 64 lowercase hexadecimal characters"
                )
        if not self.engine_version.strip():
            raise ValueError("engine_version must not be blank")


@dataclass(frozen=True, slots=True)
class PropositionCoverageReport:
    """Fail-closed coverage result used by repair and publication gates."""

    expected_proposition_count: int
    covered_proposition_count: int
    missing_proposition_ids: tuple[str, ...]
    missing_dimensions_by_proposition: tuple[
        tuple[str, tuple[ContentDimension, ...]], ...
    ]
    unexpected_proposition_ids: tuple[str, ...]
    findings: tuple[QualityFinding, ...]

    @property
    def proposition_recall(self) -> float:
        if self.expected_proposition_count == 0:
            return 0.0
        return self.covered_proposition_count / self.expected_proposition_count

    @property
    def passed(self) -> bool:
        return not self.findings

    def require_passed(self) -> None:
        if self.passed:
            return
        codes = ", ".join(finding.code for finding in self.findings)
        raise ContentQualityError(f"Generated content failed coverage gates: {codes}")


class PropositionCoverageAuditor:
    """Requires complete proposition recall and complete analytical depth."""

    def audit(
        self,
        *,
        requirements: Sequence[PropositionRequirement],
        coverage: Sequence[PropositionCoverage],
        known_source_evidence_references: Sequence[str],
        known_material_references: Sequence[str],
        generator_identity: EvaluatorIdentity,
        evaluator_identity: EvaluatorIdentity,
    ) -> PropositionCoverageReport:
        findings: list[QualityFinding] = []
        requirements_by_id = self._index_requirements(requirements, findings)
        coverage_by_id = self._index_coverage(coverage, findings)

        expected_ids = set(requirements_by_id)
        covered_ids = set(coverage_by_id)
        missing_ids = tuple(sorted(expected_ids - covered_ids))
        unexpected_ids = tuple(sorted(covered_ids - expected_ids))
        missing_dimensions = self._find_missing_dimensions(
            requirements_by_id,
            coverage_by_id,
        )
        self._audit_reference_resolution(
            requirements=requirements,
            coverage=coverage,
            known_source_evidence_references=known_source_evidence_references,
            known_material_references=known_material_references,
            findings=findings,
        )
        if generator_identity.model_sha256 == evaluator_identity.model_sha256:
            findings.append(
                QualityFinding(
                    code="non_independent_evaluator",
                    message=(
                        "The proposition coverage evaluator must use independently "
                        "identified model weights."
                    ),
                )
            )

        if not requirements:
            findings.append(
                QualityFinding(
                    code="empty_proposition_inventory",
                    message="A non-empty source requires an atomic proposition inventory.",
                )
            )
        if missing_ids:
            findings.append(
                QualityFinding(
                    code="missing_source_propositions",
                    message=(
                        f"{len(missing_ids)} source propositions are absent from the "
                        "generated material."
                    ),
                )
            )
        if unexpected_ids:
            findings.append(
                QualityFinding(
                    code="unsupported_generated_propositions",
                    message=(
                        f"{len(unexpected_ids)} covered propositions are absent from the "
                        "source inventory."
                    ),
                )
            )
        if missing_dimensions:
            findings.append(
                QualityFinding(
                    code="incomplete_analytical_depth",
                    message=(
                        f"{len(missing_dimensions)} propositions omit one or more required "
                        "analytical dimensions."
                    ),
                )
            )

        safety_missing = tuple(
            proposition_id
            for proposition_id, requirement in requirements_by_id.items()
            if requirement.safety_critical
            and (
                proposition_id not in coverage_by_id
                or ContentDimension.SAFETY
                not in coverage_by_id[proposition_id].covered_dimensions
            )
        )
        if safety_missing:
            findings.append(
                QualityFinding(
                    code="missing_safety_critical_content",
                    message=(
                        f"{len(safety_missing)} safety-critical propositions lack complete "
                        "coverage."
                    ),
                )
            )

        fully_covered_ids = expected_ids & covered_ids - {
            proposition_id for proposition_id, _missing in missing_dimensions
        }
        return PropositionCoverageReport(
            expected_proposition_count=len(expected_ids),
            covered_proposition_count=len(fully_covered_ids),
            missing_proposition_ids=missing_ids,
            missing_dimensions_by_proposition=missing_dimensions,
            unexpected_proposition_ids=unexpected_ids,
            findings=tuple(findings),
        )

    @staticmethod
    def _audit_reference_resolution(
        *,
        requirements: Sequence[PropositionRequirement],
        coverage: Sequence[PropositionCoverage],
        known_source_evidence_references: Sequence[str],
        known_material_references: Sequence[str],
        findings: list[QualityFinding],
    ) -> None:
        known_source_references = set(known_source_evidence_references)
        known_material_reference_set = set(known_material_references)
        referenced_source_evidence = {
            reference
            for requirement in requirements
            for reference in requirement.source_evidence_references
        }
        referenced_material = {
            reference for item in coverage for reference in item.material_references
        }

        unresolved_source_count = len(
            referenced_source_evidence - known_source_references
        )
        unresolved_material_count = len(
            referenced_material - known_material_reference_set
        )
        unrepresented_source_count = len(
            known_source_references - referenced_source_evidence
        )
        unaudited_material_count = len(
            known_material_reference_set - referenced_material
        )
        if unresolved_source_count:
            findings.append(
                QualityFinding(
                    code="unresolved_source_evidence_references",
                    message=(
                        f"{unresolved_source_count} proposition source references cannot "
                        "be resolved."
                    ),
                )
            )
        if unresolved_material_count:
            findings.append(
                QualityFinding(
                    code="unresolved_material_references",
                    message=(
                        f"{unresolved_material_count} generated-material references cannot "
                        "be resolved."
                    ),
                )
            )
        if unrepresented_source_count:
            findings.append(
                QualityFinding(
                    code="uninventoried_source_evidence",
                    message=(
                        f"{unrepresented_source_count} known instructional source spans "
                        "are absent from the proposition inventory."
                    ),
                )
            )
        if unaudited_material_count:
            findings.append(
                QualityFinding(
                    code="unaudited_material_references",
                    message=(
                        f"{unaudited_material_count} known generated-material spans are "
                        "absent from proposition coverage."
                    ),
                )
            )

    @staticmethod
    def _index_requirements(
        requirements: Sequence[PropositionRequirement],
        findings: list[QualityFinding],
    ) -> dict[str, PropositionRequirement]:
        proposition_ids = [requirement.proposition_id for requirement in requirements]
        duplicates = PropositionCoverageAuditor._duplicates(proposition_ids)
        if duplicates:
            findings.append(
                QualityFinding(
                    code="duplicate_proposition_requirements",
                    message=f"Duplicate proposition requirements: {duplicates}.",
                )
            )
        return {requirement.proposition_id: requirement for requirement in requirements}

    @staticmethod
    def _index_coverage(
        coverage: Sequence[PropositionCoverage],
        findings: list[QualityFinding],
    ) -> dict[str, PropositionCoverage]:
        proposition_ids = [item.proposition_id for item in coverage]
        duplicates = PropositionCoverageAuditor._duplicates(proposition_ids)
        if duplicates:
            findings.append(
                QualityFinding(
                    code="duplicate_proposition_coverage",
                    message=f"Duplicate proposition coverage entries: {duplicates}.",
                )
            )
        return {item.proposition_id: item for item in coverage}

    @staticmethod
    def _find_missing_dimensions(
        requirements_by_id: dict[str, PropositionRequirement],
        coverage_by_id: dict[str, PropositionCoverage],
    ) -> tuple[tuple[str, tuple[ContentDimension, ...]], ...]:
        missing_dimensions: list[tuple[str, tuple[ContentDimension, ...]]] = []
        for proposition_id, requirement in requirements_by_id.items():
            coverage = coverage_by_id.get(proposition_id)
            if coverage is None:
                continue
            missing = requirement.required_dimensions - coverage.covered_dimensions
            if missing:
                missing_dimensions.append(
                    (
                        proposition_id,
                        tuple(sorted(missing, key=lambda dimension: dimension.value)),
                    )
                )
        return tuple(missing_dimensions)

    @staticmethod
    def _duplicates(values: Sequence[str]) -> tuple[str, ...]:
        counts = Counter(values)
        return tuple(sorted(value for value, count in counts.items() if count > 1))

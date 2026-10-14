"""Fail-closed quality gates for canonical lecture content."""

from lecture_study.domain.content_ledger import ContentDimension
from lecture_study.quality.certification import (
    PublicationQualityCertificate,
    PublicationRuntimeIdentity,
    PublicationRuntimeIdentityResolver,
    QualityCertificateVerifier,
    QualityCertificationError,
    QualityGateName,
    QualityGateReport,
    VerifiedPublicationInputs,
    is_publication_ready,
    require_publication_ready,
)
from lecture_study.quality.content_ledger import (
    AtomicContentLedgerAuditReport,
    AtomicContentLedgerAuditor,
    canonical_source_universe_sha256,
)
from lecture_study.quality.issuer import issue_publication_certificate
from lecture_study.quality.content_density import (
    ContentDensityAuditor,
    ContentDensityPolicy,
    ContentDensityReport,
)
from lecture_study.quality.derivative import (
    DerivativeArtifactKind,
    DerivativeExecutionIdentity,
    DerivativeExecutionIdentityResolver,
    DerivativeQualityError,
    GenerationReceipt,
    UncertifiedMockExamCandidate,
    UncertifiedPodcastScriptCandidate,
    require_mock_exam_ready,
    require_podcast_script_ready,
)
from lecture_study.quality.delivery import (
    AudioRuntimeIdentityResolver,
    DeliveryArtifactKind,
    is_deterministic_delivery_current,
    is_mock_exam_delivery_current,
    is_podcast_audio_delivery_current,
    is_podcast_script_delivery_current,
    issue_deterministic_delivery,
    issue_mock_exam_delivery,
    issue_podcast_audio_delivery,
    issue_podcast_script_delivery,
    write_delivery_outputs,
)
from lecture_study.quality.evidence_auditor import (
    ContentQualityError,
    EvidenceQualityAuditor,
    EvidenceQualityReport,
    QualityFinding,
)
from lecture_study.quality.proposition_coverage import (
    EvaluatorIdentity,
    PropositionCoverage,
    PropositionCoverageAuditor,
    PropositionCoverageReport,
    PropositionRequirement,
)
from lecture_study.quality.prompt_budget import (
    PromptBudget,
    PromptBudgetExceededError,
)
from lecture_study.quality.stage_manifest import (
    ArtifactFingerprint,
    ManifestQualityAuditor,
    ManifestQualityReport,
    StageManifest,
    StageName,
)
from lecture_study.quality.source_universe import (
    SourceUniverseAuditReport,
    SourceUniverseAuditor,
)

__all__ = [
    "ArtifactFingerprint",
    "AtomicContentLedgerAuditReport",
    "AtomicContentLedgerAuditor",
    "AudioRuntimeIdentityResolver",
    "ContentDensityAuditor",
    "ContentDensityPolicy",
    "ContentDensityReport",
    "ContentQualityError",
    "ContentDimension",
    "DeliveryArtifactKind",
    "DerivativeArtifactKind",
    "DerivativeExecutionIdentity",
    "DerivativeExecutionIdentityResolver",
    "DerivativeQualityError",
    "EvidenceQualityAuditor",
    "EvidenceQualityReport",
    "EvaluatorIdentity",
    "GenerationReceipt",
    "ManifestQualityAuditor",
    "ManifestQualityReport",
    "PublicationQualityCertificate",
    "PublicationRuntimeIdentity",
    "PublicationRuntimeIdentityResolver",
    "PromptBudget",
    "PromptBudgetExceededError",
    "PropositionCoverage",
    "PropositionCoverageAuditor",
    "PropositionCoverageReport",
    "PropositionRequirement",
    "QualityFinding",
    "QualityCertificateVerifier",
    "QualityCertificationError",
    "QualityGateName",
    "QualityGateReport",
    "StageManifest",
    "StageName",
    "SourceUniverseAuditReport",
    "SourceUniverseAuditor",
    "UncertifiedMockExamCandidate",
    "UncertifiedPodcastScriptCandidate",
    "VerifiedPublicationInputs",
    "is_deterministic_delivery_current",
    "is_mock_exam_delivery_current",
    "is_podcast_audio_delivery_current",
    "is_podcast_script_delivery_current",
    "is_publication_ready",
    "issue_deterministic_delivery",
    "issue_mock_exam_delivery",
    "issue_podcast_audio_delivery",
    "issue_podcast_script_delivery",
    "write_delivery_outputs",
    "canonical_source_universe_sha256",
    "issue_publication_certificate",
    "require_mock_exam_ready",
    "require_podcast_script_ready",
    "require_publication_ready",
]

"""Immutable source-clause and independent semantic-candidate contracts."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from lecture_study.domain.content_ledger import (
    AssertionPolarity,
    AtomicContentType,
    CertaintyLevel,
    ClinicalFieldType,
    ContentDimension,
    ContentImportance,
    NumericQuantity,
    PropositionRelationType,
    QuantityRole,
    RecommendationStrength,
)
from lecture_study.domain.models import SafeIdentifier
from lecture_study.domain.source_universe import EvidenceModality
from lecture_study.domain.versioning import (
    PersistedSequenceNumber,
    SchemaVersionOne,
)

_Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_NonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
_EvidenceText = Annotated[str, StringConstraints(min_length=1)]
_Confidence = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
_PersistedOffset = Annotated[int, Field(ge=0, le=(1 << 63) - 1)]
_NormalizedCoordinate = Annotated[
    float,
    Field(ge=0.0, le=1.0, allow_inf_nan=False),
]

VISUAL_OBSERVATION_CRYPTOGRAPHIC_DOMAIN = "lecture-study.visual-observation.v1"
VISUAL_OBSERVATION_SIGNATURE_PURPOSE = "source-span-visual-observation"


class SourcePartitionKind(StrEnum):
    """Closed selector kinds for exact source-clause provenance."""

    TEXT_RANGE = "text-range"
    VISUAL_FULL_FRAME = "visual-full-frame"
    VISUAL_REGION = "visual-region"


class ClauseDisposition(StrEnum):
    """An independent producer's semantic disposition for one source clause."""

    PROPOSITIONAL = "propositional"
    NON_PROPOSITIONAL = "non-propositional"
    DUPLICATE = "duplicate"
    AMBIGUOUS = "ambiguous"
    INAUDIBLE = "inaudible"


class CaptureState(StrEnum):
    """Explicit state for a clinical or quantitative observation slot."""

    CAPTURED = "captured"
    NOT_STATED = "not-stated"
    AMBIGUOUS = "ambiguous"
    INAUDIBLE = "inaudible"
    CONFLICTING = "conflicting"
    NOT_APPLICABLE = "not-applicable"


class ComparisonDirection(StrEnum):
    """Direction of the left comparison operand relative to the right."""

    LOWER = "lower"
    EQUAL = "equal"
    HIGHER = "higher"


class TextPartitionSelector(BaseModel):
    """Exact UTF-8 byte range inside a resolved textual source record."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    kind: Literal[SourcePartitionKind.TEXT_RANGE]
    start_byte: _PersistedOffset
    end_byte: PersistedSequenceNumber
    selected_sha256: _Digest

    @model_validator(mode="after")
    def _require_positive_range(self) -> TextPartitionSelector:
        if self.end_byte <= self.start_byte:
            raise ValueError("text partition end_byte must be greater than start_byte")
        return self


class GeneratedObservationIdentity(BaseModel):
    """Content-addressed implementation and configuration identity."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    implementation_sha256: _Digest
    configuration_sha256: _Digest


def canonical_visual_region_binding_sha256(
    *,
    original_frame_sha256: str,
    coordinates: tuple[float, float, float, float],
    region_content_sha256: str,
    hashing_identity: GeneratedObservationIdentity,
) -> str:
    """Bind a region hash to its frame, coordinates, and extraction identity."""

    payload = json.dumps(
        {
            "configuration_sha256": hashing_identity.configuration_sha256,
            "coordinates": coordinates,
            "implementation_sha256": hashing_identity.implementation_sha256,
            "original_frame_sha256": original_frame_sha256,
            "region_content_sha256": region_content_sha256,
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(b"lecture-study:visual-region:v1\0" + payload).hexdigest()


class VisualFullFrameSelector(BaseModel):
    """Exact selector for the original encoded full frame."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    kind: Literal[SourcePartitionKind.VISUAL_FULL_FRAME]
    original_frame_sha256: _Digest


class VisualPartitionSelector(BaseModel):
    """Exact region bound to its original frame and hashing implementation."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    kind: Literal[SourcePartitionKind.VISUAL_REGION]
    original_frame_sha256: _Digest
    left: _NormalizedCoordinate
    top: _NormalizedCoordinate
    right: _NormalizedCoordinate
    bottom: _NormalizedCoordinate
    selected_sha256: _Digest
    hashing_identity: GeneratedObservationIdentity
    region_binding_sha256: _Digest

    @model_validator(mode="after")
    def _validate_region_binding(self) -> VisualPartitionSelector:
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("visual partition must have positive width and height")
        expected_binding = canonical_visual_region_binding_sha256(
            original_frame_sha256=self.original_frame_sha256,
            coordinates=(self.left, self.top, self.right, self.bottom),
            region_content_sha256=self.selected_sha256,
            hashing_identity=self.hashing_identity,
        )
        if self.region_binding_sha256 != expected_binding:
            raise ValueError(
                "visual region binding must match frame, coordinates, region hash, "
                "implementation, and configuration"
            )
        return self


SourcePartitionSelector = Annotated[
    TextPartitionSelector | VisualFullFrameSelector | VisualPartitionSelector,
    Field(discriminator="kind"),
]

VisualObservationSelector = Annotated[
    VisualFullFrameSelector | VisualPartitionSelector,
    Field(discriminator="kind"),
]


class AuthenticatedVisualObservationReceipt(BaseModel):
    """Signed identity of exactly one generated visual observation."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    receipt_id: SafeIdentifier
    invocation_id: SafeIdentifier
    lecture_id: SafeIdentifier
    learning_unit_id: SafeIdentifier
    source_span_id: SafeIdentifier
    original_frame_sha256: _Digest
    selector: VisualObservationSelector
    generated_observation_identity: GeneratedObservationIdentity
    observed_text_sha256: _Digest
    cryptographic_domain: Literal["lecture-study.visual-observation.v1"]
    signature_purpose: Literal["source-span-visual-observation"]
    trust_key_id: _Digest
    authority_id: SafeIdentifier
    detached_signature: _Digest

    @model_validator(mode="after")
    def _validate_frame_binding(self) -> AuthenticatedVisualObservationReceipt:
        if self.selector.original_frame_sha256 != self.original_frame_sha256:
            raise ValueError(
                "visual observation receipt selector must match its original frame"
            )
        return self

    def authentication_payload(self) -> bytes:
        """Return canonical receipt bytes excluding only its detached signature."""

        return json.dumps(
            self.model_dump(
                mode="json",
                exclude={"detached_signature"},
                warnings=False,
            ),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


class SourceClause(BaseModel):
    """One exact textual or generated visual source observation."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    clause_id: SafeIdentifier
    lecture_id: SafeIdentifier
    learning_unit_id: SafeIdentifier
    sequence: PersistedSequenceNumber
    source_span_id: SafeIdentifier
    source_content_sha256: _Digest
    modality: EvidenceModality
    selector: SourcePartitionSelector
    verbatim_text: _EvidenceText | None = None
    observed_text: _EvidenceText | None = None
    text_sha256: _Digest
    generated_observation_identity: GeneratedObservationIdentity | None = None
    alignment_group_id: SafeIdentifier | None = None

    @model_validator(mode="after")
    def _validate_clause(self) -> SourceClause:
        if self.modality is EvidenceModality.TRANSCRIPT:
            self._validate_transcript_verbatim()
        elif self.modality is EvidenceModality.OCR:
            self._validate_ocr_observation()
        else:
            self._validate_visual_observation()
        return self

    def _validate_transcript_verbatim(self) -> None:
        if self.verbatim_text is None or self.observed_text is not None:
            raise ValueError(
                "transcript clauses require verbatim_text and forbid observed_text"
            )
        if self.generated_observation_identity is not None:
            raise ValueError("transcript clauses cannot declare a generator identity")
        self._validate_text_selector(self.verbatim_text)

    def _validate_ocr_observation(self) -> None:
        if self.verbatim_text is not None:
            raise ValueError("OCR observations cannot declare verbatim_text")
        if self.observed_text is None:
            raise ValueError("OCR clauses require observed_text")
        if self.generated_observation_identity is None:
            raise ValueError("OCR clauses require a generated observation identity")
        self._validate_text_selector(self.observed_text)

    def _validate_visual_observation(self) -> None:
        if self.verbatim_text is not None:
            raise ValueError("visual observations cannot declare verbatim_text")
        if self.observed_text is None:
            raise ValueError("visual clauses require generated observed_text")
        if self.generated_observation_identity is None:
            raise ValueError("visual clauses require a generated observation identity")
        if not isinstance(
            self.selector,
            VisualFullFrameSelector | VisualPartitionSelector,
        ):
            raise ValueError("visual clauses require a visual selector")
        if self.selector.original_frame_sha256 != self.source_content_sha256:
            raise ValueError(
                "visual selector original frame hash must match source content hash"
            )
        self._validate_observed_text_hash(self.observed_text)

    def _validate_text_selector(self, text: str) -> None:
        if not isinstance(self.selector, TextPartitionSelector):
            raise ValueError("transcript and OCR clauses require a text selector")
        self._validate_observed_text_hash(text)
        selected_length = self.selector.end_byte - self.selector.start_byte
        if selected_length != len(text.encode("utf-8")):
            raise ValueError("text partition byte length must match clause text")
        if self.selector.selected_sha256 != self.text_sha256:
            raise ValueError("text partition hash must match clause text")

    def _validate_observed_text_hash(self, text: str) -> None:
        if not text.strip():
            raise ValueError("clause text must not be blank")
        observed_text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if observed_text_sha256 != self.text_sha256:
            raise ValueError("text_sha256 must match clause text")


class SourceTextExtent(BaseModel):
    """Resolved transcript or OCR byte extent with explicit modality."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    source_span_id: SafeIdentifier
    source_content_sha256: _Digest
    modality: Literal[
        EvidenceModality.TRANSCRIPT,
        EvidenceModality.OCR,
    ] = EvidenceModality.TRANSCRIPT
    canonical_text: str
    byte_length: PersistedSequenceNumber

    @model_validator(mode="after")
    def _validate_canonical_text(self) -> SourceTextExtent:
        canonical_bytes = self.canonical_text.encode("utf-8")
        if len(canonical_bytes) != self.byte_length:
            raise ValueError("source text extent byte length must match canonical text")
        if hashlib.sha256(canonical_bytes).hexdigest() != self.source_content_sha256:
            raise ValueError("source text extent hash must match canonical text")
        return self


class SourceVisualExtent(BaseModel):
    """Declared original encoded frame represented by generated observations."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    source_span_id: SafeIdentifier
    source_content_sha256: _Digest
    modality: Literal[EvidenceModality.VISUAL] = EvidenceModality.VISUAL


class SourceClauseInventory(BaseModel):
    """Complete text partition and visual observation inventory for one unit."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    lecture_id: SafeIdentifier
    learning_unit_id: SafeIdentifier
    source_universe_sha256: _Digest
    text_extents: tuple[SourceTextExtent, ...] = ()
    visual_extents: tuple[SourceVisualExtent, ...] = ()
    clauses: tuple[SourceClause, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_inventory(self) -> SourceClauseInventory:
        clause_ids = tuple(str(clause.clause_id) for clause in self.clauses)
        if len(clause_ids) != len(set(clause_ids)):
            raise ValueError("source clause IDs must be unique")
        observed_sequences = tuple(clause.sequence for clause in self.clauses)
        expected_sequences = tuple(range(1, len(self.clauses) + 1))
        if observed_sequences != expected_sequences:
            raise ValueError("source clause sequences must be contiguous and ordered")
        if any(
            clause.lecture_id != self.lecture_id
            or clause.learning_unit_id != self.learning_unit_id
            for clause in self.clauses
        ):
            raise ValueError("source clauses must match their inventory identity")
        self._validate_extent_identities()
        self._validate_text_partitions()
        self._validate_visual_observations()
        return self

    def _validate_extent_identities(self) -> None:
        text_span_ids = tuple(
            str(extent.source_span_id) for extent in self.text_extents
        )
        visual_span_ids = tuple(
            str(extent.source_span_id) for extent in self.visual_extents
        )
        all_span_ids = (*text_span_ids, *visual_span_ids)
        if len(all_span_ids) != len(set(all_span_ids)):
            raise ValueError("source extents must have unique span IDs")

    def _validate_text_partitions(self) -> None:
        extents_by_span_id = {
            str(extent.source_span_id): extent for extent in self.text_extents
        }
        clauses_by_span_id: dict[str, list[tuple[int, SourceClause]]] = {}
        for clause in self.clauses:
            if not isinstance(clause.selector, TextPartitionSelector):
                continue
            span_id = str(clause.source_span_id)
            extent = extents_by_span_id.get(span_id)
            if extent is None:
                raise ValueError("every text clause requires a source text extent")
            if extent.source_content_sha256 != clause.source_content_sha256:
                raise ValueError("source text extent hash must match its clauses")
            if extent.modality is not clause.modality:
                raise ValueError("source text extent modality must match its clauses")
            clauses_by_span_id.setdefault(span_id, []).append(
                (clause.selector.start_byte, clause)
            )
        if set(clauses_by_span_id) != set(extents_by_span_id):
            raise ValueError("every source text extent requires a clause partition")
        for span_id, extent in extents_by_span_id.items():
            cursor = 0
            canonical_bytes = extent.canonical_text.encode("utf-8")
            clauses = sorted(clauses_by_span_id[span_id], key=lambda item: item[0])
            for _, clause in clauses:
                selector = clause.selector
                if not isinstance(selector, TextPartitionSelector):
                    raise AssertionError("text partition requires a text selector")
                start_byte = selector.start_byte
                end_byte = selector.end_byte
                if start_byte != cursor:
                    raise ValueError(
                        "source clauses must form an exact non-overlapping partition"
                    )
                clause_text = clause.verbatim_text or clause.observed_text
                if clause_text is None:
                    raise AssertionError("text clause requires declared text")
                if canonical_bytes[start_byte:end_byte] != clause_text.encode("utf-8"):
                    raise ValueError(
                        "source clause text must match authenticated canonical bytes"
                    )
                cursor = end_byte
            if cursor != extent.byte_length:
                raise ValueError(
                    "source clauses must form an exact non-overlapping partition"
                )

    def _validate_visual_observations(self) -> None:
        extents_by_span_id = {
            str(extent.source_span_id): extent for extent in self.visual_extents
        }
        visual_clauses_by_span_id: dict[str, list[SourceClause]] = {}
        for clause in self.clauses:
            if clause.modality is not EvidenceModality.VISUAL:
                continue
            span_id = str(clause.source_span_id)
            extent = extents_by_span_id.get(span_id)
            if extent is None:
                raise ValueError("every visual observation requires a visual extent")
            if extent.source_content_sha256 != clause.source_content_sha256:
                raise ValueError("visual extent frame hash must match its observations")
            visual_clauses_by_span_id.setdefault(span_id, []).append(clause)
        if set(visual_clauses_by_span_id) != set(extents_by_span_id):
            raise ValueError("every visual extent requires a full-frame observation")
        for clauses in visual_clauses_by_span_id.values():
            full_frame_count = sum(
                isinstance(clause.selector, VisualFullFrameSelector)
                for clause in clauses
            )
            if full_frame_count < 1:
                raise ValueError(
                    "every visual extent requires at least one full-frame observation"
                )
            region_selectors = tuple(
                clause.selector
                for clause in clauses
                if isinstance(clause.selector, VisualPartitionSelector)
            )
            region_bindings = tuple(
                selector.region_binding_sha256 for selector in region_selectors
            )
            region_coordinates = tuple(
                (selector.left, selector.top, selector.right, selector.bottom)
                for selector in region_selectors
            )
            if len(region_bindings) != len(set(region_bindings)) or len(
                region_coordinates
            ) != len(set(region_coordinates)):
                raise ValueError("visual region selectors must be unique")


class CandidateProducerIdentity(BaseModel):
    """Content-addressed identity for one independent semantic producer."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    producer_id: SafeIdentifier
    manifest_sha256: _Digest
    model_sha256: _Digest
    prompt_sha256: _Digest
    schema_sha256: _Digest
    implementation_sha256: _Digest
    configuration_sha256: _Digest
    engine_version: _NonBlankText


class ClinicalValueObservation(BaseModel):
    """Observed clinical value with an explicit capture state."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    value_id: SafeIdentifier
    field_type: ClinicalFieldType
    state: CaptureState
    literal_value: _NonBlankText | None = None
    normalized_value: _NonBlankText | None = None
    source_clause_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_capture(self) -> ClinicalValueObservation:
        values = (self.literal_value, self.normalized_value)
        if self.state is CaptureState.CAPTURED and any(
            value is None for value in values
        ):
            raise ValueError(
                "captured clinical values require literal and normalized data"
            )
        if self.state is not CaptureState.CAPTURED and any(
            value is not None for value in values
        ):
            raise ValueError("only captured clinical values may declare data")
        if len(self.source_clause_ids) != len(set(self.source_clause_ids)):
            raise ValueError("clinical value source_clause_ids must be unique")
        return self


class QuantityObservation(BaseModel):
    """Observed numeric quantity with an explicit capture state."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    observation_id: SafeIdentifier
    state: CaptureState
    quantity: NumericQuantity | None = None
    source_clause_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_capture(self) -> QuantityObservation:
        if self.state is CaptureState.CAPTURED and self.quantity is None:
            raise ValueError("captured quantity observations require a quantity")
        if self.state is not CaptureState.CAPTURED and self.quantity is not None:
            raise ValueError(
                "only captured quantity observations may declare a quantity"
            )
        if len(self.source_clause_ids) != len(set(self.source_clause_ids)):
            raise ValueError("quantity source_clause_ids must be unique")
        return self


class CandidateDoseRegimen(BaseModel):
    """Indivisible medication, dose, route, schedule, and duration observation."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    regimen_id: SafeIdentifier
    medication: ClinicalValueObservation
    dose: QuantityObservation
    route: ClinicalValueObservation
    interval_or_frequency: QuantityObservation
    duration: QuantityObservation
    condition_candidate_ids: tuple[SafeIdentifier, ...] = ()

    @model_validator(mode="after")
    def _validate_regimen_roles(self) -> CandidateDoseRegimen:
        if self.medication.field_type is not ClinicalFieldType.DRUG:
            raise ValueError("dose regimen medication must use the drug field type")
        if self.route.field_type is not ClinicalFieldType.ROUTE:
            raise ValueError("dose regimen route must use the route field type")
        self._require_quantity_role(self.dose, {QuantityRole.DOSE}, "dose")
        self._require_quantity_role(
            self.interval_or_frequency,
            {QuantityRole.INTERVAL, QuantityRole.FREQUENCY},
            "interval_or_frequency",
        )
        self._require_quantity_role(
            self.duration,
            {QuantityRole.DURATION},
            "duration",
        )
        if len(self.condition_candidate_ids) != len(set(self.condition_candidate_ids)):
            raise ValueError("dose regimen condition candidate IDs must be unique")
        return self

    @staticmethod
    def _require_quantity_role(
        observation: QuantityObservation,
        allowed_roles: set[QuantityRole],
        slot_name: str,
    ) -> None:
        if observation.state is not CaptureState.CAPTURED:
            return
        if (
            observation.quantity is None
            or observation.quantity.role not in allowed_roles
        ):
            raise ValueError(f"dose regimen {slot_name} has an invalid quantity role")


class CandidateComparison(BaseModel):
    """Qualitative comparison with explicit metric and direction."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    comparison_id: SafeIdentifier
    left_value: _NonBlankText
    right_value: _NonBlankText
    metric: _NonBlankText
    direction: ComparisonDirection
    source_clause_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _require_unique_sources(self) -> CandidateComparison:
        if len(self.source_clause_ids) != len(set(self.source_clause_ids)):
            raise ValueError("comparison source_clause_ids must be unique")
        return self


class CandidatePredicate(BaseModel):
    """Exactly one predicate asserted by an ordinary atomic candidate."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    subject: _NonBlankText
    predicate: _NonBlankText
    object_value: _NonBlankText | None = None
    source_clause_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _require_unique_sources(self) -> CandidatePredicate:
        if len(self.source_clause_ids) != len(set(self.source_clause_ids)):
            raise ValueError("predicate source_clause_ids must be unique")
        return self


class CandidateAtomicObservation(BaseModel):
    """One producer's single-predicate atomic proposition candidate."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    candidate_id: SafeIdentifier
    sequence: PersistedSequenceNumber
    content_type: AtomicContentType
    importance: ContentImportance
    normalized_statement: _NonBlankText
    predicate: CandidatePredicate
    source_clause_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)
    clinical_values: tuple[ClinicalValueObservation, ...] = ()
    quantities: tuple[QuantityObservation, ...] = ()
    dose_regimen: CandidateDoseRegimen | None = None
    comparisons: tuple[CandidateComparison, ...] = ()
    required_dimensions: frozenset[ContentDimension] = Field(min_length=1)
    polarity: AssertionPolarity
    certainty: CertaintyLevel
    recommendation_strength: RecommendationStrength
    safety_critical: bool
    extraction_confidence: _Confidence

    @model_validator(mode="after")
    def _validate_candidate(self) -> CandidateAtomicObservation:
        source_ids = set(self.source_clause_ids)
        if len(source_ids) != len(self.source_clause_ids):
            raise ValueError("candidate source_clause_ids must be unique")
        referenced_source_ids = (
            *self.predicate.source_clause_ids,
            *(
                clause_id
                for value in self.clinical_values
                for clause_id in value.source_clause_ids
            ),
            *(
                clause_id
                for quantity in self.quantities
                for clause_id in quantity.source_clause_ids
            ),
            *(
                clause_id
                for comparison in self.comparisons
                for clause_id in comparison.source_clause_ids
            ),
        )
        if any(clause_id not in source_ids for clause_id in referenced_source_ids):
            raise ValueError(
                "candidate details must reference candidate source clauses"
            )
        self._require_unique_child_ids()
        if ContentDimension.CORE_STATEMENT not in self.required_dimensions:
            raise ValueError("required_dimensions must include core_statement")
        critical_types = {
            AtomicContentType.DOSE,
            AtomicContentType.CONTRAINDICATION,
            AtomicContentType.RED_FLAG,
        }
        if self.content_type in critical_types and not self.safety_critical:
            raise ValueError("dose, contraindication, and red-flag content is critical")
        if (
            self.safety_critical
            and ContentDimension.SAFETY not in self.required_dimensions
        ):
            raise ValueError("safety-critical candidates must require safety")
        if self.content_type is AtomicContentType.DOSE and self.dose_regimen is None:
            raise ValueError("dose content requires an indivisible dose regimen")
        if (
            self.content_type is AtomicContentType.COMPARISON
            and len(self.comparisons) != 1
        ):
            raise ValueError("comparison content requires exactly one comparison")
        if (
            self.content_type is AtomicContentType.UNCERTAINTY
            and self.certainty is CertaintyLevel.CERTAIN
        ):
            raise ValueError("uncertainty content cannot declare certain certainty")
        self._validate_dose_regimen_accounting()
        return self

    def _require_unique_child_ids(self) -> None:
        identifiers = (
            *(value.value_id for value in self.clinical_values),
            *(quantity.observation_id for quantity in self.quantities),
            *(comparison.comparison_id for comparison in self.comparisons),
        )
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("candidate child observation IDs must be unique")

    def _validate_dose_regimen_accounting(self) -> None:
        if self.dose_regimen is None:
            return
        clinical_values_by_id = {
            str(value.value_id): value for value in self.clinical_values
        }
        regimen_clinical_values = (
            self.dose_regimen.medication,
            self.dose_regimen.route,
        )
        if any(
            clinical_values_by_id.get(str(value.value_id)) != value
            for value in regimen_clinical_values
        ):
            raise ValueError(
                "dose regimen clinical values must be contained exactly in candidate "
                "clinical_values"
            )
        quantities_by_id = {
            str(quantity.observation_id): quantity for quantity in self.quantities
        }
        regimen_quantities = (
            self.dose_regimen.dose,
            self.dose_regimen.interval_or_frequency,
            self.dose_regimen.duration,
        )
        if any(
            quantities_by_id.get(str(quantity.observation_id)) != quantity
            for quantity in regimen_quantities
        ):
            raise ValueError(
                "dose regimen quantities must be contained exactly in candidate "
                "quantities"
            )


class CandidateRelationObservation(BaseModel):
    """One evidence-bearing relation between linked candidate propositions."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    relation_id: SafeIdentifier
    source_candidate_id: SafeIdentifier
    relation_type: PropositionRelationType
    target_candidate_id: SafeIdentifier
    source_clause_ids: tuple[SafeIdentifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_relation(self) -> CandidateRelationObservation:
        if self.source_candidate_id == self.target_candidate_id:
            raise ValueError("candidate relation cannot target itself")
        if len(self.source_clause_ids) != len(set(self.source_clause_ids)):
            raise ValueError("candidate relation source_clause_ids must be unique")
        return self


class CandidateClauseObservation(BaseModel):
    """One producer's complete semantic observation for one source clause."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    observation_id: SafeIdentifier
    sequence: PersistedSequenceNumber
    clause_id: SafeIdentifier
    producer_id: SafeIdentifier
    disposition: ClauseDisposition
    candidates: tuple[CandidateAtomicObservation, ...] = ()
    extraction_confidence: _Confidence

    @model_validator(mode="after")
    def _validate_observation(self) -> CandidateClauseObservation:
        is_propositional = self.disposition is ClauseDisposition.PROPOSITIONAL
        if is_propositional != bool(self.candidates):
            raise ValueError(
                "propositional disposition is required exactly when candidates exist"
            )
        expected_sequences = tuple(range(1, len(self.candidates) + 1))
        observed_sequences = tuple(candidate.sequence for candidate in self.candidates)
        if observed_sequences != expected_sequences:
            raise ValueError("candidate sequences must be contiguous and ordered")
        if any(
            self.clause_id not in candidate.source_clause_ids
            for candidate in self.candidates
        ):
            raise ValueError("every candidate must reference its observed clause")
        return self


class CandidateBatch(BaseModel):
    """Complete clause observations from one content-addressed producer."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: SchemaVersionOne
    batch_id: SafeIdentifier
    lecture_id: SafeIdentifier
    learning_unit_id: SafeIdentifier
    source_universe_sha256: _Digest
    clause_inventory_sha256: _Digest
    producer: CandidateProducerIdentity
    observations: tuple[CandidateClauseObservation, ...] = Field(min_length=1)
    relations: tuple[CandidateRelationObservation, ...] = ()

    @model_validator(mode="after")
    def _validate_batch(self) -> CandidateBatch:
        observation_ids = tuple(
            str(observation.observation_id) for observation in self.observations
        )
        clause_ids = tuple(
            str(observation.clause_id) for observation in self.observations
        )
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("candidate observation IDs must be unique")
        if len(clause_ids) != len(set(clause_ids)):
            raise ValueError("a candidate batch must observe every clause at most once")
        expected_sequences = tuple(range(1, len(self.observations) + 1))
        observed_sequences = tuple(
            observation.sequence for observation in self.observations
        )
        if observed_sequences != expected_sequences:
            raise ValueError("candidate observation sequences must be contiguous")
        if any(
            observation.producer_id != self.producer.producer_id
            for observation in self.observations
        ):
            raise ValueError("candidate observations must match the batch producer")
        candidate_ids = tuple(
            str(candidate.candidate_id)
            for observation in self.observations
            for candidate in observation.candidates
        )
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate IDs must be unique within a batch")
        relation_ids = tuple(str(relation.relation_id) for relation in self.relations)
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("candidate relation IDs must be unique")
        relation_edges = tuple(
            (
                str(relation.source_candidate_id),
                relation.relation_type.value,
                str(relation.target_candidate_id),
            )
            for relation in self.relations
        )
        if len(relation_edges) != len(set(relation_edges)):
            raise ValueError("candidate relation edges must be unique")
        known_candidate_ids = set(candidate_ids)
        known_clause_ids = set(clause_ids)
        if any(
            relation.source_candidate_id not in known_candidate_ids
            or relation.target_candidate_id not in known_candidate_ids
            for relation in self.relations
        ):
            raise ValueError("candidate relations must use known candidate endpoints")
        if any(
            clause_id not in known_clause_ids
            for relation in self.relations
            for clause_id in relation.source_clause_ids
        ):
            raise ValueError("candidate relations must use observed clauses")
        candidates_by_id = {
            str(candidate.candidate_id): candidate
            for observation in self.observations
            for candidate in observation.candidates
        }
        for relation in self.relations:
            endpoint_source_ids = {
                *candidates_by_id[str(relation.source_candidate_id)].source_clause_ids,
                *candidates_by_id[str(relation.target_candidate_id)].source_clause_ids,
            }
            if set(map(str, relation.source_clause_ids)) != set(
                map(str, endpoint_source_ids)
            ):
                raise ValueError(
                    "candidate relation sources must equal the endpoint clause union"
                )
        self._validate_dose_condition_relations(candidates_by_id)
        return self

    def _validate_dose_condition_relations(
        self,
        candidates_by_id: dict[str, CandidateAtomicObservation],
    ) -> None:
        condition_links_by_target: dict[str, set[str]] = {}
        for relation in self.relations:
            if relation.relation_type is PropositionRelationType.CONDITION_FOR:
                condition_links_by_target.setdefault(
                    str(relation.target_candidate_id),
                    set(),
                ).add(str(relation.source_candidate_id))
        for candidate_id, candidate in candidates_by_id.items():
            if candidate.dose_regimen is None:
                continue
            declared_condition_ids = set(
                map(str, candidate.dose_regimen.condition_candidate_ids)
            )
            if not declared_condition_ids.issubset(candidates_by_id):
                raise ValueError("dose regimen conditions must use known candidates")
            if condition_links_by_target.get(candidate_id, set()) != (
                declared_condition_ids
            ):
                raise ValueError(
                    "dose regimen condition IDs require exact condition-for relations"
                )

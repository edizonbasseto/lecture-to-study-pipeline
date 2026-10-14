"""Fail-closed validation of clause-relative numeric source bindings."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from lecture_study.domain.content_ledger import (
    ClauseUtf8ByteRange,
    EvidenceExcerpt,
    NumericBindingComponentState,
    NumericBoundaryKind,
    NumericQuantity,
    NumericSourceBinding,
    NumericSourceComponentBinding,
    QuantityComparator,
    QuantityRole,
)
from lecture_study.domain.semantic_observation import SourceClause
from lecture_study.domain.source_universe import EvidenceModality


_SOURCE_NUMBER_PATTERN = re.compile(
    r"(?<![\w.])-?(?:0|[1-9]\d*)(?:[.,]\d+)?(?:[eE][+-]?\d+)?"
)


class NumericBindingDisposition(StrEnum):
    """Closed audit result for one exact numeric source binding."""

    VERIFIED = "verified"
    UNVERIFIABLE = "unverifiable"
    INVALID = "invalid"


class NumericBindingScopeState(StrEnum):
    """Local effect of a numeric binding result on dependent work."""

    SATISFIED = "satisfied"
    NEEDS_REVIEW = "needs_review"
    QUARANTINE = "quarantine"


@dataclass(frozen=True, slots=True)
class NumericBindingViolation:
    """One deterministic failure tied to a binding component."""

    code: str
    component: str
    message: str


@dataclass(frozen=True, slots=True)
class NumericSourceBindingAuditReport:
    """Diagnostic result that never grants publication authority."""

    quantity_id: str
    source_clause_id: str
    disposition: NumericBindingDisposition
    violations: tuple[NumericBindingViolation, ...]

    @property
    def state(self) -> NumericBindingScopeState:
        if self.disposition is NumericBindingDisposition.VERIFIED:
            return NumericBindingScopeState.SATISFIED
        if self.disposition is NumericBindingDisposition.UNVERIFIABLE:
            return NumericBindingScopeState.NEEDS_REVIEW
        return NumericBindingScopeState.QUARANTINE

    @property
    def ready_for_reconciliation(self) -> bool:
        return self.disposition is NumericBindingDisposition.VERIFIED

    @property
    def ready_for_certification(self) -> bool:
        return False

    @property
    def ready_for_publication(self) -> bool:
        return False

    @property
    def ready_for_delivery(self) -> bool:
        return False

    @property
    def ready_for_derivatives(self) -> bool:
        return False


class NumericSourceBindingAuditor:
    """Validate structured numeric semantics against authoritative clause evidence."""

    LEXICON_VERSION = "numeric-source-binding-en-ptbr-v1"

    _COMPARATOR_TOKENS: Mapping[QuantityComparator, frozenset[str]] = {
        QuantityComparator.EXACT: frozenset({"=", "exactly", "exatamente"}),
        QuantityComparator.APPROXIMATE: frozenset(
            {"~", "≈", "approximately", "about", "aproximadamente", "cerca de"}
        ),
        QuantityComparator.LESS_THAN: frozenset(
            {"<", "less than", "below", "menor que", "abaixo de"}
        ),
        QuantityComparator.LESS_THAN_OR_EQUAL: frozenset(
            {
                "<=",
                "≤",
                "at most",
                "no more than",
                "menor ou igual a",
                "no máximo",
            }
        ),
        QuantityComparator.GREATER_THAN: frozenset(
            {">", "greater than", "above", "maior que", "acima de"}
        ),
        QuantityComparator.GREATER_THAN_OR_EQUAL: frozenset(
            {
                ">=",
                "≥",
                "at least",
                "no less than",
                "maior ou igual a",
                "pelo menos",
            }
        ),
        QuantityComparator.RANGE: frozenset({",", "-", "–", "to", "through", "até"}),
    }
    _ROLE_TOKENS: Mapping[QuantityRole, frozenset[str]] = {
        QuantityRole.DOSE: frozenset(
            {"administer", "dose", "give", "administrar", "dar"}
        ),
        QuantityRole.THRESHOLD: frozenset({"threshold", "limiar"}),
        QuantityRole.RANGE: frozenset({"range", "faixa"}),
        QuantityRole.INTERVAL: frozenset({"every", "interval", "a cada"}),
        QuantityRole.DURATION: frozenset({"for", "duration", "durante", "duração"}),
        QuantityRole.FREQUENCY: frozenset(
            {"frequency", "times", "frequência", "vezes"}
        ),
        QuantityRole.RATE: frozenset({"rate", "per", "taxa", "por"}),
        QuantityRole.REFERENCE_VALUE: frozenset({"reference", "normal", "referência"}),
        QuantityRole.MEASUREMENT: frozenset(
            {"measurement", "measured", "medição", "medido"}
        ),
    }
    _LOWER_BOUNDARY_TOKENS: Mapping[NumericBoundaryKind, frozenset[str]] = {
        NumericBoundaryKind.INCLUSIVE: frozenset({"["}),
        NumericBoundaryKind.EXCLUSIVE: frozenset({"("}),
    }
    _UPPER_BOUNDARY_TOKENS: Mapping[NumericBoundaryKind, frozenset[str]] = {
        NumericBoundaryKind.INCLUSIVE: frozenset({"]"}),
        NumericBoundaryKind.EXCLUSIVE: frozenset({")"}),
    }

    def audit(
        self,
        *,
        quantity: NumericQuantity,
        clauses_by_id: Mapping[str, SourceClause],
        evidence_excerpts: Sequence[EvidenceExcerpt],
        normalized_statement: str,
        require_evidence_excerpt: bool = True,
    ) -> NumericSourceBindingAuditReport:
        invalid: list[NumericBindingViolation] = []
        unverifiable: list[NumericBindingViolation] = []
        binding = quantity.source_binding
        clause = clauses_by_id.get(str(binding.source_clause_id))
        if clause is None:
            invalid.append(
                self._violation(
                    "numeric_binding_clause_missing",
                    "source_clause_id",
                    "The numeric binding refers to an unknown source clause.",
                )
            )
            return self._report(quantity, invalid, unverifiable)

        canonical_text = self._canonical_clause_text(clause, invalid)
        self._audit_source_identity(
            quantity=quantity,
            clause=clause,
            evidence_excerpts=evidence_excerpts,
            require_evidence_excerpt=require_evidence_excerpt,
            invalid=invalid,
        )
        if canonical_text is None:
            return self._report(quantity, invalid, unverifiable)

        expression = self._selected_text(
            canonical_text,
            binding.expression_selector.selector,
            "expression",
            invalid,
        )
        if expression is not None and expression != binding.exact_expression:
            invalid.append(
                self._violation(
                    "numeric_expression_mismatch",
                    "expression",
                    "The expression selector bytes differ from exact_expression.",
                )
            )
        self._audit_component_containment(binding, invalid)
        if normalized_statement != canonical_text:
            unverifiable.append(
                self._violation(
                    "numeric_statement_context_transformation_unproven",
                    "expression",
                    "The final statement does not preserve the complete source-clause context.",
                )
            )

        self._audit_component_states(quantity, unverifiable)
        self._audit_decimal_component(
            canonical_text,
            quantity.source_binding.lower_value_selector,
            quantity.lower_value,
            "lower_value",
            invalid,
        )
        if quantity.upper_value is not None:
            self._audit_decimal_component(
                canonical_text,
                quantity.source_binding.upper_value_selector,
                quantity.upper_value,
                "upper_value",
                invalid,
            )
        self._audit_token_component(
            canonical_text,
            quantity.source_binding.comparator_selector,
            quantity.comparator.value,
            self._COMPARATOR_TOKENS[quantity.comparator],
            "comparator",
            invalid,
            unverifiable,
        )
        self._audit_literal_component(
            canonical_text,
            quantity.source_binding.unit_selector,
            quantity.unit_literal,
            "unit",
            invalid,
        )
        if quantity.denominator is not None:
            self._audit_literal_component(
                canonical_text,
                quantity.source_binding.denominator_selector,
                quantity.denominator,
                "denominator",
                invalid,
            )
        self._audit_composite_unit(canonical_text, quantity, invalid)
        self._audit_token_component(
            canonical_text,
            quantity.source_binding.semantic_role_selector,
            quantity.role.value,
            self._ROLE_TOKENS[quantity.role],
            "semantic_role",
            invalid,
            unverifiable,
        )
        self._audit_boundaries(
            canonical_text,
            quantity,
            invalid,
            unverifiable,
        )
        if quantity.unit_normalized != quantity.unit_literal:
            unverifiable.append(
                self._violation(
                    "numeric_unit_normalization_unproven",
                    "unit",
                    "Unit normalization differs without an authenticated conversion.",
                )
            )
        return self._report(quantity, invalid, unverifiable)

    @staticmethod
    def overlapping_quantity_ids(
        quantities: Sequence[NumericQuantity],
    ) -> frozenset[str]:
        """Return bindings whose clause-relative expression ranges collide."""

        overlapping: set[str] = set()
        for index, left in enumerate(quantities):
            left_selector = left.source_binding.expression_selector.selector
            if left_selector is None:
                continue
            for right in quantities[index + 1 :]:
                if (
                    left.source_binding.source_clause_id
                    != right.source_binding.source_clause_id
                ):
                    continue
                right_selector = right.source_binding.expression_selector.selector
                if right_selector is None:
                    continue
                if (
                    left_selector.start_byte < right_selector.end_byte
                    and right_selector.start_byte < left_selector.end_byte
                ):
                    overlapping.update({str(left.quantity_id), str(right.quantity_id)})
        return frozenset(overlapping)

    @classmethod
    def clauses_with_unbound_numeric_tokens(
        cls,
        *,
        clauses_by_id: Mapping[str, SourceClause],
        quantities: Sequence[NumericQuantity],
    ) -> frozenset[str]:
        """Return clauses whose source numeric tokens lack an explicit value selector."""

        covered_ranges: dict[str, set[tuple[int, int]]] = {}
        for quantity in quantities:
            clause_id = str(quantity.source_binding.source_clause_id)
            clause_ranges = covered_ranges.setdefault(clause_id, set())
            for component in (
                quantity.source_binding.lower_value_selector,
                quantity.source_binding.upper_value_selector,
            ):
                if component.state is not NumericBindingComponentState.EXPLICIT:
                    continue
                selector = component.selector
                if selector is not None:
                    clause_ranges.add((selector.start_byte, selector.end_byte))

        unbound_clause_ids: set[str] = set()
        for clause_id, clause in clauses_by_id.items():
            canonical_text = cls._canonical_clause_text(clause, [])
            if canonical_text is None:
                continue
            expected_ranges = cls._source_numeric_byte_ranges(canonical_text)
            if not expected_ranges.issubset(covered_ranges.get(clause_id, set())):
                unbound_clause_ids.add(clause_id)
        return frozenset(unbound_clause_ids)

    @staticmethod
    def _source_numeric_byte_ranges(canonical_text: str) -> frozenset[tuple[int, int]]:
        ranges: set[tuple[int, int]] = set()
        for match in _SOURCE_NUMBER_PATTERN.finditer(canonical_text):
            start_byte = len(canonical_text[: match.start()].encode("utf-8"))
            end_byte = len(canonical_text[: match.end()].encode("utf-8"))
            ranges.add((start_byte, end_byte))
        return frozenset(ranges)

    @classmethod
    def _audit_component_containment(
        cls,
        binding: NumericSourceBinding,
        invalid: list[NumericBindingViolation],
    ) -> None:
        expression_selector = binding.expression_selector.selector
        if expression_selector is None:
            return
        components = {
            "lower_value": binding.lower_value_selector,
            "upper_value": binding.upper_value_selector,
            "comparator": binding.comparator_selector,
            "unit": binding.unit_selector,
            "denominator": binding.denominator_selector,
            "semantic_role": binding.semantic_role_selector,
            "lower_boundary": binding.lower_boundary_selector,
            "upper_boundary": binding.upper_boundary_selector,
        }
        explicit_selectors: list[tuple[str, ClauseUtf8ByteRange]] = []
        for name, component in components.items():
            selector = component.selector
            if selector is None:
                continue
            explicit_selectors.append((name, selector))
            if (
                expression_selector.start_byte <= selector.start_byte
                and selector.end_byte <= expression_selector.end_byte
            ):
                continue
            invalid.append(
                cls._violation(
                    "numeric_component_outside_expression",
                    name,
                    "The component selector is outside the exact numeric expression.",
                )
            )
        for index, (left_name, left) in enumerate(explicit_selectors):
            for right_name, right in explicit_selectors[index + 1 :]:
                if (
                    left.start_byte >= right.end_byte
                    or right.start_byte >= left.end_byte
                ):
                    continue
                if cls._is_allowed_component_overlap(
                    left_name,
                    left,
                    right_name,
                    right,
                ):
                    continue
                invalid.append(
                    cls._violation(
                        "numeric_component_overlap",
                        f"{left_name}:{right_name}",
                        "The component selectors overlap without an authorized semantic exception.",
                    )
                )

    @staticmethod
    def _is_allowed_component_overlap(
        left_name: str,
        left: ClauseUtf8ByteRange,
        right_name: str,
        right: ClauseUtf8ByteRange,
    ) -> bool:
        if {left_name, right_name} != {"unit", "denominator"}:
            return False
        unit = left if left_name == "unit" else right
        denominator = left if left_name == "denominator" else right
        return (
            unit.start_byte <= denominator.start_byte
            and denominator.end_byte <= unit.end_byte
        )

    def _audit_source_identity(
        self,
        *,
        quantity: NumericQuantity,
        clause: SourceClause,
        evidence_excerpts: Sequence[EvidenceExcerpt],
        require_evidence_excerpt: bool,
        invalid: list[NumericBindingViolation],
    ) -> None:
        if tuple(map(str, quantity.source_span_ids)) != (str(clause.source_span_id),):
            invalid.append(
                self._violation(
                    "numeric_binding_span_mismatch",
                    "source_clause_id",
                    "The quantity must bind exactly the source span owned by its clause.",
                )
            )
        if not require_evidence_excerpt:
            return
        matching_excerpts = tuple(
            excerpt
            for excerpt in evidence_excerpts
            if excerpt.source_clause_id == clause.clause_id
        )
        if len(matching_excerpts) != 1:
            invalid.append(
                self._violation(
                    "numeric_binding_evidence_cardinality",
                    "source_clause_id",
                    "Exactly one matching EvidenceExcerpt is required.",
                )
            )
            return
        if not self._excerpt_matches_clause(matching_excerpts[0], clause):
            invalid.append(
                self._violation(
                    "numeric_binding_evidence_identity_mismatch",
                    "source_clause_id",
                    "The EvidenceExcerpt does not preserve clause identity and provenance.",
                )
            )

    @staticmethod
    def _excerpt_matches_clause(
        excerpt: EvidenceExcerpt,
        clause: SourceClause,
    ) -> bool:
        selector = clause.selector.model_dump(mode="json", warnings=False)
        generator_identity = (
            clause.generated_observation_identity.model_dump(
                mode="json",
                warnings=False,
            )
            if clause.generated_observation_identity is not None
            else None
        )
        return (
            excerpt.source_span_id == clause.source_span_id
            and excerpt.modality is clause.modality
            and excerpt.source_content_sha256 == clause.source_content_sha256
            and excerpt.selector == selector
            and excerpt.generated_observation_identity == generator_identity
            and excerpt.verbatim_text == clause.verbatim_text
            and excerpt.observed_text == clause.observed_text
            and excerpt.text_sha256 == clause.text_sha256
        )

    @staticmethod
    def _canonical_clause_text(
        clause: SourceClause,
        invalid: list[NumericBindingViolation],
    ) -> str | None:
        if clause.modality is EvidenceModality.TRANSCRIPT:
            if clause.verbatim_text is not None and clause.observed_text is None:
                return clause.verbatim_text
        elif (
            clause.observed_text is not None
            and clause.verbatim_text is None
            and clause.generated_observation_identity is not None
        ):
            return clause.observed_text
        invalid.append(
            NumericBindingViolation(
                code="numeric_binding_modality_text_mismatch",
                component="source_clause_id",
                message="Clause modality does not match its canonical text evidence.",
            )
        )
        return None

    def _audit_component_states(
        self,
        quantity: NumericQuantity,
        unverifiable: list[NumericBindingViolation],
    ) -> None:
        components = {
            "lower_value": quantity.source_binding.lower_value_selector,
            "upper_value": quantity.source_binding.upper_value_selector,
            "comparator": quantity.source_binding.comparator_selector,
            "unit": quantity.source_binding.unit_selector,
            "denominator": quantity.source_binding.denominator_selector,
            "semantic_role": quantity.source_binding.semantic_role_selector,
            "lower_boundary": quantity.source_binding.lower_boundary_selector,
            "upper_boundary": quantity.source_binding.upper_boundary_selector,
        }
        for name, component in components.items():
            if component.state is NumericBindingComponentState.IMPLICIT:
                unverifiable.append(
                    self._violation(
                        "numeric_component_implicit",
                        name,
                        "An implicit component requires authenticated human review.",
                    )
                )

    def _audit_decimal_component(
        self,
        canonical_text: str,
        component: NumericSourceComponentBinding,
        expected: str,
        name: str,
        invalid: list[NumericBindingViolation],
    ) -> None:
        selected = self._selected_explicit_component(
            canonical_text,
            component,
            name,
            invalid,
        )
        if selected is None:
            return
        try:
            parsed = self._parse_source_decimal(selected)
        except InvalidOperation:
            invalid.append(
                self._violation(
                    "numeric_decimal_source_invalid",
                    name,
                    "Selected bytes are not an unambiguous decimal value.",
                )
            )
            return
        normalized_source = selected.replace(",", ".")
        if parsed != Decimal(expected) or normalized_source != expected:
            invalid.append(
                self._violation(
                    "numeric_decimal_value_mismatch",
                    name,
                    "Selected decimal value or scale differs from the structured quantity.",
                )
            )

    @staticmethod
    def _parse_source_decimal(selected: str) -> Decimal:
        token = selected
        if token.count(",") > 1 or token.count(".") > 1:
            raise InvalidOperation
        if "," in token and "." in token:
            raise InvalidOperation
        return Decimal(token.replace(",", "."))

    def _audit_literal_component(
        self,
        canonical_text: str,
        component: NumericSourceComponentBinding,
        expected: str,
        name: str,
        invalid: list[NumericBindingViolation],
    ) -> None:
        selected = self._selected_explicit_component(
            canonical_text,
            component,
            name,
            invalid,
        )
        if selected is not None and selected != expected:
            invalid.append(
                self._violation(
                    f"numeric_{name}_mismatch",
                    name,
                    f"Selected {name} bytes differ from the structured value.",
                )
            )

    def _audit_composite_unit(
        self,
        canonical_text: str,
        quantity: NumericQuantity,
        invalid: list[NumericBindingViolation],
    ) -> None:
        unit_component = quantity.source_binding.unit_selector
        denominator_component = quantity.source_binding.denominator_selector
        unit_selector = unit_component.selector
        denominator_selector = denominator_component.selector
        normalized_unit = quantity.unit_literal.casefold()
        unit_is_composite = any(
            separator in normalized_unit for separator in ("/", " per ", " por ")
        )
        if quantity.denominator is None:
            source_bytes = canonical_text.encode("utf-8")
            source_suffix = (
                source_bytes[unit_selector.end_byte :]
                if unit_selector is not None
                else b""
            )
            normalized_suffix = source_suffix.lower()
            selector_ends_before_denominator = any(
                normalized_suffix.startswith(separator)
                for separator in (b"/", b" per ", b" por ")
            )
            if unit_is_composite or selector_ends_before_denominator:
                invalid.append(
                    self._violation(
                        "numeric_denominator_omitted",
                        "denominator",
                        "A composite source unit requires an explicit denominator.",
                    )
                )
            return
        if not unit_is_composite:
            if denominator_component.state is NumericBindingComponentState.IMPLICIT:
                return
            invalid.append(
                self._violation(
                    "numeric_denominator_not_demonstrated",
                    "denominator",
                    "The declared denominator is absent from the unit literal.",
                )
            )
            return
        if (
            unit_selector is None
            or denominator_selector is None
            or not (
                unit_selector.start_byte <= denominator_selector.start_byte
                and denominator_selector.end_byte <= unit_selector.end_byte
            )
        ):
            invalid.append(
                self._violation(
                    "numeric_denominator_selector_mismatch",
                    "denominator",
                    "The denominator selector must be nested in the composite unit.",
                )
            )

    def _audit_token_component(
        self,
        canonical_text: str,
        component: NumericSourceComponentBinding,
        expected_value: str,
        allowed_tokens: frozenset[str],
        name: str,
        invalid: list[NumericBindingViolation],
        unverifiable: list[NumericBindingViolation],
    ) -> None:
        selected = self._selected_explicit_component(
            canonical_text,
            component,
            name,
            invalid,
        )
        if selected is None:
            return
        normalized_token = selected.casefold()
        if normalized_token in allowed_tokens:
            return
        known_tokens = (
            self._known_tokens(self._COMPARATOR_TOKENS.values())
            if name == "comparator"
            else self._known_tokens(self._ROLE_TOKENS.values())
        )
        target = invalid if normalized_token in known_tokens else unverifiable
        code = (
            f"numeric_{name}_mismatch"
            if normalized_token in known_tokens
            else f"numeric_{name}_not_demonstrated"
        )
        target.append(
            self._violation(
                code,
                name,
                f"Selected bytes do not demonstrate {expected_value}.",
            )
        )

    @staticmethod
    def _known_tokens(
        token_groups: Iterable[frozenset[str]],
    ) -> frozenset[str]:
        return frozenset(token for tokens in token_groups for token in tokens)

    def _audit_boundaries(
        self,
        canonical_text: str,
        quantity: NumericQuantity,
        invalid: list[NumericBindingViolation],
        unverifiable: list[NumericBindingViolation],
    ) -> None:
        if quantity.comparator is not QuantityComparator.RANGE:
            return
        self._audit_boundary(
            canonical_text,
            quantity.source_binding.lower_boundary_selector,
            quantity.lower_boundary,
            self._LOWER_BOUNDARY_TOKENS,
            "lower_boundary",
            invalid,
            unverifiable,
        )
        self._audit_boundary(
            canonical_text,
            quantity.source_binding.upper_boundary_selector,
            quantity.upper_boundary,
            self._UPPER_BOUNDARY_TOKENS,
            "upper_boundary",
            invalid,
            unverifiable,
        )

    def _audit_boundary(
        self,
        canonical_text: str,
        component: NumericSourceComponentBinding,
        boundary: NumericBoundaryKind,
        allowed_by_kind: Mapping[NumericBoundaryKind, frozenset[str]],
        name: str,
        invalid: list[NumericBindingViolation],
        unverifiable: list[NumericBindingViolation],
    ) -> None:
        selected = self._selected_explicit_component(
            canonical_text,
            component,
            name,
            invalid,
        )
        if selected is None:
            return
        allowed = allowed_by_kind.get(boundary)
        if allowed is None or selected not in allowed:
            invalid.append(
                self._violation(
                    "numeric_boundary_direction_mismatch",
                    name,
                    "Selected delimiter differs from the structured boundary.",
                )
            )

    def _selected_explicit_component(
        self,
        canonical_text: str,
        component: NumericSourceComponentBinding,
        name: str,
        invalid: list[NumericBindingViolation],
    ) -> str | None:
        if component.state is not NumericBindingComponentState.EXPLICIT:
            return None
        return self._selected_text(
            canonical_text,
            component.selector,
            name,
            invalid,
        )

    def _selected_text(
        self,
        canonical_text: str,
        selector: ClauseUtf8ByteRange | None,
        component: str,
        invalid: list[NumericBindingViolation],
    ) -> str | None:
        if selector is None:
            invalid.append(
                self._violation(
                    "numeric_selector_missing",
                    component,
                    "An explicit component requires a non-empty selector.",
                )
            )
            return None
        source_bytes = canonical_text.encode("utf-8")
        if selector.end_byte > len(source_bytes):
            invalid.append(
                self._violation(
                    "numeric_selector_out_of_bounds",
                    component,
                    "The selector exceeds the canonical clause byte length.",
                )
            )
            return None
        selected = source_bytes[selector.start_byte : selector.end_byte]
        try:
            return selected.decode("utf-8")
        except UnicodeDecodeError:
            invalid.append(
                self._violation(
                    "numeric_selector_splits_utf8",
                    component,
                    "The selector splits a UTF-8 code point.",
                )
            )
            return None

    @staticmethod
    def _report(
        quantity: NumericQuantity,
        invalid: Sequence[NumericBindingViolation],
        unverifiable: Sequence[NumericBindingViolation],
    ) -> NumericSourceBindingAuditReport:
        if invalid:
            disposition = NumericBindingDisposition.INVALID
            violations = tuple((*invalid, *unverifiable))
        elif unverifiable:
            disposition = NumericBindingDisposition.UNVERIFIABLE
            violations = tuple(unverifiable)
        else:
            disposition = NumericBindingDisposition.VERIFIED
            violations = ()
        return NumericSourceBindingAuditReport(
            quantity_id=str(quantity.quantity_id),
            source_clause_id=str(quantity.source_binding.source_clause_id),
            disposition=disposition,
            violations=violations,
        )

    @staticmethod
    def _violation(
        code: str,
        component: str,
        message: str,
    ) -> NumericBindingViolation:
        return NumericBindingViolation(
            code=code,
            component=component,
            message=message,
        )

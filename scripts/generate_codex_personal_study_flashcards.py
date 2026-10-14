#!/usr/bin/env python3
"""Generate one lecture's personal-study flashcard deck (ADR 0024's
uncertified personal-study route) through the LLM gateway (ADR 0046),
grounded per learning unit -- mirrors scripts/generate_codex_mock_exam.py
exactly, same family of gateway-backed scripts.

The adapter selected by the gateway configuration owns the child process,
its environment and its scratch directory; this script holds no process
code. It runs outside the orchestrator, so `open_stage_gateway` is its own
configuration and egress validation: an unknown or misconfigured adapter
exits 1 before the first call. Each run writes a metadata-only run record
under `workspace/data/llm_runs/<lecture_id>/flashcards.json`.

Execution is lecture-scoped: this script takes exactly one lecture
identifier, has no batch mode, and never discovers over the ledgers root.

Card count is not a fixed target (2026-09-16 operator report:
`flashcards/personal_study_generator.py`'s `_derive_card_count` was a
flat `2 * assembled_unit_count`, independent of how much content a unit
actually has -- the exact fixed-count shape ADR 0030 already moved the
exam route away from, for the same "evidence completeness is the only
criterion" reason). Each unit asks for one card per grounded clinical
fact in that unit's own Semantic Ledger, capped per unit
(`--cards-per-unit-cap`) to avoid near-duplicate cards from a single
dense unit. If the resulting total falls short of the floor
(`--minimum-total-cards`), the densest units are asked for one
additional card each, in descending fact-count order, until the floor is
met -- identical backfill shape to the mock-exam script's own
`_generate_supplemental_questions`.

The flashcard-specific student-facing-text rules (no referring phrases
such as "segundo o material", pt-BR only) were carried over from the
deleted local generator's `_FLASHCARD_INSTRUCTION`.

RESOLVED: the output-collision-with-the-local-route note that lived here
is obsolete -- the local generator and its runner script were deleted
under ADR 0038 and this script is the only writer of
`workspace/data/18_personal_study_flashcards/{lecture_id}/`.

Unit titles come from the critic-approved, post-repair units in
`workspace/data/09_validated_units/{lecture_id}/`, never from the raw
Phase 6 drafts (ADR 0045): a lecture with any unit that failed the
critic, a draft the critic never reviewed, or a gap in the sequence is
blocked as a whole and no deck is written. `08_drafts` is read only to
confirm that the critic reviewed every draft in its current version.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from lecture_study.assembly.assembler import load_critic_approved_drafts
from lecture_study.cli.llm_gateway_wiring import open_stage_gateway
from lecture_study.domain.lecture_ownership import (
    LectureOwnershipError,
    require_lecture_ownership,
)
from lecture_study.domain.errors import (
    LectureNotFoundError,
    MissingSequenceError,
    UnresolvedReviewError,
)
from lecture_study.domain.models import (
    ArtifactExecutionMode,
    LearningUnitDraft,
    PersonalStudyFlashcard,
    PersonalStudyFlashcardDeck,
    SemanticFact,
    SemanticLedger,
)
from lecture_study.llm import LlmGateway, LlmGatewayError, Stage, complete_json
from lecture_study.llm.run_record import LLM_RUNS_ROOT, record_llm_run
from lecture_study.ops.llm_response_contract import (
    LlmResponseContractError,
    parse_llm_json_response,
)
from lecture_study.quality.student_facing_text import (
    describe_student_facing_text_defects,
    student_facing_text_defects,
)

DEFAULT_LEDGERS_ROOT = Path("workspace/data/07_semantic_ledgers")
DEFAULT_DRAFTS_ROOT = Path("workspace/data/08_drafts")
DEFAULT_VALIDATED_UNITS_ROOT = Path("workspace/data/09_validated_units")
DEFAULT_OUTPUT_ROOT = Path("workspace/data/18_personal_study_flashcards")
_DEFAULT_CARDS_PER_UNIT_CAP = 5
_DEFAULT_MINIMUM_TOTAL_CARDS = 12
_CARD_ARRAY_ADAPTER: TypeAdapter[list[PersonalStudyFlashcard]] = TypeAdapter(
    list[PersonalStudyFlashcard]
)
_CARD_ARRAY_SCHEMA = json.dumps(_CARD_ARRAY_ADAPTER.json_schema())
_CRITIC_GATE_ERRORS = (
    LectureNotFoundError,
    MissingSequenceError,
    UnresolvedReviewError,
)

# Referring-phrase ban and pt-BR requirement mirror
# the retired flashcards/personal_study_generator.py's _FLASHCARD_INSTRUCTION
# verbatim; only the per-unit grounding and count derivation differ.
_FLASHCARD_INSTRUCTION = (
    "You are an expert veterinary professor writing personal-study "
    "flashcards for one learning unit of a lecture. Use ONLY the "
    "clinical facts listed below as your source of truth: NEVER use "
    "outside clinical knowledge, and never invent a fact not listed. "
    "Produce exactly {card_count} flashcards, each stating a genuine "
    "clinical question as its prompt, grounded in a DIFFERENT one of the "
    "facts below, and a grounded answer. A card's prompt MUST NOT be a "
    "source sentence with a word removed, and MUST NOT be a section "
    "heading turned into a prompt -- every prompt must be answerable "
    "from the facts below alone. "
    "Write every textual output field -- prompt and answer -- entirely "
    "in Brazilian Portuguese (pt-BR), with no English words and no "
    "sentence mixing two languages. "
    "Write for a student who never sees this instruction: a prompt or "
    "answer states its clinical facts directly and never refers to the "
    "lecture material, the source text, the transcript, the slides, this "
    "instruction or any internal name for them. A referring phrase such "
    'as "segundo o material", "conforme o texto" or "de acordo com a '
    'aula" makes the card unusable: state the clinical fact itself '
    "instead. "
    "Output valid JSON matching the provided schema: a JSON array of "
    "card objects, nothing else.\n\n"
    "Learning unit: {unit_title}\n\n"
    "Clinical facts:\n{facts}\n\n"
    "JSON Schema (the response MUST validate against this schema "
    "exactly):\n{schema}\n\n"
    "Respond with ONLY the JSON array matching the schema above -- no "
    "prose, no markdown fences."
)

_SUPPLEMENTAL_INSTRUCTION = (
    "You are an expert veterinary professor writing one additional "
    "personal-study flashcard for a learning unit that already has "
    "cards. Use ONLY the clinical facts listed below as your source of "
    "truth: NEVER use outside clinical knowledge. Produce exactly ONE "
    "flashcard, grounded in a fact from the list below that is NOT "
    "already covered by the existing cards. "
    "Write every textual output field entirely in Brazilian Portuguese "
    "(pt-BR). Write for a student who never sees this instruction: never "
    "refer to the lecture material, the source text, the transcript, the "
    "slides, this instruction, or any internal name for them. "
    "Output valid JSON matching the provided schema: a JSON array "
    "containing exactly one card object, nothing else.\n\n"
    "Learning unit: {unit_title}\n\n"
    "Clinical facts:\n{facts}\n\n"
    "Cards already written for this unit (write about a different "
    "fact):\n{existing_cards}\n\n"
    "JSON Schema (the response MUST validate against this schema "
    "exactly):\n{schema}\n\n"
    "Respond with ONLY the JSON array matching the schema above -- no "
    "prose, no markdown fences."
)


def main() -> int:
    args = _build_argument_parser().parse_args()
    try:
        return _run(args)
    except _CRITIC_GATE_ERRORS as error:
        print(
            "error: aula bloqueada pelo crítico, deck de flashcards não "
            f"gerado: {error}",
            file=sys.stderr,
        )
        return 1
    except (
        LlmResponseContractError,
        LlmGatewayError,
        FileNotFoundError,
        ValidationError,
        LectureOwnershipError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace) -> int:
    drafts = load_critic_approved_drafts(
        args.validated_units_root, args.drafts_root, args.lecture_id
    )
    ledgers = _load_semantic_ledgers(args.ledgers_root, args.lecture_id)
    drafts_by_sequence = {draft.pack_sequence: draft for draft in drafts}
    grounded_units = tuple(
        ledger
        for ledger in sorted(ledgers, key=lambda ledger: ledger.pack_sequence)
        if ledger.facts and ledger.pack_sequence in drafts_by_sequence
    )

    gateway = open_stage_gateway(
        Stage.FLASHCARDS,
        lecture_id=args.lecture_id,
        codex_executable_override=args.codex_executable,
    )
    with record_llm_run(
        lecture_id=args.lecture_id,
        script_stage=Stage.FLASHCARDS,
        schema_name="list[PersonalStudyFlashcard]",
        root=args.llm_runs_root,
    ) as recorder:
        cards = _generate_all_cards(
            gateway=recorder.track(gateway, stage=Stage.FLASHCARDS),
            grounded_units=grounded_units,
            drafts_by_sequence=drafts_by_sequence,
            cards_per_unit_cap=args.cards_per_unit_cap,
            minimum_total_cards=args.minimum_total_cards,
        )
        deck = _build_deck(args.lecture_id, cards)
        # The record closes as succeeded only after the deck is written.
        output_path = _write_deck_atomically(deck, args.output_root / args.lecture_id)
    print(f"gerado(s) {len(cards)} card(s) para a aula {args.lecture_id!r}")
    print(f"escrito em {output_path}")
    return 0


def _generate_all_cards(
    *,
    gateway: LlmGateway,
    grounded_units: tuple[SemanticLedger, ...],
    drafts_by_sequence: dict[int, LearningUnitDraft],
    cards_per_unit_cap: int,
    minimum_total_cards: int,
) -> list[PersonalStudyFlashcard]:
    cards: list[PersonalStudyFlashcard] = []
    for ledger in grounded_units:
        draft = drafts_by_sequence[ledger.pack_sequence]
        card_count = min(len(ledger.facts), cards_per_unit_cap)
        unit_cards = _generate_unit_cards(
            gateway=gateway,
            unit_title=draft.title,
            facts=ledger.facts,
            card_count=card_count,
        )
        cards.extend(
            card.model_copy(update={"source_unit_sequence": ledger.pack_sequence})
            for card in unit_cards
        )
        print(
            f"unidade {ledger.pack_sequence} ({draft.title!r}): {len(unit_cards)} card(s)"
        )

    cards.extend(
        _generate_supplemental_cards(
            gateway=gateway,
            grounded_units=grounded_units,
            drafts_by_sequence=drafts_by_sequence,
            existing_cards=cards,
            minimum_total_cards=minimum_total_cards,
        )
    )
    return cards


def _write_deck_atomically(deck: PersonalStudyFlashcardDeck, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "deck.json"
    # Atomic tmp+rename, matching the retired flashcards/personal_study_generator.py's
    # own write_personal_study_flashcard_deck() convention for this exact
    # output tree.
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp_path.write_text(deck.model_dump_json(indent=2), encoding="utf-8")
    tmp_path.replace(output_path)
    return output_path


def _build_deck(
    lecture_id: str, cards: list[PersonalStudyFlashcard]
) -> PersonalStudyFlashcardDeck:
    # PersonalStudyFlashcardDeck rejects two cards sharing the same
    # `prompt` text deck-wide (domain/models.py), but each unit's own
    # generation call only steers Codex away from duplicates within that
    # unit's own existing_cards -- a cross-unit collision (two units
    # covering overlapping anatomy) is possible and was, before this fix,
    # an uncaught pydantic.ValidationError crash discarding every
    # already-completed Codex call (principal-code-reviewer BLOCK).
    try:
        return PersonalStudyFlashcardDeck(
            lecture_id=lecture_id,
            cards=cards,
            execution_mode=ArtifactExecutionMode.UNCERTIFIED_PERSONAL_STUDY,
        )
    except ValidationError as error:
        raise LlmResponseContractError(
            f"generated flashcard deck failed validation: {error}",
            retry_category="schema",
        ) from error


def _generate_supplemental_cards(
    *,
    gateway: LlmGateway,
    grounded_units: tuple[SemanticLedger, ...],
    drafts_by_sequence: dict[int, LearningUnitDraft],
    existing_cards: list[PersonalStudyFlashcard],
    minimum_total_cards: int,
) -> list[PersonalStudyFlashcard]:
    """Ask the densest units for one more card each, in descending
    fact-count order, until the deck-wide floor is met."""

    supplemental: list[PersonalStudyFlashcard] = []
    densest_first = sorted(
        grounded_units, key=lambda ledger: len(ledger.facts), reverse=True
    )
    while len(existing_cards) + len(supplemental) < minimum_total_cards:
        remaining_capacity = False
        for ledger in densest_first:
            if len(existing_cards) + len(supplemental) >= minimum_total_cards:
                break
            unit_existing = [
                card
                for card in (*existing_cards, *supplemental)
                if card.source_unit_sequence == ledger.pack_sequence
            ]
            if len(unit_existing) >= len(ledger.facts):
                continue
            remaining_capacity = True
            draft = drafts_by_sequence[ledger.pack_sequence]
            [extra_card] = _generate_supplemental_unit_card(
                gateway=gateway,
                unit_title=draft.title,
                facts=ledger.facts,
                existing_cards=unit_existing,
            )
            supplemental.append(
                extra_card.model_copy(
                    update={"source_unit_sequence": ledger.pack_sequence}
                )
            )
        if not remaining_capacity:
            break
    return supplemental


def _generate_unit_cards(
    *,
    gateway: LlmGateway,
    unit_title: str,
    facts: Sequence[SemanticFact],
    card_count: int,
) -> list[PersonalStudyFlashcard]:
    prompt = _FLASHCARD_INSTRUCTION.format(
        card_count=card_count,
        unit_title=unit_title,
        facts=_format_facts(facts),
        schema=_CARD_ARRAY_SCHEMA,
    )
    return _request_cards(gateway=gateway, prompt=prompt, expected_count=card_count)


def _generate_supplemental_unit_card(
    *,
    gateway: LlmGateway,
    unit_title: str,
    facts: Sequence[SemanticFact],
    existing_cards: list[PersonalStudyFlashcard],
) -> list[PersonalStudyFlashcard]:
    prompt = _SUPPLEMENTAL_INSTRUCTION.format(
        unit_title=unit_title,
        facts=_format_facts(facts),
        existing_cards="\n".join(f"- {card.prompt}" for card in existing_cards),
        schema=_CARD_ARRAY_SCHEMA,
    )
    return _request_cards(gateway=gateway, prompt=prompt, expected_count=1)


def _request_cards(
    *, gateway: LlmGateway, prompt: str, expected_count: int
) -> list[PersonalStudyFlashcard]:
    """Request cards; the student guard and the count check run inside the
    gateway's single bounded retry."""

    return complete_json(
        gateway,
        stage=Stage.FLASHCARDS,
        prompt=prompt,
        parse=_parse_card_array,
        validate=lambda cards: _validate_cards(cards, expected_count),
    )


def _validate_cards(cards: list[PersonalStudyFlashcard], expected_count: int) -> None:
    _reject_cards_unfit_for_a_student(cards)
    if len(cards) != expected_count:
        raise LlmResponseContractError(
            f"expected {expected_count} card(s), got {len(cards)}",
            retry_category="contract",
        )


def _reject_cards_unfit_for_a_student(
    cards: Sequence[PersonalStudyFlashcard],
) -> None:
    """Fail the completion when a card face is not pt-BR or names an
    internal pipeline abstraction. Same ADR 0027 rationale as the exam
    and podcast guards: prompt wording alone is not enforcement, the
    detector runs on the parsed output inside the bounded retry path."""
    passages = [
        passage
        for index, card in enumerate(cards, start=1)
        for passage in (
            (f"card {index} prompt", card.prompt),
            (f"card {index} answer", card.answer),
        )
    ]
    defects = student_facing_text_defects(passages)
    if defects:
        raise LlmResponseContractError(
            "flashcard text is unfit for a student: "
            f"{describe_student_facing_text_defects(defects)}",
            retry_category="contract",
        )


def _parse_card_array(output_bytes: bytes) -> list[PersonalStudyFlashcard]:
    return parse_llm_json_response(
        output_bytes,
        expected_kind="array",
        response_adapter=_CARD_ARRAY_ADAPTER,
        declared_type=list[PersonalStudyFlashcard],
    )


def _format_facts(facts: Sequence[SemanticFact]) -> str:
    return "\n".join(
        f"- {fact.entity_name} ({fact.category}): {fact.clinical_context}"
        for fact in facts
    )


def _load_semantic_ledgers(
    ledgers_root: Path,
    lecture_id: str,
) -> tuple[SemanticLedger, ...]:
    lecture_dir = ledgers_root / lecture_id
    ledger_paths = sorted(lecture_dir.glob("ledger_*.json"))
    if not ledger_paths:
        raise FileNotFoundError(f"nenhum ledger semântico encontrado em {lecture_dir}")
    return require_lecture_ownership(
        lecture_id,
        (SemanticLedger.model_validate_json(path.read_text()) for path in ledger_paths),
        kind="semantic ledgers",
    )


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Gera o deck de flashcards de estudo pessoal de uma aula via "
            "gateway LLM, com cards fundamentados por unidade de aprendizagem."
        )
    )
    parser.add_argument("lecture_id")
    parser.add_argument("--ledgers-root", type=Path, default=DEFAULT_LEDGERS_ROOT)
    parser.add_argument(
        "--validated-units-root",
        type=Path,
        default=DEFAULT_VALIDATED_UNITS_ROOT,
        help=(
            "Unidades aprovadas pelo crítico (Fase 7), fonte do conteúdo "
            "pós-reparo usado nos flashcards."
        ),
    )
    parser.add_argument(
        "--drafts-root",
        type=Path,
        default=DEFAULT_DRAFTS_ROOT,
        help=(
            "Rascunhos da Fase 6, usados apenas para conferir que cada um "
            "foi revisado pelo crítico na versão atual."
        ),
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--codex-executable",
        default=None,
        help=(
            "sobrescreve `codex.executable` da configuração do gateway "
            "(padrão: o valor da configuração)"
        ),
    )
    parser.add_argument("--llm-runs-root", type=Path, default=LLM_RUNS_ROOT)
    parser.add_argument(
        "--cards-per-unit-cap",
        type=int,
        default=_DEFAULT_CARDS_PER_UNIT_CAP,
    )
    parser.add_argument(
        "--minimum-total-cards",
        type=int,
        default=_DEFAULT_MINIMUM_TOTAL_CARDS,
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())

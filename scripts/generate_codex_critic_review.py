#!/usr/bin/env python3
"""Generate one lecture's critic reviews (Phase 7 artifact, ADR 0024's
uncertified personal-study route) through the LLM gateway.

Stage 7 calls the `LlmGateway` port (ADR 0046) instead of the local
qwen3-32b/llama.cpp critic worker. Reviews use the gateway of the critic
stage; the redrafts of the repair loop below use the gateway of the drafting
stage (each stage selects its adapter by configuration, and the Codex route
is refused for any lecture the operator has not flagged as cleared for remote
processing). Each run writes a metadata-only run record under
`workspace/data/llm_runs/<lecture_id>/critic.json`.

Per ADR 0035: the local route's reproducible defect on 10006's pack 12
(the model substituting a Chinese-language JSON key and appending an
invalid `//` comment) motivated moving both Phase 5 and Phase 7 to Codex,
following ADR 0030's precedent for Phase 6/exam.

Execution is lecture-scoped, matching the sibling Codex scripts.

2026-09-16 (operator report, batch run on lecture 10005): ADR 0035
decision 4 originally left the local `CriticOrchestrator`'s repair loop
(redraft-and-retry a rejected draft) out of scope, persisting a failing
review as-is. Running the calibrated critic instruction against a real
lecture rejected 19 of 23 units -- almost all for genuine, real grounding
defects (an invented species-specificity claim, an inverted risk factor,
an overgeneralized prognosis), not pedantic wording complaints. With no
repair path, that leaves most of a lecture permanently flagged instead of
fixed. This script now mirrors `CriticOrchestrator._evaluate_with_repair`
exactly: on rejection, it redrafts via `generate_codex_drafts.py`'s own
`_generate_draft` (feeding the critic's feedback back into the drafting
prompt, the same heading the retired `drafting/llm_worker.py`'s local repair path (module deleted under ADR 0038)
uses) and re-reviews, up to `--max-retries` attempts, before persisting
whatever the last attempt produced -- passed or not.

The review instruction mirrors (verbatim; that module was deleted under ADR 0038) the retired critic/llm_worker.py's own
`_CRITIC_INSTRUCTION` verbatim -- only the invocation backend changes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path

import generate_codex_drafts
from pydantic import TypeAdapter, ValidationError

from lecture_study.cli.llm_gateway_wiring import open_stage_gateway
from lecture_study.domain.lecture_ownership import (
    LectureOwnershipError,
    require_lecture_ownership,
)
from lecture_study.domain.models import (
    CriticReview,
    EvidencePack,
    LearningUnitDraft,
    SafeIdentifier,
    SemanticLedger,
    ValidatedLearningUnit,
)
from lecture_study.llm import LlmGateway, LlmGatewayError, Stage, complete_json
from lecture_study.llm.run_record import LLM_RUNS_ROOT, record_llm_run
from lecture_study.ops.llm_instructions import CRITIC_INSTRUCTION as _CRITIC_INSTRUCTION
from lecture_study.ops.llm_response_contract import (
    LlmResponseContractError,
    parse_llm_json_response,
)

DEFAULT_DRAFTS_ROOT = Path("workspace/data/08_drafts")
DEFAULT_PACKS_ROOT = Path("workspace/data/06_evidence_packs")
DEFAULT_LEDGERS_ROOT = Path("workspace/data/07_semantic_ledgers")
DEFAULT_OUTPUT_ROOT = Path("workspace/data/09_validated_units")
_LECTURE_ID_ADAPTER: TypeAdapter[str] = TypeAdapter(SafeIdentifier)
_REVIEW_ADAPTER: TypeAdapter[CriticReview] = TypeAdapter(CriticReview)
_REVIEW_SCHEMA = json.dumps(CriticReview.model_json_schema())


def main() -> int:
    args = _build_argument_parser().parse_args()
    try:
        return _run(args)
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
    lecture_id = _LECTURE_ID_ADAPTER.validate_python(args.lecture_id)
    drafts = _load_drafts(args.drafts_root, lecture_id)
    packs_by_sequence = {
        pack.pack_sequence: pack
        for pack in _load_evidence_packs(args.packs_root, lecture_id)
    }
    ledgers_by_sequence = {
        ledger.pack_sequence: ledger
        for ledger in _load_semantic_ledgers(args.ledgers_root, lecture_id)
    }
    # Both gateways are built (and so policy-checked) before the first call:
    # redrafts inside stage 7 honor the drafting-stage adapter selection.
    critic_gateway = open_stage_gateway(
        Stage.CRITIC,
        lecture_id=lecture_id,
        codex_executable_override=args.codex_executable,
    )
    drafting_gateway = open_stage_gateway(
        Stage.DRAFTING,
        lecture_id=lecture_id,
        codex_executable_override=args.codex_executable,
    )
    output_dir = args.output_root / lecture_id
    output_dir.mkdir(parents=True, exist_ok=True)
    with record_llm_run(
        lecture_id=lecture_id,
        script_stage=Stage.CRITIC,
        schema_name="CriticReview",
        root=args.llm_runs_root,
    ) as recorder:
        _review_drafts(
            critic_gateway=recorder.track(critic_gateway, stage=Stage.CRITIC),
            drafting_gateway=recorder.track(drafting_gateway, stage=Stage.DRAFTING),
            drafts=drafts,
            packs_by_sequence=packs_by_sequence,
            ledgers_by_sequence=ledgers_by_sequence,
            max_retries=args.max_retries,
            output_dir=output_dir,
        )
    return 0


def _review_drafts(
    *,
    critic_gateway: LlmGateway,
    drafting_gateway: LlmGateway,
    drafts: tuple[LearningUnitDraft, ...],
    packs_by_sequence: Mapping[int, EvidencePack],
    ledgers_by_sequence: Mapping[int, SemanticLedger],
    max_retries: int,
    output_dir: Path,
) -> None:
    for draft in drafts:
        pack = packs_by_sequence.get(draft.pack_sequence)
        ledger = ledgers_by_sequence.get(draft.pack_sequence)
        if pack is None or ledger is None:
            print(
                f"unidade {draft.pack_sequence}: sem pack/ledger correspondente, "
                "pulando",
                file=sys.stderr,
            )
            continue
        final_draft, review, attempts = _evaluate_with_repair(
            critic_gateway=critic_gateway,
            drafting_gateway=drafting_gateway,
            draft=draft,
            pack=pack,
            ledger=ledger,
            max_retries=max_retries,
        )
        _write_validated_unit(
            output_dir, pack=pack, draft=final_draft, review=review, attempts=attempts
        )


def _write_validated_unit(
    output_dir: Path,
    *,
    pack: EvidencePack,
    draft: LearningUnitDraft,
    review: CriticReview,
    attempts: int,
) -> None:
    # Anchored to the trusted `pack`, not `draft` -- matches
    # CriticOrchestrator._process_draft's own identity guard
    # (critic/pipeline.py). A repair attempt's redraft is untrusted
    # model output: its own echoed lecture_id/pack_sequence could
    # drift (the exact model-echo defect class ADR 0035 exists to
    # guard against) without this file's own identity silently
    # corrupting to match it.
    unit = ValidatedLearningUnit(
        lecture_id=pack.lecture_id,
        pack_sequence=pack.pack_sequence,
        content=draft,
        review=review,
    )
    output_path = output_dir / f"unit_{pack.pack_sequence:03d}.json"
    output_path.write_text(unit.model_dump_json(indent=2), encoding="utf-8")
    verdict = "aprovado" if review.passed else "REPROVADO"
    print(
        f"unidade {pack.pack_sequence}: {verdict} "
        f"({attempts} tentativa(s) de reparo), escrito em {output_path}"
    )


def _evaluate_with_repair(
    *,
    critic_gateway: LlmGateway,
    drafting_gateway: LlmGateway,
    draft: LearningUnitDraft,
    pack: EvidencePack,
    ledger: SemanticLedger,
    max_retries: int,
) -> tuple[LearningUnitDraft, CriticReview, int]:
    """Mirrors CriticOrchestrator._evaluate_with_repair (critic/pipeline.py):
    on rejection, redraft with the critic's feedback and re-review, up to
    max_retries times, before returning whatever the last attempt
    produced."""
    current_draft = draft
    review = _generate_review(
        gateway=critic_gateway, draft=current_draft, pack=pack, ledger=ledger
    )
    attempts = 0
    while not review.passed and attempts < max_retries:
        current_draft = generate_codex_drafts._generate_draft(
            gateway=drafting_gateway,
            pack=pack,
            ledger=ledger,
            feedback=review.feedback,
        )
        review = _generate_review(
            gateway=critic_gateway, draft=current_draft, pack=pack, ledger=ledger
        )
        attempts += 1
    return current_draft, review, attempts


def _generate_review(
    *,
    gateway: LlmGateway,
    draft: LearningUnitDraft,
    pack: EvidencePack,
    ledger: SemanticLedger,
) -> CriticReview:
    return complete_json(
        gateway,
        stage=Stage.CRITIC,
        prompt=_build_prompt(draft, pack, ledger),
        parse=_parse_review,
    )


def _build_prompt(
    draft: LearningUnitDraft, pack: EvidencePack, ledger: SemanticLedger
) -> str:
    ocr_text = pack.anchor_frame.ocr_text if pack.anchor_frame else ""
    transcript_text = " ".join(segment.text for segment in pack.segments)
    ledger_facts = "\n".join(
        f"- {fact.entity_name} ({fact.category}): {fact.clinical_context}"
        for fact in ledger.facts
    )
    return (
        f"{_CRITIC_INSTRUCTION}\n\n"
        f"DRAFT title: {draft.title}\n"
        f"DRAFT body:\n{draft.body_markdown}\n"
        f"DRAFT clinical_warnings: {draft.clinical_warnings}\n\n"
        f"EVIDENCE - Slide text:\n{ocr_text}\n\n"
        f"EVIDENCE - Transcript:\n{transcript_text}\n\n"
        f"CLINICAL LEDGER (candidate facts; EVIDENCE prevails):\n"
        f"{ledger_facts}\n\n"
        f"JSON Schema (the response MUST validate against this schema "
        f"exactly):\n{_REVIEW_SCHEMA}\n\n"
        "Respond with ONLY the JSON object matching the schema above -- "
        "no explanation, no markdown fences, no extra text, no comments."
    )


def _parse_review(output_bytes: bytes) -> CriticReview:
    return parse_llm_json_response(
        output_bytes,
        expected_kind="object",
        response_adapter=_REVIEW_ADAPTER,
        declared_type=CriticReview,
    )


def _load_drafts(
    drafts_root: Path,
    lecture_id: str,
) -> tuple[LearningUnitDraft, ...]:
    lecture_dir = drafts_root / lecture_id
    draft_paths = sorted(lecture_dir.glob("draft_*.json"))
    if not draft_paths:
        raise FileNotFoundError(f"nenhum rascunho encontrado em {lecture_dir}")
    return tuple(
        LearningUnitDraft.model_validate_json(path.read_text()) for path in draft_paths
    )


def _load_evidence_packs(
    packs_root: Path,
    lecture_id: str,
) -> tuple[EvidencePack, ...]:
    lecture_dir = packs_root / lecture_id
    pack_paths = sorted(lecture_dir.glob("pack_*.json"))
    if not pack_paths:
        raise FileNotFoundError(f"nenhum evidence pack encontrado em {lecture_dir}")
    return require_lecture_ownership(
        lecture_id,
        (EvidencePack.model_validate_json(path.read_text()) for path in pack_paths),
        kind="evidence packs",
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
            "Gera as revisões críticas dos rascunhos de uma aula via gateway "
            "LLM (Fase 7, rota de estudo pessoal não certificada, ADR 0035)."
        )
    )
    parser.add_argument("lecture_id")
    parser.add_argument("--drafts-root", type=Path, default=DEFAULT_DRAFTS_ROOT)
    parser.add_argument("--packs-root", type=Path, default=DEFAULT_PACKS_ROOT)
    parser.add_argument("--ledgers-root", type=Path, default=DEFAULT_LEDGERS_ROOT)
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
        "--max-retries",
        type=int,
        default=2,
        help=(
            "máximo de tentativas de reparo (redraft via Codex com o "
            "feedback do crítico) antes de aceitar o último resultado, "
            "aprovado ou não -- mesmo padrão do CriticOrchestrator local"
        ),
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())

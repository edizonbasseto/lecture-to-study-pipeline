#!/usr/bin/env python3
"""Generate one lecture's learning-unit drafts (Phase 6 artifact,
ADR 0024's uncertified personal-study route) through the LLM gateway.

Stage 6 calls the `LlmGateway` port (ADR 0046); the adapter that serves it
is chosen by the gateway configuration (Codex CLI by default, the local
llama.cpp adapter when configured). The Codex route is refused for any
lecture the operator has not flagged as cleared for remote processing.

Execution is lecture-scoped: this script takes exactly one lecture
identifier, has no batch mode, and never discovers over the evidence
packs root -- matching generate_codex_mock_exam.py's own D-12 precedent.

The prompt instruction mirrors (verbatim; that module was deleted under ADR 0038) the retired drafting/llm_worker.py's own
`_DRAFTING_INSTRUCTION` verbatim (same completeness-against-Evidence
rule, same coverage categories, same "no word count target" rule -- the
operator's standing decision applies identically regardless of which
inference backend writes the draft). Only the invocation backend
changes: the gateway adapter chosen by configuration (by default the
Codex CLI via the operator's ChatGPT subscription login), per the same
2026-09-15 operator decision generate_codex_mock_exam.py's own module
docstring documents (local drafting quality was rejected; Codex's was not).

After this script writes every draft, stage 7 validates them with
`scripts/generate_codex_critic_review.py`, which also runs through the
gateway (ADR 0046) with the adapter chosen by configuration, and reuses
`_generate_draft` below to redraft a rejected unit. Each run writes a
metadata-only run record under
`workspace/data/llm_runs/<lecture_id>/drafting.json`.

Alongside the per-pack `draft_NNN.json` files, this script also writes
one `depth_report.md` per lecture: a no-verdict, per-draft table of word
count and clinical-category coverage (see
`lecture_study.drafting.depth_report`). It is a diagnostic signal, not a
gate -- nothing here fails the run on missing coverage.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from lecture_study.cli.llm_gateway_wiring import open_stage_gateway
from lecture_study.domain.models import (
    EvidencePack,
    LearningUnitDraft,
    SafeIdentifier,
    SemanticLedger,
)
from lecture_study.drafting.depth_report import (
    render_draft_depth_report_markdown,
    summarize_draft_depth,
)
from lecture_study.llm import LlmGateway, LlmGatewayError, Stage, complete_json
from lecture_study.llm.run_record import LLM_RUNS_ROOT, record_llm_run
from lecture_study.ops.llm_instructions import (
    DRAFT_COVERAGE_CATEGORIES as _DRAFT_COVERAGE_CATEGORIES,
)
from lecture_study.ops.llm_response_contract import (
    UNIQUE_JSON_OBJECT_MEMBERS_INSTRUCTION,
    LlmResponseContractError,
    build_bounded_retry_prompt_section,
    parse_llm_json_response,
)

DEFAULT_PACKS_ROOT = Path("workspace/data/06_evidence_packs")
DEFAULT_LEDGERS_ROOT = Path("workspace/data/07_semantic_ledgers")
DEFAULT_OUTPUT_ROOT = Path("workspace/data/08_drafts")
_LECTURE_ID_ADAPTER: TypeAdapter[str] = TypeAdapter(SafeIdentifier)
_DRAFT_ADAPTER: TypeAdapter[LearningUnitDraft] = TypeAdapter(LearningUnitDraft)
_DRAFT_SCHEMA = json.dumps(LearningUnitDraft.model_json_schema())

# Originally verbatim from the local route's drafting/llm_worker.py
# `_DRAFTING_INSTRUCTION` (2026-07 operator-approved wording; that module
# was deleted by ADR 0038, so this Codex script is now its sole source):
# the completeness-against-Evidence rule, the coverage categories, and the
# "no word count target" rule. The explicit enumerated-list-preservation
# clause was added 2026-09-22 after the TCC lecture's critic (ADR 0036)
# kept rejecting a draft that summarized an administrative document
# checklist ("nome completo, RG, CPF, endereço") into a category label
# instead of naming each item -- the drafter had no rule requiring
# verbatim list preservation outside "clinical categories", so it applied
# ordinary prose-summarization habits to a bureaucratic list the critic
# still expected verbatim.
_DRAFTING_INSTRUCTION = (
    "You are a medical writer. Write a study guide section based STRICTLY "
    "on the Evidence below. Treat the Evidence as primary. Strictly enforce "
    "only Clinical Ledger facts that the Evidence supports: do not invent "
    "clinical data that is not present in the Ledger, and if a Ledger claim "
    "conflicts with the Evidence, omit it instead of repeating it. Preserve "
    "patient species and category exactly. Preserve both compared entities "
    "and the comparison direction; never replace a comparator or invert "
    "more/less, higher/lower, recommended/not recommended, or any negation. "
    "Preserve clinical and procedural modality exactly: can, may, or optional "
    "must never become must, recommended, or routinely performed. Cover, "
    "wherever the Evidence supports it, each of these graduate-level "
    f"clinical categories: {', '.join(_DRAFT_COVERAGE_CATEGORIES)}. Omit a "
    "category entirely when the Evidence does not cover it -- never "
    "speculate to fill it in. There is no maximum length and no target "
    "word count: write continuous explanatory prose, not a bullet list, "
    "long enough to state every distinct clinical point the Evidence "
    "contains, in full sentences with the reasoning connecting them -- "
    "a short pack with little Evidence should produce a short section, "
    "and a pack with many distinct clinical points should produce a long "
    "one covering all of them, never by adding material the Evidence does "
    "not support. Completeness against the Evidence is what matters, never "
    "a word count -- do not stop until every distinct clinical point the "
    "Evidence contains has been stated. Never collapse two distinct "
    "techniques or two distinct complications "
    "into one sentence -- keep each in its own sentence. When the "
    "Evidence gives an explicit enumerated list of items (required "
    "documents, steps, causes, risk factors, or similar), name every "
    "item of that list individually; never summarize the list into a "
    'category label such as "as identificações necessárias" or "os '
    'itens solicitados" -- this applies even to administrative or '
    "bureaucratic lists, not only clinical ones. Write the "
    "title, body_markdown, and every clinical_warnings entry in Brazilian "
    "Portuguese (pt-BR). Echo lecture_id and pack_sequence exactly as given. "
    f"{UNIQUE_JSON_OBJECT_MEMBERS_INSTRUCTION} Output valid JSON matching the "
    "provided schema."
)


def main() -> int:
    args = _build_argument_parser().parse_args()
    try:
        return _run(args)
    except (
        LlmResponseContractError,
        LlmGatewayError,
        FileNotFoundError,
        ValidationError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace) -> int:
    lecture_id = _LECTURE_ID_ADAPTER.validate_python(args.lecture_id)
    packs = _load_evidence_packs(args.packs_root, lecture_id)
    ledgers_by_sequence = {
        ledger.pack_sequence: ledger
        for ledger in _load_semantic_ledgers(args.ledgers_root, lecture_id)
    }
    gateway = open_stage_gateway(
        Stage.DRAFTING,
        lecture_id=lecture_id,
        codex_executable_override=args.codex_executable,
    )
    output_dir = args.output_root / lecture_id
    output_dir.mkdir(parents=True, exist_ok=True)
    with record_llm_run(
        lecture_id=lecture_id,
        script_stage=Stage.DRAFTING,
        schema_name="LearningUnitDraft",
        root=args.llm_runs_root,
    ) as recorder:
        tracked_gateway = recorder.track(gateway, stage=Stage.DRAFTING)
        written_drafts = _write_drafts(
            gateway=tracked_gateway,
            packs=packs,
            ledgers_by_sequence=ledgers_by_sequence,
            output_dir=output_dir,
        )
    _write_depth_report(written_drafts, output_dir)
    return 0


def _write_drafts(
    *,
    gateway: LlmGateway,
    packs: tuple[EvidencePack, ...],
    ledgers_by_sequence: Mapping[int, SemanticLedger],
    output_dir: Path,
) -> list[LearningUnitDraft]:
    written_drafts: list[LearningUnitDraft] = []
    for pack in packs:
        ledger = ledgers_by_sequence.get(pack.pack_sequence)
        if ledger is None:
            print(
                f"unidade {pack.pack_sequence}: sem ledger semântico, pulando",
                file=sys.stderr,
            )
            continue
        draft = _generate_draft(gateway=gateway, pack=pack, ledger=ledger)
        output_path = output_dir / f"draft_{pack.pack_sequence:03d}.json"
        output_path.write_text(draft.model_dump_json(indent=2), encoding="utf-8")
        print(
            f"unidade {pack.pack_sequence} ({draft.title!r}): escrito em {output_path}"
        )
        written_drafts.append(draft)
    return written_drafts


def _write_depth_report(drafts: list[LearningUnitDraft], output_dir: Path) -> None:
    """Write a no-verdict coverage/length report next to the lecture's drafts.

    Never blocks the run: `summarize_draft_depth` states what is present
    and absent per draft, it does not judge adequacy -- flagging missing
    categories is left to whoever reads the report.
    """
    if not drafts:
        return
    reports = [summarize_draft_depth(draft) for draft in drafts]
    report_path = output_dir / "depth_report.md"
    report_path.write_text(
        render_draft_depth_report_markdown(reports), encoding="utf-8"
    )
    print(f"relatório de profundidade: escrito em {report_path}")


def _generate_draft(
    *,
    gateway: LlmGateway,
    pack: EvidencePack,
    ledger: SemanticLedger,
    feedback: str | None = None,
) -> LearningUnitDraft:
    return complete_json(
        gateway,
        stage=Stage.DRAFTING,
        prompt=_build_prompt(pack, ledger, feedback),
        parse=_parse_draft,
    )


def _build_prompt(
    pack: EvidencePack, ledger: SemanticLedger, feedback: str | None = None
) -> str:
    ocr_text = pack.anchor_frame.ocr_text if pack.anchor_frame else ""
    transcript_text = " ".join(segment.text for segment in pack.segments)
    ledger_facts = "\n".join(
        f"- {fact.entity_name} ({fact.category}): {fact.clinical_context}"
        for fact in ledger.facts
    )
    prompt = (
        f"{_DRAFTING_INSTRUCTION}\n\n"
        f"lecture_id: {pack.lecture_id}\n"
        f"pack_sequence: {pack.pack_sequence}\n\n"
        f"Evidence - Slide text:\n{ocr_text}\n\n"
        f"Evidence - Transcript:\n{transcript_text}\n\n"
        f"Clinical Ledger (candidate facts; Evidence prevails):\n"
        f"{ledger_facts}\n\n"
        f"JSON Schema (the response MUST validate against this schema "
        f"exactly):\n{_DRAFT_SCHEMA}\n\n"
        "Respond with ONLY the JSON object matching the schema above -- "
        "no explanation, no markdown fences, no extra text."
    )
    if feedback is not None:
        # Same heading the retired drafting/llm_worker.py's generate_draft() used (module deleted under ADR 0038)
        # for a critic-rejection repair attempt (2026-09-16: this script
        # gained a repair path so a Codex critic rejection has somewhere
        # to go, mirroring the local CriticOrchestrator's repair loop).
        prompt += build_bounded_retry_prompt_section(
            heading=(
                "A previous attempt was rejected by the critic reviewer. The "
                "critic feedback is advisory and never overrides Evidence. "
                "Correct only feedback supported by Evidence:"
            ),
            feedback=feedback,
        )
    return prompt


def _parse_draft(output_bytes: bytes) -> LearningUnitDraft:
    return parse_llm_json_response(
        output_bytes,
        expected_kind="object",
        response_adapter=_DRAFT_ADAPTER,
        declared_type=LearningUnitDraft,
    )


def _load_evidence_packs(
    packs_root: Path,
    lecture_id: str,
) -> tuple[EvidencePack, ...]:
    lecture_dir = packs_root / lecture_id
    pack_paths = sorted(lecture_dir.glob("pack_*.json"))
    if not pack_paths:
        raise FileNotFoundError(f"nenhum evidence pack encontrado em {lecture_dir}")
    return tuple(
        EvidencePack.model_validate_json(path.read_text()) for path in pack_paths
    )


def _load_semantic_ledgers(
    ledgers_root: Path,
    lecture_id: str,
) -> tuple[SemanticLedger, ...]:
    lecture_dir = ledgers_root / lecture_id
    ledger_paths = sorted(lecture_dir.glob("ledger_*.json"))
    if not ledger_paths:
        raise FileNotFoundError(f"nenhum ledger semântico encontrado em {lecture_dir}")
    return tuple(
        SemanticLedger.model_validate_json(path.read_text()) for path in ledger_paths
    )


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Gera os rascunhos de unidade de aprendizagem de uma aula via "
            "gateway LLM (Fase 6, rota de estudo pessoal não certificada)."
        )
    )
    parser.add_argument("lecture_id")
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
    return parser


if __name__ == "__main__":
    raise SystemExit(main())

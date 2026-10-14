#!/usr/bin/env python3
"""Generate one lecture's Semantic Ledgers (Phase 5 artifact, ADR 0024's
uncertified personal-study route) through the LLM gateway.

Stage 5 calls the `LlmGateway` port (ADR 0046); the adapter that serves it
is chosen by the gateway configuration (Codex CLI by default, the local
llama.cpp adapter when configured). The Codex route is refused for any
lecture the operator has not flagged as cleared for remote processing.

Per ADR 0035: the original local route produced a reproducible,
content-triggered defect on 10006's pack 12 (a Chinese-language JSON key
substituted for `clinical_context`, plus a JavaScript-style `//` comment
appended mid-object) that the strict JSON contract parser correctly rejects
and that the retry attempt reproduced byte-for-byte across three separate
full-lecture reruns. Codex, billed through the operator's ChatGPT Pro
subscription rather than the OpenAI API, replaced the local route for this
phase -- same reasoning ADR 0030 already applied to drafting.

Execution is lecture-scoped, matching generate_codex_mock_exam.py's and
generate_codex_drafts.py's own precedent: this script takes exactly one
lecture identifier and has no batch mode.

The extraction instruction mirrors (verbatim; that module was deleted under ADR 0038) the retired semantic/llm_worker.py's own
`_EXTRACTION_INSTRUCTION` verbatim (same fact-count target/cap, same
species/negation/comparison preservation rules, same pt-BR requirement)
-- only the invocation backend changes. Each run writes a metadata-only
run record under `workspace/data/llm_runs/<lecture_id>/semantic.json`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from lecture_study.cli.llm_gateway_wiring import open_stage_gateway
from lecture_study.domain.models import EvidencePack, SafeIdentifier, SemanticLedger
from lecture_study.llm import LlmGateway, LlmGatewayError, Stage, complete_json
from lecture_study.llm.run_record import LLM_RUNS_ROOT, record_llm_run
from lecture_study.ops.llm_instructions import (
    EXTRACTION_INSTRUCTION as _EXTRACTION_INSTRUCTION,
)
from lecture_study.ops.llm_response_contract import (
    LlmResponseContractError,
    parse_llm_json_response,
)
from lecture_study.ops.semantic_ledger_support import (
    LlmParsingError,
    render_timestamped_transcript as _render_timestamped_transcript,
    validate_traceability as _validate_traceability,
)

DEFAULT_PACKS_ROOT = Path("workspace/data/06_evidence_packs")
DEFAULT_OUTPUT_ROOT = Path("workspace/data/07_semantic_ledgers")
_LECTURE_ID_ADAPTER: TypeAdapter[str] = TypeAdapter(SafeIdentifier)
_LEDGER_ADAPTER: TypeAdapter[SemanticLedger] = TypeAdapter(SemanticLedger)
_LEDGER_SCHEMA = json.dumps(SemanticLedger.model_json_schema())


def main() -> int:
    args = _build_argument_parser().parse_args()
    try:
        return _run(args)
    except (
        LlmResponseContractError,
        LlmParsingError,
        LlmGatewayError,
        FileNotFoundError,
        ValidationError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace) -> int:
    lecture_id = _LECTURE_ID_ADAPTER.validate_python(args.lecture_id)
    packs = _load_evidence_packs(args.packs_root, lecture_id)
    gateway = open_stage_gateway(
        Stage.SEMANTIC,
        lecture_id=lecture_id,
        codex_executable_override=args.codex_executable,
    )
    output_dir = args.output_root / lecture_id
    output_dir.mkdir(parents=True, exist_ok=True)
    with record_llm_run(
        lecture_id=lecture_id,
        script_stage=Stage.SEMANTIC,
        schema_name="SemanticLedger",
        root=args.llm_runs_root,
        scope=_run_scope(args.pack_sequence),
    ) as recorder:
        tracked_gateway = recorder.track(gateway, stage=Stage.SEMANTIC)
        for pack in _selected_packs(packs, args.pack_sequence):
            ledger = _generate_ledger(gateway=tracked_gateway, pack=pack)
            output_path = output_dir / f"ledger_{pack.pack_sequence:03d}.json"
            output_path.write_text(ledger.model_dump_json(indent=2), encoding="utf-8")
            print(
                f"pack {pack.pack_sequence}: {len(ledger.facts)} fato(s), "
                f"escrito em {output_path}"
            )
    return 0


def _run_scope(only_pack_sequence: int | None) -> str | None:
    if only_pack_sequence is None:
        return None
    return f"pack_sequence={only_pack_sequence}"


def _selected_packs(
    packs: tuple[EvidencePack, ...], only_pack_sequence: int | None
) -> tuple[EvidencePack, ...]:
    if only_pack_sequence is None:
        return packs
    return tuple(pack for pack in packs if pack.pack_sequence == only_pack_sequence)


def _generate_ledger(*, gateway: LlmGateway, pack: EvidencePack) -> SemanticLedger:
    return complete_json(
        gateway,
        stage=Stage.SEMANTIC,
        prompt=_build_prompt(pack),
        parse=_parse_ledger,
        validate=lambda ledger: _validate_traceability(ledger, pack),
        retry_on=(LlmResponseContractError, LlmParsingError),
    )


def _build_prompt(pack: EvidencePack) -> str:
    ocr_text = pack.anchor_frame.ocr_text if pack.anchor_frame else ""
    transcript_text = _render_timestamped_transcript(pack)
    return (
        f"{_EXTRACTION_INSTRUCTION}\n\n"
        f"lecture_id: {pack.lecture_id}\n"
        f"pack_sequence: {pack.pack_sequence}\n\n"
        f"Slide text:\n{ocr_text}\n\n"
        f"Transcript:\n{transcript_text}\n\n"
        f"JSON Schema (the response MUST validate against this schema "
        f"exactly):\n{_LEDGER_SCHEMA}\n\n"
        "Respond with ONLY the JSON object matching the schema above -- "
        "no explanation, no markdown fences, no extra text, no comments."
    )


def _parse_ledger(output_bytes: bytes) -> SemanticLedger:
    return parse_llm_json_response(
        output_bytes,
        expected_kind="object",
        response_adapter=_LEDGER_ADAPTER,
        declared_type=SemanticLedger,
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


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Gera os ledgers semânticos de uma aula via gateway LLM (Fase 5, "
            "rota de estudo pessoal não certificada, ADR 0035)."
        )
    )
    parser.add_argument("lecture_id")
    parser.add_argument(
        "--pack-sequence",
        type=int,
        default=None,
        help="regenera apenas este pack_sequence, pulando os demais",
    )
    parser.add_argument("--packs-root", type=Path, default=DEFAULT_PACKS_ROOT)
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

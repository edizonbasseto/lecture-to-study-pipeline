#!/usr/bin/env python3
"""Generate one lecture's Semantic Ledgers (Phase 5 artifact, ADR 0024's
uncertified personal-study route) with the Codex CLI (`codex exec`)
instead of the local qwen3-32b/llama.cpp semantic-extraction worker.

Per ADR 0035: the local route produced a reproducible, content-triggered
defect on 10006's pack 12 (a Chinese-language JSON key substituted for
`clinical_context`, plus a JavaScript-style `//` comment appended
mid-object) that the strict JSON contract parser correctly rejects and
that the retry attempt reproduced byte-for-byte across three separate
full-lecture reruns. Codex, billed through the operator's ChatGPT Pro
subscription rather than the OpenAI API, replaces the local route for
this phase -- same reasoning ADR 0030 already applied to drafting.

Execution is lecture-scoped, matching generate_codex_mock_exam.py's and
generate_codex_drafts.py's own precedent: this script takes exactly one
lecture identifier and has no batch mode.

The extraction instruction mirrors (verbatim; that module was deleted under ADR 0038) the retired semantic/llm_worker.py's own
`_EXTRACTION_INSTRUCTION` verbatim (same fact-count target/cap, same
species/negation/comparison preservation rules, same pt-BR requirement)
-- only the invocation backend changes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from pydantic import TypeAdapter

from lecture_study.domain.models import EvidencePack, SemanticLedger
from lecture_study.ops.llm_instructions import (
    EXTRACTION_INSTRUCTION as _EXTRACTION_INSTRUCTION,
)
from lecture_study.ops.semantic_ledger_support import (
    LlmParsingError,
    render_timestamped_transcript as _render_timestamped_transcript,
    validate_traceability as _validate_traceability,
)
from lecture_study.ops.llm_response_contract import (
    LlmResponseContractError,
    build_bounded_retry_prompt_section,
    build_llm_retry_feedback,
    parse_llm_json_response,
)
from lecture_study.ops.process_environment import (
    resolve_trusted_executable,
    sanitized_child_process_environment,
)
from lecture_study.ops.process_lifecycle import run_bounded_process

DEFAULT_PACKS_ROOT = Path("workspace/data/06_evidence_packs")
DEFAULT_OUTPUT_ROOT = Path("workspace/data/07_semantic_ledgers")
_SUBPROCESS_TIMEOUT_SECONDS = 900.0
_MAXIMUM_PROCESS_OUTPUT_BYTES = 16 * 1024 * 1024
_LEDGER_ADAPTER: TypeAdapter[SemanticLedger] = TypeAdapter(SemanticLedger)
_LEDGER_SCHEMA = json.dumps(SemanticLedger.model_json_schema())


def main() -> int:
    args = _build_argument_parser().parse_args()
    try:
        return _run(args)
    except (LlmResponseContractError, FileNotFoundError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace) -> int:
    codex_executable = resolve_trusted_executable(args.codex_executable)
    environment = dict(sanitized_child_process_environment())
    # HOME is not on sanitized_child_process_environment()'s inherited
    # whitelist, but codex needs it to locate its ChatGPT-subscription auth
    # session under ~/.codex -- same restoration generate_codex_drafts.py
    # already does, scoped to this one call.
    home_directory = os.environ.get("HOME")
    if home_directory is not None:
        environment["HOME"] = home_directory

    packs = _load_evidence_packs(args.packs_root, args.lecture_id)
    only_pack_sequence = args.pack_sequence

    output_dir = args.output_root / args.lecture_id
    output_dir.mkdir(parents=True, exist_ok=True)

    for pack in packs:
        if only_pack_sequence is not None and pack.pack_sequence != only_pack_sequence:
            continue
        ledger = _generate_ledger(
            codex_executable=codex_executable,
            environment=environment,
            pack=pack,
        )
        output_path = output_dir / f"ledger_{pack.pack_sequence:03d}.json"
        output_path.write_text(ledger.model_dump_json(indent=2), encoding="utf-8")
        print(
            f"pack {pack.pack_sequence}: {len(ledger.facts)} fato(s), "
            f"escrito em {output_path}"
        )
    return 0


def _generate_ledger(
    *,
    codex_executable: str,
    environment: dict[str, str],
    pack: EvidencePack,
) -> SemanticLedger:
    prompt = _build_prompt(pack)
    try:
        ledger = _call_codex_for_ledger(
            codex_executable=codex_executable,
            environment=environment,
            prompt=prompt,
        )
        _validate_traceability(ledger, pack)
        return ledger
    except (LlmResponseContractError, LlmParsingError) as error:
        retry_prompt = prompt + build_bounded_retry_prompt_section(
            heading="Retry feedback:",
            feedback=build_llm_retry_feedback(error),
        )
        ledger = _call_codex_for_ledger(
            codex_executable=codex_executable,
            environment=environment,
            prompt=retry_prompt,
        )
        _validate_traceability(ledger, pack)
        return ledger


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


def _call_codex_for_ledger(
    *,
    codex_executable: str,
    environment: dict[str, str],
    prompt: str,
) -> SemanticLedger:
    # Scratch cwd, never the repository checkout -- same least-privilege
    # rationale as generate_codex_drafts.py's own _call_codex_for_draft:
    # the prompt is self-contained, and the repository root holds `.env`.
    with tempfile.TemporaryDirectory(prefix="codex-semantic-") as raw_scratch_dir:
        scratch_dir = Path(raw_scratch_dir)
        output_path = scratch_dir / "response.txt"
        run_bounded_process(
            [
                codex_executable,
                "exec",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "-C",
                str(scratch_dir),
                "-o",
                str(output_path),
                prompt,
            ],
            env=environment,
            cwd=scratch_dir,
            timeout_seconds=_SUBPROCESS_TIMEOUT_SECONDS,
            maximum_stdout_bytes=_MAXIMUM_PROCESS_OUTPUT_BYTES,
            maximum_stderr_bytes=_MAXIMUM_PROCESS_OUTPUT_BYTES,
            check=True,
        )
        output_bytes = output_path.read_bytes()
    return _parse_ledger(output_bytes)


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
            "Gera os ledgers semânticos de uma aula via Codex CLI (Fase 5, "
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
    parser.add_argument("--codex-executable", default="codex")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())

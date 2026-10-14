#!/usr/bin/env python3
"""Generate one lecture's critic reviews (Phase 7 artifact, ADR 0024's
uncertified personal-study route) with the Codex CLI (`codex exec`)
instead of the local qwen3-32b/llama.cpp critic worker.

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
import os
import sys
import tempfile
from pathlib import Path

import generate_codex_drafts
from pydantic import TypeAdapter

from lecture_study.ops.llm_instructions import CRITIC_INSTRUCTION as _CRITIC_INSTRUCTION
from lecture_study.domain.models import (
    CriticReview,
    EvidencePack,
    LearningUnitDraft,
    SemanticLedger,
    ValidatedLearningUnit,
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

DEFAULT_DRAFTS_ROOT = Path("workspace/data/08_drafts")
DEFAULT_PACKS_ROOT = Path("workspace/data/06_evidence_packs")
DEFAULT_LEDGERS_ROOT = Path("workspace/data/07_semantic_ledgers")
DEFAULT_OUTPUT_ROOT = Path("workspace/data/09_validated_units")
_SUBPROCESS_TIMEOUT_SECONDS = 900.0
_MAXIMUM_PROCESS_OUTPUT_BYTES = 16 * 1024 * 1024
_REVIEW_ADAPTER: TypeAdapter[CriticReview] = TypeAdapter(CriticReview)
_REVIEW_SCHEMA = json.dumps(CriticReview.model_json_schema())


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
    home_directory = os.environ.get("HOME")
    if home_directory is not None:
        environment["HOME"] = home_directory

    drafts = _load_drafts(args.drafts_root, args.lecture_id)
    packs_by_sequence = {
        pack.pack_sequence: pack
        for pack in _load_evidence_packs(args.packs_root, args.lecture_id)
    }
    ledgers_by_sequence = {
        ledger.pack_sequence: ledger
        for ledger in _load_semantic_ledgers(args.ledgers_root, args.lecture_id)
    }

    output_dir = args.output_root / args.lecture_id
    output_dir.mkdir(parents=True, exist_ok=True)

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
            codex_executable=codex_executable,
            environment=environment,
            draft=draft,
            pack=pack,
            ledger=ledger,
            max_retries=args.max_retries,
        )
        # Anchored to the trusted `pack`, not `final_draft` -- matches
        # CriticOrchestrator._process_draft's own identity guard
        # (critic/pipeline.py). A repair attempt's redraft is untrusted
        # model output: its own echoed lecture_id/pack_sequence could
        # drift (the exact model-echo defect class ADR 0035 exists to
        # guard against) without this file's own identity silently
        # corrupting to match it.
        unit = ValidatedLearningUnit(
            lecture_id=pack.lecture_id,
            pack_sequence=pack.pack_sequence,
            content=final_draft,
            review=review,
        )
        output_path = output_dir / f"unit_{draft.pack_sequence:03d}.json"
        output_path.write_text(unit.model_dump_json(indent=2), encoding="utf-8")
        verdict = "aprovado" if review.passed else "REPROVADO"
        print(
            f"unidade {draft.pack_sequence}: {verdict} "
            f"({attempts} tentativa(s) de reparo), escrito em {output_path}"
        )
    return 0


def _evaluate_with_repair(
    *,
    codex_executable: str,
    environment: dict[str, str],
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
        codex_executable=codex_executable,
        environment=environment,
        draft=current_draft,
        pack=pack,
        ledger=ledger,
    )
    attempts = 0
    while not review.passed and attempts < max_retries:
        current_draft = generate_codex_drafts._generate_draft(
            codex_executable=codex_executable,
            environment=environment,
            pack=pack,
            ledger=ledger,
            feedback=review.feedback,
        )
        review = _generate_review(
            codex_executable=codex_executable,
            environment=environment,
            draft=current_draft,
            pack=pack,
            ledger=ledger,
        )
        attempts += 1
    return current_draft, review, attempts


def _generate_review(
    *,
    codex_executable: str,
    environment: dict[str, str],
    draft: LearningUnitDraft,
    pack: EvidencePack,
    ledger: SemanticLedger,
) -> CriticReview:
    prompt = _build_prompt(draft, pack, ledger)
    try:
        return _call_codex_for_review(
            codex_executable=codex_executable,
            environment=environment,
            prompt=prompt,
        )
    except LlmResponseContractError as error:
        retry_prompt = prompt + build_bounded_retry_prompt_section(
            heading="Retry feedback:",
            feedback=build_llm_retry_feedback(error),
        )
        return _call_codex_for_review(
            codex_executable=codex_executable,
            environment=environment,
            prompt=retry_prompt,
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


def _call_codex_for_review(
    *,
    codex_executable: str,
    environment: dict[str, str],
    prompt: str,
) -> CriticReview:
    # Scratch cwd, never the repository checkout -- same least-privilege
    # rationale as the sibling Codex scripts.
    with tempfile.TemporaryDirectory(prefix="codex-critic-") as raw_scratch_dir:
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
    return _parse_review(output_bytes)


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
            "Gera as revisões críticas dos rascunhos de uma aula via Codex "
            "CLI (Fase 7, rota de estudo pessoal não certificada, ADR 0035)."
        )
    )
    parser.add_argument("lecture_id")
    parser.add_argument("--drafts-root", type=Path, default=DEFAULT_DRAFTS_ROOT)
    parser.add_argument("--packs-root", type=Path, default=DEFAULT_PACKS_ROOT)
    parser.add_argument("--ledgers-root", type=Path, default=DEFAULT_LEDGERS_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--codex-executable", default="codex")
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

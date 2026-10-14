#!/usr/bin/env python3
"""Generate one lecture's personal-study podcast script (ADR 0024's
uncertified personal-study route) with the Codex CLI (`codex exec`),
mirroring `scripts/generate_codex_mock_exam.py`/`generate_codex_personal_
study_flashcards.py` in family and invocation shape, but unlike those two
this is a single whole-lecture generation, not a per-unit loop: a podcast
dialogue needs a coherent Host/Expert conversation across the whole
lecture, not independent per-unit calls, matching
the retired `podcast/generator.py`'s local-LLM route (ADR 0014; module deleted under ADR 0038), which this
replaces for the Codex migration.

Execution is lecture-scoped: this script takes exactly one lecture
identifier, has no batch mode.

Grounding input is the critic-approved, post-repair content of every
unit in `workspace/data/09_validated_units` (Phase 7
ValidatedLearningUnits), loaded through the assembler's
`load_critic_approved_drafts` gate (ADR 0045, superseding ADR 0037
decision 2, which grounded this route on `workspace/data/08_drafts`):
derivative study material is only ever built from units the critic
approved, and a single failing unit, a gap in the pack sequence, or a
Phase 6 draft the critic never reviewed blocks the whole lecture before
Codex is invoked. Phase 8's `workspace/data/10_canonical_documents` is
still not read: the Codex route bypasses Phase 8's document write, but
applies the same completeness gate. Each unit is tagged with the same
`unit-{pack_sequence:03d}` synthetic id the retired `podcast/generator.py`
used (`_UNIT_ID_TEMPLATE`), so the LLM can cite a specific unit in
`source_unit_id` exactly as the local route's instruction already asks
for.

Output is written through the existing, execution-mode-aware
`PodcastScriptCompiler` (`podcast/compiler.py`) rather than a bespoke
write here: it already special-cases `UNCERTIFIED_PERSONAL_STUDY` to skip
derivative-certificate verification, already writes both `script.json`
and the readable `script_legivel.md` transcript, and already writes both
atomically via `write_delivery_outputs`/`RootedArtifactStore` -- reusing
it avoids duplicating that logic (R0.2) and gets the readable transcript
for free, which `generate_codex_mock_exam.py`/`generate_codex_personal_
study_flashcards.py` have no equivalent of.

RESOLVED: the output-collision-with-the-local-route note that lived here
is obsolete -- the local `podcast/generator.py` was deleted under ADR
0038 and this script is the only writer of
`workspace/data/15_podcast_scripts/{lecture_id}/`.

KNOWN, ACCEPTED GAP (not yet closed): unlike the local route's
per-attempt context/output-token escalation
(`_QWEN2_INFERENCE_WINDOWS`/`_QWEN3_INFERENCE_WINDOWS`,
`podcast/generator.py`), this script's single whole-lecture prompt has no
size check and its retry resends the same size evidence pack unchanged
-- a lecture dense enough to truncate Codex's JSON response will likely
fail identically on the retry. Not yet observed against a real lecture;
revisit (chunking or a lecture-density guard) if it does. See
`docs/decisions/0037-codex-podcast-script-route.md` for this and the two
sourcing/shape decisions this script makes relative to ADR 0014.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from lecture_study.assembly.assembler import load_critic_approved_drafts
from lecture_study.domain.errors import (
    LectureNotFoundError,
    MissingSequenceError,
    UnresolvedReviewError,
)
from lecture_study.domain.models import (
    ArtifactExecutionMode,
    LearningUnitDraft,
    PodcastScript,
    SafeIdentifier,
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
from lecture_study.ops.process_lifecycle import (
    ProcessOutputLimitExceeded,
    run_bounded_process,
)
from lecture_study.podcast.compiler import PodcastScriptCompiler
from lecture_study.quality.student_facing_text import (
    describe_student_facing_text_defects,
    student_facing_text_defects,
)

DEFAULT_DRAFTS_ROOT = Path("workspace/data/08_drafts")
DEFAULT_VALIDATED_UNITS_ROOT = Path("workspace/data/09_validated_units")
_SUBPROCESS_TIMEOUT_SECONDS = 900.0
_MAXIMUM_PROCESS_OUTPUT_BYTES = 16 * 1024 * 1024
_UNIT_ID_TEMPLATE = "unit-{:03d}"
_SCRIPT_ADAPTER: TypeAdapter[PodcastScript] = TypeAdapter(PodcastScript)
_SCRIPT_SCHEMA = json.dumps(_SCRIPT_ADAPTER.json_schema())
_LECTURE_ID_ADAPTER: TypeAdapter[str] = TypeAdapter(SafeIdentifier)

# Mirrors the retired podcast/generator.py's _PODCAST_INSTRUCTION (same
# grounding rule, same pt-BR/no-referring-phrase student-facing-text
# rules, same source_unit_id traceability contract); the schema
# framing (object, embedded as text here too, matching the mock-exam
# script's own reason for not using --output-schema), the retry
# section, and the bare-id sentence below (bracket-echo fix,
# 2026-09-16) are Codex-route-specific -- the routes are no longer
# word-for-word identical, do not sync them blindly.
_PODCAST_INSTRUCTION = (
    'You are producing a two-speaker "Deep Dive" educational podcast on '
    "veterinary medicine for the lecture {lecture_id}. Host asks "
    "questions grounded in the clinical cases below; Expert answers them, "
    "explaining the veterinary clinical reasoning. Every claim Expert "
    "makes, and every case Host references, must be anchored EXCLUSIVELY "
    "in the lecture material below: NEVER use outside clinical knowledge. "
    'Each lecture unit is labeled with an id like "[unit-001]"; every '
    "dialogue line -- Host or Expert -- must set source_unit_id to that "
    "unit's bare id with no surrounding brackets (for a unit labeled "
    '"[unit-001]", source_unit_id is exactly "unit-001"). Set lecture_id '
    "to exactly "
    '"{lecture_id}", and write a short episode title. '
    "Write the episode title and every spoken line entirely in Brazilian "
    "Portuguese (pt-BR), with no English words and no sentence mixing two "
    "languages. "
    "Write for a listener who never sees this instruction: a spoken line "
    "states its clinical facts directly and never says a unit id, nor "
    "refers to the lecture material, the source text, the transcript, the "
    "slides, this instruction or any internal name for them. A referring "
    'phrase such as "segundo o material", "conforme o texto" or "de '
    'acordo com a aula" makes the line unusable: state the clinical fact '
    "itself instead. "
    "Output valid JSON matching the provided schema: a single JSON "
    "object, nothing else.\n\n"
    "Lecture material:\n{evidence_pack}\n\n"
    "JSON Schema (the response MUST validate against this schema "
    "exactly):\n{schema}\n\n"
    "Respond with ONLY the JSON object matching the schema above -- no "
    "prose, no markdown fences."
)


def main() -> int:
    args = _build_argument_parser().parse_args()
    try:
        return _run(args)
    except (
        LectureNotFoundError,
        MissingSequenceError,
        UnresolvedReviewError,
    ) as error:
        print(
            "error: aula bloqueada: o material de estudo só é gerado a partir "
            f"de unidades aprovadas pelo crítico (ADR 0045): {error}",
            file=sys.stderr,
        )
        return 1
    except (LlmResponseContractError, FileNotFoundError, ValidationError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace) -> int:
    lecture_id = str(_LECTURE_ID_ADAPTER.validate_python(args.lecture_id))
    # The critic-approval gate runs before anything Codex-related, so a
    # blocked lecture never resolves, let alone invokes, the codex CLI.
    drafts = load_critic_approved_drafts(
        args.validated_units_root, args.drafts_root, lecture_id
    )
    codex_executable = resolve_trusted_executable(args.codex_executable)
    environment = dict(sanitized_child_process_environment())
    # HOME is not on sanitized_child_process_environment()'s inherited
    # whitelist, but the codex CLI needs it to locate its
    # ChatGPT-subscription auth session under ~/.codex -- restoring it
    # here (a plain path, not a credential itself) is scoped to this one
    # call, matching generate_codex_mock_exam.py's own rationale verbatim.
    home_directory = os.environ.get("HOME")
    if home_directory is not None:
        environment["HOME"] = home_directory

    unit_ids = tuple(_unit_id(draft.pack_sequence) for draft in drafts)
    evidence_pack = _build_evidence_pack(drafts, unit_ids)

    script = _generate_script(
        codex_executable=codex_executable,
        environment=environment,
        lecture_id=lecture_id,
        evidence_pack=evidence_pack,
        unit_ids=unit_ids,
    )
    script = script.model_copy(
        update={"execution_mode": ArtifactExecutionMode.UNCERTIFIED_PERSONAL_STUDY}
    )
    output_dir = PodcastScriptCompiler().compile(script)
    print(f"gerado(s) {len(script.dialogue)} linha(s) para a aula {lecture_id!r}")
    print(f"escrito em {output_dir}")
    return 0


def _unit_id(pack_sequence: int) -> str:
    return _UNIT_ID_TEMPLATE.format(pack_sequence)


def _build_evidence_pack(
    drafts: Sequence[LearningUnitDraft], unit_ids: Sequence[str]
) -> str:
    return "\n\n".join(
        f"[{unit_id}] {draft.body_markdown}"
        for unit_id, draft in zip(unit_ids, drafts, strict=True)
    )


def _generate_script(
    *,
    codex_executable: str,
    environment: dict[str, str],
    lecture_id: str,
    evidence_pack: str,
    unit_ids: Sequence[str],
) -> PodcastScript:
    prompt = _PODCAST_INSTRUCTION.format(
        lecture_id=lecture_id,
        evidence_pack=evidence_pack,
        schema=_SCRIPT_SCHEMA,
    )
    try:
        return _call_codex_for_script(
            codex_executable=codex_executable,
            environment=environment,
            prompt=prompt,
            lecture_id=lecture_id,
            unit_ids=unit_ids,
        )
    except LlmResponseContractError as error:
        retry_prompt = prompt + build_bounded_retry_prompt_section(
            heading="Retry feedback:",
            feedback=build_llm_retry_feedback(error),
        )
        return _call_codex_for_script(
            codex_executable=codex_executable,
            environment=environment,
            prompt=retry_prompt,
            lecture_id=lecture_id,
            unit_ids=unit_ids,
        )


def _call_codex_for_script(
    *,
    codex_executable: str,
    environment: dict[str, str],
    prompt: str,
    lecture_id: str,
    unit_ids: Sequence[str],
) -> PodcastScript:
    # Scratch cwd, never the repository checkout -- same least-privilege
    # rationale as generate_codex_mock_exam.py's own
    # _call_codex_for_questions.
    with tempfile.TemporaryDirectory(prefix="codex-podcast-") as raw_scratch_dir:
        scratch_dir = Path(raw_scratch_dir)
        output_path = scratch_dir / "response.txt"
        try:
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
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            ProcessOutputLimitExceeded,
        ) as error:
            # Never interpolate `error` itself: CalledProcessError/
            # TimeoutExpired's default __str__ renders the full `cmd`
            # list, which includes the whole-lecture prompt (unbounded,
            # since this route has no evidence-pack size cap -- see the
            # module docstring's known gap) -- matching
            # llm_response_contract.py's own llm_output_diagnostic
            # convention of never echoing model-adjacent bytes verbatim
            # into a diagnostic message.
            raise LlmResponseContractError(
                f"codex exec failed: {_describe_subprocess_failure(error)}",
                retry_category="contract",
            ) from error
        output_bytes = output_path.read_bytes()
    script = _parse_script(output_bytes)
    _normalize_echoed_unit_ids(script)
    _validate_traceability(script, lecture_id, unit_ids)
    _reject_text_unfit_for_a_listener(script)
    return script


def _describe_subprocess_failure(
    error: subprocess.CalledProcessError
    | subprocess.TimeoutExpired
    | ProcessOutputLimitExceeded,
) -> str:
    """Diagnostic text for a subprocess failure that never echoes `cmd`
    (which embeds the full, unbounded prompt for this route)."""
    if isinstance(error, subprocess.CalledProcessError):
        return f"non-zero exit code {error.returncode}"
    if isinstance(error, subprocess.TimeoutExpired):
        return f"timed out after {error.timeout}s"
    return str(error)


def _reject_text_unfit_for_a_listener(script: PodcastScript) -> None:
    """Fail the run when the episode title or a spoken line is not
    written in pt-BR or names an internal pipeline abstraction.

    Mirrors the retired podcast/generator.py's _reject_script_unfit_for_a_listener
    (ADR 0027): a prompt instruction alone is not sufficient enforcement
    -- ADR 0027's own evidence is that a prompt-only fix still produced
    referring-phrase violations, which is why every other student-facing
    Codex/local generator in this codebase runs the parsed output back
    through this same guard before accepting it.
    """
    passages = [("episode title", script.title)]
    passages.extend(
        (f"line {line_number} ({line.speaker})", line.text)
        for line_number, line in enumerate(script.dialogue, start=1)
    )
    defects = student_facing_text_defects(passages)
    if defects:
        raise LlmResponseContractError(
            "podcast script text is unfit for a listener: "
            f"{describe_student_facing_text_defects(defects)}",
            retry_category="contract",
        )


def _parse_script(output_bytes: bytes) -> PodcastScript:
    return parse_llm_json_response(
        _strip_execution_mode_echo(output_bytes),
        expected_kind="object",
        response_adapter=_SCRIPT_ADAPTER,
        declared_type=PodcastScript,
    )


def _strip_execution_mode_echo(output_bytes: bytes) -> bytes:
    """Drop an echoed `execution_mode` member before validation. The field
    is pipeline-controlled (the compiler stamps the personal-study marker
    on the payload it receives), never something the model may set, and
    the JSON Schema embedded in the prompt exposes the enum's existence
    -- so a run can fail validation on it alone (real failure, lecture
    10006: 'execution_mode: is_instance_of'). Duplicate members of any
    OTHER key still raise, preserving the contract layer's duplicate-key
    rejection (which a plain loads/dumps round-trip would silently
    collapse); anything unparseable, duplicate-bearing, or echo-free
    passes through as original bytes so the contract layer produces its
    own diagnostic against the model's actual output."""
    try:
        decoded = json.loads(
            output_bytes,
            object_pairs_hook=_reject_duplicate_members,
        )
    except ValueError:
        return output_bytes
    if isinstance(decoded, dict) and "execution_mode" in decoded:
        del decoded["execution_mode"]
        return json.dumps(decoded).encode()
    return output_bytes


def _reject_duplicate_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """object_pairs_hook preserving the contract layer's semantics: a
    duplicate member of ANY key (execution_mode included) is a hard
    defect, not a last-wins collapse."""
    members: dict[str, object] = {}
    for key, value in pairs:
        if key in members:
            raise ValueError(f"duplicate JSON key: {key}")
        members[key] = value
    return members


def _normalize_echoed_unit_ids(script: PodcastScript) -> None:
    """Strip the evidence pack's surrounding brackets when Codex echoes a
    unit id verbatim from its "[unit-NNN] {content}" label. Grounding is
    still enforced afterwards: _validate_traceability rejects any id that
    is not a real unit of this lecture, so this only corrects the label
    decoration, never the citation itself."""
    for line in script.dialogue:
        line.source_unit_id = line.source_unit_id.strip("[]")


def _validate_traceability(
    script: PodcastScript, lecture_id: str, unit_ids: Sequence[str]
) -> None:
    # Matches the retired podcast/generator.py's _validate_traceability: same two
    # checks (lecture_id echo, source_unit_id membership), same
    # collect-every-offender-before-raising shape so a repair retry hears
    # about every failing line in one message, not just the first.
    if script.lecture_id != lecture_id:
        raise LlmResponseContractError(
            "Codex echoed a lecture_id that does not match the source "
            f"drafts: got {script.lecture_id!r}, expected {lecture_id!r}",
            retry_category="contract",
        )
    valid_unit_ids = set(unit_ids)
    invalid_lines = [
        (index, line.source_unit_id)
        for index, line in enumerate(script.dialogue)
        if line.source_unit_id not in valid_unit_ids
    ]
    if invalid_lines:
        offenders = ", ".join(
            f"line {index} cites {source_unit_id!r}"
            for index, source_unit_id in invalid_lines
        )
        raise LlmResponseContractError(
            "dialogue cites source_unit_id(s) that are not among this "
            f"lecture's unit ids {sorted(valid_unit_ids)}: {offenders}",
            retry_category="contract",
        )


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Gera o roteiro de podcast de estudo pessoal de uma aula via "
            "Codex CLI, com fundamentação por unidade de aprendizagem."
        )
    )
    parser.add_argument("lecture_id")
    parser.add_argument(
        "--validated-units-root",
        type=Path,
        default=DEFAULT_VALIDATED_UNITS_ROOT,
        help=(
            "Raiz das unidades validadas pelo crítico (Fase 7); fonte do "
            "conteúdo do podcast."
        ),
    )
    parser.add_argument(
        "--drafts-root",
        type=Path,
        default=DEFAULT_DRAFTS_ROOT,
        help=(
            "Raiz dos rascunhos (Fase 6); usada apenas para conferir que "
            "cada um foi revisado pelo crítico na versão atual."
        ),
    )
    parser.add_argument("--codex-executable", default="codex")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())

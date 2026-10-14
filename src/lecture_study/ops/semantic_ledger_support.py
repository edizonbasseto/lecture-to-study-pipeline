"""Backend-agnostic Semantic Ledger helpers shared by the local llama.cpp
extraction worker and its Codex-CLI replacement (ADR 0035).

Relocated out of `semantic/llm_worker.py` so `generate_codex_semantic_ledgers.py`
stops reaching into that module's private (`_`-prefixed) symbols across a
package boundary with no shared contract -- the local llama.cpp worker
module is being retired (qwen3-32b/llama.cpp removal) and this is the
functional logic (not the invocation backend) both routes need to keep.

Logic is verbatim, unchanged from the local worker's own implementation;
this is a pure relocation.
"""

from __future__ import annotations

import math

from lecture_study.domain.models import EvidencePack, SemanticLedger
from lecture_study.ops.llm_instructions import MAXIMUM_SEMANTIC_FACTS

TIMESTAMP_BLOCK_SECONDS = 30.0
TIMESTAMP_TRACEABILITY_TOLERANCE_SECONDS = 0.05


class LlmParsingError(RuntimeError):
    """Raised when an LLM invocation fails or produces unusable output."""


def render_timestamped_transcript(evidence_pack: EvidencePack) -> str:
    blocks: list[str] = []
    block_start: float | None = None
    block_text: list[str] = []

    for segment in evidence_pack.segments:
        if (
            block_start is not None
            and segment.start_time >= block_start + TIMESTAMP_BLOCK_SECONDS
        ):
            blocks.append(f"source_sec={block_start:.3f} | text={' '.join(block_text)}")
            block_start = None
            block_text = []
        if block_start is None:
            block_start = segment.start_time
        block_text.append(segment.text)

    if block_start is not None:
        blocks.append(f"source_sec={block_start:.3f} | text={' '.join(block_text)}")
    return "\n".join(blocks)


def validate_traceability(ledger: SemanticLedger, evidence_pack: EvidencePack) -> None:
    if (
        ledger.lecture_id != evidence_pack.lecture_id
        or ledger.pack_sequence != evidence_pack.pack_sequence
    ):
        raise LlmParsingError(
            "the model echoed a lecture_id/pack_sequence that does not match "
            f"the source evidence pack: got "
            f"({ledger.lecture_id!r}, {ledger.pack_sequence}), expected "
            f"({evidence_pack.lecture_id!r}, {evidence_pack.pack_sequence})"
        )
    if len(ledger.facts) > MAXIMUM_SEMANTIC_FACTS:
        raise LlmParsingError(
            f"semantic ledger contains {len(ledger.facts)} facts, exceeding the "
            f"hard limit of {MAXIMUM_SEMANTIC_FACTS}; split the evidence pack"
        )
    for fact in ledger.facts:
        timestamp_ref = fact.timestamp_ref
        matches_transcript = any(
            segment.start_time - TIMESTAMP_TRACEABILITY_TOLERANCE_SECONDS
            <= timestamp_ref
            <= segment.end_time + TIMESTAMP_TRACEABILITY_TOLERANCE_SECONDS
            for segment in evidence_pack.segments
        )
        matches_anchor = evidence_pack.anchor_frame is not None and math.isclose(
            timestamp_ref,
            evidence_pack.anchor_frame.timestamp_sec,
            abs_tol=0.001,
        )
        if not matches_transcript and not matches_anchor:
            raise LlmParsingError(
                f"fact timestamp_ref {timestamp_ref} is outside every transcript "
                "segment and does not match the anchor-frame timestamp for "
                f"evidence pack {evidence_pack.lecture_id}/"
                f"{evidence_pack.pack_sequence}"
            )

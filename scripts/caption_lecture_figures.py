#!/usr/bin/env python3
"""Caption one lecture's extracted frames with the attested local vision
model.

Execution is lecture-scoped (D-12): this script takes exactly one lecture
identifier, has no batch mode, and never discovers over the slides root.
The vision model and its multimodal projector file are both recomputed
against the committed actor inventory before any inference starts (D-26);
a missing or digest-mismatched entry stops the script before it runs the
model.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from lecture_study.captioning.catalog_store import (
    discover_lecture_frames,
    write_figure_catalog,
)
from lecture_study.captioning.vlm_worker import caption_lecture_figures
from lecture_study.ops.model_attestation import (
    ModelAttestation,
    read_actor_inventory,
    recompute_and_compare,
)

_DEFAULT_INVENTORY_PATH = Path("config/actors.local.json")


class UnattestedModelError(RuntimeError):
    """Raised when a model path has no matching, digest-verified inventory entry."""


def main() -> int:
    args = _argument_parser().parse_args()
    try:
        _verify_attested(args.model_path, args.inventory_path)
        _verify_attested(args.mmproj_path, args.inventory_path)
    except UnattestedModelError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if not discover_lecture_frames(args.lecture_id):
        print(
            f"error: lecture {args.lecture_id!r} has no extracted frames",
            file=sys.stderr,
        )
        return 1

    catalog = asyncio.run(
        caption_lecture_figures(
            args.lecture_id,
            args.model_path,
            args.mmproj_path,
        )
    )
    write_figure_catalog(catalog)
    print(f"captioned {len(catalog.figures)} figure(s) for lecture {args.lecture_id!r}")
    return 0


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Caption one lecture's extracted frames with the attested local "
            "vision model. Processes exactly one lecture; there is no batch mode."
        )
    )
    parser.add_argument(
        "lecture_id",
        help="The single lecture identifier whose extracted frames will be captioned.",
    )
    parser.add_argument(
        "--model-path",
        type=Path,
        required=True,
        help="Path to the vision model GGUF file, attested in the actor inventory.",
    )
    parser.add_argument(
        "--mmproj-path",
        type=Path,
        required=True,
        help="Path to the model's multimodal projector GGUF file, also attested.",
    )
    parser.add_argument(
        "--inventory-path",
        type=Path,
        default=_DEFAULT_INVENTORY_PATH,
        help="Path to the attested actor inventory JSON file.",
    )
    return parser


def _verify_attested(model_path: Path, inventory_path: Path) -> None:
    attestations = read_actor_inventory(inventory_path)
    attestation = _find_attestation(model_path, attestations)
    if attestation is None:
        raise UnattestedModelError(
            f"{model_path} has no matching entry in the attested actor "
            f"inventory at {inventory_path}"
        )
    mismatch = recompute_and_compare(attestation)
    if mismatch is not None:
        raise UnattestedModelError(f"{model_path}: {mismatch}")


def _find_attestation(
    model_path: Path,
    attestations: tuple[ModelAttestation, ...],
) -> ModelAttestation | None:
    resolved_path = str(model_path.resolve())
    for attestation in attestations:
        if attestation.path == resolved_path:
            return attestation
    return None


if __name__ == "__main__":
    sys.exit(main())

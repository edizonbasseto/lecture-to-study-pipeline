#!/usr/bin/env python3
"""Attest local GGUF model files and write the phase actor inventory."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lecture_study.ops.model_attestation import (
    attest_model_file,
    read_actor_inventory,
    recompute_and_compare,
    write_actor_inventory,
)

_DEFAULT_INVENTORY_PATH = Path("config/actors.local.json")


def main() -> int:
    args = _argument_parser().parse_args()
    if args.verify_only:
        return _verify_inventory(args.inventory_path)
    return _attest_and_write(args.model, args.inventory_path)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Attest local GGUF model files against confirmed source repositories."
    )
    parser.add_argument(
        "--model",
        nargs=2,
        metavar=("PATH", "SOURCE_REPOSITORY"),
        action="append",
        default=[],
        help="A model file path paired with its confirmed source repository. Repeatable.",
    )
    parser.add_argument(
        "--inventory-path",
        type=Path,
        default=_DEFAULT_INVENTORY_PATH,
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Recompute digests for the existing inventory instead of attesting new files.",
    )
    return parser


def _attest_and_write(model_arguments: list[list[str]], inventory_path: Path) -> int:
    if not model_arguments:
        print(
            "error: at least one --model PATH SOURCE_REPOSITORY is required",
            file=sys.stderr,
        )
        return 1
    attestations = [
        attest_model_file(Path(path), source_repository)
        for path, source_repository in model_arguments
    ]
    write_actor_inventory(inventory_path, attestations)
    print(f"attested {len(attestations)} model file(s) to {inventory_path}")
    return 0


def _verify_inventory(inventory_path: Path) -> int:
    attestations = read_actor_inventory(inventory_path)
    mismatches = [
        reason
        for attestation in attestations
        if (reason := recompute_and_compare(attestation)) is not None
    ]
    for reason in mismatches:
        print(reason, file=sys.stderr)
    if mismatches:
        return 1
    print(
        f"verified {len(attestations)} inventory entr{'y' if len(attestations) == 1 else 'ies'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

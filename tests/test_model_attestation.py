"""Behavior tests for local GGUF actor attestation.

Uses small temporary files instead of real model weights; the attestation
module never inspects GGUF content, only the path, the digest, and the
confirmed source repository.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from lecture_study.ops.model_attestation import (
    ModelAttestation,
    SplitGgufRejectedError,
    attest_model_file,
    merge_digest_differs_from_shards,
    read_actor_inventory,
    recompute_and_compare,
    render_actor_inventory_json,
    write_actor_inventory,
)

_SHARD_NAMES = (
    "model-00001-of-00002.gguf",
    "MODEL-1-OF-2.GGUF",
)


def _write_small_file(path: Path, content: bytes = b"not a real model") -> Path:
    path.write_bytes(content)
    return path


def _load_attest_script() -> ModuleType:
    script_path = Path(__file__).parents[1] / "scripts" / "attest_local_actors.py"
    spec = importlib.util.spec_from_file_location("attest_local_actors", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load script from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_ATTEST_SCRIPT = _load_attest_script()


def test_attest_model_file_records_digest_size_source_and_timestamp(
    tmp_path: Path,
) -> None:
    model_path = _write_small_file(tmp_path / "actor.gguf", b"weights-content")

    attestation = attest_model_file(model_path, "org/repo")

    assert attestation.path == str(model_path)
    assert attestation.sha256 == hashlib.sha256(b"weights-content").hexdigest()
    assert attestation.size_bytes == len(b"weights-content")
    assert attestation.source_repository == "org/repo"
    assert attestation.attested_at


@pytest.mark.parametrize("shard_name", _SHARD_NAMES)
def test_attest_model_file_rejects_shard_suffixed_filenames(
    tmp_path: Path,
    shard_name: str,
) -> None:
    model_path = _write_small_file(tmp_path / shard_name)

    with pytest.raises(SplitGgufRejectedError):
        attest_model_file(model_path, "org/repo")


def test_attest_model_file_rejects_non_gguf_suffix(tmp_path: Path) -> None:
    model_path = _write_small_file(tmp_path / "actor.bin")

    with pytest.raises(ValueError, match="GGUF"):
        attest_model_file(model_path, "org/repo")


def test_attest_model_file_rejects_symbolic_link(tmp_path: Path) -> None:
    real_path = _write_small_file(tmp_path / "real.gguf")
    link_path = tmp_path / "link.gguf"
    link_path.symlink_to(real_path)

    with pytest.raises(ValueError, match="symbolic link"):
        attest_model_file(link_path, "org/repo")


def test_attest_model_file_rejects_empty_source_repository(tmp_path: Path) -> None:
    model_path = _write_small_file(tmp_path / "actor.gguf")

    with pytest.raises(ValueError, match="source"):
        attest_model_file(model_path, "")


def test_render_actor_inventory_json_is_sorted_and_order_independent(
    tmp_path: Path,
) -> None:
    first = attest_model_file(_write_small_file(tmp_path / "b.gguf", b"b"), "org/b")
    second = attest_model_file(_write_small_file(tmp_path / "a.gguf", b"a"), "org/a")

    rendered_one_order = render_actor_inventory_json([first, second])
    rendered_other_order = render_actor_inventory_json([second, first])

    assert rendered_one_order == rendered_other_order
    decoded = json.loads(rendered_one_order)
    assert [entry["path"] for entry in decoded["actors"]] == sorted(
        [first.path, second.path]
    )


def test_write_and_read_actor_inventory_round_trip(tmp_path: Path) -> None:
    attestation = attest_model_file(
        _write_small_file(tmp_path / "actor.gguf", b"content"), "org/repo"
    )
    inventory_path = tmp_path / "actors.json"

    write_actor_inventory(inventory_path, [attestation])
    loaded = read_actor_inventory(inventory_path)

    assert loaded == (attestation,)
    assert not inventory_path.with_name(".actors.json.tmp").exists()


def test_write_actor_inventory_is_reproducible_over_an_unchanged_set(
    tmp_path: Path,
) -> None:
    attestation = attest_model_file(
        _write_small_file(tmp_path / "actor.gguf", b"content"), "org/repo"
    )
    inventory_path = tmp_path / "actors.json"

    write_actor_inventory(inventory_path, [attestation])
    first_bytes = inventory_path.read_bytes()
    write_actor_inventory(inventory_path, [attestation])
    second_bytes = inventory_path.read_bytes()

    assert first_bytes == second_bytes


def test_recompute_and_compare_matches_unchanged_file(tmp_path: Path) -> None:
    model_path = _write_small_file(tmp_path / "actor.gguf", b"content")
    attestation = attest_model_file(model_path, "org/repo")

    assert recompute_and_compare(attestation) is None


def test_recompute_and_compare_detects_tampered_file(tmp_path: Path) -> None:
    model_path = _write_small_file(tmp_path / "actor.gguf", b"content")
    attestation = attest_model_file(model_path, "org/repo")

    model_path.write_bytes(b"tampered-content")

    reason = recompute_and_compare(attestation)
    assert reason is not None
    assert "differs" in reason


def test_recompute_and_compare_detects_missing_file(tmp_path: Path) -> None:
    model_path = _write_small_file(tmp_path / "actor.gguf", b"content")
    attestation = attest_model_file(model_path, "org/repo")
    model_path.unlink()

    reason = recompute_and_compare(attestation)
    assert reason is not None
    assert "missing" in reason


def test_merge_digest_differs_from_shards(tmp_path: Path) -> None:
    shard_one = ModelAttestation(
        path="shard-1.gguf",
        sha256="a" * 64,
        size_bytes=1,
        source_repository="org/repo",
        attested_at="now",
    )
    shard_two = ModelAttestation(
        path="shard-2.gguf",
        sha256="b" * 64,
        size_bytes=1,
        source_repository="org/repo",
        attested_at="now",
    )
    merged_distinct = shard_one.model_copy(
        update={"path": "merged.gguf", "sha256": "c" * 64}
    )
    merged_colliding = shard_one.model_copy(update={"path": "merged.gguf"})

    assert merge_digest_differs_from_shards(merged_distinct, [shard_one, shard_two])
    assert not merge_digest_differs_from_shards(
        merged_colliding, [shard_one, shard_two]
    )


def test_script_attest_and_write_writes_inventory(tmp_path: Path) -> None:
    model_path = _write_small_file(tmp_path / "actor.gguf", b"content")
    inventory_path = tmp_path / "actors.json"

    exit_code = _ATTEST_SCRIPT._attest_and_write(
        [[str(model_path), "org/repo"]],
        inventory_path,
    )

    assert exit_code == 0
    loaded = read_actor_inventory(inventory_path)
    assert len(loaded) == 1
    assert loaded[0].source_repository == "org/repo"


def test_script_attest_and_write_requires_at_least_one_model(tmp_path: Path) -> None:
    exit_code = _ATTEST_SCRIPT._attest_and_write([], tmp_path / "actors.json")

    assert exit_code == 1


def test_script_verify_only_passes_for_untouched_inventory(tmp_path: Path) -> None:
    model_path = _write_small_file(tmp_path / "actor.gguf", b"content")
    inventory_path = tmp_path / "actors.json"
    _ATTEST_SCRIPT._attest_and_write([[str(model_path), "org/repo"]], inventory_path)

    exit_code = _ATTEST_SCRIPT._verify_inventory(inventory_path)

    assert exit_code == 0


def test_script_verify_only_fails_for_tampered_file(tmp_path: Path) -> None:
    model_path = _write_small_file(tmp_path / "actor.gguf", b"content")
    inventory_path = tmp_path / "actors.json"
    _ATTEST_SCRIPT._attest_and_write([[str(model_path), "org/repo"]], inventory_path)

    model_path.write_bytes(b"tampered")

    exit_code = _ATTEST_SCRIPT._verify_inventory(inventory_path)

    assert exit_code == 1

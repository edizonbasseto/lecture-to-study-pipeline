"""Tree snapshots: what makes "the blocked scripts wrote nothing" checkable."""

import os
from pathlib import Path

from lecture_study.demo.tree_snapshot import difference, snapshot_tree


def _roots(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "exam"
    root.mkdir()
    (root / "exam.json").write_text("{}")
    return {"exam": root}


def test_equal_snapshots_give_an_empty_difference(tmp_path: Path) -> None:
    roots = _roots(tmp_path)

    assert difference(snapshot_tree(roots), snapshot_tree(roots)).is_empty()


def test_created_file_is_reported_as_created(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    before = snapshot_tree(roots)

    (roots["exam"] / "extra.json").write_text("{}")

    assert "exam:extra.json" in difference(before, snapshot_tree(roots)).created


def test_created_empty_directory_is_reported_as_created(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    before = snapshot_tree(roots)

    (roots["exam"] / "nested").mkdir()

    assert "exam:nested" in difference(before, snapshot_tree(roots)).created


def test_modified_byte_is_reported_as_modified(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    target = roots["exam"] / "exam.json"
    before = snapshot_tree(roots)
    original_mtime_ns = target.stat().st_mtime_ns

    target.write_text("[]")
    os.utime(target, ns=(original_mtime_ns, original_mtime_ns))

    assert "exam:exam.json" in difference(before, snapshot_tree(roots)).modified


def test_touched_modification_time_is_reported_as_modified(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    target = roots["exam"] / "exam.json"
    before = snapshot_tree(roots)
    later_ns = target.stat().st_mtime_ns + 5_000_000_000

    os.utime(target, ns=(later_ns, later_ns))

    assert difference(before, snapshot_tree(roots)).modified == ("exam:exam.json",)


def test_deleted_file_is_reported_as_deleted(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    before = snapshot_tree(roots)

    (roots["exam"] / "exam.json").unlink()

    assert "exam:exam.json" in difference(before, snapshot_tree(roots)).deleted


def test_absent_root_is_stable_and_creation_is_a_modification(
    tmp_path: Path,
) -> None:
    roots = {"podcast": tmp_path / "missing"}
    before = snapshot_tree(roots)

    assert difference(before, snapshot_tree(roots)).is_empty()
    assert before.entries[0].kind == "absent_root"

    roots["podcast"].mkdir()

    assert difference(before, snapshot_tree(roots)).modified == ("podcast:.",)


def test_symlink_is_recorded_and_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside content")
    roots = _roots(tmp_path)
    (roots["exam"] / "link").symlink_to(outside, target_is_directory=True)

    snapshot = snapshot_tree(roots)

    link_entry = next(e for e in snapshot.entries if e.relative_path == "link")
    assert link_entry.kind == "symlink"
    assert link_entry.link_target == str(outside)
    assert link_entry.sha256 is None
    assert all("secret.txt" not in entry.relative_path for entry in snapshot.entries)

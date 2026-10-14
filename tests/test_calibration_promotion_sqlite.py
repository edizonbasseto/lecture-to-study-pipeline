"""Persistence, corruption, rollback, and concurrency tests for calibration state."""

from __future__ import annotations

import hashlib
import multiprocessing
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from lecture_study.quality.calibration_promotion import (
    CalibrationArtifactKind,
    CalibrationDisposition,
    CalibrationFailureKind,
    CalibrationPromotionError,
    SemanticCalibrationRevocationPort,
    SemanticCalibrationStateRecord,
    SemanticCalibrationTrustedClockPort,
)
from lecture_study.quality.calibration_promotion_sqlite import (
    SqliteSemanticCalibrationCurrentState,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


class _TrustedClock(SemanticCalibrationTrustedClockPort):
    def __init__(self, now: int = 500) -> None:
        self.now = now
        self.available = True

    def current_epoch_seconds(self) -> int:
        if not self.available:
            raise RuntimeError("synthetic trusted clock unavailable")
        return self.now


class _Revocations(SemanticCalibrationRevocationPort):
    def __init__(self) -> None:
        self.revoked: set[tuple[CalibrationArtifactKind, str, str]] = set()
        self.available = True

    def require_not_revoked(self, record: SemanticCalibrationStateRecord) -> None:
        if not self.available:
            raise RuntimeError("synthetic revocation state unavailable")
        identity = (
            record.artifact_kind,
            str(record.artifact_id),
            str(record.artifact_version),
        )
        if identity in self.revoked:
            raise CalibrationPromotionError(
                "synthetic calibration record revoked",
                kind=CalibrationFailureKind.REVOKED,
                reason_code="revoked_calibration_state_record",
            )


def _record(
    artifact_kind: CalibrationArtifactKind,
    payload_sha256: str,
    *,
    artifact_id: str | None = None,
    artifact_version: str = "synthetic-v1",
    authority_sequence: int = 1,
    issued_at: int = 100,
    expires_at: int = 1_000,
) -> SemanticCalibrationStateRecord:
    return SemanticCalibrationStateRecord(
        artifact_kind=artifact_kind,
        artifact_id=artifact_id or f"artifact-{payload_sha256[:12]}",
        artifact_version=artifact_version,
        payload_sha256=payload_sha256,
        trust_key_id=_digest("synthetic-calibration-state-key"),
        authority_id="synthetic-calibration-state-authority",
        authority_sequence=authority_sequence,
        issued_at_epoch_seconds=issued_at,
        expires_at_epoch_seconds=expires_at,
    )


def _record_in_process(
    root_text: str,
    artifact_kind: str,
    payload_sha256: str,
    result_queue: multiprocessing.queues.Queue[str],
) -> None:
    try:
        registry = SqliteSemanticCalibrationCurrentState.open_existing(
            root=Path(root_text),
            lock_timeout_seconds=5,
            trusted_clock=_TrustedClock(),
            revocations=_Revocations(),
        )
        registry.record_issued(
            _record(CalibrationArtifactKind(artifact_kind), payload_sha256)
        )
        result_queue.put("ok")
    except Exception as error:
        result_queue.put(type(error).__name__)


def _initialize(
    root: Path,
    *,
    clock: _TrustedClock | None = None,
    revocations: _Revocations | None = None,
) -> SqliteSemanticCalibrationCurrentState:
    return SqliteSemanticCalibrationCurrentState.initialize(
        root=root,
        lock_timeout_seconds=2,
        trusted_clock=clock or _TrustedClock(),
        revocations=revocations or _Revocations(),
    )


def _database(root: Path) -> Path:
    return next(root.glob("*.sqlite3"))


def _anchors(root: Path) -> Path:
    return next(path for path in root.iterdir() if path.name.endswith("-anchors"))


def test_explicit_initialize_open_restart_and_append_only_identity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "calibration-state"
    registry = _initialize(root)
    policy = _digest("policy")
    evidence = _digest("evidence")

    policy_record = _record(CalibrationArtifactKind.POLICY, policy)
    evidence_record = _record(CalibrationArtifactKind.EVIDENCE, evidence)
    registry.record_issued(policy_record)
    registry.record_issued(evidence_record)
    restarted = SqliteSemanticCalibrationCurrentState.open_existing(
        root=root,
        lock_timeout_seconds=2,
        trusted_clock=_TrustedClock(),
        revocations=_Revocations(),
    )

    restarted.require_current(policy_record)
    restarted.require_current(evidence_record)
    assert restarted.detects_coordinated_whole_root_rollback is False
    assert not hasattr(restarted, "delete")
    assert not hasattr(restarted, "release")
    assert not hasattr(restarted, "revoke")


def test_initialization_and_opening_are_distinct_and_fail_closed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "calibration-state"
    with pytest.raises(ValueError, match="incomplete"):
        SqliteSemanticCalibrationCurrentState.open_existing(
            root=root,
            lock_timeout_seconds=1,
            trusted_clock=_TrustedClock(),
            revocations=_Revocations(),
        )
    _initialize(root)
    with pytest.raises(ValueError, match="not empty"):
        _initialize(root)
    with pytest.raises(ValueError, match="absolute"):
        _initialize(Path("relative-calibration-state"))


@pytest.mark.parametrize(
    "lock_timeout_seconds",
    (
        True,
        float("nan"),
        float("inf"),
        float("-inf"),
        60.000_001,
    ),
)
def test_invalid_lock_timeout_is_rejected_before_persistent_state_access(
    tmp_path: Path,
    lock_timeout_seconds: float,
) -> None:
    root = tmp_path / "invalid-timeout-calibration-state"

    with pytest.raises((TypeError, ValueError), match="lock timeout"):
        SqliteSemanticCalibrationCurrentState.initialize(
            root=root,
            lock_timeout_seconds=lock_timeout_seconds,
            trusted_clock=_TrustedClock(),
            revocations=_Revocations(),
        )

    assert not root.exists()


def test_maximum_lock_timeout_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "maximum-timeout-calibration-state"

    registry = SqliteSemanticCalibrationCurrentState.initialize(
        root=root,
        lock_timeout_seconds=60,
        trusted_clock=_TrustedClock(),
        revocations=_Revocations(),
    )

    assert registry.root == root


def test_duplicate_insert_is_idempotent_without_duplicate_event(
    tmp_path: Path,
) -> None:
    root = tmp_path / "calibration-state"
    registry = _initialize(root)
    payload = _digest("same-policy")

    for _ in range(3):
        registry.record_issued(_record(CalibrationArtifactKind.POLICY, payload))

    with sqlite3.connect(_database(root)) as connection:
        count = connection.execute("SELECT COUNT(*) FROM state_events").fetchone()
    assert count == (1,)
    assert len(tuple(_anchors(root).iterdir())) == 1


def test_process_concurrency_preserves_every_unique_event(tmp_path: Path) -> None:
    root = tmp_path / "calibration-state"
    _initialize(root)
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    payloads = tuple(_digest(f"concurrent-{index}") for index in range(6))
    processes = tuple(
        context.Process(
            target=_record_in_process,
            args=(
                str(root),
                CalibrationArtifactKind.EVIDENCE.value,
                payload,
                result_queue,
            ),
        )
        for payload in payloads
    )

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)

    assert all(process.exitcode == 0 for process in processes)
    assert tuple(result_queue.get(timeout=2) for _ in processes) == ("ok",) * len(
        processes
    )
    restarted = SqliteSemanticCalibrationCurrentState.open_existing(
        root=root,
        lock_timeout_seconds=2,
        trusted_clock=_TrustedClock(),
        revocations=_Revocations(),
    )
    for payload in payloads:
        restarted.require_current(_record(CalibrationArtifactKind.EVIDENCE, payload))


def test_concurrent_duplicate_identity_creates_at_most_one_event(
    tmp_path: Path,
) -> None:
    root = tmp_path / "calibration-state"
    _initialize(root)
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    payload = _digest("concurrent-duplicate")
    processes = tuple(
        context.Process(
            target=_record_in_process,
            args=(
                str(root),
                CalibrationArtifactKind.POLICY.value,
                payload,
                result_queue,
            ),
        )
        for _ in range(4)
    )

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)

    assert all(process.exitcode == 0 for process in processes)
    assert tuple(result_queue.get(timeout=2) for _ in processes) == ("ok",) * len(
        processes
    )
    with sqlite3.connect(_database(root)) as connection:
        count = connection.execute("SELECT COUNT(*) FROM state_events").fetchone()
    assert count == (1,)


@pytest.mark.parametrize("mutation", ("database-corruption", "database-truncation"))
def test_database_corruption_and_truncation_fail_closed(
    tmp_path: Path,
    mutation: str,
) -> None:
    root = tmp_path / "calibration-state"
    registry = _initialize(root)
    payload = _digest("persisted")
    record = _record(CalibrationArtifactKind.POLICY, payload)
    registry.record_issued(record)
    database = _database(root)
    if mutation == "database-corruption":
        with database.open("r+b") as stream:
            stream.seek(64)
            stream.write(b"corrupt-state")
            stream.flush()
            os.fsync(stream.fileno())
    else:
        with database.open("r+b") as stream:
            stream.truncate(128)
            stream.flush()
            os.fsync(stream.fileno())

    with pytest.raises(CalibrationPromotionError) as failure:
        registry.require_current(record)
    assert failure.value.disposition in {
        CalibrationDisposition.NEEDS_REVIEW,
        CalibrationDisposition.QUARANTINE,
    }


def test_anchor_truncation_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "calibration-state"
    registry = _initialize(root)
    payload = _digest("anchored")
    record = _record(CalibrationArtifactKind.EVIDENCE, payload)
    registry.record_issued(record)
    anchor = next(_anchors(root).iterdir())
    with anchor.open("r+b") as stream:
        stream.truncate(8)
        stream.flush()
        os.fsync(stream.fileno())

    with pytest.raises(
        CalibrationPromotionError,
        match="anchor is substituted",
    ):
        registry.require_current(record)


def test_database_only_rollback_is_detected_by_immutable_anchors(
    tmp_path: Path,
) -> None:
    root = tmp_path / "calibration-state"
    registry = _initialize(root)
    first = _digest("first")
    second = _digest("second")
    first_record = _record(CalibrationArtifactKind.POLICY, first)
    registry.record_issued(first_record)
    backup = tmp_path / "database-backup"
    shutil.copyfile(_database(root), backup)
    registry.record_issued(_record(CalibrationArtifactKind.EVIDENCE, second))
    shutil.copyfile(backup, _database(root))

    with pytest.raises(
        CalibrationPromotionError,
        match="anchors do not match",
    ):
        registry.require_current(first_record)


def test_missing_identity_and_unavailable_layout_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "calibration-state"
    registry = _initialize(root)

    with pytest.raises(CalibrationPromotionError) as missing:
        registry.require_current(
            _record(
                CalibrationArtifactKind.SHORT_MATERIAL_POLICY,
                _digest("missing"),
            )
        )

    assert missing.value.disposition is CalibrationDisposition.NEEDS_REVIEW


def test_trusted_clock_expiration_and_not_yet_valid_state_fail_closed(
    tmp_path: Path,
) -> None:
    clock = _TrustedClock(now=500)
    registry = _initialize(tmp_path / "calibration-state", clock=clock)
    record = _record(
        CalibrationArtifactKind.POLICY,
        _digest("expiring-policy"),
        issued_at=400,
        expires_at=600,
    )
    registry.record_issued(record)

    clock.now = 600
    with pytest.raises(CalibrationPromotionError) as expired:
        registry.require_current(record)
    assert expired.value.reason_code == "expired_calibration_artifact"
    assert expired.value.disposition is CalibrationDisposition.QUARANTINE

    clock.now = 50
    with pytest.raises(CalibrationPromotionError) as not_yet_valid:
        registry.record_issued(
            _record(
                CalibrationArtifactKind.EVIDENCE,
                _digest("future-evidence"),
                issued_at=100,
                expires_at=700,
            )
        )
    assert not_yet_valid.value.disposition is CalibrationDisposition.NEEDS_REVIEW


def test_revoked_and_unavailable_trusted_state_fail_closed(tmp_path: Path) -> None:
    clock = _TrustedClock()
    revocations = _Revocations()
    registry = _initialize(
        tmp_path / "calibration-state",
        clock=clock,
        revocations=revocations,
    )
    record = _record(
        CalibrationArtifactKind.EVIDENCE,
        _digest("revocable-evidence"),
    )
    registry.record_issued(record)
    revocations.revoked.add(
        (
            record.artifact_kind,
            str(record.artifact_id),
            str(record.artifact_version),
        )
    )

    with pytest.raises(CalibrationPromotionError) as revoked:
        registry.require_current(record)
    assert revoked.value.reason_code == "revoked_calibration_state_record"
    assert revoked.value.disposition is CalibrationDisposition.QUARANTINE

    revocations.revoked.clear()
    clock.available = False
    with pytest.raises(CalibrationPromotionError) as unavailable_clock:
        registry.require_current(record)
    assert unavailable_clock.value.disposition is CalibrationDisposition.NEEDS_REVIEW

    clock.available = True
    revocations.available = False
    with pytest.raises(CalibrationPromotionError) as unavailable_revocations:
        registry.require_current(record)
    assert unavailable_revocations.value.disposition is (
        CalibrationDisposition.NEEDS_REVIEW
    )


def test_logical_identity_substitution_is_rejected_across_restart(
    tmp_path: Path,
) -> None:
    root = tmp_path / "calibration-state"
    registry = _initialize(root)
    original = _record(
        CalibrationArtifactKind.POLICY,
        _digest("original-policy"),
        artifact_id="policy-a",
        artifact_version="policy-v1",
        authority_sequence=7,
    )
    registry.record_issued(original)
    substituted = original.model_copy(
        update={"payload_sha256": _digest("substituted-policy")}
    )

    with pytest.raises(CalibrationPromotionError) as substitution:
        registry.record_issued(substituted)
    assert substitution.value.reason_code == (
        "calibration_logical_identity_substitution"
    )

    restarted = SqliteSemanticCalibrationCurrentState.open_existing(
        root=root,
        lock_timeout_seconds=2,
        trusted_clock=_TrustedClock(),
        revocations=_Revocations(),
    )
    with pytest.raises(CalibrationPromotionError) as replay:
        restarted.record_issued(substituted)
    assert replay.value.disposition is CalibrationDisposition.QUARANTINE
    restarted.require_current(original)


def test_new_authority_sequence_supersedes_prior_record_without_deletion(
    tmp_path: Path,
) -> None:
    registry = _initialize(tmp_path / "calibration-state")
    original = _record(
        CalibrationArtifactKind.SHORT_MATERIAL_POLICY,
        _digest("threshold-sequence-1"),
        artifact_id="threshold-a",
        artifact_version="threshold-v1",
        authority_sequence=1,
    )
    current = original.model_copy(
        update={
            "payload_sha256": _digest("threshold-sequence-2"),
            "authority_sequence": 2,
        }
    )
    registry.record_issued(original)
    registry.record_issued(current)

    registry.require_current(current)
    with pytest.raises(CalibrationPromotionError) as stale:
        registry.require_current(original)
    assert stale.value.reason_code == "stale_calibration_authority_sequence"
    with pytest.raises(CalibrationPromotionError) as replay:
        registry.record_issued(original)
    assert replay.value.reason_code == "stale_calibration_authority_sequence"
    with sqlite3.connect(_database(registry.root)) as connection:
        count = connection.execute("SELECT COUNT(*) FROM state_events").fetchone()
    assert count == (2,)


def test_same_or_lower_unseen_authority_sequence_is_rejected(tmp_path: Path) -> None:
    registry = _initialize(tmp_path / "calibration-state")
    current = _record(
        CalibrationArtifactKind.EVIDENCE,
        _digest("current-evidence"),
        artifact_id="evidence-a",
        artifact_version="run-a",
        authority_sequence=5,
    )
    registry.record_issued(current)
    replay = current.model_copy(
        update={
            "payload_sha256": _digest("replayed-evidence"),
            "authority_sequence": 4,
        }
    )

    with pytest.raises(CalibrationPromotionError) as rejected:
        registry.record_issued(replay)
    assert rejected.value.reason_code == "calibration_authority_sequence_replay"

from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import sqlite3
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from multiprocessing.connection import Connection
from pathlib import Path

import pytest

import lecture_study.quality.human_review_quarantine as quarantine_module
from lecture_study.quality.human_review import (
    HumanReviewEvidenceIdentity,
    HumanReviewEvidenceQuarantined,
    HumanReviewFailureKind,
    HumanReviewQuarantineEvidence,
    HumanReviewQuarantineRegistryError,
)
from lecture_study.quality.human_review_quarantine import (
    SqliteHumanReviewQuarantineRegistry,
    SqliteHumanReviewQuarantineRegistryOpenMode,
)

_BUSY_TIMEOUT_MILLISECONDS = 5_000
_LOCK_TIMEOUT_MILLISECONDS = 5_000


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _evidence(
    label: str,
    *,
    scope_label: str = "scope",
    payload_label: str | None = None,
    review_version: int = 1,
    signature_label: str | None = None,
    identity_label: str | None = None,
    trust_key_label: str = "trust-key",
    authority_id: str = "authority-primary",
) -> HumanReviewQuarantineEvidence:
    return HumanReviewQuarantineEvidence(
        identity=HumanReviewEvidenceIdentity(
            evidence_payload_sha256=_digest(identity_label or f"identity-{label}")
        ),
        cryptographic_domain="lecture-study.human-review.v1",
        signature_purpose="human-review-scope-decision",
        trust_key_id=_digest(trust_key_label),
        authority_id=authority_id,
        lecture_id="lecture-primary",
        canonical_scope_sha256=_digest(scope_label),
        payload_base_sha256=_digest(payload_label or f"payload-{label}"),
        review_version=review_version,
        observed_signature_sha256=_digest(signature_label or f"signature-{label}"),
    )


def _registry(tmp_path: Path) -> SqliteHumanReviewQuarantineRegistry:
    return SqliteHumanReviewQuarantineRegistry(
        registry_root=tmp_path / "human-review-quarantine",
        mode=SqliteHumanReviewQuarantineRegistryOpenMode.INITIALIZE,
        busy_timeout_milliseconds=_BUSY_TIMEOUT_MILLISECONDS,
        lock_timeout_milliseconds=_LOCK_TIMEOUT_MILLISECONDS,
    )


def _process_append(root_text: str, label: str) -> tuple[int, str]:
    registry = SqliteHumanReviewQuarantineRegistry(
        registry_root=Path(root_text),
        mode=SqliteHumanReviewQuarantineRegistryOpenMode.OPEN_EXISTING,
        busy_timeout_milliseconds=_BUSY_TIMEOUT_MILLISECONDS,
        lock_timeout_milliseconds=_LOCK_TIMEOUT_MILLISECONDS,
    )
    record = registry.append(
        _evidence(label, scope_label=f"scope-{label}"),
        kind=HumanReviewFailureKind.INVALID,
        reason_code=f"failure-{label}",
    )
    return record.registry_sequence, record.record_sha256


def _hold_exclusive_lock(
    lock_path_text: str,
    ready_writer: Connection,
    release_reader: Connection,
) -> None:
    descriptor = os.open(lock_path_text, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        ready_writer.send_bytes(b"locked")
        release_reader.recv_bytes()
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def test_append_persists_complete_forensic_record_and_hash_chain(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    first_evidence = _evidence("first")
    second_evidence = _evidence(
        "second",
        scope_label="other-scope",
        review_version=2,
    )

    first = registry.append(
        first_evidence,
        kind=HumanReviewFailureKind.INVALID,
        reason_code="invalid-signature",
    )
    second = registry.append(
        second_evidence,
        kind=HumanReviewFailureKind.FALSIFIED,
        reason_code="falsified-evidence",
    )
    reopened = SqliteHumanReviewQuarantineRegistry(
        registry_root=tmp_path / "human-review-quarantine",
        mode=SqliteHumanReviewQuarantineRegistryOpenMode.OPEN_EXISTING,
        busy_timeout_milliseconds=_BUSY_TIMEOUT_MILLISECONDS,
        lock_timeout_milliseconds=_LOCK_TIMEOUT_MILLISECONDS,
    )

    assert first.registry_sequence == 1
    assert first.evidence_payload_sha256 == (
        first_evidence.identity.evidence_payload_sha256
    )
    assert first.cryptographic_domain == first_evidence.cryptographic_domain
    assert first.signature_purpose == first_evidence.signature_purpose
    assert first.trust_key_id == first_evidence.trust_key_id
    assert first.authority_id == first_evidence.authority_id
    assert first.lecture_id == first_evidence.lecture_id
    assert first.canonical_scope_sha256 == first_evidence.canonical_scope_sha256
    assert first.review_version == first_evidence.review_version
    assert first.reason_kind is HumanReviewFailureKind.INVALID
    assert first.observed_signature_sha256 == (first_evidence.observed_signature_sha256)
    assert first.previous_record_sha256 is None
    assert second.registry_sequence == 2
    assert second.previous_record_sha256 == first.record_sha256
    with pytest.raises(HumanReviewEvidenceQuarantined):
        reopened.require_not_quarantined(second_evidence)


def test_exact_append_is_idempotent_and_preserves_original_reason(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    evidence = _evidence("same")
    original = registry.append(
        evidence,
        kind=HumanReviewFailureKind.INVALID,
        reason_code="original-reason",
    )

    repeated = registry.append(
        evidence,
        kind=HumanReviewFailureKind.SPOOFED,
        reason_code="replacement-reason",
    )

    assert repeated == original
    assert repeated.reason_kind is HumanReviewFailureKind.INVALID
    assert repeated.reason_code == "original-reason"


def test_resigning_identical_payload_remains_an_exact_quarantine_hit(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    original_evidence = _evidence("same-payload")
    original_record = registry.append(
        original_evidence,
        kind=HumanReviewFailureKind.INVALID,
        reason_code="invalid-signature",
    )
    resigned = original_evidence.model_copy(
        update={"observed_signature_sha256": _digest("new-signature")}
    )

    with pytest.raises(HumanReviewEvidenceQuarantined):
        registry.require_not_quarantined(resigned)
    repeated = registry.append(
        resigned,
        kind=HumanReviewFailureKind.SPOOFED,
        reason_code="new-observation",
    )

    assert repeated == original_record
    assert repeated.observed_signature_sha256 == (
        original_evidence.observed_signature_sha256
    )


@pytest.mark.parametrize(
    "candidate",
    [
        _evidence("new-identity", review_version=1),
        _evidence(
            "new-version",
            payload_label="payload-baseline",
            review_version=2,
        ),
        _evidence(
            "new-payload",
            review_version=2,
            signature_label="signature-baseline",
        ),
    ],
)
def test_freshness_requires_new_payload_version_and_signature(
    tmp_path: Path,
    candidate: HumanReviewQuarantineEvidence,
) -> None:
    registry = _registry(tmp_path)
    registry.append(
        _evidence("baseline"),
        kind=HumanReviewFailureKind.REJECTED,
        reason_code="rejected-review",
    )

    with pytest.raises(HumanReviewEvidenceQuarantined):
        registry.require_not_quarantined(candidate)


def test_fully_fresh_evidence_for_same_scope_is_allowed(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.append(
        _evidence("baseline"),
        kind=HumanReviewFailureKind.REJECTED,
        reason_code="rejected-review",
    )

    registry.require_not_quarantined(_evidence("fresh", review_version=2))


def test_foreign_trust_identity_cannot_poison_legitimate_scope(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    registry.append(
        _evidence(
            "foreign",
            review_version=(1 << 63) - 1,
            trust_key_label="foreign-key",
            authority_id="foreign-authority",
        ),
        kind=HumanReviewFailureKind.SPOOFED,
        reason_code="foreign-trust",
    )

    registry.require_not_quarantined(_evidence("legitimate", review_version=2))


def test_database_triggers_reject_record_update_and_delete(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.append(
        _evidence("immutable"),
        kind=HumanReviewFailureKind.INVALID,
        reason_code="invalid-signature",
    )
    connection = sqlite3.connect(registry.database_path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                """
                UPDATE human_review_quarantine_records
                SET reason_code = 'replacement'
                WHERE registry_sequence = 1
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            connection.execute(
                """
                DELETE FROM human_review_quarantine_records
                WHERE registry_sequence = 1
                """
            )
    finally:
        connection.close()


def test_external_anchors_detect_database_rollback(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.append(
        _evidence("first"),
        kind=HumanReviewFailureKind.INVALID,
        reason_code="first-failure",
    )
    backup = tmp_path / "old-database.sqlite3"
    shutil.copyfile(registry.database_path, backup)
    registry.append(
        _evidence("second", scope_label="second-scope"),
        kind=HumanReviewFailureKind.FALSIFIED,
        reason_code="second-failure",
    )
    shutil.copyfile(backup, registry.database_path)

    with pytest.raises(
        HumanReviewQuarantineRegistryError,
        match="anchors do not match",
    ):
        SqliteHumanReviewQuarantineRegistry(
            registry_root=tmp_path / "human-review-quarantine",
            mode=SqliteHumanReviewQuarantineRegistryOpenMode.OPEN_EXISTING,
            busy_timeout_milliseconds=_BUSY_TIMEOUT_MILLISECONDS,
            lock_timeout_milliseconds=_LOCK_TIMEOUT_MILLISECONDS,
        )


def test_two_instances_share_permanent_quarantine_state(tmp_path: Path) -> None:
    first = _registry(tmp_path)
    second = SqliteHumanReviewQuarantineRegistry(
        registry_root=tmp_path / "human-review-quarantine",
        mode=SqliteHumanReviewQuarantineRegistryOpenMode.OPEN_EXISTING,
        busy_timeout_milliseconds=_BUSY_TIMEOUT_MILLISECONDS,
        lock_timeout_milliseconds=_LOCK_TIMEOUT_MILLISECONDS,
    )
    evidence = _evidence("shared")
    first.append(
        evidence,
        kind=HumanReviewFailureKind.REJECTED,
        reason_code="rejected-review",
    )

    with pytest.raises(HumanReviewEvidenceQuarantined):
        second.require_not_quarantined(evidence)


def test_cross_process_appends_do_not_lose_records(tmp_path: Path) -> None:
    root = tmp_path / "human-review-quarantine"
    SqliteHumanReviewQuarantineRegistry(
        registry_root=root,
        mode=SqliteHumanReviewQuarantineRegistryOpenMode.INITIALIZE,
        busy_timeout_milliseconds=_BUSY_TIMEOUT_MILLISECONDS,
        lock_timeout_milliseconds=_LOCK_TIMEOUT_MILLISECONDS,
    )

    with ProcessPoolExecutor(max_workers=2) as executor:
        records = tuple(
            future.result()
            for future in (
                executor.submit(_process_append, str(root), "first-process"),
                executor.submit(_process_append, str(root), "second-process"),
            )
        )

    assert sorted(sequence for sequence, _ in records) == [1, 2]
    assert len({record_sha256 for _, record_sha256 in records}) == 2
    reopened = SqliteHumanReviewQuarantineRegistry(
        registry_root=root,
        mode=SqliteHumanReviewQuarantineRegistryOpenMode.OPEN_EXISTING,
        busy_timeout_milliseconds=_BUSY_TIMEOUT_MILLISECONDS,
        lock_timeout_milliseconds=_LOCK_TIMEOUT_MILLISECONDS,
    )
    for label in ("first-process", "second-process"):
        with pytest.raises(HumanReviewEvidenceQuarantined):
            reopened.require_not_quarantined(
                _evidence(label, scope_label=f"scope-{label}")
            )


def test_truncated_database_fails_closed_after_restart(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.append(
        _evidence("truncated"),
        kind=HumanReviewFailureKind.INVALID,
        reason_code="invalid-evidence",
    )
    registry.database_path.write_bytes(b"not-a-sqlite-database")

    with pytest.raises(HumanReviewQuarantineRegistryError):
        SqliteHumanReviewQuarantineRegistry(
            registry_root=tmp_path / "human-review-quarantine",
            mode=SqliteHumanReviewQuarantineRegistryOpenMode.OPEN_EXISTING,
            busy_timeout_milliseconds=_BUSY_TIMEOUT_MILLISECONDS,
            lock_timeout_milliseconds=_LOCK_TIMEOUT_MILLISECONDS,
        )


def test_symlink_root_is_rejected(tmp_path: Path) -> None:
    real_root = tmp_path / "real-registry"
    real_root.mkdir()
    symlink_root = tmp_path / "linked-registry"
    symlink_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(
        HumanReviewQuarantineRegistryError,
        match="real directory",
    ):
        SqliteHumanReviewQuarantineRegistry(
            registry_root=symlink_root,
            mode=SqliteHumanReviewQuarantineRegistryOpenMode.OPEN_EXISTING,
            busy_timeout_milliseconds=_BUSY_TIMEOUT_MILLISECONDS,
            lock_timeout_milliseconds=_LOCK_TIMEOUT_MILLISECONDS,
        )


def test_open_existing_never_creates_a_missing_registry(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing-registry"

    with pytest.raises(
        HumanReviewQuarantineRegistryError,
        match="established.*unavailable",
    ):
        SqliteHumanReviewQuarantineRegistry(
            registry_root=missing_root,
            mode=SqliteHumanReviewQuarantineRegistryOpenMode.OPEN_EXISTING,
            busy_timeout_milliseconds=_BUSY_TIMEOUT_MILLISECONDS,
            lock_timeout_milliseconds=_LOCK_TIMEOUT_MILLISECONDS,
        )

    assert not missing_root.exists()


def test_initialize_rejects_an_existing_root(tmp_path: Path) -> None:
    existing_root = tmp_path / "existing-registry"
    existing_root.mkdir()

    with pytest.raises(
        HumanReviewQuarantineRegistryError,
        match="cannot be initialized",
    ):
        SqliteHumanReviewQuarantineRegistry(
            registry_root=existing_root,
            mode=SqliteHumanReviewQuarantineRegistryOpenMode.INITIALIZE,
            busy_timeout_milliseconds=_BUSY_TIMEOUT_MILLISECONDS,
            lock_timeout_milliseconds=_LOCK_TIMEOUT_MILLISECONDS,
        )


def test_lock_substitution_during_acquisition_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry(tmp_path)
    lock_path = tmp_path / "human-review-quarantine" / (".human-review-quarantine.lock")
    real_flock = quarantine_module.fcntl.flock
    replaced = False

    def replacing_flock(descriptor: int, operation: int) -> None:
        nonlocal replaced
        if operation & quarantine_module.fcntl.LOCK_EX and not replaced:
            replacement = lock_path.with_suffix(".replacement")
            replacement.write_bytes(b"")
            os.replace(replacement, lock_path)
            replaced = True
        real_flock(descriptor, operation)

    monkeypatch.setattr(quarantine_module.fcntl, "flock", replacing_flock)

    with pytest.raises(
        HumanReviewQuarantineRegistryError,
        match="lock identity changed",
    ):
        registry.append(
            _evidence("split-lock"),
            kind=HumanReviewFailureKind.INVALID,
            reason_code="invalid-evidence",
        )


def test_interprocess_lock_acquisition_has_an_explicit_deadline(
    tmp_path: Path,
) -> None:
    root = tmp_path / "human-review-quarantine"
    _registry(tmp_path)
    context = get_context("spawn")
    ready_reader, ready_writer = context.Pipe(duplex=False)
    release_reader, release_writer = context.Pipe(duplex=False)
    process = context.Process(
        target=_hold_exclusive_lock,
        args=(
            str(root / ".human-review-quarantine.lock"),
            ready_writer,
            release_reader,
        ),
    )
    process.start()
    assert ready_reader.poll(5)
    assert ready_reader.recv_bytes() == b"locked"

    try:
        with pytest.raises(
            HumanReviewQuarantineRegistryError,
            match="lock acquisition timed out",
        ):
            SqliteHumanReviewQuarantineRegistry(
                registry_root=root,
                mode=SqliteHumanReviewQuarantineRegistryOpenMode.OPEN_EXISTING,
                busy_timeout_milliseconds=_BUSY_TIMEOUT_MILLISECONDS,
                lock_timeout_milliseconds=25,
            )
    finally:
        release_writer.send_bytes(b"release")
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    assert process.exitcode == 0

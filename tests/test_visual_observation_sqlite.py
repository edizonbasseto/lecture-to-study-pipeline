from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import sqlite3
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from multiprocessing import get_context
from multiprocessing.connection import Connection
from pathlib import Path

import pytest

from lecture_study.domain.semantic_observation import (
    AuthenticatedVisualObservationReceipt,
    GeneratedObservationIdentity,
    SourcePartitionKind,
    VisualFullFrameSelector,
)
from lecture_study.quality.visual_observation import (
    VisualObservationHmacAuthority,
    VisualObservationHmacKey,
    VisualObservationInvocationExpiration,
    VisualObservationReceiptRegistry,
    VisualObservationReceiptRequest,
    VisualObservationReceiptReservation,
    VisualObservationRegistryError,
    VisualObservationRegistryFailureKind,
)
from lecture_study.quality.visual_observation_sqlite import (
    SqliteVisualObservationReceiptRegistry,
    SqliteVisualObservationRegistryOpenMode,
)

_FRAME_SHA256 = "a" * 64
_OBSERVED_TEXT_SHA256 = "b" * 64
_KEY_MATERIAL = bytes(range(32))
_AUTHORITY_ID = "visual-observation-authority"
_BUSY_TIMEOUT_MILLISECONDS = 5_000
_LOCK_TIMEOUT_MILLISECONDS = 5_000


def _registry(
    root: Path,
    mode: SqliteVisualObservationRegistryOpenMode,
) -> SqliteVisualObservationReceiptRegistry:
    return SqliteVisualObservationReceiptRegistry(
        registry_root=root,
        mode=mode,
        busy_timeout_milliseconds=_BUSY_TIMEOUT_MILLISECONDS,
        lock_timeout_milliseconds=_LOCK_TIMEOUT_MILLISECONDS,
    )


def _authority(
    registry: VisualObservationReceiptRegistry,
    *,
    material: bytes = _KEY_MATERIAL,
    authority_id: str = _AUTHORITY_ID,
) -> VisualObservationHmacAuthority:
    return VisualObservationHmacAuthority(
        key=VisualObservationHmacKey(material),
        authority_id=authority_id,
        registry=registry,
    )


def _request(
    *,
    receipt_id: str = "receipt-001",
    invocation_id: str = "invocation-001",
) -> VisualObservationReceiptRequest:
    return VisualObservationReceiptRequest(
        schema_version=1,
        receipt_id=receipt_id,
        invocation_id=invocation_id,
        lecture_id="lecture-001",
        learning_unit_id="unit-001",
        source_span_id="span-001",
        original_frame_sha256=_FRAME_SHA256,
        selector=VisualFullFrameSelector(
            kind=SourcePartitionKind.VISUAL_FULL_FRAME,
            original_frame_sha256=_FRAME_SHA256,
        ),
        generated_observation_identity=GeneratedObservationIdentity(
            implementation_sha256="c" * 64,
            configuration_sha256="d" * 64,
        ),
        observed_text_sha256=_OBSERVED_TEXT_SHA256,
    )


def _reservation(
    receipt: AuthenticatedVisualObservationReceipt,
) -> VisualObservationReceiptReservation:
    return VisualObservationReceiptReservation(
        cryptographic_domain=receipt.cryptographic_domain,
        signature_purpose=receipt.signature_purpose,
        trust_key_id=receipt.trust_key_id,
        authority_id=receipt.authority_id,
        receipt_id=receipt.receipt_id,
        invocation_id=receipt.invocation_id,
        authentication_payload_sha256=hashlib.sha256(
            receipt.authentication_payload()
        ).hexdigest(),
    )


def _process_reserve(
    root_text: str,
    receipt_id: str,
    invocation_id: str,
) -> str:
    root = Path(root_text)
    registry = _registry(
        root,
        SqliteVisualObservationRegistryOpenMode.OPEN_EXISTING,
    )
    authority = _authority(registry)
    request = _request(receipt_id=receipt_id, invocation_id=invocation_id)
    try:
        authority.issue(request)
    except VisualObservationRegistryError:
        return "rejected"
    return "issued"


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


class _FailingConfirmationRegistry:
    def __init__(self, registry: VisualObservationReceiptRegistry) -> None:
        self._registry = registry

    def reserve_once(
        self,
        reservation: VisualObservationReceiptReservation,
    ) -> None:
        self._registry.reserve_once(reservation)

    def confirm_issued(
        self,
        reservation: VisualObservationReceiptReservation,
    ) -> None:
        raise VisualObservationRegistryError(
            "injected confirmation failure",
            kind=VisualObservationRegistryFailureKind.UNAVAILABLE,
        )

    def expire_invocation(
        self,
        expiration: VisualObservationInvocationExpiration,
    ) -> None:
        self._registry.expire_invocation(expiration)

    def require_issued(
        self,
        reservation: VisualObservationReceiptReservation,
    ) -> None:
        self._registry.require_issued(reservation)


def test_issuance_survives_registry_and_authority_reconstruction(
    tmp_path: Path,
) -> None:
    initialized = _registry(
        tmp_path,
        SqliteVisualObservationRegistryOpenMode.INITIALIZE,
    )
    issued = _authority(initialized).issue(_request())

    reopened = _registry(
        tmp_path,
        SqliteVisualObservationRegistryOpenMode.OPEN_EXISTING,
    )
    reconstructed = _authority(reopened)
    capability = reconstructed.verify(issued.receipt)

    assert reconstructed.require_current(capability) == issued.receipt
    with sqlite3.connect(reopened.database_path) as connection:
        states = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT state
                FROM visual_observation_registry_events
                ORDER BY sequence
                """
            )
        )
    assert states == ("reserved", "issued")
    assert len(tuple((tmp_path / "visual-observation-registry-anchors").iterdir())) == 2


def test_expiration_survives_registry_and_authority_reconstruction(
    tmp_path: Path,
) -> None:
    initialized = _registry(
        tmp_path,
        SqliteVisualObservationRegistryOpenMode.INITIALIZE,
    )
    authority = _authority(initialized)
    capability = authority.issue(_request())
    authority.expire_invocation(str(capability.receipt.invocation_id))

    with pytest.raises(VisualObservationRegistryError, match="expired"):
        authority.require_current(capability)

    reopened = _registry(
        tmp_path,
        SqliteVisualObservationRegistryOpenMode.OPEN_EXISTING,
    )
    with pytest.raises(VisualObservationRegistryError, match="expired"):
        reopened.require_issued(_reservation(capability.receipt))
    reconstructed = _authority(reopened)
    with pytest.raises(VisualObservationRegistryError, match="expired"):
        reconstructed.verify(capability.receipt)
    with pytest.raises(VisualObservationRegistryError, match="already reserved"):
        reconstructed.issue(_request())


@pytest.mark.parametrize(
    ("receipt_id", "invocation_id"),
    (
        ("receipt-001", "invocation-002"),
        ("receipt-002", "invocation-001"),
    ),
)
def test_duplicate_identifiers_are_rejected_across_registry_instances(
    tmp_path: Path,
    receipt_id: str,
    invocation_id: str,
) -> None:
    initialized = _registry(
        tmp_path,
        SqliteVisualObservationRegistryOpenMode.INITIALIZE,
    )
    _authority(initialized).issue(_request())
    reopened = _registry(
        tmp_path,
        SqliteVisualObservationRegistryOpenMode.OPEN_EXISTING,
    )

    with pytest.raises(VisualObservationRegistryError, match="already reserved"):
        _authority(reopened).issue(
            _request(receipt_id=receipt_id, invocation_id=invocation_id)
        )


def test_failed_confirmation_burns_both_identifiers(tmp_path: Path) -> None:
    initialized = _registry(
        tmp_path,
        SqliteVisualObservationRegistryOpenMode.INITIALIZE,
    )
    failing = _FailingConfirmationRegistry(initialized)

    with pytest.raises(VisualObservationRegistryError, match="injected"):
        _authority(failing).issue(_request())

    reopened = _registry(
        tmp_path,
        SqliteVisualObservationRegistryOpenMode.OPEN_EXISTING,
    )
    with pytest.raises(VisualObservationRegistryError, match="already reserved"):
        _authority(reopened).issue(_request())


def test_reserved_without_issued_state_cannot_verify_an_authentic_receipt(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    source_root = tmp_path_factory.mktemp("issued-source")
    source_registry = _registry(
        source_root,
        SqliteVisualObservationRegistryOpenMode.INITIALIZE,
    )
    receipt = _authority(source_registry).issue(_request()).receipt

    reserved_registry = _registry(
        tmp_path,
        SqliteVisualObservationRegistryOpenMode.INITIALIZE,
    )
    reserved_registry.reserve_once(_reservation(receipt))

    with pytest.raises(VisualObservationRegistryError, match="no confirmed"):
        _authority(reserved_registry).verify(receipt)


def test_sqlite_triggers_reject_update_and_delete(tmp_path: Path) -> None:
    registry = _registry(
        tmp_path,
        SqliteVisualObservationRegistryOpenMode.INITIALIZE,
    )
    _authority(registry).issue(_request())

    with sqlite3.connect(registry.database_path) as connection:
        for statement in (
            "UPDATE visual_observation_registry_events SET state = 'reserved'",
            "DELETE FROM visual_observation_registry_events",
            "UPDATE visual_observation_registry_metadata SET schema_version = 1",
            "DELETE FROM visual_observation_registry_metadata",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append-only|immutable"):
                connection.execute(statement)


def test_lock_replacement_while_waiting_fails_before_critical_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry(
        tmp_path,
        SqliteVisualObservationRegistryOpenMode.INITIALIZE,
    )
    authority = _authority(registry)
    capability = authority.issue(_request())
    lock_path = tmp_path / ".visual-observation-registry.lock"
    lock_descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    original_flock = fcntl.flock
    original_flock(lock_descriptor, fcntl.LOCK_EX)
    waiting_for_lock = threading.Event()
    lock_released = False
    executor = ThreadPoolExecutor(max_workers=1)

    def observed_flock(descriptor: int, operation: int) -> None:
        if operation & fcntl.LOCK_EX:
            waiting_for_lock.set()
        original_flock(descriptor, operation)

    monkeypatch.setattr(fcntl, "flock", observed_flock)
    try:
        future = executor.submit(authority.require_current, capability)
        assert waiting_for_lock.wait(timeout=5)
        lock_path.unlink()
        replacement_descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        os.close(replacement_descriptor)
        original_flock(lock_descriptor, fcntl.LOCK_UN)
        lock_released = True

        with pytest.raises(VisualObservationRegistryError, match="lock identity"):
            future.result(timeout=5)
    finally:
        if not lock_released:
            original_flock(lock_descriptor, fcntl.LOCK_UN)
        executor.shutdown(wait=True)
        os.close(lock_descriptor)


def test_interprocess_lock_acquisition_has_an_explicit_deadline(
    tmp_path: Path,
) -> None:
    root = tmp_path / "visual-observation-registry"
    root.mkdir()
    _registry(root, SqliteVisualObservationRegistryOpenMode.INITIALIZE)
    context = get_context("spawn")
    ready_reader, ready_writer = context.Pipe(duplex=False)
    release_reader, release_writer = context.Pipe(duplex=False)
    process = context.Process(
        target=_hold_exclusive_lock,
        args=(
            str(root / ".visual-observation-registry.lock"),
            ready_writer,
            release_reader,
        ),
    )
    process.start()
    assert ready_reader.poll(5)
    assert ready_reader.recv_bytes() == b"locked"

    try:
        with pytest.raises(
            VisualObservationRegistryError,
            match="lock acquisition timed out",
        ) as caught:
            SqliteVisualObservationReceiptRegistry(
                registry_root=root,
                mode=SqliteVisualObservationRegistryOpenMode.OPEN_EXISTING,
                busy_timeout_milliseconds=_BUSY_TIMEOUT_MILLISECONDS,
                lock_timeout_milliseconds=25,
            )
    finally:
        release_writer.send_bytes(b"release")
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    assert caught.value.kind is VisualObservationRegistryFailureKind.UNAVAILABLE
    assert process.exitcode == 0


def test_database_only_rollback_is_detected_by_external_anchors(
    tmp_path: Path,
) -> None:
    registry = _registry(
        tmp_path,
        SqliteVisualObservationRegistryOpenMode.INITIALIZE,
    )
    authority = _authority(registry)
    authority.issue(_request())
    backup_path = tmp_path / "database-before-second-issuance.sqlite3"
    shutil.copyfile(registry.database_path, backup_path)
    authority.issue(_request(receipt_id="receipt-002", invocation_id="invocation-002"))
    with sqlite3.connect(registry.database_path) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copyfile(backup_path, registry.database_path)

    with pytest.raises(
        VisualObservationRegistryError,
        match="anchor set does not match",
    ):
        _registry(
            tmp_path,
            SqliteVisualObservationRegistryOpenMode.OPEN_EXISTING,
        )


def test_schema_tampering_fails_closed(tmp_path: Path) -> None:
    registry = _registry(
        tmp_path,
        SqliteVisualObservationRegistryOpenMode.INITIALIZE,
    )
    with sqlite3.connect(registry.database_path) as connection:
        connection.execute("DROP TRIGGER visual_observation_registry_event_no_update")

    with pytest.raises(VisualObservationRegistryError, match="schema is invalid"):
        _registry(
            tmp_path,
            SqliteVisualObservationRegistryOpenMode.OPEN_EXISTING,
        )


def test_cross_process_joint_reservation_has_exactly_one_winner(
    tmp_path: Path,
) -> None:
    _registry(
        tmp_path,
        SqliteVisualObservationRegistryOpenMode.INITIALIZE,
    )
    with ProcessPoolExecutor(max_workers=2) as executor:
        results = tuple(
            future.result()
            for future in (
                executor.submit(
                    _process_reserve,
                    str(tmp_path),
                    "receipt-shared",
                    "invocation-first",
                ),
                executor.submit(
                    _process_reserve,
                    str(tmp_path),
                    "receipt-shared",
                    "invocation-second",
                ),
            )
        )

    assert sorted(results) == ["issued", "rejected"]


def test_same_identifiers_are_independent_across_authority_namespaces(
    tmp_path: Path,
) -> None:
    registry = _registry(
        tmp_path,
        SqliteVisualObservationRegistryOpenMode.INITIALIZE,
    )
    default_authority = _authority(registry)
    named_authority = _authority(
        registry,
        authority_id="other-visual-authority",
    )
    keyed_authority = _authority(registry, material=bytes(range(1, 33)))
    default_capability = default_authority.issue(_request())
    named_capability = named_authority.issue(_request())
    keyed_capability = keyed_authority.issue(_request())

    default_authority.expire_invocation("invocation-001")

    with pytest.raises(VisualObservationRegistryError, match="expired"):
        default_authority.require_current(default_capability)
    assert named_authority.require_current(named_capability) == named_capability.receipt
    assert keyed_authority.require_current(keyed_capability) == keyed_capability.receipt

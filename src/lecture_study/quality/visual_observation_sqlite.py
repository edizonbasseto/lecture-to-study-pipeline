"""Persistent append-only registry for visual-observation identities."""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable, Iterator, Literal, Sequence

from lecture_study.quality.visual_observation import (
    VisualObservationInvocationExpiration,
    VisualObservationReceiptReservation,
    VisualObservationRegistryError,
    VisualObservationRegistryFailureKind,
)

_DATABASE_FILENAME = "visual-observation-registry.sqlite3"
_LOCK_FILENAME = ".visual-observation-registry.lock"
_ANCHOR_DIRECTORY = "visual-observation-registry-anchors"
_ANCHOR_FILENAME_PATTERN = re.compile(r"^[0-9]{20}\.anchor$")
_SCHEMA_VERSION = 2
_GENESIS_SHA256 = hashlib.sha256(
    b"lecture-study:visual-observation:registry-genesis:v2"
).hexdigest()
_EVENT_HASH_CONTEXT = b"lecture-study:visual-observation:registry-event:v2\0"
_RESERVED: Literal["reserved"] = "reserved"
_ISSUED: Literal["issued"] = "issued"
_EXPIRED: Literal["expired"] = "expired"
_RegistryState = Literal["reserved", "issued", "expired"]

_METADATA_TABLE_SQL = f"""
CREATE TABLE visual_observation_registry_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL CHECK (schema_version = {_SCHEMA_VERSION}),
    schema_sha256 TEXT NOT NULL
        CHECK (
            length(schema_sha256) = 64
            AND schema_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
    genesis_sha256 TEXT NOT NULL
        CHECK (
            length(genesis_sha256) = 64
            AND genesis_sha256 NOT GLOB '*[^0-9a-f]*'
        )
)
"""
_EVENT_TABLE_SQL = """
CREATE TABLE visual_observation_registry_events (
    sequence INTEGER PRIMARY KEY CHECK (sequence > 0),
    state TEXT NOT NULL CHECK (state IN ('reserved', 'issued', 'expired')),
    cryptographic_domain TEXT NOT NULL,
    signature_purpose TEXT NOT NULL,
    trust_key_id TEXT NOT NULL
        CHECK (
            length(trust_key_id) = 64
            AND trust_key_id NOT GLOB '*[^0-9a-f]*'
        ),
    authority_id TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    invocation_id TEXT NOT NULL,
    authentication_payload_sha256 TEXT NOT NULL
        CHECK (
            length(authentication_payload_sha256) = 64
            AND authentication_payload_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
    previous_entry_sha256 TEXT NOT NULL
        CHECK (
            length(previous_entry_sha256) = 64
            AND previous_entry_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
    entry_sha256 TEXT NOT NULL UNIQUE
        CHECK (
            length(entry_sha256) = 64
            AND entry_sha256 NOT GLOB '*[^0-9a-f]*'
        )
)
"""
_RECEIPT_STATE_INDEX_SQL = """
CREATE UNIQUE INDEX visual_observation_registry_receipt_state_unique
ON visual_observation_registry_events (
    cryptographic_domain,
    signature_purpose,
    trust_key_id,
    authority_id,
    receipt_id,
    state
)
"""
_INVOCATION_STATE_INDEX_SQL = """
CREATE UNIQUE INDEX visual_observation_registry_invocation_state_unique
ON visual_observation_registry_events (
    cryptographic_domain,
    signature_purpose,
    trust_key_id,
    authority_id,
    invocation_id,
    state
)
"""
_EVENT_NO_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER visual_observation_registry_event_no_update
BEFORE UPDATE ON visual_observation_registry_events
BEGIN
    SELECT RAISE(ABORT, 'visual-observation registry events are append-only');
END
"""
_EVENT_NO_DELETE_TRIGGER_SQL = """
CREATE TRIGGER visual_observation_registry_event_no_delete
BEFORE DELETE ON visual_observation_registry_events
BEGIN
    SELECT RAISE(ABORT, 'visual-observation registry events are append-only');
END
"""
_METADATA_NO_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER visual_observation_registry_metadata_no_update
BEFORE UPDATE ON visual_observation_registry_metadata
BEGIN
    SELECT RAISE(ABORT, 'visual-observation registry metadata is immutable');
END
"""
_METADATA_NO_DELETE_TRIGGER_SQL = """
CREATE TRIGGER visual_observation_registry_metadata_no_delete
BEFORE DELETE ON visual_observation_registry_metadata
BEGIN
    SELECT RAISE(ABORT, 'visual-observation registry metadata is immutable');
END
"""
_CHAIN_TRIGGER_SQL = f"""
CREATE TRIGGER visual_observation_registry_event_chain
BEFORE INSERT ON visual_observation_registry_events
WHEN
    NEW.sequence != COALESCE(
        (SELECT MAX(sequence) + 1 FROM visual_observation_registry_events),
        1
    )
    OR NEW.previous_entry_sha256 != COALESCE(
        (
            SELECT entry_sha256
            FROM visual_observation_registry_events
            ORDER BY sequence DESC
            LIMIT 1
        ),
        '{_GENESIS_SHA256}'
    )
BEGIN
    SELECT RAISE(ABORT, 'visual-observation registry chain is discontinuous');
END
"""
_ISSUED_REQUIRES_RESERVED_TRIGGER_SQL = """
CREATE TRIGGER visual_observation_registry_issued_requires_reserved
BEFORE INSERT ON visual_observation_registry_events
WHEN
    NEW.state = 'issued'
    AND NOT EXISTS (
        SELECT 1
        FROM visual_observation_registry_events
        WHERE state = 'reserved'
          AND cryptographic_domain = NEW.cryptographic_domain
          AND signature_purpose = NEW.signature_purpose
          AND trust_key_id = NEW.trust_key_id
          AND authority_id = NEW.authority_id
          AND receipt_id = NEW.receipt_id
          AND invocation_id = NEW.invocation_id
          AND authentication_payload_sha256 =
              NEW.authentication_payload_sha256
    )
BEGIN
    SELECT RAISE(
        ABORT,
        'visual-observation issuance requires its exact reservation'
    );
END
"""
_EXPIRED_REQUIRES_ISSUED_TRIGGER_SQL = """
CREATE TRIGGER visual_observation_registry_expired_requires_issued
BEFORE INSERT ON visual_observation_registry_events
WHEN
    NEW.state = 'expired'
    AND NOT EXISTS (
        SELECT 1
        FROM visual_observation_registry_events
        WHERE state = 'issued'
          AND cryptographic_domain = NEW.cryptographic_domain
          AND signature_purpose = NEW.signature_purpose
          AND trust_key_id = NEW.trust_key_id
          AND authority_id = NEW.authority_id
          AND receipt_id = NEW.receipt_id
          AND invocation_id = NEW.invocation_id
          AND authentication_payload_sha256 =
              NEW.authentication_payload_sha256
    )
BEGIN
    SELECT RAISE(
        ABORT,
        'visual-observation expiration requires its exact issuance'
    );
END
"""

_SCHEMA_STATEMENTS = (
    _METADATA_TABLE_SQL,
    _EVENT_TABLE_SQL,
    _RECEIPT_STATE_INDEX_SQL,
    _INVOCATION_STATE_INDEX_SQL,
    _EVENT_NO_UPDATE_TRIGGER_SQL,
    _EVENT_NO_DELETE_TRIGGER_SQL,
    _METADATA_NO_UPDATE_TRIGGER_SQL,
    _METADATA_NO_DELETE_TRIGGER_SQL,
    _CHAIN_TRIGGER_SQL,
    _ISSUED_REQUIRES_RESERVED_TRIGGER_SQL,
    _EXPIRED_REQUIRES_ISSUED_TRIGGER_SQL,
)


def _normalized_sql(statement: str) -> str:
    return " ".join(statement.split()).rstrip(";")


_SCHEMA_SHA256 = hashlib.sha256(
    "\n".join(_normalized_sql(statement) for statement in _SCHEMA_STATEMENTS).encode(
        "utf-8"
    )
).hexdigest()
_EXPECTED_SCHEMA_OBJECTS = {
    "visual_observation_registry_metadata": (
        "table",
        _normalized_sql(_METADATA_TABLE_SQL),
    ),
    "visual_observation_registry_events": (
        "table",
        _normalized_sql(_EVENT_TABLE_SQL),
    ),
    "visual_observation_registry_receipt_state_unique": (
        "index",
        _normalized_sql(_RECEIPT_STATE_INDEX_SQL),
    ),
    "visual_observation_registry_invocation_state_unique": (
        "index",
        _normalized_sql(_INVOCATION_STATE_INDEX_SQL),
    ),
    "visual_observation_registry_event_no_update": (
        "trigger",
        _normalized_sql(_EVENT_NO_UPDATE_TRIGGER_SQL),
    ),
    "visual_observation_registry_event_no_delete": (
        "trigger",
        _normalized_sql(_EVENT_NO_DELETE_TRIGGER_SQL),
    ),
    "visual_observation_registry_metadata_no_update": (
        "trigger",
        _normalized_sql(_METADATA_NO_UPDATE_TRIGGER_SQL),
    ),
    "visual_observation_registry_metadata_no_delete": (
        "trigger",
        _normalized_sql(_METADATA_NO_DELETE_TRIGGER_SQL),
    ),
    "visual_observation_registry_event_chain": (
        "trigger",
        _normalized_sql(_CHAIN_TRIGGER_SQL),
    ),
    "visual_observation_registry_issued_requires_reserved": (
        "trigger",
        _normalized_sql(_ISSUED_REQUIRES_RESERVED_TRIGGER_SQL),
    ),
    "visual_observation_registry_expired_requires_issued": (
        "trigger",
        _normalized_sql(_EXPIRED_REQUIRES_ISSUED_TRIGGER_SQL),
    ),
}


class SqliteVisualObservationRegistryOpenMode(StrEnum):
    """Explicit lifecycle of one persistent registry root."""

    INITIALIZE = "initialize"
    OPEN_EXISTING = "open_existing"


@dataclass(frozen=True, slots=True)
class _RegistryEvent:
    sequence: int
    state: _RegistryState
    reservation: VisualObservationReceiptReservation
    previous_entry_sha256: str
    entry_sha256: str


class _SqliteVisualObservationRegistryStore:
    """Own the immutable SQLite, hash-chain, anchor, and locking storage unit.

    External anchors detect rollback of the database alone. A coordinated rollback
    of the complete explicitly supplied root is outside local detection.
    """

    def __init__(
        self,
        *,
        registry_root: Path,
        mode: SqliteVisualObservationRegistryOpenMode,
        busy_timeout_milliseconds: int,
        lock_timeout_milliseconds: int,
    ) -> None:
        self._root = self._validated_root(registry_root)
        self._root_identity = self._path_identity(self._root, require_directory=True)
        self._busy_timeout_milliseconds = self._validated_busy_timeout(
            busy_timeout_milliseconds
        )
        self._lock_timeout_seconds = (
            self._validated_lock_timeout(lock_timeout_milliseconds) / 1_000
        )
        if not isinstance(mode, SqliteVisualObservationRegistryOpenMode):
            raise TypeError("mode must be a SqliteVisualObservationRegistryOpenMode")
        if mode is SqliteVisualObservationRegistryOpenMode.INITIALIZE:
            self._initialize_root()
        else:
            self._require_existing_layout()
        self._lock_identity = self._path_identity(self._lock_path)
        self._anchor_directory_identity = self._path_identity(
            self._anchor_directory,
            require_directory=True,
        )
        self._database_identity = self._path_identity(self._database_path)
        with self._exclusive_root_lock():
            with self._connection() as connection:
                self._validate_critical_state(connection)

    @property
    def database_path(self) -> Path:
        """Return the exact database path inside the supplied root."""

        return self._database_path

    def reserve_once(
        self,
        reservation: VisualObservationReceiptReservation,
    ) -> None:
        validated = self._validated_reservation(reservation)
        self._append_event(_RESERVED, validated)

    def confirm_issued(
        self,
        reservation: VisualObservationReceiptReservation,
    ) -> None:
        validated = self._validated_reservation(reservation)
        self._append_event(_ISSUED, validated)

    def expire_invocation(
        self,
        expiration: VisualObservationInvocationExpiration,
    ) -> None:
        validated = self._validated_expiration(expiration)
        self._append_resolved_event(
            _EXPIRED,
            lambda events: self._issued_reservation_for_expiration(
                events,
                validated,
            ),
        )

    def require_issued(
        self,
        reservation: VisualObservationReceiptReservation,
    ) -> None:
        validated = self._validated_reservation(reservation)
        with self._exclusive_root_lock():
            with self._connection() as connection:
                try:
                    connection.execute("BEGIN")
                    events = self._validate_critical_state(connection)
                    self._require_exact_issued(events, validated)
                    connection.commit()
                except VisualObservationRegistryError:
                    self._rollback(connection)
                    raise
                except (OSError, sqlite3.Error) as error:
                    self._rollback(connection)
                    raise self._unavailable(
                        "visual-observation registry could not revalidate issuance"
                    ) from error

    @property
    def _database_path(self) -> Path:
        return self._root / _DATABASE_FILENAME

    @property
    def _lock_path(self) -> Path:
        return self._root / _LOCK_FILENAME

    @property
    def _anchor_directory(self) -> Path:
        return self._root / _ANCHOR_DIRECTORY

    def _initialize_root(self) -> None:
        try:
            self._create_lock_file()
        except OSError as error:
            raise self._unavailable(
                "visual-observation registry lock initialization failed"
            ) from error
        with self._exclusive_root_lock(validate_stored_identity=False):
            self._create_anchor_directory()
            self._create_database_file()
            self._lock_identity = self._path_identity(self._lock_path)
            self._anchor_directory_identity = self._path_identity(
                self._anchor_directory,
                require_directory=True,
            )
            self._database_identity = self._path_identity(self._database_path)
            with self._connection(
                require_stored_identity=False,
                allow_delete_journal_mode=True,
            ) as connection:
                try:
                    journal_mode = connection.execute(
                        "PRAGMA journal_mode = WAL"
                    ).fetchone()
                    if journal_mode is None or str(journal_mode[0]).lower() != "wal":
                        raise self._unavailable(
                            "visual-observation registry could not enable WAL"
                        )
                    connection.execute("BEGIN IMMEDIATE")
                    for statement in _SCHEMA_STATEMENTS:
                        connection.execute(statement)
                    connection.execute(
                        """
                        INSERT INTO visual_observation_registry_metadata (
                            singleton,
                            schema_version,
                            schema_sha256,
                            genesis_sha256
                        ) VALUES (1, ?, ?, ?)
                        """,
                        (_SCHEMA_VERSION, _SCHEMA_SHA256, _GENESIS_SHA256),
                    )
                    connection.commit()
                    self._checkpoint(connection)
                    self._fsync_database()
                    self._validate_critical_state(connection)
                except VisualObservationRegistryError:
                    self._rollback(connection)
                    raise
                except (OSError, sqlite3.Error) as error:
                    self._rollback(connection)
                    raise self._unavailable(
                        "visual-observation registry initialization failed"
                    ) from error

    def _require_existing_layout(self) -> None:
        for path, require_directory in (
            (self._lock_path, False),
            (self._anchor_directory, True),
            (self._database_path, False),
        ):
            self._path_identity(path, require_directory=require_directory)

    def _append_event(
        self,
        state: _RegistryState,
        reservation: VisualObservationReceiptReservation,
    ) -> None:
        self._append_resolved_event(state, lambda _events: reservation)

    def _append_resolved_event(
        self,
        state: _RegistryState,
        resolve_reservation: Callable[
            [Sequence[_RegistryEvent]],
            VisualObservationReceiptReservation,
        ],
    ) -> None:
        with self._exclusive_root_lock():
            with self._connection() as connection:
                committed_event: _RegistryEvent | None = None
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    events = self._validate_critical_state(connection)
                    reservation = resolve_reservation(events)
                    self._require_append_allowed(events, state, reservation)
                    committed_event = self._next_event(events, state, reservation)
                    self._insert_event(connection, committed_event)
                    self._validate_database_chain(connection)
                    connection.commit()
                    self._checkpoint(connection)
                    self._fsync_database()
                    self._append_anchor(committed_event)
                    self._validate_critical_state(connection)
                except VisualObservationRegistryError:
                    self._rollback(connection)
                    raise
                except sqlite3.IntegrityError as error:
                    self._rollback(connection)
                    kind = (
                        VisualObservationRegistryFailureKind.CONFLICT
                        if state in {_RESERVED, _EXPIRED}
                        else VisualObservationRegistryFailureKind.MISMATCH
                    )
                    raise VisualObservationRegistryError(
                        "visual-observation registry rejected the append-only event",
                        kind=kind,
                    ) from error
                except (OSError, sqlite3.Error) as error:
                    self._rollback(connection)
                    raise self._unavailable(
                        "visual-observation registry could not persist its event"
                    ) from error
                if committed_event is None:
                    raise AssertionError(
                        "a committed registry append must have an event"
                    )

    def _require_append_allowed(
        self,
        events: Sequence[_RegistryEvent],
        state: _RegistryState,
        reservation: VisualObservationReceiptReservation,
    ) -> None:
        if state == _RESERVED:
            self._require_identifiers_unused(events, reservation)
            return
        exact_reserved = any(
            event.state == _RESERVED and event.reservation == reservation
            for event in events
        )
        exact_issued = any(
            event.state == _ISSUED and event.reservation == reservation
            for event in events
        )
        if state == _ISSUED:
            if not exact_reserved:
                raise VisualObservationRegistryError(
                    "visual-observation issuance has no exact reservation",
                    kind=VisualObservationRegistryFailureKind.MISMATCH,
                )
            if exact_issued:
                raise VisualObservationRegistryError(
                    "visual-observation receipt has already been confirmed",
                    kind=VisualObservationRegistryFailureKind.CONFLICT,
                )
            return
        if not exact_issued:
            raise VisualObservationRegistryError(
                "visual-observation expiration has no exact issuance",
                kind=VisualObservationRegistryFailureKind.MISMATCH,
            )
        if any(
            event.state == _EXPIRED and event.reservation == reservation
            for event in events
        ):
            raise VisualObservationRegistryError(
                "visual-observation invocation has already expired",
                kind=VisualObservationRegistryFailureKind.CONFLICT,
            )

    def _issued_reservation_for_expiration(
        self,
        events: Sequence[_RegistryEvent],
        expiration: VisualObservationInvocationExpiration,
    ) -> VisualObservationReceiptReservation:
        issued_reservations = tuple(
            event.reservation
            for event in events
            if event.state == _ISSUED
            and self._matches_expiration(event.reservation, expiration)
        )
        if len(issued_reservations) != 1:
            raise VisualObservationRegistryError(
                "visual-observation expiration has no issued invocation",
                kind=VisualObservationRegistryFailureKind.MISSING,
            )
        return issued_reservations[0]

    def _require_identifiers_unused(
        self,
        events: Sequence[_RegistryEvent],
        reservation: VisualObservationReceiptReservation,
    ) -> None:
        for event in events:
            if event.state != _RESERVED:
                continue
            if self._same_scope(event.reservation, reservation) and (
                event.reservation.receipt_id == reservation.receipt_id
                or event.reservation.invocation_id == reservation.invocation_id
            ):
                raise VisualObservationRegistryError(
                    "visual-observation receipt or invocation ID is already reserved",
                    kind=VisualObservationRegistryFailureKind.CONFLICT,
                )

    def _require_exact_issued(
        self,
        events: Sequence[_RegistryEvent],
        reservation: VisualObservationReceiptReservation,
    ) -> None:
        if any(
            event.state == _EXPIRED and event.reservation == reservation
            for event in events
        ):
            raise VisualObservationRegistryError(
                "visual-observation invocation has expired",
                kind=VisualObservationRegistryFailureKind.MISSING,
            )
        if any(
            event.state == _ISSUED and event.reservation == reservation
            for event in events
        ):
            return
        if any(
            event.state == _RESERVED and event.reservation == reservation
            for event in events
        ):
            raise VisualObservationRegistryError(
                "visual-observation receipt has no confirmed registry issuance",
                kind=VisualObservationRegistryFailureKind.MISSING,
            )
        if any(
            self._same_scope(event.reservation, reservation)
            and (
                event.reservation.receipt_id == reservation.receipt_id
                or event.reservation.invocation_id == reservation.invocation_id
            )
            for event in events
        ):
            raise VisualObservationRegistryError(
                "visual-observation registry identity does not match its issuance",
                kind=VisualObservationRegistryFailureKind.MISMATCH,
            )
        raise VisualObservationRegistryError(
            "visual-observation receipt has no confirmed registry issuance",
            kind=VisualObservationRegistryFailureKind.MISSING,
        )

    @staticmethod
    def _same_scope(
        first: VisualObservationReceiptReservation,
        second: VisualObservationReceiptReservation,
    ) -> bool:
        return (
            first.cryptographic_domain == second.cryptographic_domain
            and first.signature_purpose == second.signature_purpose
            and first.trust_key_id == second.trust_key_id
            and first.authority_id == second.authority_id
        )

    @staticmethod
    def _matches_expiration(
        reservation: VisualObservationReceiptReservation,
        expiration: VisualObservationInvocationExpiration,
    ) -> bool:
        return (
            reservation.cryptographic_domain == expiration.cryptographic_domain
            and reservation.signature_purpose == expiration.signature_purpose
            and reservation.trust_key_id == expiration.trust_key_id
            and reservation.authority_id == expiration.authority_id
            and reservation.invocation_id == expiration.invocation_id
        )

    def _next_event(
        self,
        events: Sequence[_RegistryEvent],
        state: _RegistryState,
        reservation: VisualObservationReceiptReservation,
    ) -> _RegistryEvent:
        sequence = len(events) + 1
        previous_entry_sha256 = events[-1].entry_sha256 if events else _GENESIS_SHA256
        entry_sha256 = self._event_sha256(
            sequence=sequence,
            state=state,
            reservation=reservation,
            previous_entry_sha256=previous_entry_sha256,
        )
        return _RegistryEvent(
            sequence=sequence,
            state=state,
            reservation=reservation,
            previous_entry_sha256=previous_entry_sha256,
            entry_sha256=entry_sha256,
        )

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        event: _RegistryEvent,
    ) -> None:
        reservation = event.reservation
        connection.execute(
            """
            INSERT INTO visual_observation_registry_events (
                sequence,
                state,
                cryptographic_domain,
                signature_purpose,
                trust_key_id,
                authority_id,
                receipt_id,
                invocation_id,
                authentication_payload_sha256,
                previous_entry_sha256,
                entry_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.sequence,
                event.state,
                reservation.cryptographic_domain,
                reservation.signature_purpose,
                reservation.trust_key_id,
                str(reservation.authority_id),
                str(reservation.receipt_id),
                str(reservation.invocation_id),
                reservation.authentication_payload_sha256,
                event.previous_entry_sha256,
                event.entry_sha256,
            ),
        )

    def _validate_critical_state(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[_RegistryEvent, ...]:
        try:
            self._validate_quick_check(connection)
            self._validate_schema(connection)
            events = self._validate_database_chain(connection)
            self._validate_anchors(events)
        except VisualObservationRegistryError:
            raise
        except (OSError, TypeError, ValueError, sqlite3.Error) as error:
            raise self._unavailable(
                "visual-observation registry integrity validation failed"
            ) from error
        return events

    @staticmethod
    def _validate_quick_check(connection: sqlite3.Connection) -> None:
        rows = tuple(
            str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall()
        )
        if rows != ("ok",):
            raise VisualObservationRegistryError(
                "visual-observation registry quick_check failed",
                kind=VisualObservationRegistryFailureKind.UNAVAILABLE,
            )

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        metadata = connection.execute(
            """
            SELECT schema_version, schema_sha256, genesis_sha256
            FROM visual_observation_registry_metadata
            WHERE singleton = 1
            """
        ).fetchall()
        if metadata != [(_SCHEMA_VERSION, _SCHEMA_SHA256, _GENESIS_SHA256)]:
            raise VisualObservationRegistryError(
                "visual-observation registry metadata is invalid",
                kind=VisualObservationRegistryFailureKind.UNAVAILABLE,
            )
        observed_rows = connection.execute(
            """
            SELECT type, name, sql
            FROM sqlite_master
            WHERE name GLOB 'visual_observation_registry_*'
              AND sql IS NOT NULL
            """
        ).fetchall()
        observed = {
            str(name): (str(object_type), _normalized_sql(str(sql)))
            for object_type, name, sql in observed_rows
        }
        if observed != _EXPECTED_SCHEMA_OBJECTS:
            raise VisualObservationRegistryError(
                "visual-observation registry schema is invalid",
                kind=VisualObservationRegistryFailureKind.UNAVAILABLE,
            )

    def _validate_database_chain(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[_RegistryEvent, ...]:
        rows = connection.execute(
            """
            SELECT
                sequence,
                state,
                cryptographic_domain,
                signature_purpose,
                trust_key_id,
                authority_id,
                receipt_id,
                invocation_id,
                authentication_payload_sha256,
                previous_entry_sha256,
                entry_sha256
            FROM visual_observation_registry_events
            ORDER BY sequence
            """
        ).fetchall()
        events: list[_RegistryEvent] = []
        reserved_receipts: set[tuple[str, ...]] = set()
        reserved_invocations: set[tuple[str, ...]] = set()
        issued_reservations: set[VisualObservationReceiptReservation] = set()
        expired_reservations: set[VisualObservationReceiptReservation] = set()
        previous_entry_sha256 = _GENESIS_SHA256
        for expected_sequence, row in enumerate(rows, start=1):
            try:
                event = self._event_from_row(row)
            except VisualObservationRegistryError:
                raise
            except (TypeError, ValueError) as error:
                raise self._chain_error(
                    "registry event structure is invalid"
                ) from error
            if event.sequence != expected_sequence:
                raise self._chain_error("registry event sequence is discontinuous")
            if not hmac.compare_digest(
                event.previous_entry_sha256,
                previous_entry_sha256,
            ):
                raise self._chain_error("registry previous-event hash is invalid")
            expected_hash = self._event_sha256(
                sequence=event.sequence,
                state=event.state,
                reservation=event.reservation,
                previous_entry_sha256=event.previous_entry_sha256,
            )
            if not hmac.compare_digest(event.entry_sha256, expected_hash):
                raise self._chain_error("registry event hash is invalid")
            receipt_key = self._receipt_key(event.reservation)
            invocation_key = self._invocation_key(event.reservation)
            if event.state == _RESERVED:
                if (
                    receipt_key in reserved_receipts
                    or invocation_key in reserved_invocations
                ):
                    raise self._chain_error("registry reservation identity is reused")
                reserved_receipts.add(receipt_key)
                reserved_invocations.add(invocation_key)
            elif event.state == _ISSUED:
                if (
                    receipt_key not in reserved_receipts
                    or invocation_key not in reserved_invocations
                    or event.reservation in issued_reservations
                    or not any(
                        prior.state == _RESERVED
                        and prior.reservation == event.reservation
                        for prior in events
                    )
                ):
                    raise self._chain_error(
                        "registry issuance has no exact reservation"
                    )
                issued_reservations.add(event.reservation)
            else:
                if (
                    event.reservation not in issued_reservations
                    or event.reservation in expired_reservations
                ):
                    raise self._chain_error("registry expiration has no exact issuance")
                expired_reservations.add(event.reservation)
            events.append(event)
            previous_entry_sha256 = event.entry_sha256
        return tuple(events)

    @staticmethod
    def _event_from_row(row: Sequence[object]) -> _RegistryEvent:
        (
            sequence,
            state,
            cryptographic_domain,
            signature_purpose,
            trust_key_id,
            authority_id,
            receipt_id,
            invocation_id,
            authentication_payload_sha256,
            previous_entry_sha256,
            entry_sha256,
        ) = row
        state_value = str(state)
        if state_value == _RESERVED:
            validated_state: _RegistryState = _RESERVED
        elif state_value == _ISSUED:
            validated_state = _ISSUED
        elif state_value == _EXPIRED:
            validated_state = _EXPIRED
        else:
            raise VisualObservationRegistryError(
                "visual-observation registry event state is invalid",
                kind=VisualObservationRegistryFailureKind.UNAVAILABLE,
            )
        reservation = VisualObservationReceiptReservation.model_validate(
            {
                "cryptographic_domain": str(cryptographic_domain),
                "signature_purpose": str(signature_purpose),
                "trust_key_id": str(trust_key_id),
                "authority_id": str(authority_id),
                "receipt_id": str(receipt_id),
                "invocation_id": str(invocation_id),
                "authentication_payload_sha256": str(authentication_payload_sha256),
            }
        )
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise VisualObservationRegistryError(
                "visual-observation registry sequence is invalid",
                kind=VisualObservationRegistryFailureKind.UNAVAILABLE,
            )
        return _RegistryEvent(
            sequence=sequence,
            state=validated_state,
            reservation=reservation,
            previous_entry_sha256=str(previous_entry_sha256),
            entry_sha256=str(entry_sha256),
        )

    @staticmethod
    def _event_sha256(
        *,
        sequence: int,
        state: _RegistryState,
        reservation: VisualObservationReceiptReservation,
        previous_entry_sha256: str,
    ) -> str:
        payload = {
            "authentication_payload_sha256": (
                reservation.authentication_payload_sha256
            ),
            "authority_id": str(reservation.authority_id),
            "cryptographic_domain": reservation.cryptographic_domain,
            "invocation_id": str(reservation.invocation_id),
            "previous_entry_sha256": previous_entry_sha256,
            "receipt_id": str(reservation.receipt_id),
            "sequence": sequence,
            "signature_purpose": reservation.signature_purpose,
            "state": state,
            "trust_key_id": reservation.trust_key_id,
        }
        canonical = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(_EVENT_HASH_CONTEXT + canonical).hexdigest()

    def _validate_anchors(self, events: Sequence[_RegistryEvent]) -> None:
        with self._anchor_directory_descriptor() as directory_descriptor:
            observed_names = set(os.listdir(directory_descriptor))
            expected_names = {self._anchor_filename(event.sequence) for event in events}
            if observed_names != expected_names or any(
                _ANCHOR_FILENAME_PATTERN.fullmatch(name) is None
                for name in observed_names
            ):
                raise self._chain_error(
                    "registry anchor set does not match the database chain"
                )
            for event in events:
                observed = self._read_anchor(directory_descriptor, event.sequence)
                expected = self._anchor_bytes(event)
                if not hmac.compare_digest(observed, expected):
                    raise self._chain_error(
                        "registry anchor does not match its database event"
                    )

    def _append_anchor(self, event: _RegistryEvent) -> None:
        with self._anchor_directory_descriptor() as directory_descriptor:
            descriptor = os.open(
                self._anchor_filename(event.sequence),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o400,
                dir_fd=directory_descriptor,
            )
            try:
                self._write_all(descriptor, self._anchor_bytes(event))
                os.fchmod(descriptor, 0o400)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.fsync(directory_descriptor)

    def _read_anchor(self, directory_descriptor: int, sequence: int) -> bytes:
        descriptor = os.open(
            self._anchor_filename(sequence),
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_mode & 0o222
                or metadata.st_size > 512
            ):
                raise self._chain_error("registry anchor metadata is unsafe")
            return os.read(descriptor, 513)
        finally:
            os.close(descriptor)

    @staticmethod
    def _anchor_filename(sequence: int) -> str:
        return f"{sequence:020d}.anchor"

    @staticmethod
    def _anchor_bytes(event: _RegistryEvent) -> bytes:
        return json.dumps(
            {
                "entry_sha256": event.entry_sha256,
                "previous_entry_sha256": event.previous_entry_sha256,
                "sequence": event.sequence,
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> None:
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise OSError("registry anchor write made no progress")
            written += count

    @contextmanager
    def _exclusive_root_lock(
        self,
        *,
        validate_stored_identity: bool = True,
    ) -> Iterator[None]:
        deadline = time.monotonic() + self._lock_timeout_seconds
        self._require_root_identity()
        try:
            descriptor = os.open(
                self._lock_path,
                os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
            )
        except OSError as error:
            raise self._unavailable(
                "visual-observation registry root lock is unavailable"
            ) from error
        try:
            self._require_lock_identity(
                descriptor,
                validate_stored_identity=validate_stored_identity,
            )
            self._acquire_exclusive_lock(descriptor, deadline)
            self._require_root_identity()
            self._require_lock_identity(
                descriptor,
                validate_stored_identity=validate_stored_identity,
            )
            yield
        except OSError as error:
            raise self._unavailable(
                "visual-observation registry root lock is unavailable"
            ) from error
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _acquire_exclusive_lock(self, descriptor: int, deadline: float) -> None:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError as error:
                remaining_seconds = deadline - time.monotonic()
                if remaining_seconds <= 0:
                    raise self._unavailable(
                        "visual-observation registry lock acquisition timed out"
                    ) from error
                time.sleep(min(0.01, remaining_seconds))
            except OSError as error:
                raise self._unavailable(
                    "visual-observation registry root lock is unavailable"
                ) from error

    def _require_lock_identity(
        self,
        descriptor: int,
        *,
        validate_stored_identity: bool,
    ) -> None:
        metadata = os.fstat(descriptor)
        descriptor_identity = (metadata.st_dev, metadata.st_ino)
        pathname_identity = self._path_identity(self._lock_path)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or descriptor_identity != pathname_identity
            or (validate_stored_identity and descriptor_identity != self._lock_identity)
        ):
            raise self._unavailable(
                "visual-observation registry lock identity is invalid"
            )

    @contextmanager
    def _anchor_directory_descriptor(self) -> Iterator[int]:
        try:
            descriptor = os.open(
                self._anchor_directory,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            )
        except OSError as error:
            raise self._unavailable(
                "visual-observation registry anchor directory is unavailable"
            ) from error
        try:
            metadata = os.fstat(descriptor)
            if (
                (metadata.st_dev, metadata.st_ino) != self._anchor_directory_identity
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_nlink < 1
            ):
                raise self._chain_error("registry anchor directory identity changed")
            yield descriptor
        finally:
            os.close(descriptor)

    @contextmanager
    def _connection(
        self,
        *,
        require_stored_identity: bool = True,
        allow_delete_journal_mode: bool = False,
    ) -> Iterator[sqlite3.Connection]:
        before = self._path_identity(self._database_path)
        if require_stored_identity and before != self._database_identity:
            raise self._unavailable(
                "visual-observation registry database identity changed"
            )
        try:
            connection = sqlite3.connect(
                f"{self._database_path.as_uri()}?mode=rw",
                uri=True,
                isolation_level=None,
                timeout=self._busy_timeout_milliseconds / 1000,
            )
        except sqlite3.Error as error:
            raise self._unavailable(
                "visual-observation registry database is unavailable"
            ) from error
        try:
            after = self._path_identity(self._database_path)
            if after != before:
                raise self._unavailable(
                    "visual-observation registry database changed while opening"
                )
            connection.execute(
                f"PRAGMA busy_timeout = {self._busy_timeout_milliseconds}"
            )
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA recursive_triggers = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("PRAGMA synchronous = FULL")
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()
            expected_journal_modes = (
                {"delete", "wal"} if allow_delete_journal_mode else {"wal"}
            )
            if (
                journal_mode is None
                or str(journal_mode[0]).lower() not in expected_journal_modes
            ):
                raise self._unavailable(
                    "visual-observation registry journal mode is invalid"
                )
        except VisualObservationRegistryError:
            connection.close()
            raise
        except (OSError, sqlite3.Error) as error:
            connection.close()
            raise self._unavailable(
                "visual-observation registry connection validation failed"
            ) from error
        try:
            yield connection
        finally:
            connection.close()

    def _checkpoint(self, connection: sqlite3.Connection) -> None:
        result = connection.execute("PRAGMA wal_checkpoint(FULL)").fetchone()
        if (
            result is None
            or len(result) != 3
            or int(result[0]) != 0
            or int(result[1]) != int(result[2])
        ):
            raise self._unavailable(
                "visual-observation registry WAL checkpoint did not complete"
            )

    def _fsync_database(self) -> None:
        descriptor = os.open(
            self._database_path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _create_lock_file(self) -> None:
        descriptor = os.open(
            self._lock_path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._fsync_root()

    def _create_anchor_directory(self) -> None:
        os.mkdir(self._anchor_directory, mode=0o700)
        self._fsync_root()

    def _create_database_file(self) -> None:
        descriptor = os.open(
            self._database_path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        os.close(descriptor)
        self._fsync_root()

    def _fsync_root(self) -> None:
        descriptor = os.open(
            self._root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _require_root_identity(self) -> None:
        if self._path_identity(self._root, require_directory=True) != (
            self._root_identity
        ):
            raise self._unavailable("visual-observation registry root identity changed")

    @staticmethod
    def _validated_root(registry_root: Path) -> Path:
        if not isinstance(registry_root, Path):
            raise TypeError("registry_root must be a pathlib.Path")
        if not registry_root.is_absolute():
            raise ValueError("registry_root must be absolute")
        try:
            resolved = registry_root.resolve(strict=True)
        except OSError as error:
            raise VisualObservationRegistryError(
                "visual-observation registry root is unavailable",
                kind=VisualObservationRegistryFailureKind.UNAVAILABLE,
            ) from error
        if resolved != registry_root:
            raise ValueError("registry_root must not contain symbolic links")
        return registry_root

    @staticmethod
    def _validated_busy_timeout(value: int) -> int:
        if type(value) is not int or not 1 <= value <= 60_000:
            raise ValueError(
                "busy_timeout_milliseconds must be an integer from 1 through 60000"
            )
        return value

    @staticmethod
    def _validated_lock_timeout(value: int) -> int:
        if type(value) is not int or not 1 <= value <= 60_000:
            raise ValueError(
                "lock_timeout_milliseconds must be an integer from 1 through 60000"
            )
        return value

    @staticmethod
    def _validated_reservation(
        reservation: VisualObservationReceiptReservation,
    ) -> VisualObservationReceiptReservation:
        try:
            return VisualObservationReceiptReservation.model_validate(
                reservation.model_dump(mode="python", warnings=False)
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise VisualObservationRegistryError(
                "visual-observation registry reservation is structurally invalid",
                kind=VisualObservationRegistryFailureKind.MISMATCH,
            ) from error

    @staticmethod
    def _validated_expiration(
        expiration: VisualObservationInvocationExpiration,
    ) -> VisualObservationInvocationExpiration:
        try:
            return VisualObservationInvocationExpiration.model_validate(
                expiration.model_dump(mode="python", warnings=False)
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise VisualObservationRegistryError(
                "visual-observation registry expiration is structurally invalid",
                kind=VisualObservationRegistryFailureKind.MISMATCH,
            ) from error

    @staticmethod
    def _path_identity(
        path: Path,
        *,
        require_directory: bool = False,
    ) -> tuple[int, int]:
        try:
            metadata = path.lstat()
        except OSError as error:
            raise VisualObservationRegistryError(
                "visual-observation registry path is unavailable",
                kind=VisualObservationRegistryFailureKind.UNAVAILABLE,
            ) from error
        expected_type = (
            stat.S_ISDIR(metadata.st_mode)
            if require_directory
            else stat.S_ISREG(metadata.st_mode)
        )
        invalid_link_count = (
            metadata.st_nlink < 1 if require_directory else metadata.st_nlink != 1
        )
        if not expected_type or invalid_link_count:
            raise VisualObservationRegistryError(
                "visual-observation registry path identity is unsafe",
                kind=VisualObservationRegistryFailureKind.UNAVAILABLE,
            )
        return metadata.st_dev, metadata.st_ino

    @staticmethod
    def _receipt_key(
        reservation: VisualObservationReceiptReservation,
    ) -> tuple[str, ...]:
        return (
            reservation.cryptographic_domain,
            reservation.signature_purpose,
            reservation.trust_key_id,
            str(reservation.authority_id),
            str(reservation.receipt_id),
        )

    @staticmethod
    def _invocation_key(
        reservation: VisualObservationReceiptReservation,
    ) -> tuple[str, ...]:
        return (
            reservation.cryptographic_domain,
            reservation.signature_purpose,
            reservation.trust_key_id,
            str(reservation.authority_id),
            str(reservation.invocation_id),
        )

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        if connection.in_transaction:
            connection.rollback()

    @staticmethod
    def _chain_error(message: str) -> VisualObservationRegistryError:
        return VisualObservationRegistryError(
            message,
            kind=VisualObservationRegistryFailureKind.UNAVAILABLE,
        )

    @staticmethod
    def _unavailable(message: str) -> VisualObservationRegistryError:
        return VisualObservationRegistryError(
            message,
            kind=VisualObservationRegistryFailureKind.UNAVAILABLE,
        )


class SqliteVisualObservationReceiptRegistry:
    """Adapt the persistent SQLite store to the inward receipt-registry port."""

    def __init__(
        self,
        *,
        registry_root: Path,
        mode: SqliteVisualObservationRegistryOpenMode,
        busy_timeout_milliseconds: int,
        lock_timeout_milliseconds: int,
    ) -> None:
        self._store = _SqliteVisualObservationRegistryStore(
            registry_root=registry_root,
            mode=mode,
            busy_timeout_milliseconds=busy_timeout_milliseconds,
            lock_timeout_milliseconds=lock_timeout_milliseconds,
        )

    @property
    def database_path(self) -> Path:
        """Return the exact database path inside the supplied root."""

        return self._store.database_path

    def reserve_once(
        self,
        reservation: VisualObservationReceiptReservation,
    ) -> None:
        self._store.reserve_once(reservation)

    def confirm_issued(
        self,
        reservation: VisualObservationReceiptReservation,
    ) -> None:
        self._store.confirm_issued(reservation)

    def expire_invocation(
        self,
        expiration: VisualObservationInvocationExpiration,
    ) -> None:
        self._store.expire_invocation(expiration)

    def require_issued(
        self,
        reservation: VisualObservationReceiptReservation,
    ) -> None:
        self._store.require_issued(reservation)

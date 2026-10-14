"""Persistent current-state adapter for semantic calibration promotion."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
import stat
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import NoReturn

from lecture_study.quality.calibration_promotion import (
    CalibrationArtifactKind,
    CalibrationFailureKind,
    CalibrationPromotionError,
    SemanticCalibrationCurrentStatePort,
    SemanticCalibrationRevocationPort,
    SemanticCalibrationStateRecord,
    SemanticCalibrationTrustedClockPort,
)
from lecture_study.quality.trust import canonical_json_bytes

_DATABASE_NAME = "semantic-calibration-current-state.sqlite3"
_LOCK_NAME = "semantic-calibration-current-state.lock"
_MANIFEST_NAME = "semantic-calibration-current-state.json"
_ANCHOR_DIRECTORY_NAME = "semantic-calibration-current-state-anchors"
_ANCHOR_PATTERN = re.compile(r"^[0-9]{20}\.anchor$")
_EVENT_CONTEXT = b"lecture-study:semantic-calibration:state-event:v1\0"
_ZERO_HASH = "0" * 64
_MAXIMUM_LOCK_TIMEOUT_SECONDS = 60.0


class SqliteSemanticCalibrationStateOpenMode(StrEnum):
    """Explicit initialization and opening modes."""

    INITIALIZE = "initialize"
    OPEN_EXISTING = "open-existing"


@dataclass(frozen=True, slots=True)
class _StateEvent:
    sequence: int
    record: SemanticCalibrationStateRecord
    previous_event_sha256: str
    event_sha256: str

    def canonical_payload(self) -> dict[str, object]:
        return {
            "record": self.record.model_dump(mode="json"),
            "previous_event_sha256": self.previous_event_sha256,
            "sequence": self.sequence,
        }

    def canonical_record(self) -> bytes:
        return canonical_json_bytes(
            {
                **self.canonical_payload(),
                "event_sha256": self.event_sha256,
            }
        )


class SqliteSemanticCalibrationCurrentState(SemanticCalibrationCurrentStatePort):
    """Append-only, process-safe calibration state at one explicit local root."""

    def __init__(
        self,
        *,
        root: Path,
        mode: SqliteSemanticCalibrationStateOpenMode,
        lock_timeout_seconds: float,
        trusted_clock: SemanticCalibrationTrustedClockPort,
        revocations: SemanticCalibrationRevocationPort,
    ) -> None:
        if not isinstance(root, Path):
            raise TypeError("semantic calibration state root must be a Path")
        if not root.is_absolute():
            raise ValueError("semantic calibration state root must be absolute")
        validated_lock_timeout_seconds = _require_lock_timeout_seconds(
            lock_timeout_seconds
        )
        if not isinstance(trusted_clock, SemanticCalibrationTrustedClockPort):
            raise TypeError(
                "trusted_clock must implement SemanticCalibrationTrustedClockPort"
            )
        if not isinstance(revocations, SemanticCalibrationRevocationPort):
            raise TypeError(
                "revocations must implement SemanticCalibrationRevocationPort"
            )
        self._root = root
        self._database = root / _DATABASE_NAME
        self._lock = root / _LOCK_NAME
        self._manifest = root / _MANIFEST_NAME
        self._anchors = root / _ANCHOR_DIRECTORY_NAME
        self._lock_timeout_seconds = validated_lock_timeout_seconds
        self._trusted_clock = trusted_clock
        self._revocations = revocations
        self._initialize_or_open(mode)
        self._root_identity = self._path_identity(root, directory=True)
        self._database_identity = self._path_identity(self._database)
        self._lock_identity = self._path_identity(self._lock)
        self._manifest_identity = self._path_identity(self._manifest)
        self._anchor_identity = self._path_identity(self._anchors, directory=True)
        with self._exclusive_lock():
            self._validate_persistent_state()

    @classmethod
    def initialize(
        cls,
        *,
        root: Path,
        lock_timeout_seconds: float,
        trusted_clock: SemanticCalibrationTrustedClockPort,
        revocations: SemanticCalibrationRevocationPort,
    ) -> SqliteSemanticCalibrationCurrentState:
        """Initialize a new registry at an explicit root."""

        return cls(
            root=root,
            mode=SqliteSemanticCalibrationStateOpenMode.INITIALIZE,
            lock_timeout_seconds=lock_timeout_seconds,
            trusted_clock=trusted_clock,
            revocations=revocations,
        )

    @classmethod
    def open_existing(
        cls,
        *,
        root: Path,
        lock_timeout_seconds: float,
        trusted_clock: SemanticCalibrationTrustedClockPort,
        revocations: SemanticCalibrationRevocationPort,
    ) -> SqliteSemanticCalibrationCurrentState:
        """Open and fully revalidate an existing registry."""

        return cls(
            root=root,
            mode=SqliteSemanticCalibrationStateOpenMode.OPEN_EXISTING,
            lock_timeout_seconds=lock_timeout_seconds,
            trusted_clock=trusted_clock,
            revocations=revocations,
        )

    @property
    def root(self) -> Path:
        return self._root

    @property
    def detects_coordinated_whole_root_rollback(self) -> bool:
        """Local anchors cannot detect coordinated replacement of the whole root."""

        return False

    def record_issued(
        self,
        record: SemanticCalibrationStateRecord,
    ) -> None:
        """Append one exact artifact identity, idempotently for the same payload."""

        validated = self._require_record(record)
        self._require_temporally_current(validated)
        try:
            with self._exclusive_lock():
                self._validate_persistent_state()
                with self._connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    existing = connection.execute(
                        """
                        SELECT payload_sha256
                        FROM state_events
                        WHERE artifact_kind = ?
                          AND artifact_id = ?
                          AND artifact_version = ?
                          AND trust_key_id = ?
                          AND authority_id = ?
                          AND authority_sequence = ?
                        """,
                        self._logical_identity_values(validated),
                    ).fetchone()
                    if existing is not None:
                        connection.rollback()
                        if hmac.compare_digest(
                            str(existing[0]), validated.payload_sha256
                        ):
                            self._require_no_newer_authority_sequence(
                                connection,
                                validated,
                            )
                            return
                        raise CalibrationPromotionError(
                            "calibration logical identity has a substituted payload",
                            kind=CalibrationFailureKind.SUBSTITUTED,
                            reason_code="calibration_logical_identity_substitution",
                        )
                    self._require_newer_authority_sequence(connection, validated)
                    previous = self._last_event(connection)
                    sequence = 1 if previous is None else previous.sequence + 1
                    event = self._new_event(
                        sequence=sequence,
                        record=validated,
                        previous_event_sha256=(
                            _ZERO_HASH if previous is None else previous.event_sha256
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO state_events (
                            sequence,
                            artifact_kind,
                            artifact_id,
                            artifact_version,
                            payload_sha256,
                            trust_key_id,
                            authority_id,
                            authority_sequence,
                            issued_at_epoch_seconds,
                            expires_at_epoch_seconds,
                            previous_event_sha256,
                            event_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event.sequence,
                            event.record.artifact_kind.value,
                            str(event.record.artifact_id),
                            str(event.record.artifact_version),
                            event.record.payload_sha256,
                            event.record.trust_key_id,
                            str(event.record.authority_id),
                            event.record.authority_sequence,
                            event.record.issued_at_epoch_seconds,
                            event.record.expires_at_epoch_seconds,
                            event.previous_event_sha256,
                            event.event_sha256,
                        ),
                    )
                    connection.commit()
                self._synchronize_file(self._database)
                self._append_anchor(event)
                self._synchronize_directory(self._root)
                self._validate_persistent_state()
        except CalibrationPromotionError:
            raise
        except (OSError, sqlite3.Error) as error:
            self._raise_unavailable(
                "semantic calibration state registration is unavailable",
                "sqlite_calibration_state_registration_unavailable",
                error,
            )

    def require_current(
        self,
        record: SemanticCalibrationStateRecord,
    ) -> None:
        """Require one exact artifact identity in fully valid persistent state."""

        validated = self._require_record(record)
        self._require_temporally_current(validated)
        try:
            with self._exclusive_lock():
                events = self._validate_persistent_state()
        except CalibrationPromotionError:
            raise
        except (OSError, sqlite3.Error) as error:
            self._raise_unavailable(
                "semantic calibration current state is unavailable",
                "sqlite_calibration_current_state_unavailable",
                error,
            )
        if not any(event.record == validated for event in events):
            raise CalibrationPromotionError(
                "semantic calibration artifact is absent from persistent state",
                kind=CalibrationFailureKind.UNAVAILABLE,
                reason_code="calibration_artifact_not_persisted",
            )
        latest_sequence = max(
            event.record.authority_sequence
            for event in events
            if self._same_logical_lineage(event.record, validated)
        )
        if validated.authority_sequence != latest_sequence:
            raise CalibrationPromotionError(
                "semantic calibration artifact is stale",
                kind=CalibrationFailureKind.REVOKED,
                reason_code="stale_calibration_authority_sequence",
            )

    def _initialize_or_open(
        self,
        mode: SqliteSemanticCalibrationStateOpenMode,
    ) -> None:
        if not isinstance(mode, SqliteSemanticCalibrationStateOpenMode):
            raise TypeError("semantic calibration state mode is invalid")
        if mode is SqliteSemanticCalibrationStateOpenMode.INITIALIZE:
            self._initialize()
            return
        self._require_existing_layout()

    def _initialize(self) -> None:
        if self._root.exists():
            if self._root.is_symlink() or not self._root.is_dir():
                raise ValueError("semantic calibration state root is unsafe")
            if any(self._root.iterdir()):
                raise ValueError("semantic calibration state root is not empty")
        else:
            self._root.mkdir(mode=0o700, parents=True)
        os.chmod(self._root, 0o700)
        self._anchors.mkdir(mode=0o700)
        self._lock.touch(mode=0o600, exist_ok=False)
        manifest_payload = {
            "registry_id": str(uuid.uuid4()),
            "schema_version": 1,
        }
        self._write_new_file(
            self._manifest,
            canonical_json_bytes(manifest_payload),
        )
        with self._connect(initializing=True) as connection:
            connection.execute(
                """
                CREATE TABLE state_events (
                    sequence INTEGER PRIMARY KEY,
                    artifact_kind TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    artifact_version TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    trust_key_id TEXT NOT NULL,
                    authority_id TEXT NOT NULL,
                    authority_sequence INTEGER NOT NULL,
                    issued_at_epoch_seconds INTEGER NOT NULL,
                    expires_at_epoch_seconds INTEGER NOT NULL,
                    previous_event_sha256 TEXT NOT NULL,
                    event_sha256 TEXT NOT NULL,
                    UNIQUE (
                        artifact_kind,
                        artifact_id,
                        artifact_version,
                        trust_key_id,
                        authority_id,
                        authority_sequence
                    )
                )
                """
            )
            connection.commit()
        self._synchronize_file(self._database)
        self._synchronize_directory(self._root)

    def _require_existing_layout(self) -> None:
        required = (self._database, self._lock, self._manifest, self._anchors)
        if not self._root.exists() or any(not path.exists() for path in required):
            raise ValueError("semantic calibration state registry is incomplete")

    def _validate_persistent_state(self) -> tuple[_StateEvent, ...]:
        self._require_stable_paths()
        self._validate_manifest()
        with self._connect() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity != ("ok",):
                self._raise_corrupt("semantic calibration database is corrupt")
            columns = tuple(
                row[1] for row in connection.execute("PRAGMA table_info(state_events)")
            )
            if columns != (
                "sequence",
                "artifact_kind",
                "artifact_id",
                "artifact_version",
                "payload_sha256",
                "trust_key_id",
                "authority_id",
                "authority_sequence",
                "issued_at_epoch_seconds",
                "expires_at_epoch_seconds",
                "previous_event_sha256",
                "event_sha256",
            ):
                self._raise_corrupt("semantic calibration schema is substituted")
            rows = connection.execute(
                """
                SELECT sequence, artifact_kind, artifact_id, artifact_version,
                       payload_sha256, trust_key_id, authority_id,
                       authority_sequence, issued_at_epoch_seconds,
                       expires_at_epoch_seconds, previous_event_sha256, event_sha256
                FROM state_events
                ORDER BY sequence
                """
            ).fetchall()
        events = tuple(self._event_from_row(row) for row in rows)
        self._validate_event_chain(events)
        self._validate_anchors(events)
        return events

    def _validate_manifest(self) -> None:
        try:
            payload = json.loads(self._manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            self._raise_corrupt("semantic calibration manifest is corrupt", error)
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or not isinstance(payload.get("registry_id"), str)
        ):
            self._raise_corrupt("semantic calibration manifest is invalid")
        try:
            uuid.UUID(payload["registry_id"], version=4)
        except (ValueError, TypeError, AttributeError) as error:
            self._raise_corrupt(
                "semantic calibration registry identity is invalid", error
            )

    def _validate_event_chain(self, events: tuple[_StateEvent, ...]) -> None:
        previous = _ZERO_HASH
        identities: set[tuple[object, ...]] = set()
        for expected_sequence, event in enumerate(events, start=1):
            if event.sequence != expected_sequence:
                self._raise_corrupt("semantic calibration state sequence is truncated")
            if not hmac.compare_digest(event.previous_event_sha256, previous):
                self._raise_corrupt("semantic calibration state hash chain is broken")
            expected_hash = self._event_sha256(event.canonical_payload())
            if not hmac.compare_digest(event.event_sha256, expected_hash):
                self._raise_corrupt("semantic calibration state event is substituted")
            identity = self._logical_identity(event.record)
            if identity in identities:
                self._raise_corrupt("semantic calibration state identity is duplicated")
            identities.add(identity)
            previous = event.event_sha256

    def _validate_anchors(self, events: tuple[_StateEvent, ...]) -> None:
        expected_names = {self._anchor_name(event.sequence): event for event in events}
        actual_names = {
            entry.name
            for entry in os.scandir(self._anchors)
            if entry.name not in {".", ".."}
        }
        if actual_names != set(expected_names):
            self._raise_corrupt(
                "semantic calibration anchors do not match persistent state"
            )
        for name, event in expected_names.items():
            if not _ANCHOR_PATTERN.fullmatch(name):
                self._raise_corrupt("semantic calibration anchor name is invalid")
            path = self._anchors / name
            if self._path_identity(path) and not hmac.compare_digest(
                path.read_bytes(),
                event.canonical_record(),
            ):
                self._raise_corrupt("semantic calibration anchor is substituted")

    def _append_anchor(self, event: _StateEvent) -> None:
        self._write_new_file(
            self._anchors / self._anchor_name(event.sequence),
            event.canonical_record(),
        )
        self._synchronize_directory(self._anchors)

    @staticmethod
    def _anchor_name(sequence: int) -> str:
        return f"{sequence:020d}.anchor"

    @staticmethod
    def _event_sha256(payload: Mapping[str, object]) -> str:
        return hashlib.sha256(
            _EVENT_CONTEXT + canonical_json_bytes(payload)
        ).hexdigest()

    @classmethod
    def _new_event(
        cls,
        *,
        sequence: int,
        record: SemanticCalibrationStateRecord,
        previous_event_sha256: str,
    ) -> _StateEvent:
        payload = {
            "record": record.model_dump(mode="json"),
            "previous_event_sha256": previous_event_sha256,
            "sequence": sequence,
        }
        return _StateEvent(
            sequence=sequence,
            record=record,
            previous_event_sha256=previous_event_sha256,
            event_sha256=cls._event_sha256(payload),
        )

    @classmethod
    def _event_from_row(cls, row: tuple[object, ...]) -> _StateEvent:
        try:
            (
                sequence,
                kind,
                artifact_id,
                artifact_version,
                payload,
                trust_key_id,
                authority_id,
                authority_sequence,
                issued_at,
                expires_at,
                previous,
                event_hash,
            ) = row
            if (
                isinstance(sequence, bool)
                or not isinstance(sequence, int)
                or not isinstance(kind, str)
                or not isinstance(artifact_id, str)
                or not isinstance(artifact_version, str)
                or not isinstance(payload, str)
                or not isinstance(trust_key_id, str)
                or not isinstance(authority_id, str)
                or isinstance(authority_sequence, bool)
                or not isinstance(authority_sequence, int)
                or isinstance(issued_at, bool)
                or not isinstance(issued_at, int)
                or isinstance(expires_at, bool)
                or not isinstance(expires_at, int)
                or not isinstance(previous, str)
                or not isinstance(event_hash, str)
            ):
                raise ValueError("invalid event column type")
            return _StateEvent(
                sequence=sequence,
                record=SemanticCalibrationStateRecord(
                    artifact_kind=CalibrationArtifactKind(kind),
                    artifact_id=artifact_id,
                    artifact_version=artifact_version,
                    payload_sha256=cls._require_digest(payload),
                    trust_key_id=cls._require_digest(trust_key_id),
                    authority_id=authority_id,
                    authority_sequence=authority_sequence,
                    issued_at_epoch_seconds=issued_at,
                    expires_at_epoch_seconds=expires_at,
                ),
                previous_event_sha256=cls._require_digest(previous),
                event_sha256=cls._require_digest(event_hash),
            )
        except (TypeError, ValueError) as error:
            cls._raise_corrupt("semantic calibration event is invalid", error)

    @staticmethod
    def _last_event(connection: sqlite3.Connection) -> _StateEvent | None:
        row = connection.execute(
            """
            SELECT sequence, artifact_kind, artifact_id, artifact_version,
                   payload_sha256, trust_key_id, authority_id,
                   authority_sequence, issued_at_epoch_seconds,
                   expires_at_epoch_seconds, previous_event_sha256, event_sha256
            FROM state_events
            ORDER BY sequence DESC
            LIMIT 1
            """
        ).fetchone()
        return (
            None
            if row is None
            else SqliteSemanticCalibrationCurrentState._event_from_row(row)
        )

    @contextmanager
    def _connect(self, *, initializing: bool = False) -> Iterator[sqlite3.Connection]:
        if not initializing and self._database.is_symlink():
            self._raise_corrupt("semantic calibration database path is substituted")
        connection = sqlite3.connect(
            self._database,
            timeout=self._lock_timeout_seconds,
            isolation_level=None,
        )
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("PRAGMA synchronous = FULL")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        descriptor = os.open(
            self._lock,
            os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        deadline = time.monotonic() + self._lock_timeout_seconds
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("semantic calibration state lock timed out")
                    time.sleep(0.01)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _require_stable_paths(self) -> None:
        expected = (
            (self._root, self._root_identity, True),
            (self._database, self._database_identity, False),
            (self._lock, self._lock_identity, False),
            (self._manifest, self._manifest_identity, False),
            (self._anchors, self._anchor_identity, True),
        )
        for path, identity, directory in expected:
            if self._path_identity(path, directory=directory) != identity:
                self._raise_corrupt(
                    "semantic calibration registry path identity changed"
                )

    @staticmethod
    def _path_identity(path: Path, *, directory: bool = False) -> tuple[int, int]:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("semantic calibration registry symlink is forbidden")
        expected_type = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected_type(metadata.st_mode):
            raise ValueError("semantic calibration registry path type is invalid")
        return metadata.st_dev, metadata.st_ino

    @staticmethod
    def _write_new_file(path: Path, content: bytes) -> None:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError(errno.EIO, "calibration state write made no progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _synchronize_file(path: Path) -> None:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _synchronize_directory(path: Path) -> None:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _require_record(
        value: SemanticCalibrationStateRecord,
    ) -> SemanticCalibrationStateRecord:
        try:
            return SemanticCalibrationStateRecord.model_validate(
                value.model_dump(mode="python", warnings=False)
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise CalibrationPromotionError(
                "semantic calibration state record is invalid",
                kind=CalibrationFailureKind.INVALID,
                reason_code="invalid_semantic_calibration_state_record",
            ) from error

    def _require_temporally_current(
        self,
        record: SemanticCalibrationStateRecord,
    ) -> None:
        try:
            now = self._trusted_clock.current_epoch_seconds()
            if isinstance(now, bool) or not isinstance(now, int) or now < 0:
                raise TypeError("trusted calibration clock returned an invalid value")
            self._revocations.require_not_revoked(record)
        except CalibrationPromotionError:
            raise
        except Exception as error:
            raise CalibrationPromotionError(
                "semantic calibration temporal state is unavailable",
                kind=CalibrationFailureKind.UNAVAILABLE,
                reason_code="calibration_temporal_state_unavailable",
            ) from error
        if now < record.issued_at_epoch_seconds:
            raise CalibrationPromotionError(
                "semantic calibration artifact is not yet valid",
                kind=CalibrationFailureKind.UNVERIFIABLE,
                reason_code="calibration_artifact_not_yet_valid",
            )
        if now >= record.expires_at_epoch_seconds:
            raise CalibrationPromotionError(
                "semantic calibration artifact is expired",
                kind=CalibrationFailureKind.REVOKED,
                reason_code="expired_calibration_artifact",
            )

    @staticmethod
    def _same_logical_lineage(
        left: SemanticCalibrationStateRecord,
        right: SemanticCalibrationStateRecord,
    ) -> bool:
        return (
            left.artifact_kind is right.artifact_kind
            and left.artifact_id == right.artifact_id
            and left.artifact_version == right.artifact_version
            and hmac.compare_digest(left.trust_key_id, right.trust_key_id)
            and left.authority_id == right.authority_id
        )

    @staticmethod
    def _logical_identity(record: SemanticCalibrationStateRecord) -> tuple[object, ...]:
        return (
            record.artifact_kind,
            str(record.artifact_id),
            str(record.artifact_version),
            record.trust_key_id,
            str(record.authority_id),
            record.authority_sequence,
        )

    @staticmethod
    def _logical_identity_values(
        record: SemanticCalibrationStateRecord,
    ) -> tuple[object, ...]:
        return (
            record.artifact_kind.value,
            str(record.artifact_id),
            str(record.artifact_version),
            record.trust_key_id,
            str(record.authority_id),
            record.authority_sequence,
        )

    @staticmethod
    def _require_newer_authority_sequence(
        connection: sqlite3.Connection,
        record: SemanticCalibrationStateRecord,
    ) -> None:
        row = connection.execute(
            """
            SELECT MAX(authority_sequence)
            FROM state_events
            WHERE artifact_kind = ?
              AND artifact_id = ?
              AND artifact_version = ?
              AND trust_key_id = ?
              AND authority_id = ?
            """,
            (
                record.artifact_kind.value,
                str(record.artifact_id),
                str(record.artifact_version),
                record.trust_key_id,
                str(record.authority_id),
            ),
        ).fetchone()
        maximum = None if row is None else row[0]
        if maximum is not None and (
            isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or record.authority_sequence <= maximum
        ):
            raise CalibrationPromotionError(
                "semantic calibration authority sequence is replayed or stale",
                kind=CalibrationFailureKind.SUBSTITUTED,
                reason_code="calibration_authority_sequence_replay",
            )

    @staticmethod
    def _require_no_newer_authority_sequence(
        connection: sqlite3.Connection,
        record: SemanticCalibrationStateRecord,
    ) -> None:
        row = connection.execute(
            """
            SELECT MAX(authority_sequence)
            FROM state_events
            WHERE artifact_kind = ?
              AND artifact_id = ?
              AND artifact_version = ?
              AND trust_key_id = ?
              AND authority_id = ?
            """,
            (
                record.artifact_kind.value,
                str(record.artifact_id),
                str(record.artifact_version),
                record.trust_key_id,
                str(record.authority_id),
            ),
        ).fetchone()
        maximum = None if row is None else row[0]
        if maximum != record.authority_sequence:
            raise CalibrationPromotionError(
                "semantic calibration authority sequence is stale",
                kind=CalibrationFailureKind.REVOKED,
                reason_code="stale_calibration_authority_sequence",
            )

    @staticmethod
    def _require_digest(value: str) -> str:
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError("semantic calibration payload identity is invalid")
        return value

    @staticmethod
    def _raise_corrupt(
        message: str,
        error: BaseException | None = None,
    ) -> NoReturn:
        failure = CalibrationPromotionError(
            message,
            kind=CalibrationFailureKind.INVALID,
            reason_code="corrupt_semantic_calibration_state",
        )
        if error is None:
            raise failure
        raise failure from error

    @staticmethod
    def _raise_unavailable(
        message: str,
        reason_code: str,
        error: BaseException,
    ) -> NoReturn:
        raise CalibrationPromotionError(
            message,
            kind=CalibrationFailureKind.UNAVAILABLE,
            reason_code=reason_code,
        ) from error


def _require_lock_timeout_seconds(value: float) -> float:
    if type(value) not in (int, float):
        raise TypeError(
            "semantic calibration lock timeout must be an exact int or float"
        )
    if value <= 0 or value > _MAXIMUM_LOCK_TIMEOUT_SECONDS or not math.isfinite(value):
        raise ValueError(
            "semantic calibration lock timeout must be finite, greater than zero, "
            "and no more than 60 seconds"
        )
    return float(value)

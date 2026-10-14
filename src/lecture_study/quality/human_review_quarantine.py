"""Durable append-only quarantine for canonical human-review evidence."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import time
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Iterator

from pydantic import TypeAdapter

from lecture_study.domain.models import SafeIdentifier
from lecture_study.quality.human_review import (
    HumanReviewEvidenceQuarantined,
    HumanReviewFailureKind,
    HumanReviewQuarantineEvidence,
    HumanReviewQuarantineRecord,
    HumanReviewQuarantineRegistryError,
)

_DATABASE_FILENAME = "human-review-quarantine.sqlite3"
_LOCK_FILENAME = ".human-review-quarantine.lock"
_ANCHOR_DIRECTORY = "human-review-quarantine-anchors"
_ANCHOR_FILENAME_PATTERN = re.compile(r"^[0-9]{20}\.anchor$")
_SCHEMA_VERSION = 1
_RECORD_HASH_DOMAIN = "lecture-study/human-review-quarantine-record/v1"
_SAFE_IDENTIFIER_ADAPTER = TypeAdapter(SafeIdentifier)
_QUARANTINE_FAILURE_KINDS = frozenset(
    {
        HumanReviewFailureKind.REJECTED,
        HumanReviewFailureKind.INVALID,
        HumanReviewFailureKind.SPOOFED,
        HumanReviewFailureKind.FALSIFIED,
    }
)

_METADATA_TABLE_SQL = f"""
CREATE TABLE human_review_quarantine_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL CHECK (schema_version = {_SCHEMA_VERSION}),
    schema_sha256 TEXT NOT NULL
        CHECK (
            length(schema_sha256) = 64
            AND schema_sha256 NOT GLOB '*[^0-9a-f]*'
        )
)
"""
_RECORD_TABLE_SQL = """
CREATE TABLE human_review_quarantine_records (
    registry_sequence INTEGER PRIMARY KEY CHECK (registry_sequence > 0),
    evidence_payload_sha256 TEXT NOT NULL UNIQUE
        CHECK (
            length(evidence_payload_sha256) = 64
            AND evidence_payload_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
    cryptographic_domain TEXT NOT NULL
        CHECK (cryptographic_domain = 'lecture-study.human-review.v1'),
    signature_purpose TEXT NOT NULL
        CHECK (signature_purpose = 'human-review-scope-decision'),
    trust_key_id TEXT NOT NULL
        CHECK (
            length(trust_key_id) = 64
            AND trust_key_id NOT GLOB '*[^0-9a-f]*'
        ),
    authority_id TEXT NOT NULL,
    lecture_id TEXT NOT NULL,
    canonical_scope_sha256 TEXT NOT NULL
        CHECK (
            length(canonical_scope_sha256) = 64
            AND canonical_scope_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
    payload_base_sha256 TEXT NOT NULL
        CHECK (
            length(payload_base_sha256) = 64
            AND payload_base_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
    review_version INTEGER NOT NULL CHECK (review_version > 0),
    reason_kind TEXT NOT NULL
        CHECK (reason_kind IN ('rejected', 'invalid', 'spoofed', 'falsified')),
    reason_code TEXT NOT NULL,
    observed_signature_sha256 TEXT NOT NULL
        CHECK (
            length(observed_signature_sha256) = 64
            AND observed_signature_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
    previous_record_sha256 TEXT
        CHECK (
            previous_record_sha256 IS NULL
            OR (
                length(previous_record_sha256) = 64
                AND previous_record_sha256 NOT GLOB '*[^0-9a-f]*'
            )
        ),
    record_sha256 TEXT NOT NULL UNIQUE
        CHECK (
            length(record_sha256) = 64
            AND record_sha256 NOT GLOB '*[^0-9a-f]*'
        )
)
"""
_SCOPE_INDEX_SQL = """
CREATE INDEX human_review_quarantine_scope_idx
ON human_review_quarantine_records (
    canonical_scope_sha256,
    review_version
)
"""
_RECORD_NO_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER human_review_quarantine_record_no_update
BEFORE UPDATE ON human_review_quarantine_records
BEGIN
    SELECT RAISE(ABORT, 'human-review quarantine records are immutable');
END
"""
_RECORD_NO_DELETE_TRIGGER_SQL = """
CREATE TRIGGER human_review_quarantine_record_no_delete
BEFORE DELETE ON human_review_quarantine_records
BEGIN
    SELECT RAISE(ABORT, 'human-review quarantine records cannot be deleted');
END
"""
_METADATA_NO_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER human_review_quarantine_metadata_no_update
BEFORE UPDATE ON human_review_quarantine_metadata
BEGIN
    SELECT RAISE(ABORT, 'human-review quarantine metadata is immutable');
END
"""
_METADATA_NO_DELETE_TRIGGER_SQL = """
CREATE TRIGGER human_review_quarantine_metadata_no_delete
BEFORE DELETE ON human_review_quarantine_metadata
BEGIN
    SELECT RAISE(ABORT, 'human-review quarantine metadata cannot be deleted');
END
"""
_CHAIN_TRIGGER_SQL = """
CREATE TRIGGER human_review_quarantine_chain_guard
BEFORE INSERT ON human_review_quarantine_records
BEGIN
    SELECT CASE
        WHEN NEW.registry_sequence
            != (SELECT COUNT(*) + 1 FROM human_review_quarantine_records)
        THEN RAISE(ABORT, 'human-review quarantine sequence is not contiguous')
    END;
    SELECT CASE
        WHEN NEW.registry_sequence = 1 AND NEW.previous_record_sha256 IS NOT NULL
        THEN RAISE(ABORT, 'human-review quarantine genesis has a predecessor')
    END;
    SELECT CASE
        WHEN NEW.registry_sequence > 1
            AND NEW.previous_record_sha256 != (
                SELECT record_sha256
                FROM human_review_quarantine_records
                ORDER BY registry_sequence DESC
                LIMIT 1
            )
        THEN RAISE(ABORT, 'human-review quarantine predecessor is invalid')
    END;
END
"""
_SCHEMA_STATEMENTS = (
    _METADATA_TABLE_SQL,
    _RECORD_TABLE_SQL,
    _SCOPE_INDEX_SQL,
    _RECORD_NO_UPDATE_TRIGGER_SQL,
    _RECORD_NO_DELETE_TRIGGER_SQL,
    _METADATA_NO_UPDATE_TRIGGER_SQL,
    _METADATA_NO_DELETE_TRIGGER_SQL,
    _CHAIN_TRIGGER_SQL,
)


def _normalized_sql(statement: str) -> str:
    return " ".join(statement.split()).strip().lower()


_SCHEMA_SHA256 = hashlib.sha256(
    "\n".join(_normalized_sql(statement) for statement in _SCHEMA_STATEMENTS).encode(
        "utf-8"
    )
).hexdigest()
_EXPECTED_SCHEMA_OBJECTS = {
    ("table", "human_review_quarantine_metadata"): _METADATA_TABLE_SQL,
    ("table", "human_review_quarantine_records"): _RECORD_TABLE_SQL,
    ("index", "human_review_quarantine_scope_idx"): _SCOPE_INDEX_SQL,
    (
        "trigger",
        "human_review_quarantine_record_no_update",
    ): _RECORD_NO_UPDATE_TRIGGER_SQL,
    (
        "trigger",
        "human_review_quarantine_record_no_delete",
    ): _RECORD_NO_DELETE_TRIGGER_SQL,
    (
        "trigger",
        "human_review_quarantine_metadata_no_update",
    ): _METADATA_NO_UPDATE_TRIGGER_SQL,
    (
        "trigger",
        "human_review_quarantine_metadata_no_delete",
    ): _METADATA_NO_DELETE_TRIGGER_SQL,
    ("trigger", "human_review_quarantine_chain_guard"): _CHAIN_TRIGGER_SQL,
}
_RECORD_COLUMNS = """
registry_sequence,
evidence_payload_sha256,
cryptographic_domain,
signature_purpose,
trust_key_id,
authority_id,
lecture_id,
canonical_scope_sha256,
payload_base_sha256,
review_version,
reason_kind,
reason_code,
observed_signature_sha256,
previous_record_sha256,
record_sha256
"""


class SqliteHumanReviewQuarantineRegistryOpenMode(StrEnum):
    """Explicit lifecycle for one persistent quarantine root."""

    INITIALIZE = "initialize"
    OPEN_EXISTING = "open_existing"


class SqliteHumanReviewQuarantineRegistry:
    """Persist trusted quarantine records beneath one explicit filesystem root.

    Immutable anchors detect database rollback while the anchor directory remains
    unchanged. Coordinated rollback of the entire root requires an external
    monotonic anchor and is outside this adapter's trust boundary.
    """

    def __init__(
        self,
        *,
        registry_root: Path,
        mode: SqliteHumanReviewQuarantineRegistryOpenMode,
        busy_timeout_milliseconds: int,
        lock_timeout_milliseconds: int,
    ) -> None:
        self._mode = self._validated_mode(mode)
        self._root = self._validated_root(registry_root, self._mode)
        self._root_identity = self._path_identity(self._root, require_directory=True)
        self._busy_timeout_milliseconds = self._validated_busy_timeout(
            busy_timeout_milliseconds
        )
        self._lock_timeout_seconds = (
            self._validated_lock_timeout(lock_timeout_milliseconds) / 1_000
        )
        self._open_or_create_lock_file()
        self._lock_identity = self._path_identity(self._lock_path)
        with self._exclusive_root_lock():
            try:
                self._initialize_or_require_layout()
                self._anchor_directory_identity = self._path_identity(
                    self._anchor_directory,
                    require_directory=True,
                )
                self._database_identity = self._path_identity(self._database_path)
                with self._connection() as connection:
                    self._validate_critical_state(connection)
            except HumanReviewQuarantineRegistryError:
                raise
            except (OSError, sqlite3.Error) as error:
                raise self._unavailable(
                    "human-review quarantine validation failed"
                ) from error

    @property
    def database_path(self) -> Path:
        return self._database_path

    def require_not_quarantined(
        self,
        evidence: HumanReviewQuarantineEvidence,
    ) -> None:
        validated_evidence = self._validated_evidence(evidence)
        with self._exclusive_root_lock():
            with self._connection() as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    records = self._validate_critical_state(connection)
                    self._require_fresh(records, validated_evidence)
                    connection.commit()
                except HumanReviewEvidenceQuarantined:
                    self._rollback(connection)
                    raise
                except HumanReviewQuarantineRegistryError:
                    self._rollback(connection)
                    raise
                except (OSError, sqlite3.Error) as error:
                    self._rollback(connection)
                    raise self._unavailable(
                        "human-review quarantine lookup failed"
                    ) from error

    def append(
        self,
        evidence: HumanReviewQuarantineEvidence,
        *,
        kind: HumanReviewFailureKind,
        reason_code: str,
    ) -> HumanReviewQuarantineRecord:
        validated_evidence = self._validated_evidence(evidence)
        validated_kind = self._validated_kind(kind)
        validated_reason_code = self._validated_reason_code(reason_code)
        with self._exclusive_root_lock():
            with self._connection() as connection:
                committed_record: HumanReviewQuarantineRecord | None = None
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    records = self._validate_critical_state(connection)
                    existing = self._record_for_identity(records, validated_evidence)
                    if existing is not None:
                        connection.commit()
                        return existing
                    committed_record = self._next_record(
                        records,
                        validated_evidence,
                        validated_kind,
                        validated_reason_code,
                    )
                    self._insert_record(connection, committed_record)
                    self._validate_database_chain(connection)
                    connection.commit()
                    self._fsync_database()
                    self._append_anchor(committed_record)
                    self._validate_critical_state(connection)
                except HumanReviewQuarantineRegistryError:
                    self._rollback(connection)
                    raise
                except (OSError, sqlite3.Error, ValueError) as error:
                    self._rollback(connection)
                    raise self._unavailable(
                        "human-review quarantine append failed"
                    ) from error
                if committed_record is None:
                    raise AssertionError("a quarantine append must produce a record")
                return committed_record

    @property
    def _database_path(self) -> Path:
        return self._root / _DATABASE_FILENAME

    @property
    def _lock_path(self) -> Path:
        return self._root / _LOCK_FILENAME

    @property
    def _anchor_directory(self) -> Path:
        return self._root / _ANCHOR_DIRECTORY

    def _initialize_or_require_layout(self) -> None:
        database_exists = self._path_exists(self._database_path)
        anchors_exist = self._path_exists(self._anchor_directory)
        if self._mode is SqliteHumanReviewQuarantineRegistryOpenMode.OPEN_EXISTING:
            if not database_exists or not anchors_exist:
                raise self._unavailable(
                    "established human-review quarantine layout is incomplete"
                )
            self._path_identity(self._database_path)
            self._path_identity(self._anchor_directory, require_directory=True)
            return
        if database_exists or anchors_exist:
            raise self._unavailable(
                "human-review quarantine initialization root is not empty"
            )
        os.mkdir(self._anchor_directory, mode=0o700)
        database_descriptor = os.open(
            self._database_path,
            os.O_CREAT | os.O_EXCL | os.O_RDWR | self._no_follow_flag(),
            0o600,
        )
        try:
            os.fsync(database_descriptor)
        finally:
            os.close(database_descriptor)
        self._fsync_root()
        self._anchor_directory_identity = self._path_identity(
            self._anchor_directory,
            require_directory=True,
        )
        self._database_identity = self._path_identity(self._database_path)
        with self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                for statement in _SCHEMA_STATEMENTS:
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT INTO human_review_quarantine_metadata (
                        singleton,
                        schema_version,
                        schema_sha256
                    ) VALUES (1, ?, ?)
                    """,
                    (_SCHEMA_VERSION, _SCHEMA_SHA256),
                )
                connection.commit()
                self._fsync_database()
                self._validate_critical_state(connection)
            except (OSError, sqlite3.Error) as error:
                self._rollback(connection)
                raise self._unavailable(
                    "human-review quarantine initialization failed"
                ) from error

    def _validate_critical_state(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[HumanReviewQuarantineRecord, ...]:
        self._validate_quick_check(connection)
        self._validate_schema(connection)
        records = self._validate_database_chain(connection)
        self._validate_anchors(records)
        return records

    @staticmethod
    def _validate_quick_check(connection: sqlite3.Connection) -> None:
        result = tuple(
            str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall()
        )
        if result != ("ok",):
            raise HumanReviewQuarantineRegistryError(
                "human-review quarantine quick_check failed"
            )

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        metadata = connection.execute(
            """
            SELECT schema_version, schema_sha256
            FROM human_review_quarantine_metadata
            WHERE singleton = 1
            """
        ).fetchone()
        if metadata != (_SCHEMA_VERSION, _SCHEMA_SHA256):
            raise HumanReviewQuarantineRegistryError(
                "human-review quarantine metadata is invalid"
            )
        rows = connection.execute(
            """
            SELECT type, name, sql
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
        actual = {
            (str(row[0]), str(row[1])): _normalized_sql(str(row[2])) for row in rows
        }
        expected = {
            identity: _normalized_sql(statement)
            for identity, statement in _EXPECTED_SCHEMA_OBJECTS.items()
        }
        if actual != expected:
            raise HumanReviewQuarantineRegistryError(
                "human-review quarantine schema is invalid"
            )

    def _validate_database_chain(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[HumanReviewQuarantineRecord, ...]:
        rows = connection.execute(
            f"""
            SELECT {_RECORD_COLUMNS}
            FROM human_review_quarantine_records
            ORDER BY registry_sequence
            """
        ).fetchall()
        records = tuple(self._record_from_row(row) for row in rows)
        previous_sha256: str | None = None
        for expected_sequence, record in enumerate(records, start=1):
            if (
                record.registry_sequence != expected_sequence
                or record.previous_record_sha256 != previous_sha256
                or record.record_sha256 != self._record_sha256(record)
            ):
                raise HumanReviewQuarantineRegistryError(
                    "human-review quarantine hash chain is invalid"
                )
            previous_sha256 = record.record_sha256
        return records

    @staticmethod
    def _record_from_row(
        row: sqlite3.Row | tuple[object, ...],
    ) -> HumanReviewQuarantineRecord:
        try:
            return HumanReviewQuarantineRecord.model_validate(
                {
                    "registry_sequence": row[0],
                    "evidence_payload_sha256": row[1],
                    "cryptographic_domain": row[2],
                    "signature_purpose": row[3],
                    "trust_key_id": row[4],
                    "authority_id": row[5],
                    "lecture_id": row[6],
                    "canonical_scope_sha256": row[7],
                    "payload_base_sha256": row[8],
                    "review_version": row[9],
                    "reason_kind": HumanReviewFailureKind(str(row[10])),
                    "reason_code": row[11],
                    "observed_signature_sha256": row[12],
                    "previous_record_sha256": row[13],
                    "record_sha256": row[14],
                },
            )
        except (IndexError, TypeError, ValueError) as error:
            raise HumanReviewQuarantineRegistryError(
                "human-review quarantine record is invalid"
            ) from error

    @classmethod
    def _record_sha256(cls, record: HumanReviewQuarantineRecord) -> str:
        payload = {
            "authority_id": str(record.authority_id),
            "canonical_scope_sha256": record.canonical_scope_sha256,
            "cryptographic_domain": record.cryptographic_domain,
            "evidence_payload_sha256": record.evidence_payload_sha256,
            "lecture_id": str(record.lecture_id),
            "observed_signature_sha256": record.observed_signature_sha256,
            "payload_base_sha256": record.payload_base_sha256,
            "previous_record_sha256": record.previous_record_sha256,
            "reason_code": str(record.reason_code),
            "reason_kind": record.reason_kind.value,
            "registry_sequence": record.registry_sequence,
            "review_version": record.review_version,
            "signature_purpose": record.signature_purpose,
            "trust_key_id": record.trust_key_id,
        }
        canonical_record = json.dumps(
            {"domain": _RECORD_HASH_DOMAIN, "record": payload},
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical_record).hexdigest()

    @classmethod
    def _next_record(
        cls,
        records: tuple[HumanReviewQuarantineRecord, ...],
        evidence: HumanReviewQuarantineEvidence,
        kind: HumanReviewFailureKind,
        reason_code: SafeIdentifier,
    ) -> HumanReviewQuarantineRecord:
        incomplete = HumanReviewQuarantineRecord(
            registry_sequence=len(records) + 1,
            evidence_payload_sha256=evidence.identity.evidence_payload_sha256,
            cryptographic_domain=evidence.cryptographic_domain,
            signature_purpose=evidence.signature_purpose,
            trust_key_id=evidence.trust_key_id,
            authority_id=evidence.authority_id,
            lecture_id=evidence.lecture_id,
            canonical_scope_sha256=evidence.canonical_scope_sha256,
            payload_base_sha256=evidence.payload_base_sha256,
            review_version=evidence.review_version,
            reason_kind=kind,
            reason_code=reason_code,
            observed_signature_sha256=evidence.observed_signature_sha256,
            previous_record_sha256=(records[-1].record_sha256 if records else None),
            record_sha256="0" * 64,
        )
        return incomplete.model_copy(
            update={"record_sha256": cls._record_sha256(incomplete)}
        )

    @staticmethod
    def _insert_record(
        connection: sqlite3.Connection,
        record: HumanReviewQuarantineRecord,
    ) -> None:
        connection.execute(
            f"""
            INSERT INTO human_review_quarantine_records ({_RECORD_COLUMNS})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.registry_sequence,
                record.evidence_payload_sha256,
                record.cryptographic_domain,
                record.signature_purpose,
                record.trust_key_id,
                str(record.authority_id),
                str(record.lecture_id),
                record.canonical_scope_sha256,
                record.payload_base_sha256,
                record.review_version,
                record.reason_kind.value,
                str(record.reason_code),
                record.observed_signature_sha256,
                record.previous_record_sha256,
                record.record_sha256,
            ),
        )

    @staticmethod
    def _record_for_identity(
        records: tuple[HumanReviewQuarantineRecord, ...],
        evidence: HumanReviewQuarantineEvidence,
    ) -> HumanReviewQuarantineRecord | None:
        return next(
            (
                record
                for record in records
                if record.evidence_payload_sha256
                == evidence.identity.evidence_payload_sha256
            ),
            None,
        )

    @classmethod
    def _require_fresh(
        cls,
        records: tuple[HumanReviewQuarantineRecord, ...],
        evidence: HumanReviewQuarantineEvidence,
    ) -> None:
        if cls._record_for_identity(records, evidence) is not None:
            raise HumanReviewEvidenceQuarantined(
                "human-review evidence payload is quarantined"
            )
        same_scope = tuple(
            record
            for record in records
            if (
                record.canonical_scope_sha256 == evidence.canonical_scope_sha256
                and record.cryptographic_domain == evidence.cryptographic_domain
                and record.signature_purpose == evidence.signature_purpose
                and record.trust_key_id == evidence.trust_key_id
                and record.authority_id == evidence.authority_id
            )
        )
        if not same_scope:
            return
        payload_reused = any(
            record.payload_base_sha256 == evidence.payload_base_sha256
            for record in same_scope
        )
        signature_reused = any(
            record.observed_signature_sha256 == evidence.observed_signature_sha256
            for record in same_scope
        )
        latest_review_version = max(record.review_version for record in same_scope)
        if (
            payload_reused
            or signature_reused
            or evidence.review_version <= latest_review_version
        ):
            raise HumanReviewEvidenceQuarantined(
                "human-review evidence is not fresh for its quarantined scope"
            )

    def _validate_anchors(
        self,
        records: tuple[HumanReviewQuarantineRecord, ...],
    ) -> None:
        if (
            self._path_identity(
                self._anchor_directory,
                require_directory=True,
            )
            != self._anchor_directory_identity
        ):
            raise self._unavailable(
                "human-review quarantine anchor directory identity changed"
            )
        expected = {
            self._anchor_filename(record.registry_sequence): self._anchor_bytes(record)
            for record in records
        }
        try:
            actual_names = {entry.name for entry in os.scandir(self._anchor_directory)}
        except OSError as error:
            raise self._unavailable(
                "human-review quarantine anchors are unavailable"
            ) from error
        if actual_names != set(expected) or any(
            _ANCHOR_FILENAME_PATTERN.fullmatch(name) is None for name in actual_names
        ):
            raise HumanReviewQuarantineRegistryError(
                "human-review quarantine anchors do not match the database"
            )
        for name, expected_bytes in expected.items():
            if self._read_anchor(name) != expected_bytes:
                raise HumanReviewQuarantineRegistryError(
                    "human-review quarantine anchor is invalid"
                )

    def _append_anchor(self, record: HumanReviewQuarantineRecord) -> None:
        directory_descriptor = os.open(
            self._anchor_directory,
            os.O_RDONLY | self._directory_flag() | self._no_follow_flag(),
        )
        try:
            descriptor = os.open(
                self._anchor_filename(record.registry_sequence),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | self._no_follow_flag(),
                0o600,
                dir_fd=directory_descriptor,
            )
            try:
                self._write_all(descriptor, self._anchor_bytes(record))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)

    def _read_anchor(self, filename: str) -> bytes:
        directory_descriptor = os.open(
            self._anchor_directory,
            os.O_RDONLY | self._directory_flag() | self._no_follow_flag(),
        )
        try:
            descriptor = os.open(
                filename,
                os.O_RDONLY | self._no_follow_flag(),
                dir_fd=directory_descriptor,
            )
            try:
                file_stat = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(file_stat.st_mode)
                    or file_stat.st_nlink != 1
                    or file_stat.st_size > 512
                ):
                    raise HumanReviewQuarantineRegistryError(
                        "human-review quarantine anchor type is invalid"
                    )
                return os.read(descriptor, 513)
            finally:
                os.close(descriptor)
        finally:
            os.close(directory_descriptor)

    @staticmethod
    def _anchor_filename(sequence: int) -> str:
        return f"{sequence:020d}.anchor"

    @staticmethod
    def _anchor_bytes(record: HumanReviewQuarantineRecord) -> bytes:
        return (
            json.dumps(
                {
                    "record_sha256": record.record_sha256,
                    "registry_sequence": record.registry_sequence,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")

    @staticmethod
    def _write_all(descriptor: int, value: bytes) -> None:
        offset = 0
        while offset < len(value):
            written = os.write(descriptor, value[offset:])
            if written <= 0:
                raise OSError("anchor write did not make progress")
            offset += written

    @contextmanager
    def _exclusive_root_lock(self) -> Iterator[None]:
        deadline = time.monotonic() + self._lock_timeout_seconds
        self._require_root_identity()
        root_descriptor = os.open(
            self._root,
            os.O_RDONLY | self._directory_flag() | self._no_follow_flag(),
        )
        try:
            root_stat = os.fstat(root_descriptor)
            root_identity = (
                root_stat.st_dev,
                root_stat.st_ino,
                root_stat.st_mode,
                0,
            )
            if root_identity != self._root_identity:
                raise self._unavailable("human-review quarantine root identity changed")
            self._acquire_exclusive_lock(root_descriptor, deadline)
            self._require_root_identity()
            if self._path_identity(self._lock_path) != self._lock_identity:
                raise self._unavailable("human-review quarantine lock identity changed")
            descriptor = os.open(
                self._lock_path,
                os.O_RDWR | self._no_follow_flag(),
            )
            try:
                if self._descriptor_identity(descriptor) != self._lock_identity:
                    raise self._unavailable(
                        "human-review quarantine lock identity changed"
                    )
                self._acquire_exclusive_lock(descriptor, deadline)
                self._require_root_identity()
                if (
                    self._path_identity(self._lock_path) != self._lock_identity
                    or self._descriptor_identity(descriptor) != self._lock_identity
                ):
                    raise self._unavailable(
                        "human-review quarantine lock identity changed after acquisition"
                    )
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
        finally:
            fcntl.flock(root_descriptor, fcntl.LOCK_UN)
            os.close(root_descriptor)

    def _acquire_exclusive_lock(self, descriptor: int, deadline: float) -> None:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError as error:
                remaining_seconds = deadline - time.monotonic()
                if remaining_seconds <= 0:
                    raise self._unavailable(
                        "human-review quarantine lock acquisition timed out"
                    ) from error
                time.sleep(min(0.01, remaining_seconds))
            except OSError as error:
                raise self._unavailable(
                    "human-review quarantine lock is unavailable"
                ) from error

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        before = self._path_identity(self._database_path)
        if before != self._database_identity:
            raise self._unavailable("human-review quarantine database identity changed")
        try:
            connection = sqlite3.connect(
                f"{self._database_path.as_uri()}?mode=rw",
                uri=True,
                isolation_level=None,
                timeout=self._busy_timeout_milliseconds / 1_000,
            )
        except sqlite3.Error as error:
            raise self._unavailable(
                "human-review quarantine database connection failed"
            ) from error
        try:
            try:
                connection.execute(
                    f"PRAGMA busy_timeout = {self._busy_timeout_milliseconds}"
                )
                connection.execute("PRAGMA trusted_schema = OFF")
                connection.execute("PRAGMA foreign_keys = ON")
                journal_mode = connection.execute(
                    "PRAGMA journal_mode = DELETE"
                ).fetchone()
                connection.execute("PRAGMA synchronous = FULL")
                synchronous = connection.execute("PRAGMA synchronous").fetchone()
            except sqlite3.Error as error:
                raise self._unavailable(
                    "human-review quarantine durability configuration failed"
                ) from error
            if (
                journal_mode is None
                or str(journal_mode[0]).lower() != "delete"
                or synchronous is None
                or int(synchronous[0]) != 2
            ):
                raise self._unavailable(
                    "human-review quarantine durability configuration failed"
                )
            yield connection
        finally:
            connection.close()
            if self._path_identity(self._database_path) != before:
                raise self._unavailable(
                    "human-review quarantine database changed during access"
                )

    def _open_or_create_lock_file(self) -> None:
        flags = os.O_RDWR | self._no_follow_flag()
        if self._mode is SqliteHumanReviewQuarantineRegistryOpenMode.INITIALIZE:
            flags |= os.O_CREAT | os.O_EXCL
        descriptor = os.open(self._lock_path, flags, 0o600)
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
                raise self._unavailable("human-review quarantine lock type is invalid")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._fsync_root()

    def _fsync_database(self) -> None:
        descriptor = os.open(
            self._database_path,
            os.O_RDONLY | self._no_follow_flag(),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._fsync_root()

    def _fsync_root(self) -> None:
        descriptor = os.open(
            self._root,
            os.O_RDONLY | self._directory_flag() | self._no_follow_flag(),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _require_root_identity(self) -> None:
        if (
            self._path_identity(self._root, require_directory=True)
            != self._root_identity
        ):
            raise self._unavailable("human-review quarantine root identity changed")

    @staticmethod
    def _validated_mode(
        mode: SqliteHumanReviewQuarantineRegistryOpenMode,
    ) -> SqliteHumanReviewQuarantineRegistryOpenMode:
        if not isinstance(mode, SqliteHumanReviewQuarantineRegistryOpenMode):
            raise TypeError(
                "mode must be a SqliteHumanReviewQuarantineRegistryOpenMode"
            )
        return mode

    @staticmethod
    def _validated_root(
        registry_root: Path,
        mode: SqliteHumanReviewQuarantineRegistryOpenMode,
    ) -> Path:
        if not isinstance(registry_root, Path):
            raise TypeError("registry_root must be a pathlib.Path")
        if not registry_root.is_absolute():
            raise ValueError("registry_root must be absolute")
        if mode is SqliteHumanReviewQuarantineRegistryOpenMode.INITIALIZE:
            try:
                registry_root.mkdir(mode=0o700, parents=False, exist_ok=False)
            except OSError as error:
                raise HumanReviewQuarantineRegistryError(
                    "human-review quarantine root cannot be initialized"
                ) from error
        elif not registry_root.exists():
            raise HumanReviewQuarantineRegistryError(
                "established human-review quarantine root is unavailable"
            )
        file_stat = registry_root.lstat()
        if not stat.S_ISDIR(file_stat.st_mode):
            raise HumanReviewQuarantineRegistryError(
                "human-review quarantine root must be a real directory"
            )
        for ancestor in (registry_root, *registry_root.parents):
            if stat.S_ISLNK(ancestor.lstat().st_mode):
                raise HumanReviewQuarantineRegistryError(
                    "human-review quarantine root ancestors must not be symlinks"
                )
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
    def _validated_evidence(
        evidence: HumanReviewQuarantineEvidence,
    ) -> HumanReviewQuarantineEvidence:
        try:
            return HumanReviewQuarantineEvidence.model_validate(
                evidence.model_dump(mode="python"),
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise HumanReviewQuarantineRegistryError(
                "human-review quarantine evidence is invalid"
            ) from error

    @staticmethod
    def _validated_kind(kind: HumanReviewFailureKind) -> HumanReviewFailureKind:
        if (
            not isinstance(kind, HumanReviewFailureKind)
            or kind not in _QUARANTINE_FAILURE_KINDS
        ):
            raise HumanReviewQuarantineRegistryError(
                "human-review failure kind is not quarantine-eligible"
            )
        return kind

    @staticmethod
    def _validated_reason_code(reason_code: str) -> SafeIdentifier:
        try:
            return _SAFE_IDENTIFIER_ADAPTER.validate_python(
                reason_code,
                strict=True,
            )
        except (TypeError, ValueError) as error:
            raise HumanReviewQuarantineRegistryError(
                "human-review quarantine reason code is invalid"
            ) from error

    @staticmethod
    def _path_exists(path: Path) -> bool:
        try:
            path.lstat()
        except FileNotFoundError:
            return False
        return True

    @staticmethod
    def _path_identity(
        path: Path,
        *,
        require_directory: bool = False,
    ) -> tuple[int, int, int, int]:
        try:
            file_stat = path.lstat()
        except OSError as error:
            raise HumanReviewQuarantineRegistryError(
                "human-review quarantine path is unavailable"
            ) from error
        required_type = stat.S_ISDIR if require_directory else stat.S_ISREG
        if not required_type(file_stat.st_mode):
            raise HumanReviewQuarantineRegistryError(
                "human-review quarantine path type is invalid"
            )
        if not require_directory and file_stat.st_nlink != 1:
            raise HumanReviewQuarantineRegistryError(
                "human-review quarantine file must have one link"
            )
        return (
            file_stat.st_dev,
            file_stat.st_ino,
            file_stat.st_mode,
            0 if require_directory else file_stat.st_nlink,
        )

    @staticmethod
    def _descriptor_identity(descriptor: int) -> tuple[int, int, int, int]:
        file_stat = os.fstat(descriptor)
        return (
            file_stat.st_dev,
            file_stat.st_ino,
            file_stat.st_mode,
            file_stat.st_nlink,
        )

    @staticmethod
    def _directory_flag() -> int:
        return getattr(os, "O_DIRECTORY", 0)

    @staticmethod
    def _no_follow_flag() -> int:
        return getattr(os, "O_NOFOLLOW", 0)

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        if connection.in_transaction:
            connection.rollback()

    @staticmethod
    def _unavailable(message: str) -> HumanReviewQuarantineRegistryError:
        return HumanReviewQuarantineRegistryError(message)

"""Persistent append-only current-state adapter for expert-corpus promotion."""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import os
import re
import sqlite3
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterator, Literal, NoReturn, Sequence

from pydantic import TypeAdapter

from lecture_study.domain.expert_corpus import (
    AuthenticatedVeterinaryExpertCorpus,
)
from lecture_study.domain.models import SafeIdentifier
from lecture_study.quality.expert_corpus import (
    ExpertCorpusCurrentStateObservation,
    ExpertCorpusFailureKind,
    ExpertCorpusPromotionError,
    ExpertCorpusTrustedCurrentStatePort,
    canonical_expert_corpus_evidence_sha256,
)
from lecture_study.quality.trust import canonical_json_bytes

_DATABASE_FILENAME = "expert-corpus-current-state.sqlite3"
_LOCK_FILENAME = ".expert-corpus-current-state.lock"
_ANCHOR_DIRECTORY = "expert-corpus-current-state-anchors"
_ANCHOR_FILENAME_PATTERN = re.compile(r"^[0-9]{20}\.anchor$")
_SCHEMA_VERSION = 1
_GENESIS_SHA256 = hashlib.sha256(
    b"lecture-study:expert-corpus:current-state-genesis:v1"
).hexdigest()
_EVENT_HASH_CONTEXT = b"lecture-study:expert-corpus:current-state-event:v1\0"
_EVIDENCE_RECORD_HASH_CONTEXT = (
    b"lecture-study:expert-corpus:current-state-evidence-record:v1\0"
)
_SAFE_IDENTIFIER_ADAPTER = TypeAdapter(SafeIdentifier)
_PROMOTED: Literal["promoted"] = "promoted"
_CHECKED: Literal["checked"] = "checked"
_STALE: Literal["stale"] = "stale"
_REVOKED: Literal["revoked"] = "revoked"
_EventKind = Literal["promoted", "checked", "stale", "revoked"]

_METADATA_TABLE_SQL = f"""
CREATE TABLE expert_corpus_current_state_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL CHECK (schema_version = {_SCHEMA_VERSION}),
    schema_sha256 TEXT NOT NULL,
    genesis_sha256 TEXT NOT NULL
)
"""
_EVENT_TABLE_SQL = """
CREATE TABLE expert_corpus_current_state_events (
    sequence INTEGER PRIMARY KEY CHECK (sequence > 0),
    event_kind TEXT NOT NULL
        CHECK (event_kind IN ('promoted', 'checked', 'stale', 'revoked')),
    observed_at_epoch_seconds INTEGER NOT NULL
        CHECK (observed_at_epoch_seconds >= 0),
    observed_sequence INTEGER NOT NULL UNIQUE CHECK (observed_sequence > 0),
    reason_code TEXT NOT NULL,
    promotion_id TEXT NOT NULL,
    corpus_id TEXT NOT NULL,
    corpus_version TEXT NOT NULL,
    cryptographic_domain TEXT NOT NULL,
    signature_purpose TEXT NOT NULL,
    trust_key_id TEXT NOT NULL,
    authority_id TEXT NOT NULL,
    evidence_payload_sha256 TEXT NOT NULL,
    evidence_record_sha256 TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    previous_entry_sha256 TEXT NOT NULL,
    entry_sha256 TEXT NOT NULL UNIQUE
)
"""
_PROMOTION_INDEX_SQL = """
CREATE UNIQUE INDEX expert_corpus_current_state_promotion_unique
ON expert_corpus_current_state_events (
    cryptographic_domain,
    signature_purpose,
    trust_key_id,
    authority_id,
    promotion_id
)
WHERE event_kind = 'promoted'
"""
_CORPUS_VERSION_INDEX_SQL = """
CREATE UNIQUE INDEX expert_corpus_current_state_corpus_version_unique
ON expert_corpus_current_state_events (
    corpus_id,
    corpus_version
)
WHERE event_kind = 'promoted'
"""
_EVENT_NO_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER expert_corpus_current_state_event_no_update
BEFORE UPDATE ON expert_corpus_current_state_events
BEGIN
    SELECT RAISE(ABORT, 'expert corpus current-state events are append-only');
END
"""
_EVENT_NO_DELETE_TRIGGER_SQL = """
CREATE TRIGGER expert_corpus_current_state_event_no_delete
BEFORE DELETE ON expert_corpus_current_state_events
BEGIN
    SELECT RAISE(ABORT, 'expert corpus current-state events are append-only');
END
"""
_METADATA_NO_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER expert_corpus_current_state_metadata_no_update
BEFORE UPDATE ON expert_corpus_current_state_metadata
BEGIN
    SELECT RAISE(ABORT, 'expert corpus current-state metadata is immutable');
END
"""
_METADATA_NO_DELETE_TRIGGER_SQL = """
CREATE TRIGGER expert_corpus_current_state_metadata_no_delete
BEFORE DELETE ON expert_corpus_current_state_metadata
BEGIN
    SELECT RAISE(ABORT, 'expert corpus current-state metadata is immutable');
END
"""
_CHAIN_TRIGGER_SQL = f"""
CREATE TRIGGER expert_corpus_current_state_event_chain
BEFORE INSERT ON expert_corpus_current_state_events
WHEN
    NEW.sequence != COALESCE(
        (SELECT MAX(sequence) + 1 FROM expert_corpus_current_state_events),
        1
    )
    OR NEW.previous_entry_sha256 != COALESCE(
        (
            SELECT entry_sha256
            FROM expert_corpus_current_state_events
            ORDER BY sequence DESC
            LIMIT 1
        ),
        '{_GENESIS_SHA256}'
    )
BEGIN
    SELECT RAISE(ABORT, 'expert corpus current-state chain is discontinuous');
END
"""
_SCHEMA_STATEMENTS = (
    _METADATA_TABLE_SQL,
    _EVENT_TABLE_SQL,
    _PROMOTION_INDEX_SQL,
    _CORPUS_VERSION_INDEX_SQL,
    _EVENT_NO_UPDATE_TRIGGER_SQL,
    _EVENT_NO_DELETE_TRIGGER_SQL,
    _METADATA_NO_UPDATE_TRIGGER_SQL,
    _METADATA_NO_DELETE_TRIGGER_SQL,
    _CHAIN_TRIGGER_SQL,
)


def _normalized_sql(statement: str) -> str:
    return " ".join(statement.split()).rstrip(";")


_SCHEMA_SHA256 = hashlib.sha256(
    "\n".join(_normalized_sql(statement) for statement in _SCHEMA_STATEMENTS).encode()
).hexdigest()
_EXPECTED_SCHEMA_OBJECTS = {
    "expert_corpus_current_state_metadata": (
        "table",
        _normalized_sql(_METADATA_TABLE_SQL),
    ),
    "expert_corpus_current_state_events": (
        "table",
        _normalized_sql(_EVENT_TABLE_SQL),
    ),
    "expert_corpus_current_state_promotion_unique": (
        "index",
        _normalized_sql(_PROMOTION_INDEX_SQL),
    ),
    "expert_corpus_current_state_corpus_version_unique": (
        "index",
        _normalized_sql(_CORPUS_VERSION_INDEX_SQL),
    ),
    "expert_corpus_current_state_event_no_update": (
        "trigger",
        _normalized_sql(_EVENT_NO_UPDATE_TRIGGER_SQL),
    ),
    "expert_corpus_current_state_event_no_delete": (
        "trigger",
        _normalized_sql(_EVENT_NO_DELETE_TRIGGER_SQL),
    ),
    "expert_corpus_current_state_metadata_no_update": (
        "trigger",
        _normalized_sql(_METADATA_NO_UPDATE_TRIGGER_SQL),
    ),
    "expert_corpus_current_state_metadata_no_delete": (
        "trigger",
        _normalized_sql(_METADATA_NO_DELETE_TRIGGER_SQL),
    ),
    "expert_corpus_current_state_event_chain": (
        "trigger",
        _normalized_sql(_CHAIN_TRIGGER_SQL),
    ),
}


class SqliteExpertCorpusCurrentStateOpenMode(StrEnum):
    """Explicit initialization versus opening of one supplied registry root."""

    INITIALIZE = "initialize"
    OPEN_EXISTING = "open_existing"


@dataclass(frozen=True, slots=True)
class _CurrentStateEvent:
    sequence: int
    event_kind: _EventKind
    observation: ExpertCorpusCurrentStateObservation
    reason_code: str
    evidence: AuthenticatedVeterinaryExpertCorpus
    evidence_payload_sha256: str
    evidence_record_sha256: str
    previous_entry_sha256: str
    entry_sha256: str


class SqliteExpertCorpusCurrentState:
    """Persist exact promoted-corpus state behind the inward current-state port."""

    def __init__(
        self,
        *,
        registry_root: Path,
        mode: SqliteExpertCorpusCurrentStateOpenMode,
        busy_timeout_milliseconds: int,
        lock_timeout_milliseconds: int,
        trusted_current_state: ExpertCorpusTrustedCurrentStatePort,
    ) -> None:
        self._root = self._validated_root(registry_root)
        self._root_identity = self._path_identity(self._root, directory=True)
        self._busy_timeout_milliseconds = self._validated_timeout(
            busy_timeout_milliseconds,
            "busy_timeout_milliseconds",
        )
        self._lock_timeout_seconds = (
            self._validated_timeout(
                lock_timeout_milliseconds,
                "lock_timeout_milliseconds",
            )
            / 1_000
        )
        if not isinstance(mode, SqliteExpertCorpusCurrentStateOpenMode):
            raise TypeError("mode must be a SqliteExpertCorpusCurrentStateOpenMode")
        if not isinstance(
            trusted_current_state,
            ExpertCorpusTrustedCurrentStatePort,
        ):
            raise TypeError(
                "trusted_current_state must implement "
                "ExpertCorpusTrustedCurrentStatePort"
            )
        self._trusted_current_state = trusted_current_state
        if mode is SqliteExpertCorpusCurrentStateOpenMode.INITIALIZE:
            self._initialize_root()
        else:
            self._require_existing_layout()
        self._lock_identity = self._path_identity(self._lock_path)
        self._anchor_directory_identity = self._path_identity(
            self._anchor_directory,
            directory=True,
        )
        self._database_identity = self._path_identity(self._database_path)
        with self._exclusive_lock():
            with self._connection() as connection:
                self._validate_critical_state(connection)

    @property
    def database_path(self) -> Path:
        """Return the exact database path under the explicitly supplied root."""

        return self._database_path

    def record_promotion(
        self,
        evidence: AuthenticatedVeterinaryExpertCorpus,
        *,
        evidence_payload_sha256: str,
    ) -> None:
        """Persist one exact promotion under a trusted current observation."""

        validated = self._validated_evidence(evidence, evidence_payload_sha256)
        with self._exclusive_lock():
            with self._connection() as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    events = self._validate_critical_state(connection)
                    observation = self._observe_trusted_current_state()
                    self._require_observation_appendable(events, observation)
                    event_kind: _EventKind
                    reason_code: str
                    if self._contains_exact(events, validated):
                        self._require_not_terminal(events, validated)
                        self._require_within_validity(validated, observation)
                        event_kind = _CHECKED
                        reason_code = "promotion_reissue_current"
                    else:
                        self._require_identity_unused(events, validated)
                        self._require_within_validity(validated, observation)
                        event_kind = _PROMOTED
                        reason_code = "promotion_recorded"
                    event = self._next_event(
                        events,
                        event_kind=event_kind,
                        observation=observation,
                        reason_code=reason_code,
                        evidence=validated,
                    )
                    self._persist_event(connection, event)
                except ExpertCorpusPromotionError:
                    self._rollback(connection)
                    raise
                except sqlite3.IntegrityError as error:
                    self._rollback(connection)
                    raise self._conflict(
                        "expert corpus promotion identity is already bound"
                    ) from error
                except (OSError, sqlite3.Error) as error:
                    self._rollback(connection)
                    raise self._unavailable(
                        "expert corpus current state could not persist promotion"
                    ) from error

    def require_current(
        self,
        evidence: AuthenticatedVeterinaryExpertCorpus,
        *,
        evidence_payload_sha256: str,
    ) -> None:
        """Append a trusted check or stale terminal state before consumption."""

        validated = self._validated_evidence(evidence, evidence_payload_sha256)
        with self._exclusive_lock():
            with self._connection() as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    events = self._validate_critical_state(connection)
                    self._require_exact_promotion(events, validated)
                    observation = self._observe_trusted_current_state()
                    self._require_observation_appendable(events, observation)
                    self._require_not_terminal(events, validated)
                    if observation.epoch_seconds < (
                        validated.request.issued_at_epoch_seconds
                    ):
                        raise self._unavailable(
                            "trusted current state predates corpus issuance"
                        )
                    is_stale = observation.epoch_seconds >= (
                        validated.request.expires_at_epoch_seconds
                    )
                    event = self._next_event(
                        events,
                        event_kind=_STALE if is_stale else _CHECKED,
                        observation=observation,
                        reason_code=(
                            "promotion_expired" if is_stale else "promotion_current"
                        ),
                        evidence=validated,
                    )
                    self._persist_event(connection, event)
                    if is_stale:
                        raise self._stale()
                except ExpertCorpusPromotionError:
                    self._rollback(connection)
                    raise
                except (OSError, sqlite3.Error) as error:
                    self._rollback(connection)
                    raise self._unavailable(
                        "expert corpus current state could not be revalidated"
                    ) from error

    def record_revocation(
        self,
        evidence: AuthenticatedVeterinaryExpertCorpus,
        *,
        evidence_payload_sha256: str,
        reason_code: str,
    ) -> None:
        """Append a permanent restrictive revocation for one exact promotion."""

        validated = self._validated_evidence(evidence, evidence_payload_sha256)
        validated_reason = str(
            _SAFE_IDENTIFIER_ADAPTER.validate_python(reason_code, strict=True)
        )
        with self._exclusive_lock():
            with self._connection() as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    events = self._validate_critical_state(connection)
                    self._require_exact_promotion(events, validated)
                    observation = self._observe_trusted_current_state()
                    self._require_observation_appendable(events, observation)
                    terminal = self._terminal_event(events, validated)
                    if terminal is not None:
                        if (
                            terminal.event_kind == _REVOKED
                            and terminal.reason_code == validated_reason
                        ):
                            connection.commit()
                            return
                        self._raise_terminal(terminal)
                    event = self._next_event(
                        events,
                        event_kind=_REVOKED,
                        observation=observation,
                        reason_code=validated_reason,
                        evidence=validated,
                    )
                    self._persist_event(connection, event)
                except ExpertCorpusPromotionError:
                    self._rollback(connection)
                    raise
                except (OSError, sqlite3.Error) as error:
                    self._rollback(connection)
                    raise self._unavailable(
                        "expert corpus current state could not persist revocation"
                    ) from error

    def _persist_event(
        self,
        connection: sqlite3.Connection,
        event: _CurrentStateEvent,
    ) -> None:
        self._insert_event(connection, event)
        self._validate_database_chain(connection)
        connection.commit()
        self._checkpoint(connection)
        self._fsync_database()
        self._append_anchor(event)
        self._validate_critical_state(connection)

    def _observe_trusted_current_state(
        self,
    ) -> ExpertCorpusCurrentStateObservation:
        try:
            observation = self._trusted_current_state.observe()
            if not isinstance(observation, ExpertCorpusCurrentStateObservation):
                raise TypeError("trusted observation has an invalid type")
            return ExpertCorpusCurrentStateObservation(
                epoch_seconds=observation.epoch_seconds,
                sequence=observation.sequence,
            )
        except Exception as error:
            raise self._unavailable(
                "trusted expert corpus current state is unavailable"
            ) from error

    @staticmethod
    def _require_observation_appendable(
        events: Sequence[_CurrentStateEvent],
        observation: ExpertCorpusCurrentStateObservation,
    ) -> None:
        if not events:
            return
        previous = events[-1].observation
        if (
            observation.sequence <= previous.sequence
            or observation.epoch_seconds < previous.epoch_seconds
        ):
            raise SqliteExpertCorpusCurrentState._unavailable(
                "trusted expert corpus current state rolled back"
            )

    @staticmethod
    def _require_within_validity(
        evidence: AuthenticatedVeterinaryExpertCorpus,
        observation: ExpertCorpusCurrentStateObservation,
    ) -> None:
        if observation.epoch_seconds < evidence.request.issued_at_epoch_seconds:
            raise ExpertCorpusPromotionError(
                "expert corpus promotion is not yet valid",
                kind=ExpertCorpusFailureKind.UNVERIFIABLE,
                reason_code="promotion_not_yet_valid",
            )
        if observation.epoch_seconds >= evidence.request.expires_at_epoch_seconds:
            raise SqliteExpertCorpusCurrentState._stale()

    @classmethod
    def _require_exact_promotion(
        cls,
        events: Sequence[_CurrentStateEvent],
        evidence: AuthenticatedVeterinaryExpertCorpus,
    ) -> None:
        if cls._contains_exact(events, evidence):
            return
        if cls._shares_identity(events, evidence):
            raise cls._conflict(
                "expert corpus promotion identity has different evidence"
            )
        raise ExpertCorpusPromotionError(
            "expert corpus promotion is absent from current state",
            kind=ExpertCorpusFailureKind.MISSING,
            reason_code="missing_persisted_corpus_promotion",
        )

    @classmethod
    def _require_not_terminal(
        cls,
        events: Sequence[_CurrentStateEvent],
        evidence: AuthenticatedVeterinaryExpertCorpus,
    ) -> None:
        terminal = cls._terminal_event(events, evidence)
        if terminal is not None:
            cls._raise_terminal(terminal)

    @staticmethod
    def _terminal_event(
        events: Sequence[_CurrentStateEvent],
        evidence: AuthenticatedVeterinaryExpertCorpus,
    ) -> _CurrentStateEvent | None:
        identity = canonical_expert_corpus_evidence_sha256(evidence)
        return next(
            (
                event
                for event in reversed(events)
                if event.evidence_payload_sha256 == identity
                and event.event_kind in {_STALE, _REVOKED}
            ),
            None,
        )

    @staticmethod
    def _raise_terminal(event: _CurrentStateEvent) -> NoReturn:
        if event.event_kind == _REVOKED:
            raise ExpertCorpusPromotionError(
                "expert corpus promotion was revoked",
                kind=ExpertCorpusFailureKind.REVOKED,
                reason_code=event.reason_code,
            )
        raise SqliteExpertCorpusCurrentState._stale()

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
                "expert corpus current-state lock initialization failed"
            ) from error
        with self._exclusive_lock(validate_stored_identity=False):
            try:
                os.mkdir(self._anchor_directory, mode=0o700)
                self._create_database_file()
                self._fsync_root()
                self._lock_identity = self._path_identity(self._lock_path)
                self._anchor_directory_identity = self._path_identity(
                    self._anchor_directory,
                    directory=True,
                )
                self._database_identity = self._path_identity(self._database_path)
                with self._connection(
                    require_stored_identity=False,
                    allow_delete_journal_mode=True,
                ) as connection:
                    journal = connection.execute("PRAGMA journal_mode = WAL").fetchone()
                    if journal is None or str(journal[0]).lower() != "wal":
                        raise self._unavailable(
                            "expert corpus current state could not enable WAL"
                        )
                    connection.execute("BEGIN IMMEDIATE")
                    for statement in _SCHEMA_STATEMENTS:
                        connection.execute(statement)
                    connection.execute(
                        """
                        INSERT INTO expert_corpus_current_state_metadata (
                            singleton, schema_version, schema_sha256, genesis_sha256
                        ) VALUES (1, ?, ?, ?)
                        """,
                        (_SCHEMA_VERSION, _SCHEMA_SHA256, _GENESIS_SHA256),
                    )
                    connection.commit()
                    self._checkpoint(connection)
                    self._fsync_database()
                    self._validate_critical_state(connection)
            except ExpertCorpusPromotionError:
                raise
            except (OSError, sqlite3.Error) as error:
                raise self._unavailable(
                    "expert corpus current-state initialization failed"
                ) from error

    def _require_existing_layout(self) -> None:
        self._path_identity(self._lock_path)
        self._path_identity(self._anchor_directory, directory=True)
        self._path_identity(self._database_path)

    def _validate_critical_state(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[_CurrentStateEvent, ...]:
        self._validate_quick_check(connection)
        self._validate_schema(connection)
        events = self._validate_database_chain(connection)
        self._validate_anchors(events)
        return events

    @staticmethod
    def _validate_quick_check(connection: sqlite3.Connection) -> None:
        result = tuple(str(row[0]) for row in connection.execute("PRAGMA quick_check"))
        if result != ("ok",):
            raise SqliteExpertCorpusCurrentState._unavailable(
                "expert corpus current-state quick_check failed"
            )

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        metadata = connection.execute(
            """
            SELECT schema_version, schema_sha256, genesis_sha256
            FROM expert_corpus_current_state_metadata
            WHERE singleton = 1
            """
        ).fetchall()
        if metadata != [(_SCHEMA_VERSION, _SCHEMA_SHA256, _GENESIS_SHA256)]:
            raise SqliteExpertCorpusCurrentState._unavailable(
                "expert corpus current-state metadata is invalid"
            )
        observed_rows = connection.execute(
            """
            SELECT type, name, sql
            FROM sqlite_master
            WHERE name GLOB 'expert_corpus_current_state_*'
              AND sql IS NOT NULL
            """
        ).fetchall()
        observed = {
            str(name): (str(object_type), _normalized_sql(str(sql)))
            for object_type, name, sql in observed_rows
        }
        if observed != _EXPECTED_SCHEMA_OBJECTS:
            raise SqliteExpertCorpusCurrentState._unavailable(
                "expert corpus current-state schema is invalid"
            )

    def _validate_database_chain(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[_CurrentStateEvent, ...]:
        rows = connection.execute(
            """
            SELECT
                sequence, event_kind, observed_at_epoch_seconds,
                observed_sequence, reason_code, evidence_payload_sha256,
                evidence_record_sha256, evidence_json,
                previous_entry_sha256, entry_sha256
            FROM expert_corpus_current_state_events
            ORDER BY sequence
            """
        ).fetchall()
        events: list[_CurrentStateEvent] = []
        previous = _GENESIS_SHA256
        for expected_sequence, row in enumerate(rows, start=1):
            (
                sequence,
                event_kind,
                observed_at_epoch_seconds,
                observed_sequence,
                reason_code,
                payload_sha256,
                record_sha256,
                evidence_json,
                previous_hash,
                entry_hash,
            ) = row
            if type(sequence) is not int or sequence != expected_sequence:
                raise self._unavailable(
                    "expert corpus current-state sequence is discontinuous"
                )
            try:
                evidence = AuthenticatedVeterinaryExpertCorpus.model_validate_json(
                    str(evidence_json)
                )
            except (TypeError, ValueError) as error:
                raise self._unavailable(
                    "expert corpus current-state evidence is invalid"
                ) from error
            event = _CurrentStateEvent(
                sequence=sequence,
                event_kind=self._validated_event_kind(str(event_kind)),
                observation=ExpertCorpusCurrentStateObservation(
                    epoch_seconds=observed_at_epoch_seconds,
                    sequence=observed_sequence,
                ),
                reason_code=str(
                    _SAFE_IDENTIFIER_ADAPTER.validate_python(
                        reason_code,
                        strict=True,
                    )
                ),
                evidence=evidence,
                evidence_payload_sha256=str(payload_sha256),
                evidence_record_sha256=str(record_sha256),
                previous_entry_sha256=str(previous_hash),
                entry_sha256=str(entry_hash),
            )
            if not hmac.compare_digest(event.previous_entry_sha256, previous):
                raise self._unavailable(
                    "expert corpus current-state previous hash is invalid"
                )
            if not hmac.compare_digest(
                canonical_expert_corpus_evidence_sha256(evidence),
                event.evidence_payload_sha256,
            ):
                raise self._unavailable(
                    "expert corpus current-state payload identity is invalid"
                )
            if not hmac.compare_digest(
                self._evidence_record_sha256(evidence),
                event.evidence_record_sha256,
            ):
                raise self._unavailable(
                    "expert corpus current-state stored evidence was substituted"
                )
            expected_hash = self._event_sha256(event)
            if not hmac.compare_digest(expected_hash, event.entry_sha256):
                raise self._unavailable(
                    "expert corpus current-state event hash is invalid"
                )
            self._require_row_projection(connection, event)
            self._require_observation_appendable(events, event.observation)
            events.append(event)
            previous = event.entry_sha256
        self._require_unique_identities(events)
        self._require_valid_lifecycle(events)
        return tuple(events)

    @staticmethod
    def _require_row_projection(
        connection: sqlite3.Connection,
        event: _CurrentStateEvent,
    ) -> None:
        row = connection.execute(
            """
            SELECT
                promotion_id, corpus_id, corpus_version, cryptographic_domain,
                signature_purpose, trust_key_id, authority_id
            FROM expert_corpus_current_state_events
            WHERE sequence = ?
            """,
            (event.sequence,),
        ).fetchone()
        request = event.evidence.request
        expected = (
            str(request.promotion_id),
            str(request.corpus_manifest.corpus_id),
            str(request.corpus_manifest.corpus_version),
            event.evidence.cryptographic_domain,
            event.evidence.signature_purpose,
            event.evidence.trust_key_id,
            str(event.evidence.authority_id),
        )
        if row != expected:
            raise SqliteExpertCorpusCurrentState._unavailable(
                "expert corpus current-state projection was substituted"
            )

    @classmethod
    def _require_unique_identities(
        cls,
        events: Sequence[_CurrentStateEvent],
    ) -> None:
        promotions = [event for event in events if event.event_kind == _PROMOTED]
        promotion_keys = [cls._promotion_key(event.evidence) for event in promotions]
        corpus_keys = [cls._corpus_key(event.evidence) for event in promotions]
        if len(promotion_keys) != len(set(promotion_keys)) or len(corpus_keys) != len(
            set(corpus_keys)
        ):
            raise cls._unavailable("expert corpus current-state identity was reused")

    @classmethod
    def _require_valid_lifecycle(
        cls,
        events: Sequence[_CurrentStateEvent],
    ) -> None:
        promoted: set[str] = set()
        terminal: set[str] = set()
        for event in events:
            identity = event.evidence_payload_sha256
            if event.event_kind == _PROMOTED:
                if identity in promoted:
                    raise cls._unavailable(
                        "expert corpus promotion was appended more than once"
                    )
                promoted.add(identity)
                continue
            if identity not in promoted:
                raise cls._unavailable(
                    "expert corpus current-state event has no promotion"
                )
            if identity in terminal:
                raise cls._unavailable(
                    "expert corpus current-state event follows a terminal event"
                )
            if event.event_kind in {_STALE, _REVOKED}:
                terminal.add(identity)

    def _next_event(
        self,
        events: Sequence[_CurrentStateEvent],
        *,
        event_kind: _EventKind,
        observation: ExpertCorpusCurrentStateObservation,
        reason_code: str,
        evidence: AuthenticatedVeterinaryExpertCorpus,
    ) -> _CurrentStateEvent:
        event = _CurrentStateEvent(
            sequence=len(events) + 1,
            event_kind=event_kind,
            observation=observation,
            reason_code=reason_code,
            evidence=evidence,
            evidence_payload_sha256=canonical_expert_corpus_evidence_sha256(evidence),
            evidence_record_sha256=self._evidence_record_sha256(evidence),
            previous_entry_sha256=(
                events[-1].entry_sha256 if events else _GENESIS_SHA256
            ),
            entry_sha256="",
        )
        return _CurrentStateEvent(
            sequence=event.sequence,
            event_kind=event.event_kind,
            observation=event.observation,
            reason_code=event.reason_code,
            evidence=event.evidence,
            evidence_payload_sha256=event.evidence_payload_sha256,
            evidence_record_sha256=event.evidence_record_sha256,
            previous_entry_sha256=event.previous_entry_sha256,
            entry_sha256=self._event_sha256(event),
        )

    @staticmethod
    def _event_sha256(event: _CurrentStateEvent) -> str:
        request = event.evidence.request
        payload = {
            "authority_id": str(event.evidence.authority_id),
            "corpus_id": str(request.corpus_manifest.corpus_id),
            "corpus_version": str(request.corpus_manifest.corpus_version),
            "cryptographic_domain": event.evidence.cryptographic_domain,
            "evidence_payload_sha256": event.evidence_payload_sha256,
            "evidence_record_sha256": event.evidence_record_sha256,
            "event_kind": event.event_kind,
            "observed_at_epoch_seconds": event.observation.epoch_seconds,
            "observed_sequence": event.observation.sequence,
            "previous_entry_sha256": event.previous_entry_sha256,
            "promotion_id": str(request.promotion_id),
            "reason_code": event.reason_code,
            "sequence": event.sequence,
            "signature_purpose": event.evidence.signature_purpose,
            "trust_key_id": event.evidence.trust_key_id,
        }
        return hashlib.sha256(
            _EVENT_HASH_CONTEXT + canonical_json_bytes(payload)
        ).hexdigest()

    @staticmethod
    def _evidence_record_sha256(
        evidence: AuthenticatedVeterinaryExpertCorpus,
    ) -> str:
        return hashlib.sha256(
            _EVIDENCE_RECORD_HASH_CONTEXT
            + canonical_json_bytes(evidence.model_dump(mode="json"))
        ).hexdigest()

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        event: _CurrentStateEvent,
    ) -> None:
        request = event.evidence.request
        connection.execute(
            """
            INSERT INTO expert_corpus_current_state_events (
                sequence, event_kind, observed_at_epoch_seconds,
                observed_sequence, reason_code, promotion_id, corpus_id,
                corpus_version,
                cryptographic_domain, signature_purpose, trust_key_id,
                authority_id, evidence_payload_sha256, evidence_record_sha256,
                evidence_json,
                previous_entry_sha256, entry_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.sequence,
                event.event_kind,
                event.observation.epoch_seconds,
                event.observation.sequence,
                event.reason_code,
                str(request.promotion_id),
                str(request.corpus_manifest.corpus_id),
                str(request.corpus_manifest.corpus_version),
                event.evidence.cryptographic_domain,
                event.evidence.signature_purpose,
                event.evidence.trust_key_id,
                str(event.evidence.authority_id),
                event.evidence_payload_sha256,
                event.evidence_record_sha256,
                event.evidence.model_dump_json(),
                event.previous_entry_sha256,
                event.entry_sha256,
            ),
        )

    def _require_identity_unused(
        self,
        events: Sequence[_CurrentStateEvent],
        evidence: AuthenticatedVeterinaryExpertCorpus,
    ) -> None:
        if self._shares_identity(events, evidence):
            raise self._conflict(
                "expert corpus promotion or corpus-version identity is already bound"
            )

    @classmethod
    def _contains_exact(
        cls,
        events: Sequence[_CurrentStateEvent],
        evidence: AuthenticatedVeterinaryExpertCorpus,
    ) -> bool:
        identity = canonical_expert_corpus_evidence_sha256(evidence)
        return any(
            hmac.compare_digest(event.evidence_payload_sha256, identity)
            and event.evidence == evidence
            and event.event_kind == _PROMOTED
            for event in events
        )

    @classmethod
    def _shares_identity(
        cls,
        events: Sequence[_CurrentStateEvent],
        evidence: AuthenticatedVeterinaryExpertCorpus,
    ) -> bool:
        promotion_key = cls._promotion_key(evidence)
        corpus_key = cls._corpus_key(evidence)
        return any(
            event.event_kind == _PROMOTED
            and (
                cls._promotion_key(event.evidence) == promotion_key
                or cls._corpus_key(event.evidence) == corpus_key
            )
            for event in events
        )

    @staticmethod
    def _promotion_key(
        evidence: AuthenticatedVeterinaryExpertCorpus,
    ) -> tuple[str, ...]:
        return (
            evidence.cryptographic_domain,
            evidence.signature_purpose,
            evidence.trust_key_id,
            str(evidence.authority_id),
            str(evidence.request.promotion_id),
        )

    @staticmethod
    def _corpus_key(
        evidence: AuthenticatedVeterinaryExpertCorpus,
    ) -> tuple[str, ...]:
        return (
            str(evidence.request.corpus_manifest.corpus_id),
            str(evidence.request.corpus_manifest.corpus_version),
        )

    @staticmethod
    def _validated_event_kind(value: str) -> _EventKind:
        if value == _PROMOTED:
            return _PROMOTED
        if value == _CHECKED:
            return _CHECKED
        if value == _STALE:
            return _STALE
        if value == _REVOKED:
            return _REVOKED
        raise SqliteExpertCorpusCurrentState._unavailable(
            "expert corpus current-state event kind is invalid"
        )

    def _validate_anchors(self, events: Sequence[_CurrentStateEvent]) -> None:
        with self._anchor_directory_descriptor() as descriptor:
            observed = set(os.listdir(descriptor))
            expected = {self._anchor_filename(event.sequence) for event in events}
            if observed != expected or any(
                _ANCHOR_FILENAME_PATTERN.fullmatch(name) is None for name in observed
            ):
                raise self._unavailable(
                    "expert corpus current-state anchors do not match the database"
                )
            for event in events:
                if not hmac.compare_digest(
                    self._read_anchor(descriptor, event.sequence),
                    self._anchor_bytes(event),
                ):
                    raise self._unavailable(
                        "expert corpus current-state anchor was substituted"
                    )

    def _append_anchor(self, event: _CurrentStateEvent) -> None:
        with self._anchor_directory_descriptor() as directory_descriptor:
            descriptor = os.open(
                self._anchor_filename(event.sequence),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o400,
                dir_fd=directory_descriptor,
            )
            try:
                payload = self._anchor_bytes(event)
                offset = 0
                while offset < len(payload):
                    written = os.write(descriptor, payload[offset:])
                    if written <= 0:
                        raise OSError("expert corpus anchor write made no progress")
                    offset += written
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
                raise self._unavailable(
                    "expert corpus current-state anchor metadata is unsafe"
                )
            return os.read(descriptor, 513)
        finally:
            os.close(descriptor)

    @staticmethod
    def _anchor_filename(sequence: int) -> str:
        return f"{sequence:020d}.anchor"

    @staticmethod
    def _anchor_bytes(event: _CurrentStateEvent) -> bytes:
        return canonical_json_bytes(
            {
                "entry_sha256": event.entry_sha256,
                "previous_entry_sha256": event.previous_entry_sha256,
                "sequence": event.sequence,
            }
        )

    @contextmanager
    def _exclusive_lock(
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
                "expert corpus current-state lock is unavailable"
            ) from error
        try:
            self._require_lock_identity(descriptor, validate_stored_identity)
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as error:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise self._unavailable(
                            "expert corpus current-state lock timed out"
                        ) from error
                    time.sleep(min(0.01, remaining))
            self._require_root_identity()
            self._require_lock_identity(descriptor, validate_stored_identity)
            yield
        except OSError as error:
            raise self._unavailable(
                "expert corpus current-state lock is unavailable"
            ) from error
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _require_lock_identity(
        self,
        descriptor: int,
        validate_stored_identity: bool,
    ) -> None:
        metadata = os.fstat(descriptor)
        observed = (metadata.st_dev, metadata.st_ino)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or observed != self._path_identity(self._lock_path)
            or (validate_stored_identity and observed != self._lock_identity)
        ):
            raise self._unavailable("expert corpus current-state lock identity changed")

    @contextmanager
    def _anchor_directory_descriptor(self) -> Iterator[int]:
        descriptor = os.open(
            self._anchor_directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or (metadata.st_dev, metadata.st_ino) != self._anchor_directory_identity
            ):
                raise self._unavailable(
                    "expert corpus current-state anchor directory changed"
                )
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
                "expert corpus current-state database identity changed"
            )
        try:
            connection = sqlite3.connect(
                f"{self._database_path.as_uri()}?mode=rw",
                uri=True,
                isolation_level=None,
                timeout=self._busy_timeout_milliseconds / 1_000,
            )
        except sqlite3.Error as error:
            raise self._unavailable(
                "expert corpus current-state database is unavailable"
            ) from error
        try:
            if self._path_identity(self._database_path) != before:
                raise self._unavailable(
                    "expert corpus current-state database changed while opening"
                )
            connection.execute(
                f"PRAGMA busy_timeout = {self._busy_timeout_milliseconds}"
            )
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("PRAGMA synchronous = FULL")
            journal = connection.execute("PRAGMA journal_mode").fetchone()
            permitted = {"delete", "wal"} if allow_delete_journal_mode else {"wal"}
            if journal is None or str(journal[0]).lower() not in permitted:
                raise self._unavailable(
                    "expert corpus current-state journal mode is invalid"
                )
        except ExpertCorpusPromotionError:
            connection.close()
            raise
        except (OSError, sqlite3.Error) as error:
            connection.close()
            raise self._unavailable(
                "expert corpus current-state connection validation failed"
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
                "expert corpus current-state WAL checkpoint did not complete"
            )

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

    def _create_database_file(self) -> None:
        descriptor = os.open(
            self._database_path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        os.close(descriptor)

    def _fsync_database(self) -> None:
        descriptor = os.open(
            self._database_path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

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
        if self._path_identity(self._root, directory=True) != self._root_identity:
            raise self._unavailable("expert corpus current-state root identity changed")

    @staticmethod
    def _validated_evidence(
        evidence: AuthenticatedVeterinaryExpertCorpus,
        evidence_payload_sha256: str,
    ) -> AuthenticatedVeterinaryExpertCorpus:
        try:
            validated = AuthenticatedVeterinaryExpertCorpus.model_validate(
                evidence.model_dump(mode="python", warnings=False)
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise ExpertCorpusPromotionError(
                "expert corpus current-state evidence is invalid",
                kind=ExpertCorpusFailureKind.INVALID,
                reason_code="invalid_persisted_corpus_evidence",
            ) from error
        expected = canonical_expert_corpus_evidence_sha256(validated)
        if not isinstance(evidence_payload_sha256, str) or not hmac.compare_digest(
            expected,
            evidence_payload_sha256,
        ):
            raise ExpertCorpusPromotionError(
                "expert corpus current-state identity is substituted",
                kind=ExpertCorpusFailureKind.SUBSTITUTED,
                reason_code="substituted_persisted_corpus_identity",
            )
        return validated

    @staticmethod
    def _validated_root(registry_root: Path) -> Path:
        if not isinstance(registry_root, Path):
            raise TypeError("registry_root must be a pathlib.Path")
        if not registry_root.is_absolute():
            raise ValueError("registry_root must be absolute")
        try:
            resolved = registry_root.resolve(strict=True)
        except OSError as error:
            raise SqliteExpertCorpusCurrentState._unavailable(
                "expert corpus current-state root is unavailable"
            ) from error
        if resolved != registry_root:
            raise ValueError("registry_root must not contain symbolic links")
        return registry_root

    @staticmethod
    def _validated_timeout(value: int, label: str) -> int:
        if type(value) is not int or not 1 <= value <= 60_000:
            raise ValueError(f"{label} must be an integer from 1 through 60000")
        return value

    @staticmethod
    def _path_identity(path: Path, *, directory: bool = False) -> tuple[int, int]:
        try:
            metadata = path.lstat()
        except OSError as error:
            raise SqliteExpertCorpusCurrentState._unavailable(
                "expert corpus current-state path is unavailable"
            ) from error
        expected_type = (
            stat.S_ISDIR(metadata.st_mode)
            if directory
            else stat.S_ISREG(metadata.st_mode)
        )
        valid_links = metadata.st_nlink >= 1 if directory else metadata.st_nlink == 1
        if not expected_type or not valid_links:
            raise SqliteExpertCorpusCurrentState._unavailable(
                "expert corpus current-state path identity is unsafe"
            )
        return metadata.st_dev, metadata.st_ino

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        if connection.in_transaction:
            connection.rollback()

    @staticmethod
    def _conflict(message: str) -> ExpertCorpusPromotionError:
        return ExpertCorpusPromotionError(
            message,
            kind=ExpertCorpusFailureKind.CONFLICTING,
            reason_code="conflicting_persisted_corpus_promotion",
        )

    @staticmethod
    def _stale() -> ExpertCorpusPromotionError:
        return ExpertCorpusPromotionError(
            "expert corpus promotion is stale",
            kind=ExpertCorpusFailureKind.STALE,
            reason_code="stale_persisted_corpus_promotion",
        )

    @staticmethod
    def _unavailable(message: str) -> ExpertCorpusPromotionError:
        return ExpertCorpusPromotionError(
            message,
            kind=ExpertCorpusFailureKind.UNAVAILABLE,
            reason_code="expert_corpus_current_state_unavailable",
        )

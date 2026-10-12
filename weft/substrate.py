"""Weft substrate: record store, journal, and projection writer.

Authoritative store is SQLite. Projection files are derived, never authoritative.

See docs/adr/0001-phase1-decisions.md for the design rationale.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1


class SubstrateError(Exception):
    """Base error for substrate failures.

    Note: ScopeViolation and SchemaViolation are SIBLINGS, not parent/child, so
    that `except ScopeViolation` can never accidentally catch a schema error.
    A test asserting ScopeViolation must not pass on SchemaViolation.
    """


class ScopeViolation(SubstrateError):
    """Raised when a write is not permitted by the scope engine.

    This is a HARD failure: the write is rejected and nothing is persisted.
    """


class SchemaViolation(SubstrateError):
    """Raised when a record does not match its declared schema.

    Distinct from ScopeViolation: scope denial is the authoritative security
    boundary, schema validation is advisory quality control.
    """


@dataclass
class Record:
    """A single typed record in the substrate."""

    record_id: str
    record_type: str
    scope: str
    payload: dict[str, Any]
    epistemic_status: str = "stated"
    created_at: float = field(default_factory=time.time)
    effective_at: float | None = None
    supersedes: str | None = None
    source_ids: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> tuple:
        return (
            self.record_id,
            self.record_type,
            self.scope,
            self.epistemic_status,
            json.dumps(self.payload, sort_keys=True),
            self.created_at,
            self.effective_at,
            self.supersedes,
            json.dumps(self.source_ids, sort_keys=True),
            json.dumps(self.provenance, sort_keys=True),
        )

    @classmethod
    def from_row(cls, row: tuple) -> "Record":
        return cls(
            record_id=row[0],
            record_type=row[1],
            scope=row[2],
            epistemic_status=row[3],
            payload=json.loads(row[4]),
            created_at=row[5],
            effective_at=row[6],
            supersedes=row[7],
            source_ids=json.loads(row[8]),
            provenance=json.loads(row[9]),
        )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    record_id        TEXT PRIMARY KEY,
    record_type      TEXT NOT NULL,
    scope            TEXT NOT NULL,
    epistemic_status TEXT NOT NULL,
    payload          TEXT NOT NULL,
    created_at       REAL NOT NULL,
    effective_at     REAL,
    supersedes       TEXT,
    source_ids       TEXT NOT NULL DEFAULT '[]',
    provenance       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_records_scope ON records(scope);
CREATE INDEX IF NOT EXISTS idx_records_type  ON records(record_type);

CREATE TABLE IF NOT EXISTS journal (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    op              TEXT NOT NULL,
    record_id       TEXT,
    scope           TEXT,
    detail          TEXT NOT NULL,
    recorded_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_journal_record ON journal(record_id);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Substrate:
    """The authoritative record store.

    One SQLite transaction covers the record write, the journal entry, and any
    index update. Projection files are written AFTER commit and are rebuildable.
    """

    def __init__(self, db_path: str | os.PathLike, scope_engine: "ScopeEngine | None" = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.scope_engine = scope_engine
        self._conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )

    # ---- writes -------------------------------------------------------

    def put(self, record: Record, *, validate_schema: bool = True) -> None:
        """Write a record. Rejected outright if the scope engine denies it.

        The scope check runs BEFORE any validation or persistence. A denied
        write raises ScopeViolation and leaves the store untouched.
        """
        if self.scope_engine is not None:
            decision = self.scope_engine.authorize_write(record)
            if not decision.allowed:
                self._journal("write_denied", record.record_id, record.scope, decision.reason)
                raise ScopeViolation(decision.reason)

        if validate_schema:
            self._validate(record)

        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(
                """INSERT OR REPLACE INTO records
                   (record_id, record_type, scope, epistemic_status, payload,
                    created_at, effective_at, supersedes, source_ids, provenance)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                record.to_row(),
            )
            self._journal_locked(
                "put", record.record_id, record.scope,
                f"type={record.record_type} status={record.epistemic_status}",
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def delete(self, record_id: str, *, reason: str = "") -> bool:
        """Hard delete: remove the record and journal the removal.

        Deletion OVERRIDES archival preservation. Only a non-sensitive tombstone
        may be retained by the caller's policy; the substrate removes the record.
        """
        cur = self._conn.execute("SELECT record_id, scope FROM records WHERE record_id=?", (record_id,))
        row = cur.fetchone()
        if row is None:
            return False
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute("DELETE FROM records WHERE record_id=?", (record_id,))
            self._journal_locked("delete", record_id, row[1], f"reason={reason}")
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return True

    def correct(self, original_id: str, replacement: Record, *, validate_schema: bool = True) -> None:
        """Supersede a record, retaining lineage.

        The replacement carries `supersedes=original_id`. The original is NOT
        erased -- correction preserves history. Use delete() for privacy removal.
        """
        replacement.supersedes = original_id
        self.put(replacement, validate_schema=validate_schema)

    # ---- reads --------------------------------------------------------

    def get(self, record_id: str) -> Record | None:
        cur = self._conn.execute("SELECT * FROM records WHERE record_id=?", (record_id,))
        row = cur.fetchone()
        return Record.from_row(row) if row else None

    def all_records(self) -> list[Record]:
        cur = self._conn.execute("SELECT * FROM records ORDER BY created_at")
        return [Record.from_row(r) for r in cur.fetchall()]

    def journal_entries(self) -> list[dict]:
        cur = self._conn.execute(
            "SELECT seq, op, record_id, scope, detail, recorded_at FROM journal ORDER BY seq"
        )
        return [
            {"seq": r[0], "op": r[1], "record_id": r[2], "scope": r[3], "detail": r[4], "recorded_at": r[5]}
            for r in cur.fetchall()
        ]

    # ---- projections --------------------------------------------------

    def write_projection(self, out_dir: str | os.PathLike) -> Path:
        """Write human-readable projection files. Derived, never authoritative.

        Idempotent: rewrites the same content for the same record set. A stale
        projection is detectable by comparing against the store.
        """
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        for rec in self.all_records():
            path = out / f"{rec.record_id}.json"
            path.write_text(json.dumps(rec.payload, indent=2, sort_keys=True))
        return out

    # ---- internals ----------------------------------------------------

    def _journal(self, op: str, record_id: str | None, scope: str | None, detail: str) -> None:
        self._conn.execute(
            "INSERT INTO journal(op, record_id, scope, detail, recorded_at) VALUES (?,?,?,?,?)",
            (op, record_id, scope, detail, time.time()),
        )

    def _journal_locked(self, op: str, record_id: str | None, scope: str | None, detail: str) -> None:
        self._journal(op, record_id, scope, detail)

    def _validate(self, record: Record) -> None:
        """Advisory schema validation.

        Scope enforcement is authoritative and happens earlier. This catches
        malformed records but is NOT the security boundary.
        """
        if not record.record_id:
            raise SchemaViolation("record_id is required")
        if not record.record_type:
            raise SchemaViolation("record_type is required")
        if not record.scope:
            raise SchemaViolation("scope is required")
        if record.epistemic_status not in ("stated", "derived", "verified"):
            raise SchemaViolation(f"invalid epistemic_status: {record.epistemic_status!r}")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Substrate":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

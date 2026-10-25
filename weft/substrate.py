"""Weft substrate: record store, journal, and projection writer.

Authoritative store is SQLite. Projection files are derived, never authoritative.
The journal is an audit trail of committed operations and denials. It carries
no record payloads, so deletion cannot leave content behind in it.

See docs/adr/0001-phase1-decisions.md for the design rationale.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from weft.scope import ScopeContext, ScopeEngine

SCHEMA_VERSION = 1

# Record ids are used as file names by projections. Restrict them so they can
# never contain separators, dots, or a leading dash (review fix F9).
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class SubstrateError(Exception):
    """Base error for substrate failures."""


class ScopeViolation(SubstrateError):
    """A write, delete, or correction was refused by the scope engine. Nothing persisted."""

    def __init__(self, reason: str, *, audit_recorded: bool = True):
        super().__init__(reason)
        self.audit_recorded = audit_recorded


class SchemaViolation(SubstrateError):
    """A record does not match its schema, or its id is unsafe."""


class RecordConflict(SubstrateError):
    """A record with this id already exists and overwrite was not requested."""


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


def _check_id(record_id: Any) -> None:
    if not isinstance(record_id, str) or not _ID_RE.match(record_id):
        raise SchemaViolation(f"invalid record_id: {record_id!r}")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_body(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


_MANIFEST = "_manifest.json"


class Substrate:
    """The authoritative record store.

    Every write is one SQLite transaction covering the record, its journal
    entry, and the store version. Projection files are written after commit
    and are rebuildable. A lock serialises use within one process.
    """

    def __init__(
        self,
        db_path: str | os.PathLike,
        scope_engine: ScopeEngine | None = None,
        *,
        busy_timeout: float = 5.0,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Fail closed: never operate without a gate.
        self.scope_engine = scope_engine if scope_engine is not None else ScopeEngine()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path),
            isolation_level=None,
            timeout=busy_timeout,
            check_same_thread=False,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        # Explicit, not the library default (ADR 0001 durability claim).
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('store_version', '0')")

    # ---- transaction helpers ------------------------------------------

    def _rollback_if_open(self) -> None:
        # Review fix F14: only roll back a transaction that actually exists.
        if self._conn.in_transaction:
            self._conn.execute("ROLLBACK")

    def _journal(self, op: str, record_id: str | None, scope: str | None, detail: str) -> None:
        """Append a journal entry. Must run inside an open transaction."""
        self._conn.execute(
            "INSERT INTO journal(op, record_id, scope, detail, recorded_at) VALUES (?,?,?,?,?)",
            (op, record_id, scope, detail, time.time()),
        )

    def _journal_standalone(self, op: str, record_id: str | None, scope: str | None, detail: str) -> bool:
        """Journal a denial in its own transaction. Returns False if it could not be recorded.

        Review fix F13: a lock error here must not replace the denial. The caller
        still refuses the operation and reports whether the audit entry landed.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._journal(op, record_id, scope, detail)
            self._conn.execute("COMMIT")
            return True
        except sqlite3.OperationalError:
            self._rollback_if_open()
            return False

    def _store_version(self) -> int:
        row = self._conn.execute("SELECT value FROM meta WHERE key='store_version'").fetchone()
        return int(row[0]) if row else 0

    def _bump_version(self) -> None:
        self._conn.execute(
            "UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) WHERE key='store_version'"
        )

    # ---- writes -------------------------------------------------------

    def put(
        self,
        record: Record,
        ctx: ScopeContext | None,
        *,
        validate_schema: bool = True,
        overwrite: bool = False,
    ) -> None:
        """Write a record. The caller needs a granted write scope for the record's scope.

        Refused outright (ScopeViolation) if the scope engine denies it. An existing
        record with the same id raises RecordConflict unless overwrite is requested,
        and an overwrite also needs a granted scope for the incumbent record.
        """
        with self._lock:
            self._write(record, ctx, validate_schema=validate_schema, overwrite=overwrite)

    def _write(
        self,
        record: Record,
        ctx: ScopeContext | None,
        *,
        validate_schema: bool,
        overwrite: bool,
        original_id: str | None = None,
    ) -> None:
        decision = self.scope_engine.authorize_write(record, ctx)
        if not decision.allowed:
            audited = self._journal_standalone("write_denied", record.record_id, record.scope, decision.reason)
            raise ScopeViolation(decision.reason, audit_recorded=audited)

        _check_id(record.record_id)
        if validate_schema:
            self._validate(record)

        try:
            self._conn.execute("BEGIN IMMEDIATE")

            if original_id is not None:
                orig_row = self._conn.execute(
                    "SELECT * FROM records WHERE record_id=?", (original_id,)
                ).fetchone()
                if orig_row is None:
                    raise SubstrateError(f"cannot correct unknown record {original_id!r}")
                orig = Record.from_row(orig_row)
                read = self.scope_engine.authorize_read(
                    orig.scope, ctx, self.scope_engine._payload_text(orig)
                )
                if not read.allowed:
                    # Review fix F7: a correction must not supersede what the caller cannot read.
                    self._journal("correct_denied", original_id, orig.scope, read.reason)
                    self._conn.execute("COMMIT")
                    raise ScopeViolation(read.reason)
                record.supersedes = original_id

            existing = self._conn.execute(
                "SELECT scope FROM records WHERE record_id=?", (record.record_id,)
            ).fetchone()
            if existing is not None:
                if not overwrite:
                    raise RecordConflict(f"record {record.record_id!r} already exists")
                if ctx is None or existing[0] not in ctx.granted_scopes:
                    # Review fix F2: overwrite needs write authority over the incumbent.
                    reason = f"overwrite of record in scope {existing[0]!r} not granted"
                    self._journal("write_denied", record.record_id, existing[0], reason)
                    self._conn.execute("COMMIT")
                    raise ScopeViolation(reason)

            self._conn.execute(
                """INSERT OR REPLACE INTO records
                   (record_id, record_type, scope, epistemic_status, payload,
                    created_at, effective_at, supersedes, source_ids, provenance)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                record.to_row(),
            )
            detail = f"type={record.record_type} status={record.epistemic_status}"
            if existing is not None:
                detail += " overwrite"
            self._journal("put", record.record_id, record.scope, detail)
            self._bump_version()
            self._conn.execute("COMMIT")
        except Exception:
            self._rollback_if_open()
            raise

    def delete(self, record_id: str, ctx: ScopeContext | None = None, *, reason: str = "") -> bool:
        """Hard delete: remove the record and journal the removal.

        Deletion is a write. Authorization is decided INSIDE the write transaction
        (review fix F8). If any record that would lose its supersedes link is in a
        scope the caller cannot write, the whole delete is refused (review fix F15).
        """
        with self._lock:
            if ctx is None:
                self._journal_standalone("delete_denied", record_id, None, "no scope context")
                return False
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT record_id, scope FROM records WHERE record_id=?", (record_id,)
                ).fetchone()
                if row is None:
                    self._conn.execute("COMMIT")
                    return False

                decision = self.scope_engine.authorize_delete(row[1], ctx)
                if not decision.allowed:
                    self._journal("delete_denied", record_id, row[1], decision.reason)
                    self._conn.execute("COMMIT")
                    return False

                dependents = self._conn.execute(
                    "SELECT record_id, scope FROM records WHERE supersedes = ?", (record_id,)
                ).fetchall()
                for dep_id, dep_scope in dependents:
                    if not self.scope_engine.authorize_delete(dep_scope, ctx).allowed:
                        self._journal(
                            "delete_denied", record_id, row[1],
                            f"dependent {dep_id} in ungranted scope {dep_scope!r}",
                        )
                        self._conn.execute("COMMIT")
                        return False

                for dep_id, dep_scope in dependents:
                    self._conn.execute("UPDATE records SET supersedes = NULL WHERE record_id = ?", (dep_id,))
                    self._journal(
                        "unlink_supersedes", dep_id, dep_scope,
                        f"cleared dangling supersedes -> {record_id} (deleted)",
                    )

                self._conn.execute("DELETE FROM records WHERE record_id=?", (record_id,))
                self._journal("delete", record_id, row[1], f"reason={reason}")
                self._bump_version()
                self._conn.execute("COMMIT")
                return True
            except Exception:
                self._rollback_if_open()
                raise

    def correct(
        self,
        original_id: str,
        replacement: Record,
        ctx: ScopeContext | None,
        *,
        validate_schema: bool = True,
    ) -> None:
        """Supersede a record, retaining lineage.

        The caller must be able to read the original (review fix F7) and hold a
        granted write scope for the replacement. The original is not erased.
        """
        with self._lock:
            self._write(
                replacement, ctx, validate_schema=validate_schema, overwrite=False, original_id=original_id
            )

    # ---- reads --------------------------------------------------------

    def get(self, record_id: str, ctx: ScopeContext | None = None) -> Record | None:
        """Fetch one record, gated by scope. No context -> None."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM records WHERE record_id=?", (record_id,)).fetchone()
            if row is None or ctx is None:
                return None
            rec = Record.from_row(row)
            decision = self.scope_engine.authorize_read(rec.scope, ctx, self.scope_engine._payload_text(rec))
            if not decision.allowed:
                self._journal_standalone("read_denied", record_id, rec.scope, decision.reason)
                return None
            return rec

    def query(
        self,
        ctx: ScopeContext | None,
        *,
        record_type: str | None = None,
        scope: str | None = None,
    ) -> tuple[list[Record], list[dict]]:
        """The scoped read path. Returns (readable, exclusions). No context -> nothing."""
        if ctx is None:
            return [], []
        with self._lock:
            sql = "SELECT * FROM records"
            clauses, params = [], []
            if record_type is not None:
                clauses.append("record_type = ?")
                params.append(record_type)
            if scope is not None:
                clauses.append("scope = ?")
                params.append(scope)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY created_at"
            records = [Record.from_row(r) for r in self._conn.execute(sql, params).fetchall()]
            return self.scope_engine.filter_readable(records, ctx)

    def all_records(self, ctx: ScopeContext | None = None) -> list[Record]:
        """Every record the caller may read. No context -> nothing."""
        readable, _ = self.query(ctx)
        return readable

    def journal_entries(self, ctx: ScopeContext | None = None) -> list[dict]:
        """Journal entries the caller may see (review fix F12). No context -> nothing.

        An entry is visible only if its scope is readable by the caller. Entries
        without a scope are never visible.
        """
        if ctx is None:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, op, record_id, scope, detail, recorded_at FROM journal ORDER BY seq"
            ).fetchall()
        out = []
        for seq, op, rid, scope, detail, ts in rows:
            if scope is None:
                continue
            if not self.scope_engine.authorize_read(scope, ctx, "").allowed:
                continue
            out.append({"seq": seq, "op": op, "record_id": rid, "scope": scope,
                        "detail": detail, "recorded_at": ts})
        return out

    # ---- projections --------------------------------------------------

    def _expected_files(self, ctx: ScopeContext | None) -> dict[str, tuple[str, str]]:
        """name -> (body, sha256) for every record the caller may read."""
        expected = {}
        for rec in self.all_records(ctx):
            body = _file_body(rec.payload)
            expected[f"{rec.record_id}.json"] = (body, _sha256(body))
        return expected

    @staticmethod
    def _read_manifest(out: Path) -> dict | None:
        path = out / _MANIFEST
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def write_projection(self, out_dir: str | os.PathLike, ctx: ScopeContext | None = None) -> Path:
        """Write projection files for exactly the records the caller may read.

        Derived, never authoritative. Files and a manifest are written atomically.
        Files this projection wrote earlier but that are no longer in scope are
        removed (review fix F10). Files not named in the previous manifest are
        never touched.
        """
        with self._lock:
            out = Path(out_dir)
            out.mkdir(parents=True, exist_ok=True)
            previous = self._read_manifest(out) or {}
            prev_names = set((previous.get("files") or {}).keys())

            expected = self._expected_files(ctx)
            for name, (body, _digest) in expected.items():
                _atomic_write(out / name, body)
            for name in prev_names - set(expected):
                if _safe_projection_name(name):
                    (out / name).unlink(missing_ok=True)

            manifest = {
                "store_version": self._store_version(),
                "files": {name: digest for name, (_body, digest) in expected.items()},
            }
            _atomic_write(out / _MANIFEST, json.dumps(manifest, indent=2, sort_keys=True))
            return out

    def projection_status(self, out_dir: str | os.PathLike, ctx: ScopeContext | None = None) -> dict:
        """Compare a projection directory with the store.

        state is one of:
          missing   no manifest
          tampered  a file's content differs from what the store would project
          stale     the store changed after the projection, or a file is out of scope
          current   matches the store for this caller
        """
        with self._lock:
            out = Path(out_dir)
            manifest = self._read_manifest(out)
            if manifest is None:
                return {"state": "missing", "missing": [], "modified": [], "unexpected": []}

            expected = self._expected_files(ctx)
            missing, modified, unexpected = [], [], []
            for name, (_body, digest) in expected.items():
                path = out / name
                if not path.exists():
                    missing.append(name)
                elif _sha256(path.read_text()) != digest:
                    modified.append(name)
            for name in (manifest.get("files") or {}):
                if name not in expected and _safe_projection_name(name):
                    unexpected.append(name)

            stale_version = manifest.get("store_version") != self._store_version()
            if modified:
                state = "tampered"
            elif missing or unexpected or stale_version:
                state = "stale"
            else:
                state = "current"
            return {"state": state, "missing": missing, "modified": modified, "unexpected": unexpected}

    # ---- internals ----------------------------------------------------

    def _validate(self, record: Record) -> None:
        """Schema validation. Scope and id safety are enforced separately and always."""
        if not record.record_type:
            raise SchemaViolation("record_type is required")
        if not record.scope:
            raise SchemaViolation("scope is required")
        if record.epistemic_status not in ("stated", "derived", "verified"):
            raise SchemaViolation(f"invalid epistemic_status: {record.epistemic_status!r}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Substrate":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _safe_projection_name(name: str) -> bool:
    return name.endswith(".json") and _ID_RE.match(name[: -len(".json")]) is not None


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

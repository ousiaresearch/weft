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
        # The store holds every scope in plaintext. Create the database and its
        # sidecar files owner-only from the start (review N8, F-E, F21). Encryption
        # at rest is a later phase.
        old_umask = os.umask(0o077)
        try:
            fd = os.open(str(self.db_path), os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
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
        finally:
            os.umask(old_umask)
        for suffix in ("", "-wal", "-shm"):
            path = str(self.db_path) + suffix
            if os.path.exists(path):
                os.chmod(path, 0o600)

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
            # G10: a caller without the scope must not write into its journal.
            audited = self._journal_standalone("write_denied", None, None, "write not permitted")
            # Review G4/G5: the caller learns only that the write was refused.
            # The reason goes to the journal, where only holders of the scope read it.
            raise ScopeViolation("write not permitted", audit_recorded=audited)

        if original_id is None and record.supersedes is not None:
            # G13: a supersedes link into a scope the caller did not authorize
            # would make another scope's record undeletable. Only correct() links.
            audited = self._journal_standalone("write_denied", None, None, "write not permitted")
            raise ScopeViolation("write not permitted", audit_recorded=audited)

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
                    # G9: same exception as an unreadable original.
                    raise ScopeViolation("correction not permitted")
                orig = Record.from_row(orig_row)
                read = self.scope_engine.authorize_read(
                    orig.scope, ctx, self.scope_engine._payload_text(orig)
                )
                if not read.allowed:
                    # Review fix F7: a correction must not supersede what the caller cannot read.
                    self._journal("correct_denied", None, None, "correction not permitted")
                    self._conn.execute("COMMIT")
                    raise ScopeViolation("correction not permitted")
                if record.scope != orig.scope:
                    # Review N10: a correction stays in its original's scope.
                    reason = "correction must stay within the original record's scope"  # noqa: F841
                    self._journal("correct_denied", None, None, "correction not permitted")
                    self._conn.execute("COMMIT")
                    raise ScopeViolation("correction not permitted")
                record.supersedes = original_id

            existing = self._conn.execute(
                "SELECT scope FROM records WHERE record_id=?", (record.record_id,)
            ).fetchone()
            if existing is not None:
                # Review G5: check authority over the incumbent BEFORE reporting a
                # conflict, so a caller without the scope cannot learn the id exists.
                if ctx is None or existing[0] not in ctx.granted_scopes:
                    # Review fix F2 / G4: overwrite needs the incumbent's scope. The
                    # caller gets the same generic refusal as any denied write.
                    reason = f"write to existing record in scope {existing[0]!r} not granted"
                    self._journal("write_denied", None, None, "write not permitted")
                    self._conn.execute("COMMIT")
                    raise ScopeViolation("write not permitted")
                if not overwrite:
                    raise RecordConflict(f"record {record.record_id!r} already exists")

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
                    self._journal("delete_denied", None, None, "delete not permitted")
                    self._conn.execute("COMMIT")
                    return False

                dependents = self._conn.execute(
                    "SELECT record_id, scope FROM records WHERE supersedes = ?", (record_id,)
                ).fetchall()
                for dep_id, dep_scope in dependents:
                    if not self.scope_engine.authorize_delete(dep_scope, ctx).allowed:
                        # Review fix F-B: name neither the dependent nor its scope in the
                        # audit detail, so the caller learns nothing about it.
                        self._journal(
                            "delete_denied", record_id, row[1],
                            "delete refused: a dependent record is outside your granted scopes",
                        )
                        self._conn.execute("COMMIT")
                        return False

                for dep_id, dep_scope in dependents:
                    self._conn.execute("UPDATE records SET supersedes = NULL WHERE record_id = ?", (dep_id,))
                    # Review N7: the detail names no deleted id.
                    self._journal(
                        "unlink_supersedes", dep_id, dep_scope,
                        "cleared dangling supersedes link (target deleted)",
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
                self._journal_standalone("read_denied", None, None, "read not permitted")
                return None
            return rec

    def query(
        self,
        ctx: ScopeContext | None,
        *,
        record_type: str | None = None,
        scope: str | None = None,
    ) -> tuple[list[Record], list]:
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
            return self.scope_engine.filter_readable(records, ctx), []  # no exclusions (G6, G7)

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
            # G12: renumber within the caller's view so global gaps are invisible.
            out.append({"seq": len(out) + 1, "op": op, "record_id": rid, "scope": scope,
                        "detail": detail, "recorded_at": ts})
        return out

    # ---- projections --------------------------------------------------

    @staticmethod
    def _view_token(ctx: ScopeContext) -> str:
        """Identify a caller's view by its granted scopes. One projection
        directory belongs to exactly one view (review G2)."""
        return _sha256(json.dumps(
            [sorted(ctx.granted_scopes), ctx.user_authorized_cross_scope,
             sorted(ctx.cross_scope_targets), ctx.authorization_source],
            sort_keys=True,
        ))

    def _expected_files(self, ctx: ScopeContext | None) -> dict[str, tuple[str, str]]:
        """name -> (body, sha256) for every record the caller may read.

        Records whose id is unsafe as a file name are skipped (review G3).
        """
        expected = {}
        for rec in self.all_records(ctx):
            if not _ID_RE.match(rec.record_id):
                continue
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
        """Write the projection for exactly the caller's view.

        Derived, never authoritative. Rules:
          - No context: nothing is touched, not even the directory.
          - A directory belongs to one view. Writing with a different view, or
            over unmanaged files with projected names, is refused.
          - Only files named in this view's own previous manifest are removed.
        """
        with self._lock:
            out = Path(out_dir)
            if ctx is None:
                return out
            view = self._view_token(ctx)
            manifest = self._read_manifest(out)
            if manifest is not None and manifest.get("view") != view:
                raise ScopeViolation("projection directory belongs to another view")

            expected = self._expected_files(ctx)
            if manifest is None:
                for name in expected:
                    if (out / name).exists():
                        raise ScopeViolation("projection directory holds unmanaged files")

            out.mkdir(parents=True, exist_ok=True)
            os.chmod(out, 0o700)
            prev_names = {
                n for n in ((manifest or {}).get("files") or {}) if _safe_projection_name(n)
            }
            for name, (body, _digest) in expected.items():
                _atomic_write(out / name, body)
            for name in prev_names - set(expected):
                (out / name).unlink(missing_ok=True)

            new_manifest = {
                "view": view,
                "files": {name: digest for name, (_body, digest) in expected.items()},
            }
            _atomic_write(out / _MANIFEST, json.dumps(new_manifest, indent=2, sort_keys=True))
            return out

    def projection_status(self, out_dir: str | os.PathLike, ctx: ScopeContext | None = None) -> dict:
        """Compare a projection with the store, for the caller's view only.

        Reports nothing about records outside the caller's view (review G1).
        state is one of:
          missing   no projection for this view
          tampered  a file's bytes differ from what this projection wrote
          stale     the store changed in this view since the projection was written
          current   matches the store for this view
        """
        with self._lock:
            out = Path(out_dir)
            manifest = self._read_manifest(out)
            if ctx is None or manifest is None or manifest.get("view") != self._view_token(ctx):
                return {"state": "missing", "missing": [], "modified": []}

            files = manifest.get("files") or {}
            expected = self._expected_files(ctx)
            missing, modified, changed = [], [], []
            for name, (_body, expected_digest) in expected.items():
                path = out / name
                if not path.exists():
                    missing.append(name)
                    continue
                on_disk = _sha256(path.read_text())
                written = files.get(name)
                if written is not None and on_disk != written:
                    modified.append(name)
                elif written != expected_digest:
                    changed.append(name)

            if modified:
                state = "tampered"
            elif missing or changed or len(files) != len(expected):
                state = "stale"
            else:
                state = "current"
            return {"state": state, "missing": missing, "modified": modified}

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

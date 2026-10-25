"""Regression tests for defects found by the Codex adversarial review of a37d175.

Each test names the finding it guards (F#). Every test here must FAIL on the
pre-fix code and PASS after the fix. They define the new API: write operations
take a ScopeContext, and reads and journal access are gated.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from weft.scope import ScopeContext, ScopeEngine
from weft.substrate import Record, RecordConflict, ScopeViolation, SchemaViolation, Substrate

REPO = Path(__file__).resolve().parents[1]

KNOWN = {"acs-project", "memory-providers", "ariel-continuity"}
MARKERS = {"acs-project": {"ACS", "Listening Commonwealth", "Constitutional Convention Letters"}}

ALL = ScopeContext(granted_scopes=frozenset(KNOWN))
MEM_ONLY = ScopeContext(granted_scopes=frozenset({"memory-providers"}))
ACS_ONLY = ScopeContext(granted_scopes=frozenset({"acs-project"}))


@pytest.fixture
def engine():
    return ScopeEngine(known_scopes=KNOWN, markers=MARKERS)


@pytest.fixture
def sub(tmp_path, engine):
    s = Substrate(tmp_path / "weft.db", scope_engine=engine)
    yield s
    s.close()


def rec(rid, scope, text="plain fact"):
    return Record(record_id=rid, record_type="Observation", scope=scope, payload={"text": text})


# F1: write path had no caller authority check.
def test_F1_write_requires_granted_scope(sub):
    with pytest.raises(ScopeViolation):
        sub.put(rec("r1", "acs-project", "ACS release note"), MEM_ONLY)
    assert sub.get("r1", ALL) is None


def test_F1_put_without_context_is_refused(sub):
    with pytest.raises(ScopeViolation):
        sub.put(rec("r1", "memory-providers"), None)
    assert sub.get("r1", ALL) is None


# F2: INSERT OR REPLACE let a caller overwrite a record in another scope.
def test_F2_cannot_overwrite_record_in_ungranted_scope(sub):
    sub.put(rec("r1", "acs-project", "ACS secret"), ALL)
    with pytest.raises(ScopeViolation):
        sub.put(rec("r1", "memory-providers", "clobbered"), MEM_ONLY, overwrite=True)
    assert sub.get("r1", ALL).payload["text"] == "ACS secret"


def test_F2_same_id_without_overwrite_conflicts(sub):
    sub.put(rec("r1", "memory-providers", "first"), ALL)
    with pytest.raises(RecordConflict):
        sub.put(rec("r1", "memory-providers", "second"), ALL)
    assert sub.get("r1", ALL).payload["text"] == "first"


# F3: content guard was a tautology. ACS-referencing content under another scope
# must be refused on write and must not be returned to a reader of that scope.
def test_F3_acs_content_refused_under_memory_scope_on_write(sub):
    with pytest.raises(ScopeViolation):
        sub.put(rec("r1", "memory-providers", "Owner of the ACS Constitutional Convention Letters"), ALL)


def test_F3_acs_content_under_memory_scope_not_returned_to_memory_reader(sub, tmp_path):
    # Bypass the write gate to simulate a record that already exists in the store.
    sub._conn.execute(
        "INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("legacy", "Observation", "memory-providers", "stated",
         '{"text": "The Listening Commonwealth released RC-05."}', 0.0, None, None, "[]", "{}"),
    )
    assert sub.get("legacy", MEM_ONLY) is None
    readable, _ = sub.query(MEM_ONLY)
    assert all(r.record_id != "legacy" for r in readable)


# F4: an empty cross_scope_targets set widened to every scope.
def test_F4_empty_cross_scope_targets_deny(engine):
    ctx = ScopeContext(
        granted_scopes=frozenset({"memory-providers"}),
        user_authorized_cross_scope=True,
        cross_scope_targets=frozenset(),
        authorization_source="user-typed-directive",
    )
    assert not engine.authorize_read("acs-project", ctx).allowed


def test_F4_cross_scope_without_attribution_denied(engine):
    ctx = ScopeContext(
        granted_scopes=frozenset({"memory-providers"}),
        user_authorized_cross_scope=True,
        cross_scope_targets=frozenset({"acs-project"}),
        authorization_source=None,
    )
    assert not engine.authorize_read("acs-project", ctx).allowed


# F6: a single string passed as a scope set turned into a substring grant.
def test_F6_string_where_frozenset_expected_is_rejected():
    with pytest.raises(TypeError):
        ScopeContext(granted_scopes="acs-project")


def test_F6_string_cross_scope_targets_rejected():
    with pytest.raises(TypeError):
        ScopeContext(granted_scopes=frozenset(), cross_scope_targets="acs-project")


# F7: correct() superseded an out-of-scope original and disclosed its id.
def test_F7_correct_requires_read_of_original(sub):
    sub.put(rec("secret1", "acs-project", "ACS secret"), ALL)
    with pytest.raises(ScopeViolation):
        sub.correct("secret1", rec("r2", "memory-providers", "correction"), MEM_ONLY)
    assert sub.get("r2", ALL) is None


# F8: authorization happened outside the write lock (TOCTOU on delete).
def test_F8_delete_authorization_runs_under_write_lock(tmp_path, engine, monkeypatch):
    db = tmp_path / "weft.db"
    s = Substrate(db, scope_engine=engine, busy_timeout=0.2)
    s.put(rec("r1", "memory-providers", "x"), ALL)

    original = engine.authorize_delete

    def race(record_scope, ctx):
        # A concurrent writer tries to re-scope the row while we authorize.
        other = sqlite3.connect(str(db), timeout=0.05, isolation_level=None)
        with pytest.raises(sqlite3.OperationalError):
            other.execute("UPDATE records SET scope='acs-project' WHERE record_id='r1'")
        other.close()
        return original(record_scope, ctx)

    monkeypatch.setattr(engine, "authorize_delete", race)
    s.delete("r1", ALL)
    s.close()


# F9: record_id reached the filesystem unsanitized.
@pytest.mark.parametrize("rid", ["../escape", "/abs/path", "a/b", "", "x..y"])
def test_F9_unsafe_record_id_rejected(sub, rid):
    with pytest.raises(SchemaViolation):
        sub.put(rec(rid, "memory-providers"), ALL)


def test_F9_unsafe_id_rejected_even_without_schema_validation(sub):
    with pytest.raises(SchemaViolation):
        sub.put(rec("", "memory-providers"), ALL, validate_schema=False)


# F10: stale projection files kept out-of-scope content after scope narrowed.
def test_F10_projection_prunes_files_no_longer_in_scope(sub, tmp_path):
    sub.put(rec("r1", "memory-providers", "mp fact"), ALL)
    sub.put(rec("r2", "acs-project", "ACS secret"), ALL)
    out = tmp_path / "proj"
    sub.write_projection(out, ALL)
    assert (out / "r2.json").exists()
    sub.write_projection(out, MEM_ONLY)
    assert not (out / "r2.json").exists(), "out-of-scope file survived narrowing"
    assert (out / "r1.json").exists()


def test_F10_projection_status_detects_stale_and_tampered(sub, tmp_path):
    sub.put(rec("r1", "memory-providers", "mp fact"), ALL)
    out = tmp_path / "proj"
    sub.write_projection(out, MEM_ONLY)
    assert sub.projection_status(out, MEM_ONLY)["state"] == "current"
    (out / "r1.json").write_text('{"text": "TAMPERED"}')
    assert sub.projection_status(out, MEM_ONLY)["state"] == "tampered"
    sub.write_projection(out, MEM_ONLY)
    sub.put(rec("r2", "memory-providers", "later"), ALL)
    assert sub.projection_status(out, MEM_ONLY)["state"] == "stale"


# F12: journal_entries() was ungated and disclosed out-of-scope ids and scopes.
def test_F12_journal_is_filtered_by_context(sub):
    sub.put(rec("secret1", "acs-project", "ACS secret"), ALL)
    sub.put(rec("m1", "memory-providers", "mp"), ALL)
    ids = {e["record_id"] for e in sub.journal_entries(MEM_ONLY)}
    assert "secret1" not in ids
    assert "m1" in ids


def test_F12_journal_without_context_is_empty(sub):
    sub.put(rec("m1", "memory-providers", "mp"), ALL)
    assert sub.journal_entries(None) == []


# F13: a lock error during denial journalling masked the denial.
def test_F13_denial_is_refused_even_when_audit_write_is_locked(tmp_path, engine):
    db = tmp_path / "weft.db"
    s = Substrate(db, scope_engine=engine, busy_timeout=0.2)
    holder = sqlite3.connect(str(db), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")  # hold the write lock
    try:
        with pytest.raises(ScopeViolation):
            s.put(rec("r1", "memory-providers"), None)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
        s.close()


# F14: ROLLBACK with no open transaction replaced the real error.
def test_F14_real_lock_error_is_not_masked_by_rollback(tmp_path, engine):
    db = tmp_path / "weft.db"
    s = Substrate(db, scope_engine=engine, busy_timeout=0.2)
    holder = sqlite3.connect(str(db), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError) as info:
            s.put(rec("r1", "memory-providers"), ALL)
        assert "no transaction is active" not in str(info.value)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
        s.close()


# F15: delete cascaded supersedes-unlinks into scopes the caller cannot write.
def test_F15_delete_refused_when_dependent_is_out_of_scope(sub):
    sub.put(rec("r1", "acs-project", "ACS original"), ALL)
    sub.correct("r1", rec("r2", "memory-providers", "correction"), ALL)
    # ACS_ONLY may delete r1, but the dependent r2 lives in memory-providers,
    # which ACS_ONLY cannot write. The cascade would write into that scope.
    assert sub.delete("r1", ACS_ONLY) is False
    assert sub.get("r1", ALL) is not None
    assert sub.get("r2", ALL).supersedes == "r1"


# F16: query(None) raised AttributeError instead of failing closed.
def test_F16_query_without_context_returns_nothing(sub):
    sub.put(rec("m1", "memory-providers", "mp"), ALL)
    assert sub.query(None) == ([], [])


# F18: read_denied journal reason was hardcoded.
def test_F18_read_denial_reason_is_accurate(sub):
    sub.put(rec("secret1", "acs-project", "ACS secret"), ALL)
    assert sub.get("secret1", MEM_ONLY) is None
    denials = [e for e in sub.journal_entries(ALL) if e["op"] == "read_denied"]
    assert denials and "no scope context" not in denials[-1]["detail"]


# F20: validate_schema=False let empty ids through.
def test_F20_empty_id_rejected_without_validation(sub):
    with pytest.raises(SchemaViolation):
        sub.put(rec("", "memory-providers"), ALL, validate_schema=False)

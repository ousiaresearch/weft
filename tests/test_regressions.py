"""Regression tests for defects found by the Codex adversarial review of a37d175.

Each test names the finding it guards (F#). Every test here must FAIL on the
pre-fix code and PASS after the fix. They define the new API: write operations
take a ScopeContext, and reads and journal access are gated.
"""

import json
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
def test_F10_projection_prunes_records_that_leave_the_view(sub, tmp_path):
    sub.put(rec("r1", "memory-providers", "mp fact"), ALL)
    sub.put(rec("r2", "acs-project", "ACS secret"), ALL)
    out = tmp_path / "proj"
    sub.write_projection(out, ALL)
    assert (out / "r2.json").exists()
    assert sub.delete("r2", ALL) is True
    sub.write_projection(out, ALL)
    assert not (out / "r2.json").exists(), "removed record survived re-projection"
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
    # Corrections now stay in one scope (N10), so a cross-scope dependent can only
    # exist as a legacy row. Insert one directly to exercise the cascade check.
    sub.put(rec("r1", "acs-project", "ACS original"), ALL)
    _legacy_dependent(sub, "r2", "memory-providers", "r1")
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


# ---- Second review of 126cfc1 (BLOCK). Each test names its finding. ----

def _legacy_dependent(sub, rid, scope, supersedes):
    """Insert a row directly, as a pre-existing legacy record would appear."""
    sub._conn.execute(
        "INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)",
        (rid, "Observation", scope, "stated", '{"text": "correction"}', 0.0, None, supersedes, "[]", "{}"),
    )


# F-A: content rule evaded by nested payloads, split tokens, and case/unicode variants.
@pytest.mark.parametrize(
    "payload",
    [
        {"text": "note", "meta": {"secret": "The Listening Commonwealth"}},
        {"items": [{"body": "ACS attestation"}]},
        {"text": "A.C.S. attestation"},
        {"text": "A C S attestation"},
        {"text": "ＡＣＳ attestation"},  # fullwidth
        {"text": "acs attestation"},
        {"text": "Listening-Commonwealth charter"},
        {"keyword ACS": "value"},  # marker in a key name
    ],
)
def test_F_A_marker_variants_refused_under_other_scope(engine, payload):
    rec = Record(record_id="rn", record_type="Observation", scope="memory-providers", payload=payload)
    assert not engine.authorize_write(rec, MEM_ONLY).allowed, payload


def test_F_A_marker_variants_withheld_on_read(engine):
    rec = Record(record_id="rn", record_type="Observation", scope="memory-providers",
                 payload={"text": "note", "meta": {"secret": "The Listening Commonwealth"}})
    readable = engine.filter_readable([rec], MEM_ONLY)
    assert readable == []


def test_F_A_no_false_positive_on_substring_words(engine):
    """'impacts' contains 'acs' but is a different word; must not be treated as the ACS marker."""
    rec = Record(record_id="ok", record_type="Observation", scope="memory-providers",
                 payload={"text": "The impacts were small."})
    assert engine.authorize_write(rec, MEM_ONLY).allowed


# F-B: a refused cascade must not disclose the dependent's id or scope to the caller.
def test_F_B_refused_cascade_journal_does_not_name_dependent(sub):
    sub.put(rec("r1", "acs-project", "ACS original"), ALL)
    _legacy_dependent(sub, "r2", "memory-providers", "r1")
    assert sub.delete("r1", ACS_ONLY) is False
    visible = " ".join(f"{e['detail']} {e['record_id']}" for e in sub.journal_entries(ACS_ONLY))
    assert "r2" not in visible
    assert "memory-providers" not in visible


# F-C: projection with no context must not delete anything.
def test_F_C_projection_without_context_is_non_destructive(sub, tmp_path):
    sub.put(rec("r1", "memory-providers", "mp fact"), ALL)
    out = tmp_path / "proj"
    sub.write_projection(out, ALL)
    before = sorted(p.name for p in out.iterdir())
    sub.write_projection(out, None)
    assert sorted(p.name for p in out.iterdir()) == before


# F-D: denial reasons must not name scopes the caller cannot see.
def test_F_D_content_denial_reason_names_no_other_scope(engine):
    rec = Record(record_id="rn", record_type="Observation", scope="memory-providers",
                 payload={"text": "The Listening Commonwealth"})
    reason = engine.authorize_write(rec, MEM_ONLY).reason
    assert "acs-project" not in reason


def test_F_D_read_denial_reason_names_no_other_scope(engine):
    rec = Record(record_id="rn", record_type="Observation", scope="memory-providers",
                 payload={"text": "The Listening Commonwealth"})
    reason = engine.authorize_read("memory-providers", MEM_ONLY, text="The Listening Commonwealth").reason
    assert "acs-project" not in reason


# F-E / F21: the database file must not be readable by other local users.
def test_F_E_database_file_is_owner_only(tmp_path, engine):
    db = tmp_path / "weft.db"
    s = Substrate(db, scope_engine=engine)
    s.put(rec("r1", "memory-providers", "x"), ALL)
    s.close()
    assert (os.stat(db).st_mode & 0o077) == 0


# F17: concurrent writers, same ids, no silent last-writer-wins.
def test_F17_same_id_race_has_exactly_one_winner_across_processes(tmp_path, engine):
    db = tmp_path / "weft.db"
    Substrate(db, scope_engine=ScopeEngine(known_scopes=KNOWN)).close()
    script = (
        "import sys; sys.path.insert(0, %r)\n"
        "from weft.scope import ScopeContext, ScopeEngine\n"
        "from weft.substrate import Record, RecordConflict, Substrate\n"
        "s = Substrate(%r, scope_engine=ScopeEngine(known_scopes=%r), busy_timeout=10)\n"
        "ctx = ScopeContext(granted_scopes=frozenset({'memory-providers'}))\n"
        "won = 0\n"
        "for i in range(20):\n"
        "    try:\n"
        "        s.put(Record(record_id='race%%d' %% i, record_type='Observation',\n"
        "                     scope='memory-providers', payload={'text': 'x'}), ctx)\n"
        "        won += 1\n"
        "    except RecordConflict:\n"
        "        pass\n"
        "print(won)\n"
    ) % (str(REPO), str(db), sorted(KNOWN))
    procs = [subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE) for _ in range(3)]
    wins = [int(p.communicate(timeout=120)[0].strip()) for p in procs]
    assert sum(wins) == 20, wins


# F19: the global allowlist must not be mutable from outside the engine.
def test_F19_global_allowlist_not_directly_mutable(engine):
    with pytest.raises(AttributeError):
        engine.global_allowlist.add("acs-project")  # type: ignore[attr-defined]
    assert not engine.authorize_read("acs-project", ScopeContext()).allowed


# F-A (homoglyphs): Cyrillic and Greek lookalikes must fold to the Latin marker.
@pytest.mark.parametrize("text", ["\u0410\u0421S attestation", "Listening Commonwealth \u0430\u0441s"])
def test_F_A_homoglyph_variants_refused(engine, text):
    rec = Record(record_id="rh", record_type="Observation", scope="memory-providers", payload={"text": text})
    assert not engine.authorize_write(rec, MEM_ONLY).allowed, text


# ---- Third review of 83657e0 (BLOCK). ----

# N1: combining marks must not split or merge a marker token.
@pytest.mark.parametrize("text", ["A\u0301CS charter", "AC\u0308S charter", "A\u200bC\u200bS charter", "A\u00adCS charter"])
def test_N1_combining_and_invisible_marks_do_not_hide_marker(engine, text):
    rec = Record(record_id="rc", record_type="Observation", scope="memory-providers", payload={"text": text})
    assert not engine.authorize_write(rec, MEM_ONLY).allowed, repr(text)


# N2: lookalike letters with diacritics or in the Greek/Latin range.
@pytest.mark.parametrize("text", ["\u00e5CS charter", "\u00e2CS charter", "\u0251CS charter", "AC\u03c2 charter"])
def test_N2_diacritic_and_lookalike_letters_fold(engine, text):
    rec = Record(record_id="rl", record_type="Observation", scope="memory-providers", payload={"text": text})
    assert not engine.authorize_write(rec, MEM_ONLY).allowed, repr(text)


# N3: projection prunes any record-named file in its directory that is no longer in scope.
def test_N3_unmanifested_files_are_left_alone(sub, tmp_path):
    """Round 3 widened pruning; round 4 (G2) narrowed it. A file this view's
    manifest never named is not ours to delete."""
    sub.put(rec("r1", "memory-providers", "mp fact"), ALL)
    out = tmp_path / "proj"
    out.mkdir()
    (out / "r9.json").write_text('{"text": "not ours"}')
    sub.write_projection(out, MEM_ONLY)
    assert (out / "r9.json").read_text() == '{"text": "not ours"}'
    assert (out / "r1.json").exists()


# N4: exclusions must not disclose record ids the caller cannot see.
def test_N4_exclusions_do_not_name_out_of_scope_records(engine):
    acs = Record(record_id="acs1", record_type="Observation", scope="acs-project", payload={"text": "x"})
    readable = engine.filter_readable([acs], MEM_ONLY)
    assert readable == []
    assert "acs1" not in str(readable)


# N5: projection with no context must not create anything, including the directory.
def test_N5_projection_without_context_creates_nothing(sub, tmp_path):
    out = tmp_path / "never-created"
    sub.write_projection(out, None)
    assert not out.exists()


# N6: a write in an invisible scope must not mark the caller's projection stale.
def test_N6_invisible_write_does_not_mark_projection_stale(sub, tmp_path):
    sub.put(rec("r1", "memory-providers", "mp fact"), ALL)
    out = tmp_path / "proj"
    sub.write_projection(out, MEM_ONLY)
    sub.put(rec("secret", "acs-project", "ACS secret"), ALL)
    assert sub.projection_status(out, MEM_ONLY)["state"] == "current"


# N7: the unlink journal entry must not name the deleted record to readers of the dependent's scope.
def test_N7_unlink_journal_does_not_name_deleted_record(sub):
    sub.put(rec("r1", "memory-providers", "original"), ALL)
    sub.correct("r1", rec("r2", "memory-providers", "correction"), ALL)
    assert sub.delete("r1", ALL) is True
    details = [e["detail"] for e in sub.journal_entries(MEM_ONLY) if e["op"] == "unlink_supersedes"]
    assert details and all("r1" not in d for d in details)


# N8: the database and its sidecar files are never created with loose permissions.
def test_N8_sidecars_are_owner_only_from_creation(tmp_path, engine):
    db = tmp_path / "fresh.db"
    s = Substrate(db, scope_engine=engine)
    s.put(rec("r1", "memory-providers", "x"), ALL)
    for suffix in ("", "-wal", "-shm"):
        p = str(db) + suffix
        if os.path.exists(p):
            assert (os.stat(p).st_mode & 0o077) == 0, p
    s.close()


# N9: the global allowlist is configuration-time only; there is no runtime mutator.
def test_N9_no_runtime_method_grants_global_read(engine):
    assert not hasattr(engine, "allow_globally")
    ctor = ScopeEngine(known_scopes=KNOWN, global_allowlist={"memory-providers"})
    assert ctor.authorize_read("memory-providers", ScopeContext()).allowed


# N10: a correction must stay in the original's scope.
def test_N10_correction_cannot_move_scope(sub):
    sub.put(rec("r1", "memory-providers", "original"), ALL)
    with pytest.raises(ScopeViolation):
        sub.correct("r1", rec("r2", "acs-project", "moved"), ALL)
    assert sub.get("r2", ALL) is None


# ---- Round 4 (6260aaf) GATE findings G1-G8. ----

# G1: projection_status must not disclose ids outside the caller's view.
def test_G1_status_discloses_no_out_of_view_ids(sub, tmp_path):
    sub.put(rec("r1", "memory-providers", "mp fact"), ALL)
    out = tmp_path / "proj"
    sub.write_projection(out, MEM_ONLY)
    sub.put(rec("secret9", "acs-project", "ACS secret"), ALL)
    status = sub.projection_status(out, MEM_ONLY)
    assert "secret9" not in json.dumps(status)
    assert "unexpected" not in status


# G2: a caller without authority over a projection must not delete its files.
def test_G2_other_view_cannot_prune_a_projection(sub, tmp_path):
    sub.put(rec("r1", "memory-providers", "mp"), ALL)
    sub.put(rec("r2", "acs-project", "ACS"), ALL)
    out = tmp_path / "proj"
    sub.write_projection(out, ALL)
    with pytest.raises(ScopeViolation):
        sub.write_projection(out, MEM_ONLY)
    assert (out / "r2.json").exists() and (out / "r1.json").exists()


def test_G2_unmanaged_file_with_projected_name_is_not_overwritten(sub, tmp_path):
    sub.put(rec("r1", "memory-providers", "mp"), ALL)
    out = tmp_path / "proj"
    out.mkdir()
    (out / "r1.json").write_text('{"text": "foreign file"}')
    with pytest.raises(ScopeViolation):
        sub.write_projection(out, MEM_ONLY)
    assert (out / "r1.json").read_text() == '{"text": "foreign file"}'


# G3: an id that is unsafe for a file name must never reach the filesystem.
def test_G3_unsafe_stored_id_never_written_outside_projection(sub, tmp_path):
    sub._conn.execute(
        "INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("../escape", "Observation", "memory-providers", "stated", '{"text": "x"}', 0.0, None, None, "[]", "{}"),
    )
    out = tmp_path / "proj"
    sub.write_projection(out, MEM_ONLY)
    assert not (tmp_path / "escape.json").exists()
    assert not (out / "../escape.json").exists()


# G4: an overwrite denial must not name the incumbent's scope.
def test_G4_overwrite_denial_names_no_scope(sub):
    sub.put(rec("r1", "acs-project", "ACS original"), ALL)
    with pytest.raises(ScopeViolation) as info:
        sub.put(rec("r1", "memory-providers", "clobber"), MEM_ONLY, overwrite=True)
    assert "acs-project" not in str(info.value)


# G5: a caller without the incumbent's scope must not learn that the id exists.
def test_G5_existing_foreign_id_is_indistinguishable_from_denied_write(sub):
    sub.put(rec("r1", "acs-project", "ACS original"), ALL)
    with pytest.raises(ScopeViolation):
        sub.put(rec("r1", "memory-providers", "second"), MEM_ONLY)  # no RecordConflict
    with pytest.raises(ScopeViolation):
        sub.put(rec("never-existed", "acs-project", "x"), MEM_ONLY)
    try:
        sub.put(rec("r1", "memory-providers", "second"), MEM_ONLY)
    except ScopeViolation as exc:
        a = str(exc)
    try:
        sub.put(rec("never-existed", "acs-project", "x"), MEM_ONLY)
    except ScopeViolation as exc:
        b = str(exc)
    assert a == b


# G6 and G7: query exclusions must carry no reasons and no counts.
def test_G6_G7_query_exclusions_are_empty_and_reveal_nothing(sub):
    sub.put(rec("secret1", "acs-project", "ACS secret"), ALL)
    sub.put(rec("secret2", "acs-project", "ACS secret 2"), ALL)
    readable, exclusions = sub.query(MEM_ONLY)
    assert readable == []
    assert exclusions == []


# G8: a store change in the caller's own view is stale, not tampered.
def test_G8_own_view_change_reports_stale_not_tampered(sub, tmp_path):
    sub.put(rec("r1", "memory-providers", "first"), ALL)
    out = tmp_path / "proj"
    sub.write_projection(out, MEM_ONLY)
    sub.put(rec("r1", "memory-providers", "second"), ALL, overwrite=True)
    assert sub.projection_status(out, MEM_ONLY)["state"] == "stale"

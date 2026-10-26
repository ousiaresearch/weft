# Phase 1 Decisions

**Phase:** 1 — substrate and scope spike
**Status:** Decided; implementation follows.
**Constraint inherited from Phase 0:** independently written, permissively licensed, no bundled
AGPL/ELv2/non-commercial component.

## Decisions

### 1. Storage: SQLite as transactional store + human-readable projection files

**Decision:** SQLite is the authoritative store. Structured files are a *projection* of it, written
for human inspection and diff-ability, never read as authority.

**Rationale:** The core requirement is a fail-closed write path with a durable journal — that needs
transactions, which SQLite provides and plain files do not. Human inspectability matters, but it is
satisfiable by projection, not by making files authoritative. Making files authoritative would
reintroduce the journal/file ordering ambiguity the plan flagged as unresolved.

**Consequence to test in Phase 1:** projection divergence (a file edited by hand) must be *detectable*
and must never silently override the store. Hand-edited projection files are ignored, with a warning.

### 2. Language/runtime: Python for the substrate, TypeScript only for the Electron UI

**Decision:** Python 3.11+ for substrate, scope engine, retrieval, and service. TypeScript/Electron
for the desktop UI, talking to the service over HTTP.

**Rationale:** The Electron UI and the substrate are separated by a service boundary anyway (the
desktop is a *client*, per the architecture). So the "shared types" argument for TypeScript is weak —
types are shared over an API contract, not a language. Python gives faster iteration on the
substrate and reasoning loop, which is where the real work is. TypeScript's advantage would be
in-process type sharing, which this architecture deliberately avoids.

**Constraint noted:** the host's default `python3` is 3.9.6, which lacks `hashlib.scrypt`. The
substrate requires 3.11+ (recorded from prior work in this environment). Phase 1 must pin and verify
this explicitly rather than inherit the ambient interpreter.

### 3. Schema language: JSON Schema (Draft 2020-12), not a custom typed language

**Decision:** JSON Schema for record validation.

**Rationale:** A custom typed language (the Arsumbris approach) is genuinely attractive for
expressiveness, but it is a large surface to build and specify before any memory behavior exists, and
it is *advisory* in the reference implementation anyway. JSON Schema is standard, tooled, testable,
and sufficient for the record shapes we need. The scope and authority rules — which is where the real
correctness lives — are enforced in Python at the write boundary, **not** delegated to the schema
language. Schema validation is advisory; scope enforcement is authoritative.

**Revisit trigger:** if Phase 2 shows record relationships (reference integrity, inheritance, sealed
variants) that JSON Schema expresses poorly.

### 4. Journal/file/index commit ordering: single SQLite transaction, WAL, projections after commit

**Decision:** One SQLite transaction covers (a) the record write, (b) the journal entry, and
(c) index updates. Projection files and any model-configured store are updated **after** the
transaction commits, asynchronously, and are idempotent and rebuildable.

**Rationale:** This eliminates the ordering question rather than solving it — there is one commit
point, and everything rebuildable is derived. Crash between commit and projection leaves a stale
projection, which is detectable and harmless, not a lost write. This is the opposite failure mode
from losing an accepted write, and it is the correct one.

**To verify by fault injection in Phase 1:**
- Kill mid-transaction → no partial record, no orphan journal entry.
- Kill after commit, before projection → record present, projection stale and flagged.
- ~~Rebuild-from-journal reproduces the identical record set.~~ Withdrawn: the journal is an audit trail, not a rebuild source (see the addendum above).
- Concurrent writers → no lost update, no interleaved corruption.

## Phase 1 deliverables

1. `weft/substrate/` — record store, journal, schema validation, projection writer.
2. `weft/scope/` — deterministic scope engine (deny-by-default, content-based scoping).
3. `tests/` — including the fault-injection suite above.
4. `docs/adr/0001-phase1-decisions.md` — this record.

## What Phase 1 does NOT include

Retrieval ranking, reasoning loop, model integration, ingestion pipeline, or the Electron UI. Those
are Phases 3–4. Phase 1 proves the substrate holds, scope gates correctly, and the system survives
being killed.

## Audit gate

Phase 1 completes only after an independent adversarial audit of the substrate and scope engine,
including an attempt to defeat the scope gate. The regression target from Phase 0 is the acceptance
criterion: *a query about memory providers returns zero ACS history.*

## Addendum (Phase 1 fixes after adversarial review of a37d175)

Codex review of a37d175 returned BLOCK (21 findings). The fixes below change decisions above; each is covered by tests in `tests/test_regressions.py` and `tests/test_substrate.py`.

- **Authority.** Writes, overwrites, deletes, and corrections require a granted scope on the caller's `ScopeContext`. Knowing that a scope exists is not authority. The global allowlist and cross-scope reads never confer write or delete authority.
- **Journal is an audit trail, not a rebuild source.** The earlier claim that rebuild-from-journal reproduces the record set is withdrawn. The journal holds operations and reasons, never payloads, so deletion cannot leave content behind in it. SQLite remains the only authority. Journal reads are filtered by the caller's scope.
- **Content rule replaces the no-op guard.** Scopes may declare markers. Content naming another scope's marker is refused on write and withheld on read. Regex project detection stays advisory.
- **Overwrite is explicit.** `put` raises `RecordConflict` for an existing id unless `overwrite=True`, and an overwrite needs a granted scope for the incumbent.
- **Projections.** Gated by context, pruned of files no longer in scope, written atomically, and validated by a manifest with per-file hashes and a store version. `projection_status` reports `current`, `stale`, `tampered`, or `missing`.
- **Record ids** are restricted to `[A-Za-z0-9][A-Za-z0-9_-]{0,127}` on every write, regardless of schema validation, because they are file names.
- **Known limits, unchanged:** the library trusts the code that constructs a `ScopeContext`. Attribution of user-authorized cross-scope requests is enforced at the service boundary (Phase 6). Global allowlist and scope registration are configuration, not authenticated.

### Accepted residuals (recorded before round 4 review)

1. **Content markers are best-effort, not a security boundary.** They are defence in depth against mis-filed content. The security guarantee rests on granted scopes. Lookalike coverage is a short explicit table (Cyrillic, Greek, and some Latin variants), not full Unicode confusables. Rounds 1-3 found evasions in this category; they are documented, and new ones in this category are residuals unless they defeat the granted-scope gate.
2. **In-process trust.** Code that holds a `Substrate` can reach its raw SQLite connection (`_conn`). The library enforces scope for its own API only. The service boundary (Phase 6) must not expose the connection or the database file to callers.
3. **Plaintext at rest.** The database holds all scopes unencrypted. It is created owner-only (0600, umask 077, and sidecars chmod'ed). Encryption at rest is a later phase.
4. **Global allowlist is configuration.** It is set at engine construction and exposed read-only. An attacker who can construct or mutate the engine object already controls the process.
5. **Same-scope overwrite is last-writer-wins.** `put(..., overwrite=True)` by a caller with a granted scope replaces the record. Cross-process races without overwrite produce exactly one winner per id (tested).

### Stopping rule for the review loop

Round 4 is the last open review round for Phase 1. If its BLOCK findings are limited to residuals 1 and 2 above, or to new heuristic-coverage gaps that do not defeat the granted-scope gate, they are accepted and recorded here, and Phase 2 begins. Any finding that defeats the granted-scope gate, or that discloses content or ids to a caller without the scope, blocks Phase 1 and is fixed first.

### Round-4 changes (GATE findings G1-G8)

- **One projection directory per view.** A projection is owned by the view (the set of granted scopes) that wrote it. Another view cannot write into it. Writing over unmanaged files with projected names is refused. Use separate directories for separate views.
- **Pruning is narrow.** A projection removes only files named in its own view's manifest. Files it never named are left alone (G2). A record that leaves the view, for example by deletion, is pruned on the next write.
- **Status reports only the caller's view.** It names no record outside that view (G1). It distinguishes `tampered` (a file's bytes differ from what the projection wrote) from `stale` (the view changed in the store since the projection was written) (G8). It no longer uses a global store version.
- **Unsafe ids are skipped on projection.** Ids that are not safe file names are never written to disk (G3).
- **Generic denials.** A refused write, overwrite, or correction gets one generic message, so a caller cannot tell an id in another scope from a non-existent one (G4, G5). Reasons go to the journal, which only holders of the record's scope can read.
- **Query returns readable records and no exclusions.** Exclusion reasons and counts are no longer returned, so a caller learns nothing about records it cannot read (G6, G7).
- **Known residuals, unchanged:** the content-marker table is best-effort; the raw connection is reachable in-process; the database is plaintext at rest; same-scope overwrites are last-writer-wins.

### Round-5 changes (GATE findings G9-G13)

- **Denials are never written into a scope's journal (G10).** A caller that lacks a scope is by definition not a holder of it, so it must not add rows there. Denial rows (write, overwrite, correction, delete, read) are written with no scope, no record id, and a generic detail. Consequence: holders of a scope no longer see denial entries for that scope in `journal_entries`. Denials remain in the raw audit table for operators.
- **Correction is not an existence oracle (G9).** A missing original and an unreadable original raise the same exception with the same message.
- **Supersession is set only by `correct()` (G13).** `put()` refuses a record that carries `supersedes`, so no caller can link a record into another scope's lineage.
- **Journal sequence is renumbered per view (G12).** `journal_entries` numbers the entries a caller may read 1..n, so global gaps are not observable.
- **Projection view includes all cross-scope authority (G11).** The view token covers granted scopes, cross-scope authorization, targets, and authorization source. Two callers with the same granted set but different cross-scope authority do not share a projection.
- **Projection directory is owner-only (F-E residual R3).** `write_projection` sets the directory to 0700.

Residuals carried forward unchanged from round 5: R1 (content rule does not inspect provenance and source fields), R2 (multi-marker co-occurrence), R4 (confusables outside the table), R6 (`register_scope` is a runtime mutator), R7 (unauthenticated manifest), R8 and R9 (crash and mutation windows).

### Phase 1 closure

- **Review:** round 6 on `98dbcf7` returned APPROVE_WITH_RESIDUALS. No gate-class findings.
- **Post-review cleanup (not re-reviewed; no behaviour change):** dead `reason` assignments removed; the projection directory is created owner-only (0700) from the start, closing the umask window; the stale rebuild-from-journal verification line is struck through.
- **Residuals accepted for Phase 1, carried into later phases:**
  - R1: the content rule inspects only `payload`. Provenance and source fields are not scanned. Phase 2 classifier must cover them.
  - R2: in-process code can reach `_conn`. Enforced at the service boundary in Phase 6.
  - R3: content-marker coverage is best-effort. Confusables outside the table are accepted residuals.
  - R4: over-blocking of unrelated text that names another scope's marker. Accepted; the marker list is configuration.
  - R5: `register_scope` is a runtime mutator for markers. Phase 6 service boundary must restrict it.
  - R6: the projection manifest is unauthenticated. Forging it requires write access to the owner-only projection directory, which is in-process trust.
  - R7: non-`ScopeContext` objects raise `AttributeError` rather than failing closed with a typed error. No data is returned.
  - R8: delete with `ctx=None` journals the caller-supplied id into the raw audit table (scope NULL, not visible to any view).
  - R9: the delete reason is free text with no length cap. Written only into the record's own scope journal, by a holder of that scope.
  - R10: the marker token-run matcher is separator-insensitive by design and over-blocks some strings.
  - R11: crash windows between record write and manifest rewrite can report `tampered` rather than `stale`.
- **Traceability:** the round-1 finding labelled F11 has no recoverable text in the repository; reviewer treats it as a numbering gap and judged the gated paths it would cover to be covered by F1, F7, F12, and F16.
- **Phase 1 status:** closed with the residuals above. Phase 2 may start.

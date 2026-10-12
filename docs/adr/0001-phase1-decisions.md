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
- Rebuild-from-journal reproduces the identical record set.
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

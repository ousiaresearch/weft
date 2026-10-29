# Phase 5 — Correction Semantics, Privacy, Portability

## Effective-Time and As-Of Queries

### 5.1 Design

Current `Substrate.get(record_id, ctx)` returns the latest version of a record. Effective-time queries ask: "what was the state of this record at time T?" As-of queries ask: "what records were visible at time T?"

### 5.2 Implementation

- Add `effective_time` column to `records` table (defaults to creation time, updated on correction).
- Add `superseded_by` column to track correction lineage (existing `source_ids` partially covers this).
- Add `Substrate.get_at(record_id, timestamp, ctx)` which filters `effective_time <= timestamp` and `superseded_by IS NULL`.
- Add `Substrate.query_as_of(timestamp, scope, ctx)` which returns all records visible at that timestamp.

### 5.3 Correction Semantics

Corrections are immutable — the original record is preserved with its original `effective_time`. The correction record has a later `effective_time` and a `supersedes` source_id pointing to the original. Querying the original ID at a timestamp before the correction returns the original; querying at a timestamp after the correction returns the correction via `superseded_by` chain.

### 5.4 Export / Import (Closed)
- Export: `Substrate.export_scope(scope, ctx)` serializes readable records in scope to a portable dict with schema version.
- Import: `Substrate.import_scope(data, ctx)` validates schema version, scope, and write auth; inserts with INSERT OR REPLACE.
- Round-trip: export then import preserves record content and lineage.

### 5.5 Backup / Retention / Purge

- Backup: periodic export of all records.
- Retention: `derived` records (Reasoning, Summary, Claim) may be pruned after a configurable retention period if their source records are still present.
- Purge: deletion of a source record must cascade to derived artifacts that depend solely on it.

## Exit Criteria

- `get_at` and `query_as_of` return correct states for test timelines with corrections.
- Export/import round-trip verified for 1000-record corpus.
- Retention policy respects configurable TTL.
- Independent review passed.

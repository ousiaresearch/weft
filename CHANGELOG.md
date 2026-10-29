# Changelog

All notable changes to Weft are documented here.

## [0.1.0a0] — 2026-10-29

### Added
- Phase 1–6 implementation: substrate, scope engine, ingestion, retrieval, reasoning loop, server, shadow mode, logging.
- 429 regression tests covering scope, invariants, fault injection, deletion, export/import, retention, server endpoints, shadow mode, logging redaction, and evaluation.
- Evaluation set v1 with 6 gold cases for grounded synthesis, abstention, contradiction detection, cross-scope leak, content-conflict denial, and correction lineage.
- Adversarial review cycle with Codex; all Phase 1–6 findings repaired and regression-tested.

### Changed
- README updated to reflect Phase 7 gated cutover and current test count.
- Plan document updated to mark Phases 1–6 closed.

### Fixed
- Phase 6 adversarial review findings: scope allowlist via `WEFT_KNOWN_SCOPES`, `import_scope` validation, `get_at` effective_at guard, logging regex redaction.

### Security
- Content-conflict detection prevents mis-filed records from being read across scope boundaries.
- Retrieved content is never re-ingested; recall output is assembled at call time.
- Fail-closed on unreachable policy engine, malformed extraction, and failed model calls.

### License
- AGPL-3.0. See `LICENSE`.

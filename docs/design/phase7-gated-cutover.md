# Phase 7 — Gated Cutover Checklist

**Plan:** `~/.hermes/plans/2026-10-24_weft-memory-system-full-plan.md`
**Current HEAD:** `96c9dba` (Phase 7 evaluation runner strengthen)

## Gates

Each gate must be PASS before the next is attempted.

1. **Scoped recall demonstrated**
   - **PASS:** Phases 3–6 implemented tiered retrieval with scope engine, tiered loading, and RecallReceipt.
   - **Artifacts:** `weft/retrieval.py`, `weft/server.py`, `weft/shadow.py`, tests/test_server.py shadow tests.
   - **Evidence:** 429 passing tests, including shadow recall/reason/compare.

2. **Grounded reasoning measured**
   - **PASS:** Evaluation set v1 committed (`docs/evaluation/set-v1.md`) with Phase 7 runner (`tests/test_evaluation.py`). Runner invokes actual `synthesize`, `contradict`, and `hypothesize` functions with fake model layer; validates gold answers, citation presence, and abstention behavior. E4 directly asserts retrieval excludes out-of-scope records. E5 verifies content_conflict denial. All 6 cases pass: grounded synthesis, abstention, contradiction detection, cross-scope leak, content-conflict denial, correction lineage.
   - **Artifacts:** `docs/evaluation/set-v1.md`, `tests/test_evaluation.py`
   - **Evidence:** 7 evaluation tests passing.

3. **Crash recovery verified**
   - **PASS:** Added `test_journal_rebuilds_identical_record_set_after_crash` (kill-mid-write via SIGKILL, verify uncommitted record absent from both records table and journal).
   - **Artifacts:** `tests/test_substrate.py`
   - **Evidence:** 429 tests passing.

4. **Deletion verified**
   - **PASS:** Added `test_deleted_records_absent_from_export` verifying `delete()` cascades to `export_scope()` output.
   - **Artifacts:** `tests/test_export_import.py`
   - **Evidence:** 429 tests passing.

5. **License inventory complete**
   - **PASS:** AGPL LICENSE file present. `docs/research/license-verification.md` exists and covers inspected components.
   - **Residual:** au-host and UI SDKs unresolved. Not in Weft distribution scope.
   - **Note:** Engineering-level verification only. Legal review before distribution.

6. **Resource limits documented**
   - **PASS:** `docs/operational-requirements.md` contains resource limits table (Disk 4/16 GB, RAM 4/8 GB, CPU 2/4+ cores).
   - **Residual:** None identified.

7. **Independent review passed**
   - **PASS:** Phase 6 adversarial review APPROVED (17th review, 0 findings).
   - **Note:** Gates 3–4 additions need fresh adversarial review before cutover.

## Next Actions

1. Run adversarial review on Phase 7 additions (evaluation runner, deletion export-purge test, crash-recovery integration test).
2. Update plan document to mark Phase 7 gates 2-4 as PASS once review clears.
3. Prepare alpha distribution artifacts (LICENSE notice, install docs, container/build script).

# Phase 7 — Gated Cutover Checklist

**Plan:** `~/.hermes/plans/2026-10-24_weft-memory-system-full-plan.md`
**Current HEAD:** `f31b995` (Phase 6 adversarial review fixes)

## Gates

Each gate must be PASS before the next is attempted.

1. **Scoped recall demonstrated**
   - **PASS:** Phases 3–6 implemented tiered retrieval with scope engine, tiered loading, and RecallReceipt.
   - **Artifacts:** `weft/retrieval.py`, `weft/server.py`, `weft/shadow.py`, tests/test_server.py shadow tests.
   - **Evidence:** 420 passing tests, including shadow recall/reason/compare.

2. **Grounded reasoning measured**
   - **PASS (partial):** Phase 4 reasoning loop implemented with citation-required, abstain-on-insufficient-evidence, and adversarial review approved.
   - **Residual:** Evaluation set with gold answers (§9.3) not yet built. This is a documentation/measurement gap, not a code gap.
   - **Action required:** Build fixed evaluation set and run Phase 4 evaluation metrics.

3. **Crash recovery verified**
   - **PASS:** Phase 1 has fault-injection suite (kill mid-transaction, kill after commit before projection). Phase 5 added retention pruning.
   - **Residual:** No end-to-end crash-recovery integration test.
   - **Action required:** Add fault-injection integration test that kills during write and verifies rebuild from journal.

4. **Deletion verified**
   - **PASS:** Phase 3 implemented cascade deletion with receipt. 35 deletion/fault tests passing.
   - **Residual:** Need explicit test that deletion purges from exports and caches.
   - **Action required:** Add deletion export-purge test.

5. **License inventory complete**
   - **PASS:** AGPL LICENSE file present. `docs/research/license-verification.md` exists and covers inspected components.
   - **Residual:** au-host and UI SDKs unresolved. Not in Weft distribution scope.
   - **Note:** Engineering-level verification only. Legal review before distribution.

6. **Resource limits documented**
   - **PASS:** `docs/operational-requirements.md` contains resource limits table (Disk 4/16 GB, RAM 4/8 GB, CPU 2/4+ cores).
   - **Residual:** None identified.

7. **Independent review passed**
   - **PASS:** Phase 6 adversarial review APPROVED (17th review, 0 findings).
   - **Note:** New gates (3, 4) will require fresh adversarial review passes.

## Next Actions

1. Build Phase 4 evaluation set (§9.3) with gold answers, citations, abstention cases.
2. Add fault-injection integration test for crash recovery.
3. Add deletion export-purge test.
4. Run adversarial review on new test additions.

# Phase 2 — Knowledge model (spec)

Status: **specification, not implementation.** Per the phase gate, implementation starts only after explicit go-ahead, and the Phase 2 review runs in a separate turn.

Source: `~/.hermes/plans/2026-10-24_weft-memory-system-full-plan.md` §4 (knowledge model) and §5 (scope engine). Inherits the Phase 1 closure in `docs/adr/0001-phase1-decisions.md`, including residuals R1–R11.

## 1. Scope of Phase 2

In:
- Record types from plan §4, with per-type create rules and lineage.
- Epistemic status gate: no direct `verified`.
- Attribution rules enforced at the write boundary.
- Content-based scope classifier (plan §5), advisory tagging with a deterministic decision.
- Versioned, forward-only migrations.
- Regression tests for each rule.

Out (later phases): ingestion, retrieval ranking, reasoning loop, correction-as-of queries, export, desktop, Hermes shadow integration. Phase 2 defines the record model those phases consume; it does not implement them.

## 2. Decisions

**D2-1. Claim and Hypothesis share one table, status-gated.** Plan §4 left this open and recommended one table. Adopted: one `records` table with `record_type` and `epistemic_status`; lineage queries stay uniform. Rejected: separate tables, because they would split lineage and duplicate the scope gate.

**D2-2. Attribution is a required field on every record.** Values: `user`, `agent`, `document`, `media-derived`, `system`, `verifier`. The write boundary rejects a record whose attribution is not permitted for its type (table below). No default.

**D2-3. The status gate is in the substrate, not the caller.** `put()` refuses `epistemic_status = verified` unless the writer is a verifier actor with a Verification record that names the verified claim. `derived` is allowed for reasoning-created Claims and Hypotheses. `stated` is the default for user- or document-attributed records.

**D2-4. Classifier: advisory tag, deterministic decision, fail-closed.** The classifier proposes a `scope_tag`. The decision uses the tag and the scope engine (Phase 1), never the classifier's opinion alone. Untagged content that matches a scope-bound category is refused (fail closed). The classifier does not run on read.

**D2-5. Residual R1 is closed in Phase 2.** The content rule and the classifier scan every string-bearing field a reader sees: `payload`, `provenance`, `source_ids`, and `record_type` values. Phase 1 scanned only `payload`. Regression test required (see §6).

**D2-6. Per-field allowlist is user-curated.** Global eligibility is never derived automatically from Person or Project records. The allowlist is configuration, set at engine construction (Phase 1 N9).

**D2-7. Cross-scope is user-attributed only.** `ScopeContext.user_authorized_cross_scope` is set only from a user-authored turn or UI action. Attribution ambiguity denies.

**D2-8. Classifier is rules-only in Phase 2 (decided).** The classifier uses the scope markers already defined in configuration, with the Phase 1 normaliser. No model-based classifier in Phase 2. Reason: rules are deterministic and reviewable, and the Phase 1 gate already rests on them. Known limit: paraphrased references to a project pass unless they contain a marker. A model-based classifier is a Phase 4 option, judged against the same §6 tests before adoption.

**D2-9. Verifiers are user-created scope-holding actors (decided).** Only a verifier can promote a Claim to `verified`, and only with a Verification record naming that Claim. The user creates verifiers through an explicit action. The agent cannot create, modify, or grant a verifier. Reason: otherwise the agent could mint its own verification, which defeats the status gate.

## 3. Record types and creation rules

| Type | Created by | Attribution allowed | Status allowed | Lineage |
|---|---|---|---|---|
| Person | user or import | user, document | stated | none |
| Agent | system registration | system | stated | none; self-statements attributed to agent |
| Project | user only | user | stated | scope boundary; explicit membership list |
| Source | ingestion | document, media-derived, user | stated | immutable |
| Conversation | ingestion | system | stated | project, participants, time range |
| Observation | extraction | document, media-derived | stated | must cite a Source; machine-derived marked |
| Claim | reasoning or verification | agent (derived), verifier (verified) | derived, verified | may supersede a Claim via Correction |
| Hypothesis | reasoning | agent | derived | explicitly not a conclusion |
| Decision | user only | user | stated | requires explicit confirmation record |
| Commitment | user only | user | stated | never inferred from assistant text |
| Correction | user, or reasoning-proposed and user-accepted | user | stated | original preserved; supersedes link |
| Verification | verifier actor | verifier | stated | names the Claim it checks |
| Deletion | user | user | stated | cascades; emits receipt |
| Summary | derived | system | derived | projection, never authoritative, never injected whole |
| RecallReceipt | system | system | stated | no secrets |

Enforcement: `put()` rejects any record whose `(record_type, attribution, epistemic_status)` is not in this table. Assistant text can never produce a Decision or Commitment: attribution `agent` is refused for those types.

## 4. Migrations

- Schema version stored in the database. Migrations are numbered, forward-only, and idempotent.
- Migration 2 adds the Phase 2 columns (`attribution`, `schema_version`, `effective_at` if absent) and backfills existing Phase 1 rows with `attribution = system` and a recorded backfill journal entry.
- A failed migration leaves the database at the prior version (one transaction per migration).
- Tests: migrate from a Phase 1 fixture; re-run is a no-op; failure mid-migration is rolled back.

## 5. Classifier

- Input: a record's string fields (D2-5 set). Output: `scope_tag` (a scope name, or `none`) and `confidence` (advisory only).
- Decision: a record with a non-`none` tag is written to that tag's scope if the writer holds it; otherwise refused.
- Untagged record matching a scope-bound category (per the scope's markers): refused.
- Classifier errors and timeouts: fail closed (refuse the write), with a generic denial.

## 6. Tests (must exist and pass before Phase 2 review)

Status gate
- Direct `put(epistemic_status="verified")` by a non-verifier is refused.
- Verifier with a matching Verification record may create a verified Claim.
- Verification naming a different Claim does not promote.

Attribution
- Agent-attributed Decision refused.
- Agent-attributed Commitment refused.
- Agent-attributed Claim allowed only as `derived`.
- Document-attributed Observation without a Source reference refused.

Lineage
- Correction preserves the original; supersedes link set; original still readable in its own scope.
- Correction cannot change scope (Phase 1 N10 still holds).
- Deletion cascade reaches Summary projections derived from the deleted record.

Classifier (fail closed)
- Untagged ACS-category content refused for a memory-providers writer.
- Classifier exception refuses the write, with a generic denial.
- Classifier output cannot grant a scope the writer does not hold.

R1 closure
- ACS marker in `provenance` refused under memory-providers.
- ACS marker in `source_ids` refused under memory-providers.
- ACS marker in `record_type` refused under memory-providers.

Plan §5 regression targets
- Memory-provider query returns zero ACS-project records.
- Positive same-project recall returns the relevant records.
- Unknown scope denied.
- Agent-minted cross-scope request refused.
- Cross-scope read does not authorize delete.
- Identity-sourced ACS fact excluded from unrelated queries.

Migration
- Phase 1 fixture migrates; second run is a no-op; mid-migration failure rolls back.

## 7. Exit criteria

- All §6 tests pass; no test is vacuous (each asserts behaviour that would fail on the Phase 1 code, where the rule is new).
- Fresh independent adversarial review (Codex, per standing rule) returns APPROVE or APPROVE_WITH_RESIDUALS with no gate-class findings.
- Residuals recorded in the ADR.

## 8. Open items

None blocking Phase 2 implementation.

- OCR in alpha: resolved (accepted by the user, Oct 25). OCR output enters through the Phase 3 ingestion path as machine-derived Observations.
- Classifier: resolved as D2-8.
- Verifier registration: resolved as D2-9.

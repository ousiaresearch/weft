# Weft Architecture

**Phase:** 0 — Capability study (deliverable)
**Status:** Draft, informed by the capability study. Subject to adversarial review.
**Constraint:** No AGPL or Sustainable-Use-License component may be copied, linked, or bundled. Everything below is to be independently implemented.

## Principle

> Rules decide what a model may consult; reasoning decides what matters within that boundary.

Deterministic scope and permission checks run **before** any relevance ranking or model call. A
model may discover useful connections; it may never grant itself access to out-of-scope material.

## Layers

```
┌─────────────────────────────────────────────────────────────┐
│ Electron desktop          Hermes adapter       (Phase 6)    │
│ inspect / correct / test-recall / configure    shadow mode  │
├─────────────────────────────────────────────────────────────┤
│ Reasoning loop  (Phase 4)                                   │
│ extract → retrieve → hypothesize → contradiction-check →    │
│ conclude (with citations) → synthesize (or abstain)         │
├─────────────────────────────────────────────────────────────┤
│ Retrieval  (Phase 3)                                        │
│ scope resolve → candidate scan → rank → tiered load         │
│         (L0 abstract → L1 overview → L2 source)             │
├─────────────────────────────────────────────────────────────┤
│ Ingestion  (Phase 3)                                        │
│ conversations · documents · media(transcribe/describe)      │
│ idempotent · durable jobs · recall receipts                 │
├─────────────────────────────────────────────────────────────┤
│ Knowledge model  (Phase 2)                                  │
│ Person · Agent · Project · Source · Observation · Claim ·   │
│ Hypothesis · Decision · Commitment · Correction             │
├─────────────────────────────────────────────────────────────┤
│ Substrate  (Phase 1)        Scope engine  (Phase 2)         │
│ typed structured files     deterministic, fail-closed,      │
│ validated on write         runs before ranking/models       │
└─────────────────────────────────────────────────────────────┘
```

## Knowledge model

Records are distinct types, not one undifferentiated store. Every derived record carries:
source IDs, scope, epistemic status (stated / derived / verified), recorded time, effective time
where known, and supersession links.

| Record | Meaning | Authority |
|---|---|---|
| **Person** | A human. Model evolves from what they actually said. | Their statements; others' inferences are separate records |
| **Agent** | An assistant. Self-statements are attributed, never promoted to user commitments. | Its own statements |
| **Project** | A scope boundary. Knowledge does not cross it implicitly. | Explicit membership only |
| **Source** | Raw material: a message, document, or media file. Immutable. | Canonical evidence |
| **Observation** | Something extracted from a source, with location and confidence. | Derived, cites source |
| **Claim** | An assertion held to be true, with epistemic status. | Derived or verified |
| **Hypothesis** | A tentative connection under evaluation. | Explicitly not a conclusion |
| **Decision** | A chosen course, with effective time. | User-attributed; requires explicit confirmation |
| **Commitment** | A promise or obligation. | User-attributed only; never inferred from assistant text |
| **Correction** | A revision. Preserves original + new + lineage. | Supersedes; never erases |
| **RecallReceipt** | What a recall did: scope consulted, sources read, records selected, exclusions and why. | Audit trail; no secrets |

**Invariant:** retrieval can never upgrade an inference into a fact. Graph links and type
membership are not evidence.

## Scope engine (runs first)

1. Resolve the query's permitted scopes from identity + project membership + explicit request.
2. Unknown or unstated scope → **deny by default**. No silent widening.
3. Candidate records outside permitted scopes are **excluded before ranking**, not merely ranked low.
4. A small always-eligible set (identity, standing preferences, active project) may be granted
   globally, explicitly and minimally.
5. Cross-project recall requires an explicit user request in that query.
6. Every exclusion is recorded in the RecallReceipt with its reason.

**Regression target:** a query about memory providers returns zero ACS history. This is a
deterministic scope decision, not a relevance-model outcome — it must hold even with no model available.

## Retrieval and tiered loading

Candidate scan (scoped) → rank → load tiers progressively:
- **L0** (~100 tokens): abstract — enough to judge relevance.
- **L1** (~2k tokens): overview.
- **L2**: full source, opened only when needed.

The agent and the desktop UI both read the summary before opening the source. RecallReceipt records
which tier satisfied the query.

## Reasoning loop

1. **Extract** observations from source, with location and confidence.
2. **Retrieve** relevant evidence within permitted scope.
3. **Hypothesize** connections; mark explicitly tentative.
4. **Contradiction-check** against existing claims; surface conflicts, never merge silently.
5. **Conclude** as a proposed claim, citing source IDs; persist model + config version.
6. **Synthesize** an answer, or **abstain** when evidence is absent or contradictory.

External models are opt-in per query with a visible data-egress policy; local and external models
share one interface. Reasoning explanations expose evidence and decisions, not hidden chain-of-thought.

## Media ingestion

Audio → transcription, images → description, via the configured model layer. Derived records are
marked machine-derived with model, version, and confidence, and are never presented as verbatim
quotation. Original file stays canonical for evidence linking. Unsupported formats and failed
extractions are rejected or surfaced, never recorded as silent successes. Transient artifacts follow
derived-text retention and deletion policy.

## Governance

- Typed validation is **advisory at the engine** (Arsumbris lesson: diagnostics never block a save).
  Weft adds **authoritative validation at the memory write/recall boundary** — scope-invalid writes
  are rejected regardless of engine behavior.
- Mediation must **fail closed** in Weft (Arsumbris's mediation fails open when the daemon is
  unreachable). An unreachable policy engine must block the write, not allow it.
- No sandboxing exists in the reference systems; Weft treats all retrieved content as untrusted data
  that cannot authorize writes, alter scope, or act as instructions.

## Durability

One authority per record. The substrate holds the canonical typed knowledge; a journal records
accepted operations for crash recovery. **Journal vs. file commit ordering is unresolved** — Phase 1
must resolve it via fault injection before any durability claim is made. Indexes and summaries are
rebuildable derived projections; a stale index is detectable, not silently trusted.

## Phasing

See "Staged execution" in the plan: Phase 1 substrate + scope spike, Phase 2 provenance/scope tests,
Phase 3 ingestion/retrieval, Phase 4 reasoning, Phase 5 correction/privacy/portability, Phase 6
Hermes shadow integration, Phase 7 gated cutover.

## Open questions for Phase 1

1. Storage engine: SQLite + structured files, or files-only with a derived index? (Favouring SQLite for transactional integrity, files for human inspectability — needs a decision with evidence.)
2. Journal/file/index commit ordering under crash.
3. Schema language: reuse a standard (JSON Schema) or define a minimal typed language of our own?
4. Language/runtime: Rust (substrate performance) vs TypeScript (shared types with Electron UI) — trade-off between a process boundary and implementation speed.

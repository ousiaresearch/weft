# Phase 4 — Reasoning loop and model layer

Status: implementation in progress.  
Frozen review baseline: commit `45eb1cf`.

## 4.1 Goal

Add a local reasoning loop that turns raw ingested text into cited, scoped Claims, Hypotheses, and Corrections, and an answer-synthesis path that returns grounded answers or abstains. Add a model layer with a single interface for local and external models, with visible egress policy.

## 4.2 Loop

1. **Extract** observations with location and confidence.
2. **Retrieve** permitted evidence.
3. **Hypothesize** connections, marked tentative.
4. **Contradiction-check** against existing claims; surface conflicts, never merge silently.
5. **Conclude** as a proposed claim at status `derived`, citing source IDs; persist model and config version.
6. **Synthesize** an answer with citations, or **abstain** when evidence is absent or contradictory.

## 4.3 Model layer

- One interface for local and external models.
- Model selection is per-task: extraction, transcription, description, reasoning, and answer synthesis may use different models.
- **External inference is opt-in per query** with a visible data-egress policy. The user sees what will leave the machine.
- Reasoning explanations expose evidence and decision summaries. They do not expose or promise hidden chain-of-thought.

## 4.4 Interfaces

```python
class ModelLayer:
    def extract(self, text: str, task: str) -> list[Observation]: ...
    def hypothesize(self, query: str, evidence: list[Record]) -> Hypothesis: ...
    def contradict(self, claim: Claim, evidence: list[Record]) -> list[Conflict]: ...
    def conclude(self, claim: Claim, evidence: list[Record]) -> Claim: ...
    def synthesize(self, query: str, evidence: list[Record]) -> Answer: ...
```

## 4.5 Egress policy

- Default: local-only. No external calls without explicit opt-in.
- Per-query opt-in via `allow_egress=True` or equivalent.
- Receipt includes egress decision and model identifiers used.

## 4.6 Tests

- Extraction produces Observations with source citations.
- Hypothesis is marked tentative; cannot be promoted to Claim without user action.
- Contradiction-check surfaces conflicts; does not merge silently.
- Conclusion writes `derived` status, cites sources, persists model version.
- Synthesis returns cited answer or abstains with reason.
- Egress opt-in required for external model calls; receipt records egress decision.
- Fail-closed on model unavailability.

## 4.7 Exit criteria

- All 4.6 tests pass.
- No scope leakage in synthesis output.
- Citation precision and abstention correctness measured against gold set.
- Independent adversarial review passed.

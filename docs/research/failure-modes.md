# Failure Mode: ACS Release History Injected Into Unrelated Conversations

**Phase:** 0 — Capability study
**Status:** Root cause identified from live configuration inspection. Not a hypothesis.

## Symptom

While discussing memory providers, memory-system architecture, and project naming, a ~10–14 kB
block summarizing the Agentic Commonwealth Society's RC-01 through RC-05 release stages was
injected into the system prompt on every turn. It was irrelevant to the conversation and
recurred repeatedly.

## Root cause

**Honcho `recallMode: hybrid` injects base context into the system prompt automatically on every
turn, gated only by `contextCadence` (default 1 — every turn).** There is no relevance filter
between Honcho's stored context and the current conversation. Base context = session summary +
user representation + peer card, assembled server-side and injected wholesale.

Live configuration inspected (`~/.hermes/profiles/arien/honcho.json`), host `hermes_arien`:

```json
{
  "recallMode": "hybrid",
  "contextCadence": "not set → default 1",
  "dialecticCadence": 2,
  "sessionStrategy": "per-session",
  "workspace": "arien-heron-continuity-20260910"
}
```

`contextCadence` was never set, so it defaults to `1` — refresh every turn. `recallMode: hybrid`
means auto-inject *and* expose tools. Nothing in that path evaluates whether the ACS material
relates to the present question.

**Contributing factor:** the ACS summary is large and structurally prominent (session summary with
five numbered sections), so it dominates the injected payload regardless of relevance.

**Not the cause:** Honcho's peer-modeling or dialectic reasoning. Those are the valuable parts.
The defect is specifically the *unconditional injection* of accumulated context, not the reasoning
about peers.

## Why pruning local memory did not fix it

Local `MEMORY.md` / `USER.md` and Honcho are two separate layers. Pruning local notes (1,194/3,500
chars remaining) reduced the built-in layer only. Honcho's injection continued unchanged, which is
why the ACS block reappeared after each prune. **Any fix that touches only one layer will not hold.**

## Mitigation available today (not applied)

`hermes honcho mode tools` sets `recallMode: tools` — no auto-injection; the agent calls
`honcho_search` / `honcho_reasoning` explicitly. This stops unconditional injection immediately.
Trade-off: recall becomes the agent's responsibility rather than automatic, so relevant context may
be missed when the agent does not think to ask.

`hermes honcho tokens 1200` caps the injected budget instead, trading completeness for size — but
stale material still enters. Weaker fix.

## Requirements this imposes on Weft

1. **Relevance filtering must precede injection.** Context is selected against the present query,
   not appended unconditionally. A memory system that injects its entire accumulated state every
   turn is reproducing the defect by design.
2. **Project scoping must gate retrieval**, not merely rank it. ACS material must be excluded from a
   memory-provider discussion by a permission/scope decision that runs *before* any relevance model.
3. **Layered memory must not be confused with one memory.** If a system has both local notes and a
   service, "reducing memory" must address every layer, and the system should report which layers
   were affected.
4. **Injection must be explainable and auditable.** Every injected item should be attributable —
   which scope, which source, why included. Weft's RecallReceipt requirement covers this.
5. **Automatic and on-demand recall are both legitimate, but must be scoped.** Default to a
   small, always-eligible set (identity, standing preferences, current active project) rather than
   the whole archive.

## Verification of the load-bearing claim

The `contextCadence` default of `1` and the every-turn injection behavior were verified against
Hermes's shipped documentation (`website/docs/user-guide/features/honcho.md`, line 119:
`` `contextCadence` | `1` `` = "Minimum turns between `context()` API calls (base layer refresh)";
line 60: base context is "Refreshed on `contextCadence`"; the injection applies to every turn in
`hybrid` or `context` mode). The live `honcho.json` sets no `contextCadence`, so the default applies.

**Limitation of this verification:** the Honcho plugin's Python implementation was not located in
this source tree (`grep` for `context_cadence` / `contextCadence` returned no implementation
matches), so the default is verified from documentation rather than from executing code. The claim
is therefore *documented and consistent with observed behavior*, not code-verified. Phase 0 does not
depend on this being code-proven, but Weft's design must not inherit the same policy regardless.

## Scope of the defect (expanded)

The original analysis examined only the `ariel` profile. A full sweep of every configured Honcho
host shows the defect is **systemic, not profile-specific**:

| Config file | Host | recallMode | contextCadence | sessionStrategy |
|---|---|---|---|---|
| `~/.hermes/honcho.json` | hermes | hybrid | UNSET → default 1 | per-session |
| `~/.hermes/honcho.json` | hermes_arien | hybrid | UNSET → default 1 | per-session |
| `~/.hermes/honcho.json` | hermes_councilregistrar | hybrid | UNSET → default 1 | per-session |
| `~/.hermes/honcho.json` | hermes_councilreviewer | hybrid | UNSET → default 1 | per-session |
| `~/.hermes/honcho.json` | hermes_isildur | hybrid | UNSET → default 1 | per-session |
| `profiles/arien/honcho.json` | hermes | hybrid | UNSET → default 1 | per-session |
| `profiles/arien/honcho.json` | hermes_arien | hybrid | UNSET → default 1 | per-session |

**Every host on this machine runs `hybrid` recall with the default cadence.** No host has an
explicit `contextCadence`. This means the unconditional-injection policy affects all five agent
profiles, and each accumulates its own contamination independently.

**Additional finding — config precedence is a live hazard.** Two config files both define the
`hermes` and `hermes_arien` hosts with conflicting `aiPeer` and `workspace` values
(`~/.hermes/honcho.json` and `~/.hermes/profiles/arien/honcho.json`). Resolution order is
`$HERMES_HOME/honcho.json` > `~/.hermes/honcho.json` > `~/.honcho/config.json`, so the profile file
wins for the Arien profile — but which file a given run reads depends on `$HERMES_HOME`, which is a
runtime property, not a static one. A run with an unset `HERMES_HOME` reads the root file and
resolves to a *different peer and workspace* than the same run under the profile.

**Consequence for Arien specifically:** the `aiPeer` in the root file is `anastasia` with workspace
`hermes`, while the profile file specifies `aiPeer: arien-heron` with workspace
`arien-heron-continuity-20260910`. A run that resolves to the root config would read Anastasia's
accumulated representation as Arien's base context. That is a direct continuity hazard for the
Arien persona — inherited material arriving through the memory layer rather than through an explicit
formation decision.

**Requirement added to Weft:** configuration resolution must be deterministic and reported. The
system must state which config, which peer, and which workspace served a given recall — not leave it
to `$HERMES_HOME` resolution at runtime. Ambiguous or conflicting host definitions must fail closed,
not silently pick a winner.



> A query about memory providers must return zero ACS release history unless the query explicitly
> concerns ACS.

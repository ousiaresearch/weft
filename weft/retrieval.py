"""Phase 3 retrieval (spec §8, D3-2, D3-11, D3-12).

Scope is decided before any candidate is read. Ranking runs only over the
caller's readable scopes, so it cannot widen scope. Every recall leaves a
receipt holding scope names and counts, never record text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ABSTRACT_CHARS = 400  # L0 abstract (D3-11)
OVERVIEW_CHARS = 2000  # L1 overview
_TIER_ORDER = {"L0": 0, "L1": 1, "L2": 2}


@dataclass(frozen=True)
class Item:
    record_id: str
    scope: str
    text: str
    tier: str


@dataclass
class Receipt:
    kind: str = "recall"
    scope_consulted: list = field(default_factory=list)
    tier: str = "L0"
    selected: list = field(default_factory=list)
    exclusions: list = field(default_factory=list)


def _pick_tier(text: str, terms: list) -> tuple[str, str]:
    """Smallest tier whose text contains a query term; L0 when none does (D3-11)."""
    tiers = [("L0", text[:ABSTRACT_CHARS]), ("L1", text[:OVERVIEW_CHARS]), ("L2", text)]
    for name, shown in tiers:
        lowered = shown.lower()
        if any(t in lowered for t in terms):
            return name, shown
    return "L0", tiers[0][1]


def recall(sub, query: str, ctx, scope_hint: str | None = None, limit: int = 5):
    """Lexical recall over the caller's readable scopes. Returns (items, receipt)."""
    engine = sub.scope_engine
    readable = sorted(
        s for s in engine.known_scopes if engine.authorize_read(s, ctx, "").allowed
    )
    if scope_hint is not None:
        readable = [s for s in readable if s == scope_hint]
    receipt = Receipt(scope_consulted=readable)
    terms = re.findall(r"\w+", (query or "").lower())
    if not terms or not readable:
        if ctx is not None:
            sub.record_receipt("recall", readable, {"selected": 0, "excluded": 0}, ctx)
        return [], receipt

    match = " OR ".join(f'"{t}"' for t in terms)
    candidate_ids = sub.fts_candidates(match, ctx, limit)

    items: list[Item] = []
    for rid in candidate_ids:
        rec = sub.get(rid, ctx)  # gated again: defence in depth
        if rec is None:
            receipt.exclusions.append("withheld by content rule")
            continue
        text = rec.payload.get("text", "") if isinstance(rec.payload, dict) else ""
        tier, shown = _pick_tier(text, terms)
        items.append(Item(rid, rec.scope, shown, tier))
        receipt.selected.append(rid)
        if _TIER_ORDER[tier] > _TIER_ORDER[receipt.tier]:
            receipt.tier = tier

    if ctx is not None:
        sub.record_receipt("recall", readable,
                           {"selected": len(items), "excluded": len(receipt.exclusions)}, ctx)
    return items, receipt

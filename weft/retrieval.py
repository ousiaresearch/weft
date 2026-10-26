"""Phase 3 retrieval (spec §8, D3-2, D3-11, D3-12).

Scope is decided before any candidate is read. Ranking runs only over the
caller's readable scopes, so it cannot widen scope. Every recall leaves a
receipt holding scope names and counts, never record text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ABSTRACT_CHARS = 400  # L0 abstract (D3-11)


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
        sub.record_receipt("recall", readable, {"selected": 0, "excluded": 0})
        return [], receipt

    match = " OR ".join(f'"{t}"' for t in terms)
    candidate_ids = sub.fts_candidates(match, readable, limit)

    items: list[Item] = []
    for rid in candidate_ids:
        rec = sub.get(rid, ctx)  # gated again: defence in depth
        if rec is None:
            receipt.exclusions.append("withheld by content rule")
            continue
        text = rec.payload.get("text", "") if isinstance(rec.payload, dict) else ""
        items.append(Item(rid, rec.scope, text[:ABSTRACT_CHARS], "L0"))
        receipt.selected.append(rid)

    sub.record_receipt("recall", readable,
                       {"selected": len(items), "excluded": len(receipt.exclusions)})
    return items, receipt

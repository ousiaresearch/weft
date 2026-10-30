"""Phase 3 retrieval (spec §8, D3-1, D3-2, D3-11, D3-12).

Scope is decided before any candidate is read: only the caller's readable scopes
(optionally narrowed by scope_hint) are queried, lexically and by vector. Every
candidate is checked again by the read gate. Every recall leaves a receipt holding
scope names and counts, never record text.

Modes: "lexical" (FTS5), "vector" (sqlite-vec, needs an embedder), and "hybrid"
(reciprocal-rank fusion of both). D3-1: vector is a default only after the recall
gate passes (see eval/recall_gate.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from weft.entity_link import entity_boost

ABSTRACT_CHARS = 400  # L0 abstract (D3-11)
OVERVIEW_CHARS = 2000  # L1 overview
_TIER_ORDER = {"L0": 0, "L1": 1, "L2": 2}
MODES = ("lexical", "vector", "hybrid")
RRF_K = 60  # standard reciprocal-rank-fusion constant
# Function words must not generate lexical matches (found by the gate's paraphrase split:
# "the" alone pulled unrelated notes above the vector results during fusion).
STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have", "how", "i",
    "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "was", "what", "when",
    "where", "which", "who", "why", "with", "you", "your",
})


@dataclass(frozen=True)
class Item:
    record_id: str
    scope: str
    text: str
    tier: str
    derived: bool = False  # R-P3-12: machine-derived text (OCR, transcript, description)

    def render(self) -> str:
        """The only form recalled text may take when handed back to a model (D3-5).

        Wrapped in the recall fence so re-ingestion strips it instead of storing it as
        user speech (review G-P3-4). Tag tokens inside the text are escaped (G-P3-8).
        Machine-derived text carries a label so it is never presented as verbatim (D3-6).
        """
        safe = re.sub(r"<(/?weft-recall)", r"&lt;\1", self.text, flags=re.IGNORECASE)
        label = ' derived="machine"' if self.derived else ""
        return f'<weft-recall scope="{self.scope}"{label}>{safe}</weft-recall>'


@dataclass
class Receipt:
    kind: str = "recall"
    scope_consulted: list = field(default_factory=list)
    tier: str = "L0"
    selected: list = field(default_factory=list)
    exclusions: list = field(default_factory=list)
    mode: str = "lexical"


def _pick_tier(text: str, terms: list) -> tuple[str, str]:
    """Smallest tier whose text contains a query term; L0 when none does (D3-11)."""
    tiers = [("L0", text[:ABSTRACT_CHARS]), ("L1", text[:OVERVIEW_CHARS]), ("L2", text)]
    for name, shown in tiers:
        lowered = shown.lower()
        if any(t in lowered for t in terms):
            return name, shown
    return "L0", tiers[0][1]


def _fuse(ranked_lists: list, limit: int) -> list:
    """Reciprocal-rank fusion: each list contributes 1 / (RRF_K + rank)."""
    scores: dict = {}
    for ranked in ranked_lists:
        for rank, rid in enumerate(ranked):
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (RRF_K + rank + 1)
    order = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [rid for rid, _ in order[:limit]]


def _cosine(a, b):
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _rerank(query: str, items, embedder, top_k: int):
    """Re-score fused candidates by query–candidate cosine similarity."""
    if embedder is None or not items:
        return items[:top_k]
    try:
        q_emb = embedder.embed_query(query)
        texts = [it.text for it in items]
        try:
            cand_embs = embedder.embed_documents(texts)
        except AttributeError:
            cand_embs = [embedder.embed_document(t) for t in texts]
        if len(cand_embs) != len(items):
            return items[:top_k]
        scored = [(_cosine(q_emb, emb), it) for it, emb in zip(items, cand_embs)]
        scored.sort(key=lambda x: (-x[0], x[1].record_id))
        return [it for _, it in scored[:top_k]]
    except Exception:
        return items[:top_k]


def recall(sub, query: str, ctx, scope_hint: str | None = None, limit: int = 5,
           mode: str = "hybrid", embedder=None, rerank: bool = False):
    """Recall over the caller's readable scopes. Returns (items, receipt)."""
    if mode not in MODES:
        raise ValueError(f"unknown recall mode: {mode!r}")
    if mode != "lexical" and embedder is None:
        raise ValueError(f"mode {mode!r} needs an embedder")
    engine = sub.scope_engine
    readable = sorted(
        s for s in engine.known_scopes if engine.authorize_read(s, ctx, "").allowed
    )
    if scope_hint is not None:
        readable = [s for s in readable if s == scope_hint]
    receipt = Receipt(scope_consulted=readable, mode=mode)
    terms = re.findall(r"\w+", (query or "").lower())
    content_terms = [t for t in terms if t not in STOPWORDS]
    use_lexical = mode in ("lexical", "hybrid") and bool(content_terms)
    use_vector = mode in ("vector", "hybrid")
    if not readable or (not use_lexical and not use_vector):
        if ctx is not None:
            sub.record_receipt("recall", readable, {"selected": 0, "excluded": 0}, ctx)
        return [], receipt

    # D3-1: embedding computed outside the database transaction so long-running
    # model inference does not pin a WAL snapshot or hold the connection lock.
    query_embedding = embedder.embed_query(query) if use_vector else None
    pool = limit * 4
    # Snapshot-time consistency: completeness check, candidate selection, and
    # per-record reads must observe one view (review G-P3-7). record_receipt
    # writes its own BEGIN IMMEDIATE after this block.
    with sub._read_transaction():
        if use_lexical and not use_vector:
            for scope in readable:
                if not sub.fts_index_complete(scope, ctx):
                    raise ValueError("lexical recall requires a complete full-text index for every readable scope")
        if use_vector:
            assert embedder is not None
            for scope in readable:
                if not sub.vector_index_complete(scope, ctx, model_id=embedder.model_id):
                    raise ValueError("hybrid/vector recall requires a complete vector index for every readable scope")
        ranked = []
        if use_lexical:
            match = " OR ".join(f'"{t}"' for t in content_terms)
            ranked.append(sub.fts_candidates(match, ctx, pool, scopes=readable))
        if use_vector:
            ranked.append(sub.vector_candidates(query_embedding, ctx, pool, scopes=readable, model_id=embedder.model_id))
        candidate_ids = ranked[0] if len(ranked) == 1 else _fuse(ranked, pool)

        # Phase 12: graph boost — reorder candidates by entity overlap with query.
        try:
            graph_scores = entity_boost(sub, ctx, query, candidate_ids)
            if graph_scores:
                candidate_ids.sort(key=lambda rid: (-graph_scores.get(rid, 0.0), rid))
        except Exception:
            pass

        items: list[Item] = []
        seen_documents: set = set()  # one result per document: a note is not five paragraphs
        for rid in candidate_ids:
            if len(items) >= limit and not rerank:
                break
            if len(items) >= pool:
                break
            rec = sub.get(rid, ctx)  # gated again: defence in depth
            if rec is None:
                receipt.exclusions.append("withheld by content rule")
                continue
            if rec.record_type in ("Summary", "RecallReceipt"):
                receipt.exclusions.append("composite artifact")
                continue
            document = rec.provenance.get("document") if isinstance(rec.provenance, dict) else None
            key = document if document else f"record:{rid}"
            if key in seen_documents:
                continue
            seen_documents.add(key)
            text = rec.payload.get("text", "") if isinstance(rec.payload, dict) else ""
            tier, shown = _pick_tier(text, terms)
            derived = bool(isinstance(rec.provenance, dict) and rec.provenance.get("machine_derived"))
            items.append(Item(rid, rec.scope, shown, tier, derived))
            receipt.selected.append(rid)
            if _TIER_ORDER[tier] > _TIER_ORDER[receipt.tier]:
                receipt.tier = tier

        # Optional reranking over the full pool before truncation to limit.
        if rerank and len(items) > limit:
            items = _rerank(query, items, embedder, limit)

    # Final truncation + receipt recording happens outside the read transaction.
    selected = items[:limit]
    receipt.selected = [it.record_id for it in selected]
    if ctx is not None:
        sub.record_receipt("recall", readable,
                           {"selected": len(selected), "excluded": len(receipt.exclusions)}, ctx)
    return selected, receipt

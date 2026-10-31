"""Phase 12 — Entity extraction and linking.

Uses the local embedder to create entity links between records within a
scope. No external graph database is required; links are stored in the
SQLite entity_links table.
"""
from __future__ import annotations

import time
from typing import Any

from weft.substrate import Record, Substrate
from weft.scope import ScopeContext


def _normalize(text: str) -> str:
    """Lowercase and strip punctuation for entity matching."""
    import re
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def extract_entities(text: str) -> list[str]:
    """Return candidate entity mentions from *text*.

    Simple heuristic: capitalized words longer than 2 chars, excluding
    common interrogatives and question starters. Multi-word capitalized
    sequences are also captured.
    """
    import re
    STOP = {
        "who", "what", "when", "where", "why", "how", "which", "whom",
        "whose", "whether", "while", "after", "before", "since", "until",
        "during", "through", "about", "against", "between", "under",
        "over", "throughout", "within", "without", "again", "further",
        "then", "once", "here", "there", "today", "tomorrow", "yesterday",
    }
    entities: list[str] = []
    # Single capitalized words (len > 2), excluding stop words
    for w in re.findall(r"\b([A-Z][a-zA-Z]{2,})\b", text):
        if w.lower() not in STOP:
            entities.append(w.lower())
    # Multi-word capitalized sequences
    for m in re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", text):
        norm = _normalize(m)
        if norm and norm not in STOP:
            entities.append(norm)
    # Deduplicate preserving order
    seen: set[str] = set()
    result = []
    for ent in entities:
        if ent and ent not in seen:
            seen.add(ent)
            result.append(ent)
    return result


def link_entities(sub: Substrate, ctx: ScopeContext, scope: str, limit: int = 100) -> dict[str, int]:
    """Scan records in *scope* and create entity links.

    For each Observation/Claim, extract entities and create
    (record -> entity) links. Returns {records_scanned, links_created}.
    Fail-closed: returns {} on any error.
    """
    try:
        rows = sub._conn.execute(
            "SELECT record_id, payload FROM records WHERE scope = ? AND record_type IN ('Observation', 'Claim', 'Source')",
            (scope,),
        ).fetchall()
    except Exception:
        return {}

    now = time.time()
    links_created = 0
    records_scanned = 0
    existing = {
        (r[0], r[1], r[2])
        for r in sub._conn.execute(
            "SELECT source_id, target_id, relation FROM entity_links WHERE scope = ?", (scope,)
        ).fetchall()
    }

    try:
        for record_id, payload_json in rows[:limit]:
            payload = _load_payload(payload_json)
            text = payload.get("text", "")
            if not text:
                continue
            entities = extract_entities(text)
            records_scanned += 1
            for entity in entities:
                key = (record_id, entity, "mentions")
                if key in existing:
                    continue
                sub._conn.execute(
                    "INSERT INTO entity_links (source_id, target_id, relation, valid_from, valid_to, scope) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (record_id, entity, "mentions", now, None, scope),
                )
                existing.add(key)
                links_created += 1
    except Exception:
        pass

    try:
        sub._conn.commit()
    except Exception:
        pass

    return {"records_scanned": records_scanned, "links_created": links_created}


def _load_payload(raw: Any) -> dict[str, Any]:
    import json
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    return {}


def entity_boost(sub: Substrate, ctx: ScopeContext, query: str, candidate_ids: list[str], boost: float = 0.1, min_doc_freq: int = 3) -> dict[str, float]:
    """Score candidate records by how many query entities they mention.

    Uses the precomputed entity_links table instead of re-parsing text.
    Returns {record_id: boost_score}. Scores are additive fractions of *boost*.
    Fail-closed: returns {} on any error.
    """
    try:
        query_entities = set(extract_entities(query))
        if not query_entities:
            return {}
        # Filter entities to those appearing in at least min_doc_freq records
        # to reduce noise from common words extracted from sentence-initial text.
        placeholders = ",".join("?" for _ in query_entities)
        freq_rows = sub._conn.execute(
            f"SELECT target_id, COUNT(DISTINCT source_id) FROM entity_links "
            f"WHERE target_id IN ({placeholders}) "
            f"GROUP BY target_id",
            tuple(query_entities),
        ).fetchall()
        freq = {row[0]: row[1] for row in freq_rows}
        kept = {e for e, cnt in freq.items() if cnt >= min_doc_freq}
        if not kept:
            return {}
        src_placeholders = ",".join("?" for _ in candidate_ids)
        tgt_placeholders = ",".join("?" for _ in kept)
        rows = sub._conn.execute(
            f"SELECT source_id, target_id FROM entity_links "
            f"WHERE source_id IN ({src_placeholders}) "
            f"AND target_id IN ({tgt_placeholders})",
            tuple(candidate_ids) + tuple(kept),
        ).fetchall()
        scores: dict[str, float] = {}
        for source_id, target_id in rows:
            if source_id in scores:
                scores[source_id] += boost / len(kept)
            else:
                scores[source_id] = boost / len(kept)
        return scores
    except Exception:
        return {}

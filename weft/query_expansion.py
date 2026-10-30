"""Query expansion for improved recall coverage.

Generates alternative phrasings of a query using an LLM, runs recall
for each, and fuses results with RRF. This helps match paraphrases
and synonym uses in the corpus.
"""
from __future__ import annotations

import json
import logging
from typing import Sequence

logger = logging.getLogger(__name__)


def expand_query(token_getter, query: str, n: int = 3) -> list[str]:
    """Generate alternative phrasings for a query.

    Returns [original, alt1, alt2, ...] up to n+1 total.
    Fail-closed: returns [query] on any error.
    """
    token = token_getter()
    if not token:
        return [query]

    prompt = (
        f"Generate {n} alternative ways to ask the following question, "
        f"preserving the same meaning but using different wording, synonyms, "
        f"or paraphrases. Return ONLY a JSON array of strings.\n\n"
        f"Original: {query}\n\n"
        f"Alternatives:"
    )
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://tokenharbor.ai/v1/chat/completions",
            data=json.dumps({
                "model": "deepseek-v4.1-flash",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 150,
                "temperature": 0.7,
            }).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
        text = result["choices"][0]["message"]["content"].strip()
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            alts = json.loads(text[start:end])
            if isinstance(alts, list):
                seen = {query}
                out = [query]
                for alt in alts:
                    if isinstance(alt, str) and alt.strip() and alt.strip() not in seen:
                        out.append(alt.strip())
                        seen.add(alt.strip())
                    if len(out) >= n + 1:
                        break
                return out
    except Exception as e:
        logger.warning("Query expansion failed: %s", e)
    return [query]


def _fuse_rrf(ranked_lists: Sequence[list], pool: int, k: int = 60) -> list:
    """Reciprocal Rank Fusion over multiple ranked ID lists."""
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for pos, item in enumerate(ranked):
            rid = getattr(item, "record_id", item) if hasattr(item, "record_id") else item
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (k + pos + 1)
    ordered = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    return [rid for rid, _ in ordered[:pool]]

"""Cross-encoder reranking for Weft recall.

Uses a SOTA cross-encoder to re-score candidate records after initial
lexical+vector fusion, before final truncation to limit.
"""
from __future__ import annotations

import logging
from typing import Sequence

logger = logging.getLogger(__name__)


class CrossEncoderReranker:
    """Reranks recall candidates with a cross-encoder model.

    This is intentionally scoped: only the top pool from RRF is rescored,
    not the full corpus, to keep latency acceptable.
    """

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        self.model_name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder  # noqa: F401
                self._model = CrossEncoder(self.model_name)
            except Exception as e:
                logger.warning("Failed to load cross-encoder %s: %s", self.model_name, e)
                self._model = "error"

    def rerank(self, query: str, items: Sequence, top_k: int = 10) -> list:
        """Re-score items by query-candidate relevance, return top_k."""
        if not items:
            return []
        try:
            self._load()
            if self._model in (None, "error"):
                return list(items)[:top_k]

            pairs = [(query, item.selected_texts[0] if item.selected_texts else "") for item in items]
            scores = self._model.predict(pairs, show_progress_bar=False)
            scored = sorted(zip(scores, items), key=lambda x: (-x[0], x[1].record_id))
            return [item for _, item in scored[:top_k]]
        except Exception as e:
            logger.warning("Cross-encoder rerank failed: %s", e)
            return list(items)[:top_k]

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

    _instance: CrossEncoderReranker | None = None

    def __new__(cls, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.model_name = model_name
            cls._instance._model = None
        return cls._instance

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder  # noqa: F401
                self._model = CrossEncoder(self.model_name)
            except Exception as e:
                logger.warning("Failed to load cross-encoder %s: %s", self.model_name, e)
                self._model = "error"

    def rerank(self, query: str, items: Sequence, top_k: int = 10) -> list:
        """Re-score items by query-candidate relevance, return top_k.

        Summary records are excluded from cross-encoder scoring because
        small cross-encoders (MiniLM family) assign artificially high
        relevance to comprehensive summaries over specific factual turns.
        Summaries are appended after reranked non-summaries so factual
        evidence can surface without summary contamination.
        """
        if not items:
            return []
        try:
            self._load()
            if self._model in (None, "error"):
                return list(items)[:top_k]

            factual = [it for it in items if not getattr(it, "is_summary", False)]
            summaries = [it for it in items if getattr(it, "is_summary", False)]

            if not factual:
                return list(items)[:top_k]

            pairs = [(query, item.text) for item in factual]
            scores = self._model.predict(pairs, show_progress_bar=False)
            scored = sorted(
                zip(scores, factual),
                key=lambda x: (-x[0], x[1].record_id),
            )
            reranked = [item for _, item in scored[:top_k]]

            # Append summaries after factual items, preserving order.
            remaining = top_k - len(reranked)
            if remaining > 0:
                reranked.extend(summaries[:remaining])
            return reranked[:top_k]
        except Exception as e:
            logger.warning("Cross-encoder rerank failed: %s", e)
            return list(items)[:top_k]

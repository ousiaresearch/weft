"""Tests for cross-encoder reranking."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from weft.reranking import CrossEncoderReranker


class DummyItem:
    def __init__(self, record_id, text):
        self.record_id = record_id
        self.selected_texts = [text]


def test_reranker_empty_items():
    rr = CrossEncoderReranker.__new__(CrossEncoderReranker)
    rr._model = MagicMock()
    assert rr.rerank("query", []) == []


def test_reranker_fallback_on_error(monkeypatch):
    rr = CrossEncoderReranker.__new__(CrossEncoderReranker)
    rr._model = "error"
    items = [DummyItem("a", "text a"), DummyItem("b", "text b")]
    result = rr.rerank("query", items, top_k=1)
    assert result == items[:1]


def test_reranker_sorts_by_score():
    rr = CrossEncoderReranker.__new__(CrossEncoderReranker)
    rr._model = MagicMock()
    rr._model.predict.return_value = [0.2, 0.8]
    items = [DummyItem("a", "text a"), DummyItem("b", "text b")]
    result = rr.rerank("query", items, top_k=2)
    assert [it.record_id for it in result] == ["b", "a"]

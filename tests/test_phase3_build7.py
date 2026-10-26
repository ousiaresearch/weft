"""Stopword defect in lexical recall (found by the gate's paraphrase split). Tests first."""

import pytest

from weft.ingest import Ingestor
from weft.retrieval import recall
from weft.scope import ScopeContext, ScopeEngine
from weft.substrate import Substrate

KNOWN = {"acs-project", "memory-providers"}
USER = ScopeContext(granted_scopes=frozenset(KNOWN), user_authored=True, actor="johann")
MEM = ScopeContext(granted_scopes=frozenset({"memory-providers"}), actor="arien")


@pytest.fixture
def sub(tmp_path):
    s = Substrate(tmp_path / "weft.db", scope_engine=ScopeEngine(known_scopes=KNOWN, markers={}))
    yield s
    s.close()


def test_stopwords_alone_do_not_produce_lexical_matches(sub):
    Ingestor(sub).ingest_turn("the harbour bells are quiet", "user", USER, "memory-providers", "c1")
    items, _ = recall(sub, "the sky is blue", MEM, mode="lexical")
    assert items == []


def test_content_words_still_match(sub):
    Ingestor(sub).ingest_turn("the harbour bells are quiet", "user", USER, "memory-providers", "c1")
    items, _ = recall(sub, "is the harbour quiet", MEM, mode="lexical")
    assert items and "harbour" in items[0].text

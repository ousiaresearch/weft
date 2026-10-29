import pytest

from weft.ingest import Ingestor
from weft.retrieval import recall
from weft.scope import ScopeContext, ScopeEngine
from weft.substrate import Substrate


def test_lexical_recall_refuses_missing_index_instead_of_silent_empty(tmp_path):
    sub = Substrate(tmp_path / 'db', ScopeEngine(known_scopes={'coast'}))
    ctx = ScopeContext(granted_scopes=frozenset({'coast'}))
    try:
        sid = Ingestor(sub).ingest_document('a.txt', b'lighthouse coastal beacon', ctx, 'coast', 'user')
        assert isinstance(sid, str)
        items, _ = recall(sub, 'lighthouse', ctx, mode='lexical')
        assert items
        sub._conn.execute('DELETE FROM fts WHERE record_id=?', (sid,))
        with pytest.raises(ValueError, match='complete'):
            recall(sub, 'lighthouse', ctx, mode='lexical')
    finally:
        sub.close()

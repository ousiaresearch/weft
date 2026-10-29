"""Tests for Phase 4: reasoning loop and model layer."""

import json

import pytest

from weft.models import EgressPolicyError, ModelLayer, ModelLayerError, ModelUnavailable
from weft.reasoning import Abstention, Claim, Conflict, Hypothesis, ReasoningError, contradict, conclude, extract_observations, hypothesize, synthesize
from weft.scope import ScopeContext, ScopeEngine
from weft.substrate import Record, Substrate


@pytest.fixture
def sub(tmp_path):
    engine = ScopeEngine(known_scopes={"s"})
    ctx = ScopeContext(granted_scopes=frozenset({"s"}), user_authored=True, actor="local-user")
    sub = Substrate(tmp_path / "db", engine)
    try:
        yield sub, ctx
    finally:
        sub.close()


def test_extract_observations_returns_observation_records(sub):
    class FakeModel:
        def __call__(self, prompt):
            return json.dumps([{"text": "obs1", "location": "loc", "confidence": 0.9}])
    layer = ModelLayer(local_factory=lambda _: FakeModel())
    sub_obj, ctx = sub
    src = Record(record_id="src-1", record_type="Source", scope="s", payload={"text": "text"}, attribution="document", source_ids=[])
    sub_obj.put(src, ctx)
    obs = extract_observations(sub_obj, ctx, "src-1", "text", layer)
    assert len(obs) == 1
    assert obs[0].record_type == "Observation"
    assert obs[0].scope == "s"
    assert obs[0].source_ids == ["src-1"]


def test_extract_observations_uses_source_scope(sub):
    class FakeModel:
        def __call__(self, prompt):
            return json.dumps([{"text": "obs", "location": "loc", "confidence": 0.5}])
    layer = ModelLayer(local_factory=lambda _: FakeModel())
    sub_obj, ctx = sub
    src = Record(record_id="src-2", record_type="Source", scope="s", payload={"text": "t"}, attribution="document", source_ids=[])
    sub_obj.put(src, ctx)
    obs = extract_observations(sub_obj, ctx, "src-2", "t", layer)
    assert obs[0].scope == "s"


def test_hypothesize_returns_tentative_hypothesis(sub):
    class FakeModel:
        def __call__(self, prompt):
            return json.dumps({"hypothesis": "test hypothesis"})
    layer = ModelLayer(local_factory=lambda _: FakeModel())
    sub_obj, ctx = sub
    sub_obj.put(Record(record_id="r1", record_type="Source", scope="s", payload={"text": "lighthouse evidence"}, attribution="document", source_ids=[]), ctx)
    sub_obj.rebuild_fts("s", ctx)
    h = hypothesize(sub_obj, ctx, "lighthouse", layer)
    assert h.text == "test hypothesis"
    assert h.evidence_ids == ["r1"]


def test_hypothesize_abstains_without_evidence(sub):
    layer = ModelLayer()
    sub_obj, ctx = sub
    with pytest.raises(Abstention, match="no evidence"):
        hypothesize(sub_obj, ctx, "query", layer)


def test_contradict_returns_conflicts(sub):
    class FakeModel:
        def __call__(self, prompt):
            return json.dumps([{"claim_id": "c1", "conflict_with": "r1", "reason": "opposite"}])
    layer = ModelLayer(local_factory=lambda _: FakeModel())
    sub_obj, ctx = sub
    claim = Record(record_id="c1", record_type="Claim", scope="s", payload={"text": "claim text"}, attribution="agent", source_ids=["r1"], epistemic_status="derived")
    sub_obj.put(claim, ctx)
    conflicts = contradict(sub_obj, ctx, claim, layer)
    assert len(conflicts) == 1
    assert conflicts[0].conflict_with == "r1"


def test_contradict_requires_derived_claim(sub):
    layer = ModelLayer()
    sub_obj, ctx = sub
    # contradict checks status before touching the substrate, so the claim
    # does not need to be written.
    claim = Record(record_id="c1", record_type="Claim", scope="s", payload={"text": "x"}, attribution="agent", source_ids=[], epistemic_status="stated")
    with pytest.raises(ReasoningError, match="derived claim"):
        contradict(sub_obj, ctx, claim, layer)


def test_conclude_promotes_hypothesis_without_write(sub):
    layer = ModelLayer()
    sub_obj, ctx = sub
    claim = Record(record_id="c1", record_type="Hypothesis", scope="s", payload={"text": "hyp"}, attribution="agent", source_ids=["r1"], epistemic_status="derived")
    sub_obj.put(claim, ctx)
    concluded = conclude(sub_obj, ctx, claim, layer, allow_write=False)
    assert isinstance(concluded, Claim)
    assert concluded.text == "hyp"


def test_conclude_abstains_without_evidence(sub):
    layer = ModelLayer()
    sub_obj, ctx = sub
    claim = Record(record_id="c1", record_type="Hypothesis", scope="s", payload={"text": "hyp"}, attribution="agent", source_ids=[], epistemic_status="derived")
    sub_obj.put(claim, ctx)
    with pytest.raises(ReasoningError, match="evidence citations"):
        conclude(sub_obj, ctx, claim, layer)


def test_synthesize_returns_cited_answer(sub):
    class FakeModel:
        def __call__(self, prompt):
            return "Answer is [r1] because evidence."
    layer = ModelLayer(local_factory=lambda _: FakeModel())
    sub_obj, ctx = sub
    sub_obj.put(Record(record_id="r1", record_type="Source", scope="s", payload={"text": "lighthouse evidence"}, attribution="document", source_ids=[]), ctx)
    sub_obj.rebuild_fts("s", ctx)
    answer = synthesize(sub_obj, ctx, "lighthouse", layer)
    assert "r1" in answer


def test_synthesize_abstains_without_evidence(sub):
    layer = ModelLayer()
    sub_obj, ctx = sub
    with pytest.raises(Abstention, match="no scoped evidence"):
        synthesize(sub_obj, ctx, "query", layer)


def test_synthesize_abstains_without_citations(sub):
    class FakeModel:
        def __call__(self, prompt):
            return "no citations here"
    layer = ModelLayer(local_factory=lambda _: FakeModel())
    sub_obj, ctx = sub
    sub_obj.put(Record(record_id="r1", record_type="Source", scope="s", payload={"text": "lighthouse evidence"}, attribution="document", source_ids=[]), ctx)
    sub_obj.rebuild_fts("s", ctx)
    with pytest.raises(Abstention, match="no citations"):
        synthesize(sub_obj, ctx, "lighthouse", layer)


def test_model_layer_requires_egress_opt_in_for_external():
    layer = ModelLayer(egress_allowed=False)
    with pytest.raises(EgressPolicyError):
        layer.run("m", "prompt", "task", base_url="http://x", api_key="k")


def test_model_layer_allows_external_when_opted_in():
    class FakeExternal:
        def __call__(self, prompt, api_key):
            return "external answer"
    layer = ModelLayer(external_factory=lambda m, u: FakeExternal(), egress_allowed=True)
    result = layer.run("m", "p", "task", base_url="http://x", api_key="k")
    assert result.text == "external answer"
    assert len(layer.calls) == 1
    assert layer.calls[0].egress is True
    assert layer.calls[0].provider == "external"


def test_model_layer_falls_back_to_local_without_credentials():
    class FakeLocal:
        def __call__(self, prompt):
            return "local answer"
    layer = ModelLayer(local_factory=lambda _: FakeLocal())
    result = layer.run("m", "p", "task")
    assert result.text == "local answer"
    assert result.model.provider == "local"


def test_model_layer_records_failed_local_call():
    def bad(prompt):
        raise RuntimeError("model down")
    layer = ModelLayer(local_factory=lambda _: bad)
    with pytest.raises(ModelLayerError, match="local model failed"):
        layer.run("m", "p", "task")
    assert len(layer.calls) == 1
    assert layer.calls[0].error is not None

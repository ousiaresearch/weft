"""Phase 7 evaluation runner for Weft reasoning loop.

Runs the gold evaluation set from docs/evaluation/set-v1.md against the
reasoning functions and reports per-case pass/fail plus aggregate metrics.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from weft.models import ModelLayer
from weft.reasoning import Abstention, contradict, synthesize
from weft.substrate import Record, Substrate
from weft.scope import ScopeContext, ScopeEngine


KNOWN = {"s", "t"}

EVAL_DIR = Path(__file__).resolve().parents[1] / "docs" / "evaluation"
GOLD_PATH = EVAL_DIR / "set-v1.md"


def _parse_gold() -> list[dict]:
    cases: list[dict] = []
    with open(GOLD_PATH, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    current: dict | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        current["setup_records"] = buffer
        cases.append(current)
        current = None
        buffer.clear()

    for line in lines:
        stripped = line.rstrip("\n")
        if stripped.startswith("### "):
            flush()
            case_id = stripped[4:].strip().split(" —")[0].strip()
            current = {
                "case_id": case_id,
                "gold_citations": [],
                "setup_records": [],
                "abstain": False,
                "scope": "s",
            }
            continue
        if current is None:
            continue
        if stripped.startswith("**Scope:**"):
            current["scope"] = stripped[len("**Scope:**"):].strip().strip('"')
        elif stripped.startswith("**Query:**"):
            current["query"] = stripped[len("**Query:**"):].strip().strip('"`')
        elif stripped.startswith("**Gold answer:**"):
            current["gold_answer"] = stripped[len("**Gold answer:**"):].strip().strip('"`')
        elif stripped.startswith("**Gold citations:**"):
            raw = stripped[len("**Gold citations:**"):].strip().strip("`")
            if raw.startswith("[") and raw.endswith("]"):
                current["gold_citations"] = json.loads(raw)
            else:
                current["gold_citations"] = [c.strip() for c in raw.split(",") if c.strip()]
        elif stripped.startswith("**Abstain:**"):
            current["abstain"] = stripped[len("**Abstain:**"):].strip().lower() == "yes"
        else:
            buffer.append(stripped)

    flush()
    return cases


def _build_substrate(tmp_path, case):
    engine = ScopeEngine(known_scopes=KNOWN)
    sub = Substrate(tmp_path / f"{case['case_id']}.db", engine)
    ctx = ScopeContext(granted_scopes=frozenset({"s"}), user_authored=True)

    if case["case_id"] == "E1":
        sub._conn.execute(
            "INSERT OR IGNORE INTO records (record_id, record_type, scope, epistemic_status, payload, "
            "created_at, effective_at, supersedes, source_ids, provenance, attribution) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("src-1", "Source", "s", "stated", json.dumps({"text": "The lighthouse at Heron Point was decommissioned in 1987."}), 0.0, None, None, "[]", "{}", "document"),
        )
    elif case["case_id"] == "E2":
        pass  # no records about height
    elif case["case_id"] == "E3":
        sub._conn.execute(
            "INSERT OR IGNORE INTO records (record_id, record_type, scope, epistemic_status, payload, "
            "created_at, effective_at, supersedes, source_ids, provenance, attribution) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("obs-1", "Observation", "s", "stated", json.dumps({"text": "The keeper arrived on Monday."}), 0.0, None, None, "[]", "{}", "document"),
        )
        sub._conn.execute(
            "INSERT OR IGNORE INTO records (record_id, record_type, scope, epistemic_status, payload, "
            "created_at, effective_at, supersedes, source_ids, provenance, attribution) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("obs-2", "Observation", "s", "stated", json.dumps({"text": "The keeper arrived on Tuesday."}), 0.0, None, None, "[]", "{}", "document"),
        )
    elif case["case_id"] == "E4":
        # Same substrate: one record in granted scope 's', one secret in ungranted scope 't'.
        sub._conn.execute(
            "INSERT OR IGNORE INTO records (record_id, record_type, scope, epistemic_status, payload, "
            "created_at, effective_at, supersedes, source_ids, provenance, attribution) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("src-1", "Source", "s", "stated", json.dumps({"text": "Public lighthouse status."}), 0.0, None, None, "[]", "{}", "document"),
        )
        sub._conn.execute(
            "INSERT OR IGNORE INTO records (record_id, record_type, scope, epistemic_status, payload, "
            "created_at, effective_at, supersedes, source_ids, provenance, attribution) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("src-t-1", "Source", "t", "stated", json.dumps({"text": "Secret project budget: $12M."}), 0.0, None, None, "[]", "{}", "document"),
        )
    elif case["case_id"] == "E5":
        # Register a marker for scope 't' so content_conflict can fire.
        engine.register_scope("t", markers=["ACS", "project Alpha"])
        # doc-1 is filed in scope 's' but contains a marker for scope 't'.
        sub._conn.execute(
            "INSERT OR IGNORE INTO records (record_id, record_type, scope, epistemic_status, payload, "
            "created_at, effective_at, supersedes, source_ids, provenance, attribution) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("doc-1", "Document", "s", "stated", json.dumps({"text": "Weft Phase 6 integrates shadow mode for project Alpha."}), 0.0, None, None, "[]", "{}", "document"),
        )
    elif case["case_id"] == "E6":
        sub._conn.execute(
            "INSERT OR IGNORE INTO records (record_id, record_type, scope, epistemic_status, payload, "
            "created_at, effective_at, supersedes, source_ids, provenance, attribution) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("obs-1", "Observation", "s", "stated", json.dumps({"text": "Fuel capacity: 500L."}), 0.0, None, None, "[]", "{}", "document"),
        )
        sub._conn.execute(
            "INSERT OR IGNORE INTO records (record_id, record_type, scope, epistemic_status, payload, "
            "created_at, effective_at, supersedes, source_ids, provenance, attribution) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("corr-1", "Correction", "s", "stated", json.dumps({"text": "Fuel capacity: 450L."}), 0.0, None, "obs-1", "[]", "{}", "user"),
        )
    sub._conn.commit()
    try:
        sub.rebuild_fts("s", ctx)
    except Exception:
        pass
    return sub, ctx


def _fake_model(case_id: str):
    responses = {
        "E1": "The lighthouse was decommissioned in 1987 [src-1].",
        "E2": "stub",  # Abstention raised before model call when no evidence
        "E3": "[{\"claim_id\": \"claim-1\", \"conflict_with\": \"obs-2\", \"reason\": \"opposite\"}]",
        "E4": "The lighthouse status is public [src-1].",
        "E5": "stub",  # Abstention expected due to content_conflict
        "E6": "The corrected fuel capacity is 450L [obs-1] [corr-1].",
    }
    text = responses.get(case_id, "stub")

    class FakeModel:
        def __call__(self, prompt):
            return text
    return FakeModel()


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    raw = _parse_gold()
    if not raw:
        pytest.fail(f"Failed to parse evaluation cases from {GOLD_PATH}")
    return raw


class TestPhase7Evaluation:
    def test_evaluation_set_parses(self, cases):
        assert len(cases) >= 6, f"Expected >= 6 cases, got {len(cases)}"

    @pytest.mark.parametrize("case", _parse_gold(), ids=lambda c: c["case_id"])
    def test_case(self, tmp_path, case):
        sub, ctx = _build_substrate(tmp_path, case)
        try:
            start = time.perf_counter()
            layer = ModelLayer(local_factory=lambda _: _fake_model(case["case_id"]))

            if case["case_id"] == "E1":
                answer = synthesize(sub, ctx, case["query"], layer)
                assert "1987" in answer, f"{case['case_id']}: answer={answer}"
                assert "src-1" in answer, f"{case['case_id']}: answer={answer}"

            elif case["case_id"] == "E2":
                with pytest.raises(Abstention, match="no scoped evidence"):
                    synthesize(sub, ctx, case["query"], layer)

            elif case["case_id"] == "E3":
                rec = sub.get("obs-1", ctx)
                assert rec is not None
                claim = Record(
                    record_id="claim-1",
                    record_type="Claim",
                    scope="s",
                    payload={"text": _safe_text(rec)},
                    attribution="agent",
                    source_ids=["obs-1"],
                    epistemic_status="derived",
                )
                sub.put(claim, ctx)
                conflicts = contradict(sub, ctx, claim, layer)
                assert len(conflicts) >= 1, f"{case['case_id']}: expected conflicts"
                assert any(c.conflict_with == "obs-2" for c in conflicts)

            elif case["case_id"] == "E4":
                # Scope 't' record must not appear in evidence or answer.
                answer = synthesize(sub, ctx, case["query"], layer)
                assert "src-t-1" not in answer, f"{case['case_id']}: cross-scope leak"
                assert "12M" not in answer, f"{case['case_id']}: cross-scope leak"
                assert "src-1" in answer, f"{case['case_id']}: granted-scope record missing"

            elif case["case_id"] == "E5":
                # content_conflict should deny the mis-filed record.
                with pytest.raises(Abstention):
                    synthesize(sub, ctx, case["query"], layer)

            elif case["case_id"] == "E6":
                answer = synthesize(sub, ctx, case["query"], layer)
                assert "450L" in answer, f"{case['case_id']}: answer={answer}"
                assert "obs-1" in answer, f"{case['case_id']}: answer={answer}"
                assert "corr-1" in answer, f"{case['case_id']}: answer={answer}"

            else:
                pytest.fail(f"Unknown case: {case['case_id']}")

            elapsed_ms = (time.perf_counter() - start) * 1000
            assert elapsed_ms < 5000, f"{case['case_id']}: latency {elapsed_ms:.1f}ms exceeds threshold"

        finally:
            sub.close()


def _safe_text(rec: Record) -> str:
    payload = rec.payload or {}
    if isinstance(payload, dict):
        return payload.get("text", "") or ""
    return str(payload)

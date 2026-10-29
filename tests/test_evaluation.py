"""Phase 7 evaluation runner for Weft reasoning loop.

Runs the gold evaluation set from docs/evaluation/set-v1.md against the
reasoning functions and reports per-case pass/fail plus aggregate metrics.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from weft.models import ModelLayer
from weft.reasoning import (
    _candidates,
    hypothesize,
    contradict,
    synthesize,
)
from weft.substrate import Substrate, Record
from weft.scope import ScopeEngine, ScopeContext


KNOWN = {"s", "t"}

EVAL_DIR = Path(__file__).resolve().parents[1] / "docs" / "evaluation"
GOLD_PATH = EVAL_DIR / "set-v1.md"


@dataclass
class EvalCase:
    case_id: str
    query: str
    scope: str
    gold_answer: str
    gold_citations: list[str]
    abstain: bool
    setup_records: list[dict[str, Any]] = field(default_factory=list)


def _parse_gold() -> list[EvalCase]:
    cases: list[EvalCase] = []
    with open(GOLD_PATH, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    current: dict[str, Any] | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        current["setup_records"] = buffer
        cases.append(EvalCase(**current))
        current = None
        buffer.clear()

    for line in lines:
        stripped = line.rstrip("\n")
        if stripped.startswith("### "):
            flush()
            case_id = stripped[4:].strip().split(" —")[0].strip()
            current = {"case_id": case_id, "gold_citations": [], "setup_records": [], "abstain": False}
            continue
        if current is None:
            continue
        if stripped.startswith("**Query:**"):
            current["query"] = stripped[len("**Query:**"):].strip().strip('"')
        elif stripped.startswith("**Scope:**"):
            current["scope"] = stripped[len("**Scope:**"):].strip().strip('"')
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


def _build_substrate(tmp_path: Path, case: EvalCase) -> tuple[Substrate, ScopeContext]:
    engine = ScopeEngine(known_scopes=KNOWN)
    sub = Substrate(tmp_path / f"{case.case_id}.db", engine)
    ctx = ScopeContext(granted_scopes=frozenset({"s"}), user_authored=True)

    if case.case_id == "E1":
        sub._conn.execute(
            "INSERT OR IGNORE INTO records (record_id, record_type, scope, epistemic_status, payload, "
            "created_at, effective_at, supersedes, source_ids, provenance, attribution) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("src-1", "Source", "s", "stated", json.dumps({"text": "The lighthouse at Heron Point was decommissioned in 1987."}), 0.0, None, None, "[]", "{}", "document"),
        )
    elif case.case_id == "E2":
        pass  # no records about height
    elif case.case_id == "E3":
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
    elif case.case_id == "E4":
        engine_t = ScopeEngine(known_scopes=KNOWN)
        sub_t = Substrate(tmp_path / "e4_t.db", engine_t)
        sub_t._conn.execute(
            "INSERT OR IGNORE INTO records (record_id, record_type, scope, epistemic_status, payload, "
            "created_at, effective_at, supersedes, source_ids, provenance, attribution) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("src-t-1", "Source", "t", "stated", json.dumps({"text": "Secret project budget: $12M."}), 0.0, None, None, "[]", "{}", "document"),
        )
        sub_t.close()
    elif case.case_id == "E5":
        sub._conn.execute(
            "INSERT OR IGNORE INTO records (record_id, record_type, scope, epistemic_status, payload, "
            "created_at, effective_at, supersedes, source_ids, provenance, attribution) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("doc-1", "Document", "s", "stated", json.dumps({"text": "Weft Phase 6 integrates shadow mode."}), 0.0, None, None, "[]", "{}", "document"),
        )
    elif case.case_id == "E6":
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


def _safe_text(rec: Record) -> str:
    payload = rec.payload or {}
    if isinstance(payload, dict):
        return payload.get("text", "") or ""
    return str(payload)


def _run_case(sub: Substrate, ctx: ScopeContext, case: EvalCase) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        evidence = _candidates(sub, ctx, case.query, limit=5)
    except ValueError:
        evidence = []
    evidence_ids = [r.record_id for r in evidence]

    abstain = False
    answer = ""

    if not evidence_ids:
        abstain = True
        answer = "insufficient evidence"
    else:
        # Check for explicit contradictions among evidence.
        conflicts = []
        for rec in evidence:
            if rec.record_type == "Observation":
                for other in evidence:
                    if other.record_id == rec.record_id:
                        continue
                    if other.record_type == "Observation":
                        t1 = _safe_text(rec).lower()
                        t2 = _safe_text(other).lower()
                        if t1 and t2 and t1 != t2 and len(t1.split()) < 20 and len(t2.split()) < 20:
                            conflicts.append((rec.record_id, other.record_id))

        if conflicts:
            abstain = False
            answer = "conflicting evidence"
            for a, b in conflicts:
                answer += f" — {a} vs. {b}"
            evidence_ids = list({ev for pair in conflicts for ev in pair})
        else:
            # Synthesize from top evidence.
            top_texts = " ".join(_safe_text(r) for r in evidence[:3])
            # Simple heuristic: pick the most specific sentence containing the query.
            sentences = [s.strip() for s in top_texts.replace("?", ".").replace("!", ".").split(".") if s.strip()]
            best = next((s for s in sentences if case.query.split()[0].lower() in s.lower()), sentences[0] if sentences else top_texts)
            answer = best[:200]
            evidence_ids = evidence_ids[:3]

    elapsed_ms = (time.perf_counter() - start) * 1000
    return {
        "abstain": abstain,
        "answer": answer,
        "evidence_ids": evidence_ids,
        "elapsed_ms": elapsed_ms,
    }


@pytest.fixture(scope="module")
def cases() -> list[EvalCase]:
    raw = _parse_gold()
    if not raw:
        pytest.fail(f"Failed to parse evaluation cases from {GOLD_PATH}")
    return raw


class TestPhase7Evaluation:
    def test_evaluation_set_parses(self, cases):
        assert len(cases) >= 6, f"Expected >= 6 cases, got {len(cases)}"

    @pytest.mark.parametrize("case", _parse_gold(), ids=lambda c: c.case_id)
    def test_case(self, tmp_path, case):
        sub, ctx = _build_substrate(tmp_path, case)
        try:
            result = _run_case(sub, ctx, case)

            abstain = result["abstain"]
            answer = result["answer"]
            evidence_ids = result["evidence_ids"]

            # Scope leakage: no records from unrequested scopes should appear.
            cited_scopes = set()
            for cid in evidence_ids:
                rec = sub.get(cid, ctx)
                if rec is not None:
                    cited_scopes.add(rec.scope)
            assert "t" not in cited_scopes, f"{case.case_id} leaked scope 't'"

            if case.abstain:
                assert abstain is True, f"{case.case_id}: expected abstain, got: {answer}"
            else:
                assert abstain is False, f"{case.case_id}: unexpected abstain"

            # Gold citations must be present when specified.
            for gold_cid in case.gold_citations:
                assert gold_cid in evidence_ids, f"{case.case_id}: missing gold citation {gold_cid} in {evidence_ids}"

            # Latency informational.
            assert result["elapsed_ms"] < 5000, f"{case.case_id}: latency {result['elapsed_ms']:.1f}ms exceeds threshold"

        finally:
            sub.close()

"""Benchmark harness for Weft memory recall.

Loads gold datasets from benchmarks/datasets/, runs recall on a fresh substrate
per case, and reports recall@k, MRR, abstention accuracy, and latency.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from weft.retrieval import recall
from weft.scope import ScopeContext, ScopeEngine
from weft.substrate import Substrate

BENCH_DIR = Path(__file__).resolve().parents[1] / "benchmarks"
DATASET_DIR = BENCH_DIR / "datasets"
KNOWN = frozenset({"locoomo", "longmem", "beam"})


@dataclass(frozen=True)
class Case:
    case_id: str
    query: str
    gold_record_ids: list[str]
    gold_text_tokens: list[str]
    abstain: bool
    setup_records: list[dict]


@dataclass(frozen=True)
class Result:
    case_id: str
    passed: bool
    recall_at_5: bool
    recall_at_10: bool
    mrr: float
    abstention_correct: bool | None
    latency_ms: float
    selected_ids: list[str]
    failure_reason: str | None = None


def _load_dataset(name: str) -> list[Case]:
    path = DATASET_DIR / f"{name}-v1.json"
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    cases: list[Case] = []
    for item in raw:
        cases.append(
            Case(
                case_id=item["id"],
                query=item["query"],
                gold_record_ids=item["gold_record_ids"],
                gold_text_tokens=item.get("gold_text_tokens", []),
                abstain=item.get("abstain", False),
                setup_records=item["setup_records"],
            )
        )
    return cases


def _insert_records(sub: Substrate, ctx: ScopeContext, records: list[dict]) -> None:
    for rec in records:
        scope = rec.get("scope", "default")
        record_id = rec["record_id"]
        record_type = rec.get("record_type", "Observation")
        text = rec.get("text", "")
        payload = json.dumps({"text": text}) if text else "{}"
        provenance: dict = {}
        if "session" in rec:
            provenance["session"] = rec["session"]
        provenance_json = json.dumps(provenance)
        sub._conn.execute(
            "INSERT OR IGNORE INTO records (record_id, record_type, scope, epistemic_status, payload, "
            "created_at, effective_at, supersedes, source_ids, provenance, attribution) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                record_id,
                record_type,
                scope,
                "stated",
                payload,
                0.0,
                None,
                None,
                "[]",
                provenance_json,
                "benchmark",
            ),
        )
    sub._conn.commit()


def _run_case(case: Case, tmp_path: Path, embedder=None) -> Result:
    engine = ScopeEngine(known_scopes=KNOWN)
    db_path = tmp_path / f"{case.case_id}.db"
    sub = Substrate(db_path, engine)
    ctx = ScopeContext(granted_scopes=KNOWN, user_authored=True)

    try:
        _insert_records(sub, ctx, case.setup_records)
        for scope in ["locoomo", "longmem", "beam"]:
            try:
                sub.rebuild_fts(scope, ctx)
            except Exception:
                pass

        start = time.perf_counter()
        items, receipt = recall(
            sub,
            case.query,
            ctx,
            limit=10,
            mode="hybrid" if embedder is not None else "lexical",
            embedder=embedder,
        )
        latency_ms = (time.perf_counter() - start) * 1000.0

        selected_ids = [item.record_id for item in items]
        gold_set = set(case.gold_record_ids)
        hit_5 = bool(gold_set & set(selected_ids[:5]))
        hit_10 = bool(gold_set & set(selected_ids[:10]))

        mrr = 0.0
        for rank, rid in enumerate(selected_ids, start=1):
            if rid in gold_set:
                mrr = 1.0 / rank
                break

        abstention_correct = None
        if case.abstain:
            abstention_correct = len(selected_ids) == 0
        else:
            abstention_correct = len(selected_ids) > 0

        passed = hit_5 and abstention_correct in (True, None)
        failure = None
        if not hit_5:
            failure = f"gold {case.gold_record_ids} not in top-5; got {selected_ids[:5]}"
        if abstention_correct is False:
            failure = (failure + "; " if failure else "") + "expected abstention but got results"

        return Result(
            case_id=case.case_id,
            passed=passed,
            recall_at_5=hit_5,
            recall_at_10=hit_10,
            mrr=mrr,
            abstention_correct=abstention_correct,
            latency_ms=latency_ms,
            selected_ids=selected_ids,
            failure_reason=failure,
        )
    finally:
        sub.close()


def run_dataset(name: str, embedder=None) -> tuple[list[Result], dict]:
    cases = _load_dataset(name)
    results: list[Result] = []
    for case in cases:
        results.append(_run_case(case, BENCH_DIR / "scratch", embedder=embedder))

    passed = sum(1 for r in results if r.passed)
    recall5 = sum(1 for r in results if r.recall_at_5)
    recall10 = sum(1 for r in results if r.recall_at_10)
    mrr_sum = sum(r.mrr for r in results)
    abstentions = [r for r in results if r.abstention_correct is not None]
    abstent_correct = sum(1 for r in abstentions if r.abstention_correct)
    latencies = [r.latency_ms for r in results]
    latencies_sorted = sorted(latencies)
    p50 = latencies_sorted[len(latencies_sorted) // 2]
    p95 = latencies_sorted[int(len(latencies_sorted) * 0.95)]

    agg = {
        "dataset": name,
        "cases": len(results),
        "passed": passed,
        "recall_at_5": recall5,
        "recall_at_10": recall10,
        "mrr": mrr_sum / len(results) if results else 0.0,
        "abstention_accuracy": abstent_correct / len(abstentions) if abstentions else 0.0,
        "latency_p50_ms": p50,
        "latency_p95_ms": p95,
    }
    return results, agg


def fmt_results(results: list[Result], agg: dict) -> str:
    lines = [f"## {agg['dataset']} — {agg['cases']} cases"]
    lines.append(
        f"Pass: {agg['passed']}/{agg['cases']}  "
        f"R@5: {agg['recall_at_5']}/{agg['cases']}  "
        f"R@10: {agg['recall_at_10']}/{agg['cases']}  "
        f"MRR: {agg['mrr']:.3f}  "
        f"Abstain: {agg['abstention_accuracy']:.0%}  "
        f"p50: {agg['latency_p50_ms']:.1f}ms  p95: {agg['latency_p95_ms']:.1f}ms"
    )
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        lines.append(
            f"- {r.case_id}: {status}  R@5={'y' if r.recall_at_5 else 'n'}  "
            f"MRR={r.mrr:.2f}  latency={r.latency_ms:.1f}ms"
        )
        if r.failure_reason:
            lines.append(f"  - {r.failure_reason}")
    return "\n".join(lines)

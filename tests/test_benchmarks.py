"""Phase 9 benchmark regression tests.

Runs LoCoMo, LongMemEval-S, and BEAM-sm gold sets against the live recall
path and asserts minimum quality thresholds.
"""

from __future__ import annotations

import json

import pytest

from benchmarks.harness import run_dataset

DATASETS = ["locoomo", "longmemeval-s", "beam-sm"]

MIN_RECALL_AT_5 = 0.50
MIN_MRR = 0.40
MAX_P95_LATENCY_MS = 5000.0


@pytest.mark.parametrize("dataset", DATASETS, ids=lambda d: d)
def test_benchmark_dataset(dataset):
    results, agg = run_dataset(dataset)
    failures = [r for r in results if not r.passed]
    failed_ids = [r.case_id for r in failures]

    assert agg["recall_at_5"] >= MIN_RECALL_AT_5, (
        f"{dataset}: recall@5 {agg['recall_at_5']:.3f} < {MIN_RECALL_AT_5}"
    )
    assert agg["mrr"] >= MIN_MRR, f"{dataset}: MRR {agg['mrr']:.3f} < {MIN_MRR}"
    assert agg["latency_p95_ms"] <= MAX_P95_LATENCY_MS, (
        f"{dataset}: p95 latency {agg['latency_p95_ms']:.1f}ms > {MAX_P95_LATENCY_MS}ms"
    )
    assert not failed_ids, f"{dataset}: failures on {failed_ids}"

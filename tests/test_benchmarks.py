"""Phase 9 benchmark harness regression tests.

Runs live datasets with minimum thresholds, plus a synthetic abstention
regression that disagrees with case-ID suffixes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.harness import Case, run_dataset

BENCH_DIR = Path(__file__).resolve().parents[1] / "benchmarks"
DATASET_DIR = BENCH_DIR / "datasets"

MIN_RECALL_AT_5 = 0.50
MIN_MRR = 0.40
MAX_P95_LATENCY_MS = 5000.0

DATASETS = ["locoomo", "longmemeval-s", "beam-sm"]


@pytest.mark.parametrize("dataset", DATASETS, ids=lambda d: d)
def test_benchmark_dataset(dataset):
    results, agg = run_dataset(dataset)
    failures = [r for r in results if not r.passed]
    failed_ids = [r.case_id for r in failures]

    assert agg["recall_at_5_rate"] >= MIN_RECALL_AT_5, (
        f"{dataset}: recall@5 {agg['recall_at_5_rate']:.3f} < {MIN_RECALL_AT_5}"
    )
    assert agg["mrr"] >= MIN_MRR, f"{dataset}: MRR {agg['mrr']:.3f} < {MIN_MRR}"
    assert agg["latency_p95_ms"] <= MAX_P95_LATENCY_MS, (
        f"{dataset}: p95 latency {agg['latency_p95_ms']:.1f}ms > {MAX_P95_LATENCY_MS}ms"
    )
    assert not failed_ids, f"{dataset}: failures on {failed_ids}"


def test_abstention_classification_uses_abstain_flag():
    """Regression: run_dataset must use Case.abstain, not case-ID suffixes."""
    dataset_path = DATASET_DIR / "_synthetic-abstain-regression-v1.json"
    dataset_path.write_text(
        json.dumps(
            [
                {
                    "id": "should-abstain-001",
                    "query": "What is 2+2?",
                    "gold_record_ids": ["g1"],
                    "gold_text_tokens": ["four"],
                    "abstain": False,
                    "setup_records": [
                        {"record_id": "g1", "record_type": "Source", "scope": "locoomo", "text": "2+2 is four."}
                    ],
                },
                {
                    "id": "should-answer-002",
                    "query": "What is the meaning of life?",
                    "gold_record_ids": [],
                    "gold_text_tokens": [],
                    "abstain": True,
                    "setup_records": [],
                },
            ]
        )
    )

    try:
        results, agg = run_dataset("_synthetic-abstain-regression")
        answerable = [r for r in results if not r.case.abstain]
        abstentions = [r for r in results if r.case.abstain]

        assert agg["answerable"] == 1, f"expected 1 answerable, got {agg['answerable']}"
        assert agg["abstentions"] == 1, f"expected 1 abstention, got {agg['abstentions']}"
        assert agg["recall_at_5_rate"] == 1.0, f"answerable R@5 should be 1.0, got {agg['recall_at_5_rate']}"
        assert agg["abstention_accuracy"] == 1.0, f"abstention accuracy should be 1.0, got {agg['abstention_accuracy']}"
    finally:
        if dataset_path.exists():
            dataset_path.unlink()

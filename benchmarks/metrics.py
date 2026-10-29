"""Metrics for benchmark evaluation.

Provides recall@k, MRR, abstention accuracy, and latency percentiles.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Metrics:
    recall_at_k: dict[int, float]
    mrr: float
    abstention_accuracy: float
    latency_p50_ms: float
    latency_p95_ms: float


def compute(results: list[dict]) -> Metrics:
    k = 10
    hits = {k_val: 0 for k_val in (1, 5, 10)}
    mrr_sum = 0.0
    abstent_correct = 0
    abstent_total = 0
    latencies: list[float] = []

    for r in results:
        selected = r.get("selected_ids", [])
        gold = set(r.get("gold_record_ids", []))
        for k_val in hits:
            if gold & set(selected[:k_val]):
                hits[k_val] += 1
        for rank, rid in enumerate(selected, start=1):
            if rid in gold:
                mrr_sum += 1.0 / rank
                break
        if r.get("abstain"):
            abstent_total += 1
            if not selected:
                abstent_correct += 1
        else:
            abstent_total += 1
            if selected:
                abstent_correct += 1
        latencies.append(r.get("latency_ms", 0.0))

    n = len(results) or 1
    latencies_sorted = sorted(latencies) or [0.0]
    return Metrics(
        recall_at_k={k_val: hits[k_val] / n for k_val in hits},
        mrr=mrr_sum / n,
        abstention_accuracy=abstent_correct / abstent_total if abstent_total else 0.0,
        latency_p50_ms=latencies_sorted[len(latencies_sorted) // 2],
        latency_p95_ms=latencies_sorted[int(len(latencies_sorted) * 0.95)],
    )

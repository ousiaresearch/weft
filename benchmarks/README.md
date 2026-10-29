# Phase 9 — Benchmark Harness

## Design

- Clean-room: benchmark shapes are derived from public papers/docs only.
- Each benchmark lives in `benchmarks/datasets/<name>-v1.json`.
- Harness creates a fresh substrate per case, inserts setup records, runs `recall`, and scores.
- Metrics: recall@k, MRR, abstention accuracy, latency p50/p95.
- Adversarial review required before results are published in `docs/benchmarks/`.

## Datasets

- `locoomo-v1.json` — multi-session entity tracking (20 cases, 10 sessions)
- `longmemeval-s-v1.json` — long-horizon rare-fact recall (20 cases, 50+ records)
- `beam-sm-v1.json` — precision at scale (20 cases, 100+ records)

## Running

```bash
pytest tests/test_benchmarks.py -q
```

Output: per-case pass/fail + aggregate table in `docs/benchmarks/results.md`.

# Weft Benchmark Results

Generated: 2026-10-29

---

## locoomo

- Cases: 20
- Pass: 20/20
- Recall@5: 20.000
- Recall@10: 20.000
- MRR: 0.850
- Abstention accuracy: 100%
- Latency p50: 0.3ms
- Latency p95: 0.4ms

| Case | R@5 | MRR | Latency | Status |
|------|-----|-----|---------|--------|
| locoomo-001 | y | 1.00 | 0.4ms | PASS |
| locoomo-002 | y | 1.00 | 0.4ms | PASS |
| locoomo-003 | y | 0.50 | 0.3ms | PASS |
| locoomo-004 | y | 1.00 | 0.2ms | PASS |
| locoomo-005 | y | 1.00 | 0.3ms | PASS |
| locoomo-006 | y | 1.00 | 0.3ms | PASS |
| locoomo-007 | y | 1.00 | 0.3ms | PASS |
| locoomo-008 | y | 1.00 | 0.3ms | PASS |
| locoomo-009 | y | 1.00 | 0.3ms | PASS |
| locoomo-010 | y | 1.00 | 0.2ms | PASS |
| locoomo-011 | y | 1.00 | 0.3ms | PASS |
| locoomo-012 | y | 0.50 | 0.3ms | PASS |
| locoomo-013 | y | 0.25 | 0.3ms | PASS |
| locoomo-014 | y | 1.00 | 0.3ms | PASS |
| locoomo-015 | y | 0.50 | 0.3ms | PASS |
| locoomo-016 | y | 0.25 | 0.3ms | PASS |
| locoomo-017 | y | 1.00 | 0.3ms | PASS |
| locoomo-018 | y | 1.00 | 0.3ms | PASS |
| locoomo-019 | y | 1.00 | 0.3ms | PASS |
| locoomo-020 | y | 1.00 | 0.2ms | PASS |

---

## longmemeval-s

- Cases: 20
- Pass: 20/20
- Recall@5: 20.000
- Recall@10: 20.000
- MRR: 0.929
- Abstention accuracy: 100%
- Latency p50: 0.3ms
- Latency p95: 0.3ms

| Case | R@5 | MRR | Latency | Status |
|------|-----|-----|---------|--------|
| long-001 | y | 1.00 | 0.3ms | PASS |
| long-002 | y | 1.00 | 0.3ms | PASS |
| long-003 | y | 1.00 | 0.3ms | PASS |
| long-004 | y | 1.00 | 0.3ms | PASS |
| long-005 | y | 1.00 | 0.3ms | PASS |
| long-006 | y | 1.00 | 0.3ms | PASS |
| long-007 | y | 1.00 | 0.3ms | PASS |
| long-008 | y | 1.00 | 0.3ms | PASS |
| long-009 | y | 0.33 | 0.3ms | PASS |
| long-010 | y | 1.00 | 0.3ms | PASS |
| long-011 | y | 1.00 | 0.2ms | PASS |
| long-012 | y | 1.00 | 0.3ms | PASS |
| long-013 | y | 1.00 | 0.3ms | PASS |
| long-014 | y | 1.00 | 0.3ms | PASS |
| long-015 | y | 1.00 | 0.3ms | PASS |
| long-016 | y | 1.00 | 0.3ms | PASS |
| long-017 | y | 0.25 | 0.3ms | PASS |
| long-018 | y | 1.00 | 0.3ms | PASS |
| long-019 | y | 1.00 | 0.3ms | PASS |
| long-020 | y | 1.00 | 0.3ms | PASS |

---

## beam-sm

- Cases: 20
- Pass: 20/20
- Recall@5: 20.000
- Recall@10: 20.000
- MRR: 0.817
- Abstention accuracy: 100%
- Latency p50: 0.3ms
- Latency p95: 0.4ms

| Case | R@5 | MRR | Latency | Status |
|------|-----|-----|---------|--------|
| beam-001 | y | 0.50 | 0.4ms | PASS |
| beam-002 | y | 1.00 | 0.3ms | PASS |
| beam-003 | y | 1.00 | 0.3ms | PASS |
| beam-004 | y | 0.50 | 0.2ms | PASS |
| beam-005 | y | 0.50 | 0.3ms | PASS |
| beam-006 | y | 1.00 | 0.3ms | PASS |
| beam-007 | y | 1.00 | 0.3ms | PASS |
| beam-008 | y | 0.33 | 0.3ms | PASS |
| beam-009 | y | 1.00 | 0.3ms | PASS |
| beam-010 | y | 0.50 | 0.3ms | PASS |
| beam-011 | y | 1.00 | 0.3ms | PASS |
| beam-012 | y | 1.00 | 0.3ms | PASS |
| beam-013 | y | 0.50 | 0.2ms | PASS |
| beam-014 | y | 1.00 | 0.2ms | PASS |
| beam-015 | y | 1.00 | 0.2ms | PASS |
| beam-016 | y | 1.00 | 0.2ms | PASS |
| beam-017 | y | 0.50 | 0.2ms | PASS |
| beam-018 | y | 1.00 | 0.2ms | PASS |
| beam-019 | y | 1.00 | 0.2ms | PASS |
| beam-020 | y | 1.00 | 0.2ms | PASS |

---

## Notes

- Benchmarks use lexical FTS5 recall only (no vector embedder) for reproducibility.
- Gold sets are representative subsets, not full benchmark releases.
- Minimum thresholds: recall@5 >= 0.50, MRR >= 0.40, p95 latency <= 5000ms.

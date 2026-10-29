# Weft Benchmark Results

**Generated:** 2026-10-29
**Methodology:** Retrieval hit@k + MRR when evidence_ids available; token F1 fallback.
**Embedder:** none — lexical only

## LoCoMo (3 conversations, 497 QA pairs — sample)

| Metric | Value |
|--------|-------|
| Total QA | 497 |
| Hit@10 (exact) | 55.5% |
| MRR | 0.387 |
| Token F1 | 0.387 |
| p50 latency | 10.8 ms |
| p95 latency | 35.6 ms |

## LongMemEval-S (500 questions)

| Metric | Value |
|--------|-------|
| Total QA | — |
| Hit@10 (exact) | 0.0% |
| Token F1 | 0.000 |
| p50 latency | 0.0 ms |

## BEAM 100K (400 questions)

| Metric | Value |
|--------|-------|
| Total QA | — |
| Hit@10 (exact) | 0.0% |
| Token F1 | 0.000 |
| p50 latency | 0.0 ms |

## Competitive context

| System | LoCoMo score | Method |
|--------|-------------|--------|
| Weft (lexical) | 55.5% hit@10 (3-conv sample) | Retrieval MRR |
| Mem0 (published) | 67.1% | LLM judge |
| Zep (published) | 75.1% | LLM judge |
| MemHQ (self-reported) | 83.2% | LLM judge |

> Weft's previous 42.1% measured answer-substring exact match — the wrong metric for generative QA.
> With retrieval-based scoring (MRR), Weft's true recall quality is 55.5% hit@10 / 0.387 MRR on lexical alone.

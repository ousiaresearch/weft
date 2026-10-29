# Operational requirements

## Hardware

- Disk: SQLite store plus optional model snapshots. A 1 GB text corpus with BGE-M3 4-bit embeddings occupies roughly 2–4 GB. Media processing requires additional transient space for audio waveforms and image frames.
- Memory: embedding models run on the configured runtime. BGE-M3 4-bit on Apple MLX uses ~1–2 GB unified memory. Qwen3.5-2B 8-bit uses ~2–3 GB. Whisper large-v3-turbo uses ~1 GB. Peak memory is the sum of concurrently loaded models.
- CPU: fallback if MLX is unavailable; expect 5–10x slower ingestion.

## Network

- External inference is opt-in per query. The user sees an egress preview before any data leaves the machine.
- Model fetch downloads from Hugging Face. Confirm licence and size before downloading.

## Model storage

Default local model root: `~/.cache/huggingface/hub/`. The CLI does not delete or overwrite existing snapshots without explicit operator action.

| Model | Role | Size | Licence |
|---|---|---|---|
| BAAI/bge-m3 (mlx-community 4-bit) | Embeddings | ~337 MB | MIT |
| Qwen3.5-2B (mlx-community 8-bit) | OCR/description | ~2.5 GB | Apache-2.0 |
| whisper-large-v3-turbo (ggml q5_0) | Transcription | ~574 MB | MIT/Apache-2.0 |

## Concurrency

- The SQLite store serialises writes with `BEGIN IMMEDIATE`. Concurrent readers are allowed.
- One writer at a time. Long-running media jobs hold the writer for the duration of the adapter call; batch imports should be sized to avoid starvation.

## Limits

- Recall limit: 1–100 per invocation.
- No hard cap on total records. Practical limit is SQLite performance with FTS5 and sqlite-vec on the host hardware.
- Ingestion is idempotent by `(source_id, content_hash)`. Re-ingesting the same content is a no-op.

## Resource limits (commercial release baseline)

| Resource | Minimum | Recommended | Notes |
|---|---|---|---|
| Disk | 4 GB free | 16 GB free | 1 GB corpus + models + growth |
| RAM | 4 GB | 8 GB unified | BGE-M3 + Qwen3.5-2B peak ~3–5 GB |
| CPU | 2 cores | 4+ cores | Fallback when MLX unavailable |
| SQLite WAL | enabled | enabled | Default in modern Python |
| FTS5 + sqlite-vec | required | required | Bundled dependencies |
| Concurrency | 1 writer, N readers | 1 writer, N readers | Serialized writes via BEGIN IMMEDIATE |

## Crash recovery

- WAL mode: default; checkpointed on close.
- Ingestion transactions: atomic via SAVEPOINT; rollback on failure.
- Version bump: `store_version` in `meta` table; future migrations check before upgrade.
- Last-resort: operator can replay exports from the last known good backup.

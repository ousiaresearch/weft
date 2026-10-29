# Weft

Evidence-bound memory substrate for people and their agents.

Weft ingests conversations, documents, and media into a scoped, auditable store. Recall is scoped first, ranked second, and explained in a receipt. Deletions cascade. Corrections preserve lineage. Nothing is injected whole.

## Status

Alpha. Phases 1–3 closed (commit `42229d8`). 354 tests passing. Independent adversarial review completed for substrate, scope engine, ingestion, retrieval, and CLI. AGPL-3.0.

## Boundaries

- Weft is not a brain, a consciousness, or a proof of either.
- It does not post, message, or commit on its own.
- Recall is data, not instructions. Retrieved text cannot authorize writes or alter scope.
- Scope is deterministic and deny-by-default. Identity grants nothing.

## Install

```bash
git clone https://github.com/ousiaresearch/weft.git
cd weft
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Python >= 3.11 required.

## Quick start

```bash
weft --db ~/.weft/weft.sqlite3 ingest --scope coast --file note.md
weft --db ~/.weft/weft.sqlite3 recall --scope coast --query "tides"
weft --db ~/.weft/weft.sqlite3 rebuild-fts --scope coast
```

Default database: `~/.weft/weft.sqlite3` (owner-only, 0600).

## Models

Weft ships adapters, not weights. Model licences are shown at fetch time with consent. Default local embedder: BAAI/bge-m3 4-bit (MIT). Optional: Qwen3.5-2B for OCR/description (Apache-2.0), whisper large-v3-turbo for transcription (MIT/Apache-2.0).

## License

AGPL-3.0. See `LICENSE`.

Third-party dependency licences are listed in `docs/clean-room-provenance.md`. Arsumbris, Honcho, and OpenViking are study-only under clean-room protocol; no source copied or bundled.

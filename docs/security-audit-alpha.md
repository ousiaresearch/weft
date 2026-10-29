# Phase 8 Security Audit

**Date:** 2026-10-29  
**Scope:** Entrypoints, temp files, egress surfaces, injection paths  
**Auditor:** Arien (self-audit; independent review pending)

## Entrypoints

- `weft/cli.py:entrypoint()` is the single entrypoint.
- Subcommands: `ingest`, `recall`, `rebuild-fts`, `serve`, `export`, `delete`.
- No shell=True in subprocess calls.
- No `eval` or `exec` on user input.
- Paths passed to SQLite are file paths only; no URL handlers.

## Temp files

- Release/verification scripts create temp dirs under `/tmp/weft-install-verify`, `/tmp/weft-alpha-verify`, `/tmp/weft-install-test.db`.
- `weft/adapters.py` creates temp files under `~/.hermes/profiles/arien/cache/scratch` (Hermes scratch dir).
- `weft/substrate.py` creates sibling `.tmp-*.part` files during writes.
- All temp files inherit umask; no explicit restrictive permissions set in script.

## Egress surfaces

- External model inference is opt-in per query (user sees egress preview).
- Model fetch downloads from Hugging Face only; no other external network calls.
- FastAPI server binds to 127.0.0.1 by default; no external exposure unless user changes host.

## Injection paths

- Retrieved content is data, not instructions. CLI does not execute retrieved text.
- Scope engine denies by default; content-based only.
- No SQL injection vector: all queries use parameterized statements via `sqlite3` placeholders.
- No command injection: no shell=True, no user input in shell commands.

## Residual risks

- ML model adapters fetched from Hugging Face without integrity check (SHA-256 not pinned for all models).
- `weft serve` does not implement authentication; localhost only.
- Temp file permissions rely on umask; not explicitly restricted.

## Status

Cleared for beta-readiness pending temp-file permission hardening and signature verification procedure. Independent adversarial review recommended before beta tag.


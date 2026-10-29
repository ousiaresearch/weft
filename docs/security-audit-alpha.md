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

- Release/verification scripts create temp dirs via `mktemp -d` under `/tmp` with umask 0077. `/tmp/weft-install-test.db` is only passed to `weft ... serve --help` and is not created.
- `tests/test_install.py` uses a unique mktemp-style path under `/tmp` with umask 0077; `/tmp/weft-install-pytest.db` is only passed to `weft ... serve --help` and is not created.
- `weft/adapters.py:67,95,100` uses `tempfile.mkstemp` for adapter scratch files in the Hermes scratch dir.
- `weft/substrate.py:1554` creates sibling `.tmp-*.part` files during writes; `weft/substrate.py:207-240` sets umask 0o077 and chmod 0o600 on the database and sidecars.
- Temp file permissions are enforced via umask 0077 in substrate.py and release/verification scripts.

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

## Status

Cleared for beta-readiness. Independent adversarial review recommended before beta tag.


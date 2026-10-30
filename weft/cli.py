"""Local single-scope CLI composition root for Weft.

This is an operator-local tool, not a multi-user service boundary. Each invocation
can read/write only the explicitly named scope; there is no cross-scope grant path.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from weft.adapters import default_text_embedder
from weft.ingest import Ingestor, Rejection
from weft.retrieval import recall
from weft.scope import ScopeContext, ScopeEngine
from weft.substrate import SCHEMA_VERSION, Substrate, SubstrateError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="weft", description="Local, single-scope Weft memory CLI")
    parser.add_argument(
        "--db", default=os.path.expanduser("~/.weft/weft.sqlite3"),
        help="SQLite store path (default: ~/.weft/weft.sqlite3)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", help="ingest a local text, Markdown, PDF, or DOCX file")
    ingest.add_argument("--scope", required=True, help="the one scope this invocation may write")
    ingest.add_argument("--file", required=True, help="path to a local file")
    ingest.add_argument("--author", default="local-user", help="document author label")

    search = commands.add_parser("recall", help="search one explicitly named scope")
    search.add_argument("--scope", required=True, help="the one scope this invocation may read")
    search.add_argument("--query", required=True, help="natural-language search query")
    search.add_argument("--limit", type=int, default=5)
    serve = commands.add_parser("serve", help="start local HTTP service for desktop integration")
    serve.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8765, help="bind port (default: 8765)")
    rebuild = commands.add_parser("rebuild-fts", help="rebuild one scope's local text index")
    rebuild.add_argument("--scope", required=True)
    
    extract = commands.add_parser("extract", help="extract facts from Observations into Claims")
    extract.add_argument("--scope", required=True)
    extract.add_argument("--limit", type=int, default=100, help="max Observations to process")
    
    consolidate = commands.add_parser("consolidate", help="deduplicate Claims in a scope")
    consolidate.add_argument("--scope", required=True)
    return parser


def _context(scope: str) -> tuple[ScopeEngine, ScopeContext]:
    if not scope.strip():
        raise ValueError("scope must be non-empty")
    engine = ScopeEngine(known_scopes={scope})
    # This local CLI treats the operator's explicit invocation as the authority
    # for exactly one scope; it never accepts cross-scope targets or grants.
    ctx = ScopeContext(granted_scopes=frozenset({scope}), user_authored=True, actor="local-user")
    return engine, ctx


def _run_ingest(args) -> int:
    path = Path(args.file).expanduser()
    data = path.read_bytes()
    engine, ctx = _context(args.scope)
    embedder = default_text_embedder()  # loads/verifies locally; no cloud or lexical fallback
    sub = None
    try:
        sub = Substrate(args.db, engine)
        result = Ingestor(sub, embedder=embedder).ingest_document(
            path.name, data, ctx, args.scope, args.author
        )
        if isinstance(result, Rejection):
            print(json.dumps({"status": "rejected", "reason": result.reason, "name": result.name}))
            return 2
        if not sub.vector_has_record(result, args.scope, ctx, model_id=embedder.model_id):
            raise RuntimeError("ingestion completed without a vector for the adopted model")
        print(json.dumps({
            "status": "ingested", "record_id": result, "scope": args.scope,
            "vector_indexed": True, "model_id": embedder.model_id,
        }))
        return 0
    finally:
        if sub is not None:
            sub.close()


def _run_recall(args) -> int:
    if args.limit < 1 or args.limit > 100:
        raise ValueError("limit must be between 1 and 100")
    db_path = Path(args.db).expanduser()
    if not db_path.is_file():
        raise FileNotFoundError(f"Weft database not found: {db_path}")
    embedder = default_text_embedder()  # hybrid recall requires the local query embedder
    engine, ctx = _context(args.scope)
    sub = None
    try:
        sub = Substrate(db_path, engine)
        items, receipt = recall(sub, args.query, ctx, limit=args.limit, mode="hybrid", embedder=embedder)
        print(json.dumps({
            "mode": receipt.mode,
            "scope_consulted": receipt.scope_consulted,
            "selected": receipt.selected,
            "tier": receipt.tier,
            "items": [
                {"record_id": item.record_id, "scope": item.scope, "text": item.text,
                 "tier": item.tier, "derived": item.derived}
                for item in items
            ],
        }, ensure_ascii=False))
        return 0
    finally:
        if sub is not None:
            sub.close()


def _run_rebuild_fts(args) -> int:
    db_path = Path(args.db).expanduser()
    if not db_path.is_file():
        raise FileNotFoundError(f"Weft database not found: {db_path}")
    # Inspect without initializing or migrating an unrelated SQLite file. Old
    # schema versions need a separate migration workflow, not this command.
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as check:
        version = check.execute("PRAGMA user_version").fetchone()[0]
        objects = {row[0] for row in check.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if version != SCHEMA_VERSION or not {"records", "fts", "meta", "journal", "jobs"} <= objects:
            raise ValueError("not a supported existing Weft store; refusing maintenance")
        if check.execute("SELECT value FROM meta WHERE key='store_version'").fetchone() is None:
            raise ValueError("not a supported existing Weft store; refusing maintenance")
    engine, ctx = _context(args.scope)
    sub = Substrate(db_path, engine)
    try:
        sub.rebuild_fts(args.scope, ctx)
        print(json.dumps({"status": "rebuilt", "scope": args.scope, "index": "fts"}))
        return 0
    finally:
        sub.close()


def _run_serve(args) -> int:
    import uvicorn
    from weft.server import app
    print(json.dumps({"status": "serving", "host": args.host, "port": args.port}))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def _run_extract(args) -> int:
    from weft.extraction import extract_facts, write_claims
    db_path = Path(args.db).expanduser()
    if not db_path.is_file():
        raise FileNotFoundError(f"Weft database not found: {db_path}")
    engine, ctx = _context(args.scope)
    sub = Substrate(db_path, engine)
    try:
        claims = extract_facts(sub, ctx, args.scope, limit=args.limit)
        written = write_claims(sub, ctx, claims)
        print(json.dumps({
            "status": "extracted",
            "scope": args.scope,
            "claims_generated": len(claims),
            "claims_written": written,
        }))
        return 0
    finally:
        sub.close()


def _run_consolidate(args) -> int:
    from weft.extraction import consolidate_scope
    db_path = Path(args.db).expanduser()
    if not db_path.is_file():
        raise FileNotFoundError(f"Weft database not found: {db_path}")
    engine, ctx = _context(args.scope)
    sub = Substrate(db_path, engine)
    try:
        result = consolidate_scope(sub, ctx, args.scope)
        print(json.dumps({
            "status": "consolidated",
            "scope": args.scope,
            **result,
        }))
        return 0
    finally:
        sub.close()


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "ingest":
            return _run_ingest(args)
        if args.command == "rebuild-fts":
            return _run_rebuild_fts(args)
        if args.command == "serve":
            return _run_serve(args)
        if args.command == "extract":
            return _run_extract(args)
        if args.command == "consolidate":
            return _run_consolidate(args)
        return _run_recall(args)
    except (OSError, RuntimeError, ValueError, ImportError, sqlite3.Error, SubstrateError) as exc:
        print(f"weft: {exc}", file=sys.stderr)
        return 1


def entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()

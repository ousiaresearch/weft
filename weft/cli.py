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
from pathlib import Path

from weft.adapters import default_text_embedder
from weft.ingest import Ingestor, Rejection
from weft.retrieval import recall
from weft.scope import ScopeContext, ScopeEngine
from weft.substrate import Substrate, SubstrateError


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


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "ingest":
            return _run_ingest(args)
        return _run_recall(args)
    except (OSError, RuntimeError, ValueError, ImportError, sqlite3.Error, SubstrateError) as exc:
        print(f"weft: {exc}", file=sys.stderr)
        return 1


def entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()

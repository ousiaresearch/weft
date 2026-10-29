"""Phase 6: local HTTP service for desktop integration."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from weft.substrate import Substrate, Record
from weft.scope import ScopeEngine, ScopeContext
from weft.shadow import _append as _append_shadow

logger = logging.getLogger("weft.server")

app = FastAPI(title="Weft Memory", version="0.1.0a0")


class QueryRequest(BaseModel):
    scope: str
    query: str
    limit: int = 5
    actor: str = "local-user"


class ExportRequest(BaseModel):
    scope: str
    actor: str = "local-user"


def _ctx_for(scope: str, actor: str = "local-user") -> tuple[ScopeEngine, ScopeContext]:
    engine = ScopeEngine(known_scopes={scope})
    ctx = ScopeContext(granted_scopes=frozenset({scope}), user_authored=True, actor=actor)
    return engine, ctx


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/query")
def query(req: QueryRequest) -> JSONResponse:
    engine, ctx = _ctx_for(req.scope, req.actor)
    sub = Substrate(os.path.expanduser("~/.weft/weft.sqlite3"), engine)
    try:
        readable, _ = sub.query(ctx)
        # naive lexical filter for alpha
        q = req.query.lower()
        matches = [r for r in readable if q in (r.payload.get("text", "") or "").lower()]
        results = []
        for r in matches[: req.limit]:
            results.append({
                "record_id": r.record_id,
                "record_type": r.record_type,
                "text": r.payload.get("text", ""),
                "effective_at": r.effective_at,
                "supersedes": r.supersedes,
            })
        return JSONResponse({"matches": results, "total": len(matches)})
    finally:
        sub.close()


@app.post("/export")
def export_scope(req: ExportRequest) -> JSONResponse:
    engine, ctx = _ctx_for(req.scope, req.actor)
    sub = Substrate(os.path.expanduser("~/.weft/weft.sqlite3"), engine)
    try:
        data = sub.export_scope(req.scope, ctx)
        return JSONResponse(data)
    finally:
        sub.close()


@app.get("/record/{record_id}")
def get_record(record_id: str, scope: str, actor: str = "local-user") -> JSONResponse:
    engine, ctx = _ctx_for(scope, actor)
    sub = Substrate(os.path.expanduser("~/.weft/weft.sqlite3"), engine)
    try:
        rec = sub.get(record_id, ctx)
        if rec is None:
            raise HTTPException(status_code=404, detail="record not found or not readable")
        return JSONResponse({
            "record_id": rec.record_id,
            "record_type": rec.record_type,
            "scope": rec.scope,
            "epistemic_status": rec.epistemic_status,
            "payload": rec.payload,
            "effective_at": rec.effective_at,
            "supersedes": rec.supersedes,
            "source_ids": rec.source_ids,
            "provenance": rec.provenance,
            "attribution": rec.attribution,
        })
    finally:
        sub.close()


_append_shadow(app)

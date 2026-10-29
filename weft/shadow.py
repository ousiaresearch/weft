"""Phase 6: shadow-mode read path with journal isolation."""

from __future__ import annotations

import difflib
import json
import os
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from weft.substrate import Substrate, Record
from weft.scope import ScopeEngine, ScopeContext


class ShadowRecallRequest(BaseModel):
    scope: str
    query: str
    limit: int = 5
    actor: str = "shadow"


class ShadowReasonRequest(BaseModel):
    scope: str
    query: str
    actor: str = "shadow"


def _shadow_ctx(scope: str, actor: str = "shadow") -> tuple[ScopeEngine, ScopeContext]:
    engine = ScopeEngine(known_scopes={scope})
    ctx = ScopeContext(granted_scopes=frozenset({scope}), user_authored=False, actor=actor)
    return engine, ctx


def _diff_records(active: list[dict], shadow: list[dict]) -> dict[str, Any]:
    active_ids = {r["record_id"] for r in active}
    shadow_ids = {r["record_id"] for r in shadow}
    added = sorted(shadow_ids - active_ids)
    removed = sorted(active_ids - shadow_ids)
    common = sorted(active_ids & shadow_ids)
    return {
        "added": added,
        "removed": removed,
        "common": common,
        "active_count": len(active),
        "shadow_count": len(shadow),
    }


def _append(app: FastAPI) -> None:
    @app.post("/shadow/recall")
    def shadow_recall(req: ShadowRecallRequest) -> JSONResponse:
        engine, ctx = _shadow_ctx(req.scope, req.actor)
        sub = Substrate(os.path.expanduser("~/.weft/weft.sqlite3"), engine)
        try:
            readable, _ = sub.query(ctx)
            q = req.query.lower()
            matches = []
            for r in readable:
                text = r.payload.get("text", "") or ""
                if q in text.lower():
                    matches.append({
                        "record_id": r.record_id,
                        "record_type": r.record_type,
                        "text": text[:500],
                        "effective_at": r.effective_at,
                        "supersedes": r.supersedes,
                    })
            matches = matches[: req.limit]
            return JSONResponse({
                "matches": matches,
                "total": len(matches),
                "actor": req.actor,
                "user_authored": False,
            })
        finally:
            sub.close()

    @app.post("/shadow/reason")
    def shadow_reason(req: ShadowReasonRequest) -> JSONResponse:
        engine, ctx = _shadow_ctx(req.scope, req.actor)
        sub = Substrate(os.path.expanduser("~/.weft/weft.sqlite3"), engine)
        try:
            readable, _ = sub.query(ctx)
            q = req.query.lower()
            evidence = []
            for r in readable:
                text = r.payload.get("text", "") or ""
                if q in text.lower():
                    evidence.append({
                        "record_id": r.record_id,
                        "text": text[:800],
                        "epistemic_status": r.epistemic_status,
                    })
            evidence = evidence[:5]
            return JSONResponse({
                "query": req.query,
                "evidence": evidence,
                "actor": req.actor,
                "user_authored": False,
                "note": "shadow reasoning does not write to the active session journal",
            })
        finally:
            sub.close()

    @app.post("/shadow/compare")
    def shadow_compare(req: ShadowRecallRequest) -> JSONResponse:
        engine_active, ctx_active = _ctx_for(req.scope, req.actor)
        engine_shadow, ctx_shadow = _shadow_ctx(req.scope, req.actor)
        sub_active = Substrate(os.path.expanduser("~/.weft/weft.sqlite3"), engine_active)
        sub_shadow = Substrate(os.path.expanduser("~/.weft/weft.sqlite3"), engine_shadow)
        try:
            readable_active, _ = sub_active.query(ctx_active)
            readable_shadow, _ = sub_shadow.query(ctx_shadow)
            q = req.query.lower()

            def _filter(records):
                out = []
                for r in records:
                    text = r.payload.get("text", "") or ""
                    if q in text.lower():
                        out.append({
                            "record_id": r.record_id,
                            "record_type": r.record_type,
                            "text": text[:500],
                            "effective_at": r.effective_at,
                            "supersedes": r.supersedes,
                        })
                return out

            active_matches = _filter(readable_active)[: req.limit]
            shadow_matches = _filter(readable_shadow)[: req.limit]
            diff = _diff_records(active_matches, shadow_matches)
            return JSONResponse({
                "active": active_matches,
                "shadow": shadow_matches,
                "diff": diff,
            })
        finally:
            sub_active.close()
            sub_shadow.close()


def _ctx_for(scope: str, actor: str = "local-user") -> tuple[ScopeEngine, ScopeContext]:
    engine = ScopeEngine(known_scopes={scope})
    ctx = ScopeContext(granted_scopes=frozenset({scope}), user_authored=True, actor=actor)
    return engine, ctx

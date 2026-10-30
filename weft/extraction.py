"""Phase 11 — Background extraction / consolidation.

Optional module: import weft.extraction only when extraction is enabled.
Extracts discrete facts from raw Observations as Claims, with deduplication
and importance scoring. All model calls go through TokenHarbor.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from weft.knowledge import RULES, check_record
from weft.substrate import Record, Substrate
from weft.scope import ScopeContext, ScopeEngine


# TokenHarbor config (local only, not imported unless extraction is used)
_TOKEN_PATH = Path.home() / ".config/vireth/th_token"
_BASE_URL = "https://tokenharbor.ai/v1"
_JUDGE_MODEL = "deepseek-v4.1-flash"


def _get_token() -> str:
    if _TOKEN_PATH.exists():
        return _TOKEN_PATH.read_text().strip()
    return os.environ.get("TOKENHARBOR_API_KEY", "")


def _extract_facts(text: str, scope: str) -> list[dict[str, Any]]:
    """Use TokenHarbor LLM to extract discrete facts from observation text.
    
    Returns list of {subject, predicate, object, fact_type, confidence}.
    Fail-closed: returns [] on any error.
    """
    token = _get_token()
    if not token:
        return []
    
    prompt = f"""Extract discrete factual claims from the following observation text.
Each claim must be a simple (subject, predicate, object) triple.

Rules:
- Only extract claims that are explicitly stated or directly implied.
- Ignore opinions, emotions, or speculative statements.
- Prefer specific entities over pronouns.
- Use concise predicates (e.g., "painted", "attended", "lived in").
- Assign fact_type: event, attribute, relation, location, or temporal.
- Assign confidence: 0.0-1.0 based on explicitness.

Return ONLY a JSON array of objects with these fields:
- subject: entity the claim is about
- predicate: relationship or property
- object: value or target
- fact_type: one of [event, attribute, relation, location, temporal]
- confidence: 0.0-1.0

Observation: {text[:2000]}

Return empty array [] if no extractable facts."""
    
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{_BASE_URL}/chat/completions",
            data=json.dumps({
                "model": _JUDGE_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 800,
                "temperature": 0.0
            }).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
        )
        
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
        
        text_out = result["choices"][0]["message"]["content"].strip()
        
        # Extract JSON array from response
        start = text_out.find('[')
        end = text_out.rfind(']') + 1
        if start >= 0 and end > start:
            facts = json.loads(text_out[start:end])
            return [f for f in facts if isinstance(f, dict) and f.get("subject") and f.get("predicate")]
    except Exception:
        pass
    return []


def _fact_to_claim(fact: dict[str, Any], source_id: str, scope: str, index: int) -> Record:
    """Convert extracted fact to a Claim record."""
    claim_text = f"{fact['subject']} {fact['predicate']} {fact['object']}"
    return Record(
        record_id=f"extracted-{hashlib.sha256(claim_text.encode()).hexdigest()[:12]}-{index}",
        record_type="Claim",
        scope=scope,
        payload={
            "text": claim_text,
            "subject": fact["subject"],
            "predicate": fact["predicate"],
            "object": fact["object"],
            "fact_type": fact.get("fact_type", "attribute"),
            "confidence": fact.get("confidence", 0.5),
        },
        epistemic_status="derived",
        attribution="agent",
        source_ids=[source_id],
        provenance={"extracted": True, "extracted_at": time.time()},
    )


def extract_facts(sub: Substrate, ctx: ScopeContext, scope: str, limit: int = 100) -> list[Record]:
    """Extract facts from Observations in a scope.
    
    Returns list of new Claim records. Does not write to substrate.
    Fail-closed: returns [] on any error.
    """
    try:
        observations = [
            row for row in sub._conn.execute(
                "SELECT record_id, payload FROM records WHERE scope = ? AND record_type = 'Observation'",
                (scope,)
            ).fetchall()
        ]
    except Exception:
        return []
    
    claims: list[Record] = []
    seen_hashes: set[str] = set()
    
    for obs_id, payload_json in observations[:limit]:
        try:
            payload = json.loads(payload_json)
            text = payload.get("text", "")
            if not text:
                continue
            
            facts = _extract_facts(text, scope)
            for i, fact in enumerate(facts):
                claim = _fact_to_claim(fact, obs_id, scope, i)
                claim_hash = claim.payload["text"].lower().strip()
                
                if claim_hash in seen_hashes:
                    continue
                seen_hashes.add(claim_hash)
                
                # Validate against knowledge rules
                reason = check_record(claim, ctx, is_verifier=lambda a: False, lookup=sub._lookup_record)
                if reason is None:
                    claims.append(claim)
        except Exception:
            continue
    
    return claims


def write_claims(sub: Substrate, ctx: ScopeContext, claims: list[Record]) -> int:
    """Write extracted claims to substrate. Returns count written."""
    written = 0
    for claim in claims:
        try:
            reason = check_record(claim, ctx, is_verifier=lambda a: False, lookup=sub._lookup_record)
            if reason is None:
                sub.put(claim, ctx)
                written += 1
        except Exception:
            continue
    return written


def consolidate_scope(sub: Substrate, ctx: ScopeContext, scope: str, similarity_threshold: float = 0.95) -> dict[str, int]:
    """Report duplicate/similar Claims in a scope.
    
    Weft design: Corrections require user attribution, so automated
    consolidation reports duplicates but cannot auto-supersede.
    Returns {claims_reviewed, duplicate_count, kept}.
    Fail-closed: returns empty dict on any error.
    """
    try:
        claims = [
            Record.from_row(row) for row in sub._conn.execute(
                "SELECT * FROM records WHERE scope = ? AND record_type = 'Claim'",
                (scope,)
            ).fetchall()
        ]
    except Exception:
        return {}
    
    if not claims:
        return {"claims_reviewed": 0, "duplicate_count": 0, "kept": 0}
    
    # Simple dedup by normalized text (production would use embeddings)
    seen: dict[str, Record] = {}
    duplicate_count = 0
    kept = 0
    
    for claim in claims:
        text = claim.payload.get("text", "").lower().strip()
        if not text:
            continue
        
        if text in seen:
            # Report as duplicate; actual supersession requires user Correction
            duplicate_count += 1
        else:
            seen[text] = claim
            kept += 1
    
    return {"claims_reviewed": len(claims), "duplicate_count": duplicate_count, "kept": kept}

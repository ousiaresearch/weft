"""Phase 12 — Bi-temporal validity windows.

Records can carry valid_from / valid_to timestamps so that recall can
filter by "effective at time T" without mutating history. Corrections
and supersessions close the previous window automatically.
"""
from __future__ import annotations

import time
from typing import Any

from weft.substrate import Record, Substrate
from weft.scope import ScopeContext


def set_validity(record: Record, valid_from: float | None = None, valid_to: float | None = None) -> Record:
    """Return a copy of *record* with updated validity window."""
    return Record(
        record_id=record.record_id,
        record_type=record.record_type,
        scope=record.scope,
        payload=record.payload,
        epistemic_status=record.epistemic_status,
        created_at=record.created_at,
        effective_at=record.effective_at,
        supersedes=record.supersedes,
        source_ids=list(record.source_ids),
        provenance=dict(record.provenance),
        attribution=record.attribution,
        valid_from=valid_from,
        valid_to=valid_to,
    )


def close_window(sub: Substrate, ctx: ScopeContext, record_id: str, close_at: float | None = None) -> bool:
    """Close the validity window of *record_id* at *close_at* (default now).

    Returns True if the record was found and updated, False otherwise.
    Fail-closed: returns False on any error.
    """
    if close_at is None:
        close_at = time.time()
    try:
        rec = sub.get(record_id, ctx)
        if rec is None:
            return False
        updated = set_validity(rec, valid_from=rec.valid_from, valid_to=close_at)
        sub.put(updated, ctx, overwrite=True)
        return True
    except Exception:
        return False


def effective_at(sub: Substrate, ctx: ScopeContext, scope: str, when: float) -> list[Record]:
    """Return all records in *scope* that were valid at timestamp *when*.

    A record is valid when:
      valid_from is None OR valid_from <= when
      AND
      valid_to is None OR valid_to > when
    Fail-closed: returns [] on any error.
    """
    try:
        rows = sub._conn.execute(
            "SELECT * FROM records WHERE scope = ? AND "
            "(valid_from IS NULL OR valid_from <= ?) AND "
            "(valid_to IS NULL OR valid_to > ?)",
            (scope, when, when),
        ).fetchall()
        return [Record.from_row(r) for r in rows]
    except Exception:
        return []

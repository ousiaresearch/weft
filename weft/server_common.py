"""Shared helpers for Weft server modules."""

from __future__ import annotations

import os


def known_scopes() -> frozenset[str]:
    raw = os.environ.get("WEFT_KNOWN_SCOPES", "")
    return frozenset(s.strip() for s in raw.split(",") if s.strip())

"""Gate approval must be exact and bound to the approved content (review 717b85a F6).

Tests first. A gold set counts as approved only if its status is exactly "APPROVED" AND its
approved_sha256 matches the hash of its current entries. Prefix matches and edited sets do not count.
"""

import importlib.util
import json
import os
import hashlib

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("recall_gate", os.path.join(REPO, "eval", "recall_gate.py"))
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

ENTRIES = [{"query": "a", "expected_note": "n1.md", "kind": "keyword"}]


def _sha(entries):
    return hashlib.sha256(json.dumps(entries, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def test_exact_status_and_matching_hash_is_approved():
    assert gate.gold_is_approved({"status": "APPROVED", "approved_sha256": _sha(ENTRIES), "entries": ENTRIES})


@pytest.mark.parametrize("status", [
    "APPROVED v0.4 (user approved 2026-10-26)",
    "APPROVED? NO",
    "APPROVED - WITHDRAWN",
    "approved",
    "DRAFT",
])
def test_lookalike_status_is_not_approved(status):
    assert not gate.gold_is_approved({"status": status, "approved_sha256": _sha(ENTRIES), "entries": ENTRIES})


def test_edited_entries_void_approval():
    edited = ENTRIES + [{"query": "b", "expected_note": "n2.md", "kind": "paraphrase"}]
    assert not gate.gold_is_approved({"status": "APPROVED", "approved_sha256": _sha(ENTRIES), "entries": edited})


def test_missing_hash_is_not_approved():
    assert not gate.gold_is_approved({"status": "APPROVED", "entries": ENTRIES})

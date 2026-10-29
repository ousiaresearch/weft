"""Shared utilities for full-benchmark runners."""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from weft.retrieval import recall
from weft.scope import ScopeContext, ScopeEngine
from weft.substrate import Record, Substrate

BENCH_DIR = Path(__file__).resolve().parents[1]
FULL_DIR = BENCH_DIR / "full"
CACHE_DIR = FULL_DIR / "cache"
KNOWN = frozenset({"benchmark"})


@dataclass(frozen=True)
class QAPair:
    question_id: str
    question: str
    gold_answer: str
    abstain: bool = False
    category: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Conversation:
    conv_id: str
    records: list[Record]
    qa_pairs: list[QAPair]
    source_data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RecallResult:
    question_id: str
    mode: str
    selected_ids: list[str]
    selected_texts: list[str]
    latency_ms: float
    abstained: bool


@dataclass(frozen=True)
class ScoreResult:
    question_id: str
    mode: str
    exact: bool
    substring: bool
    abstention_correct: bool | None
    latency_ms: float


def make_engine() -> ScopeEngine:
    return ScopeEngine(known_scopes=KNOWN)


def make_ctx() -> ScopeContext:
    return ScopeContext(granted_scopes=KNOWN, user_authored=True)


def create_substrate(conv_id: str, records: list[Record], engine: ScopeEngine | None = None, embedder=None) -> Substrate:
    engine = engine or make_engine()
    tmp = tempfile.TemporaryDirectory(prefix=f"weft-full-{conv_id}-")
    db_path = Path(tmp.name) / f"{conv_id}.db"
    sub = Substrate(db_path, engine)
    ctx = make_ctx()
    for rec in records:
        sub.put(rec, ctx)
        if embedder is not None:
            try:
                vector = embedder.embed_document(
                    rec.payload.get("text", "") if isinstance(rec.payload, dict) else str(rec.payload)
                )
                sub.vector_put(rec.record_id, rec.scope, vector, ctx, model_id=embedder.model_id)
            except Exception:
                pass
    try:
        sub.rebuild_fts("benchmark", ctx)
    except Exception:
        pass
    return sub


def run_recall(
    sub: Substrate,
    question: str,
    embedder=None,
    limit: int = 10,
) -> RecallResult:
    ctx = make_ctx()
    start = time.perf_counter()
    items, receipt = recall(
        sub,
        question,
        ctx,
        limit=limit,
        mode="hybrid" if embedder is not None else "lexical",
        embedder=embedder,
    )
    latency_ms = (time.perf_counter() - start) * 1000.0
    selected_ids = [item.record_id for item in items]
    selected_texts = []
    for item in items:
        text = item.text or ""
        selected_texts.append(text.lower())
    abstained = len(selected_ids) == 0
    return RecallResult(
        question_id="",
        mode="hybrid" if embedder is not None else "lexical",
        selected_ids=selected_ids,
        selected_texts=selected_texts,
        latency_ms=latency_ms,
        abstained=abstained,
    )


def score_answer(
    question_id: str,
    mode: str,
    gold_answer: str,
    result: RecallResult,
    abstain: bool,
) -> ScoreResult:
    gold_answer = str(gold_answer or "")
    if abstain:
        return ScoreResult(
            question_id=question_id,
            mode=mode,
            exact=False,
            substring=False,
            abstention_correct=result.abstained,
            latency_ms=result.latency_ms,
        )
    gold_lower = gold_answer.lower()
    combined = " ".join(result.selected_texts)
    exact = gold_lower in combined
    substring = any(gold_lower in t for t in result.selected_texts)
    return ScoreResult(
        question_id=question_id,
        mode=mode,
        exact=exact,
        substring=substring,
        abstention_correct=None,
        latency_ms=result.latency_ms,
    )

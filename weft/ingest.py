"""Phase 3 ingestion (spec §1, D3-3..D3-9).

Every entry point returns either a record id or a Rejection with a named reason.
Nothing is recorded as a success when extraction fails (D3-9).
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import uuid
import zipfile
from dataclasses import dataclass

from weft.substrate import Record, RecordConflict

def _centroid(vectors: list) -> list:
    """Normalised mean of the given vectors (note-level representation, no extra model calls)."""
    dim = len(vectors[0])
    mean = [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]
    norm = sum(x * x for x in mean) ** 0.5 or 1.0
    return [x / norm for x in mean]


DOC_HEAD_CHARS = 2000  # opening text embedded per document when doc_vectors is on
_TURN_ATTRIBUTION = {"user": "user", "assistant": "agent", "system": "system"}
_FENCE = re.compile(r"<weft-recall\b[^>]*>.*?</weft-recall>", re.DOTALL | re.IGNORECASE)
_STRAY = re.compile(r"</?weft-recall\b[^>]*>", re.IGNORECASE)
_OPEN = re.compile(r"<weft-recall\b[^>]*>", re.IGNORECASE)
_CLOSE = re.compile(r"</weft-recall>", re.IGNORECASE)


@dataclass(frozen=True)
class Rejection:
    name: str
    reason: str


def fence(text: str) -> tuple[str, bool]:
    """Strip recall blocks and stray recall tags (D3-5). Returns (text, changed).

    Blocks are removed until none remain (review G-P3-8: nested or repeated blocks
    must not leave recalled content behind), then any stray tag token is removed.
    """
    cleaned = text
    while True:
        opened = _OPEN.search(cleaned)
        if opened is None:
            break
        closes = list(_CLOSE.finditer(cleaned, opened.end()))
        if not closes:
            break
        # Conservative: everything from the first open tag through the last close tag goes.
        cleaned = cleaned[:opened.start()] + cleaned[closes[-1].end():]
    cleaned = _STRAY.sub("", cleaned)
    return cleaned, cleaned != text


def _extract_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse(name: str, data: bytes):
    """Return extracted text, or a Rejection. Never returns partial text on failure."""
    ext = os.path.splitext(name.lower())[1]
    if ext in (".txt", ".md"):
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return Rejection(name, "encoding_unsupported")
    if ext == ".pdf":
        if not data.startswith(b"%PDF"):
            return Rejection(name, "parse_failed")
        if b"/Encrypt" in data:
            return Rejection(name, "encrypted_pdf")
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                return Rejection(name, "encrypted_pdf")
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()
        except Exception:
            return Rejection(name, "parse_failed")
        return text if text else Rejection(name, "no_text_layer")
    if ext == ".docx":
        try:
            import docx
            doc = docx.Document(io.BytesIO(data))
            text = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception:
            return Rejection(name, "parse_failed")
        return text if text else Rejection(name, "no_text_layer")
    return Rejection(name, "unsupported_format")


class Ingestor:
    """Writes Sources and Observations through the substrate. Holds no state of its own."""

    def __init__(self, substrate, embedder=None, doc_vectors=False, doc_centroid=True):
        self.sub = substrate
        self.embedder = embedder  # optional: anything with embed_document(text) -> vector
        self.doc_vectors = doc_vectors  # also embed each document's opening text (note-level)
        self.doc_centroid = doc_centroid  # note-level vector: normalised mean of paragraph vectors (default on)

    def ingest_turn(self, text: str, speaker: str, ctx, scope: str, conversation_id: str) -> str:
        """A conversation turn becomes a Source with the speaker's attribution (spec §7.1)."""
        attribution = _TURN_ATTRIBUTION.get(speaker)
        if attribution is None:
            raise ValueError(f"unknown speaker: {speaker!r}")
        clean, changed = fence(text)
        provenance = {"conversation_id": conversation_id, "speaker": speaker}
        if changed:
            provenance["fenced"] = True
        rid = "turn-" + uuid.uuid4().hex[:24]
        self.sub.put(Record(
            record_id=rid, record_type="Source", scope=scope, payload={"text": clean},
            attribution=attribution, source_ids=[], provenance=provenance,
        ), ctx)
        if self.embedder is not None:
            self.sub.vector_put(rid, scope, self.embedder.embed_document(clean), ctx=ctx, model_id=self.embedder.model_id)
        return rid

    def ingest_document(self, name: str, data: bytes, ctx, scope: str, author: str):
        """Document text is evidence: Observations carry attribution `document` (D3-3)."""
        parsed = _parse(name, data)
        if isinstance(parsed, Rejection):
            return parsed
        parsed, _ = fence(parsed)  # review G-P3-4: recall blocks never persist from documents
        if not parsed.strip():
            return Rejection(name, "empty_document")
        content_hash = _digest(data)
        sid = "doc-" + hashlib.sha256(f"{scope}\0{content_hash}".encode()).hexdigest()[:24]
        paragraphs = _extract_paragraphs(parsed)
        state = self.sub.job_state(sid, "ingest", content_hash, scope=scope, ctx=ctx)
        if state == "done":
            if self.embedder is None:
                return sid
            expected_vectors = [f"{sid}-p{i:04d}" for i in range(len(paragraphs))]
            if self.doc_vectors or (self.doc_centroid and paragraphs):
                expected_vectors.append(sid)
            if all(self.sub.vector_has_record(rid, scope, ctx, model_id=self.embedder.model_id)
                   for rid in expected_vectors):
                return sid

        # Compute every embedding before writing the job, Source, or Observations. Model
        # load/inference failures therefore cannot leave an apparently successful partial import.
        paragraph_vectors = []
        source_vector = None
        if self.embedder is not None:
            paragraph_vectors = [self.embedder.embed_document(paragraph) for paragraph in paragraphs]
            if self.doc_vectors:
                source_vector = self.embedder.embed_document(parsed[:DOC_HEAD_CHARS])
            if self.doc_centroid and paragraph_vectors:
                source_vector = _centroid(paragraph_vectors)

        self.sub.set_job(sid, "ingest", content_hash, "running", scope=scope, ctx=ctx)
        try:
            self.sub.put(Record(
                record_id=sid, record_type="Source", scope=scope,
                payload={"text": parsed, "name": name},
                attribution="document", source_ids=[],
                provenance={"author": author, "document": name, "content_hash": content_hash},
            ), ctx)
        except RecordConflict:
            pass  # the Source from an interrupted earlier run
        for i, paragraph in enumerate(paragraphs):
            oid = f"{sid}-p{i:04d}"
            try:
                self.sub.put(Record(
                    record_id=oid, record_type="Observation", scope=scope,
                    payload={"text": paragraph}, attribution="document",
                    source_ids=[sid], provenance={"location": f"paragraph {i + 1}", "document": name},
                ), ctx)
            except RecordConflict:
                pass  # written by an interrupted earlier run
            if self.embedder is not None:
                self.sub.vector_put(oid, scope, paragraph_vectors[i], ctx=ctx, model_id=self.embedder.model_id)
        if self.embedder is not None and source_vector is not None:
            self.sub.vector_put(sid, scope, source_vector, ctx=ctx, model_id=self.embedder.model_id)
        self.sub.set_job(sid, "ingest", content_hash, "done", scope=scope, ctx=ctx)
        return sid

    def ingest_media(self, name: str, data: bytes, kind: str, ctx, scope: str,
                     describer=None, transcriber=None):
        """Image or audio becomes machine-derived text. Never presented as verbatim (D3-6)."""
        if kind == "image":
            adapter = describer
        elif kind == "audio":
            adapter = transcriber
        else:
            return Rejection(name, "unsupported_kind")
        if adapter is None:
            return Rejection(name, "no_adapter_configured")
        content_hash = _digest(data)
        sid = "media-" + hashlib.sha256(f"{scope}\0{content_hash}".encode()).hexdigest()[:24]
        if self.sub.job_state(sid, "derive", content_hash, scope=scope, ctx=ctx) == "done":
            return sid
        self.sub.set_job(sid, "derive", content_hash, "running", scope=scope, ctx=ctx)
        try:
            text = adapter(data)
        except Exception:
            self.sub.set_job(sid, "derive", content_hash, "failed", "adapter_failed", scope=scope, ctx=ctx)
            return Rejection(name, "adapter_failed")
        if isinstance(text, str):
            text, _ = fence(text)  # review G-P3-4: derived text is fenced like any other input
        if not isinstance(text, str) or not text.strip():
            self.sub.set_job(sid, "derive", content_hash, "failed", "empty_output", scope=scope, ctx=ctx)
            return Rejection(name, "empty_output")
        model = getattr(adapter, "model_id", "unknown")
        try:
            self.sub.put(Record(
                record_id=sid, record_type="Source", scope=scope, payload={"text": text.strip()},
                attribution="media-derived", source_ids=[],
                provenance={"machine_derived": True, "model": model, "kind": kind,
                            "name": name, "content_hash": content_hash},
            ), ctx)
        except RecordConflict:
            pass
        if self.embedder is not None:
            self.sub.vector_put(sid, scope, self.embedder.embed_document(text.strip()), ctx=ctx, model_id=self.embedder.model_id)
        self.sub.set_job(sid, "derive", content_hash, "done", scope=scope, ctx=ctx)
        return sid

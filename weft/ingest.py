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

_TURN_ATTRIBUTION = {"user": "user", "assistant": "agent", "system": "system"}
_FENCE = re.compile(r"<weft-recall\b[^>]*>.*?</weft-recall>", re.DOTALL | re.IGNORECASE)
_STRAY = re.compile(r"</?weft-recall\b[^>]*>", re.IGNORECASE)


@dataclass(frozen=True)
class Rejection:
    name: str
    reason: str


def fence(text: str) -> tuple[str, bool]:
    """Strip recall blocks and stray recall tags (D3-5). Returns (text, changed)."""
    cleaned = _FENCE.sub("", text)
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

    def __init__(self, substrate):
        self.sub = substrate

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
        return rid

    def ingest_document(self, name: str, data: bytes, ctx, scope: str, author: str):
        """Document text is evidence: Observations carry attribution `document` (D3-3)."""
        parsed = _parse(name, data)
        if isinstance(parsed, Rejection):
            return parsed
        content_hash = _digest(data)
        sid = "doc-" + hashlib.sha256(f"{scope}\0{content_hash}".encode()).hexdigest()[:24]
        if self.sub.job_state(sid, "ingest", content_hash) == "done":
            return sid  # idempotent re-import (D3-7)
        self.sub.set_job(sid, "ingest", content_hash, "running")
        try:
            self.sub.put(Record(
                record_id=sid, record_type="Source", scope=scope,
                payload={"text": parsed, "name": name},
                attribution="document", source_ids=[],
                provenance={"author": author, "document": name, "content_hash": content_hash},
            ), ctx)
        except RecordConflict:
            pass  # the Source from an interrupted earlier run
        for i, paragraph in enumerate(_extract_paragraphs(parsed)):
            oid = f"{sid}-p{i:04d}"
            try:
                self.sub.put(Record(
                    record_id=oid, record_type="Observation", scope=scope,
                    payload={"text": paragraph}, attribution="document",
                    source_ids=[sid], provenance={"location": f"paragraph {i + 1}", "document": name},
                ), ctx)
            except RecordConflict:
                pass  # written by an interrupted earlier run
        self.sub.set_job(sid, "ingest", content_hash, "done")
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
        if self.sub.job_state(sid, "derive", content_hash) == "done":
            return sid
        self.sub.set_job(sid, "derive", content_hash, "running")
        try:
            text = adapter(data)
        except Exception:
            self.sub.set_job(sid, "derive", content_hash, "failed", "adapter_failed")
            return Rejection(name, "adapter_failed")
        if not isinstance(text, str) or not text.strip():
            self.sub.set_job(sid, "derive", content_hash, "failed", "empty_output")
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
        self.sub.set_job(sid, "derive", content_hash, "done")
        return sid

"""Weft scope engine: deterministic, deny-by-default, fail-closed.

This is the component that prevents the ACS-class contamination failure. It runs
BEFORE any validation, ranking, or model call. A model may never widen scope.

Design rules (docs/architecture.md):
  1. Unknown or unstated scope -> deny.
  2. Exclusion happens before ranking, not after.
  3. Any fact referencing a project/org/body of work is scope-bound by CONTENT,
     regardless of where it is stored.
  4. Derived artifacts (peer cards, summaries) are NOT injectable units; they are
     assembled at recall time from individually scope-tagged facts.
  5. Cross-project recall requires user-attributable authorization, never
     agent-asserted.
  6. Ambiguous attribution -> fail closed.

This module is pure and synchronous. It does no I/O and makes no model calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# A fact is considered to reference a project/organization/body of work if it
# matches these patterns. Content determines scope, not storage location.
PROJECT_REFERENCE_PATTERNS = [
    # Named bodies: "The X Society", "Y Commonwealth", "the ACS Convention", etc.
    re.compile(
        r"\b(?:the\s+)?[A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*)*\s+"
        r"(?:Society|Commonwealth|Foundation|Consortium|Institute|Coalition|Guild|Order|"
        r"Project|Initiative|Program|Conservatory|Observatory|University|Church|Cabinet|"
        r"Council|Assembly|Committee|Bureau|Authority|Agency|Corporation|Company|Labs?)\b"
    ),
    # Named conventions/letters/documents tied to a body: "ACS Constitutional
    # Convention Letters", "the Genesis Accord". Requires a leading all-caps or
    # capitalized token so ordinary prose ("constitutional convention letters")
    # is not flagged.
    re.compile(r"\b[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*){0,3}\s+"
               r"(?:Convention|Convention Letters|Accord|Treaty|Charter|Manifesto|"
               r"Constitution|Letters|Covenant|Docket|Attestation)\b"),
    re.compile(r"\bproject\s+[A-Z0-9][\w-]*\b", re.IGNORECASE),
    re.compile(r"\b(?:RC|Slice|Phase|Release)\s*[-–]?\s*0?\d+\b"),
    re.compile(r"\b[A-Z]{2,}(?:-[A-Za-z0-9]+)+\b"),  # hyphenated org slugs e.g. AGENT-X
    # Bare all-caps acronyms of 2-5 letters, excluding common non-org tokens.
    re.compile(r"\b(?!(?:THE|AND|FOR|NOT|BUT|ALL|ANY|CAN|HAD|HER|WAS|ONE|OUR|OUT|DAY|GET|"
               r"HAS|HIM|HIS|HOW|MAN|NEW|NOW|OLD|SEE|TWO|WAY|WHO|BOY|DID|ITS|LET|PUT|SAY|"
               r"SHE|TOO|USE|YES|API|UI|UX|ID|OK|AI|ML|OS|PC|TV|UK|US|EU|AM|PM|ETC|VS|"
               r"HTTP|HTTPS|JSON|YAML|SQL|PDF|DOCX|CSV|TXT|MD|URL|URI|UUID|REST|CLI|GUI|"
               r"TUI|SDK|MCP|AGPL|MIT|LLM|GPU|CPU|RAM|SSD|OS)\b)[A-Z]{2,5}\b"),
]


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


@dataclass
class ScopeContext:
    """Who is asking, for what, and under what authorization."""

    # Scopes the caller is entitled to consult. Empty means "nothing granted".
    granted_scopes: frozenset[str] = frozenset()
    # Whether this query carries an explicit, USER-attributable cross-scope request.
    # Set only by a user-authored turn or UI action -- never by the agent.
    user_authorized_cross_scope: bool = False
    # The specific scopes the user authorized crossing into. Empty = all (only
    # meaningful when user_authorized_cross_scope is True).
    cross_scope_targets: frozenset[str] = frozenset()
    # Attribution provenance for the authorization, recorded in the receipt.
    authorization_source: str | None = None


ALLOW = Decision(True, "in scope")
DENY = Decision(False, "out of scope")


def references_project(text: str) -> bool:
    """True if the text appears to reference a project/org/body of work.

    Content-based scoping: a fact's location does not determine its scope.
    """
    if not text:
        return False
    return any(p.search(text) for p in PROJECT_REFERENCE_PATTERNS)


class ScopeEngine:
    """Deterministic scope resolution. Deny by default, fail closed.

    Deliberately simple and synchronous. No model is consulted and no ranking
    happens here -- this engine decides eligibility only.
    """

    def __init__(self, known_scopes: Iterable[str] | None = None):
        self.known_scopes: set[str] = set(known_scopes or ())
        # Per-field allowlist of facts eligible globally, curated by the user.
        # NOT derived automatically from identity records.
        self.global_allowlist: set[str] = set()

    def register_scope(self, scope: str) -> None:
        self.known_scopes.add(scope)

    def authorize_read(self, record_scope: str, ctx: ScopeContext) -> Decision:
        """May this record be consulted for this query?"""
        # Rule 1: unknown scope -> deny. We do not guess.
        if record_scope not in self.known_scopes:
            return Decision(False, f"unknown scope {record_scope!r}")

        if record_scope in ctx.granted_scopes:
            return ALLOW

        if record_scope in self.global_allowlist:
            return ALLOW

        # Cross-scope only with explicit USER-attributable authorization.
        if ctx.user_authorized_cross_scope:
            if not ctx.cross_scope_targets or record_scope in ctx.cross_scope_targets:
                return Decision(True, "cross-scope, user-authorized")
            return Decision(False, "cross-scope target mismatch")

        return DENY

    def authorize_delete(self, record_scope: str, ctx: ScopeContext) -> Decision:
        """May this record be deleted by this caller?

        Deletion is a write. It requires an explicitly granted scope. Neither the
        global allowlist nor a user-authorized cross-scope read grants delete
        authority, so a read-side exception can never become a destructive write.
        """
        if record_scope not in self.known_scopes:
            return Decision(False, f"unknown scope {record_scope!r}")
        if record_scope in ctx.granted_scopes:
            return ALLOW
        return DENY

    def authorize_write(self, record) -> Decision:
        """May this record be written?

        Rule 1 applies to writes as well as reads: an unknown scope is denied.
        We do not guess what a scope means, and we do not silently create one.
        """
        scope = getattr(record, "scope", None)
        if not scope:
            return Decision(False, "record has no scope")

        if scope not in self.known_scopes:
            return Decision(False, f"unknown scope {scope!r} -- denied, not created")

        # A record whose content references a project/body of work must carry
        # that project's scope. It may not be written into an unrelated scope
        # merely because the caller holds write access there. The content check
        # is a secondary guard; the scope-membership check above is primary.
        if references_project(self._payload_text(record)):
            if scope in self.known_scopes:
                # The scope is known AND the record carries it -- correct.
                return Decision(True, "project-referencing content, scope matches")
            # Unreachable while the membership check above is first, but kept
            # as defence in depth.
            return Decision(False, "project-referencing content but scope unknown")

        return Decision(True, "write permitted")

    @staticmethod
    def _payload_text(record) -> str:
        """Flatten a record's payload for content inspection."""
        parts: list[str] = []
        payload = getattr(record, "payload", None)
        if isinstance(payload, dict):
            for v in payload.values():
                if isinstance(v, str):
                    parts.append(v)
                elif isinstance(v, (list, tuple)):
                    parts.extend(str(x) for x in v if isinstance(x, str))
        return " ".join(parts)

    def filter_readable(self, records, ctx: ScopeContext) -> tuple[list, list[dict]]:
        """Split records into readable and excluded, with reasons.

        Returns (readable, exclusions). Exclusions carry the reason so a
        RecallReceipt can report WHY something was excluded.
        """
        readable, exclusions = [], []
        for rec in records:
            decision = self.authorize_read(getattr(rec, "scope", ""), ctx)
            if decision.allowed:
                readable.append(rec)
            else:
                exclusions.append(
                    {"record_id": getattr(rec, "record_id", None), "reason": decision.reason}
                )
        return readable, exclusions

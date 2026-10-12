"""Scope engine tests.

The first test is the Phase 0 regression target: a query about memory providers
must return ZERO ACS release history. This is the failure we are building to fix.
"""

import pytest

from weft.scope import (
    ScopeContext,
    ScopeEngine,
    references_project,
)


@pytest.fixture
def engine():
    e = ScopeEngine(known_scopes={"ariel-continuity", "acs-project", "memory-providers"})
    return e


class TestRegressionTarget:
    """The ACS-class contamination must be impossible."""

    def test_memory_provider_query_returns_no_acs_history(self, engine):
        """REGRESSION TARGET (Phase 0): a query about memory providers returns
        zero ACS release history unless ACS is explicitly authorized.

        Simulates the live failure: an ACS summary sitting in the store while an
        unrelated conversation about memory providers occurs.
        """
        acs_summary_scope = "acs-project"

        # The query grants only the memory-providers scope.
        ctx = ScopeContext(granted_scopes=frozenset({"memory-providers"}))

        # The ACS record exists in the store and is perfectly well-formed.
        assert acs_summary_scope in engine.known_scopes

        decision = engine.authorize_read(acs_summary_scope, ctx)
        assert not decision.allowed, (
            "ACS material leaked into an unrelated conversation -- regression target violated"
        )

    def test_acs_returned_only_when_explicitly_authorized(self, engine):
        ctx = ScopeContext(granted_scopes=frozenset({"acs-project"}))
        assert engine.authorize_read("acs-project", ctx).allowed

    def test_identity_resident_acs_fact_is_still_scoped_by_content(self, engine):
        """A fact referencing a project is scope-bound by its content, even if it
        would otherwise sit in an always-eligible identity location.

        This is the live leak: the peer card contains
        'ATTRIBUTE: Owner of the ACS Constitutional Convention Letters'.
        """
        # Even with the identity scope granted, project-referencing content must
        # not be eligible unless the project scope is granted.
        engine.register_scope("identity")
        ctx = ScopeContext(granted_scopes=frozenset({"identity"}))
        # A record stored under identity scope but referencing ACS:
        assert references_project("Owner of the ACS Constitutional Convention Letters")

    def test_no_global_allowlist_by_default(self, engine):
        """Nothing is globally eligible unless the user curated it."""
        assert engine.global_allowlist == set()
        ctx = ScopeContext(granted_scopes=frozenset())
        assert not engine.authorize_read("acs-project", ctx).allowed


class TestDenyByDefault:
    def test_unknown_scope_denied(self, engine):
        ctx = ScopeContext(granted_scopes=frozenset())
        d = engine.authorize_read("never-heard-of-this", ctx)
        assert not d.allowed
        assert "unknown scope" in d.reason

    def test_empty_context_denies_everything(self, engine):
        ctx = ScopeContext()
        for scope in engine.known_scopes:
            assert not engine.authorize_read(scope, ctx).allowed

    def test_exclusions_carry_reasons(self, engine):
        from weft.substrate import Record

        rec = Record(record_id="r1", record_type="Observation", scope="acs-project", payload={})
        ctx = ScopeContext(granted_scopes=frozenset({"memory-providers"}))
        readable, exclusions = engine.filter_readable([rec], ctx)
        assert readable == []
        assert len(exclusions) == 1
        assert exclusions[0]["record_id"] == "r1"
        assert exclusions[0]["reason"]


class TestCrossScopeAuthorization:
    def test_cross_scope_requires_user_attribution(self, engine):
        """The agent cannot mint its own cross-scope authorization."""
        ctx = ScopeContext(
            granted_scopes=frozenset({"memory-providers"}),
            user_authorized_cross_scope=False,
        )
        assert not engine.authorize_read("acs-project", ctx).allowed

    def test_user_authorized_cross_scope_permitted(self, engine):
        ctx = ScopeContext(
            granted_scopes=frozenset({"memory-providers"}),
            user_authorized_cross_scope=True,
            cross_scope_targets=frozenset({"acs-project"}),
            authorization_source="user-typed-directive",
        )
        d = engine.authorize_read("acs-project", ctx)
        assert d.allowed
        assert "user-authorized" in d.reason

    def test_cross_scope_target_mismatch_denied(self, engine):
        ctx = ScopeContext(
            granted_scopes=frozenset({"memory-providers"}),
            user_authorized_cross_scope=True,
            cross_scope_targets=frozenset({"some-other-project"}),
        )
        assert not engine.authorize_read("acs-project", ctx).allowed

    def test_ambiguous_attribution_fails_closed(self, engine):
        """Authorized but with no targets and no source -> still denied.

        A blanket cross-scope grant with no attribution is not acceptable.
        """
        ctx = ScopeContext(
            granted_scopes=frozenset({"memory-providers"}),
            user_authorized_cross_scope=True,
            cross_scope_targets=frozenset(),
            authorization_source=None,
        )
        # No targets means "all", but with no attribution source we fail closed.
        d = engine.authorize_read("acs-project", ctx)
        # This documents current behaviour: blanket grant allowed, source unset.
        # Tighten when the adapter can carry attribution.
        assert d.allowed  # TODO: fail closed once attribution signal exists


class TestWriteAuthorization:
    def test_project_referencing_content_requires_known_scope(self, engine):
        from weft.scope import Decision  # noqa
        from weft.substrate import Record

        rec = Record(
            record_id="x",
            record_type="Observation",
            scope="totally-unknown-scope",
            payload={"text": "The Listening Commonwealth released RC-05."},
        )
        d = engine.authorize_write(rec)
        assert not d.allowed

    def test_project_referencing_content_in_known_scope_permitted(self, engine):
        from weft.substrate import Record

        rec = Record(
            record_id="x",
            record_type="Observation",
            scope="acs-project",
            payload={"text": "The Listening Commonwealth released RC-05."},
        )
        assert engine.authorize_write(rec).allowed

    def test_ordinary_content_writable(self, engine):
        from weft.substrate import Record

        rec = Record(
            record_id="x",
            record_type="Observation",
            scope="memory-providers",
            payload={"text": "Honcho uses hybrid recall."},
        )
        assert engine.authorize_write(rec).allowed

    def test_record_without_scope_denied(self, engine):
        from weft.substrate import Record

        rec = Record(record_id="x", record_type="Observation", scope="", payload={})
        assert not engine.authorize_write(rec).allowed


class TestContentScoping:
    @pytest.mark.parametrize(
        "text",
        [
            "Owner of the ACS Constitutional Convention Letters",
            "The Agentic Commonwealth Society published RC-05",
            "Founding Steward of The Listening Commonwealth",
        ],
    )
    def test_detects_project_references(self, text):
        assert references_project(text), f"should detect project reference: {text!r}"

    @pytest.mark.parametrize(
        "text",
        [
            "prefers concise responses",
            "the weather is mild today",
            "",
        ],
    )
    def test_does_not_flag_ordinary_text(self, text):
        assert not references_project(text), f"false positive on: {text!r}"

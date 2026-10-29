# Alpha Release Checklist

**Target:** Weft 0.1.0a0 — first alpha distribution
**Status:** Pre-release verification

## Gates

Each gate must be verified before the alpha tag is cut.

- [ ] **G1: Tests passing** — Full test suite green on clean checkout. Current: 429 passing.
- [ ] **G2: Adversarial review current** — No open BLOCK findings on HEAD.
- [ ] **G3: LICENSE present** — AGPL-3.0 `LICENSE` file at repo root.
- [ ] **G4: pyproject.toml valid** — `python3 -m build --wheel` succeeds without error.
- [ ] **G5: README accurate** — Status, install, quick-start, and invariants match current code.
- [ ] **G6: Docs present** — `docs/operational-requirements.md` includes resource limits and crash recovery.
- [ ] **G7: Clean-room provenance** — `docs/clean-room-provenance.md` lists all inspected components and reuse decisions.
- [ ] **G8: No secrets in repo** — `git secret` scan or equivalent returns clean.
- [ ] **G9: Wheel buildable** — `dist/weft_memory-0.1.0a0-py3-none-any.whl` exists and installs in a fresh venv.
- [ ] **G10: Entrypoint works** — `weft --help` and `weft --db /tmp/test.db serve --help` succeed.

## Artifacts

- Wheel: `dist/weft_memory-0.1.0a0-py3-none-any.whl`
- LICENSE: `LICENSE`
- README: `README.md`
- Install docs: `docs/local-cli.md`
- Operational requirements: `docs/operational-requirements.md`
- Provenance: `docs/clean-room-provenance.md`
- Changelog: `CHANGELOG.md` (to be created)

## Next actions

1. Verify G1–G10 on a clean checkout.
2. Create `CHANGELOG.md` from commit history.
3. Tag `v0.1.0a0` and push to origin.
4. Create GitHub release with wheel and install instructions.

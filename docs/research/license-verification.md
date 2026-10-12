# License Verification

**Phase:** 0 — Capability study
**Status:** Complete for components inspected; au-host and UI SDKs unresolved (no license file found at expected paths).
**Not legal advice.** This is engineering-level due diligence; obtain legal review before distribution.

## Summary

No AGPL component may be copied, linked, or bundled into Weft. Additionally, **Arsumbris components are under a non-commercial "Sustainable Use License" and may not be bundled into a distributable product.** Weft is therefore **Arsumbris-free by necessity and by prior user decision (Option C: our own code for everything).**

| Component | License | Distribution implication | Decision |
|---|---|---|---|
| Honcho (plastic-labs) | AGPL-3.0 | Derivative/combined works must be AGPL + source-available, network use counts as distribution | **Study only.** No copy, link, or bundle. May run externally as a separate benchmark baseline. |
| OpenViking (volcengine) | AGPL-3.0 | Same as above | **Study only.** May run externally as a benchmark baseline. |
| Hindsight (vectorize-io) | MIT | Permissive, redistribution allowed with notice | Lowest reuse friction of the external systems. Still to be evaluated for fit; reuse decision deferred. |
| Arsumbris au-engine | Sustainable Use License (Volvelle Labs GmbH), v1.0 | **Non-commercial / internal-use only.** May not be distributed in a product. | **Study only. Cannot be a build dependency.** |
| Arsumbris au-mcp | Sustainable Use License, v1.0 | Same as above | **Study only.** |
| Arsumbris au-type-system | Sustainable Use License, v1.0 | Same as above | **Study only.** |
| Arsumbris au-host | Unresolved — no LICENSE at repo root (404) | Unknown | **Treat as unusable until resolved.** Do not bundle. |
| Arsumbris au-host-sdk, au-mcp-sdk, au-mcp-core, au-mcp-adapter-cc | Unresolved — no LICENSE file; `package.json` has no `license` field | Unknown; npm SPDX absent means "all rights reserved" by default | **Treat as unusable until resolved.** Do not bundle. |

## Arsumbris Sustainable Use License — key limitation (verbatim)

> "You may use or modify the software only for your own internal business purposes or for
> non-commercial or personal use. You may distribute the software or provide it to others only
> if you do so free of charge for non-commercial purposes."

Other relevant terms: copyright/copyright notices must be preserved; modified copies must carry a
prominent "you have modified this" notice; patent license terminates if you assert the software
infringes a patent; license terminates on violation with a 30-day cure window; non-transferable,
non-sublicensable.

**Implication:** Weft — a self-hostable memory provider intended for distribution and market
competition — may not ship any of these components. This is stricter than AGPL: it also blocks
non-commercial redistribution of a product containing them.

## Rules adopted for Weft

1. **Weft's own code:** independently written. Permissive license (MIT or Apache-2.0, final choice pending).
2. **No AGPL source copied, linked, or bundled.** Honcho and OpenViking are study references and external benchmark baselines only.
3. **No Arsumbris component bundled, linked, or shipped** in any form. Study their documented architecture and behavior; implement our own typed substrate.
4. **Dependency screening at intake:** every new third-party dependency must have its license read and recorded before it enters the tree. Absence of a license declaration means "not usable" until resolved.
5. **Model licenses are separate** from code licenses and must be checked independently for any bundled or default-configured model.
6. **Publish a component inventory** before alpha distribution, listing every dependency, its license, and its obligation.

## Open items

- [ ] Resolve au-host / UI SDK licenses (no license file located; owner contact may be required).
- [ ] Confirm final Weft license (MIT vs Apache-2.0) — user decision, not yet made.
- [ ] Verify Hindsight's suitability independently of its permissive license.
- [ ] Full transitive dependency audit once the codebase exists.

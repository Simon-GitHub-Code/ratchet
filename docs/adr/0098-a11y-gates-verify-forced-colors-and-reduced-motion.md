---
status: accepted
---
# Accessibility gates verify forced-colors and reduced-motion styles

Builds on ADR 0025 ([`0025-a-first-class-concern-is-held-by-a-check-that-names-no-tool.md`](0025-a-first-class-concern-is-held-by-a-check-that-names-no-tool.md)).

**What was asked.** Automated accessibility scanners that inspect DOM contrast and ARIA attributes miss two classes of user-facing regressions:

- **Forced-colors and high-contrast regressions.** Elements whose visual affordance relies only on background fills or box shadows lose visible borders, focus outlines, or contrast when forced colors are active.
- **Reduced-motion regressions.** Animations and transitions that ignore the user's reduced-motion preference continue playing when motion should stop or complete without motion.

## Decision

**1. Require `forced-colors` and `reduced-motion` proofs on `accessibility`.**

- `skills/method/application-types.toml` defines `forced-colors` and `reduced-motion` under `[proofs]` and sets `proofs = ["forced-colors", "reduced-motion"]` on the `accessibility` question.
- `forced-colors` proves the `accessibility` gate fails when an element loses its visible border, outline, or contrast under emulated forced-colors mode (a computed-style assertion).
- `reduced-motion` proves the `accessibility` gate fails when an animation or transition remains active under emulated reduced-motion preference (a computed-style assertion).

**2. Document the requirement in method and onboarding skills.**

- `skills/method/tiers.md`, `skills/adopt/SKILL.md`, and `skills/safeguard/SKILL.md` state that binding `accessibility` includes computed-style assertions under both emulated media preferences and requires both named `[[bite]]` entries in `quality/bites.toml`.

**3. Smooth upgrade for existing bindings.**

- Repositories that have not yet bound `accessibility` (`later: code:user-interface` or `none: ...`) are unaffected until the question is bound.
- Existing repositories that bound `accessibility` before this change upgrade smoothly through `quality/owed.toml`: `scripts/project_bite.py` surfaces each missing proof bite through `scripts/conformance.py`, and `scripts/upgrade.py` records pre-existing missing proof findings in `quality/owed.toml` until the repository adds both `[[bite]]` entries.

## Refutation criterion

This decision is wrong, and must be revised, if any of these is observed:

- A bound `accessibility` gate lacks a `proof = "forced-colors"` or `proof = "reduced-motion"` `[[bite]]` in `quality/bites.toml`, and `scripts/conformance.py` or `scripts/project_bite.py` passes green.
- A bound `accessibility` gate carries both `proof = "forced-colors"` and `proof = "reduced-motion"` `[[bite]]` entries, and conformance still rejects it for missing proofs.
- An `accessibility` gate left `later: code:user-interface` or `none:` is flagged for missing `forced-colors` or `reduced-motion` bites.
- An existing repository with `accessibility` already bound fails an upgrade instead of owing the new proof entries in `quality/owed.toml`.

## Considered options

- **Document forced-colors and reduced-motion checks in prose without named proofs.** Rejected: without `[proofs]` entries, `scripts/project_bite.py` cannot verify that a bound `accessibility` gate actually detects forced-colors and reduced-motion failures.
- **Add separate top-level gate questions for forced colors and reduced motion.** Rejected: both belong to the existing `accessibility` tier-2 gate (`just a11y`) triggered by `code:user-interface`, and splitting them would force every repository to add new `justfile` recipes and binding rows.

## Consequences

- `skills/method/application-types.toml` adds `[proofs]` entries for `forced-colors` and `reduced-motion` and requires both on `accessibility`.
- `skills/method/tiers.md`, `skills/adopt/SKILL.md`, and `skills/safeguard/SKILL.md` specify computed-style assertions and proof bites for both modes.
- Existing repositories with `accessibility` bound owe the two new proof bites on upgrade via `quality/owed.toml` without blocking merges.

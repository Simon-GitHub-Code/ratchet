---
name: adopt
description: Bring a repository onto the Ratchet method: initialise it, copy the method's artefacts, write the binding for this runner, forge and stack, and leave the entry points red until every gate is bound or accepted as none.
disable-model-invocation: true
---

# Adopt the Ratchet method

Load the `ratchet:method` skill first; this skill assumes its vocabulary. Templates live in `${CLAUDE_SKILL_DIR}/templates/` and mirror the repository layout; the `justfile` and `.github/` among them are the `just` and GitHub examples.

## Steps

1. **Check the ground.** The repository is a git repository with a default branch; `claude plugin list` shows `ratchet`, `mattpocock-skills`, `pstack` and `impeccable`; `python3` (3.11 or later) is installed for the plugin's own gates. Done when all hold or you have told the owner which is missing.

2. **Initialise.** Run `/ratchet:init` unless `AGENTS.md` already opens with its four-plugin stanza. Done when `AGENTS.md` exists and `CLAUDE.md` links to it.

3. **Copy the templates.** Copy every file under `templates/` to the same path in the repository, skipping any that exists and listing the skipped ones for the owner. Done when the listing of `templates/` and the repository agree, and the skip list is reported.

4. **Fill `AGENTS.md`.** Through the interactive question dialog, one dialog per topic: the declared paths or layers, if any, with their allowed direction; the non-negotiables, each with the ADR that will hold it and the gate that will enforce it; the stack rules the method leaves open. Delete the Layers section when the project declares none. Done when `AGENTS.md` has no angle-bracket placeholder left.

5. **Choose the runner and the forge.** Fill the binding's Runner and Forge sections. A runner other than `just`: delete the `justfile`, create the same entry points in that runner, and rewrite the Entry points table. A forge other than GitHub: move the pull request template to the path the forge reads and name it in the binding, replace `.github/workflows/` with the forge's CI running `tier0`, `tier1` and `tier2`, and put those paths in the manifest's `ci` group. Done when every entry point names a command and the Forge section has no placeholder.

6. **Write the binding.** For each question row of the Gates table, name the tool for this stack, the threshold source and the entry point. A question with no tool in this stack gets `none`, the reason, and the number of the ADR that accepts the gap; write that ADR. Fill Absolute paths and Limits. Done when every Tool cell is filled.

7. **Bind the entry points, tier 0 first.** The method's own gates arrive bound to the plugin's scripts (`markers`, `suppressions`, `prose`, `snapshots`, `snapshots-check`, `commits`, `change-size`, `ratchet-lowered`, `settings-check`, `guard-diff`, `conformance`, `budget`, the tool-neutral half of `pins`, the shipped half of `bite`, and `coverage` once the suite writes a Cobertura report); `python3 "$RATCHET_PLUGIN/scripts/bite.py"` shows each of them bite. Replace each remaining `bind:` body with the real command; a `none` row prints its reason and exits 0. Every bound entry point runs green on the skeleton before you move to the next. Tier 0, then tier 1, then tier 2. Done when `tier0` is green and every remaining `bind:` body is listed for the owner with its tier.

8. **Fill the manifest, then generate the permission rules.** Add this stack's tool-config, fitness-test and gate-tool paths to `guards/manifest.toml` so no group is left with an empty `paths` (conformance reports one). Then run `just settings`, which rewrites the `deny` and `ask` lists of `.claude/settings.json` from the finished manifest (one `Edit` rule per pattern, `deny` for a locked group, plus the fixed command rules) and keeps every other key; never edit those two lists by hand. Done when `just settings-check` is green. The path rules of the guard hook come from the manifest as committed on the default branch, so none of this provokes a prompt until the adoption merges.

9. **Records.** Run `mattpocock-skills:grill-with-docs` for the design brief: purpose, shape, slice one as checkable items, the explicit out-list. It writes ADRs and glossary entries as decisions crystallise; record the agreed brief in `docs/design-brief.md`, dated. When the repository has a user interface, run `/impeccable document` so `DESIGN.md` records the design system the reviews judge against. Fill the version and URL in `docs/adr/0001-adopt-the-ratchet-method.md`. Done when the brief is dated and ADR 0001 names the plugin version.

10. **CI.** Pin every action or image to a commit SHA or digest with the version in a trailing comment; add the toolchain steps the binding needs. Done when the CI config parses.

11. **Speed.** The CI templates arrive cached, split one job per gate and cancelling superseded runs; keep that shape when you bind them. Fill each `bind:` cache line with the dependency directory and the tool caches of this stack, keyed on its lockfiles and tool configs. Run `just budget tier0 --set` and `just budget tier1 --set` once on the skeleton, which records the first ceilings in `quality/thresholds.toml` with headroom; they only fall from here. Name in the binding's Speed section the ceilings, the cache keys, the shard counts for any suite over the budget you set there, and the affected-only tool if the repository is a monorepo. Done when the Speed section has no placeholder and `[ceilings.seconds]` has a key per tier.

12. **Conformance.** Run the `conformance` entry point. Fix each failure it names. Done when it exits 0.

13. **Hand over.** Commit on a branch as `chore(method): adopt the ratchet method` (or the convention the binding names), push, and open a pull request with every template section filled, the `tier0` tail under Gates, and the list of unbound entry points and `none` rows under Criticism as standing criticisms of the binding. The owner merges.

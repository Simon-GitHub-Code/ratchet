---
name: adopt
description: Bring a repository onto the Ratchet method: copy the method's artefacts, interview the owner for the binding, and leave the repository red until every gate is bound.
disable-model-invocation: true
---

# Adopt the Ratchet method

Load the `ratchet:method` skill first; this skill assumes its vocabulary. Templates live in `${CLAUDE_SKILL_DIR}/templates/` and mirror the repository layout.

## Steps

1. **Check the ground.** The repository is a git repository with a `main` branch and a remote; `claude plugin list` shows `ratchet`; `just` and `python3` (3.11 or later) are installed. Done when all four hold or you have told the owner which is missing.

2. **Copy the templates.** Copy every file under `templates/` to the same path in the repository, skipping any that exists and listing the skipped ones for the owner. Done when the listing of `templates/` and the repository agree, and the skip list is reported.

3. **Interview the owner for `CLAUDE.md`.** Through the interactive question dialog, one dialog per topic: the one-paragraph purpose; the four layer paths; the non-negotiables (each with the ADR that will hold it and the gate that will enforce it); the stack rules the method leaves open. Replace every `<placeholder>`. Done when `CLAUDE.md` has no angle-bracket placeholder left.

4. **Write the binding.** For each row of `docs/process/binding.md`, name the tool for this stack, the threshold source and the recipe. A gate with no tool in this stack gets `none` and the reason. Done when every Tool cell is filled.

5. **Bind the recipes, tier 0 first.** Replace each `bind:` body in the `justfile` with the real command. Every bound recipe runs green on the skeleton before you move to the next. Tier 0, then tier 1, then tier 2. Done when `just tier0` is green and every remaining `bind:` body is listed for the owner with its tier.

6. **Fill the manifest.** Add this stack's tool-config, fitness-test and gate-tool paths to `guards/manifest.toml`, and mirror every pattern as an `ask` rule in `.claude/settings.json` (locked groups as `deny`). Done when every manifest pattern has a permission rule.

7. **Records.** Interview the owner for `docs/design-brief.md` (purpose, shape, slice one as checkable items, the explicit out-list) and the first glossary clusters. Fill the version and URL in `docs/adr/0001-adopt-the-ratchet-method.md`. Done when the brief is dated and ADR 0001 names the plugin version.

8. **CI.** Pin every action to a commit SHA with the version in a trailing comment; add the toolchain steps the binding needs. Done when the three workflows parse.

9. **Conformance.** Run `just conformance`. Fix each failure it names. Done when it exits 0.

10. **Hand over.** Commit on a branch as `chore(method): adopt the ratchet method`, push, and open a pull request with every template section filled, the `just tier0` tail under Gates, and the list of unbound recipes under Criticism as standing criticisms of the binding. The owner merges.

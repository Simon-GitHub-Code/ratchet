---
status: accepted
---
# Permission rules are generated from the manifest and checked by a gate

The adopt template shipped `.claude/settings.json` by hand beside `guards/manifest.toml`, and the two had drifted: the settings denied `gh label create`, which the hook allows for a recorded label, so an agent following `init`'s label step was refused by one layer and allowed by the other; `GLOSSARY-MAP.md` was a guard path with no rule; the fitness-test and gate-tool groups adopt fills had no rules at all, and nothing checked. We decided that the `deny` and `ask` lists of the project's settings are generated from the manifest and never written by hand: `scripts/settings_rules.py --write` (`just settings`) turns every pattern into one `Edit` rule, `deny` for a locked group and `ask` for the rest, and adds the fixed command rules the hook also enforces (`gh pr merge` as an ask; `gh label create` not denied). `--check` (`just settings-check`, tier 1, and a conformance check) fails on any rule missing from or foreign to the two lists; a project's own rules go in `allow`. A group's `exclude` has no rule equivalent, so the check refuses a manifest that uses it and the template lists the harness paths one by one instead of `.claude/**` minus the agent worktrees. Decided 2026-11-07.

## Refutation criterion

The decision is wrong if, in the first ten adopted repositories, owners need a `deny` or `ask` rule the generator cannot express more than twice (counted as settings-check disputes), or if the hook and the settings layer are found disagreeing on any call after adoption. Check when the tenth repository adopts, or at the second dispute, whichever is first.

## Considered options

- Keep both files hand-written and document the mirroring: rejected, that is the state that drifted; a rule nothing checks is the one that goes stale.
- Generate the manifest from the settings instead: rejected, the manifest carries the reason per group and the diff check and the hook already read it; the settings carry nothing the manifest does not.
- Mirror `.claude/**` with the worktrees excluded by enumerating worktree paths in `allow`: rejected, Claude Code evaluates `deny` before `allow`, so an allow cannot carve a path out of a deny.
- Let projects add their own deny and ask rules beside the generated ones: rejected for now, a foreign deny is exactly how `gh label create` came to contradict the hook; `allow` is open, and a needed exception is a dispute the refutation criterion counts.

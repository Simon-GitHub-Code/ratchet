---
status: accepted
---
# The binding says whether an install comes first

ADR 0039 made a recipe that runs a package manager's tool depend on an `install` recipe, and made `conformance` red on a tree that would fail in a fresh checkout. It found the case by the lockfile at the root and the recipe by a pattern over four package managers. This decision keeps the intent and drops the knowledge of the four. It supersedes ADR 0039 in part: its decision on the recipe's shape, on the tiers running `install` first and on rehearsing in a fresh worktree stands, and its detection, its fixed commands and its stamp path no longer exist.

## Decision

**The binding's `## Install` section says whether this stack needs an install first** (ADR 0040). It has three lines. `Install` is `just install`, or `none: <reason>` for a stack whose tools arrive on demand. `Lockfile` is the one file the install is frozen to. `Needed by` lists the recipes that run a tool the install provides. The agent that adopts writes them, as it chose the tools.

**`conformance` holds the justfile to the section, and knows no package manager.** With `just install` named, it is red when the lockfile is not a file, when a listed recipe is not defined, when the `install` recipe does not name the lockfile, when a listed recipe does not reach `install`, or when a tier does not run `install` directly. With `none` it asks nothing more. A section that is missing, still the template's, or says `none` with no reason is red. The structural findings stay on the owed ledger's `never` list for the `justfile` path, for the reason ADR 0037 gives: the recipes they name are red in the merge's work tree and in CI as they stand. A missing or malformed section can be owed, since no other gate reads it.

**The recipe's idempotence is the agent's.** The template says that `install` installs exactly what the named lockfile says and does nothing once that is installed, and the check that it names the lockfile is the part a gate can read. A stamp, a marker file or the package manager's own check each meet it.

**The upgrade writes the section where the justfile states it.** An `install` recipe that does nothing, that no recipe but a tier waits on, gives `none: the justfile's install recipe does nothing, and no recipe waits on it`. An `install` recipe with a body that names a tracked file gives that file as the lockfile and the recipes that list `install` first among their dependencies, less the tiers, as `Needed by`. Anything else is an agent's. The upgrade trial counts either edit only when the check no longer raises the finding on the edited tree.

## Residue

ADR 0039's check found a stack that needed an install by its lockfile. This one cannot: a section that says `none` for a stack that needs an install passes `conformance`. Two things hold that gap. The rehearsal of step 12 of the adopt skill runs in a fresh worktree with no setup by hand, so a recipe that needs an install fails there. `just merge` runs the same recipes in a fresh work tree on every merge. The weaker guarantee is that the first merge finds it, not the commit that wrote the section.

## Refutation criterion

This is wrong if an adopted project merges through `just merge` or passes its CI jobs, with `conformance` green and a section that names the right recipes, and a gate is red there for a missing tool an `install` first would have supplied. Check it on the next ten adoptions by reading their first `just merge`. It is also wrong if a project whose section says `none` goes red at its first merge on a missing tool more than once in those ten: then the section needs a signal the plugin can read without naming a tool, and the rehearsal needs to run in `check`.

## Considered options

- **Keep the lockfile detector and add managers.** Rejected by ADR 0040.
- **Have the section hold the install command and check it appears in the recipe.** Rejected: the command is the agent's text in the recipe already, and a second copy drifts. The lockfile it names is the one fact a gate can compare.
- **Require the section's recipes to be found by the plugin from the justfile.** Rejected: which recipes run an installed tool is a fact about the tools, not about the justfile.

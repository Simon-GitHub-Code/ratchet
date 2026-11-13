---
status: accepted
---
# A recipe that runs a package manager's tool installs it first

A first-hour eval run adopted a Python app with Biome and TypeScript through npm for its page. The adoption's `check-deep` was green in the working tree and in its own rehearsal. `just merge` then ran `check-deep` on the merge result in a fresh work tree (ADR 0021, ADR 0031), and `fmt-check` and `lint` went red with `npx canceled due to missing packages`, so the merge went to the owner and the run stalled. An older run on pnpm (`pnpm exec prettier|eslint`) had the same recipes. The Python recipes survived because `uv run --frozen` syncs `.venv` as it runs; `npx --no-install`, `pnpm exec`, `yarn` and `bunx` find nothing until an install has run, and no recipe ran one. CI is the same: each gate is a job in a fresh checkout.

Why the rehearsal passed: step 12 of adopt told the agent to make a worktree and "run the `setup` of `quality/bites.toml` in it", and that setup was `uv sync` and `npm ci`. The rehearsal installed by hand what the recipes lacked, so it ran where the recipes were green. The bite proof does the same by design. The one place nothing was installed first was the merge.

## Decision

**A recipe that runs a tool from a package manager that does not install on demand depends on an `install` recipe.** `install` installs from the lockfile (`npm ci`, `pnpm install --frozen-lockfile`, `yarn install --immutable` or `--frozen-lockfile`, `bun install --frozen-lockfile`) and does nothing once the lockfile is installed: a stamp, the lockfile copied to `node_modules/.ratchet-install` after a successful install, so deleting `node_modules` reinstalls. The rule is the method's and no preset's; the template `justfile` carries an `install` recipe that does nothing, for a stack that installs on demand, and says where to bind it.

**Every tier runs `install` right after `plugin-pin`.** Each gate is its own `just` process, so with the dependency on the gates alone, a tier that starts fifteen of them at once would run fifteen installs over one `node_modules`. The tier installs once; the gates' own dependency finds the stamp and does nothing, and a single gate run alone in CI installs for itself.

**`conformance` is red on a tree that would fail in a fresh checkout** (`check_installs`). With a lockfile of npm, pnpm, yarn or bun at the root, it is red when the justfile has no `install` recipe that runs the frozen install, when a recipe that runs `npx`, `pnpm exec|run`, `yarn <script>`, `bunx`, `npm run|exec|test`, `bun run|x` or `node_modules/.bin/` (following the variables its lines interpolate) does not reach `install`, or when a tier does not run it directly. The self-test plants each of the four faults on the template.

**`upgrade.py` writes the dependency where it can derive it.** The finding carries an edit: the `install` recipe for the lockfile found, `install` first among the dependencies of each recipe that runs a tool, and after `plugin-pin` in each tier. The upgrade trial (ADR 0029) counts the edit only when the check no longer raises the finding on the edited tree; otherwise an agent fixes it. The finding is on the `never` list of ADR 0037: the recipes it names are red in the merge's work tree and in CI whatever the ledger says, so an owed entry would print green over a gate that is red.

**Step 12 rehearses where the merge runs.** The rehearsal and the final `check` run in a fresh worktree of the adoption's commit with no setup by hand; the first command is a recipe. A recipe that fails there for a missing tool lacks its `install` dependency, which is an edit to the adoption. A bite's `setup` is `just install` where the recipe exists, so there is one copy of the command.

The CI template is unchanged: a job runs `just <gate>`, and the gate installs for itself.

## Refutation criterion

This is wrong if an adopted JavaScript project merges through `just merge` or passes its CI jobs, with `conformance` green, and a gate is red there for a missing tool that an `install` first would have supplied: then the rule misses a way a recipe runs a tool, and the patterns of `check_installs` need it. It is also wrong if the stamp lets a stale `node_modules` through: a lockfile change that leaves a gate judging the old tools. Check the first on the next ten adoptions that use a JavaScript tool, by reading their first `just merge`; check the second by changing a pinned version and running a gate once.

## Considered options

- **Prose in the adopt skill alone.** Rejected: step 9 already said "install each tool through the package manager", and the rehearsal still passed. A rule an agent can satisfy by installing by hand is not a rule about the recipes.
- **Have `merge.py` install before it runs `check`.** Rejected: it would make the merge green where CI is red, and it cannot know the stack's installer. The recipes are what CI runs, so they carry the install.
- **Each gate runs the installer itself with no stamp.** Rejected: `npm ci` deletes `node_modules`, so a tier running its gates at once breaks the gates that are running.
- **A tier-only dependency.** Rejected: a gate that runs alone as a CI job would not install.
- **An owed entry for the upgrade to leave.** Rejected for the reason above; the finding goes to an agent as an item that stays red until fixed.

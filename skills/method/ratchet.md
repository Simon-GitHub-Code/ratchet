# The ratchet

Read this before you change a threshold, a ceiling, a listed config or the mutant register. A stored number is one more thing an agent can edit to pass, so six rules make loosening visible, enforced by gate tools that are themselves absolute paths: typed, 100% covered and mutation-tested.

## Steps

### Raise the thresholds a change earned

1. **Run `ratchet`.** It runs the suites and raises each value to the measured one, never lowering one. It reads the last full mutation runs for the scores and refreshes the snapshots.
2. **Commit the result with the change that earned it.**

Done when the values `ratchet` raised and the snapshots it refreshed are committed in the change that earned them.

### Change a listed config

1. **Run `snapshots` after the intended change.** It rewrites the copies in `quality/snapshots/`.
2. **Commit the copies with the change.** Their diff is reviewed with the pull request.

Done when the pull request holds the config change and its rewritten copy.

### Answer a surviving mutant

- **A test can kill it:** write that test.
- **It is equivalent:** the plugin's `scripts/mutation_survivors.py` prints the entry to paste for every unregistered survivor. Paste this one's into `quality/mutation-register.toml`, with a reason of twenty characters or more.
- **It timed out and the register holds an entry for it:** rerun it with less load. If it times out again on its own, it is killed: remove the entry.

Done when every surviving mutant is killed by a test or has an entry of its own with its reason, and the mutation gate reports no registered mutant as timed out.

## 1. Thresholds only rise

`quality/thresholds.toml` holds:

- a line and a branch coverage value per ratcheted path group;
- the mutation score per language, for the trend;
- any other ratcheted percentage the binding names, such as type coverage and documentation coverage.

```toml
[<language>.coverage.<group>]
paths = ["src/<group>/**"]
line = 93.4
branch = 88.0

[<language>.coverage.core]
paths = ["src/core/**"]
absolute = true

[<language>.mutation]
score = 99.8

[<language>.types]
coverage = 97.1
```

- The `ratchet-lowered` entry point (tier 0) flattens the file to dotted keys at the merge base and at head. It fails on any key that is lower or missing.
- Relaxing a value is an owner's change with an ADR behind it. The ADR names each loosening on a line of its own, `Relaxes: <key> from <old> to <new>`, with `removed` for a dropped key and `true` and `false` for a flag such as `absolute`. `ratchet-lowered` passes exactly the loosenings an ADR names, and prints which ADR.
- An agent may write a new ADR, never one with this line. A record that carries `Relaxes:` is no addition, so the merge judge sends the change to the owner and the guard refuses it on the default branch: the line is the owner's ([guards.md](guards.md)).

### The owed ledger

`quality/owed.toml` holds what the project owes since an upgrade: the findings a new plugin version raised that the upgrade could not settle itself. The upgrade writes the file, and nobody edits it by hand (plugin ADR 0037).

- The conformance gate counts each entry as owed and is not red on it.
- An entry excuses one finding: the check, the file and the problem it names. No entry excuses a finding another gate judges, such as the permission rules or the thresholds.
- Between upgrades the list only falls. An entry goes once its finding is fixed (`upgrade.py --apply` drops it), and a change that only drops entries needs no owner at the merge.
- `ratchet-lowered` fails a change that adds or alters an entry, unless the same change moves the plugin pin to a newer version and the merge base was already red on that finding.

## 2. Absolute paths

The gate tools, and every path the binding declares pure, need 100% line and branch coverage. This is a rule in the coverage tool, not a number in the file. A fitness test forbids any coverage exclusion, pragma or glob that reaches an absolute path. A measured file that falls in no declared group fails the check, so new code cannot land outside the rules.

## 3. Counts are computed, never stored

On every run, the gate counts at the merge base and at head:

- suppressions per kind: lint ignores, type ignores, coverage and mutation pragmas, test skips and expected failures, lowered property-test example counts;
- marker comments: TODO, FIXME, HACK and their cousins;
- the number of collected tests per suite.

No suppression kind may rise. Test counts may not fall, and deleted test files are named. A focus marker, one that narrows a suite to the part somebody was looking at, is banned outright. The binding's Suppressions section lists the directives and markers of its stack (plugin ADR 0042).

## 4. Mutation gates on zero unexplained survivors

The score is printed for the trend and does not gate. Every surviving mutant is killed by a test or listed in `quality/mutation-register.toml`:

```toml
[[survivor]]
tool = "<mutation tool>"
identity = "<mutant name as the tool prints it>"
function_hash = "sha256:..."
reason = "Equivalent: the branch rounds a value that is already integral here."
date = 2026-11-06
```

- **The identity is stable.** Tools number mutants per run, so it is the file, the mutator, the replacement and the mutant's place in its unit, `@<line>:<column>-<line>:<column>` counted from the unit's first line, plus a hash of the unit. An edit elsewhere in the file leaves the entry valid; an edit inside the unit changes the hash. The plugin's `scripts/mutation_survivors.py` computes both.
- **Any mutation tool feeds the gate.** Its report is converted to one JSON line per mutant: the file, the mutator, the replacement, the status, the start and end of the mutated text, and the lines of the enclosing unit when the tool gives them. The converter is a few lines the repository keeps as a gate tool, and the script's docstring gives the format and a worked one.
- **An entry excuses one mutant.** Entries and live survivors match one to one: a register with fewer entries than live survivors is red and names the unmatched ones. So a second survivor added to a function with a registered one needs an entry of its own.
- **The reason has twenty characters or more.** Its honesty is for the owner's review: the register is a guard path.
- **A stale entry fails.** An entry is stale when its mutant is now killed or gone, or its function hash changed, so editing a function forces its equivalents to be reviewed again.
- **A timeout counts as a kill**, as mutation tools count it, unless the register holds an entry for the mutant. Then the run cannot tell it from a survivor, and the gate is red and says so.
- **An entry without the `@` suffix** is from a register written before the place was part of the identity. Each is red until replaced: the gate names it as the old identity, lists the live mutants it matched, and prints an entry to paste for each unregistered survivor. The owner reviews them and writes one entry per mutant they judge equivalent, with a reason of their own. Nothing carries over unreviewed.
- **Scope.** A partial run judges only its scope. Pull requests mutate changed functions (tier 1). The default branch mutates everything on the absolute paths (tier 2).

## 5. Listed configuration is snapshotted

`quality/snapshots/` holds a copy of each file the `tool-config` group of `guards/manifest.toml` lists. The `snapshots-check` entry point (tier 0) copies them again and diffs. Loosening a listed config therefore shows as a reviewed diff instead of a silent config edit, and a config the group does not list is neither guarded nor recorded: an agent can loosen it and no gate sees it. Adoption lists each config it writes. After adoption `/ratchet:safeguard` adds a new tool's config path to the manifest on its branch, and the path is guarded once the owner merges that change.

## 6. Ceilings only fall

`[ceilings.seconds]` in `quality/thresholds.toml` holds the wall-clock ceiling of each tier, and of any gate with its own runner.

- The `budget` entry point wraps the run and goes red over the ceiling. That blocks everywhere but on a pull request, where the runner varies and CI asks it to report (`--over report`).
- `ratchet-lowered` fails a raised ceiling exactly as it fails a lowered threshold.
- `budget --set` records the first ceiling from a measured run with headroom, no lower than the tier's budget, and afterwards only lowers it.
- A ceiling is paid for by caching, splitting, sharding or running less, never by a quarantined test. Read [tiers.md](tiers.md#speed) for how.

## Limits versus ratchets

A ratchet that only rises cannot hold a ceiling, so the file carries both directions: thresholds rise, ceilings fall, and the gate knows which is which by the `ceilings` table. Size and complexity are hard limits rather than either, tighter on absolute paths, held in the lint config and in a fitness test, with the numbers in the binding. Split the unit rather than raise the limit.

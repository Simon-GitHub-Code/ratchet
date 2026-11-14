# The ratchet

A stored number is one more thing an agent can edit to pass. The ratchet has six rules that make loosening visible, enforced by gate tools that are themselves absolute paths: typed, 100% covered and mutation-tested.

## 1. Thresholds only rise

`quality/thresholds.toml` holds a line and a branch coverage value per ratcheted path group, the mutation score per language for the trend, and any other ratcheted percentage the binding names, such as type coverage and documentation coverage:

```toml
[<language>.coverage.<group>]
line = 93.4
branch = 88.0

[<language>.mutation]
score = 99.8

[<language>.types]
coverage = 97.1
```

- The `ratchet-lowered` entry point (tier 0) flattens the file to dotted keys at the merge base and at head and fails on any key that is lower or missing.
- The `ratchet` entry point runs the suites, raises each value to the measured one (never lowers), reads the last full mutation runs for the scores, refreshes the snapshots. Commit the result with the change that earned it.
- Relaxing a value is an owner's change with an ADR behind it. The ADR names each loosening on a line of its own, `Relaxes: <key> from <old> to <new>` (`removed` for a dropped key, `true` and `false` for a flag such as `absolute`), and `ratchet-lowered` passes exactly the loosenings an ADR names and prints which ADR. An agent may write a new ADR, never one with this line: a record that carries `Relaxes:` is no addition, so the merge judge sends the change to the owner and the guard refuses it on the default branch: the line is the owner's ([guards.md](guards.md)).
- `quality/owed.toml` holds what the project owes since an upgrade: the findings a new plugin version raised that the upgrade could not settle itself. The conformance gate counts each as owed and is not red on it. An entry excuses one finding: the check, the file and the problem it names. Between upgrades the list only falls. An entry goes once its finding is fixed (`upgrade.py --apply` drops it), a change that only drops entries needs no owner at the merge, and `ratchet-lowered` fails a change that adds or alters an entry unless the same change moves the plugin pin to a newer version and the merge base was already red on that finding. No entry excuses a finding another gate judges, such as the permission rules or the thresholds. The upgrade writes the file, and nobody edits it by hand (plugin ADR 0037).

## 2. Absolute paths

The gate tools, and every path the binding declares pure, need 100% line and branch coverage. This is a rule in the coverage tool, not a number in the file. A fitness test forbids any coverage exclusion, pragma or glob that reaches an absolute path. A measured file that falls in no declared group fails the check, so new code cannot land outside the rules.

## 3. Counts are computed, never stored

On every run, the gate counts at the merge base and at head:

- suppressions per kind: lint ignores, type ignores, coverage and mutation pragmas, test skips and expected failures, lowered property-test example counts;
- marker comments: TODO, FIXME, HACK and their cousins;
- the number of collected tests per suite.

No suppression kind may rise. Test counts may not fall, and deleted test files are named. A focus marker, one that narrows a suite to the part somebody was looking at, is banned outright; the binding's Suppressions section lists the directives and markers of its stack (ADR 0040). There is no number to edit, no slack to spend and no merge conflict.

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

- The identity is stable: the mutant's name plus a hash of its enclosing function. Where the tool numbers mutants per run, as most do, it is the file, the mutator, the replacement and the mutant's place in its unit, `@<line>:<column>-<line>:<column>` counted from the unit's first line, plus a hash of the unit. An edit elsewhere in the file leaves the entry valid; an edit inside the unit changes the hash. The plugin's `scripts/mutation_survivors.py` computes both and prints the entry to paste for every unregistered survivor; a partial run judges only its scope.
- Any mutation tool feeds the gate. Its report is converted to one JSON line per mutant: the file, the mutator, the replacement, the status, the start and end of the mutated text, and the lines of the enclosing unit when the tool gives them. The converter is a few lines the repository keeps as a gate tool, and the script's docstring gives the format and a worked one. The reports of mutmut and Stryker are read as they are.
- An entry excuses one mutant. Entries and live survivors match one to one: a register with fewer entries than live survivors is red and names the unmatched ones, so a second survivor added to a function with a registered one needs an entry of its own.
- The reason has twenty characters or more. The honesty of the reason is for the owner's review; the register is a guard path.
- An entry whose mutant is now killed, gone, or whose function hash changed is stale and fails. Editing a function forces its equivalents to be reviewed again.
- A mutant that timed out counts as killed, as mutation tools count it, unless the register holds an entry for it. Then the run cannot tell it from a survivor: the gate is red and says so, and the answer is to rerun it with less load. If it times out again on its own, it is killed: remove the entry.
- Registers written before the place was part of the identity hold stryker entries without the `@` suffix. Each is red until replaced: the gate names it as the old identity, lists the live mutants it matched, and prints an entry to paste for each unregistered survivor. The owner reviews them and writes one entry per mutant they judge equivalent, with a reason of their own. Nothing carries over unreviewed. Entries for mutmut keep their identity.
- Pull requests mutate changed functions (tier 1). The default branch mutates everything on the absolute paths (tier 2).

## 5. Listed configuration is snapshotted

`quality/snapshots/` holds a copy of each file the `tool-config` group of `guards/manifest.toml` lists. The `snapshots-check` entry point (tier 0) copies them again and diffs. After an intended change, the `snapshots` entry point rewrites them and the diff is reviewed with the pull request. Loosening a listed config therefore shows as a reviewed diff instead of a silent config edit, and a config the group does not list is neither guarded nor recorded: an agent can loosen it and no gate sees it. Adoption lists each config it writes; after adoption the manifest is the owner's, so `/ratchet:safeguard` prints the lines for him to add.

## 6. Ceilings only fall

`[ceilings.seconds]` in the same file holds the wall-clock ceiling of each tier, and of any gate with its own runner. The `budget` entry point wraps the run and goes red over the ceiling, which blocks everywhere but on a pull request, where the runner varies and CI asks it to report (`--over report`); `ratchet-lowered` fails a raised ceiling exactly as it fails a lowered threshold; `budget --set` records the first ceiling from a measured run with headroom, no lower than the tier's budget, and afterwards only lowers it. A ceiling is paid for by caching, splitting, sharding or running less, never by a quarantined test. [tiers.md](tiers.md#speed)

## Limits versus ratchets

A ratchet that only rises cannot hold a ceiling, so the file carries both directions: thresholds rise, ceilings fall, and the gate knows which is which by the `ceilings` table. Size and complexity are hard limits rather than either, tighter on absolute paths, held in the lint config and in a fitness test. Split the unit rather than raise the limit. The binding holds the numbers.

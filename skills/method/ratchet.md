# The ratchet

A stored number is one more thing an agent can edit to pass. The ratchet has five rules that make loosening visible, and they are enforced by gate tools that are themselves typed, 100% covered and mutation-tested.

## 1. Thresholds only rise

`quality/thresholds.toml` holds a line and a branch coverage value per ratcheted layer, and the mutation score per language for the trend:

```toml
[<language>.coverage.<layer>]
line = 93.4
branch = 88.0

[<language>.mutation]
score = 99.8
```

- `just ratchet-lowered` (tier 0) flattens the file to dotted keys at the merge base and at head and fails on any key that is lower or missing.
- `just ratchet` runs the suites, raises each value to the measured one (never lowers), reads the last full mutation runs for the scores, refreshes the snapshots. Commit the result with the change that earned it.
- Relaxing a value is an owner's change with an ADR behind it.

## 2. Absolute layers

The pure core, the application layer and the gate tools need 100% line and branch coverage. This is a rule in the coverage tool, not a number in the file. A fitness test forbids any coverage exclusion, pragma or glob that reaches an absolute layer. A measured file that falls in no layer fails the check, so new code cannot land outside the rules.

## 3. Counts are computed, never stored

On every run, the gate counts at the merge base and at head:

- suppressions per kind: lint ignores, type ignores, coverage and mutation pragmas, test skips and expected failures, lowered property-test example counts;
- the number of collected tests per suite.

No suppression kind may rise. Test counts may not fall, and deleted test files are named. A focus marker (`.only` and its cousins) is banned outright. There is no number to edit, no slack to spend and no merge conflict.

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

- The identity is stable: the mutant's name plus a hash of its enclosing function (or file, unit, mutator and replacement where the tool has no function identity).
- The reason has twenty characters or more. The honesty of the reason is for the owner's review; the register is a guard path.
- An entry whose mutant is now killed, gone, or whose function hash changed is stale and fails. Editing a function forces its equivalents to be reviewed again.
- Pull requests mutate changed functions (tier 1). Main mutates everything in the absolute layers (tier 2).

## 5. Effective configuration is snapshotted

`quality/snapshots/` holds each gate's configuration as the tool resolves it (presets, inherited defaults and upgrades included), normalised and sorted. `just snapshots-check` (tier 0) regenerates and diffs. After an intended change, `just snapshots` rewrites them and the diff is reviewed with the pull request. Loosening a gate therefore shows as a reviewed diff instead of a silent config edit.

## Limits versus ratchets

A ratchet that only rises cannot hold a ceiling. Size and complexity are therefore hard limits, tighter in the pure core, held in the lint config and in a fitness test. Split the function rather than raise the limit. Default values are in [code.md](code.md).

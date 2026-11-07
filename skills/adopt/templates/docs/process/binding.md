# Binding

This repository's binding of the Ratchet method (`ratchet:method`; [tiers.md](../../../skills/method/tiers.md) lists the questions each tier answers). The binding names the runner, the forge, the tool behind each gate and the paths held absolute. CI runs the same entry points.

## Runner

<The task runner: `just`, `make`, `npm run`, `cargo xtask`, a script directory. The entry points below are its targets. The `justfile` shipped with the templates is the `just` example; delete it when the runner is something else.>

## Forge

- Default branch: `main`
- Pull request template: `.github/pull_request_template.md`
- Change flow: <one reviewed change at a time; the commit convention, if any, and its hook>

## Entry points

Every entry point is one command that exits non-zero on any failure. `check` is tier 0 plus tier 1; `check-deep` adds tier 2.

| Entry point | Command |
| --- | --- |
| tier0 | `just tier0` |
| tier1 | `just tier1` |
| tier2 | `just tier2` |
| check | `just check` |
| check-deep | `just check-deep` |
| ratchet | `just ratchet` |
| ratchet-lowered | `just ratchet-lowered` |
| snapshots | `just snapshots` |
| snapshots-check | `just snapshots-check` |
| suppressions | `just suppressions` |
| guard-diff | `just guard-diff` |
| conformance | `just conformance` |
| reports | `just reports` |

## Gates

One row per question the method asks. The Tool cell names the tool for this stack, or `none` with the reason and the ADR that accepts the gap; an accepted `none` is green and stays a standing criticism of the binding. An empty Tool cell is an unfinished binding.

| Question | Tool | Tier | Threshold source | Entry point |
| --- | --- | --- | --- | --- |
| format | | 0 | | `just fmt-check` |
| lint, near-all rules | | 0 | | `just lint` |
| strict types | | 0 | | `just types` |
| dependency direction | | 0 | | `just imports` |
| dead code and dependency hygiene | | 0 | | `just deadcode` |
| scripts and CI lint | | 0 | | `just lint-workflows` |
| secrets | | 0 | | `just secrets` |
| effective-config snapshots | | 0 | `quality/snapshots/` | `just snapshots-check` |
| no threshold lowered | `ratchet` plugin `scripts/lowered.py` | 0 | `quality/thresholds.toml` at the merge base | `just ratchet-lowered` |
| no suppression kind rose, no focus marker | | 0 | the merge base's own counts | `just suppressions` |
| unit, property and fitness tests | | 1 | | `just test` |
| coverage, absolute and ratcheted | | 1 | 100% for absolute paths; `quality/thresholds.toml` for the rest | `just coverage` |
| diff coverage | | 1 | every changed line and branch | `just diff-coverage` |
| test count did not fall | | 1 | the merge base's count | `just test-counts` |
| changed-code mutation | | 1 | `quality/mutation-register.toml` | `just mutation-changed` |
| contract drift | | 1 | | `just contracts-check` |
| shipped-dependency audit | | 1 | any advisory fails | `just audit` |
| duplication | | 1 | | `just duplication` |
| build | | 1 | | `just build` |
| guard paths changed | `ratchet` plugin `ratchet_guards.diff` | 1 | `guards/manifest.toml` | `just guard-diff` |
| method conformance | `ratchet` plugin `scripts/conformance.py` | 1 | | `just conformance` |
| full mutation | | 2 | `quality/mutation-register.toml` | `just mutation` |
| deep interface conformance | | 2 | | `just api-deep` |
| symbolic checking | | 2 | | `just symbolic` |
| fuzzing | | 2 | `FUZZ_SECONDS` | `just fuzz` |
| end to end | | 2 | | `just e2e` |
| formal verification | | 2 | the project-local verification skill | `just formal` |

## Absolute paths

<The paths held at 100% line and branch coverage with no exclusion: the gate tools always, plus whatever the owner declares pure.>

## Limits

<The size and complexity limits the lint config and a fitness test hold, per path group. Hard limits: split the unit rather than raise one.>

## Advisory reports

`just reports` prints hotspots, coupling and complexity. They inform and never gate.

## Tool versions

<Where versions are pinned: lockfiles, and the checksum-pinned binaries outside them.>

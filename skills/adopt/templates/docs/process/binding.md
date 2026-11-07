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
| commits | `just commits` |
| suppressions | `just suppressions` |
| guard-diff | `just guard-diff` |
| settings-check | `just settings-check` |
| conformance | `just conformance` |
| budget | `just budget tier1` |
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
| pins and least privilege | `ratchet` plugin `scripts/pins.py` plus the lockfile check | 0 | | `just pins` |
| size and complexity limits | | 0 | the Limits section below | `just limits` |
| marker comments did not rise | `ratchet` plugin `scripts/markers.py` | 0 | the merge base's own counts | `just markers` |
| prose vocabulary | `ratchet` plugin `scripts/prose.py` | 0 | `GLOSSARY.md` | `just prose` |
| effective-config snapshots | `ratchet` plugin `scripts/snapshots.py` | 0 | `quality/snapshots/` | `just snapshots-check` |
| no threshold lowered | `ratchet` plugin `scripts/lowered.py` | 0 | `quality/thresholds.toml` at the merge base | `just ratchet-lowered` |
| no suppression kind rose, no focus marker | `ratchet` plugin `scripts/suppressions.py` | 0 | the merge base's own counts | `just suppressions` |
| commit subjects follow the convention | `ratchet` plugin `scripts/commits.py` | 0 | | `just commits` |
| unit, property and fitness tests | | 1 | | `just test` |
| coverage, absolute and ratcheted | `ratchet` plugin `scripts/coverage_groups.py` over the suite's Cobertura report | 1 | 100% for absolute paths; `quality/thresholds.toml` for the rest | `just coverage` |
| diff coverage | | 1 | every changed line and branch | `just diff-coverage` |
| test count did not fall | | 1 | the merge base's count | `just test-counts` |
| changed-code mutation | | 1 | `quality/mutation-register.toml` | `just mutation-changed` |
| contract drift | | 1 | | `just contracts-check` |
| shipped-dependency audit | | 1 | any advisory fails | `just audit` |
| duplication | | 1 | | `just duplication` |
| build | | 1 | | `just build` |
| starts and answers | | 1 | | `just smoke` |
| every checker bites | `ratchet` plugin `scripts/bite.py` plus the project's own | 1 | | `just bite` |
| public surface changed on purpose | | 1 | `quality/snapshots/` | `just api-check` |
| old data still loads | | 1 | | `just data-compat` |
| documentation examples run | | 1 | | `just doctests` |
| static security analysis | | 1 | | `just sast` |
| artefact and image scan | | 1 | | `just artefact-scan` |
| type coverage, ratcheted | | 1 | `quality/thresholds.toml` | `just type-coverage` |
| documentation coverage, ratcheted | | 1 | `quality/thresholds.toml` | `just doc-coverage` |
| change size | `ratchet` plugin `scripts/change_size.py` | 1 | `quality/thresholds.toml` `[ceilings.change]` | `just change-size` |
| guard paths changed | `ratchet` plugin `ratchet_guards.diff` | 1 | `guards/manifest.toml` | `just guard-diff` |
| permission rules mirror the manifest | `ratchet` plugin `scripts/settings_rules.py` | 1 | `guards/manifest.toml` | `just settings-check` |
| method conformance | `ratchet` plugin `scripts/conformance.py` | 1 | | `just conformance` |
| full mutation | | 2 | `quality/mutation-register.toml` | `just mutation` |
| deep interface conformance | | 2 | | `just api-deep` |
| symbolic checking | | 2 | | `just symbolic` |
| fuzzing | | 2 | `FUZZ_SECONDS` | `just fuzz` |
| end to end | | 2 | | `just e2e` |
| suite order-independent and repeatable | | 2 | | `just test-stability` |
| reproducible build | | 2 | | `just reproducible` |
| race and memory detectors | | 2 | | `just sanitizers` |
| performance budget | | 2 | the Limits section below | `just perf` |
| dynamic security scan | | 2 | | `just dast` |
| accessibility | | 2 | | `just a11y` |
| formal verification | | 2 | the project-local verification skill | `just formal` |

## Absolute paths

<The paths held at 100% line and branch coverage with no exclusion: the gate tools always, plus whatever the owner declares pure.>

## Limits

<The ceilings the gates hold, per path group where they differ: size and complexity (cognitive and cyclomatic complexity, nesting depth, unit and file length); change size per pull request; performance (latency, allocation, bundle size). Hard limits: split the unit rather than raise one.>

## Speed

<The ceilings: `quality/thresholds.toml` `[ceilings.seconds]`, one per tier, set by `just budget <tier> --set` from the first green run and only lowered since. The cache keys: the lockfile(s) and tool configs each CI job's cache is keyed on, and the tool caches it restores. The shard counts: each suite over the budget below, its tool and its runner count. The affected-only tool, if any, and the path filters. The budget: the seconds a single gate may take before it is sharded.>

## Weekly and advisory

`just audit-all`, `just licenses` and `just drift` run weekly and block nothing; a finding gets its own pull request. `just reports` prints hotspots, coupling, cohesion and complexity. They inform and never gate.

## Tool versions

<Where versions are pinned: lockfiles, and the checksum-pinned binaries outside them.>

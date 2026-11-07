# Binding

This repository's binding of the Ratchet method (`ratchet:method`, [tiers.md](../../../skills/method/tiers.md) lists the gates). The root `justfile` is the command contract; CI runs the same recipes. `just check` is tier 0 plus tier 1; `just check-deep` adds tier 2.

| Tier | When | Local | CI |
| --- | --- | --- | --- |
| 0 | every commit | `just tier0`, and the pre-commit hooks | `ci.yml` |
| 1 | every pull request | `just tier1` | `ci.yml` |
| 2 | merge to main, nightly, on demand | `just tier2` | `deep.yml` |

## Gates

A row per gate the method names. `none` in the Tool column is a standing criticism of this binding; give the reason.

| Gate | Tool | Tier | Threshold source | Recipe |
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
| coverage, absolute and ratcheted | | 1 | 100% for absolute layers; `quality/thresholds.toml` for the rest | `just coverage` |
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
| formal specs and traces | | 2 | `formal/specs/` | `just formal`, `just traces` |

## Absolute layers

<The paths held at 100% line and branch coverage: the core, the application layer, the gate tools.>

## Advisory reports

`just reports` prints hotspots, coupling and complexity. They inform and never gate.

## Tool versions

<Where versions are pinned: lockfiles, and the checksum-pinned binaries outside them.>

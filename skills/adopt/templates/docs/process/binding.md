# Binding

This repository's binding of the Ratchet method (`ratchet:method`; `skills/method/tiers.md` in the `ratchet` plugin lists the questions each tier answers). The binding names the runner, the forge, the tool behind each gate and the paths held absolute. CI runs the same entry points.

## Runner

<The task runner: `just`, `make`, `npm run`, `cargo xtask`, a script directory. The entry points below are its targets. The `justfile` shipped with the templates is the `just` example; delete it when the runner is something else.>

- Ratchet plugin: <X.Y.Z> at <ratchet-sha>

The plugin version has this one home. Every workflow checks the plugin out at this commit, and a local tier is red when the plugin it runs from is at another (`plugin-pin`). To upgrade, change this line and every workflow `ref` to the new commit in one change.

## Forge

- Default branch: `main`
- Pull request template: `.github/pull_request_template.md`
- Change flow: <one reviewed change at a time; the commit convention, if any, and its hook>
- Commit hooks: `.pre-commit-config.yaml` (the `pre-commit` framework, the template's example. Another mechanism, such as lefthook, husky or a directory of plain scripts, names its own file or directory here, then `, installed by` and its install command in backticks. `hooks-installed` proves either by running the hooks.)
- Merge: the merge judge decides (`merge-ready`). The `merge` workflow merges a pull request the judge passes once `ci` is green, and comments why on one it leaves to the owner. With no forge, `merge <branch>` judges the branch, runs `check` on the merge result and merges it. The one-way doors are the `[[door]]` tables of `guards/manifest.toml`.

## Entry points

Every entry point is one command that exits non-zero on any failure. `check` is tier 0 plus tier 1; `check-deep` adds tier 2; `check-fast` is `check` stopped at the first red, the inner loop. `tier0-tree` is tier 0 less the history gate, which the pre-commit hook runs; `hooks-installed` is red while a commit would not run tier 0; `settings` writes the permission rules from the manifest; `bite-one <gate>` shows one gate's bites while its recipe is written. The method's skills and hooks call every entry point below by name, and the conformance gate requires each. Each tier runs its gates at once, and the `justfile`'s lists are the only list of a tier's gates: CI builds its matrices from them.

| Entry point | Command |
| --- | --- |
| tier0 | `just tier0` |
| tier0-tree | `just tier0-tree` |
| pre-commit | `just pre-commit` |
| commit-msg | `just commit-msg .git/COMMIT_EDITMSG` |
| hooks-installed | `just hooks-installed` |
| tier1 | `just tier1` |
| tier2 | `just tier2` |
| check | `just check` |
| check-fast | `just check-fast` |
| check-deep | `just check-deep` |
| ratchet | `just ratchet` |
| ratchet-lowered | `just ratchet-lowered` |
| snapshots | `just snapshots` |
| snapshots-check | `just snapshots-check` |
| commits | `just commits` |
| suppressions | `just suppressions` |
| guard-diff | `just guard-diff` |
| settings | `just settings` |
| settings-check | `just settings-check` |
| conformance | `just conformance` |
| merge-ready | `just merge-ready` |
| merge | `just merge` |
| weekly | `just weekly` |
| budget | `just budget tier1` |
| reports | `just reports` |
| bite-one | `just bite-one` |

## Gates

- Application types: <one or more of `service`, `web-ui`, `app`, `cli`, `job`, `library`>

One row per question the method asks. The application types decide which rows apply (`skills/method/application-types.toml` in the `ratchet` plugin); they never decide the tool. A row that applies names the tool for this stack, or `none` with the reason and the ADR that accepts the gap: green, and a standing criticism of the binding. A row that does not apply says `none: not a <type>`, naming a type the question is asked of, and needs no ADR. A row is never dropped, and an empty Tool cell is an unfinished binding: the conformance gate is red on both.

| Question | Tool | Tier | Threshold source | Entry point |
| --- | --- | --- | --- | --- |
| format | | 0 | | `just fmt-check` |
| lint, near-all rules | | 0 | | `just lint` |
| strict types | | 0 | | `just types` |
| dependency direction | | 0 | | `just imports` |
| dead code and dependency hygiene | | 0 | | `just deadcode` |
| scripts and CI lint | | 0 | | `just lint-workflows` |
| secrets | | 0 | | `just secrets` |
| feature flags declared | `ratchet` plugin `scripts/flags.py` | 0 | `flags.toml`, and the merge base's copy | `just flags` |
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
| changed-code mutation | the mutation tool, judged by `ratchet` plugin `scripts/mutation_survivors.py` | 1 | `quality/mutation-register.toml` | `just mutation-changed` |
| contract drift | | 1 | | `just contracts-check` |
| shipped-dependency audit | | 1 | any advisory fails | `just audit` |
| duplication | | 1 | | `just duplication` |
| build | | 1 | | `just build` |
| starts and answers | `ratchet` plugin `scripts/with_service.py` on the runner's `boot` and `ready` | 1 | | `just smoke` |
| telemetry emitted | | 1 | the Telemetry section below | `just telemetry` |
| changed flags off and on | | 1 | `flags.toml` against the merge base | `just flag-states` |
| load smoke | `ratchet` plugin `scripts/load_smoke.py` inside `with_service.py` | 1 | zero errors; the Limits section below | `just load-smoke` |
| every changed gate still bites | `ratchet` plugin `scripts/project_bite.py --changed` | 1 | `quality/bites.toml` | `just bite` |
| public surface changed on purpose | | 1 | `quality/snapshots/` | `just api-check` |
| old data still loads | | 1 | | `just data-compat` |
| documentation examples run | | 1 | | `just doctests` |
| static security analysis | | 1 | | `just sast` |
| configuration hardening | | 1 | | `just iac` |
| artefact and image scan | | 1 | | `just artefact-scan` |
| type coverage, ratcheted | | 1 | `quality/thresholds.toml` | `just type-coverage` |
| documentation coverage, ratcheted | | 1 | `quality/thresholds.toml` | `just doc-coverage` |
| change size | `ratchet` plugin `scripts/change_size.py` | 1 | `quality/thresholds.toml` `[ceilings.change]` | `just change-size` |
| guard paths changed | `ratchet` plugin `ratchet_guards.diff` | 1 | `guards/manifest.toml` | `just guard-diff` |
| permission rules mirror the manifest | `ratchet` plugin `scripts/settings_rules.py` | 1 | `guards/manifest.toml` | `just settings-check` |
| method conformance | `ratchet` plugin `scripts/conformance.py` | 1 | | `just conformance` |
| full mutation | the mutation tool, judged by `ratchet` plugin `scripts/mutation_survivors.py` | 2 | `quality/mutation-register.toml` | `just mutation` |
| deep interface conformance | | 2 | | `just api-deep` |
| symbolic checking | | 2 | | `just symbolic` |
| fuzzing | | 2 | the recipe's `seconds` parameter | `just fuzz` |
| end to end | | 2 | | `just e2e` |
| suite order-independent and repeatable | | 2 | | `just test-stability` |
| reproducible build | | 2 | | `just reproducible` |
| race and memory detectors | | 2 | | `just sanitizers` |
| performance budget | | 2 | the Limits section below | `just perf` |
| dynamic security scan | | 2 | | `just dast` |
| active security scan | | 2 | | `just pentest` |
| load | | 2 | the Limits section below | `just load` |
| telemetry arrives | `ratchet` plugin `scripts/telemetry_sink.py` around `with_service.py` | 2 | the Telemetry section below | `just telemetry-e2e` |
| no flag past its removal date | `ratchet` plugin `scripts/flags.py --expired` | 2 | `flags.toml` | `just flags-expired` |
| accessibility | | 2 | | `just a11y` |
| formal verification | | 2 | the project-local verification skill | `just formal` |
| every gate bites | `ratchet` plugin `scripts/bite.py` and `scripts/project_bite.py` | 2 | `quality/bites.toml` | `just bite-all` |

## Absolute paths

<The paths held at 100% line and branch coverage with no exclusion: the gate tools always, plus whatever the owner declares pure.>

## Limits

<The ceilings the gates hold, per path group where they differ: size and complexity (cognitive and cyclomatic complexity, nesting depth, unit and file length); change size per pull request; performance (latency, allocation, bundle size); load (the arrival rate the `load` gate drives, the percentile latency and the error rate it must hold, the concurrency and seconds of the `load-smoke`). Hard limits: split the unit rather than raise one.>

## Telemetry

<The signals every entry point emits and where they go: the OpenTelemetry SDK (or the stack's equivalent) and the one module that starts it; the seam through which every route, job and command is registered, so none can be added uninstrumented; the attributes a span must carry (the route template, the outcome, never a secret or a personal identifier); the metrics and the log fields; the exporter and the collector the end-to-end environment runs. A feature's spec names the signal that shows each criterion working in production.>

## Feature flags

<The registry is `flags.toml`. Name the one module through which a flag is read (OpenFeature, or the stack's equivalent, behind it), the override it honours so the `flag-states` gate can force a flag off and on (an environment variable such as `FLAG_OVERRIDES=key=true`), and who may turn a flag on in production. New behaviour ships behind a flag that defaults to off; a spec that ships none says why.>

## Speed

How this repository stays fast, from the first commit (the Speed section of `skills/method/tiers.md` in the `ratchet` plugin). One line per concept, each saying how this stack meets it; the conformance gate is red while a line is missing or unfilled. The `Shards` line holds a whole number and nothing else: the CI workflows read it and run each long gate as that many jobs, so adding runners is this one number.

- Parallel: <how the suite uses every core on a developer's machine and in CI, what keeps the tests isolated enough for that, and which gates share state and so run one after another (the `justfile`'s `serial` variables; none until a tier's gates run red in parallel and green alone)>
- One pipeline: <CI calls the entry points above and nothing else, so a red job is reproduced by its one command; or the pipeline engine that runs the same definition in both places>
- Shards: 1
- Cache: <the lockfiles and tool configs each CI cache is keyed on, the tool caches it restores, and the caches the tools keep locally>
- Affected only: <what a pull request does not run: the changed-code gates, the path filters, the affected-only tool of a monorepo; the default branch runs everything>
- Ceilings: <`quality/thresholds.toml` `[ceilings.seconds]`, one per tier, set by `just budget <tier> --set` from the first green run and only lowered since; the seconds one gate may take before it is sharded>

## Weekly and advisory

`just audit-all`, `just licenses`, `just drift` and `just load-soak` run weekly as `just weekly`, from the `weekly` workflow or, with no forge, from the machine's scheduler, and block nothing; a red run opens or updates one issue, and a finding gets its own pull request. `just reports` prints hotspots, coupling, cohesion and complexity. They inform and never gate.

## Tool versions

<Where versions are pinned: lockfiles, and the checksum-pinned binaries outside them.>

# Binding

This repository's binding of the Ratchet method (`ratchet:method`; `skills/method/tiers.md` in the `ratchet` plugin lists the questions each tier answers). The binding names the runner, the forge, the tool behind each gate and the paths held absolute. CI runs the same entry points.

## Runner

<The task runner the entry points below are targets of. The `justfile` shipped with the templates is for `just`; delete it when the runner is something else.>

- Ratchet plugin: <X.Y.Z> at <ratchet-sha>

The plugin version has this one home. Every workflow checks the plugin out at this commit. A local tier is red when the plugin it runs from is older than this pin, or another commit of the same version, and says on every run when it is newer (`plugin-pin`). To upgrade, run the plugin's `scripts/upgrade.py --apply`: it changes this line, every workflow `ref` and the permission rules together, adds what the new version's templates hold, and lists the rest for an agent or the owner. The upgrade is one change.

## Forge

- Default branch: `main`
- Pull request template: `.github/pull_request_template.md`
- Change flow: <one reviewed change at a time; the commit convention, if any, and its hook>
- Commit hooks: `.pre-commit-config.yaml` (the `pre-commit` framework, the template's example. Another mechanism names its own file or directory here, then `, installed by` and its install command in backticks. `hooks-installed` proves either by running the hooks.)
- Merge: the merge judge decides (`merge-ready`). The `merge` workflow merges a pull request the judge passes once `ci` is green, and comments why on one it leaves to the owner. With no forge, `merge <branch>` judges the branch, runs `check` on the merge result and merges it. The one-way doors are the `[[door]]` tables of `guards/manifest.toml`.

## Architecture

The shape of the system, chosen with the owner at adoption (ADR 0049 in the `ratchet` plugin; the method's `architecture.md` says what the record holds). Every agent reads this before it places code, a review judges a change against it, and the `dependency direction` gate holds the table. The `conformance` gate is red while the decision record is missing, records fewer than two alternatives, or lacks who chose it, a reversal cost or a refutation criterion, and while the table declares no part or lets a part depend on one it does not declare.

- Decision: <`docs/adr/NNNN-<slug>.md`, the decision record of the architecture>

One row per part, inner to outer. `Path` is the globs of the files that belong to the part, each in backticks. `May depend on` names other parts of this table, comma-separated, or `nothing`.

| Part | Path | May depend on |
| --- | --- | --- |
| <part> | <`glob` of its files> | <parts, or `nothing`> |

## Entry points

Every entry point is one command that exits non-zero on any failure. `check` is tier 0 plus tier 1; `check-deep` adds tier 2; `check-fast` is `check` stopped at the first red, the inner loop. `tier0-tree` is tier 0 less the history gate, which the pre-commit hook runs; `hooks-installed` is red while a commit would not run tier 0, and `hooks-install` installs the repository's own hooks and asks it again; `settings` writes the permission rules from the manifest; `bite-one <gate>` shows one gate's bites while its recipe is written. The method's skills and hooks call every entry point of the first table below by name, and the conformance gate requires each. Each tier runs its gates at once, and the `justfile`'s lists are the only list of a tier's gates: CI builds its matrices from them.

| Entry point | Command |
| --- | --- |
| tier0 | `just tier0` |
| tier0-tree | `just tier0-tree` |
| pre-commit | `just pre-commit` |
| commit-msg | `just commit-msg .git/COMMIT_EDITMSG` |
| hooks-installed | `just hooks-installed` |
| hooks-install | `just hooks-install` |
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
| triggers | `just triggers` |

The second table's entry points are conveniences for the roles and the owner, which no gate requires or runs: `test-files <file>...` runs the tests named, or every test, with the runner the Tests section names, bound or not; `start` runs the deliverable for a person and prints where to open it.

| Convenience | Command |
| --- | --- |
| test-files | `just test-files` |
| start | `just start` |

## Gates

- Application types: <one or more of `service`, `web-ui`, `app`, `cli`, `job`, `library`>
- Life events: none

One row per question the method asks. The application types decide which rows apply (`skills/method/application-types.toml` in the `ratchet` plugin); they never decide the tool. A row that applies names the tool for this stack, or `none` with the reason and the ADR that accepts the gap: green, and a standing criticism of the binding. A row that does not apply says `none: not a <type>`, naming a type the question is asked of, and needs no ADR. A row is never dropped, and an empty Tool cell is an unfinished binding: the conformance gate is red on both.

Every question also has a trigger in the same file (ADR 0028): `always`, `code:<kind>`, `life:<event>`, or those two joined by ` and `. Until the trigger has fired, the Tool cell says `later: <trigger>`, copied from the catalogue exactly (`later: code:outbound-call`, `later: life:deployed`, or a compound such as `later: life:deployed and code:stored-format`, which fires when every part has), and the row judges nothing, needs no bite and has a recipe that prints the same words and exits 0. Adoption writes `later:` for every row whose trigger has not fired in this repository, and binds only the rest. The change that brings a kind of code is free, however many files it touches (ADR 0030): the `triggers` gate notes it and the route makes binding the owed rows the next step (`/ratchet:safeguard`); another instance added while a row is still `later:` is red. A row that is bound stays bound: nothing here unbinds a gate.

`Life events` lists the events the owner has declared, from `deployed`, `irreplaceable-data` and `depended-on` (`none` until then). A declared event owes its rows in the same way and is never red. The route proposes an event when it sees a sign of it; only the owner declares one.

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
| no suppression kind rose, no focus marker | `ratchet` plugin `scripts/suppressions.py` | 0 | the Suppressions section below, as the merge base holds it | `just suppressions` |
| commit subjects follow the convention | `ratchet` plugin `scripts/commits.py` | 0 | | `just commits` |
| unit, property and fitness tests | | 1 | | `just test` |
| coverage, absolute and ratcheted | `ratchet` plugin `scripts/coverage_groups.py` over the suite's Cobertura or LCOV report | 1 | 100% for absolute paths; `quality/thresholds.toml` for the rest | `just coverage` |
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
| survives failing dependencies | | 1 | every outbound call has a timeout | `just dependency-faults` |
| refuses bad configuration | | 1 | | `just config-refusal` |
| shuts down gracefully | | 1 | the seconds it may take to exit | `just graceful-stop` |
| every changed gate still bites | `ratchet` plugin `scripts/project_bite.py --changed` | 1 | `quality/bites.toml` | `just bite` |
| public surface changed on purpose | | 1 | `quality/snapshots/` | `just api-check` |
| old data still loads | | 1 | | `just data-compat` |
| rolls back safely | | 1 | | `just rollback` |
| documentation examples run | | 1 | | `just doctests` |
| static security analysis | | 1 | | `just sast` |
| configuration hardening | | 1 | | `just iac` |
| artefact and image scan | | 1 | | `just artefact-scan` |
| type coverage, ratcheted | | 1 | `quality/thresholds.toml` | `just type-coverage` |
| documentation coverage, ratcheted | | 1 | `quality/thresholds.toml` | `just doc-coverage` |
| change size | `ratchet` plugin `scripts/change_size.py` | 1 | `quality/thresholds.toml` `[ceilings.change]`, and the Test paths section below | `just change-size` |
| guard paths changed | `ratchet` plugin `ratchet_guards.diff` | 1 | `guards/manifest.toml` | `just guard-diff` |
| permission rules mirror the manifest | `ratchet` plugin `scripts/settings_rules.py` | 1 | `guards/manifest.toml` | `just settings-check` |
| method conformance | `ratchet` plugin `scripts/conformance.py` | 1 | | `just conformance` |
| incidents tighten a gate | `ratchet` plugin `scripts/conformance.py` | 1 | `docs/incidents/` against `quality/bites.toml` | `just incidents` |
| safeguards bound before more instances | `ratchet` plugin `scripts/triggers.py` | 1 | the Triggers section below, as the merge base holds it | `just triggers` |
| architecture reviewed when its shape changes | `ratchet` plugin `scripts/triggers.py` | 1 | the Triggers section below and `docs/adr/`, as the merge base holds them | `just triggers` |
| full mutation | the mutation tool, judged by `ratchet` plugin `scripts/mutation_survivors.py` | 2 | `quality/mutation-register.toml` | `just mutation` |
| deep interface conformance | | 2 | | `just api-deep` |
| symbolic checking | | 2 | | `just symbolic` |
| fuzzing | | 2 | the recipe's `each` (seconds a target) and `cap` (seconds in total) | `just fuzz` |
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
| backup restores | | weekly | | `just restore-drill` |
| production answers | | weekly | | `just prod-probe` |

## Triggers

Where each `code:<kind>` trigger of the Gates table finds its instances in this stack (ADR 0028). One row per kind that a question asked of this repository's application types names, and one per kind whose arrival reopens the architecture, whatever the types (ADR 0052); `skills/method/application-types.toml` in the `ratchet` plugin lists the kinds and what each means. `Paths` are comma-separated globs over tracked files. Without a `Pattern` each matching file is one instance; with one, each matching line is, and a literal `|` in it is written `\|`. A repository that never has the kind writes `none: <reason>`. The `triggers` gate counts instances at the merge base and at head, reads this table as the merge base holds it, and holds the rule that the change that brings a kind is free, in any number of files, and a later one that adds another instance while a row it owes is still `later:`, or while the architecture review its arrival reopened has not landed as a decision record, is red.

| Kind | Paths | Pattern |
| --- | --- | --- |
| <kind> | <globs of the tracked files that hold one, or `none: <reason>`> | <optional regex> |

## Life event signs

Which files of this project show that a life event has happened or is about to (ADR 0050), so the route can propose the event to its owner. The method knows no stack, so name this one's from what you know of the tools it chose: what a place that runs the project, or a store that keeps its records, leaves in a tree. `Paths` are comma-separated globs over tracked files, each in backticks, or `none: <reason>` when no file of this project marks the event. A sign only proposes; the owner declares the event on the `- Life events:` line. The `depended-on` event's signs are the method's own and need no row. Revisit the rows when the architecture or its tools change (`/ratchet:architecture`).

| Event | Paths |
| --- | --- |
| deployed | <globs of the tracked files that show where it runs, or `none: <reason>`> |
| irreplaceable-data | <globs of the tracked files that show where its records are kept, or `none: <reason>`> |

## Install

Whether a fresh checkout must install the tools before a recipe runs one (ADR 0043). `merge` runs the gates in a new work tree and CI runs each in a new checkout, so a tool that is on disk only after an install is missing there unless a recipe installs it first. Say which case this stack is, from what you know of the tools you bound; the method names no package manager. Write ``- Install: `just install` ``, with the lockfile it is frozen to and the recipes that run a tool it provides, or `- Install: none: <reason>` and delete the two lines below when the tools arrive on demand. The `install` recipe names the lockfile, installs exactly what it says, and does nothing once that is installed. Each recipe listed depends on `install`, and each tier runs it before its gates start.

- Install: <`just install`, or `none: <reason>`>
- Lockfile: <the path of the file the install is frozen to>
- Needed by: <the recipes that run a tool the install provides, each in backticks>

## Dependencies

What `guard-diff` and the merge judge read to tell a change that only adds or raises dependencies from one that changes a gate (ADR 0044, ADR 0011). A manifest or a lockfile is a guard path. The method knows no manifest or lockfile format: name the files of this stack, from what you know of its tools, and write the entry point. What this section cannot read is not dependency-only, and goes to the owner.

- Manifests: <globs of the files that declare dependencies, each in backticks, or `none: <reason>`>
- Lockfiles: <globs of the files that record what they resolved to, each in backticks, or `none: <reason>`>
- Entry point: <`just dependencies`, or `none: <reason>`>
- Registries: <the `source` of each registry this project accepts that its lockfiles do not use yet, each in backticks, or `none`>

`just dependencies <dir>` reads the files under `<dir>`, each at its own path, as data. It does not run the project, install anything or reach the network, and it exits non-zero when it cannot read a file. It prints one record per line, fields separated by tabs, in the format below. The merge base's copy of the recipe reads the files at the merge base and at the head, so a change cannot rewrite what judges it.

| Record | Fields after the record name |
| --- | --- |
| `declared` | file, table, name, spec, origin |
| `locked` | file, scope, name, version, order, origin, source, integrity, sides |
| `rest` | file, digest |

- `declared`: one dependency a manifest asks for. `table` names the list it sits in, `spec` is its version requirement as the file writes it, and `origin` is `registry` when the spec fetches it from a registry by name and version range and `other` for any other means of fetching.
- `locked`: one resolved version of a package in a lockfile. `scope` names the inventory it belongs to (one name when the file has only one), `version` is as the file writes it, `order` is dot-separated non-negative integers that make the versions of one name sort the way its registry sorts them (a pre-release below its release), `origin` is `registry`, `direct` for a version fetched by other means, or `local` for a path inside the project, `source` is where it comes from and `integrity` the value that identifies its contents (a lockfile that holds none for a `registry` or `direct` version cannot be judged, and goes to the owner), empty only for `local`. `sides` is `many` when several versions of the name can be installed beside each other in the scope, and `one` when only one runs.
- `rest`: one line for each file given, with a digest of everything in it that can change what installs, less the dependency tables, the resolved packages and the package's own version: its settings, overrides, patches and the like. Two files with the same digest differ only in what the other records show.
- Print every record of every file under `<dir>`. Write names as the registry compares them, so two spellings of one name are one name.

## Image pins

Which files of this project name a container image to build from or run, and how a line of one writes it, so the `pins` gate can hold each image to a digest (ADR 0050). The method knows no container tool: name this stack's files and write the pattern from what you know of the format. `Files` are comma-separated globs over tracked files, each in backticks. `Reference` is one regular expression in backticks (Python's `re`; a literal `|` in it is written `\|`) that each line of those files is searched with: group 1 is the image reference, which must carry a digest (`@<algorithm>:<hex>`), and an optional group 2 is a name the line gives to a stage that later lines may build from. Write the pattern so a line that names no external image does not match. A project that names no image in a file of its own writes one row, `none: <reason>`, in `Files`. The gate reads this section at the merge base, so a change cannot narrow what it checks; until it is filled no such file is read. Workflow files are always read.

| Files | Reference |
| --- | --- |
| <`globs` of the files that name an image, or `none: <reason>`> | <`regex`> |

## Suppressions

How the tools bound in the Gates table are told, in the code, to skip a finding, and which markers narrow a suite to the part somebody was looking at (ADR 0042). The method names none: list each directive of the tools this stack binds, from what you know of them, as one row per kind. `Pattern` is an extended regular expression in backticks (a literal `|` in it is written `\|`); the `suppressions` gate counts the lines it matches at the merge base and at head. `Rule` is `rises` (red when the count rose) or `never` (red when a line matches at all). The merge base's table judges a change, so a change cannot loosen its own judge.

| Kind | Pattern | Rule |
| --- | --- | --- |
| <suppression kind> | <`pattern`> | rises |

Every Gates row whose question a directive can silence (the catalogue marks them `directives`; `skills/method/application-types.toml` in the `ratchet` plugin) and that is bound to a tool says here which kinds silence it, or `none: <reason>` when the tool has no such directive. The `conformance` gate is red while a bound row has no line, or names a kind the table above lacks.

| Question | Kinds |
| --- | --- |
| <question> | <`kind`, ... or `none: <reason>`> |

## Absolute paths

<The paths held at 100% line and branch coverage with no exclusion: the gate tools always, plus whatever the owner declares pure.>

## Tests

<The test frameworks and directories every role writes tests with, chosen and installed at adoption so the first feature has nothing to install: the test runner and its configuration file; the property-testing library, or `built in` where the runner has one; where unit and property tests live and how their files are named. The `unit, property and fitness tests` row stays `later: code:source` until the first module of logic arrives: no gate runs these tests until then, each role runs its own with `just test-files <file>...`, and `/ratchet:safeguard` binds the row to what this section names.>

## Test paths

What the change-size gate reads to count tests apart from product code (ADR 0045): the ceiling on product lines and files bounds the unguarded code a change brings, and test lines have a ceiling of their own that only stops a runaway. The method knows no stack, so name the files of this one, from what you know of its tools. The gate reads this section at the merge base, so a change cannot widen what it exempts; until it is filled every changed line counts as product.

- Tests: <globs of every file that is a test, a fixture or test support, the critique directory included, each in backticks, or `none: <reason>`>

## Critique tests

<The one flat directory the pipeline's critic keeps its tests in, apart from the unit tests, with the file name pattern: `tests/critique/<slug>.<layer>.critique.test.<ext>`, one file per layer a feature touches. A critique file is organised by feature, not by module, and often reaches past one unit, so it never sits beside the code. The test runner's configuration must include this directory.>

## Limits

<The ceilings the gates hold, per path group where they differ: size and complexity (cognitive and cyclomatic complexity, nesting depth, unit and file length); change size per pull request; performance (latency, allocation, bundle size); load (the arrival rate the `load` gate drives, the percentile latency and the error rate it must hold, the concurrency and seconds of the `load-smoke`). Hard limits: split the unit rather than raise one.>

## Telemetry

<The signals every entry point emits and where they go: the telemetry library this stack uses and the one module that starts it; the seam through which every route, job and command is registered, so none can be added uninstrumented; the attributes a span must carry (the route template, the outcome, never a secret or a personal identifier); the metrics and the log fields; the exporter and the collector the end-to-end environment runs. A feature's spec names the signal that shows each criterion working in production.>

## Feature flags

<The registry is `flags.toml`. Name the one module through which a flag is read (a feature-flag library behind it, if the stack has one), the override it honours so the `flag-states` gate can force a flag off and on and the gates that request routes can boot with every flag due for removal on (an environment variable that lists the flags to force), and who may turn a flag on in production. New behaviour ships behind a flag that defaults to off; a spec that ships none says why.>

## Speed

How this repository stays fast, from the first commit (the Speed section of `skills/method/tiers.md` in the `ratchet` plugin). One line per concept, each saying how this stack meets it; the conformance gate is red while a line is missing or unfilled. The `Shards` line holds a whole number and nothing else: the CI workflows read it and run each long gate as that many jobs, so adding runners is this one number.

- Parallel: <how the suite uses every core on a developer's machine and in CI, what keeps the tests isolated enough for that, and which gates share state and so run one after another (the `justfile`'s `serial` variables; none until a tier's gates run red in parallel and green alone)>
- One pipeline: <CI calls the entry points above and nothing else, so a red job is reproduced by its one command; or the pipeline engine that runs the same definition in both places>
- Shards: 1
- Cache: <the lockfiles and tool configs each CI cache is keyed on, the tool caches it restores, and the caches the tools keep locally>
- Affected only: <what a pull request does not run: the changed-code gates, the path filters, the affected-only tool of a monorepo; the default branch runs everything>
- Ceilings: <`quality/thresholds.toml` `[ceilings.seconds]`, one per tier, set by `just budget <tier> --set` from the first green run and only lowered since; the seconds one gate may take before it is sharded>

## Weekly and advisory

`just audit-all`, `just licenses`, `just drift`, `just load-soak`, `just restore-drill` and `just prod-probe` run weekly as `just weekly`, from the `weekly` workflow or, with no forge, from the machine's scheduler, and block nothing; a red run opens or updates one issue, and a finding gets its own pull request. `just reports` prints hotspots, coupling, cohesion and complexity. They inform and never gate.

## Tool versions

<Where versions are pinned: lockfiles, and the checksum-pinned binaries outside them.>

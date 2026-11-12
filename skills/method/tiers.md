# Tiers and the questions each answers

The method asks questions; the binding names the tool that answers each one for this stack. The method ships the questions and never the tools: which questions apply is decided by what kind of software the repository is, and which tool answers one is chosen at adoption, from what is current then (ADR 0017). A question that applies and has no tool in the stack is bound to `none` with the reason and the ADR that accepts the gap: that row is green, and a standing criticism of the binding until a tool exists. A question is never advisory because its tool is inconvenient; advisory is for reports that inform a choice rather than judge a change.

## Application types

A binding declares what the repository is, on one line: `- Application types: service, web-ui`. There are six types, and a repository may be several.

| Type | What it is |
| --- | --- |
| `service` | answers requests over a network: an HTTP or gRPC API, a web backend |
| `web-ui` | runs in a browser |
| `app` | an installed client with a user interface: mobile, desktop |
| `cli` | a command a person or a script runs |
| `job` | runs unattended on a schedule, a queue or a stream: a worker, a batch, a pipeline |
| `library` | code other code imports; nothing to boot |

A type decides which questions apply. It never decides the tool. Every question applies to every type unless [application-types.toml](application-types.toml) narrows it, and that file is the single source: the conformance gate reads it, and `python3 scripts/application_types.py <type>...` in the plugin prints the rows a set of types leaves out. Contract drift, for example, is asked only of a service, accessibility only of a web interface or an app, and a library is not asked whether it starts, emits telemetry or holds under load. The weekly `load-soak` follows `load`.

A row is never dropped. One that applies names a tool or is an accepted gap, as above. One that applies to none of the declared types says `none: not a <type>`, naming a type the question is asked of, and needs no ADR: the type line is the reason. The conformance gate is red on a missing or unknown type, on an empty Tool cell, on a row that applies bound to `none` without a reason and an ADR the repository holds, and on a dropped row.

## When each tier runs

| Tier | When | Entry point | Budget | Blocks |
| --- | --- | --- | --- | --- |
| 0 | every commit | `tier0`; the commit hooks split it, `pre-commit` on the tree before the commit and `commit-msg` on the message being written, since `commits` over history would judge the commit an amend replaces | about a minute | the commit, and the pull request |
| 1 | every pull request | `tier1` | under fifteen minutes, so nobody is tempted to skip it | the pull request |
| 2 | merge to the default branch, nightly, on demand | `tier2` | unbounded; time-boxed where a tool runs forever | the next piece of work: a red tier 2 on the default branch is fixed before anything else merges, and while it is red the merge judge leaves every merge to the owner |
| weekly | scheduled | `audit-all`, `licenses`, `drift`, `load-soak` | | nothing; the finding is visible and gets its own pull request |
| advisory | on demand | `reports` | | nothing, ever; read before a refactor to pick targets |

Tier 0 runs on a commit only if the commit hooks are installed, and a hook that is not installed fails silently: the commit goes through. A `core.hooksPath` in the git config, local or global, is the usual cause, since `pre-commit install` then refuses. So the method asks "would a commit here run tier 0?" with `scripts/hooks_installed.py`. It runs the hooks git would run for a commit, with `RATCHET_HOOK_PROBE` naming a file, and the `pre-commit` and `commit-msg` recipes answer by writing their own name to that file before any gate runs. It is green only when both names come back. That proves the whole chain, a global hook that chains to the repository's hooks included, without reading a hook script, and tier 0 itself never runs. Only those two recipes answer the probe; `tier0` and `check` call the gates directly, so a stray variable never skips one.

The feature preflight, the route and the `ratchet` recipe ask it, and each prints the owner's one command while it is red (the guard refuses a hook install or hook-path change from an agent). CI and `conformance` do not: a CI checkout has no hooks, and tier 0 runs there as a job. `conformance` checks only that the recipes can answer. A forged green needs a script written into `.git/hooks` (the `local-git` group denies the edit) or the recipes changed (`justfile` is a `gate-commands` path, which `guard-diff` lists). Setting the variable on a real commit would skip tier 0, and the guard denies that like `SKIP=`.

`check` is tier 0 plus tier 1. `check-deep` adds tier 2. Each tier is a list of small entry points, so a failure names the one that went red and that one reruns alone until green.

Every comparison against history uses the merge base with the default branch (`HEAD~1` on the default branch itself), so a loosening made in an earlier commit of the same branch is still seen. CI checks out full history.

## Tier 0: the shape of the code

| Question | What commonly answers it |
| --- | --- |
| Is every file in one canonical form? | a formatter in check mode |
| Does the code break any rule a linter can state? | a linter with near-all rules on; each opt-out carries a written reason |
| Is every type as strict as the language allows? | the strictest static checking the language offers; its dynamic escape hatch is banned |
| Do dependencies point only the way the design says? | a dependency tool over the imports, and a fitness test that checks it again |
| Is everything reachable and declared? | dead-code and dependency-hygiene tools |
| Is the build held to the same bar as the product? | shell, CI and config linters |
| Is anything secret in the commit, the tree or the history? | a secret scanner |
| Is every feature flag declared once, owned, off when new and due for removal? | the method's flags gate over `flags.toml`: key, owner, default, a `remove-by` date or `permanent`, a reason; a flag the merge base lacks defaults to off; a key no source file names is removed |
| Is every dependency, action and image pinned exactly, and every CI job least-privileged? | lockfile matches the manifest with no floating range; actions and images pinned by digest; workflow permissions declared and minimal |
| Is every unit within its size and complexity limits? | the linter's size and complexity rules plus a fitness test, with the numbers the binding's Limits section holds; a hard limit, split the unit |
| Did the count of TODO, FIXME and HACK markers rise? | counted at the merge base and at head, never stored |
| Does prose use the glossary too? | a prose linter carrying the _Avoid_ lists over docs and comments, judging the uses a change adds against the merge base |
| Do commit subjects follow the convention? | the method's commits gate: `type(scope): summary`, at most 72 characters |
| Has any gate been loosened? | the method's own gates: snapshots match the effective configuration, no threshold lowered, no suppression kind rose, no focus marker |

## Tier 1: the behaviour of the code

| Question | What commonly answers it |
| --- | --- |
| Does the code do what its tests claim? | unit, property and fitness tests; property tests with deterministic seeds |
| How much did the tests reach, and did the reach fall? | coverage: 100% on absolute paths, ratcheted elsewhere; every changed line and branch ran; the test count did not fall and deleted test files are named |
| Would the tests notice if the changed code were wrong? | mutation of changed functions: every mutant killed or registered |
| Do the published interfaces match the code? | generated contract artefacts committed; regenerating them produces no diff |
| Does anything shipped carry a known advisory? | a dependency audit |
| Does the code repeat itself past the ceiling? | a clone detector |
| Does the deliverable build? | the build |
| Does the deliverable start and answer? | for a service, the method's `scripts/with_service.py`: boot it by any command, wait until a URL answers, stop the whole process tree; another type runs its own deliverable once |
| Does every entry point emit the telemetry the binding names? | tests against an in-memory OpenTelemetry exporter: each route, job and command yields a span with the attributes the binding requires, a failure is recorded on its span, a log line carries the trace id; a fitness test fails an entry point registered outside the instrumented seam; the signal names are snapshotted beside the tests, so the telemetry surface changes on purpose |
| Does the code hold with each changed flag off and on? | the suite once per state of every flag the change adds or touches, through the override the flag seam honours; off is the merge base's behaviour |
| Does the deliverable stay correct under concurrent requests? | the method's `scripts/load_smoke.py` inside `with_service.py`: seconds of concurrent requests against the booted deliverable, zero errors and a floor on requests answered; latency is printed, not judged, since a pull-request runner is no benchmark host |
| Does every gate the change touches still bite? | the method's `scripts/project_bite.py --changed`: when the change touches a path that defines a gate (a tool's config, the command contract, a fitness test, a gate tool, the binding, the bite registry), each fault `quality/bites.toml` holds for a tier 0 or tier 1 gate is planted in a scratch copy and the gate must go red; a change that touches none proves nothing, and says so |
| Did the public surface change on purpose? | a snapshot of exported symbols, routes or flags, diffed; a change needs a version bump or a changelog line |
| Does old persisted data still load? | golden fixtures from earlier versions read cleanly; migrations apply and reverse |
| Do the documentation's examples run? | doctests, or extracted snippets executed |
| Does the code contain a known insecure pattern? | static security analysis with the stack's rule set; findings fail |
| Does the container, infrastructure or deployment configuration break a hardening rule? | a configuration scanner over the Dockerfile, the manifests and the infrastructure code |
| Does the shipped artefact or base image carry a known vulnerability? | an image or artefact scanner, over the built image when the deliverable is one |
| How much of the code is typed, and did it fall? | type coverage, ratcheted, for gradually typed languages |
| How much of the public surface is documented, and did it fall? | documentation coverage, ratcheted |
| Is the change within the size budget? | files and lines against the merge base, a ceiling the binding holds |
| Did a guard path change? | the method's guard diff, against the manifest at base and head |
| Do the permission rules still mirror the manifest? | the method's settings check: one `Edit` rule per manifest pattern, the fixed command rules, nothing else in `deny` or `ask` |
| Does the repository still carry the method? | the method's conformance gate |

## Tier 2: the limits of the code

| Question | What commonly answers it |
| --- | --- |
| Would the tests notice any wrong code at all? | full mutation over the absolute paths |
| Does the interface hold under every sequence of calls? | stateful property tests against the published contract |
| Can a solver find an input that breaks a contract? | symbolic checking |
| Can random input crash it? | time-boxed fuzzing with a kept corpus; a crash is a bug |
| Does the real system work through its real interface? | an end-to-end suite, the thin top of the pyramid |
| Does the suite pass in any order, twice? | shuffled order, repeated runs, fresh seeds; a failure here is a bug, never "flaky" |
| Is the build reproducible? | build twice, compare hashes |
| Does the code hold under a race or memory detector? | race detector and sanitizers where the stack has them |
| Is it within the performance budget? | benchmarks against the ceilings the binding holds for latency, allocation and bundle size; the owner's `/pstack:benchmark-checklist` before a number is trusted |
| Does the running system resist a dynamic scan? | a passive baseline scan against the end-to-end environment: headers, cookies, transport, disclosure |
| Can an attacker's toolkit break in? | an active scan with a signed-in session, and requests generated from the published contract that try each route's authentication, authorisation and input handling; a finding fails |
| Does it hold its latency and throughput under the load the binding names? | a load test against the end-to-end environment with the thresholds the binding's Limits hold: the arrival rate, the percentile latency, the error rate; the owner's `/pstack:benchmark-checklist` before a number is trusted |
| Does the telemetry arrive? | the method's `scripts/telemetry_sink.py`: an OTLP receiver the booted deliverable exports to, green only when a request's traces, metrics and logs arrive under the service's name; a project with a collector reads them back from it instead |
| Is any flag past its removal date? | the method's flags gate with `--expired`; it goes red by the calendar, so it is asked here and never on a pull request |
| Is the interface usable by everyone? | accessibility checks inside the end-to-end suite, for user interfaces; `impeccable audit` is the agent-side pass, the gate is the deterministic one |
| Does the model still hold, and does the code still mirror it? | the project-local formal verification skill: the owner's `/pstack:create-formal-verification-skill` writes it where a bug costs most and tests reach least, their `/pstack:maintain-formal-verification-skill` keeps it true to the code. The model and the skill are guard paths |
| Does every gate bite, and is it red with nothing to run? | the method's `scripts/bite.py` for the gates the plugin ships, then `scripts/project_bite.py` for the gates the repository bound: every fault of `quality/bites.toml`, and the emptied collection of each gate that judges one, green without the fault and red with it |

## Weekly and advisory

`audit-all` audits every dependency, dev tooling included. `licenses` checks the shipped dependencies' licences are compatible. `drift` lists outdated dependencies and writes the software bill of materials. `load-soak` holds the load for an hour and then raises it until the system breaks, and reports where: a leak and a breaking point are findings, each with its own pull request. `reports` prints hotspots (churn times complexity), coupling, cohesion and the complexity distribution: read before a refactor to pick targets, never as a score to chase.

## Speed

Gates that accumulate become gates that get skipped. So speed is a property the method asks for and measures from the first commit: the local run and the CI run are built to be fast before anything in them is slow, and the optimisation that usually arrives late is a standing obligation. The method names the concepts. The tools in brackets are examples; the binding's Speed section says how this stack meets each one (ADR 0019).

**Parallel by default.** The test suite uses every core, on a developer's machine as well as in CI, from the first test (`pytest-xdist`, vitest's worker pool, `cargo nextest`, `go test -p`). That forces each test to own its state: no shared file, port, database or clock. The tier 2 order-independence gate keeps them so. A tier's gates run at once as well, wherever they share no state, so the wall clock is the slowest gate and never the sum: in CI each gate is its own job.

**One pipeline, the same everywhere.** A CI job runs one entry point and nothing else, so a red job is reproduced on a developer's machine by one command, and CI cannot drift from the local run. A project that wants hermetic, cached steps puts a pipeline engine under the entry points (Dagger, Earthly, Bazel); the entry points stay the interface.

**Shard before you need to.** The long gates (the suite, mutation, end to end) run in CI as a matrix over a shard count, and the count is one line of the binding, `- Shards: 1` on day one. Each job receives `SHARD` and `SHARDS`, and the recipe hands them to its tool (`pytest-split`, `vitest --shard`, `cargo nextest --partition`, the mutation tool's own shards); a local run is shard 1 of 1. Adding runners later is that one number, never a restructuring.

**Cache everything, keyed on what it depends on.** Dependencies and every tool cache (type checker, linter, test, mutation, build), keyed on the lockfile and the tool's config with fallback keys; the toolchain pinned, with the setup action's own cache on. Locally the tools' incremental caches stay on.

**Run only what the change can affect.** Mutation of changed functions, coverage of changed lines, the flag states of changed flags, incremental type checking and builds; path filters, and an affected-only tool in a monorepo (`turbo`, `nx`, `pants`). The default branch runs everything.

**Fail fast locally, report everything in CI.** Locally the cheapest gates run first and the run stops at the first red. In CI a matrix never stops early (`fail-fast: false`), so one run shows every red, and a pushed fix cancels the run it replaces.

**Measured, not hoped.** `quality/thresholds.toml` holds `[ceilings.seconds]` with one key per tier, and one per gate where a gate has its own runner. The `budget` entry point wraps a tier or gate, measures its wall clock and goes red over its ceiling. A ceiling only falls: `ratchet-lowered` fails a raised one as it fails a lowered threshold, and raising one is a loosening with an ADR behind it, whose `Relaxes:` line ([ratchet.md](ratchet.md)) the gate reads. Adopt sets the first ceilings from the first measured run with headroom (`budget --set`), never under the tier's budget in the table above, so an empty skeleton's few seconds do not become the first feature's ceiling; from then on every change that pushes a tier over must pay for itself in the same pull request. Runners vary, so the ceiling blocks on the default branch and nightly, in the tier 2 sense, and reports on pull requests. Every CI job appends its timing (`budget --report`) and uploads it, so the slowest gate is always named.

The conformance gate checks what a machine can: the binding's Speed section has a line for `Parallel`, `One pipeline`, `Shards`, `Cache`, `Affected only` and `Ceilings`, none of them left unfilled; `Shards` is a whole number; every workflow cancels superseded runs and caches; and the tier workflows run their long gates over the shard count.

What speed never buys: a quarantined test (a failure here is a bug, never "flaky"), a tier 2 gate moved to on-demand to hide its cost, or a `timeout-minutes` so generous it never fires.

## Choosing a tier for a new question

Fast and local: tier 0. Needs the whole tree or a test run: tier 1. Needs more than the pull-request budget: tier 2. The catalogue is a floor: a project adds rows for what its domain demands.

## Operable from the first commit

Four things are usually bolted on after the first incident: telemetry, feature flags, security testing and load testing. The method asks each of them from the first commit, and splits each across the tiers so that the part a developer waits for is the cheap part (ADR 0012).

| Concern | Tier 0, seconds | Tier 1, the pull request | Tier 2, the default branch and nightly | Weekly |
| --- | --- | --- | --- | --- |
| Telemetry | | every entry point emits its span, in memory | the signals arrive at a collector | |
| Feature flags | the registry: declared, owned, off when new | the suite with each changed flag off and on | no flag past its removal date | |
| Security | secrets, pins | static analysis, dependency audit, configuration hardening, artefact or image scan | passive scan, active scan, fuzzing | `audit-all` |
| Load | | concurrent requests, zero errors | latency and throughput against the Limits | soak and breaking point |

A pull request pays only for what it changed: the flag gate reruns the suite for the flags the change touches and for none when it touches none, the load smoke is seconds long, and the telemetry tests are unit tests with an exporter that keeps spans in memory. What needs a running environment, a clock or a network waits for tier 2. The binding's Telemetry and Feature flags sections name the seams: the one module that starts the telemetry SDK and through which every entry point is registered, and the one module through which a flag is read.

A repository whose application types leave a row out (a library has nothing to boot, to load or to scan from outside) binds it `none: not a <type>`; the rows themselves stay, and the conformance gate checks they are there.

## What a recipe must get right

A tool is chosen per repository, so no recipe is shipped for it. These hold for any tool, and each was learnt on a binding where a gate passed while judging nothing or failed for a reason that was not the code's.

- **A gate with nothing to run is red.** A suite that collects no test, a performance gate with no benchmark, a fuzz gate with no target, a telemetry gate with no telemetry test: each fails, on every stack. `none` is for a property the project will never have, and it is written in the binding, never printed by a recipe that found nothing.
- **A gate over the change may find nothing to judge, and says so.** Mutation of changed functions, coverage of changed lines and the flag states of changed flags are green when the change touches nothing in their scope, and only when the tool itself reports zero items: a tool that fails for another reason is red.
- **A failed collection is red, never a count of zero.** The test-count gate compares two counts; an install, an import or a listing that fails at either side is a failure, not a smaller number.
- **Mutation is judged by the register, never the score.** The score is a trend. Clear the tool's cache of verdicts before a run, so a new test meets the old survivors again.
- **A rule set for a framework is judged only when the framework is a dependency.** Static-analysis packs bundle rules for frameworks the project does not use, and one of them firing on a look-alike call is noise; the language and runtime rules always apply, and the recipe prints how many it left out.
- **A generated artefact that is not committed fails its drift gate.** Comparing with the index alone passes an untracked report.
- **Every tool runs through the package manager that locks it**, so the lockfile pins the gate as it pins the product, and the audit of shipped dependencies leaves the development tools to the weekly `audit-all`.
- **Each coverage group is measured by a report that reaches it.** Two suites write two reports and one judgement reads both; a fitness test's logic lives where coverage measures it.
- **A repeated run takes a fresh seed and prints it**, so a failure in the order-independence gate replays.
- **A recipe builds what it runs, every time.** In CI each gate is a job of its own, so the boot command of a service gate compiles first, and nothing depends on what another recipe left behind: a coverage report, a build directory, a cache. A recipe that skips the build when its output is already there judges the last build.
- **A red gate says what is wrong.** A recipe that ends on a bare comparison or a tool's exit code prints nothing a reader, or a bite's `expect`, can tell from any other failure. It prints the rule and the number.
- **A threshold starts at the measured value.** A ratchet recorded as zero is a gate that cannot fail.
- **A tool that walks the tree is pointed at the source.** `quality/snapshots/` holds copies of the tool configs, a dependency manifest among them, which reads as a second package to a tool that searches for manifests.

## Every bound gate is shown to bite

The method ships no tool, so it cannot promise that a repository's recipes work. The repository shows it. `quality/bites.toml` holds one `[[bite]]` or more for each Gates row whose Tool cell names a tool the repository chose, a row that pairs a plugin script with a tool of the stack included. A row bound `none`, and a row bound to the plugin's scripts alone, needs none: `scripts/bite.py` proves those.

A bite names its `gate` (the last word of the row's entry point), the fault in a few words, exactly one fault, and `expect`: words the gate prints when it is red on this fault. The fault is `write` (create or replace one file), `patch` (a diff under `quality/bites/`) or `remove` (delete what the globs match). A gate that judges a collection, which `application-types.toml` marks, carries a `remove` bite as well: the collection emptied, and the gate red. `args` are appended to the gate's command, and `commit = true` commits the fault first, for a gate that reads history.

`scripts/project_bite.py` makes a scratch copy of the work tree as git sees it, runs the registry's `setup` there, and for each bite runs the gate (green), plants the fault, and runs it again. `bites` is the one green verdict. `DOES NOT BITE` is a gate that cannot fail on what it is for. `RED BEFORE THE FAULT` is a gate that is red on a fresh checkout, usually because it leans on what another recipe left behind. `RED FOR ANOTHER REASON` is a gate red without the words the bite expects, or a bite whose words the gate prints as often when it is green. Each is fixed in the recipe or the fault, never by loosening `expect` until it matches. The real work tree is never written, and nothing git ignores is copied, so a gate is judged as a CI job would run it.

The whole proof is `bite-all`, in tier 2. On a pull request `bite` asks whether the change touches a path that defines a gate, and proves the tier 0 and tier 1 gates only then. The conformance gate is red on a bound gate with no bite and on a collection gate with no `remove` bite, so a gate cannot be added unproven. The registry and its patches sit under `quality/`, a guard path: dropping a bite asks the owner.

One fault shows that a gate is alive and wired to the thing it judges. It does not show that the tool's rule set is complete. A gate nobody can plant a fault for is not a gate: its row is bound `none`, with the reason and an ADR.

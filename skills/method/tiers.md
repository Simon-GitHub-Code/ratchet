# Tiers and the questions each answers

The method asks questions; the binding names the tool that answers each one for this stack. The method ships the questions and never the tools: which questions apply is decided by what kind of software the repository is, when one is first asked is decided by its [trigger](#triggers), and which tool answers one is chosen when it is first asked, from what is current then (ADR 0017, ADR 0028). A question that applies and has no tool in the stack is bound to `none` with the reason and the ADR that accepts the gap: that row is green, and a standing criticism of the binding until a tool exists. A question is never advisory because its tool is inconvenient; advisory is for reports that inform a choice rather than judge a change.

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

A row is never dropped. One that applies names a tool or is an accepted gap, as above. One that applies to none of the declared types says `none: not a <type>`, naming a type the question is asked of, and needs no ADR: the type line is the reason. One that applies and whose trigger has not fired says `later: <trigger>`, and judges nothing until it fires. The conformance gate is red on a missing or unknown type, on an empty Tool cell, on a row that applies bound to `none` without a reason and an ADR the repository holds, and on a dropped row.

## When each tier runs

| Tier | When | Entry point | Budget | Blocks |
| --- | --- | --- | --- | --- |
| 0 | every commit | `tier0`; the commit hooks split it, `pre-commit` on the tree before the commit and `commit-msg` on the message being written, since `commits` over history would judge the commit an amend replaces | about a minute | the commit, and the pull request |
| 1 | every pull request | `tier1` | under fifteen minutes, so nobody is tempted to skip it | the pull request |
| 2 | merge to the default branch, nightly, on demand | `tier2` | unbounded; time-boxed where a tool runs forever | the next piece of work: a red tier 2 on the default branch is fixed before anything else merges, and while it is red the merge judge leaves every merge to the owner |
| weekly | scheduled | `audit-all`, `licenses`, `drift`, `load-soak`, and once their events are declared the backup restore and the production check | | nothing; the finding is visible and gets its own pull request |
| advisory | on demand | `reports` | | nothing, ever; read before a refactor to pick targets |

Tier 0 runs on a commit only if the commit hooks are installed, and a hook that is not installed fails silently: the commit goes through. A `core.hooksPath` in the git config, local or global, is the usual cause, since `pre-commit install` then refuses. So the method asks "would a commit here run tier 0?" with `scripts/hooks_installed.py`. It runs the hooks git would run for a commit, with `RATCHET_HOOK_PROBE` naming a file, and the `pre-commit` and `commit-msg` recipes answer by writing their own name to that file before any gate runs. It is green only when both names come back. That proves the whole chain, a global hook that chains to the repository's hooks included, without reading a hook script, and tier 0 itself never runs. A hook that fails before it answers is reported as failed, with its output, not as missing. Only those two recipes answer the probe; `tier0` and `check` call the gates directly, so a stray variable never skips one.

The feature preflight, the route and the `ratchet` recipe ask it, and each prints the repair while it is red. Where the repository's own hooks are missing, that is the `hooks-install` entry point, which an agent runs: it runs the hook framework's own install, with a global or system `core.hooksPath` hidden from that one command, changes no git config, moves no hook somebody else wrote, and ends with the same probe. A change of git config (a local `core.hooksPath`, a global hook that does not chain to the repository's own) is printed as the owner's, since the guard refuses it from an agent (ADR 0026). CI and `conformance` do not: a CI checkout has no hooks, and tier 0 runs there as a job. `conformance` checks only that the recipes can answer. A forged green needs a script written into `.git/hooks` (the `local-git` group denies the edit) or the recipes changed (`justfile` is a `gate-commands` path, which `guard-diff` lists). Setting the variable on a real commit would skip tier 0, and the guard denies that like `SKIP=`.

`check` is tier 0 plus tier 1. `check-deep` adds tier 2. `check-fast` is `check` stopped at the first red, for the inner loop. Each tier is a list of small entry points, so a failure names the one that went red and that one reruns alone until green.

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
| Did the count of TODO, FIXME, HACK and XXX markers rise? | counted at the merge base and at head, never stored |
| Does prose use the glossary too? | a prose linter carrying the _Avoid_ lists over docs and comments, judging the uses a change adds against the merge base |
| Do commit subjects follow the convention? | the method's commits gate: `type(scope): summary`, at most 72 characters, not counting the ` (#12)` a squash merge appends |
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
| Does the deliverable start and answer? | for a service, the method's `scripts/with_service.py`: boot it by any command, wait until a URL answers, stop the whole process tree, and boot once more on a new port when a service on a port it picked exits before it answers; another type runs its own deliverable once |
| Does every entry point emit the telemetry the binding names? | tests against an in-memory OpenTelemetry exporter: each route, job and command yields a span with the attributes the binding requires, a failure is recorded on its span, a log line carries the trace id; a fitness test fails an entry point registered outside the instrumented seam; the signal names are snapshotted beside the tests, so the telemetry surface changes on purpose |
| Does the code hold with each changed flag off and on? | the suite once per state of every flag the change adds or touches, through the override the flag seam honours; off is the merge base's behaviour |
| Does the deliverable stay correct under concurrent requests? | the method's `scripts/load_smoke.py` inside `with_service.py`: seconds of concurrent requests against the booted deliverable, zero errors and a floor on requests answered; red while it requests nothing but the readiness URL, which a service with no other route yet answers with `--health-only`; latency is printed, not judged, since a pull-request runner is no benchmark host |
| Does it refuse bad configuration? | the method's `with_service.py --refuses NAME`: the deliverable booted with the required setting `NAME` missing must exit non-zero before it answers and print the setting's name |
| Does it shut down gracefully? | the method's `with_service.py --graceful SECONDS`: the deliverable is signalled while a request is in flight, and is green only when that request completes and the process exits within `SECONDS` |
| Does it survive a dependency that fails? | a timeout on every outbound call, held by a lint rule or a fitness test; a test double or a fault-injecting proxy in front of the dependency makes it answer with an error, answer slowly and vanish, and the test expects a bounded failure the code names |
| Does the merge base's build still run on the data the head leaves? | expand and contract migrations: add a column or a field, write both, and remove the old one in a later change. The recipe applies the head's migrations to a scratch store and runs the merge base's build, and its smoke, against it |
| Does every incident name the gate that now catches it? | incident records under `docs/incidents/`, each with a `Bite:` line naming a bite of `quality/bites.toml` that reproduces the incident ([records.md](records.md#incident-record-docsincidentsnnnn-slugmd)) |
| Did the change add another instance of a kind whose safeguards are still owed? | the method's `scripts/triggers.py`: counts each kind's instances at the merge base and at head, by the binding's Triggers table, and is red when the count rose while a row that kind fires is still owed. The change that brings the first instance is green, and the gate names the rows it made owed |
| Does every gate the change touches still bite? | the method's `scripts/project_bite.py --changed`: when the change touches a path that defines a gate (a tool's config, the command contract, a fitness test, a gate tool, the binding, the bite registry), each fault `quality/bites.toml` holds for a tier 0 or tier 1 gate is planted in a scratch copy and the gate must go red; a change that touches none proves nothing, and says so |
| Did the public surface change on purpose? | a snapshot of exported symbols, routes or flags, diffed; a change needs a version bump or a changelog line |
| Does old persisted data still load? | golden fixtures from earlier versions read cleanly; migrations apply and reverse |
| Do the documentation's examples run? | doctests, or extracted snippets executed |
| Does the code contain a known insecure pattern? | static security analysis with the stack's rule set; findings fail |
| Does the container, infrastructure or deployment configuration break a hardening rule? | a configuration scanner over the Dockerfile, the manifests and the infrastructure code |
| Does the shipped artefact or base image carry a known vulnerability? | an image or artefact scanner, over the built image when the deliverable is one |
| How much of the code is typed, and did it fall? | type coverage, ratcheted, for gradually typed languages |
| How much of the public surface is documented, and did it fall? | documentation coverage, ratcheted |
| Is the change within the size budget? | files and lines against the merge base, a ceiling the binding holds; the method's own records, generated lockfiles and their snapshots are not counted, and a moved file counts by its changed lines |
| Did a guard path change? | the method's guard diff, against the manifest at base and head |
| Do the permission rules still mirror the manifest? | the method's settings check: one `Edit` rule per manifest pattern, the fixed command rules, nothing else in `deny` or `ask` |
| Does the repository still carry the method? | the method's conformance gate |

## Tier 2: the limits of the code

| Question | What commonly answers it |
| --- | --- |
| Would the tests notice any wrong code at all? | full mutation over the absolute paths |
| Does the interface hold under every sequence of calls? | stateful property tests against the published contract |
| Can a solver find an input that breaks a contract? | symbolic checking |
| Can random input crash it? | fuzzing with a kept corpus, each target for a fixed time under a cap on the total; a crash is a bug |
| Does the real system work through its real interface? | an end-to-end suite, the thin top of the pyramid |
| Does the suite pass in any order, twice? | shuffled order, repeated runs, fresh seeds; a failure here is a bug, never "flaky" |
| Is the build reproducible? | build twice, compare hashes |
| Does the code hold under a race or memory detector? | race detector and sanitizers where the stack has them |
| Is it within the performance budget? | benchmarks against the ceilings the binding holds for latency, allocation and bundle size; the owner's `/pstack:benchmark-checklist` before a number is trusted |
| Does the running system resist a dynamic scan? | a passive scan of the running deliverable, fed the routes of the published contract: headers, cookies, transport, disclosure |
| Can an attacker's toolkit break in? | an active scan with a signed-in session, and requests generated from the published contract that try each route's authentication, authorisation and input handling; a finding fails |
| Does it hold its latency and throughput under the load the binding names? | a load test against the end-to-end environment with the thresholds the binding's Limits hold: the arrival rate, the percentile latency, the error rate; the owner's `/pstack:benchmark-checklist` before a number is trusted |
| Does the telemetry arrive? | the method's `scripts/telemetry_sink.py`: an OTLP receiver the booted deliverable exports to, green only when a request's traces, metrics and logs arrive under the service's name; a project with a collector reads them back from it instead |
| Is any flag past its removal date? | the method's flags gate with `--expired`; it goes red by the calendar, so it is asked here and never on a pull request |
| Is the interface usable by everyone? | accessibility checks inside the end-to-end suite, for user interfaces; `impeccable audit` is the agent-side pass, the gate is the deterministic one |
| Does the model still hold, and does the code still mirror it? | the project-local formal verification skill: the owner's `/pstack:create-formal-verification-skill` writes it where a bug costs most and tests reach least, their `/pstack:maintain-formal-verification-skill` keeps it true to the code. The model and the skill are guard paths |
| Does every gate bite, and is it red with nothing to run? | the method's `scripts/bite.py` for the gates the plugin ships, then `scripts/project_bite.py` for the gates the repository bound: every fault of `quality/bites.toml`, and the emptied collection of each gate that judges one, green without the fault and red with it |

## Weekly and advisory

`audit-all` audits every dependency, dev tooling included. `licenses` checks the shipped dependencies' licences are compatible. `drift` lists outdated dependencies and writes the software bill of materials. `load-soak` holds the load for an hour and then raises it until the system breaks, and reports where: a leak and a breaking point are findings, each with its own pull request. The `weekly` entry point runs the four one after another. Two more join it once their events are declared, both bound by the repository:

| Question | Trigger | What commonly answers it |
| --- | --- | --- |
| Does the latest backup restore? | `life:irreplaceable-data` | the latest backup restored into a scratch store, then the deliverable booted on it with `with_service.py` and its smoke run; red when the restore fails or the deliverable does not answer |
| Does production answer? | `life:deployed` | the load smoke pointed at the live URL: the real routes, zero errors, a few seconds at a low rate. It also runs after each deploy | The forge's `weekly` workflow schedules it and opens or updates one issue while a run is red; with no forge the machine's scheduler runs it. `reports` prints hotspots (churn times complexity), coupling, cohesion and the complexity distribution: read before a refactor to pick targets, never as a score to chase.

## Speed

Gates that accumulate become gates that get skipped. So speed is a property the method asks for and measures from the first commit: the local run and the CI run are built to be fast before anything in them is slow, and the optimisation that usually arrives late is a standing obligation. The method names the concepts. The tools in brackets are examples; the binding's Speed section says how this stack meets each one (ADR 0019).

**Parallel by default.** The test suite uses every core, on a developer's machine as well as in CI, from the first test (`pytest-xdist`, vitest's worker pool, `cargo nextest`, `go test -p`). That forces each test to own its state: no shared file, port, database or clock. The tier 2 order-independence gate keeps them so. A tier's gates run at once as well, so the wall clock is the slowest gate and never the sum: in CI each gate is its own job, and a local run goes through a small runner that does the same on one machine (`scripts/parallel.py` here; `make -j`, `turbo`, `nx` or a pipeline engine elsewhere). The runner holds each gate's output whole, names every red gate, and records every gate's seconds so the slowest is always named. One list of gates per tier, kept beside the entry points, feeds the local run and the CI matrix alike. Gates that share state cannot run at once: two that write one file or directory, a gate that mutates the tree and the gates that read what it writes, two that bind one port. Prefer giving each its own output; declare the group only where that is not possible. A gate that is red in parallel and green alone shares state with another. One number caps how many gates run at once (`RATCHET_JOBS` in the `just` example), for a shared machine or a stack whose every gate already uses every core. The bite proof's scratch copies count against the same number, and since each gate's build also runs as many jobs as its tool is told (`CARGO_BUILD_JOBS`, `-j`), the load a tier puts on a machine is about that number times those build jobs.

**One pipeline, the same everywhere.** A CI job runs one entry point and nothing else, so a red job is reproduced on a developer's machine by one command, and CI cannot drift from the local run. A project that wants hermetic, cached steps puts a pipeline engine under the entry points (Dagger, Earthly, Bazel); the entry points stay the interface.

**Shard before you need to.** The long gates (the suite, mutation, end to end) run in CI as a matrix over a shard count, and the count is one line of the binding, `- Shards: 1` on day one. Each job receives `SHARD` and `SHARDS`, and the recipe hands them to its tool (`pytest-split`, `vitest --shard`, `cargo nextest --partition`, the mutation tool's own shards); a local run is shard 1 of 1. Adding runners later is that one number, never a restructuring.

**Cache everything, keyed on what it depends on.** Dependencies and every tool cache (type checker, linter, test, mutation, build), keyed on the lockfile and the tool's config with fallback keys; the toolchain pinned, with the setup action's own cache on. Locally the tools' incremental caches stay on.

**Run only what the change can affect.** Mutation of changed functions, coverage of changed lines, the flag states of changed flags, incremental type checking and builds; path filters, and an affected-only tool in a monorepo (`turbo`, `nx`, `pants`). The default branch runs everything.

**Fail fast locally, report everything in CI.** Locally an inner-loop entry point (`check-fast`) runs the same gates and stops at the first red; `check` runs them all and names every red. In CI a matrix never stops early (`fail-fast: false`), so one run shows every red, and a pushed fix cancels the run it replaces.

**Measured, not hoped.** `quality/thresholds.toml` holds `[ceilings.seconds]` with one key per tier, and one per gate where a gate has its own runner. The `budget` entry point wraps a tier or gate, measures its wall clock and goes red over its ceiling. A ceiling only falls: `ratchet-lowered` fails a raised one as it fails a lowered threshold, and raising one is a loosening with an ADR behind it, whose `Relaxes:` line ([ratchet.md](ratchet.md)) the gate reads. Adopt sets the first ceilings from the first measured run with headroom (`budget --set`), never under the tier's budget in the table above, so a young project's few seconds do not become the next feature's ceiling; from then on every change that pushes a tier over must pay for itself in the same pull request. Runners vary, so the ceiling blocks on the default branch and nightly, in the tier 2 sense, and reports on pull requests: the CI template passes `budget --over report` there, which prints and records the red and exits 0. In CI a tier is one job per gate, so its wall clock is its slowest gate, and each job is held to its tier's ceiling as well as its own (`budget --tier`). Every CI job appends its timing (`budget --report`) and uploads it, so the slowest gate is always named.

The conformance gate checks what a machine can: the binding's Speed section has a line for `Parallel`, `One pipeline`, `Shards`, `Cache`, `Affected only` and `Ceilings`, none of them left unfilled; `Shards` is a whole number; every workflow cancels superseded runs and caches; the tier workflows run their long gates over the shard count and hold each job to its tier's ceiling; and `ci.yml` says that a ceiling reports on a pull request.

What speed never buys: a quarantined test (a failure here is a bug, never "flaky"), a tier 2 gate moved to on-demand to hide its cost, or a `timeout-minutes` so generous it never fires.

## Choosing a tier for a new question

Fast and local: tier 0. Needs the whole tree or a test run: tier 1. Needs more than the pull-request budget: tier 2. Then choose its trigger: `always` only when it needs no setup and runs in seconds, the kind of code it guards when it costs more to add at the fortieth instance than at the first, and the life event that gives it something to judge otherwise. The catalogue is a floor: a project adds rows for what its domain demands.

## Triggers

Safeguards are added by judgement, per project, at the stage they earn their keep. The method is for hobby and solo projects first, and it must never block the owner in the middle of an idea (ADR 0028). Asking everything late is no answer either: a timeout on the first outbound call is minutes, and on forty after an outage it is a week. So every question carries a trigger in [application-types.toml](application-types.toml), and it is asked of a repository once its trigger has fired. Before that its row is bound `later: <trigger>` and judges nothing.

- **`always`: the floor.** These need no setup and run in seconds: format, lint, strict types, secrets, commit subjects, the guards, the method's own gates and the tests that exist. They hold from the first commit.
- **`code:<kind>`: the first instance of a kind of code.** An entry point, an outbound call, a listener, a configuration read, a stored format, a user interface, and the other kinds the catalogue lists. The binding's `## Triggers` table says what marks an instance in this stack: paths, and a pattern where a path is not enough. `scripts/triggers.py` counts each kind's instances at the merge base and at head. No owner is needed, and the count is deterministic.
- **`life:<event>`: an event in the project's life.** `deployed`: people other than the owner can reach it. `irreplaceable-data`: it holds data the owner could not recreate. `depended-on`: people outside the project rely on it. A machine cannot see these reliably, so the owner declares them on the binding's `- Life events:` line. The route proposes one when it sees a sign of it (a deploy file for `deployed`, a migrations directory for `irreplaceable-data`), and never declares it.

**The first one is free.** The change that brings the first instance of a kind is never red for it. `triggers.py` says that the kind has arrived and which rows it makes **owed**, and the route makes binding them the next step: `/ratchet:safeguard`, where an agent chooses the tool, writes the recipe and the fault, and binds the row. A later change that adds another instance while a row is still owed is red. Retrofitting then costs one instance, and the owner never stops mid-idea to build a safeguard. A declared life event makes its rows owed in the same way, and is never red: the route puts binding them first.

**Read from the merge base.** The life events and the Triggers table are read at the merge base wherever they decide whether a change waits for the owner, so a change cannot loosen its own judge. Both live in the binding, which is a guard path.

**Nothing unbinds.** A row already bound stays bound, whatever its trigger. The ratchet does not loosen, so a repository adopted before triggers keeps every gate it has.

### Every question, its tier and its trigger

Each group says why its questions wait for that trigger and no later. A row's applies-to types are in [application-types.toml](application-types.toml); the tier is the binding template's.

| Trigger | Questions, with their tier | Why it sits there |
| --- | --- | --- |
| `always` | format (0), lint (0), strict types (0), dead code and dependency hygiene (0), scripts and CI lint (0), secrets (0), feature flags declared (0), pins and least privilege (0), size and complexity limits (0), marker comments did not rise (0), prose vocabulary (0), effective-config snapshots (0), no threshold lowered (0), no suppression kind rose (0), commit subjects (0), duplication (1), build (1), every changed gate still bites (1), change size (1), guard paths changed (1), permission rules mirror the manifest (1), method conformance (1), incidents tighten a gate (1), every gate bites (2) | They need no setup, take seconds, and judge only what exists: an empty flag registry or no incident record passes. Most protect the method itself, and asking them later saves nothing. |
| `code:source`: a module of the project's own logic | unit, property and fitness tests (1), coverage (1), diff coverage (1), test count did not fall (1), changed-code mutation (1), static security analysis (1), type coverage (1), full mutation (2), suite order-independent and repeatable (2) | A suite and its analysers cost minutes around one module and a week around forty, and tests written late test what the code does rather than what it should. |
| `code:entry-point`: a route, command, job handler or screen | starts and answers (1), telemetry emitted (1), load smoke (1), end to end (2) | Registering one entry point through an instrumented seam is minutes; moving forty into one is a week. A boot command written for the first route serves every later one. |
| `code:outbound-call`: a call to something the project does not run | survives failing dependencies (1) | A timeout on the first call is a line. Forty calls with none are found in an outage. |
| `code:config-read`: a setting read from the environment or a file | refuses bad configuration (1) | One place reads settings while there is one setting. Spread over forty modules, a missing one fails late and far from its cause. |
| `code:listener`: a server, a worker loop, a consumer | shuts down gracefully (1) | The shutdown path is written once, beside the loop. Added later, it meets work in flight the loop was never built to finish. |
| `code:stored-format`: a schema, migration or file format written and read back | old data still loads (1), rolls back safely (1) | A fixture of an old version can only be captured while that version exists, and expand and contract is a habit cheapest from the first migration. |
| `code:public-interface`: an exported symbol, a published route or schema | contract drift (1), public surface changed on purpose (1), documentation coverage (1), deep interface conformance (2) | Before there is a surface there is nothing to snapshot. After it, a change to it should be on purpose, and its documentation ratchets from the size it had. |
| `code:untrusted-input`: a parser or decoder of outside bytes | fuzzing (2) | A fuzz target sits beside one parser. Its corpus grows from the first run. |
| `code:user-interface`: a page, screen or component | accessibility (2) | Accessible components are built, not retrofitted: the fortieth screen copies the first. |
| `code:container`: an image or infrastructure file | configuration hardening (1), artefact and image scan (1) | Nothing to scan before there is an image; one hardened Dockerfile is the template for the rest. |
| `code:dependency`: a shipped third-party package | shipped-dependency audit (1) | An audit with no dependency audits nothing. |
| `code:concurrency`: threads, shared memory, unsafe code, a lock | race and memory detectors (2) | A detector run over one lock finds its race; over forty, the races are already in the design. |
| `code:doc-example`: a code example in the documentation | documentation examples run (1) | Nothing to run before there is an example. |
| `code:flag`: a flag in `flags.toml` | changed flags off and on (1), no flag past its removal date (2) | The flag seam and its override are written for the first flag and serve every later one. |
| `code:layer`: a layer declared in `AGENTS.md` | dependency direction (0) | A direction needs two layers to point between. |
| `code:formal-model`: a model or proof the project keeps | symbolic checking (2), formal verification (2) | Asked of the model the project chose to write, from the day it exists. |
| `life:deployed` | reproducible build (2), dynamic security scan (2), active security scan (2), telemetry arrives (2), production answers (weekly and after a deploy) | They judge what other people meet. Before anyone else can reach it there is nothing to attack, nothing to watch arrive and nothing to redeploy. |
| `life:irreplaceable-data` | backup restores (weekly) | A restore drill before there is data worth restoring tests nothing. |
| `life:depended-on` | performance budget (2), load (2), with its weekly soak | A budget needs a load that people put on the system; before that its numbers are guesses. The load smoke already catches errors under concurrency from the first entry point. |

### What else waits for a life event

Some behaviour of the merge judge and the pipeline protects people other than the owner, so it starts when they exist. Guard paths, disputes and the ratchet hold from the first commit: they protect the method itself, and they cost nothing until something touches them.

- New surface with no flag that is off goes to the owner only once the project is `deployed`. Before that, nobody meets the surface. ([guards.md](guards.md#the-merge-judge))
- A one-way door goes to the owner only once the project is `deployed` or holds `irreplaceable-data`. Before that, nothing is lost by walking back through it.
- The pipeline's critic and its fix loop run only once the project is `depended-on`. Before that, a ticket is built by spec, implement and review, and a finding the review opens is still open. ([pipeline.md](pipeline.md))

### How each concern splits across the tiers

Once a concern's trigger has fired, the part a developer waits for is the cheap part.

| Concern | Tier 0, seconds | Tier 1, the pull request | Tier 2, the default branch and nightly | Weekly |
| --- | --- | --- | --- | --- |
| Telemetry | | every entry point emits its span, in memory | the signals arrive at a collector | |
| Feature flags | the registry: declared, owned, off when new | the suite with each changed flag off and on | no flag past its removal date | |
| Security | secrets, pins | static analysis, dependency audit, configuration hardening, artefact or image scan | passive and active scans over the contract's routes, fuzzing | `audit-all` |
| Load | | concurrent requests, zero errors | latency and throughput against the Limits | soak and breaking point |
| Reliability | | a failing dependency survived, bad configuration refused, a graceful shutdown, the merge base's build on the head's data | | the latest backup restores, production answers |

A pull request pays only for what it changed: the flag gate reruns the suite for the flags the change touches and for none when it touches none, the load smoke is seconds long, and the telemetry tests are unit tests with an exporter that keeps spans in memory. What needs a running environment, a clock or a network waits for tier 2. The binding's Telemetry and Feature flags sections name the seams once their rows are owed: the one module that starts the telemetry SDK and through which every entry point is registered, and the one module through which a flag is read. Whether new behaviour sits behind a flag at all is not a gate's question, since some new surface has nothing to turn off: the merge judge asks it, and once the project is `deployed` a change that adds a route, a command or an exported symbol with no flag that is off waits for the owner ([guards.md](guards.md)).

A repository whose application types leave a row out (a library has nothing to boot, to load or to scan from outside) binds it `none: not a <type>`; the rows themselves stay, and the conformance gate checks they are there.

## What a recipe must get right

A tool is chosen per repository, so no recipe is shipped for it. These hold for any tool, and each was learnt on a binding where a gate passed while judging nothing or failed for a reason that was not the code's.

- **A gate once asked is red with nothing to run.** A suite that collects no test, a performance gate with no benchmark, a fuzz gate with no target, a telemetry gate with no telemetry test: each fails, on every stack. A row is bound only after its trigger has fired, so the set it judges already has a member. A row whose trigger has not fired is bound `later: <trigger>` and has no recipe yet; `none` is for a property the project will never have. Both are written in the binding, never printed by a recipe that found nothing.
- **A gate over the change may find nothing to judge, and says so.** Mutation of changed functions, coverage of changed lines and the flag states of changed flags are green when the change touches nothing in their scope, and only when the tool itself reports zero items: a tool that fails for another reason is red.
- **A time box is per thing judged, under a cap.** A fuzz run gives each target a fixed number of seconds and stops at a cap on the total, and a soak or a repeated run scales the same way. The cost of a gate then follows the code: a project with one target pays for one, and the cap is what the tier's ceiling allows. A single fixed box makes an empty project wait as long as a large one.
- **A gate over the change takes its base from the method, and judges nothing on the adoption.** `scripts/changed.py -- <command>` hands the command the commit the change is compared with, the one the shipped gates use. The adoption is the change whose base holds no binding: every line is new against it, so such a gate would redo on each run what tier 2 does once over the whole tree. There the wrapper runs nothing and says so. It reads the base commit, so no later change can claim to be the adoption. The `bite` gate does the same, and `bite-all` proves every gate once. In a scratch copy of the bite proof the default branch stands at `HEAD`, so the change is the planted fault and such a gate judges that alone.
- **A failed collection is red, never a count of zero.** The test-count gate compares two counts; an install, an import or a listing that fails at either side is a failure, not a smaller number.
- **Mutation is judged by the register, never the score.** The score is a trend. Clear the tool's cache of verdicts before a run, so a new test meets the old survivors again.
- **A mutant that does not build is not a kill.** The recipe reports it as `unviable`, the gate counts it apart and prints the count, and a run whose every mutant was unviable is red. Build mutants with warnings left as warnings: a lint promoted to an error turns most mutants unviable, and the gate then reports a run that tested a fraction of them.
- **The mutation tool runs its mutants at once**, by its own parallel mode set in the recipe, and one shard of them when handed `SHARD`. One mutant at a time is hours on a tree of any size.
- **A bite of a whole-tree gate is narrowed.** Its `args` name one file, one target or one mutant, as a fuzz bite names its seconds. The bite shows the gate is alive; the whole run stays tier 2's.
- **What a gate starts, it stops, even when it is killed.** A test that boots the deliverable does it through `with_service.py`, or in a process group its runner ends: a mutation tool kills a hung test and not what the test forked. `parallel.py` and the bite proof end what a gate left running when it exits and say so, and the line names a recipe to fix.
- **A gate that judges elapsed time is measured alone.** Name it in the runner's `--alone` list (`alone` in the `just` example): it runs once the other gates have finished, by itself. Its ceiling is set from a run on a quiet machine or on the CI runner; a reading taken beside fifteen other gates measures the machine.
- **A gate that requests the deliverable's routes from outside boots it with every flag due for removal on.** New surface ships off, so interface conformance, a scan, a load smoke or a load test of the defaults meets a 404 on each flagged route and is green on what it never reached. `flags.py --temporary` prints the key of every flag with a `remove-by` date, and the recipe hands them, on, to the override the flag seam reads (`probed` in the `just` example, through `--env` of `with_service.py`). A `permanent` flag is a switch and keeps its default. The defaults are what ships: `smoke`, the end-to-end suite and the off half of `flag-states` judge them.
- **A scan is shown to reach the routes.** A scanner that follows links from the root finds none in an API that serves no pages, and is green on the one answer it saw. Feed it the published contract, or the requests the end-to-end suite makes, and plant its bite's fault on a route of the contract other than the root: a scan that never requests that route does not bite.
- **A rule set for a framework is judged only when the framework is a dependency.** Static-analysis packs bundle rules for frameworks the project does not use, and one of them firing on a look-alike call is noise; the language and runtime rules always apply, and the recipe prints how many it left out.
- **A generated artefact that is not committed fails its drift gate.** Comparing with the index alone passes an untracked report.
- **Every tool runs through the package manager that locks it**, so the lockfile pins the gate as it pins the product, and the audit of shipped dependencies leaves the development tools to the weekly `audit-all`. A pinned image that fetches at run time is not pinned: a tool that downloads its rules, add-ons or database when it starts is handed them at a fixed version, or told not to update.
- **Each coverage group is measured by a report that reaches it.** Two suites write two reports and one judgement reads both; a fitness test's logic lives where coverage measures it.
- **A repeated run takes a fresh seed and prints it**, so a failure in the order-independence gate replays.
- **A recipe builds what it runs, every time.** In CI each gate is a job of its own, so the boot command of a service gate compiles first, and nothing depends on what another recipe left behind: a coverage report, a build directory, a cache. A recipe that skips the build when its output is already there judges the last build.
- **A red gate says what is wrong.** A recipe that ends on a bare comparison or a tool's exit code prints nothing a reader, or a bite's `expect`, can tell from any other failure. It prints the rule and the number.
- **A threshold starts at the measured value.** A ratchet recorded as zero is a gate that cannot fail.
- **A tool that walks the tree is pointed at the source.** `quality/snapshots/` holds copies of the tool configs, a dependency manifest among them, which reads as a second package to a tool that searches for manifests.
- **A snapshot of the product's own contract lives beside the tests.** A command's `--help`, a public API report or a schema changes with the feature that changes the contract. Under `quality/`, a guard path for what only rises, it could land neither ahead of its feature nor with it.
- **A prose gate that flags the owner's own text is answered in the text.** When the `prose` gate flags a word on a glossary *Avoid* list, the text is reworded; an *Avoid* list is never shortened to turn a gate green.
- **A gate red in parallel and green alone shares state with another.** Separate their outputs (a report path, a cache directory, a work tree of its own per gate); only when that is not possible, declare the group in that tier's `serial` variable (`--serial A B`).

## Every bound gate is shown to bite

The method ships no tool, so it cannot promise that a repository's recipes work. The repository shows it. `quality/bites.toml` holds one `[[bite]]` or more for each Gates row whose Tool cell names a tool the repository chose, a row that pairs a plugin script with a tool of the stack included. A row bound `none` or `later: <trigger>` needs none, and nor does a row bound to the plugin's scripts alone: `scripts/bite.py` proves those. A row's bite is written when the row is bound, in the same change.

A bite names its `gate` (the last word of the row's entry point), the fault in a few words, exactly one fault, and `expect`: words the gate prints when it is red on this fault. The fault is `write` (create or replace one file), `patch` (a diff under `quality/bites/`) or `remove` (delete what the globs match). A gate that judges a collection, which `application-types.toml` marks, carries a `remove` bite as well: the collection emptied, and the gate red. A question may also require a proof by name, a fault that only its gate can catch, and a bite claims it with `proof`. The catalogue requires three: `outside-the-seam` of `telemetry emitted` (an entry point registered outside the instrumented seam), `broken-flag-state` of `changed flags off and on` (a flag whose on state breaks a test its off state passes) and `erroring-route` of `load smoke` (a route the smoke requests that answers with an error). The repository writes the fault; the name says what it must be. `args` are appended to the gate's command, and `commit = true` commits the fault first, for a gate that reads history.

`scripts/project_bite.py` makes a scratch copy of the work tree as git sees it, runs the registry's `setup` there, and for each bite runs the gate (green), plants the fault, and runs it again. `bites` is the one green verdict. `DOES NOT BITE` is a gate that cannot fail on what it is for. `RED BEFORE THE FAULT` is a gate that is red on a fresh checkout, usually because it leans on what another recipe left behind. `RED FOR ANOTHER REASON` is a gate red without the words the bite expects, or a bite whose words the gate prints as often when it is green. `THE FAULT WAS NOT PLANTED` is a fault that could not be written, applied or removed in the copy. Each is fixed in the recipe or the fault, never by loosening `expect` until it matches. The real work tree is never written, and nothing git ignores is copied, so a gate is judged as a CI job would run it.

The whole proof is `bite-all`, in tier 2. On a pull request `bite` asks whether the change touches a path that defines a gate, and proves the tier 0 and tier 1 gates only then. The conformance gate is red on a bound gate with no bite, on a collection gate with no `remove` bite and on a bound gate with no bite for a proof its question requires, so a gate cannot be added unproven. The registry and its patches sit under `quality/`, a guard path: dropping a bite asks the owner.

One fault shows that a gate is alive and wired to the thing it judges. It does not show that the tool's rule set is complete. A gate nobody can plant a fault for is not a gate: its row is bound `none`, with the reason and an ADR.

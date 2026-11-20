# Tiers and the questions each answers

The method asks questions; the binding names the tool that answers each one for this stack. The method ships the questions and never the tools: which questions apply is decided by what kind of software the repository is, when one is first asked is decided by its [trigger](#triggers), and which tool answers one is chosen when it is first asked, from what is current then (ADR 0017, ADR 0028). A question that applies and has no tool in the stack is bound to `none` with the reason and the ADR that accepts the gap: that row is green, and a standing criticism of the binding until a tool exists. A question is never advisory because its tool is inconvenient; advisory is for reports that inform a choice rather than judge a change.

## Application types

A binding declares what the repository is, on one line: `- Application types: service, web-ui`. A repository may be several.

A type decides which questions apply. It never decides the tool. Every question applies to every type unless [application-types.toml](application-types.toml) narrows it, and that file is the single source: the conformance gate reads it, and `python3 scripts/application_types.py <type>...` in the plugin prints the rows a set of types leaves out. The weekly `load-soak` follows `load`.

| Catalogue table | Holds |
| --- | --- |
| `[types]` | each application type and what it is |
| `[kinds]` | each kind of code a `code:` trigger names; its text is what the binding's Triggers row matches |
| `[architecture]` | each trigger whose first arrival reopens the architecture, with the question it reopens |
| `[events.<event>]` | each life event: its `meaning`, and either `signs`, globs over the tree the route reads to propose it, or `in-binding = true`, where the binding's `## Life event signs` table names this stack's signs |
| `[proofs]` | each named proof and the fault it plants |
| `[[question]]` | one per question the catalogue narrows |

A `[[question]]` holds:
- `name`: the question, as the binding's Gates table spells it;
- `applies-to`: the types it is asked of, all six when absent;
- `trigger`: `always` when absent, `code:<kind>`, `life:<event>`, or parts joined by `and`;
- `collection = true`: its gate judges a set the project writes, so it carries a `remove` bite;
- `directives = true`: a directive in the code can tell its tool to skip a finding, so the binding's `## Suppressions` names the kinds that silence it;
- `proofs`: the named proofs its gate's bites claim.

A question the catalogue does not list is asked of every type, always, and judges no collection.

A row is never dropped. One that applies names a tool or is an accepted gap, as above. One that applies to none of the declared types says `none: not a <type>`, naming a type the question is asked of, and needs no ADR: the type line is the reason. One that applies and whose trigger has not fired says `later: <trigger>`, and judges nothing until it fires.

## When each tier runs

| Tier | When | Entry point | Budget | Blocks |
| --- | --- | --- | --- | --- |
| 0 | every commit | `tier0`; the commit hooks split it, `pre-commit` on the tree before the commit and `commit-msg` on the message being written, since `commits` over history would judge the commit an amend replaces | about a minute | the commit, and the pull request |
| 1 | every pull request | `tier1` | under fifteen minutes, so nobody is tempted to skip it | the pull request; on the default branch, as tier 2: a red tier 1 there is fixed before anything else merges, and while it is red the merge judge leaves every merge to the owner |
| 2 | merge to the default branch, nightly, on demand | `tier2` | unbounded; time-boxed where a tool runs forever | the next piece of work: a red tier 2 on the default branch is fixed before anything else merges, and while it is red the merge judge leaves every merge to the owner |
| weekly | scheduled | `audit-all`, `licenses`, `drift`, `load-soak`, and once their events are declared the backup restore and the production check | | nothing; the finding is visible and gets its own pull request |
| advisory | on demand | `reports` | | nothing, ever; read before a refactor to pick targets |

Tier 0 runs on a commit only if the commit hooks are installed, and a hook that is not installed fails silently: the commit goes through. A `core.hooksPath` in the git config, local or global, is the usual cause, since `pre-commit install` then refuses. So the method asks "would a commit here run tier 0?" with `scripts/hooks_installed.py`. It runs the hooks git would run for a commit, with `RATCHET_HOOK_PROBE` naming a file, and the `pre-commit` and `commit-msg` recipes answer by writing their own name to that file before any gate runs. That proves the whole chain, a global hook that chains to the repository's hooks included, without reading a hook script, and tier 0 itself never runs. Only those two recipes answer the probe; `tier0` and `check` call the gates directly, so a stray variable never skips one.

The feature preflight, the route and the `ratchet` recipe ask it, and each prints the repair while it is red. CI and `conformance` do not: a CI checkout has no hooks, and tier 0 runs there as a job. `conformance` checks only that the recipes can answer. A forged green needs a script written into `.git/hooks` (the `local-git` group denies the edit) or the recipes changed (`justfile` is a `gate-commands` path, which `guard-diff` lists). Setting the variable on a real commit would skip tier 0, and the guard denies that like `SKIP=`.

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
| Is every feature flag declared once, owned, off when new and due for removal? | the method's flags gate over `flags.toml`, one `[[flag]]` table per flag: `key`, `owner`, `default`, a `remove-by` date or `permanent = true`, and a `reason` of twenty characters or more |
| Is every dependency, action and image pinned exactly, and every CI job least-privileged? | lockfile matches the manifest with no floating range; actions and images pinned by digest; workflow permissions declared and minimal |
| Is every unit within its size and complexity limits? | the linter's size and complexity rules plus a fitness test, with the numbers the binding's Limits section holds; a hard limit, split the unit |
| Did the count of TODO, FIXME, HACK and XXX markers rise? | counted at the merge base and at head, never stored |
| Is every comment a directive a tool reads? | the method's comments gate over the binding's lister: a comment only on a line a Suppressions kind matches, a doc comment only in a library's published interface; what existed at adoption is owed until it is gone |
| Does prose use the glossary too? | a prose linter carrying the _Avoid_ lists over docs and comments |
| Do commit subjects follow the convention? | the method's commits gate: `type(scope): summary`, at most 72 characters, not counting the ` (#12)` a squash merge appends |
| Has any gate been loosened? | the method's own gates: snapshots match each tool config the guard manifest lists, no threshold lowered, no suppression kind rose, no focus marker |

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
| Does the deliverable start and answer? | for a service, the method's `scripts/with_service.py`; another type runs its own deliverable once. The boot line passes `{host}`, the ready URL's host, where the deliverable takes its address, so it is not open to the network while a gate holds it up |
| Does every entry point emit the telemetry the binding names? | tests against an in-memory exporter of the telemetry library: each route, job and command yields a span with the attributes the binding requires, a failure is recorded on its span, a log line carries the trace id; a fitness test fails an entry point registered outside the instrumented seam; the signal names are snapshotted beside the tests, so the telemetry surface changes on purpose |
| Does the code hold with each changed flag off and on? | the suite once per state of every flag the change adds or touches, through the override the flag seam honours; off is the merge base's behaviour |
| Does the deliverable stay correct under concurrent requests? | the method's `scripts/load_smoke.py` inside `with_service.py`: seconds of concurrent requests against the booted deliverable, zero errors and a floor on requests answered; red while it requests nothing but the readiness URL, which a service with no other route yet answers with `--health-only`; latency is printed, not judged, since a pull-request runner is no benchmark host |
| Does it refuse bad configuration? | the method's `with_service.py --refuses NAME`, repeatable: the deliverable booted with the environment variable `NAME` removed must exit non-zero before it answers and print the setting's name as a whole word. A setting read from a file the service finds itself is not removed |
| Does it shut down gracefully? | the method's `with_service.py --graceful SECONDS -- load_smoke.py --until-stopped ...`: SIGTERM reaches the service `--signal-after` seconds after the load smoke starts sending, a fixed point so every runner signals alike. Green only when the service exits 0 within `SECONDS` and every request it accepted was answered. After the signal a refused connection is expected, and so is a reset of a connection the service never accepted: the gate holds a copy of the listener and, once no process of the service holds it, names and resets each connection waiting there or arriving after. That needs Linux 5.6 or later and the ptrace permission over the service that the gate has over a process it started. On a port where the gate holds no listener, a reset counts only when its request started before the signal, as it did before the gate held listeners, and the smoke names the port and says why. The command must create the path `RATCHET_SENDING` once it sends, as the load smoke does, or the check is red. The boot command must `exec` the server, so the signal reaches it and not a shell |
| Does it survive a dependency that fails? | a timeout on every outbound call, held by a lint rule or a fitness test; a test double or a fault-injecting proxy in front of the dependency makes it answer with an error, answer slowly and vanish, and the test expects a bounded failure the code names |
| Does the merge base's build still run on the data the head leaves? | expand and contract migrations: add a column or a field, write both, and remove the old one in a later change. The recipe applies the head's migrations to a scratch store and runs the merge base's build, and its smoke, against it |
| Does every incident name the gate that now catches it? | incident records under `docs/incidents/`, each with a `Bite:` line naming a bite of `quality/bites.toml` that reproduces the incident ([records.md](records.md#incident-record-docsincidentsnnnn-slugmd)) |
| Did the change add another instance of a kind whose safeguards are still owed? | the method's `scripts/triggers.py` |
| Does every gate the change touches still bite? | the method's `scripts/project_bite.py --changed` |
| Did the public surface change on purpose? | a snapshot of exported symbols, routes or flags, diffed; a change needs a version bump or a changelog line |
| Does old persisted data still load? | golden fixtures from earlier versions read cleanly; migrations apply and reverse |
| Do the documentation's examples run? | doctests, or extracted snippets executed |
| Does the code contain a known insecure pattern? | static security analysis with the stack's rule set; findings fail |
| Does the container, infrastructure or deployment configuration break a hardening rule? | a configuration scanner over the container build files, the deployment manifests and the infrastructure code |
| Does the shipped artefact or base image carry a known vulnerability? | an image or artefact scanner, over the built image when the deliverable is one |
| How much of the code is typed, and did it fall? | type coverage, ratcheted, for gradually typed languages |
| How much of a library's published interface is documented, and did it fall? | documentation coverage, ratcheted |
| Is the change within the size budget? | files and lines against the merge base, a ceiling the binding holds; the method's own records, the owed ledger, generated lockfiles and their snapshots are not counted, and a moved file counts by its changed lines |
| Did a guard path change? | the method's guard diff, against the manifest at base and head |
| Do the permission rules still mirror the manifest? | the method's settings check |
| Does the repository still carry the method? | the method's conformance gate |

## Tier 2: the limits of the code

| Question | What commonly answers it |
| --- | --- |
| Would the tests notice any wrong code at all? | full mutation over the absolute paths |
| Does the interface hold under every sequence of calls? | stateful property tests against the published contract |
| Can a solver find an input that crashes it or breaks an assertion in it? | symbolic or concolic search of the code, alone or as the fuzzer's hybrid mode, each target for a fixed time under a cap on the total |
| Can input that follows the code crash it? | coverage-guided fuzzing with a kept corpus, each target for a fixed time under a cap on the total; a crash is a bug |
| Does the real system work through its real interface? | an end-to-end suite, the thin top of the pyramid |
| Does the suite pass in any order, twice? | shuffled order, repeated runs, fresh seeds; a failure here is a bug, never "flaky" |
| Is the build reproducible? | build twice, compare hashes |
| Does the code hold under a race or memory detector? | race detector and sanitizers where the stack has them |
| Is it within the performance budget? | benchmarks against the ceilings the binding holds for latency, allocation and bundle size; the owner's `/pstack:benchmark-checklist` before a number is trusted |
| Does the running system resist a dynamic scan? | a passive scan of the running deliverable, fed the routes of the published contract: headers, cookies, transport, disclosure |
| Can an attacker's toolkit break in? | an active scan with a signed-in session, and requests generated from the published contract that try each route's authentication, authorisation and input handling; a finding fails |
| Does it hold its latency and throughput under the load the binding names? | a load test against the end-to-end environment with the thresholds the binding's Limits hold: the arrival rate, the percentile latency, the error rate; the owner's `/pstack:benchmark-checklist` before a number is trusted |
| Does the telemetry arrive? | the method's `scripts/telemetry_sink.py`; a project with a collector reads the traces, metrics and logs back from it instead |
| Is any flag past its removal date? | the method's flags gate with `--expired`; it goes red by the calendar, so it is asked here and never on a pull request |
| Is the interface usable by everyone? | accessibility checks over the components and pages, for user interfaces, inside the end-to-end suite once there is one; `impeccable audit` is the agent-side pass, the gate is the deterministic one |
| Does the model still hold, and does the code still mirror it? | a formal model an agent writes with `pstack:create-formal-verification-skill` where a bug costs most and tests reach least, and keeps true to the code with `pstack:maintain-formal-verification-skill`. `just formal` runs it whole on the default branch, and a time-out is red. Its proof is a break planted in the code the model mirrors, with the model unchanged, which the check catches. A broken property is red on the next tier 2 run, and the route then names a round of upkeep. The model, its harness and the skill are guard paths |
| Does every gate bite, and is it red with nothing to run? | the method's `scripts/bite.py` for the gates the plugin ships, then `scripts/project_bite.py` for the gates the repository bound |

## Weekly and advisory

`audit-all` audits every dependency, dev tooling included. `licenses` checks the shipped dependencies' licences are compatible. `drift` lists outdated dependencies and writes the software bill of materials. `load-soak` holds the load for an hour and then raises it until the system breaks, and reports where: a leak and a breaking point are findings, each with its own pull request. The `weekly` entry point runs the four one after another. Two more join it once their events are declared, both bound by the repository:

| Question | Trigger | What commonly answers it |
| --- | --- | --- |
| Does the latest backup restore? | `life:irreplaceable-data` | the latest backup restored into a scratch store, then the deliverable booted on it with `with_service.py` and its smoke run; red when the restore fails or the deliverable does not answer |
| Does production answer? | `life:deployed` | the load smoke pointed at the live URL (`load_smoke.py --ready <live health URL> --url <live route>`, no service booted): the real routes, zero errors, a few seconds at a low rate. It also runs after each deploy |

The forge's `weekly` workflow schedules it and opens or updates one issue while a run is red; with no forge the machine's scheduler runs it. `reports` prints hotspots (churn times complexity), coupling, cohesion and the complexity distribution: read before a refactor to pick targets, never as a score to chase.

## Speed

Gates that accumulate become gates that get skipped. So speed is a property the method asks for and measures from the first commit: the local run and the CI run are built to be fast before anything in them is slow, and the optimisation that usually arrives late is a standing obligation. The method names the concepts and no tool; the binding's Speed section says how this stack meets each one (ADR 0019).

**Parallel by default.** The test suite uses every core, on a developer's machine as well as in CI, from the first test, through the test runner's own parallel mode or one the stack adds to it. That forces each test to own its state: no shared file, port, database or clock. The tier 2 order-independence gate keeps them so. A tier's gates run at once as well, so the wall clock is the slowest gate and never the sum: in CI each gate is its own job, and a local run goes through a small runner that does the same on one machine (`scripts/parallel.py` here and in the `just` example; elsewhere the task runner's own parallel mode or a pipeline engine). One list of gates per tier, kept beside the entry points, feeds the local run and the CI matrix alike. Gates that share state cannot run at once: two that write one file or directory, a gate that mutates the tree and the gates that read what it writes, two that bind one port. Prefer giving each its own output; declare the group only where that is not possible. A gate that is red in parallel and green alone shares state with another. One number caps how many gates run at once, for a shared machine or a stack whose every gate already uses every core: set once in the version-control tool's configuration (`ratchet.jobs`), for the whole machine or for one repository, it is read by every work tree and every agent's shell, and an environment variable (`RATCHET_JOBS` in the `just` example) overrides it for one shell. Since each gate's build also runs as many jobs as its tool is told by its own jobs setting, the load a tier puts on a machine is about that number times those build jobs.

**One pipeline, the same everywhere.** A CI job runs one entry point and nothing else, so a red job is reproduced on a developer's machine by one command, and CI cannot drift from the local run. A project that wants hermetic, cached steps puts a pipeline engine under the entry points; the entry points stay the interface.

**Shard before you need to.** The long gates (the suite, mutation, end to end) run in CI as a matrix over a shard count, and the count is one line of the binding, `- Shards: 1` on day one. Each job receives `SHARD` and `SHARDS`, and the recipe hands them to its tool (the test runner's or the mutation tool's own sharding, or what the stack adds for it); a local run is shard 1 of 1. Adding runners later is that one number, never a restructuring.

**Cache everything, keyed on what it depends on.** Dependencies and every tool cache (type checker, linter, test, mutation, build), keyed on the lockfile and the tool's config with fallback keys; the toolchain pinned, with the setup action's own cache on. Each key ends in the commit, and the fallback key is the same key without it, so every run restores the newest cache and saves its own: a key that hits exactly is never saved again, and a tool cache behind it stays as its first run left it. A job per gate keys its own cache by its gate. Locally the tools' incremental caches stay on, since CI runs every gate without them before the merge; the binding's `Incremental` line names the mutation tool's.

**A stack that compiles.** Builds are most of a compiled stack's gate time, so its binding keeps one content-addressed compiler cache outside the work tree: it stores each compiled unit under a key of its inputs (sources, flags, toolchain) and copies the result out on a hit, so a planted fault is a miss and never a stale hit. Every gate, scratch copy and worktree reuses it, and CI restores it. It is never a shared build output directory: each work tree, scratch copy and gate keeps its own build output inside itself, so parallel lanes never judge one another's builds. It builds in the bite registry's `setup`, which runs in every scratch copy: the first copy runs it alone and fills the cache, the later copies' builds are cache hits, and each planted fault then rebuilds incrementally in its own copy. It installs each tool as the publisher's prebuilt release where one exists, and not by building it from source, at a pinned version and with the recipe checking the publisher's checksum (the `pins` gate does not read a download's checksum, so the recipe owns it). It pins the toolchain before the first build, so nothing is built twice after a toolchain change. And it knows that each separately compiled unit adds build time to every gate: a layer rule can be held inside one unit by the imports gate, so the architecture round states that cost when it offers a split. A recipe still builds what it runs: an incremental build from a cache is a build (ADR 0019, ADR 0059).

**Run only what the change can affect.** Mutation of changed functions, coverage of changed lines, the flag states of changed flags, the test files a change adds or edits through each suite's own entry point (`tests-changed`, ADR 0073), incremental type checking and builds; path filters, and an affected-only tool in a monorepo. The default branch runs everything.

**Fail fast locally, report everything in CI.** Locally an inner-loop entry point (`check-fast`) runs the same gates and stops at the first red; `check` runs them all and names every red. In CI a matrix never stops early (`fail-fast: false`), so one run shows every red, and a pushed fix cancels the run it replaces.

**Measured, not hoped.** `quality/thresholds.toml` holds `[ceilings.seconds]` with one key per tier, and one per gate where a gate has its own runner. A ceiling only falls: raising one is a loosening with an ADR behind it, whose `Relaxes:` line ([ratchet.md](ratchet.md)) `ratchet-lowered` reads. Adopt sets the first ceilings from the first measured run with headroom (`budget --set`), never under the tier's budget in the table above, so a young project's few seconds do not become the next feature's ceiling; from then on every change that pushes a tier over must pay for itself in the same pull request. Runners vary, so the ceiling blocks on the default branch and nightly, in the tier 2 sense, and reports on pull requests.
What speed never buys: a quarantined test (a failure here is a bug, never "flaky"), a tier 2 gate moved to on-demand to hide its cost, or a `timeout-minutes` so generous it never fires.

## Choosing a tier for a new question

Fast and local: tier 0. Needs the whole tree or a test run: tier 1. Needs more than the pull-request budget: tier 2. Then choose its trigger: `always` only when it needs no setup and runs in seconds, the kind of code it guards when it costs more to add at the fortieth instance than at the first, and the life event that gives it something to judge otherwise. The catalogue is a floor: a project adds rows for what its domain demands.

## Triggers

Safeguards are added by judgement, per project, at the stage they earn their keep. The method is for hobby and solo projects first, and it must never block the owner in the middle of an idea (ADR 0028). Asking everything late is no answer either: a timeout on the first outbound call is minutes, and on forty after an outage it is a week. So every question carries a trigger in [application-types.toml](application-types.toml), and it is asked of a repository once its trigger has fired. Before that its row is bound `later: <trigger>` and judges nothing.

- **`always`: the floor.** These need no setup and run in seconds: format, lint, strict types, secrets, commit subjects, the guards, the method's own gates and the tests that exist. They hold from the first commit.
- **`code:<kind>`: the first instance of a kind of code.** An entry point, an outbound call, a listener, a configuration read, a stored format, a user interface, and the other kinds the catalogue lists. The binding's `## Triggers` table says what marks an instance in this stack: paths, and a pattern where a path is not enough. `scripts/triggers.py` counts each kind's instances at the merge base and at head. No owner is needed, and the count is deterministic.
- **`life:<event>`: an event in the project's life.** `deployed`: people other than the owner can reach it. `irreplaceable-data`: it holds data the owner could not recreate. `depended-on`: people outside the project rely on it. A machine cannot see these reliably, so the owner declares them on the binding's `- Life events:` line.
- **Both, joined by `and`.** `life:deployed and code:stored-format` fires when every part has: a question some deployed projects never meet (a static site stores nothing) waits for the event and the kind together, and its second instance of the kind is the red.

**The first one is free.** The change that brings a kind is never red for it: it takes the kind from no instances at the merge base to any number, in one file or many (ADR 0030), and only the first time: a kind that held instances earlier on the default branch, went back to none and returns is owed, so adding to it is red until the row is bound (ADR 0048). What bounds that change is the change-size ceiling once the repository has recorded one; a repository with none has no bound yet (ADR 0030). `triggers.py` says that the kind has arrived and which rows it makes **owed**, and the route makes binding them the next step: `/ratchet:safeguard`, where an agent chooses the tool, writes the recipe and the fault, and binds the row. A later change that adds another instance while a row is still owed is red. Retrofitting then costs what one change brought, and the owner never stops mid-idea to build a safeguard. A declared life event makes its rows owed in the same way, and is never red: the route puts binding them first.

**Read from the merge base.** The life events and the Triggers table are read at the merge base wherever they decide whether a change waits for the owner, so a change cannot loosen its own judge. Both live in the binding, which is a guard path.

**Nothing unbinds.** A row already bound stays bound, whatever its trigger. The ratchet does not loosen, so a repository adopted before triggers keeps every gate it has.

**Some arrivals reopen the architecture** (ADR 0052). The architecture the adoption chose fits the project it saw. When a kind of code that changes the project's shape arrives for the first time, or the owner declares a life event, an **architecture review** is owed beside any rows, and owed the same way: the arrival is free and noted, a later change that adds another instance of the kind is red until the review has landed, and an event is never red. The route names `/ratchet:architecture` before `/ratchet:safeguard`, since the review decides where the new kind lives and so where its safeguards go. The review re-reads the architecture decision and the declared layers against the code as it now is, and records either that the decision holds or, after one question to the owner, the option he chose, with the layers updated so the gates hold. It counts once a decision record at the merge base names the trigger on its `- Architecture review:` line, with `- Outcome: holds` or `changes`, `- Decided:`, a `## Decision`, a `## Refutation criterion`, and two or more options under `## Considered options` when it changes the architecture; a record in the same change as another instance does not count. The `architecture reviewed when its shape changes` row asks it.

### Which triggers reopen the architecture

The catalogue's `[architecture]` table holds each, with the question its arrival reopens. Every repository's Triggers table has a row for each of these kinds, whatever its types.

The other kinds are more of the shape the adoption drew (a module, a route, a setting, a package, an example, a flag, a proof), packaging whose place is decided at `deployed`, or a surface that becomes a promise at `depended-on`.

### Every question, its tier and its trigger

Each group says why its questions wait for that trigger and no later. A row's applies-to types are in [application-types.toml](application-types.toml); the tier is the binding template's.

| Trigger | Questions, with their tier | Why it sits there |
| --- | --- | --- |
| `always` | format (0), lint (0), strict types (0), dead code and dependency hygiene (0), scripts and CI lint (0), secrets (0), feature flags declared (0), pins and least privilege (0), size and complexity limits (0), marker comments did not rise (0), no prose comments (0), prose vocabulary (0), effective-config snapshots (0), no threshold lowered (0), no suppression kind rose (0), commit subjects (0), duplication (1), build (1), every changed gate still bites (1), change size (1), guard paths changed (1), permission rules mirror the manifest (1), method conformance (1), incidents tighten a gate (1), every gate bites (2) | They need no setup, take seconds, and judge only what exists: an empty flag registry or no incident record passes. Most protect the method itself, and asking them later saves nothing. |
| `code:source`: a module of the project's own logic | unit, property and fitness tests (1), coverage (1), diff coverage (1), test count did not fall (1), changed tests run (1), static security analysis (1), type coverage (1), suite order-independent and repeatable (2) | A suite and its analysers cost minutes around one module and a week around forty, and tests written late test what the code does rather than what it should. |
| `code:entry-point`: a route, command, job handler or screen | starts and answers (1), telemetry emitted (1), load smoke (1) | Registering one entry point through an instrumented seam is minutes; moving forty into one is a week. A boot command written for the first route serves every later one. |
| `code:outbound-call`: a call to something the project does not run | survives failing dependencies (1) | A timeout on the first call is a line. Forty calls with none are found in an outage. |
| `code:config-read`: a setting read from the environment or a file | refuses bad configuration (1) | One place reads settings while there is one setting. Spread over forty modules, a missing one fails late and far from its cause. |
| `code:listener`: a server, a worker loop, a consumer | shuts down gracefully (1) | The shutdown path is written once, beside the loop. Added later, it meets work in flight the loop was never built to finish. |
| `code:stored-format`: a schema, migration or file format written and read back | old data still loads (1) | A fixture of an old version can only be captured while that version exists. |
| `code:public-interface`: an exported symbol, a published route or schema | contract drift (1), public surface changed on purpose (1), documentation coverage (1, a library only), deep interface conformance (2) | Before there is a surface there is nothing to snapshot. After it, a change to it should be on purpose, and its documentation ratchets from the size it had. |
| `code:untrusted-input`: a parser or decoder of outside bytes | fuzzing (2), symbolic checking (2), formal verification of untrusted input (2) | A fuzz target sits beside one parser. Its corpus grows from the first run, and a solver takes the branches it cannot reach. |
| `code:user-interface`: a page, screen or component | accessibility (2) | Accessible components are built, not retrofitted: the fortieth screen copies the first. |
| `code:container`: an image or infrastructure file | configuration hardening (1), artefact and image scan (1) | Nothing to scan before there is an image; one hardened container build file is the template for the rest. |
| `code:dependency`: a shipped third-party package | shipped-dependency audit (1) | An audit with no dependency audits nothing. |
| `code:concurrency`: threads, shared memory, unsafe code, a lock | race and memory detectors (2), formal verification of concurrency (2) | A detector run over one lock finds its race; over forty, the races are already in the design. |
| `code:doc-example`: a code example in the documentation | documentation examples run (1) | Nothing to run before there is an example. |
| `code:flag`: a flag in `flags.toml` | changed flags off and on (1), no flag past its removal date (2) | The flag seam and its override are written for the first flag and serve every later one. |
| `code:layer`: a part declared in the binding's Architecture section | dependency direction (0) | Every project declares its parts at adoption (ADR 0049), so the direction is held from the first feature. |
| `code:formal-model`: a model or proof the project keeps | formal verification (2) | Asked of the model the project chose to write, from the day it exists. |
| `code:state-machine`: a fixed set of named states something moves between | formal verification of state (2) | Modelled at the first, while it has a few states and one writer, so the next is written against the model. |
| `life:deployed` | end to end (2), reproducible build (2), dynamic security scan (2), active security scan (2), telemetry arrives (2), production answers (weekly and after a deploy) | They judge what other people meet. Before anyone else can reach it there is nothing to attack, nothing to watch arrive and nothing to redeploy or roll back. Running the merge base's build on the head's data, and a flow suite, cost the same whenever they are added. |
| `life:deployed and code:stored-format` | rolls back safely (1) | Running the merge base's build on the head's data needs data to run it on: a deployed project that stores nothing (a static site) has none, so the question is asked only once a stored format exists too. |
| `life:irreplaceable-data` | backup restores (weekly) | A restore drill before there is data worth restoring tests nothing. |
| `life:depended-on` | changed-code mutation (1), full mutation (2), performance budget (2), load (2), with its weekly soak | A budget needs a load that people put on the system; before that its numbers are guesses. The load smoke already catches errors under concurrency from the first entry point. Mutation judges only changed code, so it costs the same to add on the day a defect would first hurt someone. |

### What else waits for a life event

Some behaviour of the merge judge and the pipeline protects people other than the owner, so it starts when they exist. Guard paths, disputes and the ratchet hold from the first commit: they protect the method itself, and they cost nothing until something touches them.

- New surface with no flag that is off goes to the owner only once the project is `deployed`. Before that, nobody meets the surface. ([guards.md](guards.md#the-merge-judge))
- A one-way door goes to the owner only once the project is `deployed` or holds `irreplaceable-data`. Before that, nothing is lost by walking back through it.
- The pipeline's critic and its fix loop run only once the project is `depended-on`. Before that, a ticket is built by spec, the ticket check, implement and review, and a finding the review opens is still open. ([pipeline.md](pipeline.md))

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

A repository whose application types leave a row out (a library has nothing to boot, to load or to scan from outside) binds it `none: not a <type>`; the rows themselves stay.

## What a recipe must get right

A tool is chosen per repository, so no recipe is shipped for it. These hold for any tool, and each was learnt on a binding where a gate passed while judging nothing or failed for a reason that was not the code's.

- **A gate once asked is red with nothing to run.** A suite that collects no test, a performance gate with no benchmark, a fuzz gate with no target, a telemetry gate with no telemetry test: each fails, on every stack. A row is bound only after its trigger has fired, so the set it judges already has a member. A row whose trigger has not fired is bound `later: <trigger>` and has no recipe yet; `none` is for a property the project will never have. Both are written in the binding, never printed by a recipe that found nothing.
- **A gate over the change may find nothing to judge, and says so.** Mutation of changed functions, coverage of changed lines and the flag states of changed flags are green when the change touches nothing in their scope, and only when the tool itself reports zero items: a tool that fails for another reason is red.
- **A time box is per thing judged, under a cap.** A fuzz run gives each target a fixed number of seconds and stops at a cap on the total, and a soak or a repeated run scales the same way. The cost of a gate then follows the code: a project with one target pays for one, and the cap is what the tier's ceiling allows. A single fixed box makes an empty project wait as long as a large one.
- **No tier 0 or tier 1 gate rests on how much work fit in a number of seconds.** A test bounded by seconds rather than by a count of cases, or one that fails a case for being slow, is not deterministic: on a busy machine it tries fewer cases, or fails a correct one. Bound it by a count with a fixed seed, and leave a time-boxed run to tier 2. ADR 0080 names the uses of time that stand.
- **A gate over the change takes its base from the method, and judges nothing on the adoption.** `scripts/changed.py -- <command>` hands the command the commit the change is compared with, the one the shipped gates use. The adoption is the change whose base holds no binding: every line is new against it, so such a gate would redo on each run what tier 2 does once over the whole tree. There the wrapper runs nothing and says so. It reads the base commit, so no later change can claim to be the adoption. The `bite` gate does the same, and `bite-all` proves every gate once. In a scratch copy of the bite proof the default branch stands at `HEAD`, so the change is the planted fault and such a gate judges that alone.
- **A failed collection is red, never a count of zero.** The test-count gate compares two counts; an install, an import or a listing that fails at either side is a failure, not a smaller number.
- **Mutation is judged by the register, never the score.** The score is a trend. Clear the tool's cache of verdicts before a run, so a new test meets the old survivors again.
- **A mutant that does not build is not a kill.** The recipe reports it as `unviable`. Build mutants with warnings left as warnings: a lint promoted to an error turns most mutants unviable, and the gate then reports a run that tested a fraction of them.
- **The mutation tool runs its mutants at once**, by its own parallel mode set in the recipe, and one shard of them when handed `SHARD`. One mutant at a time is hours on a tree of any size.
- **A bite of a whole-tree gate is narrowed.** Its `args` name one file, one target or one mutant, as a fuzz bite names its seconds. The bite shows the gate is alive; the whole run stays tier 2's.
- **What a gate starts, it stops, even when it is killed.** A test that boots the deliverable does it through `with_service.py`, or in a process group its runner ends: a mutation tool kills a hung test and not what the test forked.
- **A gate that judges elapsed time is measured alone.** Name it in the runner's `--alone` list (`alone` in the `just` example). Its ceiling is set from a run on a quiet machine or on the CI runner; a reading taken beside fifteen other gates measures the machine.
- **A gate that requests the deliverable's routes from outside boots it with every flag due for removal on.** New surface ships off, so interface conformance, a scan, a load smoke or a load test of the defaults meets a 404 on each flagged route and is green on what it never reached. `flags.py --temporary` prints the key of every flag with a `remove-by` date, and the recipe hands them, on, to the override the flag seam reads (`probed` in the `just` example, through `--env` of `with_service.py`). A `permanent` flag is a switch and keeps its default. The defaults are what ships: `smoke`, the end-to-end suite and the off half of `flag-states` judge them.
- **A scan is shown to reach the routes.** A scanner that follows links from the root finds none in an API that serves no pages, and is green on the one answer it saw. Feed it the published contract, or the requests the end-to-end suite makes, and plant its bite's fault on a route of the contract other than the root: a scan that never requests that route does not bite.
- **A rule set for a framework is judged only when the framework is a dependency.** Static-analysis packs bundle rules for frameworks the project does not use, and one of them firing on a look-alike call is noise; the language and runtime rules always apply, and the recipe prints how many it left out.
- **A generated artefact that is not committed fails its drift gate.** Comparing with the index alone passes an untracked report.
- **Every tool runs through the package manager that locks it**, so the lockfile pins the gate as it pins the product, and the audit of shipped dependencies leaves the development tools to the weekly `audit-all`. A pinned image that fetches at run time is not pinned: a tool that downloads its rules, add-ons or database when it starts is handed them at a fixed version, or told not to update.
- **Each coverage group is measured by a report that reaches it.** Two suites write two reports and one judgement reads both; a fitness test's logic lives where coverage measures it.
- **A repeated run takes a fresh seed and prints it**, so a failure in the order-independence gate replays.
- **A recipe that runs a tool an install provides installs it first** (ADR 0039, ADR 0043). Some tools are on disk only after an install, and the merge's work tree and every CI job are fresh checkouts. The binding's `## Install` section says, from what the adopting agent knows of the tools it bound, whether this stack needs an install first, the lockfile it is frozen to and the recipes that need it; the method names no package manager. One `install` recipe installs exactly what that lockfile says and does nothing once it has; each listed recipe depends on it, and each tier runs it before its gates start so that no two gates install at once. A second gate that installs for itself, or a bite's `setup` that does, hides the gap: the gate is green where it was prepared and red where the merge runs it. A stack that says `none` and needs one is not found by a gate; the rehearsal in a fresh worktree (step 12 of the adopt skill) is the check.
- **A recipe builds what it runs, every time.** In CI each gate is a job of its own, so the boot command of a service gate compiles first, and nothing depends on what another recipe left behind: a coverage report, a build directory, a cache. A recipe that skips the build when its output is already there judges the last build.
- **A red gate says what is wrong.** A recipe that ends on a bare comparison or a tool's exit code prints nothing a reader, or a bite's `expect`, can tell from any other failure. It prints the rule and the number.
- **A threshold starts at the measured value.** A ratchet recorded as zero is a gate that cannot fail.
- **A tool that walks the tree is pointed at the source.** `quality/snapshots/` holds copies of the tool configs, a dependency manifest among them, which reads as a second package to a tool that searches for manifests.
- **A snapshot of the product's own contract lives beside the tests.** A command's `--help`, a public API report or a schema changes with the feature that changes the contract. Under `quality/`, a guard path for what only rises, it could land neither ahead of its feature nor with it.
- **A prose gate that flags the owner's own text is answered in the text.** When the `prose` gate flags a word on a glossary *Avoid* list, the text is reworded; an *Avoid* list is never shortened to turn a gate green.
- **A gate red in parallel and green alone shares state with another.** Separate their outputs (a report path, a cache directory, a work tree of its own per gate); only when that is not possible, declare the group in that tier's `serial` variable (`--serial A B`). Two gates whose tools each start a worker for every core can starve the tests inside one until they time out. Group them when a run shows that, or when a timed run of the tier is faster with the group: a group runs its gates one after another, so once its chain outlasts the slowest gate outside it, the group sets the run's wall clock. A group acts only where a tier's gates run in one process, as in a local `check` or `check-deep`, or a CI job that runs a whole tier. Where CI gives each gate a job of its own, a group changes nothing there, so time a local `check` before and after. A gate that takes every core in a run that joins tiers is measured alone (`--alone`), since a group holds the gates of one tier.

## Every bound gate is shown to bite

The method ships no tool, so it cannot promise that a repository's recipes work. The repository shows it. `quality/bites.toml` holds one `[[bite]]` or more for each Gates row whose Tool cell names a tool the repository chose, a row that pairs a plugin script with a tool of the stack included. A row bound `none` or `later: <trigger>` needs none, and nor does a row bound to the plugin's scripts alone: `scripts/bite.py` proves those. A row's bite is written when the row is bound, in the same change.

A bite is a `[[bite]]` table of `quality/bites.toml` and holds exactly one fault:

| Key | Value |
| --- | --- |
| `gate` | the last word of the row's entry point: `just lint` is `lint` |
| `name` | the fault in a few words, unique within its gate |
| `expect` | words the gate prints because of this fault, such as the rule's code or the name of what is missing |
| `write` | a fault: `{ path = "...", content = "..." }` creates or replaces one file; `content` may be a list of strings, joined, so a planted secret is split and no scanner reads the registry as a leak |
| `replace` | a fault: `{ path = "...", old = "...", new = "..." }`, or a list of them; in a file that exists, the one place `old` stands becomes `new`, and `old` standing nowhere or twice is red |
| `patch` | a fault: the path of a diff under `quality/bites/`, applied with `git apply` |
| `remove` | a fault: globs, and every file they match is deleted |
| `proof` | the named proof this bite is, where the row's question requires one |
| `args` | optional words appended to the gate's command, such as the seconds a fuzz run takes or the one file a whole-tree run is narrowed to |
| `commit` | optional; `true` commits the fault in the copy first, for a gate that reads history or the index |

Before the first `[[bite]]`, two optional keys serve every bite. `setup` is a list of commands run once in each scratch copy, in order. A copy holds what git sees and nothing ignored, so `setup` rebuilds from the lockfile what the gates do not build themselves, and builds the deliverable once so a gate's own build is incremental. `include` lists ignored paths a gate needs that `setup` cannot rebuild, copied when present.

A fault inside a file other changes also edit, such as a manifest or a lockfile, is a `replace`: it keeps applying while the lines around it change, where a patch's context breaks on the first unrelated edit. A gate that judges a collection, which `application-types.toml` marks, carries a `remove` bite as well: the collection emptied, and the gate red. A question may also require a proof by name, a fault that only its gate can catch, and a bite claims it with `proof`. The repository writes the fault; the name says what it must be.

`bites` is the one green verdict, and it proves three runs of one command: green on the restored copy, red with the expected words once the fault is planted, and green again on every later run without it. It does not prove the red came from the fault in three cases: a gate whose verdict hangs on state restoring does not put back, such as a cache git ignores, a file outside the copy or the clock, can flip on its own, and one that alternates green and red still bites; a gate that reads `RATCHET_BITING` can act otherwise in the copy; and `args` that narrow the command prove only the narrowed command. Each run with no fault is one more chance for a gate that flips on its own to go red: one red one run in twenty fails a proof of five bites about one time in two, twice as often as when one green run served every bite. `DOES NOT BITE` is a gate that cannot fail on what it is for. `RED BEFORE THE FAULT` is a gate that is red with no fault planted: on a fresh checkout, usually because it leans on what another recipe left behind, or after an earlier bite, because that bite's run left something the restore does not put back. `TIMED OUT` is a run of the gate ended by its time limit, `--limit` where given, else `bite-run` in `quality/thresholds.toml`, else 900 seconds. A gate's and a tier's ceilings bound its warm runs in CI, and a proof's run is a cold one, so neither limits it; a time limit inside a gate's own tool is not seen, and reads as `NOT PROVEN`, `DOES NOT BITE` or `RED BEFORE THE FAULT`; it is red and takes the place of the verdict the exit code alone would give, so a load-slowed proof never reads as a gate unable to fail. `NOT PROVEN` is a gate that went red on the fault and was red again once the fault was gone, so its red may be its own state. `RED FOR ANOTHER REASON` is a gate red without the words the bite expects, or a bite whose words the gate prints as often when it is green. `THE FAULT WAS NOT PLANTED` is a fault that could not be written, replaced, applied or removed in the copy. Each is fixed in the recipe or the fault, never by loosening `expect` until it matches. The script's own writes never reach the real work tree: a fault is planted only through descriptors of the copy's own directories, never through a link, so a process setup leaves running cannot steer one into the real tree. Setup itself runs as the user and could write the real tree directly, so it is trusted like any recipe. Nothing git ignores is copied, so a gate is judged as a CI job would run it.

The whole proof is `bite-all`, in tier 2, under a ceiling of its own. On a pull request and in the local check, `bite` proves only the gates the change could affect. It narrows only on what a record declares: a bite of the registry, a patch a bite names, a row of the binding, a tool-config path under a gate's bullet in the binding's `## What each gate reads`, and a file a bite's fault edits, which adds that bite's gate. Every other changed path that defines a gate, a recipe included, proves every tier 0 and tier 1 gate, and so does a file under `quality/bites/` that no bite names as its patch, since `setup` may run it, and any `.gitignore` or `.gitattributes`, since the proof reads them when it plants a fault. The conformance gate is red on a bound gate with no bite, on a collection gate with no `remove` bite and on a bound gate with no bite for a proof its question requires, so a gate cannot be added unproven. The registry and its patches sit under `quality/`, a guard path: a change that drops a bite is the owner's to merge.

One fault shows that a gate is alive and wired to the thing it judges. It does not show that the tool's rule set is complete. A gate nobody can plant a fault for is not a gate: its row is bound `none`, with the reason and an ADR.

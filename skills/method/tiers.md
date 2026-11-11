# Tiers and the questions each answers

The method asks questions; the binding names the tool that answers each one for this stack. A question with no tool in the stack is bound to `none` with the reason and the ADR that accepts the gap: that row is green, and a standing criticism of the binding until a tool exists. A question is never advisory because its tool is inconvenient; advisory is for reports that inform a choice rather than judge a change.

## When each tier runs

| Tier | When | Entry point | Budget | Blocks |
| --- | --- | --- | --- | --- |
| 0 | every commit | `tier0`; the commit hooks split it, `pre-commit` on the tree before the commit and `commit-msg` on the message being written, since `commits` over history would judge the commit an amend replaces | about a minute | the commit, and the pull request |
| 1 | every pull request | `tier1` | under fifteen minutes, so nobody is tempted to skip it | the pull request |
| 2 | merge to the default branch, nightly, on demand | `tier2` | unbounded; time-boxed where a tool runs forever | the next piece of work: a red tier 2 on the default branch is fixed before anything else merges |
| weekly | scheduled | `audit-all`, `licenses`, `drift`, `load-soak` | | nothing; the finding is visible and gets its own pull request |
| advisory | on demand | `reports` | | nothing, ever; read before a refactor to pick targets |

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
| Does the deliverable start and answer? | boot it, hit a health probe, stop it |
| Does every entry point emit the telemetry the binding names? | tests against an in-memory OpenTelemetry exporter: each route, job and command yields a span with the attributes the binding requires, a failure is recorded on its span, a log line carries the trace id; a fitness test fails an entry point registered outside the instrumented seam; the signal names are snapshotted beside the tests, so the telemetry surface changes on purpose |
| Does the code hold with each changed flag off and on? | the suite once per state of every flag the change adds or touches, through the override the flag seam honours; off is the merge base's behaviour |
| Does the deliverable stay correct under concurrent requests? | a load smoke: seconds of concurrent requests against the booted deliverable, zero errors; latency is printed, not judged, since a pull-request runner is no benchmark host |
| Does every home-grown checker still bite? | each fitness test and gate tool carries a synthetic violation it must fail on, run as a suite |
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
| Does the telemetry arrive? | the end-to-end environment exports to a collector, and a request's trace, its metrics and its logs are read back from it |
| Is any flag past its removal date? | the method's flags gate with `--expired`; it goes red by the calendar, so it is asked here and never on a pull request |
| Is the interface usable by everyone? | accessibility checks inside the end-to-end suite, for user interfaces; `impeccable audit` is the agent-side pass, the gate is the deterministic one |
| Does the model still hold, and does the code still mirror it? | the project-local formal verification skill: the owner's `/pstack:create-formal-verification-skill` writes it where a bug costs most and tests reach least, their `/pstack:maintain-formal-verification-skill` keeps it true to the code. The model and the skill are guard paths |

## Weekly and advisory

`audit-all` audits every dependency, dev tooling included. `licenses` checks the shipped dependencies' licences are compatible. `drift` lists outdated dependencies and writes the software bill of materials. `load-soak` holds the load for an hour and then raises it until the system breaks, and reports where: a leak and a breaking point are findings, each with its own pull request. `reports` prints hotspots (churn times complexity), coupling, cohesion and the complexity distribution: read before a refactor to pick targets, never as a score to chase.

## Speed

Gates that accumulate become gates that get skipped. Every repository on the method starts with CI shaped for speed and a ceiling per tier that only falls, so the optimisation phase that usually arrives late is a standing obligation from the first commit.

**The ceiling.** `quality/thresholds.toml` holds `[ceilings.seconds]` with one key per tier, and one per gate where a gate has its own runner. The `budget` entry point wraps a tier or gate, measures its wall clock and goes red over its ceiling. A ceiling only falls: `ratchet-lowered` fails a raised one as it fails a lowered threshold, and raising one is a loosening with an ADR behind it, whose `Relaxes:` line ([ratchet.md](ratchet.md)) the gate reads. Adopt sets the first ceilings from the first measured run with headroom (`budget --set`), never under the tier's budget in the table above, so an empty skeleton's few seconds do not become the first feature's ceiling; from then on every change that pushes a tier over must pay for itself in the same pull request. Runners vary, so the ceiling blocks on the default branch and nightly, in the tier 2 sense, and reports on pull requests.

**The shape, from the start.** The CI templates ship with these, and the conformance gate checks the first two are present:

| Technique | Where | Why from day one |
| --- | --- | --- |
| Cancel superseded runs (`concurrency`, cancel-in-progress on pull requests) | every workflow | a pushed fix should not wait behind the run it replaces |
| Cache dependencies and tool caches (type checker, linter, test and mutation caches), keyed on the lockfile and the tool's config, with fallback keys | every job | a cold toolchain is the first minute of every job |
| Split a tier into one job per gate (a matrix over the recipes), `fail-fast: false` | tier 1 and tier 2 | wall clock is the slowest gate, not the sum, and one run shows every red |
| Shard any suite over the budget the binding names (`pytest-split`, `vitest --shard`, `cargo nextest partition`, the mutation tool's shards) | the long gates | the ceiling is reached by adding runners, not by removing tests |
| Run only what the change can affect (path filters; `turbo`, `nx`, `pants` for a monorepo), with the full run on the default branch | tier 0 and 1 on pull requests | a docs change should not run the mutation suite |
| Incremental where the tool allows (changed-function mutation, incremental type checking, a build cache) | tier 1 | the pull request pays for its diff |
| Pin the toolchain and use the setup action's own cache | every job | installs are the slow, flaky part |
| Publish the timings (`budget --report`, uploaded as an artefact) and read them in `reports` | every job | the slowest gate is always named |

What speed never buys: a quarantined test (a failure here is a bug, never "flaky"), a tier 2 gate moved to on-demand to hide its cost, or a `timeout-minutes` so generous it never fires. The binding's Speed section records the ceilings, the cache keys, the shard counts and the affected-only tool.

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

A repository with no entry point to instrument, no behaviour to flag and nothing to serve (a library) binds these rows to `none` with the reason, as any other question; the rows themselves stay, and the conformance gate checks they are there.

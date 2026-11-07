# Tiers and the questions each answers

The method asks questions; the binding names the tool that answers each one for this stack. A question with no tool in the stack is bound to `none` with the reason and the ADR that accepts the gap: that row is green, and a standing criticism of the binding until a tool exists. A question is never advisory because its tool is inconvenient; advisory is for reports that inform a choice rather than judge a change.

## When each tier runs

| Tier | When | Entry point | Budget | Blocks |
| --- | --- | --- | --- | --- |
| 0 | every commit | `tier0`, mirrored by the commit hook for the inputs the commit touches | about a minute | the commit, and the pull request |
| 1 | every pull request | `tier1` | under fifteen minutes, so nobody is tempted to skip it | the pull request |
| 2 | merge to the default branch, nightly, on demand | `tier2` | unbounded; time-boxed where a tool runs forever | the next piece of work: a red tier 2 on the default branch is fixed before anything else merges |
| weekly | scheduled | `audit-all`, `licenses`, `drift` | | nothing; the finding is visible and gets its own pull request |
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
| Is every dependency, action and image pinned exactly, and every CI job least-privileged? | lockfile matches the manifest with no floating range; actions and images pinned by digest; workflow permissions declared and minimal |
| Is every unit within its size and complexity limits? | the linter's size and complexity rules plus a fitness test, with the numbers the binding's Limits section holds; a hard limit, split the unit |
| Did the count of TODO, FIXME and HACK markers rise? | counted at the merge base and at head, never stored |
| Does prose use the glossary too? | a prose linter carrying the _Avoid_ lists over docs and comments |
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
| Does every home-grown checker still bite? | each fitness test and gate tool carries a synthetic violation it must fail on, run as a suite |
| Did the public surface change on purpose? | a snapshot of exported symbols, routes or flags, diffed; a change needs a version bump or a changelog line |
| Does old persisted data still load? | golden fixtures from earlier versions read cleanly; migrations apply and reverse |
| Do the documentation's examples run? | doctests, or extracted snippets executed |
| Does the code contain a known insecure pattern? | static security analysis with the stack's rule set; findings fail |
| Does the shipped artefact or base image carry a known vulnerability? | an image or artefact scanner |
| How much of the code is typed, and did it fall? | type coverage, ratcheted, for gradually typed languages |
| How much of the public surface is documented, and did it fall? | documentation coverage, ratcheted |
| Is the change within the size budget? | files and lines against the merge base, a ceiling the binding holds |
| Did a guard path change? | the method's guard diff, against the manifest at base and head |
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
| Is it within the performance budget? | benchmarks against the ceilings the binding holds for latency, allocation and bundle size; `pstack:benchmark-checklist` before trusting a number |
| Does the running system resist a dynamic scan? | a dynamic application security scan against the end-to-end environment |
| Is the interface usable by everyone? | accessibility checks inside the end-to-end suite, for user interfaces |
| Does the model still hold, and does the code still mirror it? | the project-local formal verification skill: `pstack:create-formal-verification-skill` writes it where a bug costs most and tests reach least, `pstack:maintain-formal-verification-skill` keeps it true to the code. The model and the skill are guard paths |

## Weekly and advisory

`audit-all` audits every dependency, dev tooling included. `licenses` checks the shipped dependencies' licences are compatible. `drift` lists outdated dependencies and writes the software bill of materials. `reports` prints hotspots (churn times complexity), coupling, cohesion and the complexity distribution: read before a refactor to pick targets, never as a score to chase.

## Choosing a tier for a new question

Fast and local: tier 0. Needs the whole tree or a test run: tier 1. Needs more than the pull-request budget: tier 2. The catalogue is a floor: a project adds rows for what its domain demands.

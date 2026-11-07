# Tiers and the gate catalogue

The method names the gates; the binding names the tools. A gate with no tool in a given stack is recorded in the binding as `none`, with the reason, and that line is a standing criticism of the binding until a tool exists.

## When each tier runs

| Tier | When | Local | Budget | Blocks |
| --- | --- | --- | --- | --- |
| 0 | every commit | `just tier0`, mirrored by the pre-commit hooks for the inputs the commit touches | about a minute | the commit, and the pull request |
| 1 | every pull request | `just tier1` | under fifteen minutes, so nobody is tempted to skip it | the pull request |
| 2 | merge to main, nightly, on demand | `just tier2` | unbounded; time-boxed where a tool runs forever | the next piece of work: a red tier 2 on main is fixed before anything else merges |
| weekly | scheduled | `just audit-all` | | nothing; the finding is visible and gets its own pull request |
| advisory | on demand | `just reports` | | nothing, ever; read before a refactor to pick targets, never as a score to chase |

`just check` is tier 0 plus tier 1. `just check-deep` adds tier 2. Every tier is a dependency list of small recipes, so a failure names its recipe and that recipe reruns alone until green.

Every comparison against history uses the **merge base** with main (`HEAD~1` on a clean main), so a loosening made in an earlier commit of the same branch is still seen. CI checks out full history.

## Tier 0: the shape of the code

| Gate | Principle |
| --- | --- |
| format | one canonical form, decided by a machine |
| lint, near-all rules | turn everything on, opt out with a written reason per rule |
| strict types | the strictest static checking the language offers; the dynamic escape hatch (`Any` and its cousins) is banned |
| dependency direction | layers point inward; a tool checks the imports, a fitness test checks it again |
| dead code and dependency hygiene | every artefact is reachable and every dependency declared |
| scripts and CI lint | the build is held to the same bar as the product |
| secrets | scan the commit, the tree and the history |
| tamper checks | snapshots match the tools' effective configuration; no threshold lowered; no suppression kind rose; no `.only` |

## Tier 1: the behaviour of the code

| Gate | Principle |
| --- | --- |
| unit, property and fitness tests | behaviour, laws and architecture, all as tests; property tests run with deterministic seeds |
| coverage, absolute and ratcheted | 100% where code is pure or is the gate itself; elsewhere a threshold that only rises |
| diff coverage | every changed line ran and took every branch; a changed file the report does not know fails |
| test count | may not fall against the merge base; deleted test files are named |
| changed-code mutation | every mutant of a changed function is killed or registered |
| contract drift | generated interface artefacts are committed and regenerating them produces no diff |
| shipped-dependency audit | any known advisory in a shipped dependency fails |
| duplication | a low ceiling on cloned lines |
| build | the deliverable builds |
| guard diff | every changed guard path is listed against the manifest at base and head |
| conformance | the repository still has the method's artefacts and recipes (`just conformance`) |

## Tier 2: the limits of the code

| Gate | Principle |
| --- | --- |
| full mutation | every mutant in the pure core and the gate tools, killed or registered |
| deep interface conformance | property-test the interface against its published contract, stateful where possible |
| symbolic checking | a solver searches the contracts for violations |
| fuzzing | time-boxed, corpus kept, crashes are bugs |
| end to end | the real system through its real interface, the thin top of the pyramid |
| formal specs | the model checker runs every spec and every trace validates against it |

## Choosing a tier for a new gate

Fast and local: tier 0. Needs the whole tree or a test run: tier 1. Needs more than the pull-request budget: tier 2. A gate is never advisory because it is inconvenient; advisory is for reports that inform a choice (hotspots, coupling, complexity distributions) rather than judge a change.

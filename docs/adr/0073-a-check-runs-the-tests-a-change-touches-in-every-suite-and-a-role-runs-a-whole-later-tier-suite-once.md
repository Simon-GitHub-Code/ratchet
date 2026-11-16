---
status: accepted
---
# A check runs the tests a change touches in every suite, and a role runs a whole later-tier suite once

Amends `skills/method/tiers.md`, "Run only what the change can affect" and the rules for a gate over the change, the binding's `Affected only` line, `skills/method/pipeline.md`, and ADR 0069's rule that a binding change outside the gate rows proves every gate. With ADR 0072 it answers one run that took three and a half hours on a small ticket.

**Today** a suite whose gate sits in tier 2 has two ways to run: whole, through its entry point, or not at all. In the run that prompted this record:

- The ticket's criteria named two tier 2 gates as green. `check` runs neither, and no rule of the pipeline says when a role runs them, so roles ran both suites whole nine times, 38 minutes in all. Four of those runs were on product code that had not changed since the run before. Their only reds were tests the critic had just written and already run alone.
- Three critics ran the whole suites although their rule is "only the tests you wrote". The binding gave a way to run one file for the unit suite only. The later-tier entry points take no files and build first, so a role that wanted one file called the tool itself, without the entry point's environment, and failed about thirteen times before it found the form.
- A pull request never ran the new tests of a tier 2 suite: tier 2 runs on the default branch and nightly. Only the roles' own runs showed them green before the merge.
- `check` ran eight times, 30 minutes. Three runs re-proved a tree that was unchanged or had one comment edited. Nothing in the run's result said so.

Any answer must keep three things:

- **The whole suite still runs where its tier says.** Nothing here moves a suite to an earlier tier or drops a run of it.
- **One entry point, the same everywhere.** A role, a developer and CI run a suite's files through the same recipe as the whole suite, so the environment and the build are the gate's own.
- **Nothing is inferred from code.** Which tests a change touches is what git says changed, read against what the binding declares. No rule guesses which tests a product change affects.

## Decision

**1. The binding names its suites.** `## Tests` gains a table, one row per suite: its name, the gate that runs it whole, the globs of its test files, and how to run given files of it. The way to run files is the suite's own entry point taking paths, or `none:` with the reason, for a suite that cannot run by file.

**2. A tier 1 gate runs the test files the change adds or edits.** `tests-changed` lists the test files the change adds or edits, tracked changes and the files git would add, against the base every gate over the change uses (`tiers.md`, "A gate over the change takes its base from the method"), sorts them by suite from the table's globs, and runs each suite's files through its row. A change that touches no test file of a suite in its scope is green and says so. By default the gate runs the suites whose whole run sits in a later tier, since the tier's own gates already run the others whole. Where no suite's whole run sits in a later tier, the default is every suite, so the gate can be proven in any project. `tests-changed all` runs every suite's changed files: that is the inner loop.

**3. The run knows which criteria a gate holds.** A criterion names a gate when it holds, as a code span, the gate's name or its entry point from the binding's Gates table. The skill's preflight returns the ticket's criteria that name one, with the gate's tier, and the spec role returns those of spec.md, which may add to them. The run hands that list to every role.

**4. Each role is told what it runs, and when.**

- While it works, a role runs tier 0 and `tests-changed all`. It never runs a suite whole to see whether its own tests pass.
- The implementer, the fixer and the reviewer run `check` last, before they return, and again only after an edit. A role does not run it again on a tree it has not changed since a green run.
- A gate outside `check` that a criterion names is run whole once by the implementer, once its `check` is green, and once by each review, on the tree it judges. No other role runs it.
- A critic's and the spec role's rule stays "tier 0 and your own tests", and names `tests-changed all` as the way.

**5. The run's time is in its result.** Every gate run already writes a line to the work tree's ignored state. Each line gains when it ran, the run it belongs to and the content of the tree it ran on, and a tier's run writes one line for the whole. The preflight stamps the ticket's start and the skill stamps its end. When the run returns, and before the work tree is put away, the feature skill reads the lines between the two stamps and states, with the result: how long the ticket took, how many `check` runs it held and their seconds, and how many of them ran on a tree an earlier green run had already covered. A drain's summary carries the same line per ticket. Nothing is cut short (the owner's decision of 2026-11-16).

**6. The suites table is the gate's own declaration.** For the proof of ADR 0069, a change to a row of the table names `tests-changed` and no other gate, as a row of the Gates table names its own.

**7. Conformance holds the shape.** Red where `## Tests` has no suites table, a row names a gate the Gates table does not hold, or a row has neither a way to run files nor a reason. Red where the Gates table has no `tests-changed` row. An upgraded project meets these as owed findings, and the fault that proves the new gate is the project's to register, as for every bound gate.

## Refutation criterion

This is wrong if any of these is seen:

- `tests-changed` is green on a change that adds a failing test file to a suite with a way to run files.
- A pull request merges with a new test of a later-tier suite that no gate ran before the merge.
- A suite with neither a way to run files nor a reason passes conformance.
- A run's result reports no repeated `check` when one ran twice on the same tree.
- After this is built, a run's roles run a later-tier suite whole more often than the implementer once and each review once.

## Considered options

- **Guidance only: name a one-file command per suite and tell the roles.** The owner declined it. A pull request would still not run a later-tier suite's new tests, and only a prompt would hold the roles to it.
- **Run the tests a product change affects, found by an import graph or a coverage map.** That infers from code what a record does not say, the line ADR 0069 drew for the proof. It differs per stack, and a wrong map is a green run that proved nothing.
- **Move such suites to tier 1.** The whole suite is minutes long, and tier 1 has a ceiling for that reason.
- **Reuse a `check` result on an unchanged tree.** ADR 0071 declined reuse across runs: what a gate reads is wider than the tree. Point 5 reports the repeat, and a later record can revisit reuse if the runner can make a gate's inputs complete.
- **A wall-clock limit on a run.** The owner declined it (ADR 0072).

## For the builders

### What changes where

- `scripts/changed_tests.py`: new. It reads the suites table, lists the changed test files against the base, runs each row's files, and writes its line. Deleted files are not run. A file that matches two rows is a conformance finding, not a choice.
- The timing lines: `at` in UTC, `run` (the run id of ADR 0071) and `tree` (the content id of ADR 0071, since a role never commits and `HEAD` is the same for every run of a ticket), and a line for a tier's whole run. A workflow script has no clock, so no time comes from it.
- The adopted justfile: a `tests-changed` recipe in tier 1; each suite's recipe takes files. A conformance shape check with a written fix carries the recipe to adopted projects; taking files is the project's to bind.
- The binding template: the suites table under `## Tests`; the `Affected only` line of `## Speed` names the gate.
- `scripts/conformance.py`: point 7.
- `scripts/project_bite.py`: point 6.
- `scripts/feature_preflight.py`: point 3 for the ticket's criteria, and the start stamp, in its JSON.
- `workflows/pipeline.js`: `CHECK_RULE` and `OWN_TESTS_RULE` become point 4; the spec role returns `namedGates`; the list of point 3 in every role's prompt; the implementer's and the reviewer's results say which named gates they ran.
- The CI workflows of the template: the job that runs `tests-changed` sets up what each later-tier suite needs, as the deep workflow does for the whole suite.
- `skills/feature/SKILL.md`, `scripts/drain.py`: point 5's line, read before `close` or `clean` removes the work tree, since the ignored state is in no stash.
- `skills/method/tiers.md`, `skills/method/pipeline.md`, `skills/adopt/SKILL.md`, `skills/fill/SKILL.md` and the templates: the list the skills' owner wrote for this record.

### Order

ADR 0071's build, #212 and #210 make one `check` cheaper and are built first: #210 because gates inside one `check` slowed each other to two and three times their own time in the run above. The build a later-tier suite needs is a shared report under ADR 0071 within one run. A report is not reused from one run to the next, so each `tests-changed` a role starts by hand builds again.

### Consequences

- For a change that touches a later-tier suite's test files, `check` grows by that suite's build and the time of those files. A change that touches none pays nothing.
- A product change that breaks an unchanged test of a later-tier suite is still found where it is today: by the implementer's whole run and each review's when a criterion names the gate, else by tier 2 after the merge.
- Point 5 sees gate runs. A role that calls a tool by hand, outside every entry point, is not in the line, and neither is the time a role spends thinking: in the run above the lines would have covered about two thirds of the time.
- The tier 1 job of CI installs what the later-tier suites need, where it used to install tier 1's tools only.

## Settled by the owner, 2026-11-16

- Every adopted project gets the gate, as a method rule.
- A run's time is reported and not limited.

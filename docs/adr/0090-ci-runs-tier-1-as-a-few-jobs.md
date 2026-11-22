---
status: accepted
---
# CI runs tier 1 as a few jobs

Settles issue #296 for tier 1, except its ask 4, which is left to ADR 0088. Builds on ADR 0019 (the CI shape), ADR 0025 (a job is held to its own ceiling and its tier's), ADR 0069 (a red tier 1 on the default branch holds merges) and ADR 0086 (every runner keeps the project's schedule). Amends the Speed sections of `skills/method/tiers.md` and of the binding template, and `skills/adopt/runners-and-forges.md`.

**What was asked.** The template's `ci.yml` gave every tier 1 gate a job of its own. Each job spends its setup (checkout, toolchain, caches) before its gate starts, so a pull request paid more for setup than for its gates, and a gate whose every row of the binding says `none:` or `later:`, whose recipe is one `echo`, still started a runner. A pull request should pay for the gates it runs. Grouping must not loosen a gate, drop one silently or lose a ceiling.

## Decision

**1. The `justfile` names the jobs.** Beside `tier1_gates` and `tier1_sharded`, the new list `tier1_jobs` names the jobs CI runs tier 1 as. It is one plain string, and each word is a job. A gate alone, as in `bite`, is a job of its own. `NAME=A,B` is the job `NAME`, which runs the gates `A` and `B` of the tier. `rest` runs every gate of `tier1_gates` that no other job names. For example, `tier1_jobs := "bite build suite=unit,coverage rest"` runs four jobs. The template's list is `"bite rest"`. A job of several gates runs them at once through `parallel.py`, which keeps each gate's output whole and names every red one, with the schedule the recipes and the binding's `Apart` and `Alone` lines give those gates (ADR 0086). `just ci-job NAME` runs one job, locally as in CI.

**2. The plugin reads and writes only a job list in its own form.** The lists are read exactly: a value is a string literal, or literals and variables joined by `+` that resolve exactly, and anything else, such as a function call, is not read at all. A `tier1_jobs` that is not one plain string, or that holds any word other than a gate of the tier, `rest` or `NAME=gate,gate`, is the project's own, such as a list of the project's own recipes that its own matrix step reads. For the project's own list the plugin applies no edit, to the `justfile` or to `ci.yml`, and raises one finding, never red, that says it left the list as it is and holds the template's form and CI for an agent. When the plugin's own shape step reads such a list, that step refuses it, so conformance is red there, as CI would be. An edit to a list in the plugin's form is applied on its own only when the workflow does not read `tier1_jobs`, or runs every job through `just ci-job`, so an applied edit never names a job that no recipe and no `ci-job` route can run.

**3. A deterministic check holds every gate in exactly one job.** `scripts/ci_jobs.py` reads the three lists, and conformance is red on each of these, with one finding that names the gate and the fix:

- a gate of `tier1_gates` that no job runs, when the list has no `rest` (the edit adds `rest`);
- a gate in two jobs (for an agent, since only the project knows which it meant);
- two jobs with one name, or a group named after a gate or `rest`, since a ceiling is found by the job's name;
- a gate that must be alone sitting in a group (the edit gives it a job of its own before `rest`). A gate must be alone when it has a seconds ceiling of its own, since that ceiling is found by the job's name, or when the binding's `- Shards:` count is over 1 and it is in `tier1_sharded`, since a group would shard every gate in it, or when the workflow sets up a step for it alone by `matrix.gate`, since that step would not run for it inside a group. The proof `bite` is held alone by a `bite-all` ceiling.

The shape job runs the same check before it starts a runner and refuses a list with any of the first three, printing each finding, so a hand-edited list cannot drop a gate in CI either. Conformance no longer looks for the literal `just --evaluate` in `ci.yml`: it is red when the workflow reads neither `ci_jobs.py` nor `tier1_jobs`. A workflow that reads the list its own way passes.

**4. A gate with nothing to run gets no runner.** A gate gets no runner when every row of the binding's Gates table bound to it says `none:` or `later:` and its recipe is exactly the waiting form the template writes, one `@echo "later: ..."` or `@echo "none: ..."` line, which `parallel.py` counts as not run. The shape job leaves such a gate out of every job, drops a job left empty, and prints one line that names each such gate and its row. `ci_jobs.py --run` leaves it out the same way, so the gate is green by its row and costs nothing. A gate with one row that runs a tool still runs, and so does a gate whose recipe does anything else, such as a `bind:` line that exits 1, which stays red.

**5. A gate alone for no reason is red, and the fix moves it into `rest`.** A gate in a job of its own needs one of the reasons above, or a code span on the new `- Own jobs:` line of the binding's Speed section, which says why, such as a gate whose tool would poison another job's cache. Without one, conformance is red and the edit moves the gate into `rest`. This is red rather than offered because an offered fix would leave the cost in place on every pull request, and the edit clears it with no judgement.

**6. Tier 1 no longer waits for tier 0.** Tier 1 and its long job need only the shape job. Tier 1 reads nothing tier 0 produces: each job has its own checkout, caches and artifacts. Both must still be green before a merge, since the merge judge reads the run's conclusion, which is red when any job is red. So ADR 0069's hold on a red tier 1 on the default branch is unchanged, and ADR 0088's "A time red never skips another tier" stays true, since no tier now waits on another to start. The cost: when tier 0 is red, tier 1 still bills its minutes. This is a default the owner may reverse.

**7. Ceilings keep their meaning.** A ceiling is still found by the name of the job that runs it (`budget.py --tier tier1`). Every gate with its own ceiling is a job of its own by point 3, so it is held to that ceiling as before. A gate without one was held to the tier's ceiling in its own job, and is held to the tier's ceiling through its group's job now. The group's wall clock is at least its slowest gate's time, so this holds each gate at least as tightly as before. A `rest` job has no seconds ceiling of its own unless the project declares one under the key `rest`. A ceiling for each gate inside a group, judged from the timing record `parallel.py` writes, is ask 4 of the ticket and is left to ADR 0088's build.

**8. Tier 2 keeps a job per gate.** `deep.yml` is unchanged. Its long gates (full mutation, fuzzing, symbolic runs, end to end) run for up to an hour each, and one runner holding several of them would risk the job's time limit, which ADR 0077 treats as an unfinished deep run that holds merges. Tier 2 runs once per default-branch push, not per pull request, so its setup cost is paid far less often.

## What a project sees

The shape job prints one line naming each gate with no runner. A pull request starts the tier 0 jobs, the shape job, `bite`, `rest` and one long job per shard of each sharded gate that has a job of its own. A project with a later-tier suite sets up that suite's tools in tier 1 under `if: matrix.gate == 'tests-changed'`, so the finding that asks for that step now also asks for `tests-changed` to have a job of its own, and the upgrade's computed list gives it one where the step is already there. Such a step reads `matrix.gate`, so the upgrade keeps that project's `ci.yml` as it is and owes the change (below). A red group job prints `parallel: <gate>: red`, so the gate is still named, and `just <gate>` reproduces it. The binding's Entry points table gains `ci-job`.

## What the upgrade does

The upgrade adds `tier1_jobs` to the `justfile`, computed from the project's own lists, ceilings, shard count, `Own jobs` line and workflow steps, so a project whose gates already need to be alone keeps them alone. It adds the `ci-job` recipe and the Entry points row. A project that already has a `tier1_jobs` of its own keeps it word for word, with its `ci.yml`, so its CI matrix is the one it had (point 2).

The upgrade rewrites a `ci.yml` on its own only when the result cannot change the meaning of a line the plugin did not write. It compares the file with the previous template, the `ci.yml` every release from v0.13.0 to v0.13.5 wrote, after it turns CRLF line ends into LF and the plugin's pinned `ref` into the template's placeholder. The file is template-shaped when all of these hold:

- each block the change replaces (the shape job's two old steps, the `tier1:` line, the tier 1 jobs' `needs:` lines and the budget calls' run lines) is found byte for byte, in the same jobs and as many times as the previous template holds it, and the shape job's script has no line of the project's own after it;
- each job holding such a block has no key the previous template's job lacks, such as an `if:` of the project's own on `tier1`;
- no line outside those blocks that differs from the previous template reads what the change alters: `needs.shape.outputs.*`, `matrix.gate`, or `needs.tier0.*`, which tier 1 no longer waits for. A step or job id the change removed, or a job it renamed, would count too; this change removes and renames none.

A template-shaped file is rewritten to the template's new blocks, with the project's own plugin checkout line, its other lines and its line ends kept. Every gate then runs with the same command, `just <gate>`, under the same ceiling, and a second run writes nothing. The rewrite also waits until the `justfile` holds a `tier1_jobs` in the plugin's form and the `ci-job` recipe, which the same upgrade adds first, so every job the new workflow starts has a way to run. Any other `ci.yml` is kept as it is, and the finding's offered edit names the first reason it was kept and holds the unified difference between the previous template and the new one, for an agent to apply by hand. That finding is owed after the upgrade, so it turns nothing red. Unedited files from v0.13.3, v0.13.4 and v0.13.5 take the first path, since their `ci.yml` is the same file.

## Known limits

A project that groups gates by hand can still group two that share state. `parallel.py` keeps the groups and alone gates the recipes state, as it does for `check`, so this is the same risk a local `check` carries. A `rest` ceiling, once declared, holds every gate in `rest` together. A group shares one runner's cores and the job's 30-minute limit, so a project whose `rest` outgrows them gives its heaviest gates jobs of their own, with a ceiling each or a code span on the `Own jobs` line. A gate that needs a workflow step of its own is found only by a quoted gate name on a line that mentions `matrix.gate`. A step chosen some other way is not seen, and the `Own jobs` line keeps that gate alone.

## What was observed

One adopted project, with a job per gate, started 34 tier 1 jobs, each spending about 36 s on setup before its gate began. They held 213 s of gates in 1,336 s of job time. A `none:` or `later:` gate cost 34 to 46 s of runner time. Tier 1 waited 84 s for tier 0, and a run billed 46 to 75 minutes. Grouped by hand into a few jobs, the same project went from 36 jobs to 6 and from 67 to 24 billed minutes, with every gate green. Another project, adopted on 0.13.5, started 35 jobs on its first records-only pull request, 22 of them `later:` gates.

A fresh project adopted from v0.13.5 and upgraded here: the shape step started 31 tier 1 jobs and 2 long jobs before, and `bite` and `rest` after, with one gate (`doc-coverage`, `none: not a library`) named as getting no runner. The same held from v0.13.3. A second upgrade wrote nothing, and conformance was green. These counts come from running the shape step, not from a CI run.

Arithmetic, not a measured run: at 36 s of setup per job, 31 fewer tier 1 jobs save about 18 minutes of runner time a pull request.

## Refutation criterion

This is wrong if a gate that was green in its own job goes red in a group with no change to it, which would mean the groups share state the recipes do not state. It is also wrong if a project's billed minutes for a pull request do not fall after the upgrade, or if a gate is dropped from CI with no line naming it.

## What the owner may reverse

- **Tier 1 beside tier 0 (point 6).** Putting `tier0` back in tier 1's `needs:` saves tier 1's minutes when tier 0 is red, and costs the wait for tier 0 on every green run.
- **The drift guard is red (point 5).** It could be an offered edit instead, which would let a project keep a job of its own without stating why.
- **Tier 2 keeps a job per gate (point 8).**

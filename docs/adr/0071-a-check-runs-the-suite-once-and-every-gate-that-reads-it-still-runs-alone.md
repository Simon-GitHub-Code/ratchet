---
status: accepted
---
# A check runs the suite once, and every gate that reads it still runs alone

Settles issue #211. Amends `skills/method/tiers.md`, "One pipeline, the same everywhere", for gates that read one run's report.

**Today** one local `check` in an adopted project on 0.12.3 runs the whole suite four times: for `test` (41 s), `coverage` (50 s), `diff-coverage` (55 s) and `test-counts` (45 s). The tier runs its gates at once, each as its own `just <gate>` process, so the four runs also compete for every core with each other and with the rest of tier 1. The cause is the contract: a CI job runs one entry point and nothing else, so a red job is reproduced by one command (`tiers.md:132`), and an entry point that stands alone runs the suite itself. Nothing lets a gate read the report another gate of the same check produced.

Any answer must keep two things:

- **Every gate still runs alone.** `just coverage` on its own, a CI job, and a bite in a scratch copy each run the suite when nothing else has.
- **A reader's bite stays honest.** A reader that reads a stale or missing report goes red, never green.

## Decision

**1. One report, produced once per run, through the plugin.** The binding names each shared report: the recipe that produces it, the paths it writes, and the gates that read it. A reader never opens those paths itself. It asks `scripts/report.py <report>` for them. The script returns them when this run already produced them from this tree, and otherwise runs the producing recipe once, then returns them. When several readers ask at once, the first runs the recipe and the others wait for it and read what it wrote.

**2. A run is the top-level `just`.** The top-level `just` names a run id and hands it to every `just` a recipe starts, beside the plugin folder it already hands down (ADR 0068). A report is reused only by the run that produced it, and only while the work tree's content is the one it was produced from. A gate run alone has its own run, so it produces its own report. No report carries from one `check` to the next.

**3. The report lives in the work tree's own ignored folder.** Each work tree, each scratch copy of the bite proof and each CI job has its own, so no two trees read one another's report.

**4. A failed suite fails every reader.** The report holds the suite's exit status. A reader whose report came from a failed suite is red, with one line naming the gate that judges the failure (`test`). That is what each reader does today, when its own run of the suite fails.

**5. The bite stays as it is.** A bite runs one gate in a scratch copy, with no run of the tier around it, so the gate produces its own report from the copy with the fault planted. A reader whose recipe opens the report's paths directly, past the script, reads the report the green run before left behind, since restoring the copy keeps what git ignores. It is green on the fault, and the proof says `DOES NOT BITE`. The freshness rule itself lives in one plugin script, held by the plugin's own tests.

**6. Conformance holds the shape.** A recipe that a shared report names as a reader, and that names one of the report's paths other than through `scripts/report.py`, is a red finding with the line.

**7. CI is unchanged** (item 2 below): each gate is its own job, which produces its own report.

## Refutation criterion

This is wrong if any of these is seen:

- A reader is green on a tree whose report was produced from other content, or by another run.
- A reader's bite that opens the report directly is `bites`.
- One local `check` runs a shared report's recipe more than once.
- A gate run alone is red because no other gate produced its report.

## Considered options

- **The tier's schedule names a producer and its readers, and runs the producer first** (issue #211, option 2). The runner gains an order, a gate run alone must still produce its own report, and the bite must plant through the producer. Point 1 gives the same single run with no order in the runner: readers that start together wait on the first.
- **Bind one tool run to several questions** (issue #211, option 3). The Gates table already lets rows share an entry point, as two share `just triggers`. One red would then name one gate for four questions. The timing would be one line, and the coverage gate would shard with the suite. It stays open to a project whose tool answers several questions in one run. It is not the method's default.
- **Reuse a report across runs, keyed by the tree's content.** It would also spare `just coverage` after `just test` in the inner loop. But what the suite read is wider than the tree: installed packages, the environment, files git ignores. A report from an earlier run can be stale with the tree unchanged.

## For the builders

### What changes where

- `scripts/report.py`: new. It handles the run id, the content check, the single run and the wait, and writes the exit status. The content check covers every file git tracks or would add, as the tree id of ADR 0068 covers a copy. A report is keyed by `SHARD` and `SHARDS` too, so a sharded `test` job reads only its own shard's report.
- `scripts/project_bite.py`: the scratch copy's runs get no run id from around them. The `bite` gate runs inside tier 1, and a copy's runs would otherwise share the tier's id, leaving the content check as the only guard.
- The adopted justfile: the run id handed down beside `RATCHET_PLUGIN_CHECKED`; a `suite-report` recipe; `test`, `coverage`, `diff-coverage` and `test-counts` bodies that ask the script. A conformance shape check with a written fix carries them to adopted projects.
- The binding template: a `## Shared reports` section.
- `scripts/conformance.py`: the section's shape and point 6.
- `skills/method/tiers.md`: "One pipeline, the same everywhere" and the speed rules say a reader asks for its report and never runs the suite itself.
- The runner, `scripts/parallel.py`, does not change: the waiting is in `scripts/report.py`. No model covers the tier's schedule today. If the runner itself ever gains waiting readers, the schedule gets a model under `docs/model/` before its code, as the models turn first (`docs/model/README.md`).

### Consequences

- One local `check` runs the suite once, under the coverage tool, where it ran four times.
- A reader waiting on the report holds one of the tier's job slots while it waits.
- `test` runs under the coverage tool locally, which is slower than a bare run. CI's `test` job runs the same instrumented run (item 3 below).

## Settled by the owner, 2026-11-16

Each item was put with a recommendation, and the owner took each recommendation.

- **1. How a check shares the suite.** The first reader runs it, and the others wait (point 1). Cost: one new plugin script, and a section in every binding. The other options were a producer the runner schedules first, which adds an order to the runner and makes the tier's time the producer's plus its slowest reader's, and one gate answering several questions, which gives one red for four questions and no separate timing.
- **2. CI.** Unchanged: each job runs its own suite (point 7). Cost: four times the CI minutes for these gates, with no change to the time a pull request waits. The other option was a producing job whose readers wait for it and download its report, in which a CI reader job no longer runs the command a developer runs.
- **3. What `test` runs.** The shared, instrumented run, locally and in CI. Cost: `test` is slower in CI by the coverage tool's overhead. The other option, a bare run in CI, would have CI's `test` and the local `test` run different commands.
- **4. How long a report lives.** One run, on one tree (point 2). Cost: `just test` then `just coverage` by hand still runs the suite twice. The other option, reuse across runs while the tree's content is unchanged, can serve a stale report when something outside the tree changed, such as an installed package.

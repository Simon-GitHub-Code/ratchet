---
status: accepted
---
# Speed is asked by concept from the first commit, and the shard count is one number in the binding

Commitment 9 gave every tier a ceiling in seconds that only falls, and the CI templates arrived cached, split one job per gate and cancelling superseded runs. The rest of what makes a repository fast was a table of techniques in `tiers.md` and a comment in each workflow: to shard a suite, replace its matrix entry with rows written by hand. So sharding was a restructuring left for the day a suite was already slow, nothing asked the suite to use more than one core, and the conformance gate checked only that a workflow mentioned `concurrency:` and `cache`. The templates had also drifted from the tiers they run: at commit `50c291d` the `justfile` listed `telemetry`, `flag-states`, `load-smoke`, `iac` and `settings-check` in tier 1 and `pentest`, `load`, `telemetry-e2e` and `flags-expired` in tier 2, and no CI job ran any of the nine.

The owner asked that the method move every repository towards fast local runs and fast CI before they are needed, by concept, with tools named only as examples.

We decided that the method asks five things of every binding, and measures a sixth.

- **Parallel by default.** The suite uses every core locally and in CI from the first test, which forces each test to own its state; a tier's gates run at once wherever they share none.
- **One pipeline.** A CI job runs one entry point and nothing else, so one local command reproduces a red job.
- **Shard before you need to.** Each workflow has a `shape` job that reads `- Shards: N` from the binding's Speed section, and the long gates (`test` and `mutation-changed` in tier 1; `mutation`, `e2e` and `test-stability` in tier 2) run as a matrix of gate by shard. Each job exports `SHARD` and `SHARDS`, the template `justfile` reads them as `shard` and `shards`, and a local run is shard 1 of 1. The count is 1 on day one. Raising it is one number.
- **Cache everything**, keyed on what it depends on.
- **Run only what the change can affect**, with the full run on the default branch.
- **Measured.** The ceilings of ADR 0010 and the per-gate timings every job uploads.

The binding's Speed section states how the stack meets each, on six labelled lines. The conformance gate is red when a line is missing or left as a placeholder, when `Shards` is not a whole number, and when `ci.yml` or `deep.yml` runs nothing over the shard count; it keeps its two older checks. A test holds each workflow's gates equal to the tier's recipes in the template `justfile`, and runs the `shape` step against the template binding. Decided 2026-11-11.

## Refutation criterion

This is wrong if the shape costs more than it saves or turns into paperwork. On the next two adoptions: the `shape` job takes under fifteen seconds, and adds nothing to a pull request's wall clock, since tier 1 already waits for tier 0. Raise `Shards` from 1 to 4 on a repository whose suite has grown: if anything but that number and the tool's own flag has to change, the count is not one number and the matrix is redesigned. If a sharded gate judges differently from the whole one (a coverage group scored per shard, a mutant counted twice), that gate leaves the long list. And read the Speed sections of the first three adoptions: if they say the same thing in different words and name no tool or key of the stack, the lines are ceremony and are replaced by checks a machine can make.

## Considered options

- A static matrix in the workflow, mirrored in the binding and held equal by conformance: rejected. One decision would be two edits of two guard paths, and the gate would exist only to keep them the same.
- Shard every gate: rejected. Most gates cannot run one part of themselves, and each would run whole once per shard.
- Hold the count in `quality/thresholds.toml`: rejected. That file holds what only rises or only falls, and `ratchet-lowered` would have to learn a number that does neither.
- Require a parallel test runner or a pipeline engine by name: rejected. The method names concepts, and the tool is the binding's, as ADR 0017 decides for every other row.
- Leave speed to review: rejected. An agent builds what the gates ask for and nothing else, and a slow suite is found when it is already slow.

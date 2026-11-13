---
status: accepted
---
# The suite is measured on the pinned Python and run again on the floor

The plugin's own `test` gate ran the suite under branch coverage with the runner's `python3`, which on `ubuntu-latest` is 3.12. On 3.12 coverage traces branches through `sys.settrace`, and the suite spent about half its CPU time in that tracer: on one runner model the same commit took 88 s without coverage and 261 s with it. A workstation that let `uv` choose ran 3.14, where coverage uses `sys.monitoring`, so the local run and the CI run measured different things. The 120 s ceiling held while the suite was small. Main went red at 196.9 s once the upgrade tests landed, because each of them runs the conformance checks many times and each check parsed the same justfiles again. Three runs of one unchanged commit took 137, 232 and 263 s, so the runner adds a factor of two of its own.

## Decision

**`.python-version` pins the interpreter the gates run on, 3.14, locally and in CI alike.** On 3.14 coverage measures the same branches at 100% through `sys.monitoring`, at a fraction of the tracer's cost. Statement counts differ by version (3.14 does not execute a bare annotation in a class body), and none of the lines it does not count is code a test could run.

**The suite runs a second time on Python 3.11, the floor ADR 0007 sets, without coverage, as the `test-floor` gate.** ADR 0007 exists because the runner's 3.12 caught a 3.13 API, so moving the coverage run to 3.14 must not leave the oldest supported Python unrun. 3.12 was only ever the runner's default, and the floor tests what the plugin promises. `test-floor` has its own environment (`.venv-floor`) and its own ceiling.

**The justfile parser keeps what it parsed.** `justfile_text.blocks` is a pure function of the lines, and its result is cached, so the checks parse each justfile once per process.

**An idle worker takes queued tests from a busy one** (`pytest-xdist --dist worksteal`), so a run does not wait on one worker's tail.

**A ceiling passed on a pull request raises a warning annotation**, which shows on the run's page and in `gh run view`, and the gate names its ten slowest tests on every run. The pull requests that pushed main over its ceiling had each reported the red in a log nobody read.

The ceiling stays at 120 s. Decided 2026-11-13.

## Refutation criterion

This is wrong if the floor run stops catching what it is for: if a hook or gate fails on a Python between 3.11 and 3.13 in an adopted repository while both `test` and `test-floor` were green on the change that broke it. It is also wrong if coverage on 3.14 reports a line or branch as run that the 3.12 tracer reports as missed, on the same suite. Check the first by reading the next ten failures reported against the hook; check the second once, by running the suite under coverage on 3.12 and 3.14 and comparing the missed lines of the two reports.

## Considered options

- **Raise the ceiling.** Rejected: it is a loosening, and the time was waste, not the suite's honest cost.
- **Stay on 3.12 and remove more waste.** Rejected after measuring: with the parser cache and work stealing, three runs on 3.12 took 117 to 147 s, still over the ceiling, because the tracer doubles every line the suite runs.
- **Shard the suite over two jobs on 3.12.** Kept for later: it is the method's own lever, but it needs a job that combines the shards' coverage before `fail_under = 100` can be judged, and it does not remove the tracer's cost, only spreads it.
- **Judge CPU time, the test phase alone, or the median of several runs instead of one job's wall clock.** Not taken here: each changes what a ceiling measures, which is a change to the method that needs its own decision.

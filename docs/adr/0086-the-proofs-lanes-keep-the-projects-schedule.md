---
status: accepted
---
# The proof's lanes keep the project's schedule

Settles issue #291. Builds on ADR 0020 (the proof), ADR 0069 (each verdict's wall time and lane) and ADR 0078 (the fault record). Amends the Speed section of `skills/method/tiers.md` and of the binding template.

The proof gives the same verdict however many lanes it runs, or it is not a proof of the gates. Two adopted projects saw it give another. Both failures had one shape: the proof starved a gate, or ran it beside a gate the project keeps apart, so a gate that is green alone was reported red.

- **Lanes times workers.** `RATCHET_JOBS` capped the lanes and nothing else. A suite in a lane started a worker per core, so 4 lanes on 16 cores ran about 64 workers. Five full proofs came in at 30 to 32 of 33 faults. Every miss was a clean run that failed on one of two timing-sensitive tests, which passed alone in 7 to 12 s against a 30 s timeout.
- **A schedule the proof never saw.** The project's deep check passed `--serial` for two gates that start the product on one fixed port and `--alone` for a gate that judges a p95 latency. The proof read neither. At 4 lanes one of the port gates was `NOT PROVEN` because the other held the port, and the latency gate was red before its fault in 3 of 5 full proofs.

Before this record the schedule lived only in the flags a project's tier recipes pass to `parallel.py`. The proof's lanes took the next gate from a queue, whatever the others ran.

## Decision

**The binding states the schedule once.** The Speed section of `docs/process/binding.md` holds three optional lines, read by `scripts/schedule.py`:

- ``- Apart: `a b` `c d` `` gives groups of gates that never run at once. Each code span is a group, with the names split by spaces.
- ``- Alone: `e` `f` `` names the gates that run with nothing beside them.
- ``- Wide: `g` `` names the gates that are themselves parallel.

A line that is missing, or still holds its `<placeholder>`, states nothing of its kind. A line with no code span states that none is. The plugin names no gate here beyond the template's own, which repeats the template recipes' flags.

**One relation decides who may run with whom, for both runners.** Two gates may run at once unless either is `Alone` or both are in one `Apart` group. `schedule.Board` holds the pending and running tasks and starts the first pending task that may run beside every running one, with the `Alone` tasks last. `parallel.py` and the proof's lanes both take their next task from it. The board takes tasks, not gates, and a function that names each task's gate, so two tasks of one `Apart` gate never run at once either. With no statement it starts the tasks in the order given, which is the queue the lanes read before.

**`check` reads the same lines.** `parallel.py` reads the binding at `--root`. Each kind the binding states replaces the flag of that kind (`--serial`, `--alone`, `--wide`), restricted to the run's gates. When a run's flags of a stated kind name other gates, the run prints `parallel: the recipe passes <flag> <gates> to this run, and the binding's Speed section says <kind>: <gates>; the binding's line wins, since the bite proof reads only it: give the recipe the same gates, or take <flag> out of it` before the gates start. It is never red. A kind the binding does not state keeps the flags, as before.

**Each lane is told its share of the cores.** Every gate run in the proof gets `RATCHET_JOBS` set to the machine's jobs (`RATCHET_JOBS`, else `ratchet.jobs`, else the cores) divided by the lanes, at least 1. An `Alone` gate gets all of them. The plugin cannot make a test runner obey the number. The binding's `Parallel` line says how the project's suite takes its worker count from it.

**A verdict a neighbour could cause names it.** On `RED BEFORE THE FAULT`, `RED FOR ANOTHER REASON`, `NOT PROVEN` and `TIMED OUT`, the verdict gains one line naming the gates other lanes ran while this gate's runs ran, and the cure: prove the gate alone with `--gate <gate> --jobs 1`, and if it bites there, state it `Apart` from them or `Alone`. The proof does not rerun the gate.

## What a project sees

A project whose binding states nothing schedules exactly as before, except that each gate run in the proof now sees `RATCHET_JOBS` set to its share. A gate whose recipe never reads `RATCHET_JOBS` runs as before. One that reads it starts fewer workers, and the lanes together start about as many as the machine has cores. That changes no verdict except through time, and it lowers the load. No conformance finding is added, so an upgrade turns nothing red.

A new adoption's binding carries `Apart` and `Alone` lines equal to its recipes' `serial1` and `alone` flags, so the two agree and nothing is printed. A project that adds the lines and drops the flags from its recipes has one statement.

## What was observed

The tests drive `schedule.Board` with 4 lanes over 200 seeded orders of starts and finishes. Two gates of one `Apart` group never ran at once, an `Alone` gate never ran beside anything, and gates in separate groups did run together. With an empty statement the board started the same tasks in the same lanes at the same steps as the queue on `main`, on every seed. Stub gates that log their start, their end and the `RATCHET_JOBS` they were given show the proof at 4 lanes and `check` keeping one statement: no `Apart` pair overlapped, the `Alone` gate overlapped nothing, each lane was told 2 of 8 jobs and the `Alone` gate 8. On `main` the same stubs ran the `Apart` pair at once in both runners. With the share left out, every lane was told 8.

## Refutation criterion

This is wrong if an adopted project that states its schedule still sees a gate green alone and red in the full proof from what ran beside it, which the new line names. It is also wrong if a proof ever turns red, or a check ever fails, from the share alone on a gate whose suite reads `RATCHET_JOBS`.

## Considered options

- **`--wide` as a gate that takes the whole machine.** Rejected. In `parallel.py` a wide gate is one that is itself parallel and is told its share of the jobs, and the method already says a gate that takes every core is measured alone. So `Wide` keeps that meaning in `check`. In the proof every lane is told its share whatever the line says, and a gate that needs every core is `Alone`.
- **The proof parses the recipes' flags.** Rejected. The runner is the project's, and the plugin names none.
- **A red finding when the flags and the lines differ.** Rejected. The binding wins in every runner either way, and a disagreement from an older recipe is not a broken gate.
- **Rerun a red clean run alone before the verdict.** Not built. A single bounded rerun, named in the report, is the stronger cure, since it tells contention from a real red in the same run. It costs a whole gate run with every lane idle, and a green rerun beside a red one is a reading under other conditions, which ADR 0080 does not let a gate rest on. The line names what ran beside the gate, and the project states the pair once. Left as a follow-up.
- **Conformance offers the new lines as an edit.** Not built here. Another change owns the conformance gate now, and its check that `perf` and `load` run alone reads the recipes. Once it reads the binding's lines, the template can drop its recipe flags.

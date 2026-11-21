---
status: accepted
---
# The proof's lanes keep the project's schedule

Settles issue #291. Builds on ADR 0020 (the proof), ADR 0069 (each verdict's wall time and lane) and ADR 0078 (the fault record). Amends the Speed section of `skills/method/tiers.md` and of the binding template.

The proof gives the same verdict however many lanes it runs, or it is not a proof of the gates. Two adopted projects saw it give another. Both failures had one shape: the proof starved a gate, or ran it beside a gate the project keeps apart, so a gate that is green alone was reported red.

- **Lanes times workers.** `RATCHET_JOBS` capped the lanes and nothing else. A suite in a lane started a worker per core, so 4 lanes on 16 cores ran about 64 workers. Five full proofs came in at 30 to 32 of 33 faults. Every miss was a clean run that failed on one of two timing-sensitive tests, which passed alone in 7 to 12 s against a 30 s timeout.
- **A schedule the proof never saw.** The project's deep check passed `--serial` for two gates that start the product on one fixed port and `--alone` for a gate that judges a p95 latency. The proof read neither. At 4 lanes one of the port gates was `NOT PROVEN` because the other held the port, and the latency gate was red before its fault in 3 of 5 full proofs.

Before this record the schedule lived only in the flags a project's recipes pass to `parallel.py`, and the proof's lanes took the next gate from a queue, whatever the others ran.

## Decision

**A restriction is never taken away.** A project says that two gates never run at once, or that a gate runs with nothing beside it. Every runner the plugin ships keeps every such statement, wherever it is made, and no other statement removes one. Running gates apart that could share the machine costs only time.

**A project states its schedule in either of two places, and both count.**

- The flags its recipes pass to `parallel.py`: `--serial a b` (a group that never runs at once), `--alone e` and `--wide g`, as before.
- Three optional lines of the binding's Speed section, read by `scripts/schedule.py`. ``- Apart: `a b` `c d` `` gives groups, one per code span, with the names split by spaces. ``- Alone: `e` `` names the gates that run with nothing beside them. ``- Wide: `g` `` names the gates that are themselves parallel. A line that is missing, or still holds its `<placeholder>`, states nothing. These lines are for what a recipe cannot pass as plain text, or for a project whose runner is not a recipe file.

**The proof reads the recipes.** Before its lanes start, `project_bite.py` reads the project's `justfile` and the files it imports with the plugin's recipe reader (`justfile_text.py`, which conformance and the tier checks use). In every recipe it follows the `{{ }}` interpolations through the variables they name, joining quoted strings with `+`, and takes each `--serial`, `--alone` and `--wide` that the recipe passes to `parallel.py`. The flags of every recipe add up, so a group one tier keeps apart is kept apart in the whole proof. The proof then keeps those flags and the binding's lines together. A value of `--runner`, `--jobs`, `--root` or `--report` may be anything. Anything else in a call to `parallel.py` that is not plain text (a function call, a recipe parameter, a backtick command, a variable that is none of these) could hide a flag. The proof cannot read it, so it prints one line before the lanes start: `bite: the <recipe> recipe passes parallel.py a schedule flag the proof cannot read as text, so its lanes do not keep it; state it in the binding's Speed section, ` and the two lines. That line is never red.

**`check` keeps both as well.** `parallel.py` keeps its flags and the binding's lines for this run's gates. It refuses its own flags exactly as before: a `--serial` name that is not among the gates, a gate in two `--serial` groups, and a gate in `--alone` and in a group. It refuses nothing in the binding. Two `Apart` groups that share a gate, and a gate that is both `Alone` and in a group, are kept as written in both runners. When the binding states any of the three lines and the run's flags keep something those lines do not, one line before the gates start names it: `parallel: the recipe passes --serial `c d` to this run and the binding's Speed section does not say it; this run keeps both, and the line - Apart: `a b` `c d` there would state it for good`. Groups with the same gates in another order agree, and a pair kept apart because one of them is `Alone` agrees. A project whose binding states none of the lines has one statement, its flags, and sees no such line.

**One relation decides who may run with whom, for both runners.** Two gates may run at once unless either is alone or both are in one group. `schedule.Board` holds the pending and running tasks and starts the first pending task that may run beside every running one, with the alone tasks last. `parallel.py` and the proof's lanes both take their next task from it. The board takes tasks, not gates, so two faults of one gate in a group never run at once either. With no restriction it starts the tasks in the order given, which is the queue the lanes read before.

**Each lane is told its share of the cores.** The pages say what `RATCHET_JOBS` and `ratchet.jobs` mean: they cap how many gates run at once (`skills/method/tiers.md`, "One number caps how many gates run at once"; the adopt skill, "cap the gates with `RATCHET_JOBS`"). Before this record the proof also passed the cap through to every gate, so a suite that reads `RATCHET_JOBS` started that many workers in each lane. The proof now sets `RATCHET_JOBS` for every gate it runs:

- With no cap below the machine's cores, each gate is told the cores divided by the lanes, at least 1, and an `Alone` gate the whole of `RATCHET_JOBS`, as before.
- With a cap below the cores, every gate is told the cap, as it was before. The proof then runs no more lanes than the cores divided by the cap, at least 1, so the lanes together never start more workers than the machine has cores. It runs fewer lanes rather than telling each one less.

The plugin cannot make a test runner obey the number. The binding's `Parallel` line says how the project's suite takes its worker count from it.

**A verdict a neighbour could cause names it.** On `RED BEFORE THE FAULT`, `RED FOR ANOTHER REASON`, `NOT PROVEN` and `TIMED OUT`, the verdict gains one line naming the gates other lanes ran while this gate's runs ran, and the cure: prove the gate alone with `--gate <gate> --jobs 1`, and if it bites there, state it apart from them or alone. The proof does not rerun the gate.

**The template states its schedule in its recipes.** The template's `serial1`, `alone` and `weekly` flags stay, and its `Apart`, `Alone` and `Wide` lines are placeholders. That leaves one statement, which both runners read, so nothing can drift. Moving the statement into the binding would leave the recipes passing nothing, and conformance's check that the timed gates run alone reads the recipes' `--alone`. Stating it in both places would make two copies that a later edit can split.

## What a project sees

**A project that states its schedule only in its recipes,** which is every project adopted before this record: after the upgrade the proof keeps the groups and alone gates its recipes pass to `parallel.py`, with no edit by anyone. `check` schedules exactly as before and prints nothing new. A recipe that passes `parallel.py` something the proof cannot read gets the one line above in every proof.

**Every project:** each gate run in the proof sees `RATCHET_JOBS`. A gate whose recipe never reads it runs as before. One that reads it starts at most its share of the cores. No conformance finding is added, so an upgrade turns nothing red.

**A project that caps the jobs** at a number below the machine's cores: every gate is told the cap, as before. On 16 cores a cap of 4 runs 4 lanes, as before. A cap of 8 runs 2 lanes, and a cap from 9 to 15 runs 1, where 4 ran before; the proof takes longer and no gate is told fewer workers than before.

## What was observed

The reviewer's property test drives `schedule.Board` and `run_lanes` over 3,000 seeded statements, including groups that share a gate and gates both alone and in a group, and found no two gates kept apart running at once. The repository's tests show, each red on the reviewed head and green here:

- Every recipe of the template that runs `parallel.py`, read from `just --dry-run` and run through the runner's real `main` with the template's binding, starts the same gates at the same steps as with no binding, never runs a pair its flags keep apart at once, and prints no line about the binding. The proof's schedule, read from the same `justfile`, keeps every one of those pairs apart. On the reviewed head the template's weekly gates, which its recipe runs one after another, ran at once.
- A project with a recipe that passes `--serial holder user`, or `--alone user`, and no line in its binding, where `user` fails while `holder` runs: the proof is green at 1, 2, 4 and 8 lanes.
- `parallel.py` with groups that share a gate, a gate alone and in a group, and a binding group whose gate the flags pass as alone runs every gate and keeps every restriction, where the reviewed head stopped with exit 2.
- A binding that states `Apart: none` and `Alone: none` leaves the flags' group and alone gate kept.
- With `RATCHET_JOBS` 4 on 16 cores each gate is told 4 and the `Alone` gate 4; with 8, each is told 8 on at most 2 lanes; with 1, 1; with 16 on 6 cores, each is told 1 and the `Alone` gate 16.

## Refutation criterion

This is wrong if an adopted project that states its schedule in its recipes or its binding still sees a gate green alone and red in the full proof from what ran beside it, which the neighbour line names. It is also wrong if a proof ever turns red, or a check ever fails, from the share alone on a gate whose suite reads `RATCHET_JOBS`.

## Considered options

- **The binding's line wins over a flag of its kind.** Rejected. A binding that stated a kind for other gates switched off the recipe's flags of that kind, and the template's own weekly recipe lost its group that way.
- **The binding as the only statement.** Rejected for now. Every project adopted before this record states its schedule in its recipes, and would have had to edit its binding before the proof kept it.
- **The proof parses the recipes' flags.** Taken. The plugin already reads the `justfile` with one reader and names no tool to do it. What the reader cannot read is named, not guessed.
- **The cap as a budget of cores for the whole proof.** Rejected. With a cap of 4 on 16 cores each lane would be told 1 where every gate was told 4 before, while 12 cores sat idle.
- **Each lane told the cores divided by the lanes, never more than the cap.** Rejected. With a cap of 8 on 16 cores at 4 lanes each gate would be told 4 where it was told 8 before. A gate whose run takes longer with fewer workers could then pass its time limit only because of the number.
- **A red finding when the flags and the lines differ.** Rejected. A restriction either one makes is kept, so a difference is a copy to tidy, not a broken gate.
- **Rerun a red clean run alone before the verdict.** Not built. A single bounded rerun, named in the report, is the stronger cure, since it tells contention from a real red in the same run. It costs a whole gate run with every lane idle, and a green rerun beside a red one is a reading under other conditions, which ADR 0080 does not let a gate rest on. The line names what ran beside the gate. Left as a follow-up.

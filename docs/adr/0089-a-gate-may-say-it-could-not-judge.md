---
status: proposed
---
# A gate may say it could not judge

Takes up the third outcome that pull request 347 (#139, ADR 0084 on that branch) gave the graceful-stop gate and took back out. Amends ADR 0088's build 1a (a reading per gate) and point 5 (the count is over the last runs, not a run in a row), and ADR 0084 point 3.

A gate's verdict must not depend on the load of its machine, and some gates cannot always tell. Whatever a gate writes, any process it starts can write too. So a gate never excuses itself: it makes a claim, and the reader decides.

## Evidence

On `origin/main` at 8cc3ff6:

- **A declared fact has a precedent.** `parallel.py` reads a gate's `none: <reason>` output as not applicable only when `tools(root)` finds that its binding row binds no tool (`Finished.standing`): the reader trusts the binding, not the gate. The binding is in the template's `decisions` guard group.
- **A guarded path is a reason.** `merge_ready.guard_reasons` returns `Reason("guard", f"{path} ({group.name})")`, and `verdict` gives `merge: owner` for any reason. `deep_reasons`, fed through `Asked`, is the precedent for a reason from a run. The new rule goes in `merge_ready.py`. With no forge, `merge.py` runs `check` after the judge (`run_check`), so it calls the rule again with that run's record.
- **ADR 0088's build 1a reads the machine per run, not per gate.** Its point 5 puts `k`, `share` and `m` only on the tier's line, and `parallel.py` runs many gates. This record amends 1a, which is not built: `parallel.py` reads the counters as each gate starts and ends, and each gate's line carries its own `k`, `share` and `m`.
- **The reviewer's test passes today.** A helper under `tests/` that writes `machine: ...` to `RATCHET_NOT_JUDGED` on a failed assertion and exits 0 gets no merge reason (`[]`); under `scripts/` it gets `guard: scripts/gate_tool.py (gate-commands)`. The proof's selection (`project_bite.gate_paths_changed`) maps `tests/` to no gate. After the build it is red first twice: the claim with `m` of 1 is red in `check`, and with a record naming the gate not judged, the merge is the owner's.
- **Mutation has no clock.** `mutation_survivors.py` counts timeouts as kills (`killed = sum(m.state in {KILLED, TIMEOUT} ...)`) and reds a registered one: "rerun it with less load".
- **The graceful-stop gate on `origin/main`** judges by the load command's requests. In #139's third sighting, at a load of 8 to 14 on 16 cores, the planted fault passed it: `DOES NOT BITE`.

## Decision

### 1. A gate claims the machine, and the reader confirms it

Each reader that runs a gate (`parallel.py`, `budget.py`, `project_bite.py`) sets `RATCHET_NOT_JUDGED` to a fresh path in a private temporary directory for each gate run. A gate whose wait ended before it could judge appends one line and exits 0:

```text
machine: the service did not accept the held request within 30s
```

The machine is the only cause a gate may claim, because it is the only one that differs from run to run.

```text
Claim = Machine(words: str)
Declared = (gate: str, reason: str)                # the owner's bullet, point 2
Outcome = Green | Red(why) | NotJudged(words, m) | Weaker(reason, Green | Red)

def judge(exit: int, claims: list[str] | None, m: float, weaker: str | None) -> Outcome: ...
```

**The reader accepts a claim only when its own reading confirms it.** With the gate's `m` for that run at 1.1 or more, ADR 0088's noise floor, the gate is not judged. Below that, the claim is red. A wait has no budget to subtract from, so a wait far too short is excused on every busy run, and point 5's count names it.

**Where readings cannot be trusted, no claim is accepted.** `m` is then 1 (ADR 0088 point 2), so every wait that runs out is red, as today. In a container with a CPU quota, that is every run under load, and a rerun on a quieter machine settles it. A hosted job has its machine to itself, so `m` is near 1 there too: the hosted run always judges a gate, as it judges the loop.

**A gate that never heard of this behaves as today**, and every exit code keeps its meaning. A plugin script writes the line through one helper in `gatelib.py` and removes the variable from the commands it starts.

**A reader is a gate to its parent.** When a gate it ran was not judged and none was red, it writes `machine: <gate>: <words>` to its parent's file, and the parent applies its own reading. A reader removes its directory when the gate ends or on an interrupt. After a kill it cannot catch, the system's cleaning of temporary directories does. It reads at most 1 MiB.

### 2. A cause that is the same on every run is declared in the binding

A gate that cannot read what it needs, or that nothing bounds, meets the same cause on every run: a fact about the project. The owner declares it in a binding section keyed like `## What each gate reads`:

```text
## Weaker gates

- graceful-stop: the service runs in a network namespace the gate cannot read
```

The reader sets `RATCHET_WEAKER` to the reason for that gate alone. A plugin gate that meets a fixed cause judges by its weaker rule when the variable is set, and says so. Without it, the gate is red and names the bullet to declare. The reader labels a declared gate from the binding on every run, whatever the gate prints, as it reads `none:` today. A bullet with no reason is red. A name not among the run's gates is ignored.

| Plugin gate | Claims `machine` when | May declare | Weaker rule |
| --- | --- | --- | --- |
| `with_service.py --graceful` | the command did not send, or the held request was not accepted, within `--timeout` | it cannot see whether the service accepted or still listens, cannot reach the route, or the route answers before the last byte | the judgement on `origin/main`, by the load command's requests |
| `mutation_survivors.py` | a registered mutant timed out | nothing | none |
| every other plugin gate | never | nothing | none |

That weaker rule passed #139's planted fault by chance under load. A project that declares it has a weaker gate, and every run says so. A command that ends before the signal is red, as one that never creates `RATCHET_SENDING` is today. An unregistered timeout stays a kill, counted apart, so a slowed survivor still passes: a known limit.

The loop keeps ADR 0088's reasons: no gate claims for it, and `no budget` is a fact, a key the owner has not merged yet.

### 3. What each reader does

| Who claims or declares | What the reader checked | Outcome | Exit of `check` | Line a person sees | Who may merge |
| --- | --- | --- | --- | --- | --- |
| nobody | exit 0, no file | green | 0 | `green in 4.1s` | the agent |
| nobody | exit not 0 | red | 1 | `red (exit N)` and the file's lines | nobody |
| the gate: `machine:` | exit 0, its `m` 1.1 or more | not judged | 0 | `not judged (machine: <words>; m 1.6)`; last line `parallel: not judged: graceful-stop; 25 ran and passed` | the owner if the change touches the gate's files, else the agent |
| the gate: `machine:` | exit 0, `m` below 1.1 or untrusted | red | 1 | `red: it claims the machine (<words>); m 1.02` | nobody |
| the gate: another line, or none | exit 0 | red | 1 | `red: claim line 1 is not machine: <words>` | nobody |
| the owner: a `## Weaker gates` bullet | exit 0 | green by a weaker rule | 0 | `green by a weaker rule (<reason>)`; last line `parallel: green (25 ran and passed, 1 by a weaker rule)` | the agent |
| the owner: the same bullet | exit not 0 | red | 1 | `red (exit N), by a weaker rule` | nobody |
| the gate, in a run of the proof | exit 0, `m` 1.1 or more | the fault is `NOT JUDGED` | 0 if every other fault bit | `bite: graceful-stop: <fault>: NOT JUDGED (machine: <words>; m 1.4)`; last line `bite: not judged: 31 of 33 faults bit, 2 not judged` | the owner |

In the proof, an unconfirmed claim is red: a bite on a planted run, `RED BEFORE THE FAULT` on a clean one. In CI, `budget.py` exits 0 with the warning `ratchet-budget: graceful-stop: not judged (machine: ...)`. The record line's `status` is `not judged`, never counted as green. From the exit code and the last line alone, a person sees 0 and `not judged`.

### 4. A gate that could not judge the change gives the merge to the owner

The merge judge gains the reason `not-judged`, in the form of `guard`, for each fault whose verdict is `NOT JUDGED` and each gate not judged on the head whose files the change touches:

```text
not-judged: graceful-stop (machine: the held request was not accepted within 30s); the change touches tests/support.py
```

A gate's files are the paths the proof's selection (`project_bite.could_affect`) maps to it. A path that defines no gate is every gate's file, since the plugin cannot tell which code a gate judges. So the agent merges, with the warning, only a change confined to paths that define other gates. Nothing is red, held or rerun. The merge comment names every gate not judged on the merged head.

With no forge, `merge.py` reads the record of its own `check`. On a forge, `merge.yml` passes the head run's record as `--run`, which every job uploads from ADR 0088's build 3.

### 5. It cannot go quiet

The route reads each gate's last ten runs in the record and names, to the owner, a gate not judged, or whose claim was red, in three or more. Ten is ADR 0088's span for a median. A count in a row resets on every judged run, so it never names a gate not judged exactly when it would be red. Three in ten is far above the refutation criterion's one in twenty. On a forge it reads each job's cached record (ADR 0088 build 5). ADR 0088 point 5's loop count works the same way. The owner can run on a quieter machine or lengthen the wait in the guarded recipe. The count holds nothing.

### 6. What an adopted project sees on upgrade

Nothing green turns red, and every exit code keeps its meaning. The graceful-stop gate's sending timeout and a registered mutant's timeout are red today and stay red unless a reading confirms the machine. Through conformance `Edit`s (ADR 0037), the upgrade adds `--run` to a `merge.yml` in the previous template's form. Where the binding binds the graceful-stop row, it adds `- graceful-stop: kept as before the upgrade (ADR 0089)`, so a gate that cannot read keeps today's judgement and says so. The owner merges both guarded edits. Until a hand-edited `merge.yml` takes its edit, only the count names a gate not judged on a forge.

## What this would have said

| Case | Today | Under this record |
| --- | --- | --- |
| #139's third sighting, at load 8 to 14 | `DOES NOT BITE` | `NOT JUDGED`, the owner merges. Hosted: a bite. |
| Pull request 347, a service in another network namespace | its fix round falls back to the judgement on `origin/main` | red, naming the bullet. With it: green by a weaker rule, said on every run. |
| The reviewer's helper | green, the agent merges | red where `m` is below 1.1, hosted included. Under confirmed load: the owner merges. |

## Refutation criterion

This is wrong, and point 1 or 4 is reopened, if any of these is seen:

- More than one in twenty accepted claims are red when run again on a quiet machine.
- More than one in twenty gate runs in the first ten adopted projects are not judged.
- A claim is accepted on a hosted runner.
- A gate is not judged in three of its last ten runs and the route names nothing.
- An upgrade turns a green red, or changes what a project gate's exit means.

## Considered options

- **The smallest alternative: each gate falls back to a weaker judgement on its own.** For a fixed cause it judges as a declaration does, without the owner's merge or the label on every run. For a machine cause it puts the machine back into the verdict: the graceful-stop fallback passed #139's planted fault under load. This record costs a claim file and a reading per gate run, a binding section and a merge reason. In return, no verdict rests on load nobody measured. This record is recommended over the alternative.
- **Any reason claimed at run time**, the first draft, or **a reserved exit code**. Rejected: any process a gate starts can write the claim, and tools give codes above 1 meanings.
- **Hold a pull request on not judged, or retry once.** Rejected, as ADR 0088 and ADR 0080 reject them.

## What the owner may reverse

Each of these is a default:

- A fault not judged, or a gate not judged whose files the change touches, makes the merge the owner's; otherwise the agent merges, with the warning.
- A path that defines no gate is every gate's file. With the proof's map alone, a test helper edited under confirmed load is the agent's merge.
- A claim is accepted at an `m` of 1.1 or more, with nothing subtracted.
- An outer reader applies its own reading to a claim passed on.
- A registered mutant that timed out stays red unless the reading confirms the machine; an unregistered timeout stays a kill, counted apart.
- The count is three of the last ten runs, told to the owner.
- The upgrade declares the graceful-stop gate weaker where the binding binds it.

## For the builders

Each pull request starts from a red test and ends in something a user can see. Scripts are under `scripts/`, tests under `hooks/tests/`.

1. **The runners judge a claim and a declaration.** `runrecord.py`, `gatelib.py`, `parallel.py`, `budget.py`, the template's binding, `docs/gates.md`, `skills/method/tiers.md`. Red first: `test_parallel.py` and `test_scripts.py` (each row of point 3), `test_gatelib.py`, `test_runrecord.py`, and the reviewer's helper, red with `m` of 1. Waits for pull request 346, which edits `parallel.py`, and for ADR 0088's build 1a with each gate's reading.
2. **The proof judges a claim.** `project_bite.py`, `bite.py`. Red first: `test_project_bite.py`, `test_bite.py`. Waits for 1.
3. **The merge is the owner's.** `merge_ready.py`, `merge.py`, the template's `merge.yml`, `conformance.py`. Red first: `test_merge_ready.py` (the reviewer's test gives `not-judged`; a change to another gate's paths stays the agent's), `test_merge.py`, `test_templates.py`, `test_upgrade_writes.py`. Waits for 2 and ADR 0088's build 3.
4. **The graceful-stop gate claims and declares.** `with_service.py`, `conformance.py`, `tiers.md`. Red first: `test_with_service.py`, `test_upgrade_writes.py`. Waits for pull request 347 and 3.
5. **The mutation gate claims.** `mutation_survivors.py`, `bite.py`. Red first: `test_mutation_survivors.py`, `test_bite.py`. Waits for 3.
6. **It cannot go quiet.** `route.py`. Red first: `test_route.py` (judged runs between three not judged). Waits for 1, and on a forge for ADR 0088's build 5. Whichever of it and ADR 0088's build 2 comes first writes the counter.

No gate writes a claim before 3, since until then the merge judge cannot see it.

---
status: proposed
---
# A gate may say it could not judge

Takes up the third outcome that pull request 347 (#139, ADR 0084 on that branch) gave the graceful-stop gate and then took back out. Extends ADR 0088: its not judged and its three reasons become one outcome that every gate and every reader shares. Amends ADR 0088 point 2 (the `Reason` type) and point 5 (the count and the last line), and ADR 0084 point 3 (could not judge is red in `check`).

A gate's verdict must not depend on the load or the timing of its machine. Some gates cannot always tell, and today every reader turns that into green or red.

## Evidence

Every place that turns a gate's result into a verdict, on `origin/main` at 8cc3ff6:

| Reader | What it does with an exit other than 0 and 1 |
| --- | --- |
| `parallel.py`, the local `check` | red, `red (exit N)`. A gate that exits 0 with one `later:` or `none:` line as its whole output is counted apart and never green. |
| `budget.py`, every CI and deep job | passes the exit through, so 3 stays 3, status `failed`. It does not capture output. |
| The template's `ci.yml`, `deep.yml` | the step fails the job. A job's conclusion is success or failure. |
| `forge_runs.py`, `merge.yml` | `success` is green, `cancelled` and `skipped` are dropped, anything else is red. The judge runs only on a `success` run. |
| `merge.py run_check`, no forge | the reason `gates`, red on the merge result. |
| `drain.py` | `check --final` reads no gate. `land` reads a merge exit other than 0 or 1 as "stopped, run land again". The pipeline's `checkPasses` is true only on exit 0. |
| `bite.py` | with the fault planted, a bite. On the clean run, `RED ON CLEAN STATE`. |
| `project_bite.py` | a bite when the output holds the bite's `expect` more often than the clean run did; on the clean run, `RED BEFORE THE FAULT`. Its own limit gives `TIMED OUT`, red. |
| `run_time.py`, `red_gates.py` | read only `green` and `red (exit N)`. |
| `conformance.py` | reads no gate's exit. |

The plugin's gates that can already tell:

- `with_service.py --graceful`, on pull request 347's branch, exits 3 with `with_service: could not judge` for five causes. `check` reads it as red, both proofs as a bite on a planted fault, and a project whose sockets it cannot read is never green.
- `mutation_survivors.py` counts an unregistered timed-out mutant as a kill, as mutation tools do. A registered one is red: "rerun it with less load". The plugin's proof plants that case and wants red.
- The listener check (ADR 0079) falls back to a weaker judgement and says "not checked here".
- The loop's budget (ADR 0088), not built yet, has not judged with three reasons.

## Decision

### 1. A gate says it in a file the runner names

Every reader that runs a gate sets `RATCHET_NOT_JUDGED` to a path in a private directory it made for that run. A gate that could not judge appends one line per cause and exits 0:

```text
machine: the service did not accept the held request within 30s
```

| Exit | File | Judgement |
| --- | --- | --- |
| 0 | absent | green |
| 0 | every line `<reason>: <words>`, a known reason, words not empty | not judged |
| 0 | empty, or any other line | red, naming the first bad line |
| non-zero | either | red, as today, with the file's lines printed |

**A gate that never heard of this behaves as today.** No project tool reads the variable, so none writes the file, and every exit code means what it meant. Exit 3 is red. A plugin script that writes the file removes the variable from the commands it starts, so a child cannot speak for it. `gatelib.py` holds the one helper that writes a line.

**A runner is a gate too.** `parallel.py`, `budget.py` and `project_bite.py` write their parent's file when a gate they ran was not judged and none was red, so no reader parses output.

**Why not the others.**

- *A reserved exit code.* Tools already give codes above 1 meanings, such as an internal error or nothing collected, so a gate that exits 3 today is red and must stay red. Every reader above treats non-zero as red, and a workflow edited by hand would still do so.
- *A fixed line on the output.* `budget.py` does not capture output, runners append lines after a gate's last one, and a test or a replayed log can print any text. A stray line would turn green into not judged.
- *A declaration in the binding.* It says which gate may, not when, and adds a conformance rule. The file needs no such guard.

### 2. Reasons are a closed set, with required words

```text
class Reason(Enum):
    MACHINE = "machine"   # the machine's load or timing ended a wait before the gate could judge
    UNREAD = "unread"     # what the gate judges could not be read where it ran
    UNBOUND = "unbound"   # the binding gives the gate nothing it can judge

NotJudged = (reason: Reason, words: str)   # one line: what was missing, what to change
Judgement = Green | Red(exit: int) | NotJudged
```

These are ADR 0088's reasons, named by who acts: the machine is `machine`, times unread is `unread`, no budget is `unbound`. ADR 0088's printed words stay. The type lives in `runrecord.py`, beside the record lines that carry it. Free text alone would give point 4 nothing exact to count.

| Gate | Cause | Reason |
| --- | --- | --- |
| `with_service.py --graceful` | the load command did not send within `--timeout` or ended before the signal; the held request was not accepted within `--timeout` | machine |
| | the gate could not send the held request, may not read the service's open files, or finds the connection in no table | unread |
| | the service answered before the request's last byte | unbound |
| `mutation_survivors.py` | a registered mutant timed out | machine |

The loop gives all three, as ADR 0088 says, and a runner passes on its gates' reasons. `later:` and `none:` stay apart: the binding declares them, and not judged is what happened on one run.

### 3. What each reader does

Green and red are unchanged everywhere, and a malformed file is red everywhere. "Warning" is the annotation `budget.py` already prints.

| Reader | Not judged: exit and effect | What a person sees |
| --- | --- | --- |
| Local `check` | 0, or 1 when another gate is red | `parallel: graceful-stop: not judged (machine: ...) in 41.2s`; last line `parallel: not judged: graceful-stop (machine); 25 ran and passed`, or `parallel: red` after a line naming it |
| Pull request | 0 with a warning; the next tier runs | the job's last line `ratchet-budget: graceful-stop took 41.2s (...): not judged (machine: ...)` |
| Default-branch push | 0 with a warning; ADR 0069 holds nothing | the same line |
| Deep run | 0 with a warning; the run is finished and not red, so ADR 0077 holds nothing | the same line |
| The proof | verdict `NOT JUDGED`; exit 0 when every other fault bit; writes its parent's file | `bite: graceful-stop: <fault>: NOT JUDGED (machine: ...)` and which run; last line `bite: not judged: 31 of 33 faults bit, 2 not judged (graceful-stop: machine), ...` |
| Merge judge | unchanged; holds nothing | the run's warning; once ADR 0088's `loop` job exists, its summary lists the gates not judged |
| Drain | `land` merges; `checkPasses` is true | the check's last line, in what `land` prints and the tail the pipeline returns |

**A fault not judged is neither caught nor missed.** A clean, planted or later run that is not judged makes the fault `NOT JUDGED`, never `bites`, `DOES NOT BITE`, `RED BEFORE THE FAULT` or `NOT PROVEN`.

**From the exit code and the last line alone,** a person sees 0 and `not judged`, never green. The tier's record line has `status` `not judged`, which `run_time.py` does not count as green. When ADR 0088's loop line is last, it ends `; not judged: <gates>`.

### 4. It cannot go quiet

A not-judged record line carries `reason`. The route counts runs in a row per gate and reason, from the record (on a forge, each job's cached record, as ADR 0088 caches it), and names a run of three. One counter serves both records, and the loop is one more gate, `loop`.

| Reason | Told | What they can do |
| --- | --- | --- |
| machine | the owner | run on a quieter machine. When the words name a wait, such as `--timeout`, an agent may lengthen it: a longer wait turns not judged into judged and never red into green. |
| unread | the owner | let the gate read what it needs, or write the gate's row as `none: <reason>` |
| unbound | an agent; the owner when the words name an owner's question, such as `loop.wait` | change the binding as the words say |

**A pull request is never held by a not judged.** The push run and the deep run judge the change again, and a red there holds the next merge (ADR 0069, ADR 0077). `unread` and `unbound` answer the same on every run, so holding one pull request fixes nothing.

**No place always judges a gate,** as the hosted run judges the loop. A hosted job has its machine to itself, so three `machine` runs in a row there mean the gate's wait is too short for the project's runner. With no forge, only the route's line stands behind a gate never judged.

### 5. What an adopted project sees on upgrade

Nothing turns red. No workflow, justfile, binding or threshold changes, and conformance gains no finding. Every exit code means what it meant. Two plugin cases move from red to not judged: the graceful-stop gate's could not judge, whose exit 3 never reached `main`, and a registered mutant that timed out. A workflow edited by hand to skip `budget.py` sees exit 0 and the gate's last line, with no warning.

## What this would have said

| Case | Today | Under this record |
| --- | --- | --- |
| ADR 0088's loop | its own reasons and counter | the same words, one type and one counter |
| #139's third sighting, a planted fault green at load 8 to 14 | `DOES NOT BITE` | with pull request 347, a held request not accepted in time is `NOT JUDGED (machine)`, never a bite |
| Pull request 347, a service in another network namespace | exit 3: red in `check`, a bite in the proof | `unread` on every run, never red; the owner is told after three |
| A registered mutant timed out under load | red | not judged, `machine` |
| An unregistered survivor slowed past the tool's limit | a kill | still a kill: the gate cannot tell a looping mutant from a slowed one |

#291's clean runs, red from contention, write no file and stay red: ADR 0086 on pull request 346 owns them.

## Refutation criterion

This is wrong, and point 1 or 4 is reopened, if any of these is seen:

- More than one in twenty gates not judged for `machine` are not judged again on a quiet machine.
- More than one in twenty gate runs in the first ten adopted projects are not judged.
- A gate is not judged three runs in a row and the route names nothing.
- An upgrade turns a green red, or changes what a project gate's exit means.
- A command not meant to writes the file.

## Considered options

- **The smallest alternative: no general outcome.** Each gate that cannot judge falls back to a weaker judgement and says so, as the listener check does, and no reader changes. For the graceful-stop gate that is the old judgement by the load command's requests, which passed the planted fault in #139. A project that cannot be read gets green with a note, which the proof reads as `DOES NOT BITE`. The registered mutant stays red. Each fallback puts the machine back into the verdict. The record costs one file per gate run, in scripts the plugin owns. It is recommended.
- **Hold a pull request on a not judged.** Rejected, as ADR 0088 rejected it for the loop.
- **Retry once.** Rejected: ADR 0080 rules out retries, and a retry that passes hides the first answer.
- **Make the proof's `TIMED OUT` a not judged.** Not now: a hang is a real fault. Once ADR 0088 1a records the machine factor, its `judge` could excuse a timeout the machine explains.

## What the owner may reverse

Each of these is a default:

- Not judged exits 0 and never holds a merge, the proof of a gate the change edits included.
- The merge judge's comment does not name gates not judged.
- The reasons are three, shared with ADR 0088.
- `machine` and `unread` go to the owner after three in a row, and `unbound` to an agent.
- A registered mutant that timed out is not judged, and an unregistered one stays a kill.
- A malformed file is red.

## For the builders

Each pull request starts from a red test and ends in something a user can see. Scripts are under `scripts/`, tests under `hooks/tests/`.

1. **The runners read it.** `runrecord.py`, `gatelib.py`, `parallel.py`, `budget.py`, `docs/gates.md`, `skills/method/tiers.md`. Seen: a gate that writes `machine: x` and exits 0 ends `check` on `parallel: not judged`, exit 0. Red first: `test_parallel.py` and `test_scripts.py` (each row of point 1's table, exit 3 with no file red, the parent's file written), `test_gatelib.py`, `test_runrecord.py`. Waits for pull request 346, which edits `parallel.py`.
2. **The proof reads it.** `project_bite.py`, `bite.py`. Seen: a fault whose gate could not judge is `NOT JUDGED`, and the proof exits 0. Red first: `test_project_bite.py` (clean, planted and later runs), `test_bite.py`. Waits for 1 and pull request 346.
3. **The graceful-stop gate says it.** `with_service.py`, `tiers.md`, ADR 0084. Red first: `test_with_service.py`. Waits for pull request 347 and for 2, since before 2 an exit 0 on a planted fault reads `DOES NOT BITE`.
4. **The mutation gate says it.** `mutation_survivors.py`, `bite.py`. Red first: `test_mutation_survivors.py`, `test_bite.py`. Waits for 2.
5. **It cannot go quiet.** `route.py`. Red first: `test_route.py`. Waits for 1. It shares its counter with ADR 0088 point 5, and whichever is built first writes it.

1 and 2 go first, so no gate writes the file before every reader can read it.

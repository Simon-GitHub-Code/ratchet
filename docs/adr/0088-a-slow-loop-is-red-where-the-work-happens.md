---
status: proposed
---
# A slow loop is red where the work happens

Settles #308. Takes up #299 and #125, and the timing parts of #296 and #305. Supersedes ADR 0010. Amends ADR 0069 (the consequence of settled item 5 that a red from runner variance holds merges), ADR 0080 (point 5, the bullet on ceilings), and `skills/method/tiers.md` ("Measured, not hoped"), `skills/method/ratchet.md` (section 6) and the adopt skill's speed step. Builds on ADR 0077: a red deep run holds the next merge, which makes moving a check later safe.

The method says that gates which pile up get skipped, that a change runs only what it can affect, and that a question needing more than the pull-request budget belongs in tier 2 (`tiers.md`). Nothing makes a breach of that red. On 0.13.3:

- **The local `check` never judges time.** `parallel.py` writes every gate's record with a null ceiling.
- **A pull request only reports.** The template's `ci.yml` passes `--over report` on a pull request, and each tier 1 job is held to its own key or the `tier1` key, so the tier's wall clock is judged nowhere.
- **The tier ceilings have a floor that never falls.** `budget --set` records `max(ceil(1.5 × one run), floor)`, with a floor of 60 s for `tier0` and 900 s for `tier1` (ADR 0010). A `check` of 234 s held to 900 can almost quadruple and stay green (#299), and nothing offers a lower ceiling.
- **A tier red only on time skips the next tier on the default branch.** Tier 1 `needs` tier 0, and on a push `--over block` makes a slow tier 0 red. In #125 two pushes went red at 75.2 s against 60 with every gate green, tier 1 did not run, and ADR 0069 settled item 5 held every merge.
- **The record cannot tell a slow loop from a slow machine.** Its lines hold each gate's seconds, the tier's wall clock, and each fault's `lane` and `verdict` (#333), but no start offset, CPU time or load.

So in #308 every pull request of a day spent 650 to 830 s in local `check` and about 14 minutes in CI, against ceilings of 900 and 1,800, and the owner found it. This record is the class fix: the owner sets the loop's budget, the work that breaks it is red where it is done, and the red says the way out.

## Decision

### 1. One model: three kinds of limit

Every limit on time or work is a row of one table. `judge` returns a judgement from a limit and a reading, and a table maps the kind of limit, the place and the judgement to what happens. No caller branches on the place.

| Limit | Key in `quality/thresholds.toml` | Who sets it | Judged by | Before tier 2 | In tier 2 |
| --- | --- | --- | --- | --- | --- |
| The loop's budget | `[ceilings.loop]` `seconds` | the owner, asked once (point 7) | `parallel.py --budget loop` at the end of `check`, and of `check-fast` when it ran every gate; the `loop` job of `ci` | over is red on the branch and the pull request, reported on the default branch | not judged |
| A seconds ceiling | `[ceilings.seconds]` `<key>` | `budget --set` from five or more runs (point 4) | `budget.py` | reported, never red | over is red and holds the next merge (ADR 0077) |
| A work ceiling | `[ceilings.work.<gate>]` `<count>` | the same | the runner (point 3) | red everywhere | red everywhere |

```text
class Reason(Enum):
    MACHINE = "the machine explains the time"
    NO_BUDGET = "no budget is declared"
    UNREAD = "the run's times could not be read"

Judgement = Within | Over | NotJudged(reason: Reason)
Forced = (cause: str, record: str, seconds: float)
Reading = (seconds: float | None, m: float, machine_read: bool, forced: Forced | None)

def judge(budget: float | None, reading: Reading) -> Judgement: ...
EFFECT: Mapping[tuple[Kind, Place, Judgement], Effect]
```

The set is closed: within, over, or not judged for exactly one of three reasons. `Place` is `local`, `pull-request`, `default-branch` or `deep`, and `Effect` holds the exit code, the annotation and the words. A work count has no excuse, so a work ceiling is within or over (point 3).

**The loop is what the owner waits on:** `check` locally, which an agent runs after each edit, and the pull request's `ci` run on a forge. One number, the owner's, covers both. Every change is judged on every run, whatever its size. Only the seconds of a proof the method forces are left out (point 2).

**On a forge, the judged time is the run's work path.** The `loop` job runs last (`needs` every other job, `if: always()`). With the run's own token, given read access to the run's jobs in its own `permissions:`, it reads the run's jobs in one request and the timing artefacts every job uploads. The jobs listing does not give the `needs` graph, so the job reads it from the workflow file with the standard library only. Its reading is the longest path through that graph, each job counted from start to end. A wait for a runner after a job's `needs` ended is not judged, and is printed when over a tenth of the run. When a read fails, the loop is not judged, times unread: never green as if judged, and never red.

### 2. Not judged has one named reason, and the machine is one of them

`judge` reads the budget `B`, the judged time `J`, the seconds `F` of a forced proof of the same run, and the machine factor `m`. It decides in this order:

1. No `[ceilings.loop]` key: **not judged**, no budget is declared.
2. `J` could not be read: **not judged**, the run's times could not be read.
3. `J′ = J − F`, with `F` 0 when no proof was forced.
4. `J′ ≤ B`: **within**.
5. `J′ − E > B`: **over**.
6. Otherwise: **not judged**, the machine explains the time.

Not judged is never green and never red: its line names the reason (point 6).

`E` is `J′ × (1 − 1/m)`, where `m` is read in the same run. A factor below 1.1, the noise floor, counts as 1.

**A proof the method forces is left out, and nothing else.** Today only a plugin pin that differs from the merge base forces the whole proof (ADR 0069, settled item 4). `project_bite.py` decides that from the pin, not from the first fallback it tests, so an upgrade that also edits the binding outside its rows is forced. The proof's lines then carry `forced`, naming the cause (`plugin-pin`) and its record. `F` is the proof gate's seconds, off the wall clock locally and off its job before the longest path on a forge. Every other gate is judged, so a gate the upgrade slowed still counts. Beside other gates, `F` can exceed what the proof added, an error toward within on a moved pin only. Every other fallback to every gate is judged in full, a binding edit with the pin unchanged included. Until #307 narrows or defers those, a loop they take past the budget is over, its way out names placement, and a builder does not exempt them.

**A forced line counts only for this run and this tree.** Each record line carries `run`, from `RATCHET_RUN`, and `tree`, the content proved, and `F` comes only from lines where both are the judging run's. Locally the justfile exports one `RATCHET_RUN` per `check`. On a forge each job's `just` makes its own, so the template's `ci.yml` gains one line in its top-level `env:`, `RATCHET_RUN: ${{ github.run_id }}-${{ github.run_attempt }}`, which the justfile keeps for every job of the attempt. A line from a cache, another run or another tree never counts. Where `ci.yml` lacks the line, nothing matches the `loop` job's run, so the loop is judged in full until the upgrade adds it (point 7).

**The machine factor.** The runner, `parallel.py` locally and `budget.py` in each CI job, times a chain of hashes calibrated to about 20 ms of CPU every 2 s, reading each sample's wall time and CPU time. At the start and the end it reads the busy CPU time of the processors it can use, including time stolen by a hypervisor (`/proc/stat` on Linux), and the CPU time of its own work.

- `k`, the contention a ready task met, is the median over the samples of wall time over CPU time. It needs no baseline: on a machine with a free core, a sample's wall time equals its CPU time.
- `share` is `(own + others) / own`, where `own` is the CPU time of the runner and its descendants, and `others` is the busy time less `own`.
- `m = min(k, share)`. Contention the check makes for itself (lanes times a suite's workers past the cores, #291) raises `k` but leaves `share` near 1, so it is not excused. Contention from other work raises both.

**The run's own work, detached or not, is never excused.** A gate that detaches work into a new session re-parents it away from the run, and the CPU time of descendants counts only once they are waited for. So on Linux the runner makes itself its descendants' reaper (`PR_SET_CHILD_SUBREAPER`): a detached process is re-parented to it, and its CPU time joins the run's when it is reaped, or is read at the end while it still runs. Under load, three detaching gates counted 15.5 and 15.8 s of own CPU time with the reaper and 0.4 and 0.9 s without: over with it, not judged without. Work handed to a process outside the run's descent, such as a server started before it, cannot be told from other tenants' work. Its excuse is bounded by `k`, 1.0 to 2.07 there, up to half of `J`.

**The reaper never changes a gate's verdict.** The runner reaps only orphans handed to it, never a process it started and still waits for, so a gate's exit status reaches it unchanged. A reap of any child can take a red gate's exit 3 and leave the runner reading 0. Where that cannot be guaranteed, the reaper is off and `m` is 1. Red first: with the reaper on, a gate that exits 3 beside one that detaches work leaves the tier red, in `parallel.py` and `budget.py`.

**The factor excuses only readings of this run's processors.** When the CPU affinity covers fewer processors than the machine reports, the control group sets a CPU quota, or any reading is missing or unreadable, `m` is 1. That one rule covers a container reading its host's counters, an operating system without them, and a failed read. The loop is judged on its wall clock alone, and an over adds that the machine could not be read, so a busy machine is a possible cause. It stays red: a busy day there can give a false red, and a rerun on a quiet machine settles it. On 2 pinned processors, twice the work read through the host's line was not judged in 2 of 2 runs, and over through the pinned ones'.

At a load of 19 on 16 cores, five runs of a fixed workload ran within 1.05 of their CPU time, so a rule on the load average would excuse runs the machine did not slow.

**Its error, stated.**

- It does not see a slowdown that raises CPU time itself: a busy sibling hyperthread, a lower clock, or a slower processor. The same fixed work took 1.49 to 1.82 s of CPU in those five runs, so on a shared machine up to about 1.2 of slowdown is not excused.
- On a hosted runner the job has its virtual machine to itself, so `m` is near 1 and a slower host is not seen either. Over five runs this repository's test shards spread 1.56 at the median and up to 2.15, and its work path 1.16. Deep runs spread 1.68 (#299). A loop within that spread of its budget can go red on a slow runner.
- It does not see contention for a disk, a network or memory bandwidth, and it excuses a gate that waits on input and output while others use the CPU: twice the waits beside two loaders was not judged in 2 of 2 runs.
- The probe calibrated to 20 ms took a median of 43 ms of CPU under load. `k` on identical cases ranged from 1.24 to 1.77.

### 3. A count of work is a ceiling that does not move with the runner

**A gate states a count in one line of its output:** `ratchet-work: <count> <number>`, for example `ratchet-work: builds-per-check 1`. The recipe prints it from whatever its tool reports, and the plugin names no tool. `mutation_survivors.py` prints `tests-per-mutant` itself, from the report's tests completed per mutant. `budget.py` reads a gate's output through a pipe and passes it on unchanged, and each count goes into the gate's record line as `work`.

**It is held like any ceiling.** The key sits under `ceilings.`, so `lowered.py` already reads raising one as a loosening that needs a `Relaxes:` line and the owner. A count over its key is red on every run, since it is the same on every runner: tests per mutant were 19.03 with a bad test order and 10.51 with a good one, hosted and locally alike (#305). A declared key whose gate printed no count, or a count of zero, is red, and its line says the count was missing: an absent count never passes.

**A count qualifies when it does not grow with the code:** a count per unit of work, not a total. Suite starts per check, builds per check and the runs of one producer per tier qualify, and so do tests per mutant while the order runs a mutant's own tests first. The number of tests does not: every new test would need an owner's record. A count that rightly rises is raised the way any threshold is loosened, by the owner's merge.

**When it replaces a seconds ceiling.** Before tier 2 a seconds ceiling only reports, so the count is a gate's only red guard there. In tier 2 both stand, since the deep run's length sets how fast the queue merges (ADR 0077). Where a gate has a work key, `budget` offers removing its seconds key before tier 2, as an owner's `Relaxes:` line naming this record.

### 4. A ceiling follows the measurement down, and the floor goes

**A first ceiling comes from five runs or more.** `budget --set <key>` reads the record, not one run it times: the key's green runs that were within their ceiling or had none, up to the last ten. It records `ceil(max × 1.25)` for seconds, or `max × 1.1` rounded up for a count, and with fewer than five runs records nothing and says how many it has. The headroom is over the slowest run seen: in #125 tier 0 ranged 37.6 to 75.7 s over 45 runs, which 1.5 times one 40 s run cannot hold.

**The record is the judging runner's.** The CI templates add `.ratchet/timing.jsonl` to each job's cache, keyed per job with the commit and restored by prefix, so each job reads its gate's history on that kind of runner.

**A ceiling that the runs left behind is offered lower.** After a green run, when five or more of the runs `--set` would take give a value at most 0.8 of the key, `budget` prints the key, the value and the runs, and conformance makes it an agent finding: lowering a ceiling is a tightening an agent may merge.

**The floor goes, and ADR 0010 is superseded.** `budget --set` applies no floor, and adoption records no `tier0` or `tier1` key: a skeleton's few seconds cannot become the first feature's ceiling when the owner's number steers the loop. Existing tier keys stay, report only, and get the lowering offer.

### 5. What the runner prints, and what only the record keeps

After a tier, `parallel.py` prints its closing line, then at most three more.

1. **What set the wall clock, always**, in place of today's group line: the lanes were full, or one gate or `--serial` group set it while other lanes stood idle, or the order lost seconds against the best schedule. Once the runner holds a gate's lock itself (#291), it adds the seconds gates waited on a lock.
2. **Slower than usual, only when it is:** at 1.3 times or more the median of this entry point's last ten runs that were within, with a machine factor below 1.1, the two times and the gates that grew most against their own medians.
3. **The loop's judgement, as the run's last line**, wherever `--budget` is given: the line of point 6, so a not judged never reads as green from the exit code and last line alone.

**Findings, from the record, by conformance:** the producer of a shared report (ADR 0071) that ran more than once in one `run`; a ceiling the runs left behind (point 4).

**A run of not judged is never a finding and never red.** Each reason is counted on its own, and the line gives the runs in a row with it, from the record (on a forge, the `loop` job's cached record). The route names a run of three: the machine to the owner, and times unread with what is missing, the read access the `loop` job needs. No budget it asks as `loop.wait` (point 7). The owner acts on these, not an agent. Where a forge exists, the pull request's run always judges the loop for the machine, since there `m` is near 1 (point 2). With no forge, only the route's line stands behind a loop never judged locally.

**The record only:** each gate's lane, start offset, CPU time and counts, whether it ran alone, and on the tier's line the cores, the load, `k`, `share`, `m` and the judgement. On a forge the `loop` job writes each job's wait for a runner.

**The red's words carry the way out.** An over names the budget, the time, `E`, line 1, and the gates that grew against their medians. Then it gives the placement, in this order: narrow the gate to what the change reaches (the binding's `## What each gate reads`); or move it to tier 2, offered only where a red deep run holds the next merge (ADR 0077); or make it less work. It never offers raising the budget. When no gate grew, it says the loop was already over before this change, so an agent reports it as a blocker and does not absorb it.

### 6. What each judgement does where it is made

The deep run judges each tier 2 seconds ceiling, and the other places judge the loop. Every line reads `loop: <judgement>: J s of B s`, then what "Printed" gives. Where `F` was left out, the line also gives `J′`, `F`, the cause and its record. Other cells give the exit. "Gates' exit" is 0 when every gate passed. "Warning" is an annotation with the line. "N in a row" is the count of point 5.

| Judgement | Printed | Local `check` | Pull request, `loop` job | Default-branch push, `loop` job | Deep run, a tier 2 seconds ceiling |
| --- | --- | --- | --- | --- | --- |
| within | nothing more | gates' exit | 0 | 0 | gate's exit |
| over | `E` s explained, then the way out (point 5), and that a busy machine is a possible cause where the machine was not read | 1 | 1, red on the pull request | 0 with a warning, reported | 1, a red tier 2 that holds the next merge |
| not judged: the machine | the machine explains `E` s; N in a row | gates' exit | 0 with a warning | 0 with a warning | gate's exit with a warning |
| not judged: no budget | no budget is declared, so the owner is asked `loop.wait`; N in a row | gates' exit | 0 with a warning | 0 with a warning | gate's exit; no ceiling is recorded |
| not judged: times unread | the read that failed; N in a row | does not arise: `check` times itself | 0 with a warning | 0 with a warning | does not arise: `budget.py` times its own child |

A tier 0 or tier 1 seconds ceiling prints its line, marked reported, at every place, and never changes an exit or stops a tier.

**On the default branch the loop is reported, never red.** The pull request judged the same change, and a red push run would hold every merge through ADR 0069 settled item 5. Two merges that are each within and together over make the next pull request red, and its words say that no gate of that change grew.

**Before tier 2 no seconds ceiling is red.** The template's `ci.yml` passes `--over report` to the tier 0 and tier 1 budget steps on every event, so tier 1, which `needs` tier 0, is never skipped for time. This amends ADR 0080 point 5 and ADR 0069: before tier 2, time is judged only as the owner's loop budget.

**In tier 2, for a red on time alone,** the route names the gate, its time against its ceiling, that the machine explains none of it, and that every gate passed, then point 5's way out with "raising the ceiling is an owner's record" in place of moving it later.

**A time red never skips another tier:** the loop is judged after every gate has run, tier 2 runs after a push whatever the judgement, and nothing runs after tier 2.

### 7. What an adopted project sees on upgrade

**Nothing green turns red.** The upgrade writes no `[ceilings.loop]` and no work key. Through conformance `Edit`s (ADR 0037) it rewrites the budget steps of a `ci.yml` in the previous template's form to `--over report` and adds the `loop` job and the `RATCHET_RUN` line, as it adds `--tier1` to `merge.yml` (ADR 0069), and the same way it adds `--budget loop` to a justfile's `check` and `check-fast` recipes in the previous template's form. A hand-edited file owes the edits and keeps today's behaviour until they are paid. Existing seconds keys stay, tier keys included.

**The owner is asked once.** `questions.toml` gains `loop.wait`, asked by `adopt` and `next`, of kind `product`: "When you ask for a check on a small change, how long will you wait for the answer?" The route asks it while `[ceilings.loop]` is missing, with the median and range of the last ten `check` runs, and says that a number under the median makes every check red until the loop is narrowed. An agent writes the answer as the key on a branch, which the merge judge leaves to the owner. Until then every run prints not judged, no budget, with the question's name. No conformance finding reads the missing key, so nothing turns red on an upgrade or on a pin moved by hand.

**A new adoption asks `loop.wait` before the first `check` exists,** so the first loop is judged. When that first loop is already over, it is over: the adoption says so with the way out, and nothing is recorded as a ceiling from a run that was over.

### 8. How an agent meets it

The red's words carry the way out, so no page tells an agent to notice slowness. One sentence changes, in `skills/method/SKILL.md`'s list of what to do on a red, replacing "A tier runs over its ceiling": "**The loop is over its budget:** narrow the gate the red names to what the change reaches, or move it to tier 2 where the red offers that; never raise the budget, which is the owner's, and when the red says the loop was over before this change, report it as a blocker." `tiers.md` takes the rules of points 1 to 6, in its own words.

## What this record would have said

Replayed with an assumed budget of 300 s for both places.

| Case | Today | Under this record |
| --- | --- | --- |
| This repository, 40 `ci` runs on 2026-11-21, wall 181 to 756 s | no time red | 16 runs over 300 s by wall clock. The five whose jobs were read had work paths of 116 to 134 s, within; the other eleven are inferred within. |
| #125, tier 0 at 56.7 s on the pull request and 75.2 s on the push, against 60 | two red pushes, tier 1 skipped, merges held | tier 0 reports. No red, tier 1 runs. |
| #308, five pull requests at 650 to 830 s locally and about 14 minutes in CI | green against 900 and 1,800 | over on each, locally and on the pull request. Excusing 650 s down to 300 needs `m` of 2.2 or more. The drift is red on its first pull request. |
| #305, tests per mutant from 19.03 to 10.51 | a seconds ceiling the runner moves by 1.68 | a work key of about 11.7, red at once on any runner when the order loses its effect. |

Limits: the five `ci` runs ran on different commits, the budget is assumed, and #308's machine factor is inferred.

## Refutation criterion

This is wrong, and the rule of point 2 or 4 is reopened, if any of these is seen, where "run again" means on the same kind of machine with others below a tenth of the cores:

- A run not judged for the machine is over when run again.
- More than one in twenty runs judged over is within when run again unchanged.
- Five runs in a row on the default branch are not judged for the machine.
- A loop's median grows by half over ten merges with no over and no slower-than-usual line.
- A work key is red on a run whose gate's inputs did not change.
- A tier 2 seconds ceiling set from five or more runs is red within its next twenty runs on unchanged work.
- On an idle machine the probe's wall time is more than 1.1 times its CPU time.

## Considered options

- **Keep measured tier ceilings, set wider.** Rejected: #308's 900 s was wide already.
- **Red only on the default branch.** Rejected by the owner: the one who slowed the loop learns only after the merge.
- **Judge the forge run's wall clock.** Rejected: it follows the queue for runners (point 1).
- **Excuse by the load average.** Rejected for point 2's measurement.
- **A reference workload against a stored baseline.** Rejected for now: CI has no history to hold it. It alone sees a slower processor, and returns if the refutation bullet on misses fires on hosted runners.
- **A second budget for the pull request.** Not now: in #308 the two places took about the same. A project whose places differ in kind gets a later record.
- **Let a not judged on the pull request hold the merge.** Rejected: it makes a busy machine a prompt, and the push run and the next pull request judge again.

## Consequences

- An agent's `check` can be red with every gate green. Its words say which gate to narrow or move, and whether the change caused it.
- The template's `ci` gains one short last job, which the owner also waits on.
- A loop within about 1.2 of its budget locally, within the runner's spread on a forge, or on a machine that cannot be read, can go red on a slow day. A rerun settles it.
- Tier 0 and tier 1 seconds keys guard nothing red. Their regressions show in the loop's judgement, line 2 of point 5 and the work keys.
- An upgrade's pull request is judged without its forced proof's seconds. Until #307 ships, any other fallback to every gate can take the loop over its budget.

## What the owner may reverse

Each of these is a default:

- The seconds of a proof the method forces are left out of the loop's judgement.
- One number covers the local check and the pull request.
- Tier 0 and tier 1 seconds ceilings stop blocking everywhere.

## For the builders

Each pull request stays under the change-size ceiling, starts from a red test, and ends in something a user can see. Scripts are under `scripts/`, tests under `hooks/tests/`.

- **1a. The run records the machine.** `machine.py` (new), `runrecord.py`, `parallel.py`, `budget.py`. Seen: the tier's line names `k`, `share` and `m`. Red first: `test_machine.py` (new: `m` is 1 on a free machine and under self-made contention, and the others' factor under theirs), `test_runrecord.py`, `test_parallel.py` and `test_scripts.py` (the reaper keeps a gate's exit 3).
- **1b. A forced proof says so.** `project_bite.py`. Seen: an upgrade's proof lines carry `forced`. Red first: `test_project_bite.py` (a moved pin with a binding edit outside the rows is forced; that edit alone is not).
- **2. The loop's budget, locally.** `loop_budget.py` (new), `parallel.py`, `conformance.py` (the justfile edit the upgrade offers), `route.py`, the template's justfile, `questions.toml`, the next and adopt skills, and the method pages this record amends. Seen: `just check` over the budget exits 1 and names the gate to narrow. Red first: `test_loop_budget.py` (new: each cell of point 6's table, and `F` only from this run and tree), `test_parallel.py` (the judgement is last), `test_owner_questions.py`, `test_route.py`, `test_upgrade_writes.py`.
- **3. The loop's budget, on the pull request.** The template's `ci.yml` (the `loop` job and the `RATCHET_RUN` line), `forge_runs.py`, `loop_budget.py`, `conformance.py`, `docs/gates.md`. Seen: a pull request whose work path is over the budget has a red `loop` job naming the gate. Red first: `test_forge_runs.py`, `test_templates.py`, `test_conformance.py`, `test_upgrade_writes.py`.
- **4. What set the wall clock.** `parallel.py`, `conformance.py`. Seen: every tier prints what set its time. Red first: `test_parallel.py`, `test_conformance.py`.
- **5. Ceilings from many runs.** `budget.py`, the template's `ci.yml` and `deep.yml`, `conformance.py` (and `check_bite_all_ceiling`'s words that `--set` records the ceiling from one measured run), the adopt skill, `tiers.md`, ADR 0010, `docs/gates.md`. Seen: `just budget bite-all --set` with four runs says it needs five, and a push with tier 0 over its key leaves tier 1 running. Red first: `test_scripts.py`, `test_templates.py`, `test_conformance.py`, `test_upgrade_writes.py`.
- **6. Counts of work.** `loop_budget.py`, `parallel.py`, `budget.py`, `mutation_survivors.py`, `tiers.md`. Seen: tests per mutant past their key are red on any runner. Red first: `test_scripts.py`, `test_parallel.py`, `test_mutation_survivors.py`, `test_lowered_records.py` (raising a work key is a loosening).

They are built one at a time, in the order above. 1a waits for the open work on `parallel.py`. 1b waits for #307's work on `project_bite.py`, since both touch the cause logic. 2 waits for 1a, for 1b, whose `forced` it reads, and for the open work on `conformance.py`. Each later one waits for the one before it: 3 for `loop_budget.py`, 4 and 5 for `conformance.py`, 6 for the `--set` of `budget.py`. Pull request 2 is the first that would have made #308 red. 1a and 1b go before it, so the first red a user sees can already say not judged when the machine explains it, and leave out a forced proof.

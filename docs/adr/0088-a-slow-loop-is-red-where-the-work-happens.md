---
status: proposed
---
# A slow loop is red where the work happens

Settles #308. Takes up #299 and #125, and the timing parts of #296 and #305. Supersedes ADR 0010. Amends ADR 0069 (the consequence of settled item 5 that a red from runner variance holds merges), ADR 0080 (point 5, the bullet on ceilings), and `skills/method/tiers.md` ("Measured, not hoped"), `skills/method/ratchet.md` (section 6) and the adopt skill's speed step. Builds on ADR 0077: a red deep run holds the next merge, which makes moving a check later safe.

The method says that gates which pile up get skipped, that a change runs only what it can affect, and that a question needing more than the pull-request budget belongs in tier 2 (`tiers.md`). Nothing makes a breach of that red. On 0.13.3:

- **The local `check` never judges time.** `parallel.py` writes every gate's record with a null ceiling.
- **A pull request only reports.** The template's `ci.yml` passes `--over report` on a pull request, and each tier 1 job is held to its own key or the `tier1` key, so the tier's wall clock is judged nowhere.
- **The tier ceilings have a floor that never falls.** `budget --set` records `max(ceil(1.5 × one run), floor)`, with a floor of 60 s for `tier0` and 900 s for `tier1` (ADR 0010). A `check` of 234 s held to 900 can almost quadruple and stay green (#299), and nothing offers a lower ceiling.
- **One run with 1.5 times headroom does not cover a hosted runner.** Four deep runs of one mutation gate on identical work spread 1.68 (#299), and this repository's test shards up to 2.15.
- **A tier red only on time skips the next tier on the default branch.** Tier 1 `needs` tier 0, and on a push `--over block` makes a slow tier 0 red. In #125 two pushes went red at 75.2 s against 60 with every gate green, tier 1 did not run, and ADR 0069 settled item 5 held every merge.
- **The record cannot tell a slow loop from a slow machine.** Its lines hold each gate's seconds, the tier's wall clock, and each fault's `lane` and `verdict` (#333), but no start offset, CPU time, wait on a lock or load. The proof prints the load and core count, and why it proved every gate (`bite: fallback: ...`), and records neither.

So in #308 every pull request of a day spent 650 to 830 s in local `check` and about 14 minutes in CI, against ceilings of 900 and 1,800, and the owner found it, five days after first raising it. This record is the class fix: the owner sets the loop's budget, the work that breaks it is red where it is done, and the red says the way out.

## Decision

### 1. One model: three kinds of limit

Every limit on time or work is a row of one table. `judge` returns a judgement from a limit and a reading, and a table maps the kind of limit, the place and the judgement to what happens. No caller branches on the place.

| Limit | Key in `quality/thresholds.toml` | Who sets it | Judged by | Before tier 2 | In tier 2 |
| --- | --- | --- | --- | --- | --- |
| The loop's budget | `[ceilings.loop]` `seconds` | the owner, asked once (point 7) | `parallel.py --budget loop` at the end of `check`, and of `check-fast` when it ran every gate; the `loop` job of `ci` | over is red on the branch and on the pull request; on the default branch it is reported | not judged |
| A seconds ceiling | `[ceilings.seconds]` `<key>` | `budget --set` from five or more runs (point 4) | `budget.py` | reported, never red | over is red and holds the next merge (ADR 0077) |
| A work ceiling | `[ceilings.work.<gate>]` `<count>` | `budget --set` from five or more runs | the runner that read the gate's count line (point 3) | red everywhere | red everywhere |

```text
class Reason(Enum):
    MACHINE = "the machine explains the time"
    FORCED = "the method required the whole proof"
    NO_BUDGET = "no budget is declared"
    UNREAD = "the run's times could not be read"

Judgement = Within | Over | NotJudged(reason: Reason)
Reading = (seconds: float | None, excused: float, machine_read: bool, forced: str | None)

def judge(budget: float | None, reading: Reading) -> Judgement: ...
EFFECT: Mapping[tuple[Kind, Place, Judgement], Effect]
```

The set is closed: within, over, or not judged for exactly one reason. `Place` is `local`, `pull-request`, `default-branch` or `deep`, and `Effect` holds the exit code, the annotation and the words. A work count has no excuse, so a work ceiling is within or over (point 3).

**The loop is what the owner waits on:** `check` locally, the entry point an agent runs after each edit, and the pull request's `ci` run on a forge. One number covers both, the owner's answer to how long they will wait for the answer on a change. Every change is judged on every run, whatever its size, except one the method makes prove every gate (point 2).

**On a forge, the judged time is the run's work path.** The `loop` job runs last (`needs` every other job, `if: always()`). With the run's own token, given read access to the run's jobs in the job's own `permissions:`, it reads the run's jobs in one request and the timing artefacts every job already uploads. The jobs listing does not give the `needs` graph, so the job reads it from the workflow file with the standard library only. Its reading is the longest path through that graph, each job counted from start to end, set-up included. A wait for a runner after a job's `needs` ended is not judged, and is printed when over a tenth of the run. In five runs of this repository's `ci` the wall clock was 182 to 691 s, the work path 116 to 134 s, and the wait for runners 56 to 575 s. When a read fails, the loop is not judged because the run's times could not be read: never green as if judged, and never red on a push to the default branch, where ADR 0069 settled item 5 would hold every merge.

### 2. Not judged has one named reason, and the machine is one of them

`judge` reads the budget `B`, the judged time `J`, the part `E` of `J` the machine explains, and what the proof of the same run recorded. It decides in this order:

1. No `[ceilings.loop]` key: **not judged**, no budget is declared.
2. `J` could not be read: **not judged**, the run's times could not be read.
3. `J ≤ B`: **within**.
4. The proof recorded a forced cause: **not judged**, the method required the whole proof.
5. `J − E > B`: **over**, even with every excused second taken away.
6. Otherwise: **not judged**, the machine explains the time.

Not judged is never green and never red: its line names the reason (point 6), and the record keeps it.

`E` is `J × (1 − 1/m)`, where `m` is the machine factor, read in the same run. A factor below 1.1, the noise floor, counts as 1.

**The whole proof the method requires** is a reason only when the proof says it proved every gate for a cause that a decision record names as forced: today only the plugin pin (ADR 0069, settled item 4). The proof's record says nothing of why now, so each fault line of such a run gains one field, `forced`, naming the cause (`plugin-pin`) and its record. The runner reads it from the proof's lines of the same run, never from the diff. Every other fallback to every gate stays judged: a binding edit outside its rows, a tool config no bullet names, a recipe file and the rest. Until #307 narrows or defers those, a loop they take past the budget is over, and the red's way out names placement. A builder does not exempt them.

**The machine factor.** The runner, `parallel.py` locally and `budget.py` in each CI job, times a chain of hashes calibrated to about 20 ms of CPU every 2 s, reading each sample's wall time and CPU time. At the start and the end it reads the busy CPU time of the processors it can use, including time stolen by a hypervisor (`/proc/stat` on Linux), and the CPU time of its own work.

- `k`, the contention a ready task met, is the median over the samples of wall time over CPU time. It needs no baseline: on a machine with a free core, a sample's wall time equals its CPU time.
- `share` is `(own + others) / own`, where `own` is the CPU time of the runner and its descendants, and `others` is the busy time less `own`.
- `m = min(k, share)`. Contention the check makes for itself (lanes times a suite's workers past the cores, #291) raises `k` but leaves `share` near 1, so it is not excused. Contention from other work raises both.

**The run's own work, detached or not, is never excused.** `getrusage` counts only descendants that were waited for, and a gate that detaches work into a new session re-parents it away from the run. So on Linux the runner makes itself its descendants' reaper (`PR_SET_CHILD_SUBREAPER`): a detached process is re-parented to it, and its CPU time joins the run's when it is reaped, or is read at the end while it still runs. On 2 pinned processors at a 1-minute load of 19.0 to 21.8 on 16 cores, three gates that detach their work counted 15.5 and 15.8 s of own CPU time with the reaper and 0.4 and 0.9 s without: over with it, not judged without. Work handed to a process outside the run's descent, such as a server started before it, cannot be told from other tenants' work. Its excuse is bounded by `k`: where detached work counted as others', `m` was `k`, 1.0 to 2.07, excusing up to half of `J`.

**The factor excuses only readings of this run's processors.** When the CPU affinity covers fewer processors than the machine reports, the control group sets a CPU quota, or any reading is missing or unreadable, `m` is 1. That one rule covers a container reading its host's counters, an operating system without them, and a failed read. The loop is judged on its wall clock alone, and an over adds that the machine could not be read, so a busy machine is a possible cause. It stays red. A busy day there can give a false red, and a rerun on a quiet machine settles it. On 2 pinned processors, twice the work read through the whole host's line was not judged in 2 of 2 runs, and over through the pinned processors' own lines.

At a load of 19 on 16 cores, five runs of a fixed workload ran within 1.05 of their CPU time, so a rule on the load average would excuse runs the machine did not slow. The load stays in the record and decides nothing.

**Its error, stated.**

- It does not see a slowdown that raises CPU time itself: a busy sibling hyperthread, a lower clock, or a slower processor model. The same fixed work took 1.49 to 1.82 s of CPU in those five runs, so on a shared machine up to about 1.2 of slowdown is not excused.
- On a hosted runner the job has its virtual machine to itself, so `m` is near 1 and a slower host is not seen either. Over five runs this repository's test shards spread 1.56 at the median and up to 2.15, and its work path 1.16. Deep runs spread 1.68 (#299). A loop within that spread of its budget can go red on a slow runner.
- It does not see contention for a disk, a network or memory bandwidth.
- It excuses a gate that waits on input and output while others use the CPU: twice the waits beside two loaders was not judged in 2 of 2 runs.
- The probe calibrated to 20 ms took a median of 43 ms of CPU under load, about 2 percent of one core. `k` on identical cases ranged from 1.24 to 1.77.
- Work outside the run's descent can make a slow run not judged, never within, and the pull request's run then judges the change.

### 3. A count of work is a ceiling that does not move with the runner

**A gate states a count in one line of its output:** `ratchet-work: <count> <number>`, for example `ratchet-work: builds-per-check 1`. The recipe prints it from whatever its tool reports, and the plugin names no tool. `mutation_survivors.py` prints `tests-per-mutant` itself, from the report's tests completed per mutant. `budget.py` reads a gate's output through a pipe and passes it on unchanged, and each count goes into the gate's record line as `work`.

**It is held like any ceiling.** The key is `[ceilings.work.<gate>]` `<count>`, under `ceilings.`, so `lowered.py` already reads raising one as a loosening that needs a `Relaxes:` line and the owner. A count over its key is red on every run, since it is the same on every runner: tests per mutant were 19.03 with a bad test order and 10.51 with a good one, on a hosted runner and locally alike (#305). A declared key whose gate printed no count, or a count of zero, is red, and its line says the count was missing: an absent count never passes.

**A count qualifies when it does not grow with the code:** a count per unit of work, not a total. Suite starts per check, builds per check and the runs of one producer per tier qualify, and so do tests per mutant while the order runs a mutant's own tests first. The number of tests does not: every new test would need an owner's record. A count that rightly rises is raised the way any threshold is loosened, by the owner's merge.

**When it replaces a seconds ceiling.** Before tier 2 a seconds ceiling only reports, so the count is a gate's only red guard there. In tier 2 both stand, since the deep run's length sets how fast the queue merges (ADR 0077). Where a gate has a work key, `budget` offers removing its seconds key before tier 2, as an owner's `Relaxes:` line naming this record.

### 4. A ceiling follows the measurement down, and the floor goes

**A first ceiling comes from five runs or more.** `budget --set <key>` reads the record, not one run it times: the key's green runs that were within their ceiling or had none, up to the last ten. It records `ceil(max × 1.25)` for seconds, or `max × 1.1` rounded up for a count, and with fewer than five runs records nothing and says how many it has. The headroom is over the slowest run seen: in #125 tier 0 ranged 37.6 to 75.7 s over 45 runs, which 1.5 times one 40 s run cannot hold.

**The record is the judging runner's.** The CI templates add `.ratchet/timing.jsonl` to each job's existing cache, keyed per job with the commit and restored by prefix, so a job's `budget.py` reads that gate's history on that kind of runner.

**A ceiling that the runs left behind is offered lower.** After a green run, when the record holds five or more of the runs `--set` would take and `ceil(max of the last ten × 1.25)` is at most 0.8 of the key, `budget` prints the key, the value and the runs it came from, and conformance turns it into an agent finding: lowering a ceiling is a tightening an agent may merge.

**The floor goes, and ADR 0010 is superseded.** `budget --set` applies no floor, and adoption records no `tier0` or `tier1` key: a skeleton's few seconds cannot become the first feature's ceiling when the owner's number steers the loop. Existing tier keys stay, report only, and get the lowering offer.

### 5. What the runner prints, and what only the record keeps

After a tier, `parallel.py` prints its closing line, then at most three more.

1. **What set the wall clock, always**, in place of today's group line: the lanes were full, or one gate or `--serial` group set it while other lanes stood idle, or the order lost seconds against the best schedule (the longest gate or group, or the summed gate time over the lanes, plus the `--alone` gates). Once the runner holds a gate's lock itself (#291), it adds the seconds gates waited on a lock.
2. **Slower than usual, only when it is:** at 1.3 times or more the median of this entry point's last ten runs that were within, with a machine factor below 1.1, the two times and the gates that grew most against their own medians.
3. **The loop's judgement**, where `--budget` names a key: the line of point 6.

**Findings, from the record, by conformance:** the producer of a shared report (ADR 0071) that ran more than once in one `run`; a ceiling the runs left behind (point 4).

**A run of not judged is never a finding and never red.** The runner's line carries the count, and the route names it to the owner once it reaches three. The owner acts on it, not an agent. Where a forge exists, the pull request's run is where a loop is always judged for the machine, since there `m` is near 1 (point 2). With no forge, only the route's line stands behind a loop that is never judged locally.

**The record only:** each gate's lane, start offset, CPU time and counts, whether it ran alone, and on the tier's line the cores, the load, `k`, `share`, `m` and the judgement. On a forge the `loop` job writes each job's wait for a runner.

**The red's words carry the way out.** An over names the budget, the time, `E`, line 1, and the gates that grew against their medians. Then it gives the placement, in this order: narrow the gate to what the change reaches (the binding's `## What each gate reads`); or move it to tier 2, offered only where a red deep run holds the next merge (ADR 0077); or make it less work. It never offers raising the budget. When no gate grew, it says the loop was already over before this change, so an agent reports it as a blocker and does not absorb it.

### 6. What each judgement does where it is made

The deep run judges each tier 2 seconds ceiling, and the other places judge the loop. Every line reads `loop: <judgement>: J s of B s`, then what "Printed" gives. Other cells give the exit, then what runs next. "Gates' exit" is 0 when every gate passed. "Warning" is an annotation with the line.

| Judgement | Printed | Local `check` | Pull request, `loop` job | Default-branch push, `loop` job | Deep run, a tier 2 seconds ceiling |
| --- | --- | --- | --- | --- | --- |
| within | nothing more | gates' exit; no tier follows | 0; no job follows | 0; tier 2 runs | gate's exit; nothing follows |
| over | `E` s explained, then the way out (point 5), and that a busy machine is a possible cause where the machine was not read | 1; no tier follows | 1, red on the pull request; no job follows | 0 with a warning, reported; tier 2 runs | 1, a red tier 2 that holds the next merge; nothing follows |
| not judged: the machine | the machine explains `E` s; N runs in a row on this machine | gates' exit; no tier follows | 0 with a warning; no job follows | 0 with a warning; tier 2 runs | gate's exit with a warning; nothing follows |
| not judged: the whole proof | the method required the whole proof: the cause and the record that forces it | gates' exit; no tier follows | 0 with a warning; no job follows | 0 with a warning; tier 2 runs | does not arise: `bite-all` proves every gate and has its own key |
| not judged: no budget | no budget is declared: the owner has not said how long they will wait | gates' exit; no tier follows | 0 with a warning; no job follows | 0 with a warning; tier 2 runs | gate's exit; no ceiling is recorded; nothing follows |
| not judged: times unread | the run's times could not be read: the read that failed | does not arise: `check` times itself | 0 with a warning; no job follows | 0 with a warning; tier 2 runs | does not arise: `budget.py` times its own child |

A tier 0 or tier 1 seconds ceiling prints its line, marked reported, at every place, and never changes an exit or stops a tier.

**On the default branch the loop is reported, never red.** The pull request judged the same change, and a red push run would hold every merge through ADR 0069 settled item 5. Two merges that are each within and together over make the next pull request red, and its words say that no gate of that change grew.

**Before tier 2 no seconds ceiling is red.** The template's `ci.yml` passes `--over report` to the tier 0 and tier 1 budget steps on every event, so tier 1, which `needs` tier 0, is never skipped for time. This amends ADR 0080 point 5: before tier 2, time is judged only as the owner's loop budget. It ends the consequence of ADR 0069 settled item 5 that a red from runner variance holds merges, before tier 2.

**In tier 2, for a red on time alone,** the route names the gate, its time against its ceiling, that the machine explains none of it, and that every gate passed, then point 5's way out with "raising the ceiling is an owner's record" in place of moving it later.

**A time red never skips another tier:** the loop is judged after every gate has run, and nothing runs after tier 2.

### 7. What an adopted project sees on upgrade

**Nothing green turns red.** The upgrade writes no `[ceilings.loop]` and no work key. Through conformance `Edit`s (ADR 0037) it rewrites the budget steps of a `ci.yml` in the previous template's form to `--over report` and adds the `loop` job. A hand-edited file owes the rewrite and keeps today's behaviour until it is paid. Existing seconds keys stay, tier keys included.

**The owner is asked once.** `questions.toml` gains `loop.wait`, asked by `adopt` and `next`, of kind `product`: "When you ask for a check on a small change, how long will you wait for the answer?" The route asks it while conformance's owner finding says the loop has no budget, gives the median and range of the last ten `check` runs, and says that a number under the median makes every check red until the loop is narrowed. An agent writes the answer as the key on a branch, a change the merge judge leaves to the owner. Until then the loop is not judged because no budget is declared. Lines 1 and 2 of point 5 print from the upgrade on.

**A new adoption asks `loop.wait` before the first `check` exists,** so the first loop is judged. When that first loop is already over, it is over: the adoption says so with the way out, and nothing is recorded as a ceiling from a run that was over.

### 8. How an agent meets it

The red's words carry the way out, so no page tells an agent to notice slowness. One sentence changes, in `skills/method/SKILL.md`'s list of what to do on a red, replacing "A tier runs over its ceiling": "**The loop is over its budget:** narrow the gate the red names to what the change reaches, or move it to tier 2 where the red offers that; never raise the budget, which is the owner's, and when the red says the loop was over before this change, report it as a blocker." `tiers.md` takes the rules of points 1 to 6, in its own words.

## What this record would have said

Replayed with an assumed budget of 300 s for both places.

| Case | Today | Under this record |
| --- | --- | --- |
| This repository, 40 `ci` runs on 2026-11-21, wall 181 to 756 s | no time red | 16 runs over 300 s by wall clock. The five whose jobs were read had work paths of 116 to 134 s, within, and the other eleven are inferred within. |
| #125, tier 0 at 56.7 s on the pull request and 75.2 s on the push, against 60 | two red pushes, tier 1 skipped, merges held | tier 0 reports. No red, tier 1 runs. |
| #308, five pull requests at 650 to 830 s locally and about 14 minutes in CI | green against 900 and 1,800 | over on each, locally and on the pull request. Excusing 650 s down to 300 needs `m` of 2.2 or more. The drift is red on its first pull request. |
| #305, tests per mutant from 19.03 to 10.51 | a seconds ceiling that the runner moves by 1.68 | a work key set from runs at 10.1 to 10.6 is about 11.7, red at once on any runner when the order loses its effect. |

Limits: the five `ci` runs ran on different commits, the budget is assumed, and #308's machine factor is inferred from its tickets.

## Refutation criterion

This is wrong, and the rule of point 2 or 4 is reopened, if any of these is seen, where "run again" means on the same kind of machine with others below a tenth of the cores:

- A run not judged for the machine is over when run again. The factor excused work.
- More than one in twenty runs judged over is within when run again unchanged. The factor misses too much of the machine.
- Five runs in a row on the default branch are not judged for the machine. The budget has gone quiet.
- A loop's median grows by half over ten merges with no over and no slower-than-usual line.
- A work key is red on a run whose gate's inputs did not change. The count is not steady enough to be a ceiling.
- A tier 2 seconds ceiling set from five or more runs is red within its next twenty runs on unchanged work.
- On an idle machine the probe's wall time is more than 1.1 times its CPU time.

## Considered options

- **Keep measured tier ceilings, set wider.** Rejected: #308's 900 s was wide already, and steered nothing the owner cares about.
- **Red only on the default branch.** Rejected by the owner: the one who slowed the loop is told only after the merge.
- **Judge the forge run's wall clock.** Rejected: the reading would follow the queue for runners (point 1).
- **Excuse by the load average.** Rejected for the measurement in point 2.
- **A reference workload scaled against a stored baseline.** Rejected for now: CI has no history to hold the baseline. It is the only option that sees a slower processor, and returns if the refutation bullet on misses fires on hosted runners.
- **A second budget for the pull request.** Not now: in #308 the two places took about the same before the fix and after it. A project whose two places differ in kind gets a later record.
- **Let a not judged on the pull request hold the merge for the owner.** Rejected: it makes a busy machine a prompt, and the push run and the next pull request judge again.

## Consequences

- An agent's `check` can now be red with every gate green. Its words say which gate to narrow or move, and whether the change caused it.
- The template's `ci` gains one short last job, which the owner also waits on.
- A loop within about 1.2 of its budget locally, within the runner's spread on a forge, or on a machine that cannot be read, can go red on a slow day. A rerun settles it.
- Tier 0 and tier 1 seconds keys guard nothing red. Their regressions show in the loop's judgement, line 2 of point 5 and the work keys.
- An upgrade's pull request is not judged for time. Until #307 ships, a change that falls back to every gate for any other cause is judged, and is over wherever the whole proof takes the loop past its budget.

## What the owner may reverse

Each of these is a default:

- A change the method requires to prove every gate is not judged against the loop's budget.
- One number covers the local check and the pull request.
- Tier 0 and tier 1 seconds ceilings stop blocking everywhere.

## For the builders

Each pull request stays under the change-size ceiling, starts from a red test, and ends in something a user can see.

1. **The run records what explains its time.** `scripts/machine.py` (new), `scripts/runrecord.py`, `scripts/parallel.py`, `scripts/budget.py`, `scripts/project_bite.py` (`forced`). Seen: the tier's line names `k`, `share` and `m`, and an upgrade's proof lines carry `forced`.
2. **The loop's budget, locally.** `scripts/loop_budget.py` (new), `scripts/parallel.py`, `skills/adopt/templates/justfile`, `skills/method/questions.toml`, `skills/next/SKILL.md`, `skills/adopt/SKILL.md`, `scripts/conformance.py`, `scripts/route.py`, `skills/method/tiers.md`, `skills/method/ratchet.md`, `skills/method/SKILL.md`. Seen: `just check` over the budget exits 1 and names the gate to narrow, and each cell of point 6's table has a test.
3. **The loop's budget, on the pull request.** The template's `ci.yml`, `scripts/forge_runs.py`, `scripts/loop_budget.py`, `scripts/conformance.py`, `docs/gates.md`. Seen: a pull request whose work path is over the budget has a red `loop` job naming the gate.
4. **What set the wall clock.** `scripts/parallel.py`, `scripts/conformance.py`. Seen: every tier prints what set its time.
5. **Ceilings from many runs.** `scripts/budget.py`, the template's `ci.yml` and `deep.yml`, `scripts/conformance.py` (with `check_bite_all_ceiling`'s words that `--set` records the ceiling from one measured run), `skills/adopt/SKILL.md`, `skills/method/tiers.md`, ADR 0010, `docs/gates.md`. Seen: `just budget bite-all --set` with four runs says it needs five, and a push with tier 0 over its key leaves tier 1 running.
6. **Counts of work.** `scripts/loop_budget.py`, `scripts/parallel.py`, `scripts/budget.py`, `scripts/mutation_survivors.py`, `skills/method/tiers.md`. Seen: tests per mutant past their key are red on any runner.

Every pair shares a file except 1 and 3, and 3 needs 2, so they are built one at a time, in the order above. Pull request 1 waits for the open work on `scripts/parallel.py` and `scripts/project_bite.py`, #307's included. Pull request 2 waits for 1 and for the open work on `scripts/parallel.py` and `scripts/conformance.py`. Each later one waits for the one before it: 3 for `scripts/loop_budget.py`, 4 and 5 for `scripts/conformance.py`, 6 for the `--set` of `scripts/budget.py`. Pull request 2 is the first that would have made #308 red, and 1 goes before it so that the first red a user sees can already say not judged when the machine explains it.

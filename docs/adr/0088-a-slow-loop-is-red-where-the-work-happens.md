---
status: proposed
---
# A slow loop is red where the work happens

Settles #308. Takes up #299 and #125, and the parts of #296 and #305 that are about how time is judged. Supersedes ADR 0010. Amends ADR 0069 (the consequence of settled item 5 that a red from runner variance holds merges), ADR 0080 (point 5, the bullet on ceilings), and `skills/method/tiers.md` ("Measured, not hoped"), `skills/method/ratchet.md` (section 6) and the adopt skill's speed step. Builds on ADR 0077: a red deep run holds the next merge, which is what makes moving a check later safe.

The method says that gates which pile up get skipped, that a change runs only what it can affect, and that a question needing more than the pull-request budget belongs in tier 2 (`tiers.md`, the speed section). Nothing makes a breach of that red. On 0.13.3:

- **The local `check` never judges time.** `parallel.py` writes every gate's record with a null ceiling, and the template's `check` recipe calls nothing else.
- **A pull request only reports.** The template's `ci.yml` passes `--over report` on a pull request. The tier 0 job is timed as a whole. Each tier 1 job is held to the smaller of its own key and the `tier1` key, so the tier 1 ceiling caps one job, and the tier's wall clock is judged nowhere, not even on the default branch.
- **The tier ceilings have a floor that never falls.** `budget --set` records `max(ceil(1.5 × one run), floor)`, with a floor of 60 s for `tier0` and 900 s for `tier1` (ADR 0010), and lowers a ceiling only below that. A `check` of 234 s held to 900 can almost quadruple and stay green (#299). Only a hand edit lowers it, and nothing offers one.
- **One run with 1.5 times headroom does not cover a hosted runner.** Four deep runs of one mutation gate on identical work spread 1.68 (#299). Five `ci` runs of this repository on 2026-11-21 spread 1.56 per test shard at the median, and up to 2.15 (different commits, so the work was close but not identical).
- **A tier red only on time skips the next tier on the default branch.** Tier 1 `needs` tier 0, and on a push `--over block` makes a slow tier 0 red. In #125 two pushes went red at 75.2 s against 60 with every gate green, and tier 1 did not run. ADR 0069 settled item 5 then holds every merge on that red.
- **The record cannot tell a slow loop from a slow machine.** A gate's line holds `key`, `seconds`, `ceiling`, `status`, `exit`, `at`, `run` and `tree`. The tier's line adds the wall clock and the gate names. The fault line of #333 holds `lane` and `verdict`. The proof prints the load and core count and records neither. No line holds a start offset, a gate's CPU time or a wait on a lock.

So in #308 every pull request of a day spent 650 to 830 s in local `check` and about 14 minutes in CI, against ceilings of 900 and 1,800, and the owner found it, five days after first raising it. This record is the class fix: the owner sets the loop's budget, the work that breaks it is red where it is done, and the red says the way out.

## Decision

### 1. One model: three kinds of limit

Every limit on time or work is a row of one table. `judge` returns an outcome from a limit and a reading, and a table maps the kind of limit, the place and the outcome to what happens. No caller branches on the place.

| Limit | Key in `quality/thresholds.toml` | Who sets it | Judged by | Before tier 2 | In tier 2 |
| --- | --- | --- | --- | --- | --- |
| The loop's budget | `[ceilings.loop]` `seconds` | the owner, asked once (point 7) | `parallel.py --budget loop` at the end of `check`, and of `check-fast` when it ran every gate; the `loop` job of `ci` | over is red on the branch and on the pull request; on the default branch it is reported | not judged |
| A seconds ceiling | `[ceilings.seconds]` `<key>` | `budget --set` from five or more runs (point 4) | `budget.py` | reported, never red, everywhere | over is red on the default branch and nightly, and holds the next merge (ADR 0077) |
| A work ceiling | `[ceilings.work.<gate>]` `<count>` | `budget --set` from five or more runs | the runner that read the gate's count line (point 3) | red everywhere, like any gate | red everywhere |

```python
class Outcome(Enum):
    WITHIN = "within"
    OVER = "over"
    UNJUDGED = "could not judge"
    UNBOUNDED = "no limit"

@dataclass(frozen=True)
class Reading:
    seconds: float
    excused: float
    why: str

def judge(limit: float | None, reading: Reading) -> Outcome: ...

EFFECT: Mapping[tuple[Kind, Place], Mapping[Outcome, Effect]]
```

`Place` is `local`, `pull-request`, `default-branch` or `deep`. `Effect` holds the exit code, the annotation and the words. A work count has no excuse, so a work ceiling's reading always has `excused = 0` and is never `UNJUDGED`.

**The loop is what the owner waits on.** Locally that is `check`, the entry point an agent runs after each edit. On a forge it is the pull request's `ci` run. One number covers both: the owner's answer to how long they will wait for the answer on a change. Each change is judged on every run, whatever its size, since the change-size ceiling already bounds how much a change can make a check do.

**On a forge, the judged time is the run's work path.** The `loop` job runs last (`needs` every other job, `if: always()`). It reads the run's jobs from the forge in one request with the run's own token, and the timing artefacts every job already uploads. Its reading is the longest path through the `needs` graph, where each job counts from its start to its end, set-up included. Time a job spent waiting for a runner after its `needs` ended is not the change's work and is not judged. The job prints it when it is more than a tenth of the run. In five runs of this repository's `ci` the wall clock was 182 to 691 s, the work path 116 to 134 s, and the wait for runners 56 to 575 s. The two runs over 400 s started alongside other runs, and their latest job started 434 and 644 s after the first.

### 2. "Could not judge" is decided by what the machine took

`judge` reads three numbers: the budget `B`, the judged time `J`, and the part `E` of `J` the machine explains.

- `J ≤ B` is **within**.
- `J − E > B` is **over**: even with every excused second taken away, the loop is past the budget.
- Otherwise it is **could not judge**: the loop is over the budget only by time the machine explains. Its line names the seconds and the cause. It is never green: the closing line says the loop was not judged, the run's record keeps it, and three such runs in a row on one key are a finding (point 5). It is never red.

`E` is `J × (1 − 1/m)`, where `m` is the machine factor, read in the same run. A factor below 1.1 counts as 1, which is the noise floor of the reading.

**The machine factor.** The runner, `parallel.py` locally and `budget.py` in each CI job, times a fixed reference workload in its own process: a chain of hashes that takes about 20 ms of CPU, once before the first gate and then every 2 s while gates run. For each sample it reads both the wall time and its own CPU time. It also reads, at the start and the end, the machine's busy CPU time including time stolen by a hypervisor (`/proc/stat` on Linux) and its own and its children's CPU time (`getrusage`).

- `k`, the contention a ready task met, is the median over the samples of wall time over CPU time. It needs no history and no baseline: on a machine with a free core, a sample's wall time equals its CPU time.
- `share` is `(own + others) / own`, where `own` is the CPU time of the gates and the runner, and `others` is the machine's busy time less `own`.
- `m = min(k, share)`. Contention the check makes for itself (lanes times a suite's workers past the cores, #291) raises `k` but leaves `share` near 1, so it is not excused. Contention from other work raises both.

Measured on a shared 16-core machine at a 1-minute load of 19.0 to 19.3, five runs of a fixed workload in two processes: each run's wall time was within 1.05 of its processes' CPU time, while others held 34 to 44 percent of the cores. A rule on the load average would have excused runs that the machine did not slow. Reading both counters took about 50 µs, over 1,000 readings. The probe is sized to cost about 1 percent of one core.

**Its error, stated.**

- It does not see a slowdown that raises CPU time itself: a busy sibling hyperthread, a lower clock, or a slower processor model on a hosted runner. The same fixed work took 1.49 to 1.82 s of CPU in those five runs, a cause not identified, so on a shared machine up to about 1.2 of slowdown is not excused. Such a run is judged over. A loop that sits within about 1.2 of its budget can go red on a slow day.
- It does not see contention for a disk, a network or memory bandwidth, so such a slowdown is judged over.
- It overstates the excuse for a gate that waits on input and output while others use the CPU, so such a run is judged could not judge more often. The error leans toward not judging, and the next place judges again (point 6).
- Where `/proc/stat` cannot be read (macOS, a container without it), `others` is unknown, `m` is 1, and the line says the machine was not read. A loaded machine there can give a red that a rerun clears.
- An agent can excuse a slow run only by making the machine busier with work that is not the check's. It then gets could not judge, never within, and the pull request's run on a clean runner judges the same change.

**The options weighed.**

| Option | Cost per run | How wrong | How it is gamed | On a hosted runner |
| --- | --- | --- | --- | --- |
| Load and core count at the start and the end | nothing | the load average counts waiting tasks and the run's own work: at a load of 19 on 16 cores a workload ran unslowed | start other work beside the run | sees only the virtual machine; blind to a slow host |
| A fixed reference workload that scales the budget | seconds, and a stored baseline per machine | CI has no history to hold the baseline; read at the start and the end it misses load that comes and goes; its own spread was 1.22 here | none cheap | catches a slower processor if the baseline exists |
| A count of work the gate prints | nothing | exact for what it counts; blind to slower units of work | edit the recipe, a guarded path | the same on every runner |
| The run's summed gate time against its wall clock | nothing | separates schedule from work, not machine from work | none | on a forge it separates the wait for runners exactly |

The chosen rule takes the reference workload, read through the run rather than at its ends, and makes it relative to its own CPU time, so it needs no baseline. The record keeps the load and core count for anyone who reads it later, and they decide nothing. Counts are the guard for a gate's own regression (point 3). The summed time against the wall clock becomes the summary line of point 5, and on a forge it removes the wait for runners from the reading.

### 3. A count of work is a ceiling that does not move with the runner

**A gate states a count in one line of its output:** `ratchet-work: <count> <number>`, for example `ratchet-work: tests-per-mutant 10.51`. The count's name is lower-case words joined by hyphens, and the number is finite and not negative. The recipe prints it from whatever its tool reports. The plugin names no tool. Where a plugin script reads a tool's report, it prints the line itself: `mutation_survivors.py` prints `tests-per-mutant` where the report carries each mutant's tests completed. `parallel.py` already holds each gate's output. `budget.py` reads it through a pipe and passes it on unchanged. Each count goes into the gate's record line as `work`. A line that does not parse is ignored and named once.

**It is held like any ceiling.** The key is `[ceilings.work.<gate>]` `<count>`, under `ceilings.`, so `lowered.py` already reads raising one as a loosening that needs a `Relaxes:` line and the owner. A count over its key is red on every run, since it is the same on every runner: tests per mutant were 19.03 with a bad test order and 10.51 with a good one, on a 2-core hosted runner and on 15 local workers alike (#305). A gate that declares a key and prints no count is red with words that say so.

**A count qualifies when it does not grow with the code.** Tests per mutant, suite starts per check, builds per check and the runs of one producer per tier qualify. The number of tests does not: every new test would need an owner's record.

**When it replaces a seconds ceiling.** Before tier 2 a seconds ceiling only reports (point 6), so the count is the only red guard a gate has there. In tier 2 both stand, since the deep run's length sets how fast the queue merges (ADR 0077, the bound). Where a gate has a work key, `budget` offers removing the gate's seconds key before tier 2, as an owner's `Relaxes:` line naming this record.

### 4. A ceiling follows the measurement down, and the floor goes

**A first ceiling comes from five runs or more.** `budget --set <key>` reads the record, not one run it times. It takes the green runs of the key that were within their ceiling or had none, up to the last ten, and records `ceil(max × 1.25)` for seconds, or `max × 1.1` rounded up to the count's precision for a count. With fewer than five it records nothing and says how many it has. The headroom is over the slowest run seen, not over one run: in #125 tier 0 ranged 37.6 to 75.7 s over 45 runs, which a ceiling at 1.5 times one 40 s run cannot hold and the slowest of ten runs times 1.25 can, unless ten runs happen to miss the slow half.

**The record is the judging runner's.** A local record holds local runs. The CI templates add `.ratchet/timing.jsonl` to each job's existing cache, keyed per job with the commit and restored by prefix, so a job's `budget.py` reads that gate's history on that kind of runner.

**A ceiling that the runs left behind is offered lower.** After a green run, when `ceil(max of the last ten × 1.25)` is at most 0.8 of the key, `budget` prints one line with the key, the value and the runs it came from, and conformance turns it into an agent finding. Lowering a ceiling is a tightening an agent may merge.

**The floor goes, and ADR 0010 is superseded.** `budget --set` applies no floor. Adoption no longer records `tier0` and `tier1` keys: the problem ADR 0010 solved, a skeleton's few seconds becoming the first feature's ceiling, does not arise when the owner's number steers the loop. A project's existing tier keys stay, report only, and get the lowering offer.

### 5. What the runner prints, and what only the record keeps

Fewer, truer lines. After a tier, `parallel.py` prints its closing line as today, then at most three more.

1. **What set the wall clock, always.** One line replaces the group line of today. With `W` the wall clock, `S` the summed gate time, `L` the lanes and `U` the longest gate or `--serial` group, the best schedule is `max(U, S / L)` plus the `--alone` gates in turn. When `W` is within a tenth of `S / L`: "the lanes were full: fewer gate-seconds or more lanes shorten this". When it is within a tenth of `U`: "`<gate or group>` set it at `U` s, and the other lanes stood idle for `L × W − S` s". Otherwise: "the order lost `W − best` s against the best schedule". Once the runner holds a gate's lock itself (#291), the line adds the seconds gates waited on a lock, apart from the work.
2. **Slower than usual, only when it is.** When the run took 1.3 times or more the median of this entry point's last ten runs that were within, and the machine factor is below 1.1: the two times and the gates that grew most against their own medians.
3. **The loop's outcome**, where `--budget` names a key: the outcome, the seconds, the budget, `E` and its cause.

**Findings, from the record, by conformance:** the producer of a shared report (ADR 0071) that ran more than once in one `run`; a key whose last three judgements were could not judge; a ceiling the runs left behind (point 4).

**The record only:** each gate's lane, start offset, CPU time and count lines, whether `budget.py` ran it alone or `parallel.py` ran it beside others, and on the tier's line the cores, the load at the start and the end, `k`, `share`, `m` and the outcome. On a forge the `loop` job writes each job's wait for a runner.

**The red's words carry the way out.** An over names the budget, the time, `E`, the line of item 1, and the gates that grew against their medians. Then it gives the placement, in this order: narrow the gate to what the change reaches (the binding's `## What each gate reads`, the gates over the change); or move it to tier 2, which this red offers only where a red deep run holds the next merge (ADR 0077: on a forge, or with no forge once its record is built); or make it less work. It never offers raising the budget. When no gate grew, it says the loop was already over before this change and is the next piece of work, so an agent reports it as a blocker and does not absorb it.

### 6. The default branch, the deep run and the next tier

**On the default branch the loop is reported, never red.** The pull request judged the same change before the merge. A red push run would hold every merge through ADR 0069 settled item 5 for a time the pull request already reported. Two merges that are each within and together over make the next pull request red, and its words say that no gate of that change grew.

**Before tier 2 no seconds ceiling is red.** The template's `ci.yml` passes `--over report` to the tier 0 and tier 1 budget steps on every event. Tier 0's time therefore never fails its job, and tier 1, which `needs` tier 0, is never skipped for time. This amends ADR 0080 point 5: before tier 2, time is judged only as the owner's loop budget, whose question is the wait itself and which excuses the machine. It ends the consequence of ADR 0069 settled item 5 that a red from runner variance holds merges, for every gate before tier 2.

**In tier 2 a seconds ceiling is judged with the same three outcomes.** Over is a red tier 2 and holds the next merge (ADR 0077). Could not judge is not red. The route says, for a tier 2 red on time alone: the gate, its time against its ceiling, that the machine explains none of it, and that every gate passed. Then it gives the same way out as point 5, with "raising the ceiling is an owner's record" in place of moving it later.

**A time red never skips another tier.** The only time reds left are the loop's, which `check` and the `loop` job judge after every gate has run, and tier 2's, after which nothing runs.

### 7. What an adopted project sees on upgrade

**Nothing green turns red.** The upgrade writes no `[ceilings.loop]` and no work key. It rewrites the budget steps of a `ci.yml` in the previous template's form to `--over report` and adds the `loop` job, through conformance `Edit`s (ADR 0037). Where the file is hand-edited, the rewrite is owed and today's behaviour holds until it is paid. The three outcomes of tier 2 only remove reds. Existing seconds keys stay, tier keys included.

**The owner is asked once.** `questions.toml` gains `loop.wait`, asked by `adopt` and `next`, of kind `product`: "When you ask for a check on a small change, how long will you wait for the answer?" The route asks it while conformance's owner finding says the loop has no budget. It gives the median and the range of the last ten `check` runs in the record, and says that a number under the median makes every check red until the loop is narrowed. An agent writes the owner's answer as the key on a branch. Adding a key to a ratchet file is a change the merge judge leaves to the owner.

**Until the owner answers,** `check` prints "the loop has no budget: the owner has not said how long they will wait" in place of an outcome, and the `loop` job prints the same and passes. Lines 1 and 2 of point 5 print from the upgrade on.

### 8. How an agent meets it

The red is the signal, and its words carry the way out, so no page tells an agent to notice slowness. One sentence changes, in `skills/method/SKILL.md`'s list of what to do on a red, replacing "A tier runs over its ceiling": "**The loop is over its budget:** narrow the gate the red names to what the change reaches, or move it to tier 2 where the red offers that; never raise the budget, which is the owner's, and when the red says the loop was over before this change, report it as a blocker." `tiers.md` takes the rules of points 1 to 6, in its own words.

## What this record would have said

Replayed on real figures. The budget is an assumption, since neither project had one: 300 s for both places.

| Case | Today | Under this record |
| --- | --- | --- |
| This repository, 40 `ci` runs on 2026-11-21, wall 181 to 756 s | no time red; the run's wall clock judged nowhere | 16 runs over 300 s by wall clock. In the five whose jobs were read, the work path was 116 to 134 s: within. The other eleven are inferred within, from the same jobs and code base within hours. No red on correct work. |
| #125, tier 0 at 56.7 s on the pull request and 75.2 s on the push, against 60 | two red pushes, tier 1 skipped, merges held | tier 0 reports. No red, tier 1 runs. |
| #308, five pull requests at 650 to 830 s locally and about 14 minutes in CI | green against 900 and 1,800 | over on each, locally and on the pull request. Excusing 650 s down to 300 needs `m` of 2.2 or more, which needs others to use more than 1.2 times the check's own CPU time while the check was the machine's main load. The drift is red on its first pull request, not found by the owner five days later. |
| #305, tests per mutant from 19.03 to 10.51 | a seconds ceiling that the runner moves by 1.68 | a work key set from runs at 10.1 to 10.6 is about 11.7. A test order that loses its effect is red at once on any runner. |

Limits: the five `ci` runs ran on different commits; the 300 s budget is assumed; #308's machine factor is inferred from its tickets, since its record kept no load. The cases where this record goes wrong are its error list in point 2.

## Refutation criterion

This is wrong, and the rule of point 2 or 4 is reopened, if any of these is seen:

- A run judged could not judge whose tree, run again on the same kind of machine with others below a tenth of the cores, is also over. The machine factor excused work.
- More than one in twenty runs judged over is within when run again unchanged on the same kind of machine with others below a tenth of the cores. The factor misses too much of the machine.
- A key's record holds five could not judge outcomes in a row on the default branch's runs. The budget has gone quiet.
- A loop's median grows by half over ten merges with no over and no slower-than-usual line.
- A work key is red on a run whose gate's inputs did not change. The count is not steady enough to be a ceiling.
- A tier 2 seconds ceiling set from five or more runs is red within its next twenty runs on unchanged work.
- On an idle machine the probe's wall time is more than 1.1 times its CPU time.

## Considered options

- **Keep measured tier ceilings, set wider.** Rejected: a ceiling from the first runs steers nothing the owner cares about, and #308's 900 s was wide already.
- **Red only on the default branch.** Rejected by the owner: the one who slowed the loop is told only after the merge.
- **Judge the forge run's wall clock.** Rejected: in this repository's runs the wait for runners was 56 to 575 s of 182 to 691, so the reading would follow the queue.
- **Excuse by the load average.** Rejected for the measurement in point 2.
- **A reference workload scaled against a stored baseline.** Rejected for now: CI has no history to hold it, and it is the only option that sees a slower processor. It returns if the first refutation bullet about misses fires on hosted runners.
- **A second budget for the pull request.** Not now: in #308 the two places took 650 to 830 s and about 840 s before the fix, and 112 to 146 s and 164 s after. A project whose two places differ in kind gets a later record.
- **Let a could not judge on the pull request hold the merge for the owner.** Rejected: it makes a busy machine a prompt, and the push run and the next pull request judge again.

## Consequences

- An agent's `check` can now be red with every gate green. Its words say which gate to narrow or move, and whether the change caused it.
- The template's `ci` gains one short job after the others, about the length of its set-up, which the owner also waits on.
- A loop that sits within about 1.2 of its budget can go red on a slow day for a cause the factor does not see.
- Tier 0 and tier 1 seconds keys guard nothing red any more. Their regressions show in the loop's outcome, line 2 of point 5, and the work keys.
- On macOS or where `/proc/stat` is missing, the machine is not read, and a loaded machine can give a red that a rerun clears.

## For the builders

Each pull request stays under the adopt template's change-size ceiling, 25 product files and 1,000 product lines, and ends in something a user can see.

1. **The run records the machine.** `scripts/machine.py` (new: the probe, the counters, `k`, `share`, `m`), `scripts/runrecord.py` (the new fields, and `well_formed` for them), `scripts/parallel.py` (samples in the poll loop, each gate's lane, start offset and CPU time from its own wait, the tier's machine fields), `scripts/budget.py` (polls its child, samples, records the machine). Seen: the tier's line in `.ratchet/timing.jsonl` names the cores, the load, `k`, `share` and `m`. Red first: `hooks/tests/test_machine.py` (a fake counter source for a free machine, for self-made contention and for others' contention gives `m` of 1, 1 and the others' factor), `hooks/tests/test_parallel.py` and `hooks/tests/test_runrecord.py` (the fields are written and read back), `hooks/tests/test_scripts.py` (`budget.py` records them).
2. **The loop's budget, locally.** `scripts/loop_budget.py` (new: `Outcome`, `Reading`, `judge`, `EFFECT`), `scripts/parallel.py` (`--budget`, line 3, the red's words), `skills/adopt/templates/justfile` (`check` and `check-fast` pass `--budget loop`), `skills/method/questions.toml` (`loop.wait`), `skills/next/SKILL.md` and `skills/adopt/SKILL.md` (ask it), `scripts/conformance.py` (the owner finding with no key; the finding after three could not judge outcomes), `skills/method/tiers.md`, `skills/method/ratchet.md`, `skills/method/SKILL.md` (point 8's sentence). Seen: `just check` over the budget exits 1 and names the gate to narrow. Red first: `hooks/tests/test_loop_budget.py` (new: every row of the truth table in the report), `hooks/tests/test_parallel.py`, `hooks/tests/test_owner_questions.py`, `hooks/tests/test_conformance.py`.
3. **The loop's budget, on the pull request.** `skills/adopt/templates/.github/workflows/ci.yml` (the `loop` job), `scripts/forge_runs.py` (a run's jobs, their starts, ends and `needs`), `scripts/loop_budget.py` (the work path and the waits), `scripts/conformance.py` (the job's marker and the `Edit` for an older `ci.yml`), `docs/gates.md`. Seen: a pull request whose work path is over the budget has a red `loop` job naming the gate. Red first: `hooks/tests/test_forge_runs.py`, `hooks/tests/test_templates.py`, `hooks/tests/test_conformance.py`, `hooks/tests/test_upgrade_writes.py`.
4. **What set the wall clock.** `scripts/parallel.py` (lines 1 and 2 of point 5, replacing `chains`), `scripts/conformance.py` (a producer run twice). Seen: every tier prints what set its time. Red first: `hooks/tests/test_parallel.py`, `hooks/tests/test_conformance.py`.
5. **Ceilings from many runs, following the measurement down.** `scripts/budget.py` (`--set` from the record, no floor, the lowering offer), `skills/adopt/templates/.github/workflows/ci.yml` and `deep.yml` (`--over report` on tier 0 and tier 1 always, the record in each job's cache), `scripts/conformance.py` (the `Edit`s, the finding for a ceiling left behind), `skills/adopt/SKILL.md` (step 10 records no tier keys), `skills/method/tiers.md`, `docs/adr/0010-a-first-ceiling-is-never-under-the-tier-budget.md` (superseded by this record), `docs/gates.md`. Seen: `just budget bite-all --set` with four runs says it needs five, and a push with tier 0 over its key leaves tier 1 running. Red first: `hooks/tests/test_scripts.py`, `hooks/tests/test_templates.py`, `hooks/tests/test_conformance.py`, `hooks/tests/test_upgrade_writes.py`.
6. **Counts of work.** `scripts/loop_budget.py` (the count line and the work kind), `scripts/parallel.py` and `scripts/budget.py` (read the line), `scripts/budget.py` (`--set` for a count), `scripts/mutation_survivors.py` (prints `tests-per-mutant`), `skills/method/tiers.md`. Seen: a mutation gate whose tests per mutant rise past its key is red on any runner. Red first: `hooks/tests/test_scripts.py`, `hooks/tests/test_parallel.py`, `hooks/tests/test_mutation_survivors.py`, `hooks/tests/test_lowered_records.py` (raising a work key is a loosening).

Pull request 1 comes first. After it, 3 waits for 2, and 6 waits for 2 and 5. Pull request 5 can be built beside 2 and 4. Pull requests 1, 2, 4 and 6 edit `scripts/parallel.py`, and 2, 3, 4 and 5 edit `scripts/conformance.py`, so each lands after the open work on those files and the two that share a file are not built at once. None edits `scripts/project_bite.py`.

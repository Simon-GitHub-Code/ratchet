---
status: proposed
---
# A red or unfinished deep run holds the next merge

Tracked by: #316. Amends ADR 0021 and ADR 0024 on what the merge judge reads of tier 2, and ends the residue ADR 0021 names, that with no forge nothing records tier 2. Keeps ADR 0069, settled item 2: while tier 2 is red on the default branch, the judge leaves every merge to the owner. #307 (a deferred proof) and #238 (a deep run in slices) build on it without amending it.

The method says tier 2 runs after every merge and a red one comes first (`skills/method/tiers.md`). Two paths let merges keep landing on a default branch whose deep run is red or not finished.

- **On a forge.** The adopted `merge.yml` asks the forge for `deep` runs with `status=completed`. `forge_runs.on_the_tip` drops a run whose conclusion is `cancelled` or `skipped` and answers with the run nearest the tip. The template's `deep.yml` sets `cancel-in-progress: true`. So while the tip's run is going, the judge reads an older green one. A merge that starts a newer run cancels the one going, which then never counts. In a busy queue only the nightly run may finish.
- **With no forge.** `merge.py` judges with `merge_ready.Asked(current=True)`, whose tier 2 answer is `none`. After the merge it prints that `check-deep` should be run. Nothing records that run, and nothing reads it before the next merge.

#306 is a live case. A change broke a tier 2 fault's patch and merged green. The next merge cancelled its deep run, and the red landed on an unrelated change 34 minutes later. #307 would let a change whose proof cannot be mapped to a gate defer that proof to the deep run after the merge. That is safe only if an unfinished or red deep run holds the next merge, which #308 asks for too.

The rule has to end that without slowing every adopted project's queue. So every project is held on a red, as today, and the red names every merge it can be blamed on. A deep run that has started finishes. An unfinished run holds the next merge only where the project declares how many merges may land unproven, or, once #307 is built, where a merge deferred its proof.

## Decision

### The data shape

**A deep run is four fields to the judge.** The forge adapter and the no-forge record each produce the same values, so the judge names no forge.

```python
@dataclass(frozen=True)
class DeepRun:
    commit: str
    state: Literal["green", "red", "running"]
    at: str
    name: str


@dataclass(frozen=True)
class Deep:
    runs: tuple[DeepRun, ...] | None
    start: str


@dataclass(frozen=True)
class Landed:
    commit: str
    subject: str


class Hold(NamedTuple):
    code: Literal["tier2", "unproven"]
    text: str


def held(
    chain: Sequence[Landed], deep: Deep, *, gated: bool, bound: int | None
) -> Hold | None: ...


def speaks_for(chain: Sequence[Landed], deep: Deep, run: DeepRun) -> tuple[Landed, ...]: ...
```

- `commit` is the default-branch commit the run checked out. `at` is when the run was created. `name` is what a person opens: the run's address on a forge, the record's line without one.
- `runs` is `None` when the forge's answer or the record could not be read. `start` says how a run on the tip is started, or why the last attempt to start one failed. The judge prints it and never runs it.
- `chain` is the default branch's first-parent line, tip first.
- `gated` says the merge base binds a tier 2 gate. `bound` is the bound the project declares, or `None` when it declares none.

**Every status maps.** A completed run concluded `success` is green, `cancelled` or `skipped` is dropped, and any other conclusion is red. A run in any other status is running, whether the forge names it `queued`, `pending` (held back by the concurrency group), `requested`, `waiting` or `in_progress`, or uses a word the adapter does not know. A running run covers nothing, and a judgement not made is not green, so reading a status as running can never pass a red. Runs from another repository, or from an event other than a push, a schedule or a dispatch on the default branch, are dropped as today.

**What a run covers.** A finished run covers the commit it checked out. A merge commit stands for the change it merged, since the branch's own commits sit on its second parent. A run whose commit is off the chain covers nothing. The **unproven distance** is the index in the chain of the nearest commit a finished run covers: 0 when a finished run checked out the tip, the length of the chain when none did.

**Which run decides a commit.** Where several finished runs checked out one commit, the newest by `at` decides its colour, as `on_the_tip` decides today. A run started again keeps the time it was created, so restarting an older run never moves the answer, in either direction. That is on purpose: an agent that may start runs could otherwise restart an older run until it passes and hide a newer red. To judge a commit again, start a new run. A new attempt of the deciding run is that run's own answer.

**What a run speaks for.** A finished run speaks for every chain commit from its own down to, and not including, the nearest older chain commit whose deciding run is green, or the rest of the chain when there is none. That is the set of merges its result can be blamed on. A run that follows a red run carries that red's fault, so its own red cannot clear the merges under it (row 17).

**The rule.** `held` answers in this order.

1. `runs` is `None`: `tier2`, tier 2 on the default branch is not known. Every project, as today, where an unreadable answer is `red`.
2. Find the nearest chain commit a finished run covers, and the run that decides it. If that run is red: `tier2`. The text names the run, the merges it speaks for, and the running run nearer the tip, if any, whose green would end the hold. Every project, as today.
3. `gated` is false: no hold.
4. `bound` is `None`: no hold.
5. The unproven distance is more than `bound`: `unproven`. The text names the distance, the bound, and what ends the hold: the oldest running run on a chain commit nearer the tip than the nearest finished one, or `start` when there is none.
6. Otherwise: no hold.

`tier2` is the owner's, as today. `unproven` waits: nobody has anything to decide, and the hold ends without anyone acting. A project that declares no bound is held by steps 1 and 2 alone: the holds it has today, read so that a red names the right merges.

**The truth table.** `T0` is the tip, `T1` its first parent, and so on. The merge base binds a tier 2 gate unless the row says otherwise. "None" in the bound column means the project declares none.

| # | Case | Bound | Runs the judge sees | Answer |
| --- | --- | --- | --- | --- |
| 1 | A red, no tier 2 gate bound | none | red on `T0`, green on `T2` | `tier2`: speaks for `T0` and `T1` |
| 2 | No tier 2 gate bound, the tip past a declared bound | 1 | green on `T3` | no hold |
| 3 | No bound declared, runs in flight | none | running on `T0`, green on `T3` | no hold |
| 4 | No bound declared, a red, a newer run in flight | none | running on `T0`, red on `T2`, green on `T4` | `tier2`: speaks for `T2` and `T3`; the run on `T0` would end it |
| 5 | A declared bound, no run has finished | 1 | none | `unproven`: then `start` |
| 6 | A run in flight at the tip | 1 | running on `T0`, green on `T1` | no hold: distance 1 |
| 7 | A run in flight one behind | 1 | running on `T1`, green on `T2` | `unproven`: distance 2, wait for the run on `T1` |
| 8 | A declared bound exceeded, nothing running: the tip's run cancelled by hand | 1 | green on `T2` | `unproven`: distance 2, then `start` |
| 9 | A status the adapter does not name | 1 | that run on `T0`, green on `T2` | read as running: `unproven`, wait for it; with no bound, no hold |
| 10 | A red run older than a green one | none or 1 | green on `T1`, red on `T2` | no hold |
| 11 | A nightly run red after the merge's own run was green | none | green on `T0`, red on `T0` created later | `tier2`: speaks for `T0` and each merge down to the last green |
| 12 | An older run on `T0` started again, red after a newer green | none | green on `T0`, red on `T0` created earlier | no hold: the newer run decides |
| 13 | A merge that changes the deep workflow | any | green on `T0` | no hold here; the `guard` reason sends it to the owner |
| 14 | A force-moved default branch | 1 | green off the chain, running on `T0` | `unproven`: wait for the run on `T0`; with no bound, no hold |
| 15 | The forge cannot be read | any | `runs` is `None` | `tier2`: not known |
| 16 | The fix for a red, after the owner merged it | none | running on `T0`, red on `T1` | `tier2`: names the run on `T0` as what ends it |
| 17 | `X` green; `A` breaks tier 2; `B` lands while `A`'s run is going; both runs go red | none or 1 | red on `T0` (`B`), red on `T1` (`A`), green on `T2` (`X`) | `tier2`: the run on `B` speaks for `B` and `A`; the run on `A` for `A` |
| 18 | A merge that deferred its proof (#307) | none | running on `T0`, green on `T1`, `T0` deferred | `unproven` once #307 adds it; no hold before |

Row 13 needs no rule of its own: a push runs the workflow file of its commit, so the deep run that covers the change is the changed workflow's. Row 16 is how a red ends: the owner merges the fix, its deep run goes green, and the next merge job passes. The judge never tries to tell a fix from another change. Row 17 is why the range runs to the nearest green. With bound 1, `B` lands while `A`'s run is going, the run on `B` holds `A`'s fault, and a reason that named `B` alone would repeat #306.

### The bound

**There is no default bound.** A project that wants one declares `unproven` under `[ceilings.deep]` in `quality/thresholds.toml`, where its other limits live. The adopt template and the upgrade write none. The judge reads it from the merge base, as it reads the binding (ADR 0028), so a change cannot loosen its own judge.

**Declaring, lowering and raising.** Declaring the bound adds a key to a ratchet file, which the judge leaves to the owner (`merge_ready.more_than_tightened`). A key under `ceilings.` only falls (`lowered.py`): lowering it is a tightening an agent may merge, and raising or removing it is a loosening that needs a `Relaxes:` line and the owner. A value that is not a whole number of 0 or more is read as 0, and the hold says so.

**What a bound costs.** A model of a full queue, not a measurement: ten pull requests, a 40-minute deep run, a 12-minute `ci`, one minute to update a branch, two for the merge job to start. The last column is how many merges a red can be blamed on when the runs before it were green; a red after a red speaks for both (row 17).

| Rule | Merges an hour, steady | Most merges one run covers |
| --- | --- | --- |
| Today: a newer merge cancels the running run, no hold | 4.0 | 10 |
| A started run finishes, no bound declared | 4.0 | 3 |
| Bound 0 | 1.4 | 1 |
| Bound 1 | 1.5 | 1 |
| Bound 2 | 2.5 | 2 |
| Bound 3 | 2.9 | 2 |

The 4.0 is the queue's own limit in the model: one pull request at a time takes an update, a `ci` and a merge job. A bound costs rate while the deep run is longer than that loop. With a 10-minute deep run every row takes 4.0 an hour, and one run covers one merge. So a project should declare a bound once its deep run is shorter than one pull request's update and `ci`, which is what #238 is for.

### On a forge

**A deep run that has started finishes.** The template's `deep.yml` sets `cancel-in-progress: false`. Its concurrency group, `deep-${{ github.ref }}`, holds every run on the default branch, from a push, the schedule or a dispatch, so runs go one at a time. The group keeps the running run and at most one pending run, and a newer pending run replaces an older one (ADR 0021 saw this of the `merge` group). The replaced run never started, and the newer run covers its commits.

**Each deep run says which commits it speaks for.** The last job of `deep.yml` needs every gate job, runs under `if: always()` with `actions: read`, and runs `forge_runs.py speaks`. That writes the run's result and the commits `speaks_for` gives into the run's summary. Every older run has finished by then, so the list is final, and a red's `tier2` reason names the same merges.

**The merge job reads every deep run and judges again when one ends.**

- It lists the deep workflow's runs on the default branch in every status.
- `forge_runs.py deep --tip <ref> --answer FILE` writes the `Deep` value as JSON. It prints `start` when the tip is past the nearest finished run and nothing runs on a chain commit nearer the tip than that run. The job then dispatches the deep workflow before it judges. Without `--answer`, the script still prints `green`, `red` or `none`, for a `merge.yml` the upgrade left owed.
- A dispatch that fails, because the workflow is off or the forge refused the call, does not end the job. The job passes the error's first line to the judge with the answer, and a hold's text says the dispatch failed and why. The dispatch after a merge stays, under the same guard.
- `merge_ready.py --deep FILE` takes the answer in place of `--tier2`, refuses both, and judges as today given only `--tier2`.
- `merge.yml` runs on `workflow_run` of both `ci` and `deep`. Its `if:` also takes a `deep` run on the default branch of this repository, whatever its conclusion, so a run cancelled by hand wakes the job too.
- The job still judges every green pull request in each run (ADR 0021). A deep run's end merges the first one that now passes. The next is behind the new tip and waits for its own `ci`, so a job never merges twice against one answer.

With no bound declared, a deep run's end matters when it clears a red: the pull requests the red held merge on the green that ends it, without waiting for another `ci`. With a declared bound, or a deferred merge once #307 is built, it also ends an `unproven` hold.

**The verdict says when it waits.** The judge prints `merge: wait` in place of `merge: owner` when every reason is `unproven` or one the drain already retries (`queue_notes.MECHANICAL`: `behind`, `moved`, `conflict`, `gates`, `refs`), since in a full queue most pull requests that wait are also behind. The comment says nothing in it is the owner's to decide, and what each reason waits for. It exits 1 either way. Any other reason makes it `merge: owner`.

**What the forge cannot be read for.** An unreadable list, or a full page of runs with no finished run on the chain, is `runs = None`: `tier2`, as `red` is today, until the next merge job reads the forge again.

### With no forge

**The merge reads the record and never runs the deep check.** `merge.py` reads the record below into a `Deep` and asks `held` before it judges the branch. With no bound declared it refuses only on a recorded red that no later green covers. A refusal names the command that proves the tip. A deep run inside `merge.py`, inside `drain.py land`, would outlive the 600-second limit on an agent's foreground command and be killed before it wrote its line. The `check-deep` ADR 0031 has `merge.py` run on an adoption's merge result stays, unrecorded.

**The proving command is `merge.py --deep`.** It extends the `merge` entry point as `--records` does, so `just merge --deep` needs no new recipe or binding row. It belongs there because the record lives in the state `merge.py` already keeps, beside its locks, and `merge` is the entry point `/ratchet:next` already lets an agent run. It proves the default branch's tip:

1. It takes the repository's one deep lock, `ratchet-deep.lock` in the common git directory, and writes into it the commit it proves. A second caller waits on the lock, then proves nothing when a line for the tip was appended after it started. So two callers never prove one commit twice.
2. It makes a detached work tree of the commit, copies in the bite registry's `include` paths, which name the ignored folders a gate needs, and runs the registry's `setup` commands, as `project_bite.make_copy` does.
3. It runs the binding's `check-deep` there and appends one line to the record.

An agent runs it in the background and waits on its exit, not on a timer. The `next:` line after every merge names it in place of `check-deep`.

**A proof has three outcomes.** Green is `check-deep` exiting 0. Red is any other exit of `check-deep`. Unrun is everything that stops `check-deep` judging the tree: the work tree cannot be made, an `include` path cannot be copied, a `setup` command fails, the binding names no `check-deep`, or a signal stops the run. An unrun line says why, covers nothing, and is not red. A red is the owner's, as on a forge.

**The record.** `ratchet-deep.jsonl` in the repository's common git directory, where `merge.py` keeps its landing state, so every work tree reads one file. Each line is one JSON object.

```json
{"schema": 1, "commit": "<sha>", "tree": "<the commit's tree>", "status": "green", "exit": 0, "seconds": 812.4, "at": "2026-11-20T09:00:00Z", "entry": "<the check-deep command>", "speaks_for": ["<sha>"], "why": ""}
```

`status` is `green`, `red` or `unrun`. The reader drops an unrun line, and reads a held deep lock as a running run on the commit it names. A line that does not parse makes `runs` `None`, which is `tier2`, since skipping it could skip a red. That reason names the escape: the owner removes the file, and the next `merge --deep` proves the tip again.

**Why an agent cannot forge a green more easily than today.** Only `merge.py --deep` appends, right after it ran `check-deep` in a work tree whose tracked files are the commit's. The record sits beside the refs the path guards protect (ADR 0016), and the adopt template's `local-git` group denies agents edits to it as it denies `.git/refs/**`. An agent that writes it past the hook could as well move the default branch past the hook, which already lands anything, so a forged line buys nothing new.

### When the hold arrives

**A red holds every project, as today.** Steps 1 and 2 ask nothing about the binding or the bound.

**`unproven` needs a tier 2 gate and a reason to wait.** `gated` is true when the merge base's binding has a tier 2 Gates row whose tool is bound, as `project_bite.Row.bound` reads it. `bite-all` is tier 2 and `always`, so every adopted project is gated from its adoption, and waits only where it declares a bound or, once #307 is built, where a merge deferred its proof.

### The upgrade

**An upgraded project keeps its rate.** Today a queue merges as fast as its pull requests' updates, `ci` runs and merge jobs allow, and a deep run holds it only when red. The upgrade writes no bound, so that rate does not change. It rewrites the rest where it can (ADR 0037):

- **`cancel-in-progress: true` in `deep.yml`.** Conformance's `Edit` changes that one line, the way it adds `--tier1` to an older `merge.yml`. Where the line is not in the template's form, it is owed. Owing it is safe: a cancelled run is today's behaviour, with wider blame.
- **`deep.yml` without the last job.** Conformance's `Edit` appends the template's job. Where the file's jobs cannot be read, it is owed, and the `tier2` reason still names the merges.
- **`merge.yml` without the deep answer or the `deep` trigger.** Rewritten where its lines are the previous template's, as for `--tier1`, otherwise owed. Until it is paid, the job passes `--tier2` and judges as today.
- **The `local-git` group without the record's path.** Owed, always, as an owner finding: an upgrade never writes under `guards/`. Until the owner adds it, an agent could write the record without passing the hook.

**What the owner sees on the first drain after upgrading.** The same rate as before. A deep run is no longer cancelled by the next merge. A red's reason and the run's summary name every merge since the last green run. A pull request that waits only for a mechanical reason, such as being behind, reads `merge: wait` where it read `merge: owner`.

### The drain

**On a forge**, when the judge's comment says `merge: wait` and an `unproven` reason names a deep run, the drain waits for that run and the merge run that follows, then reads the pull request's state. It asks the forge for the run's status once a minute, one call each time, in a background command it waits on, and never uses `gh run watch` or a tighter poll: every session on the machine shares the forge's request budget, and a 40-minute run costs about 40 of its 5,000 calls an hour. The step's waits for `ci` and the merge run take the same poll. The deep workflow's `timeout-minutes` and the forge's own limits end a run, so the drain sets no deadline. When `merge: wait` names no run, the drain closes the ticket `waits`. `unproven` is mechanical, so a later drain retries it, and after `RETRIES` tries the ticket is held for the owner.

**With no forge**, when the merge's reason names `merge --deep`, the drain runs it in the background, waits on its exit, and lands the ticket again once.

### What ends each hold

No state holds forever with nothing anyone can do.

| Hold | What ends it | Who acts |
| --- | --- | --- |
| `unproven`, a run is running | That run ends, and the merge job wakes on its end. | nobody |
| `unproven`, nothing runs on a forge | The merge job dispatches a run each time it wakes: each `ci` that finishes, each deep run that ends, the nightly run. | nobody, unless the deep workflow is off or the forge is down; then the owner turns it on |
| `unproven`, or an unrun line, with no forge | `merge --deep` proves the tip. | the agent that merges |
| `tier2`, red | A later green run. | the owner merges the fix |
| `tier2`, not known | The next merge job reads the forge again. | nobody |
| `tier2`, the record does not parse | The owner removes the file, and the next `merge --deep` proves the tip. | the owner |

If no deep run starts, the hold's text says the dispatch failed and why. The owner can also merge by hand, as past any reason: only an agent's merge is held, and the next finished deep run still covers what the owner merged.

### What #307 and #238 add

**#307: a merge that deferred its proof is covered before the next one.** It adds one keyword to `held`, `deferred: Collection[str] = frozenset()`: the chain commits whose merge deferred its bite. It adds one step between steps 3 and 4: an unproven chain commit in `deferred` holds `unproven` as if the bound were 0, naming the deferral, whether or not the project declares a bound. Row 18 is its case. With no forge, the merge refuses with it and names `merge --deep`. #307 decides where `deferred` comes from. Nothing else changes. It is the first change that makes a project that declares no bound wait, and the forge facts below are seen before then.

**#238: a deep run in slices.** The slices are jobs inside one run: one concurrency group keeps one running and one pending run, so slice runs would replace one another. The template's `deep.yml` already runs its gates as matrix jobs with `fail-fast: false`, and slices are more entries in it. To report red as soon as one slice is red, the adapter reads the run's jobs, a change to `forge_runs.py` alone.

## Refutation criterion

This is wrong if any of these is seen:

- A red's `tier2` reason or its run's summary leaves out a merge between the red run's commit and the last green run below it, or names one at or below that green run.
- A deep run that started is cancelled by a merge.
- A project that declares no bound merges more slowly after the upgrade than before, with the same queue and its deep runs green.
- With a declared bound, a pull request reads `merge: wait` across two merge-job events with no deep run running or pending, and the workflow on.
- With no forge, a merge lands while the record holds a red that no later green covers, or an unrun proof is read as red.

## Considered options

- **Keep cancelling a superseded run.** Rejected: a started run's work is thrown away, blame spans the queue, and #316 asks that a started run finish.
- **A default bound of 1.** At a 40-minute deep run it takes every adopted project's full queue from 4.0 to 1.5 merges an hour, on the upgrade. Rejected: finishing runs and blaming back to the last green bring blame to about three merges at no cost in rate.
- **Blame a red on the merges since the nearest finished run.** Wrong whenever a red follows a red (row 17). Rejected.
- **Count a running run as covering its commit.** Rejected: a judgement not made is not green.
- **Order a commit's runs by their latest attempt.** Restarting an older run until it passes could hide a newer red. Rejected.
- **Let the merge job wait inside its run for the deep run to end.** Rejected: a runner held for a deep run's length, failing silently when the job's time runs out.
- **With no forge, have `merge.py` prove the tip before it merges.** Rejected: killed by the 600-second limit before it wrote its line.
- **With no forge, run `check-deep` on the merge result in place of `check`.** Rejected: a red there refuses the change for an earlier merge's fault.
- **With no forge, record every non-zero exit as red.** Rejected: a tree that could not run would become a red only the owner can clear.
- **With no forge, read `.ratchet/timing.jsonl`.** Rejected: unguarded, in the work tree, written by any run of any tree.

## For the builders

### Seen before it is built

These are facts of the forge this record infers and has not seen. Check each on a sandbox repository before pull request 2 merges, as ADR 0021 checked its own: it is the first after which a hold can wait on a deep run's end, in a project that declares a bound, or a red clear when its run ends. Pull request 1 is inert and pull request 3 touches no forge, so neither needs them.

1. A `workflow_run` on `deep` completed starts the default branch's `merge.yml` when the merge job's own token dispatched the deep run.
2. With `cancel-in-progress: false`, a run in progress is not cancelled by a newer one, and a newer pending run replaces an older pending one.
3. A completed run that ended cancelled starts `workflow_run`.
4. The runs list with no status filter returns queued, pending, requested, waiting and in-progress runs, each with its `head_sha`.
5. The forge documents that `workflow_run` cannot chain more than three levels of workflows. The loop here is `ci`, `merge`, a dispatched `deep`, `merge` again, and so on. Seen after five rounds: whether a dispatch made with the workflow's own token starts the count again, so the merge job still wakes on the end of a deep run a merge job dispatched.

If fact 1 or 5 does not hold, the deep workflow wakes the merge job itself. Its last job gains `actions: write` and dispatches `merge.yml` on the default branch. `merge.yml` gains a `workflow_dispatch` trigger, and its `if:`, which today reads only `github.event.workflow_run` and so would skip a dispatched start, gains `github.event_name == 'workflow_dispatch'`. A dispatch is not a `workflow_run`, so it does not count toward the chain.

### The pull requests

The repository records no change-size ceiling of its own. Each pull request stays under the adopt template's, 25 product files and 1,000 product lines, and is green on its own.

**1. The judge and its verdict, inert until a workflow asks.** No template passes `--deep` yet. A pull request that is only behind reads `merge: wait` from this change on, with a forge and without, so every reader of the verdict line moves with it.

- Writes `scripts/unproven.py` (new: the shape, `held`, `speaks_for`, the JSON reader), `scripts/forge_runs.py` (`deep --answer` with every status mapped, and `speaks`), `scripts/merge_ready.py` (`--deep`, `gated` and the bound read from the merge base, the `unproven` reason, `merge: wait` and its comment), and `scripts/queue_notes.py` (`unproven` is mechanical).
- Readers of the verdict line: `scripts/drain.py` (`landed` reads the reasons after `merge: wait` as after `merge: owner`), `scripts/merge.py` (on `merge: wait` its `next:` line names what the wait needs, not the owner's hand merge), `skills/adopt/templates/.github/workflows/merge.yml` (its `merge: <verdict>` line is the judge's first line, not a word chosen from the exit code), `skills/next/SKILL.md` (`merge: wait` is the merge's answer, not a failure to rerun), `skills/feature/SKILL.md` (the one-ticket hand-over names the verdicts), and `skills/method/pipeline.md` (step 7 and the parking rule).
- Red first: `hooks/tests/test_unproven.py` (new), one case per truth-table row against `held`, and the `speaks_for` ranges of rows 1, 4, 11 and 17. `hooks/tests/test_forge_runs.py`: `deep --answer` maps `queued`, `pending`, `requested`, `waiting`, `in_progress` and an unknown word to running, drops cancelled and skipped runs, and keeps the legacy word without `--answer`. `hooks/tests/test_merge_ready.py`, on a real repository: a red names every merge since the last green; a declared bound exceeded gives `merge: wait`; no bound with a run in flight passes; the bound comes from the merge base; a change only behind gives `merge: wait`; `--deep` with `--tier2` is refused. `hooks/tests/test_templates.py`, `hooks/tests/test_merge.py`, `hooks/tests/test_drain.py` and `hooks/tests/test_drain_model.py`: a pull request or branch only behind reads `merge: wait`, with no owner's command, and its ticket is retried, not held. `hooks/tests/test_queue_notes.py`: `unproven` is mechanical.

**2. The forge turns it on.** Nothing waits unless a project declares a bound. Needs the forge facts above.

- Writes `skills/adopt/templates/.github/workflows/deep.yml` (the cancel line, the last job), `skills/adopt/templates/.github/workflows/merge.yml` (runs of every status, `--answer`, the guarded dispatch, `--deep`, the `deep` trigger and its `if:`), `scripts/conformance.py` (the markers and the three `Edit`s), `skills/method/tiers.md` and `skills/method/guards.md` (the rule, the bound, `merge: wait`), `skills/feature/SKILL.md` (the drain's step 4.6 polls once a minute), and `docs/gates.md`.
- Red first: `hooks/tests/test_templates.py`: with a declared bound and a run in flight two merges back, the merge job comments `merge: wait` naming that run; a red's comment names every merge since the last green; a failed dispatch still judges every pull request and the reason says why; a completed `deep` run of any conclusion passes the job's `if:`. `hooks/tests/test_conformance.py`: a project on the 0.13.3 templates yields the three `Edit`s, which give the new template's text. `hooks/tests/test_upgrade_writes.py`: upgrading such a project rewrites all three, writes no `[ceilings.deep]`, and leaves conformance green; a hand-edited `merge.yml` is owed.

**3. The no-forge record.** The proving command, the record and its guard, and the merge that reads it.

- Writes `scripts/unproven.py` (the record's reader and writer), `scripts/merge.py` (`--deep`; the merge reads the record and refuses; the `next:` line names `merge --deep`), `skills/adopt/templates/guards/manifest.toml` (the record under `local-git`), `scripts/conformance.py` (the owner finding when the manifest lacks it), `skills/next/SKILL.md` and `skills/feature/SKILL.md` (run `merge --deep` in the background and wait on its exit), `skills/method/guards.md` and `skills/method/tiers.md` (the paragraph with no forge), and `docs/gates.md` (`merge.py`'s row).
- Red first: `hooks/tests/test_merge.py`: `merge --deep` proves the tip in a detached work tree holding the registry's `include` paths after its `setup`, and appends a green line with `speaks_for`; a red `check-deep` appends a red line; a failing `setup` appends an unrun line a merge does not read as red; a second caller that waited on the lock proves nothing; a recorded red refuses with `tier2` and moves nothing; a declared bound exceeded refuses with `merge: wait` naming `merge --deep`; no bound and no record merges; a line that does not parse refuses and names the file; no merge runs `check-deep` outside an adoption. `hooks/tests/test_conformance.py` for the owner finding.

### Consequences

- A project that declares no bound merges at today's rate, and a red names every merge it can be blamed on. The project in #238, with a deep run of 34 to 51 minutes, would take about one merge per deep run at bound 1 until #238 shortens it.
- #307 can defer a proof to the deep run and stay safe.

## For the owner

The direction is the owner's, from #316. These are the calls in it that no measurement settles.

- **No default bound.** A project is held on a red, as today, and not for a run in flight. Declaring a bound is the owner's change, and costs what the table above says: at bound 1 about one merge per deep run, nothing once the deep run is shorter than a pull request's update and `ci`. The adopt and fill skills may offer one later; that is not built here.
- **Finish rather than cancel**, as #316 asks, at no cost in rate in the model.
- **`merge: wait`** for a pull request that waits only for a run or a mechanical reason.
- **The owner's way past a deep run that never starts.** A hand merge, or turning the workflow back on. No agent path skips a wait.

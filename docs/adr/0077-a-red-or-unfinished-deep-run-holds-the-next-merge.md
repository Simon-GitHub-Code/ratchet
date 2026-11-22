---
status: accepted
---
# A red or unfinished deep run holds the next merge

Tracked by: #316. Amends ADR 0021 and ADR 0024 on what the merge judge reads of tier 2, and ends the residue ADR 0021 names, that with no forge nothing records tier 2. Keeps ADR 0069, settled item 2: while tier 2 is red on the default branch, the judge leaves every merge to the owner. #307 (a deferred proof) and #238 (a deep run in slices) build on it without amending it.

The method says tier 2 runs after every merge and a red one comes first (`skills/method/tiers.md`). Two paths let merges keep landing on a default branch whose deep run is red or not finished.

- **On a forge.** The adopted `merge.yml` asks the forge for `deep` runs with `status=completed`. `forge_runs.on_the_tip` drops a run whose conclusion is `cancelled` or `skipped` and answers with the run nearest the tip. The template's `deep.yml` sets `cancel-in-progress: true`. So while the tip's run is going, the judge reads an older green one. A merge that starts a newer run cancels the one going, which then never counts. In a busy queue only the nightly run may finish.
- **With no forge.** `merge.py` judges with `merge_ready.Asked(current=True)`, whose tier 2 answer is `none`. After the merge it prints that `check-deep` should be run. Nothing records that run, and nothing reads it before the next merge.

#306 is a live case. A change broke a tier 2 fault's patch and merged green. The next merge cancelled its deep run, and the red landed on an unrelated change 34 minutes later. #307 would let a change whose proof cannot be mapped to a gate defer that proof to the deep run after the merge. That is safe only if an unfinished or red deep run holds the next merge, which #308 asks for too.

The rule has to end that without slowing every adopted project's queue. So under this record every project is held on a red, as today, and the red names every merge it can be blamed on. Once pull request 2 below is built, a deep run that has started finishes. An unfinished run holds the next merge only where the project declares how many merges may land unproven, or, once #307 is built, where a merge deferred its proof.

This record is built in three pull requests (For the builders). The first, the judge, is inert: no template passes it a deep answer, so it changes no project's merges. Where a sentence below describes what the second or third builds, it says so.

## Decision

### The data shape

**A deep run is four fields to the judge.** The forge adapter, and the no-forge record that pull request 3 builds, each produce the same values, so the judge names no forge.

```python
@dataclass(frozen=True)
class DeepRun:
    commit: str
    state: Literal["green", "red", "running"]
    at: datetime
    name: str


@dataclass(frozen=True)
class Deep:
    runs: tuple[DeepRun, ...] | None
    start: str


@dataclass(frozen=True)
class Landed:
    commit: str
    subject: str
    merged: frozenset[str] = frozenset()


class Hold(NamedTuple):
    code: Literal["tier2", "unproven"]
    text: str


def held(
    chain: Sequence[Landed], deep: Deep, *, gated: bool, bound: int | None
) -> Hold | None: ...


def speaks_for(chain: Sequence[Landed], deep: Deep, run: DeepRun) -> tuple[Landed, ...]: ...
```

- `commit` is the default-branch commit the run checked out. `at` is the instant the run was created, parsed once where the answer is read. It takes one form, `YYYY-MM-DDTHH:MM:SS`, then up to six digits of a second after a point, then `Z` or an offset `+HH:MM` or `-HH:MM`: the forge's `created_at` and the answer the adapter writes both take it, and pull request 3's record keeps it. Any other form, a week date or a time with no offset among them, makes `runs` `None`. `name` is what a person opens: the run's address on a forge, the record's line without one.
- `runs` is `None` when the forge's answer or the record could not be read. `start` says how a run on the tip is started, or why the last attempt to start one failed. The judge prints it and never runs it. A `name` or a `start` that holds a character that cannot be printed, a line break or a lone surrogate among them, makes `runs` `None`, so no text the judge prints can break its output or add a line to it. The adapter names a run whose address holds one by its number.
- `chain` is the default branch's first-parent line, tip first. `merged` holds the commits off that line, among those the runs checked out, that this chain commit is the oldest to contain: the merge that brought them onto the line. `gatelib.chain` reads both from git, in one call however many runs sit off the line, where the scripts read git and no forge is named. `unproven.py` reads no git.
- `gated` says the merge base binds a tier 2 gate. `bound` is the bound the project declares, or `None` when it declares none.

**Every status maps.** A completed run concluded `success` is green, `cancelled` or `skipped` is dropped, and any other conclusion is red. A run in any other status is running, whether the forge names it `queued`, `pending` (held back by the concurrency group), `requested`, `waiting` or `in_progress`, or uses a word the adapter does not know. A running run covers nothing, and a judgement not made is not green, so reading a status as running can never pass a red. Runs from another repository, or from an event other than a push, a schedule or a dispatch on the default branch, are dropped as today.

**What cannot be read.** An unreadable list, a full page of runs with no finished run on the chain, or a kept run whose time is not an instant, is `runs = None`: `tier2`, as `red` is today, until the next merge job reads the forge again. So is an answer file that is missing, not a regular file, not readable, over 1 MiB, not UTF-8, not JSON, nested past the interpreter's limit, or not the shape above anywhere in it. The reader catches by name what reading and decoding raise: `OSError`, `ValueError` (which holds `UnicodeDecodeError` and the decoder's refusal of a number too long to convert) and `RecursionError`. It reads at most 1 MiB, so a larger file is refused before it is decoded. `forge_runs.py` reads the forge's pages with the same `ValueError` and `RecursionError`, so a page nested past the limit is unreadable on every one of its commands. It reads standard input as bytes and escapes those that are not UTF-8, in every locale as the C locale does today, so a field holding one equals no word the commands compare it with, is no instant, and is no printable name; and a run whose `conclusion` or `event` is a list or an object makes the page unreadable to `deep` and `tier1` without `--answer`. The judge's verdict-record reader does the same, and such a record is `gone or is not a JSON object`.

**What a run covers.** A finished run covers the commit it checked out. A merge commit stands for the change it merged, since the branch's own commits sit on its second parent. A green or running run whose commit is off the chain covers nothing: no chain commit's tree is the one it judged. The **unproven distance** is the index in the chain of the nearest commit a finished run covers: 0 when a finished run checked out the tip, the length of the chain when none did.

**Where a red off the chain sits.** A commit can be off the chain and still in the default branch's history: a branch's own commits under the merge that landed it, or the default branch's earlier tip after a pull merge was pushed to it, which takes the local commits as first parent. `forge_runs.py deep` reads a red run on such a commit as red today, so the rule cannot drop it. It sits on the oldest chain commit that contains it, the merge that brought it onto the line. It decides that commit only when no run checked out the commit itself, since a run on the merge judged the merged tree. From there it speaks for that merge and the merges below it, as any red does.

**Which run decides a commit.** Where several finished runs sit on one commit, the latest `at` decides its colour, compared as an instant, not as text: text orders `09:00:00.5Z` before `09:00:00Z`, and an hour at another offset wherever its digits fall. A run started again keeps the time it was created, so restarting an older run never moves the answer, in either direction. That is on purpose: an agent that may start runs could otherwise restart an older run until it passes and hide a newer red. To judge a commit again, start a new run. A new attempt of the deciding run is that run's own answer. Two runs created at the same instant are a true tie: nothing says which judged later, so the commit is red if either is, since a judgement not made is not green. Between two of one colour the name decides, so the answer does not hang on the order the forge listed them.

**What a run speaks for.** A finished run speaks for every chain commit from its own down to, and not including, the nearest older chain commit whose deciding run is green, or the rest of the chain when there is none. That is the set of merges its result can be blamed on. A run that follows a red run carries that red's fault, so its own red cannot clear the merges under it (row 17).

**The rule.** `held` answers in this order.

1. `runs` is `None`: `tier2`, tier 2 on the default branch is not known. Every project, as today, where an unreadable answer is `red`.
2. Find the nearest chain commit a finished run covers, and the run that decides it. If that run is red: `tier2`. The text names the run, the ten nearest merges it speaks for and how many more there are, and the running run nearer the tip, if any, whose green would end the hold. Every project, as today. The nearest merges are the likeliest cause, and a red with no green below it speaks for the whole chain, thousands of merges past any comment's limit. `speaks_for`, and `forge_runs.py speaks`, which prints it, list them all.
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
| 19 | A red on a commit `T2` brought onto the line, such as the default branch's earlier tip under a pushed pull merge | none | red on that commit, green on `T4` | `tier2`: names the run on its own commit; speaks for `T2` and `T3` |
| 20 | That red, after a green on `T2` itself | none | green on `T2`, red on the commit `T2` brought in, created later | no hold: the run on `T2` judged the merged tree |
| 21 | That red, a green nearer the tip | none | green on `T1`, red on the commit `T2` brought in, created later | no hold |
| 22 | A green on a commit off the line | 1, or none | green on the commit `T2` brought in, running on `T0`; or that green and red on `T3` | `unproven`: distance 6, the green covers nothing; `tier2` on the red on `T3` |

Row 13 needs no rule of its own: a push runs the workflow file of its commit, so the deep run that covers the change is the changed workflow's. Row 16 is how a red ends: the owner merges the fix, its deep run goes green, and the next merge job passes. The judge never tries to tell a fix from another change. Row 17 is why the range runs to the nearest green. With bound 1, `B` lands while `A`'s run is going, the run on `B` holds `A`'s fault, and a reason that named `B` alone would repeat #306. Rows 19 to 22 are a red that today's reader holds on and a chain-only reader would drop: today `forge_runs.py deep` counts every ancestor of the tip by how many commits the tip has that it lacks.

### The bound

**There is no default bound.** A project that wants one declares `unproven` under `[ceilings.deep]` in `quality/thresholds.toml`, where its other limits live. The adopt template and the upgrade write none. The judge reads it from the merge base, as it reads the binding (ADR 0028), so a change cannot loosen its own judge.

**Declaring, lowering and raising.** Declaring the bound adds a key to a ratchet file, which the judge leaves to the owner (`merge_ready.more_than_tightened`). A key under `ceilings.` only falls (`lowered.py`): lowering it is a tightening an agent may merge, and raising or removing it is a loosening that needs a `Relaxes:` line and the owner. A value that is not a whole number of 0 or more is read as 0, and the hold says so. So is the bound of a `quality/thresholds.toml` that does not parse at the merge base.

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

Pull request 2 builds the workflow changes in this section. Pull request 1 builds what they call: `forge_runs.py deep --answer`, `forge_runs.py speaks`, `merge_ready.py --deep` and `merge: wait`, which no template calls yet.

**A deep run that has started will finish.** Pull request 2 sets `cancel-in-progress: false` in the template's `deep.yml`. Its concurrency group, `deep-${{ github.ref }}`, holds every run on the default branch, from a push, the schedule or a dispatch, so runs will go one at a time. The group keeps the running run and at most one pending run, and a newer pending run replaces an older one (ADR 0021 saw this of the `merge` group). The replaced run never started, and the newer run covers its commits.

**Each deep run will say which commits it speaks for.** Pull request 2 adds a last job to `deep.yml`. It needs every gate job, runs under `if: always()` with `actions: read`, and runs `forge_runs.py speaks`, which writes the run's result and the commits `speaks_for` gives into the run's summary. Every older run has finished by then, so the list is final, and a red's `tier2` reason names the same merges.

**The merge job will read every deep run and judge again when one ends.** Pull request 2 changes the template's `merge.yml` so that:

- It lists the deep workflow's runs on the default branch in every status.
- It runs `forge_runs.py deep --tip <ref> --answer FILE`, which writes the `Deep` value as JSON. The script prints `start` when the tip is past the nearest finished run and nothing runs on a chain commit nearer the tip than that run, and the job then dispatches the deep workflow before it judges. Without `--answer`, the script prints `green`, `red` or `none`, as today, for a `merge.yml` the upgrade left owed.
- A dispatch that fails, because the workflow is off or the forge refused the call, does not end the job. The job passes the error's first line to the judge with the answer, and a hold's text says the dispatch failed and why. The dispatch after a merge stays, under the same guard.
- It passes the answer to `merge_ready.py --deep FILE`, which takes it in place of `--tier2`, refuses both, and judges as today given only `--tier2`.
- It runs on `workflow_run` of both `ci` and `deep`. Its `if:` also takes a `deep` run on the default branch of this repository, whatever its conclusion, so a run cancelled by hand wakes the job too.
- It still judges every green pull request in each run (ADR 0021). A deep run's end merges the first one that now passes. The next is behind the new tip and waits for its own `ci`, so a job never merges twice against one answer.

With no bound declared, a deep run's end will matter when it clears a red: the pull requests the red held merge on the green that ends it, without waiting for another `ci`. With a declared bound, or a deferred merge once #307 is built, it also ends an `unproven` hold.

**The verdict says when it waits.** The judge prints `merge: wait` in place of `merge: owner` when at least one reason is `unproven` and every other reason is one the drain already retries (`queue_notes.MECHANICAL`, which holds `unproven` too), since in a full queue most pull requests that wait for a run are also behind. A pull request with no `unproven` reason keeps today's verdict: one that is only behind still reads `merge: owner`. The comment says nothing in it is the owner's to decide, and what each reason waits for. It exits 1 either way. Any other reason beside `unproven` makes it `merge: owner`. Only `--deep` gives an `unproven` reason, so no project reads `merge: wait` until pull request 2 passes it.

### With no forge

Pull request 3 builds this section. Before it, `merge.py` judged with no tier 2 answer.

**The merge reads the record and never runs the deep check.** `merge.py` reads the record below into a `Deep` and asks `held` before it judges the branch. With no bound declared it refuses only on a recorded red that no later green covers. A refusal names the command that proves the tip. A deep run inside `merge.py`, inside `drain.py land`, would outlive the 600-second limit on an agent's foreground command and be killed before it wrote its line. The `check-deep` ADR 0031 has `merge.py` run on an adoption's merge result stays, unrecorded.

**The proving command is `merge.py --deep`.** It extends the `merge` entry point as `--records` does, so `just merge --deep` needs no new recipe or binding row. It belongs there because the record lives in the state `merge.py` already keeps, beside its locks, and `merge` is the entry point `/ratchet:next` already lets an agent run. It proves the default branch's tip:

1. It takes the repository's one deep lock, `ratchet-deep.lock` in the common git directory, and writes into it the commit it proves. A second caller waits on the lock, then proves nothing when a green or red line for the tip was appended after it started; after an unrun line for the tip, it proves the tip again. So two callers never prove one commit twice.
2. It makes a detached work tree of the commit, copies in the bite registry's `include` paths, which name the ignored folders a gate needs, and runs the registry's `setup` commands, as `project_bite.make_copy` does.
3. It runs the binding's `check-deep` there and appends one line to the record.

An agent runs it in the background and waits on its exit, not on a timer. The `next:` line after every merge names it in place of `check-deep`.

**A proof has three outcomes.** Green is `check-deep` exiting 0. Red is any other exit of `check-deep`. Unrun is everything that stops `check-deep` judging the tree: the work tree cannot be made, an `include` path cannot be copied, a `setup` command fails, the binding names no `check-deep`, or a signal stops the run. An unrun line says why, covers nothing, and is not red. A red is the owner's, as on a forge.

**The record is** `ratchet-deep.jsonl` in the repository's common git directory, where `merge.py` keeps its landing state, so every work tree reads one file. Each line is one JSON object.

```json
{"schema": 1, "commit": "<sha>", "tree": "<the commit's tree>", "status": "green", "exit": 0, "seconds": 812.4, "at": "2026-11-20T09:00:00Z", "entry": "<the check-deep command>", "speaks_for": ["<sha>"], "why": ""}
```

`status` is `green`, `red` or `unrun`. The reader drops an unrun line, and reads a held deep lock as a running run on the commit it names. A line that does not parse makes `runs` `None`, which is `tier2`, since skipping it could skip a red. That reason names the escape: the owner removes the file, and the next `merge --deep` proves the tip again.

**Why an agent does not forge a green more easily than before.** Only `merge.py --deep` appends, right after it ran `check-deep` in a work tree whose tracked files are the commit's. The record sits beside the refs the path guards protect (ADR 0016), and the adopt template's `local-git` group denies agents edits to it as it denies `.git/refs/**`. An agent that writes it past the hook could as well move the default branch past the hook, which already lands anything, so a forged line buys nothing new.

### When the hold arrives

**A red holds every project, as today.** Steps 1 and 2 ask nothing about the binding or the bound.

**`unproven` needs a tier 2 gate and a reason to wait.** `gated` is true when the merge base's binding has a tier 2 Gates row whose tool is bound, as `project_bite.Row.bound` reads it, or is the plugin's own. `Row.bound` leaves out a tool the plugin ships, since such a gate needs no fault of the project's, but the deep run still proves it. `bite-all` is tier 2 and its tool is the plugin's, so every adopted project is gated from its adoption. Once pull request 2 passes the answer, a project waits only where it declares a bound or, once #307 is built, where a merge deferred its proof.

### The upgrade

Pull request 2 builds the upgrade's rewrites, and pull request 3 the owner finding for the record's path.

**An upgraded project will keep its rate.** Today a queue merges as fast as its pull requests' updates, `ci` runs and merge jobs allow, and a deep run holds it only when red. The upgrade writes no bound, so that rate does not change. It will rewrite the rest where it can (ADR 0037):

- **`cancel-in-progress: true` in `deep.yml`.** Conformance's `Edit` changes that one line, the way it adds `--tier1` to an older `merge.yml`. Where the line is not in the template's form, it is owed. Owing it is safe: a cancelled run is today's behaviour, with wider blame.
- **`deep.yml` without the last job.** Conformance's `Edit` appends the template's job. Where the file's jobs cannot be read, it is owed, and the `tier2` reason still names the merges.
- **`merge.yml` without the deep answer or the `deep` trigger.** Rewritten where its lines are the previous template's, as for `--tier1`, otherwise owed. Until it is paid, the job passes `--tier2` and judges as today.
- **The `local-git` group without the record's path.** Owed, always, as an owner finding: an upgrade never writes under `guards/`. Until the owner adds it, an agent could write the record without passing the hook.

**What the owner will see on the first drain after upgrading to pull request 2.** The same rate as before. A deep run is no longer cancelled by the next merge. A red's reason and the run's summary name every merge since the last green run. A pull request that is only behind still reads `merge: owner`. `merge: wait` appears only with an `unproven` hold, which a project that declares no bound meets only once #307 is built.

### The drain

Pull request 2 builds the wait on a forge, and pull request 3 the wait with no forge.

**On a forge**, when the judge's comment says `merge: wait` and an `unproven` reason names a deep run, the drain will wait for that run and the merge run that follows, then read the pull request's state. It will ask the forge for the run's status once a minute, one call each time, in a background command it waits on, and never use `gh run watch` or a tighter poll: every session on the machine shares the forge's request budget, and a 40-minute run costs about 40 of its 5,000 calls an hour. The step's waits for `ci` and the merge run take the same poll. The deep workflow's `timeout-minutes` and the forge's own limits end a run, so the drain sets no deadline. When `merge: wait` names no run, the drain closes the ticket `waits`. `unproven` is mechanical, so a later drain retries it, and after `RETRIES` tries the ticket is held for the owner.

**With no forge**, when the merge's reason names `merge --deep`, the drain runs it in the background, waits on its exit, and lands the ticket again once.

### What ends each hold

Once pull requests 2 and 3 are built, no state holds forever with nothing anyone can do.

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
- On any history and any page of runs, `forge_runs.py deep` without `--answer` prints `red` and the judge reading the same page through `--answer` and `--deep` gives no `tier2` hold. That includes a red on a commit off the first-parent line, such as the default branch's earlier tip under a pushed pull merge (rows 19 to 22).
- An answer file or a page of runs, however malformed, makes the judge or `forge_runs.py` raise rather than answer, on any of its commands and in any locale. Two inputs are outside this criterion, and raise on the default branch as they do here: an `--events` file for `ci` whose `event` is a list, and standard input of `merge_ready.py record` that is not UTF-8 where the locale decodes strictly. Later changes fix them.

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
6. A `workflow_dispatch` does not count toward that limit on chained workflow runs. The fallback below rests on it, and no page of the forge's this record has read says so.

**Observed on `Simon-GitHub-Code/ratchet-sandbox-0077` on 2026-11-22:**

1. **False.** `merge` (`45059091156`) dispatched `deep` (`45059100537`) with `GITHUB_TOKEN`; `deep` completed `success`, and GitHub never fired `workflow_run` on `merge.yml` (reconfirmed across five more `GITHUB_TOKEN`-dispatched `deep` runs `45059323482`–`45059402793`).
2. **True.** With `cancel-in-progress: false`, in-progress run `45059532823` finished `success`, older pending run `45059555125` was `cancelled` when newer pending run `45059567105` arrived, and `45059567105` ran afterwards and finished `success`.
3. **True.** User-dispatched cancelled run `45059555125` (`conclusion=cancelled`) fired `workflow_run` `merge` (`45059574438`).
4. **True.** Unfiltered `workflows/deep.yml/runs` returned both `status=in_progress` and `status=pending` runs with full `head_sha`.
5. **False.** Chaining stops at round 1 because of fact 1.
6. **True.** `merge-fallback <-> deep` ran 6 full rounds (11 chained workflows: `45059315135` -> `45059412493`) via `GITHUB_TOKEN` `workflow_dispatch` with zero failures.

Because facts 1 and 5 are false and fact 6 is true, pull request 2 takes the fallback below: `deep.yml`'s last job (`speaks`) carries `actions: write` and dispatches `merge.yml` on the default branch via `workflow_dispatch`, and `merge.yml` adds a `workflow_dispatch` trigger and `github.event_name == 'workflow_dispatch'` in its `if:` alongside `workflow_run` on `[ci, deep]`.

If fact 1 or 5 does not hold, and fact 6 does, the deep workflow wakes the merge job itself. Its last job gains `actions: write` and dispatches `merge.yml` on the default branch. `merge.yml` gains a `workflow_dispatch` trigger, and its `if:`, which today reads only `github.event.workflow_run` and so would skip a dispatched start, gains `github.event_name == 'workflow_dispatch'`. By fact 6, that dispatch does not count toward the chain.

### The pull requests

The repository records no change-size ceiling of its own. Each pull request stays under the adopt template's, 25 product files and 1,000 product lines, and is green on its own.

**1. The judge, inert until a workflow asks.** Built. No template passes `--deep` yet, and only `--deep` can give an `unproven` reason, so no verdict a project gets today changes. Without `--deep` the judge's output and exit code are today's, byte for byte, and `forge_runs.py deep` without `--answer` prints today's word. The one change on today's paths: a document that crashed a reader now gets an answer. A forge answer (runs, pull requests or events) or a verdict record nested past the interpreter's limit is unreadable, as any other unreadable one is. A page with a run whose `conclusion` or `event` is a list or an object, which crashed `deep` and `tier1`, is `red`. A page of runs on standard input with bytes that are not UTF-8, which crashed `forge_runs.py` where the locale decodes strictly, is read in every locale as the C locale reads it today, and gets the answer it gets there. The readers of the verdict line move with the change that first makes `merge: wait` reachable: on a forge pull request 2, with no forge pull request 3.

- Writes `scripts/unproven.py` (new, with no git: the shape, `held`, `speaks_for`, and the JSON answer's reader and writer), `scripts/gatelib.py` (`chain`, the first-parent line with the commits each merge brought onto it, from one read of the history), `scripts/forge_runs.py` (`deep --answer` with every status that is not `completed` read as running and each time read as an instant; `speaks`; a page nested past the limit is unreadable), `scripts/merge_ready.py` (`--deep`, refused beside `--tier2`; `gated` and the bound read from the merge base; the `unproven` reason; `merge: wait` and its comment; a verdict record nested past the limit is no record), and `scripts/queue_notes.py` (`unproven` is mechanical).
- Red first: `hooks/tests/test_unproven.py` (new): one case per truth-table row against `held`; the `speaks_for` ranges of rows 1, 4, 11, 17 and 19; a red's list capped at ten with the rest counted; and, over any chain and any runs on it or off it, that a red deciding the nearest covered commit always holds, that a red speaks down to and never past the nearest older commit whose deciding run is green, that a run not finished never turns a hold into a pass, and that with no bound only steps 1 and 2 hold. `hooks/tests/test_forge_runs.py`: `deep --answer` maps `queued`, `pending`, `requested`, `waiting`, `in_progress`, a missing status and an unknown word to running, drops cancelled and skipped runs, and keeps the legacy word without `--answer`; a field of the wrong type is not trusted and a time that is not an instant is not known; a page nested past the limit is unreadable to every command; under a strict locale and the C, C.UTF-8 and POSIX locales, bytes on standard input that are not UTF-8 change no answer in a field no logic reads, and in a `conclusion`, `event`, `head_sha`, time or run name give the answer for a value the command cannot trust, on every command; `speaks` names each merge back to the last green, from the merge that brought an off-line red; and, over generated histories with merges and pushed pull merges and any page, that a `red` from `forge_runs.py deep` is a hold through `--answer`. `hooks/tests/test_gatelib.py`: `chain` gives each merge the commits it brought, whichever of two merges brought a red and whichever is green; it places every commit where one `rev-list --ancestry-path` per commit places it, over generated histories with merges, pushed pull merges and octopus merges; and it calls git as often for one run off the line as for many. `hooks/tests/test_merge_ready.py`, on a real repository: the judge loads no forge adapter; it answers an answer file of hostile text under a strict locale and the C locale; a red names every merge since the last green; a declared bound exceeded gives `merge: wait` naming the running run or the start; no bound with a run in flight passes; the bound and `gated` come from the merge base; a change only behind gives `merge: owner`, as today, and `merge: wait` beside an `unproven` hold; an `unproven` hold beside a reason of the owner's gives `merge: owner`; `--deep` with `--tier2` is refused; an answer that does not parse is `tier2`, not known, for each way a file can fail to read; two runs on one commit are ordered by instant, and a tie is red; a red on a commit a pull merge left off the line holds, and a green there covers nothing; a verdict record nested past the limit is no record. `hooks/tests/test_queue_notes.py`: `unproven` is mechanical.

**2. The forge turns it on.** Built. Nothing waits unless a project declares a bound. Uses the fallback confirmed in the sandbox facts above.

- Writes `skills/adopt/templates/.github/workflows/deep.yml` (the cancel line, the last job), `skills/adopt/templates/.github/workflows/merge.yml` (runs of every status, `--answer`, the guarded dispatch, `--deep`, the `deep` trigger and its `if:`, and its `merge: <verdict>` line, which becomes the judge's first line, not a word chosen from the exit code), `scripts/conformance.py` (the markers and the three `Edit`s), `skills/method/tiers.md` and `skills/method/guards.md` (the rule, the bound, `merge: wait`), `skills/feature/SKILL.md` (the drain's step 4.6 polls once a minute), and `docs/gates.md`.
- Readers of the verdict line, since a forge is where `merge: wait` is first reached: `scripts/drain.py` (`landed` reads the reasons after `merge: wait` as after `merge: owner`), `skills/next/SKILL.md` (`merge: wait` is the merge's answer, not a failure to rerun), `skills/feature/SKILL.md` (the one-ticket hand-over names the verdicts), `skills/method/pipeline.md` (step 7 and the parking rule), and `skills/conjecture/SKILL.md` (its hand-over relays the verdict line).
- Red first: `hooks/tests/test_templates.py`: with a declared bound and a run in flight two merges back, the merge job comments `merge: wait` naming that run; a red's comment names every merge since the last green; a failed dispatch still judges every pull request and the reason says why; a completed `deep` run of any conclusion passes the job's `if:`. `hooks/tests/test_conformance.py`: a project on the 0.13.3 templates yields the three `Edit`s, which give the new template's text. `hooks/tests/test_upgrade_writes.py`: upgrading such a project rewrites all three, writes no `[ceilings.deep]`, and leaves conformance green; a hand-edited `merge.yml` is owed. `hooks/tests/test_drain.py` and `hooks/tests/test_drain_model.py`: a pull request held by `unproven` alone reads `merge: wait`, with no owner's command, and its ticket is retried, not held.

**3. The no-forge record.** Built. The proving command, the record and its guard, and the merge that reads it.

- Writes `scripts/unproven.py` (the record's reader and writer), `scripts/merge.py` (`--deep`; the merge reads the record and refuses; the `next:` line names `merge --deep`; it reads the verdict line, so on `merge: wait` its `next:` line names what the wait needs, not the owner's hand merge), `skills/adopt/templates/guards/manifest.toml` (the record under `local-git`), `scripts/conformance.py` (the owner finding when the manifest lacks it), `skills/next/SKILL.md` and `skills/feature/SKILL.md` (run `merge --deep` in the background and wait on its exit), `skills/method/guards.md` and `skills/method/tiers.md` (the paragraph with no forge), and `docs/gates.md` (`merge.py`'s row).
- Red first: `hooks/tests/test_merge.py`: `merge --deep` proves the tip in a detached work tree holding the registry's `include` paths after its `setup`, and appends a green line with `speaks_for`; a red `check-deep` appends a red line; a failing `setup` appends an unrun line a merge does not read as red; a second caller that waited on the lock proves nothing; a recorded red refuses with `tier2` and moves nothing; a declared bound exceeded refuses with `merge: wait` naming `merge --deep`; no bound and no record merges; a line that does not parse refuses and names the file; no merge runs `check-deep` outside an adoption; a branch held by `unproven` alone reads `merge: wait`, with no owner's command. `hooks/tests/test_conformance.py` for the owner finding.

### Consequences

- A project that declares no bound merges at today's rate, and, once pull request 2 passes the answer, a red names every merge it can be blamed on. The project in #238, with a deep run of 34 to 51 minutes, would take about one merge per deep run at bound 1 until #238 shortens it.
- #307 can defer a proof to the deep run and stay safe.

## For the owner

The direction is the owner's, from #316. These are the calls in it that no measurement settles.

- **No default bound.** A project is held on a red, as today, and not for a run in flight. Declaring a bound is the owner's change, and costs what the table above says: at bound 1 about one merge per deep run, nothing once the deep run is shorter than a pull request's update and `ci`. The adopt and fill skills may offer one later; that is not built here.
- **Finish rather than cancel**, as #316 asks, at no cost in rate in the model. Pull request 2 builds it.
- **`merge: wait`** for a pull request a deep run holds, alone or beside reasons the drain retries. One that is only behind keeps `merge: owner`.
- **The owner's way past a deep run that never starts.** A hand merge, or turning the workflow back on. No agent path skips a wait.

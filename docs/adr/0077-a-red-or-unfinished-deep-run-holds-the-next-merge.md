---
status: proposed
---
# A red or unfinished deep run holds the next merge

Tracked by: #316. Amends ADR 0021 and ADR 0024 on what the merge judge reads of tier 2, and ends the residue ADR 0021 names, that with no forge nothing records tier 2. Keeps ADR 0069, settled item 2: while tier 2 is red on the default branch, the judge leaves every merge to the owner. #307 (a deferred proof) and #238 (a deep run in slices) build on it without amending it.

The method says tier 2 runs after every merge and a red one comes first (`skills/method/tiers.md`). Two paths let merges keep landing on a default branch whose deep run is red or not finished.

- **On a forge.** The adopted `merge.yml` asks the forge for `deep` runs with `status=completed`. `forge_runs.on_the_tip` drops a run whose conclusion is `cancelled` or `skipped` and answers with the run nearest the tip. The template's `deep.yml` sets `cancel-in-progress: true`. So while the tip's run is going, the judge reads an older green one. A merge that starts a newer run cancels the one going, which then never counts. In a busy queue only the nightly run may finish.
- **With no forge.** `merge.py` judges with `merge_ready.Asked(current=True)`, whose tier 2 answer is `none`. After the merge it prints that `check-deep` should be run. Nothing records that run, and nothing reads it before the next merge.

#306 is a live case. A change broke a tier 2 fault's patch and merged green. The next merge cancelled its deep run, and the red landed on an unrelated change 34 minutes later. #307 would let a change whose proof cannot be mapped to a gate defer that proof to the deep run after the merge. That is safe only if an unfinished or red deep run holds the next merge, which #308 asks for too.

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


def held(chain: Sequence[Landed], deep: Deep, *, bound: int | None) -> Hold | None: ...
```

- `commit` is the default-branch commit the run checked out. `state` is `running` from the moment the run is queued until it finishes. `at` is when the run was created, and orders two runs on one commit. `name` is what a person opens: the run's address on a forge, the record's line without one.
- `runs` is `None` when the forge's answer or the record could not be read. `start` says how a run on the tip is started, in the adapter's words. The judge prints it and never runs it.
- `chain` is the default branch's first-parent line, tip first: one entry per merge, or per commit made on the branch directly.
- `bound` is the project's bound (below), or `None` when the merge base binds no tier 2 gate.

The adapter maps the forge's runs as `forge_runs.py` does today. A completed run concluded `success` is green. A completed run with any other conclusion except `cancelled` and `skipped` is red. A run that is queued, waiting or in progress is running. A cancelled or skipped run is dropped: it covers nothing. Runs from another repository, or from an event other than a push, a schedule or a dispatch on the default branch, are dropped as today.

**What a run covers.** A finished run covers the commit it checked out. A merge commit stands for the change it merged, since the branch's own commits sit on its second parent and never on the chain. The **unproven distance** is the index in the chain of the nearest commit a finished run covers. It is 0 when a finished run checked out the tip, and the length of the chain when none did. A run whose commit is not on the chain covers nothing, so a force move drops what it proved. A finished run **speaks for** the chain from its commit down to, and not including, the next older commit a finished run covers. That is the set of merges its result can be blamed on.

**The rule.** `held` answers in this order.

1. `bound` is `None`: no hold.
2. `runs` is `None`: `tier2`, tier 2 on the default branch is not known. This is what the judge does today, where an unreadable answer is `red`.
3. Find the nearest chain commit that a finished run covers. Where several finished runs checked it out, the newest by `at` decides its colour, as `on_the_tip` decides today.
4. That run is red: `tier2`. The text names the run, the merges it speaks for, and the running run nearer the tip, if there is one, whose green would end the hold.
5. The unproven distance is more than `bound`: `unproven`. The text names the distance and the bound, and what ends the hold: the oldest running run on a chain commit nearer the tip than the nearest finished one, or `start` when there is none.
6. Otherwise: no hold.

`tier2` is the owner's, as today. `unproven` waits: nobody has anything to decide, and the hold ends without anyone acting.

**The truth table.** `T0` is the tip, `T1` its first parent, and so on. The bound is 1 unless the row says otherwise.

| # | Case | Runs the judge sees | Answer |
| --- | --- | --- | --- |
| 1 | No tier 2 gate bound at the merge base | any | no hold |
| 2 | No deep run ever, none running | none | `unproven`: nothing runs on `T0`, then `start` |
| 3 | The first run after the adoption, still going | running on `T0` | `unproven`: wait for the run on `T0` |
| 4 | A run in flight at the tip | running on `T0`, green on `T1` | no hold: distance 1 |
| 5 | A run in flight one behind | running on `T1`, green on `T2` | `unproven`: distance 2, wait for the run on `T1` |
| 6 | Row 5 with bound 2 | running on `T1`, green on `T2` | no hold |
| 7 | Row 4 with bound 0 | running on `T0`, green on `T1` | `unproven`: wait for the run on `T0` |
| 8 | The newest finished run is red and covers the tip | red on `T0` | `tier2`: it speaks for `T0` and each merge down to the last finished run |
| 9 | A red run older than a green one | green on `T1`, red on `T2` | no hold: distance 1 |
| 10 | A nightly run red after the merge's own run was green | green on `T0`, red on `T0` and newer | `tier2` |
| 11 | A nightly run on a tip whose own run never started | green on `T0` from the schedule | no hold |
| 12 | The tip's run cancelled by hand, nothing running | green on `T2` | `unproven`: nothing runs on `T0`, then `start` |
| 13 | A merge that changes the deep workflow | green on `T0` | no hold here; the `guard` reason sends the change to the owner |
| 14 | A force-moved default branch | green on a commit no longer on the chain, running on `T0` | `unproven`: wait for the run on `T0` |
| 15 | The forge cannot be read | `runs` is `None` | `tier2`: not known |
| 16 | The fix for a red run, before it lands | red on `T0` | `tier2`: the owner merges the fix |
| 17 | The same fix after the owner merged it | running on `T0`, red on `T1` | `tier2`: names the run on `T0` as what ends it |
| 18 | A merge that deferred its proof (#307), with bound 1 | green on `T1`, `T0` deferred | `unproven`: wait for the run on `T0` |

Row 13 needs no rule of its own. The deep run that covers the change is the changed workflow's, since a push runs the workflow file of its commit and a dispatch runs the default branch's. If the changed file starts no run at all, the project is at row 2 and the dispatch fails, which the hold's text covers. Row 17 is how a red ends: the owner merges the fix, the deep run on it goes green, and the next merge job passes. The judge never tries to tell a fix from another change.

### The bound

**The bound is how many merges may already be unproven when the next one lands.** It is `unproven` under `[ceilings.deep]` in `quality/thresholds.toml`, where the project's other limits live. The judge reads it from the merge base, as it reads the binding (ADR 0028), so a change cannot loosen its own judge. A key under `ceilings.` only falls (`lowered.py`). Lowering it is a tightening the judge lets an agent merge (`merge_ready.more_than_tightened`). Raising it or removing it is a loosening: `ratchet-lowered` is red on it without a `Relaxes:` line, and the change is the owner's to merge. A value that is not a whole number of 0 or more is read as 0, and the hold says so. A project that declares none is held at 1.

**What the bound costs.** The derivation below is from a model of a full queue, not a measurement. A finished run moves coverage up to its own commit. Runs go one after another, and a waiting run is replaced by a newer one. For a deep run of `D` minutes the default branch then takes at most:

- `60 / D` merges an hour with bound 0 or 1;
- `30 × (N + 1) / D` merges an hour with bound `N` of 1 or more.

| Deep run | Bound 0 or 1 | Bound 2 | Bound 3 |
| --- | --- | --- | --- |
| 10 minutes | 6 an hour | 9 an hour | 12 an hour |
| 30 minutes | 2 an hour | 3 an hour | 4 an hour |
| 45 minutes | 1.3 an hour | 2 an hour | 2.7 an hour |

With bound 1 the steady state is one new merge per deep run, so a red speaks for exactly one merge. With bound 2 the runs speak for two and one merges in turn, and a red can sit under two later merges.

**The default is 1.** It is the bound that keeps a red's blame to one merge at the highest rate that blame allows. Bound 0 has the same blame and a lower rate: each run starts only after the merge job has seen the last one end and merged. Bound 1 overlaps that latency with the run in flight. Its cost is one merge that may land on a tip whose run then goes red. That merge is not blamed, since the red run speaks for the merge before it. Above 1 the rate grows by half a merge per deep run for each step, and blame doubles. The lever for the rate is a shorter deep run (#238), not the bound.

### On a forge

**A deep run that has started finishes.** The template's `deep.yml` sets `cancel-in-progress: false`. The forge's concurrency group then keeps the running run and at most one waiting run, and a newer waiting run replaces an older one (ADR 0021 saw this of the `merge` group). The replaced run never started. It is dropped as cancelled, and the newer run covers its commits by checking out a later one. So no run that started is cancelled by a merge.

**Each deep run says which commits it speaks for.** The first job of `deep.yml` writes the commits since the commit the previous run checked out, run or finished, into the run's summary (`forge_runs.py speaks`, with `actions: read`). A started run always finishes now, so that list is exact unless the owner cancels the run before it, and then the judge's own text, which is computed from finished runs, is the one to trust. A red's `tier2` reason on each pull request names the same merges.

**The merge job reads every deep run and judges again when one ends.**

- It lists the deep workflow's runs on the default branch of every status, not only completed ones.
- `forge_runs.py deep --tip <ref> --answer FILE` writes the `Deep` value as JSON and prints `start` when nothing is running on a commit nearer the tip than the nearest finished run, while the tip is past that run. The job then dispatches the deep workflow on the default branch before it judges. Without `--answer`, the script still prints `green`, `red` or `none`, for a `merge.yml` the upgrade left owed.
- `merge_ready.py --deep FILE` takes the answer in place of `--tier2`. Given both, it refuses. Given only `--tier2`, it judges as today.
- `merge.yml` runs on `workflow_run` of both `ci` and `deep`. Its `if:` also takes a `deep` run on the default branch of this repository, whatever its conclusion, so a run that was cancelled by hand wakes the job too.
- The job still judges every green pull request in each run (ADR 0021). A deep run's end merges the first one that now passes. The next is behind the new tip and waits for its own `ci`, so a job never merges twice against one answer.

**The verdict says when it waits.** The judge prints `merge: wait` in place of `merge: owner` when every reason it has is `unproven`, and the merge job's comment says that nobody needs to act and which run it waits for. It exits 1 either way. Any other reason makes it the owner's. So the owner is not shown a pull request as theirs that will merge itself, and is not invited to merge by hand past a hold that ends on its own.

**What the forge cannot be read for.** An unreadable list, or a full page of runs that holds no finished run on the chain, is `runs = None`. That is `tier2`, not known, the owner's, as `red` is today. The next event that starts the merge job reads the forge again.

### With no forge

**The merge proves the tip itself.** `merge.py` reads the record below into a `Deep` and asks `held` before it judges the branch. When `held` holds and no record line has the tip's own commit, it runs the binding's `check-deep` in a detached work tree of the tip, appends the result, and asks again. Then it goes on as today, or refuses with the reason that is left. A red on the tip itself is `tier2`, and the owner merges the fix by hand, as `merge.py` already tells them. The next merge then finds a tip past the red with no record of its own, and proves it before it judges. So no-forge holds never wait: the merge does the work. With bound `N`, one merge in `N + 1` pays for a deep run of `D` minutes, and a red speaks for up to `N + 1` merges. When `merge.py` runs `check-deep` on an adoption's merge result, as ADR 0031 has it do, it records that result for the commit it lands.

**The record.** `ratchet-deep.jsonl` in the repository's common git directory, the one `merge.py` already keeps its landing state in, so every work tree of the repository reads one file. Each line is one JSON object.

```json
{"schema": 1, "commit": "<sha>", "tree": "<the commit's tree>", "status": "green", "exit": 0, "seconds": 812.4, "at": "2026-11-20T09:00:00Z", "entry": "<the check-deep command>", "speaks_for": ["<sha>"]}
```

The merge reads a line by its commit. `tree` says what was checked: the commit's tree, checked out clean. A line that does not parse makes `runs` `None`, which is `tier2`. Skipping it could skip a red. That reason names the escape: the owner removes the file, and the next merge proves the tip again, so a red that is still there is found again.

**Why an agent cannot forge a green more easily than today.** Only `merge.py` appends to the record, right after it ran `check-deep` itself, in a work tree it made of that commit, so no uncommitted file took part. The record sits beside the refs the path guards protect (ADR 0016), and the adopt template's `local-git` group denies agents edits to it as it denies `.git/refs/**`. An agent that writes it past the hook could as well move the default branch past the hook. That already lands anything today, with no record at all, so a forged line buys nothing an agent does not already have. Compare `.ratchet/timing.jsonl`, the runner's record that `runrecord.stamp` signs. It sits in the work tree, no guard names it, any run in any tree appends to it, and its tree stamp is a content id that a dirty work tree also has.

### When the hold arrives

**A project with no tier 2 gate bound is never held.** `bound` is `None` unless the merge base's binding has a Gates row in tier 2 whose tool is bound, as `project_bite.Row.bound` reads it: not empty, not `later:`, not `none:`. The `every gate bites` row (`bite-all`) is tier 2 and its trigger is `always`, so every adopted project has one from its adoption. In practice the hold arrives with the adoption. Its cost then is a young project's deep run, which is seconds to minutes. A project with no binding has no merge judge to hold it.

### The upgrade

What the new rule finds in a project adopted under an older release, and what the upgrade does with it (ADR 0037):

- **`deep.yml` still has `cancel-in-progress: true`.** Rewritten: conformance raises the finding with an `Edit` that changes that one line, the way it adds `--tier1` to an older `merge.yml`. Where the line is not in the template's form, it is owed. Owing it is safe. Under the bound, a merge that cancels a running deep run is one of at most `bound + 1`, and the merge after them waits, so runs cannot starve. Only the blame grows.
- **No `[ceilings.deep]` key.** Rewritten: the upgrade adds the table at 1, which is what the judge reads when the key is missing, so it loosens nothing and changes no merge. With the key written, a later lowering keeps the file's shape and is the agent's to merge. Where `quality/thresholds.toml` does not parse, it is owed, and the judge holds at 1.
- **`merge.yml` without the deep answer or the `deep` trigger.** Rewritten where its lines are the previous template's, as for `--tier1`. Otherwise it is owed. Until it is paid, the job passes `--tier2` and the judge holds as today. That is not red. It is the old rule, and conformance names the debt.
- **The `local-git` group without the record's path.** Owed, always: an upgrade never writes under `guards/` (ADR 0037). It is an owner finding, assumed to leave the record unguarded until the owner adds the path. That changes nothing an agent could not already do (above).

### The drain

**On a forge**, the drain does not merge. The merge job does. When the judge's comment on a ticket's pull request says `merge: wait` and names a deep run, the drain waits for that run to end and for the merge run that follows, then reads the verdict again. It sets no timer of its own: the deep workflow's jobs carry `timeout-minutes`, and the forge's own limits end a run that never gets a runner. It reports `waits: <ticket>: the deep run <name> on <commit>`. When a `merge: wait` names no run, it closes the ticket `waits` with the reason. `unproven` is one of the reasons `queue_notes.py` counts as mechanical, so a later drain retries it. After `RETRIES` tries the ticket is held for the owner, who sees that nothing starts the deep run.

**With no forge**, `drain.py land` runs `merge.py`, which proves the tip itself when the hold asks for it. The landing takes a deep run longer. It prints `deep: <tip> is <n> merges past the last finished deep run, and this project lets <bound> land unproven: running check-deep on it first`, then `deep: <green|red> on <tip> in <seconds> s, recorded; it speaks for <subjects>`. Nothing waits.

### What ends each hold

No state holds forever with nothing anyone can do.

| Hold | What ends it | Who acts |
| --- | --- | --- |
| `unproven`, a run is running | That run ends. The merge job wakes on its end. | nobody |
| `unproven`, nothing runs on a forge | The merge job dispatches a run each time it wakes: on each `ci` that finishes, each deep run that ends, and the nightly run. | nobody, unless the deep workflow is off or the forge is down; then the owner turns it on |
| `unproven` or `tier2` with no forge | The next merge proves the tip. | the agent that merges |
| `tier2`, red | A later green run. | the owner merges the fix |
| `tier2`, not known | The next merge job reads the forge again. | nobody |
| `tier2`, the record does not parse | The owner removes the file, and the next merge proves the tip. | the owner |

A deep run that never starts is the case to solve. On a forge, the merge job asks for one each time it wakes. If none starts, the deep workflow is turned off or the forge is down, and the hold's text says both. A transient outage ends at the next event, and the nightly run is that event when the queue is idle. A workflow turned off is the owner's to turn on. The owner can also merge by hand, as they can past any reason. That is not a bypass. Only an agent's merge is held. An owner's hand merge is the owner's path the method already has, and the next finished deep run still covers what they merged. It skips the wait, not the proof. Raising the bound is the other way out, and it is a loosening with a `Relaxes:` line, in the history.

### What #307 and #238 add

**#307: a merge that deferred its proof is covered before the next one.** It adds one keyword to `held`, `deferred: Collection[str] = frozenset()`: the chain commits whose merge deferred its bite. In rule 5, an unproven commit in `deferred` holds as if the bound were 0: `unproven`, with the text naming the deferral. Row 18 is its case. #307 decides where `deferred` comes from, for example by reading each unproven merge's first-parent diff with `project_bite.py`'s causes. Neither `DeepRun`, `Deep` nor the other rules change.

**#238: a deep run in slices.** The judge still sees one `DeepRun` per run on one commit. Green means every slice was green. Red means some slice was red, and the adapter may say so as soon as one is, before the others end. Running means neither. Only `forge_runs.py`'s mapping and `deep.yml` change. If #238 splits slices into separate runs rather than one run's jobs, the adapter groups them by commit and attempt into one `DeepRun`. A shorter `D` raises the rate the table above gives, at the same bound.

## Refutation criterion

This is wrong if any of these is seen:

- With the default bound on a forge, a red deep run speaks for more than two merges, as its summary or the `tier2` reason lists them.
- A pull request reads `merge: wait` across two merge-job events with no deep run running or waiting on the default branch, and the workflow is on. A hold then named nothing that ends it.
- A deep run that started is cancelled by a merge.
- With a full queue, merges on an adopted project come further apart than the deep run's own time plus the merge job's, at bound 1. The bound then costs more than this record says.
- With no forge, a merge lands on a tip past the bound with no record line for that tip written by `merge.py`.

## Considered options

- **Keep cancelling a superseded run and only bound the distance.** Under the bound this does not starve: merges stop until a run finishes. With a full queue it would take about twice the merges an hour, since each run would cover `bound + 1` merges. But a red would speak for `bound + 1` merges, a started run's work would be thrown away, and #306's second ask would stay unmet. #316 asks that a started run finish. Rejected.
- **Count a running run as covering its commit.** A run that has not finished has proven nothing. Rejected: a judgement not made is not green.
- **Hold on a red only, and no bound.** While a run is in flight, a queue could land any number of merges on what then turns out red. That is today's failure. Rejected.
- **Let the merge job wait inside its run for the deep run to end.** A timer that holds a runner for the length of a deep run, and fails silently when the job's own time runs out. Rejected for the event the forge already gives.
- **With no forge, run `check-deep` on the merge result in place of `check`.** One run instead of two, and a red would never land. But a red there refuses the change for an earlier merge's fault, and its blame spans the change and every unproven merge before it. Rejected for proving the tip first, which blames the merges that are on the default branch.
- **With no forge, read the runner's `.ratchet/timing.jsonl`.** Rejected: unguarded, in the work tree, written by any run of any tree.
- **Default bound 0.** The same blame as 1 at a lower rate (above). Rejected as the default. A project may set it.
- **Default bound 2 or more.** Half a merge more per deep run for each step, and blame doubles. Rejected as the default. The owner may raise it with a `Relaxes:` line.

## For the builders

### Seen before it is built

These are facts of the forge this record infers and has not seen. Check each on a sandbox repository before the forge pull request, as ADR 0021 checked its own.

1. A `workflow_run` on `deep` completed starts the default branch's `merge.yml` when the deep run was dispatched by the merge job's own token. If it does not, the template's deep workflow ends with a job that runs whatever came before it and dispatches `merge.yml`, which gains a `workflow_dispatch` trigger.
2. With `cancel-in-progress: false`, a run in progress is not cancelled by a newer one, and a newer waiting run replaces an older waiting one.
3. A completed run that ended cancelled starts `workflow_run`.
4. The runs list with no status filter returns queued and in-progress runs with their `head_sha`.

### The pull requests

The repository records no change-size ceiling of its own. Each pull request below stays under the adopt template's, 25 product files and 1,000 product lines, and is green on its own.

**1. The judge, inert until a workflow asks.** The shape, the rule and both readers of the forge, with nothing in a template using them yet.

- Writes `scripts/unproven.py` (new: `DeepRun`, `Deep`, `Landed`, `Hold`, `held`, `speaks_for`, and the JSON reader), `scripts/forge_runs.py` (`deep --answer`, and `speaks`), and `scripts/merge_ready.py` (`--deep`, `Asked.deep`, the bound and the tier 2 predicate read from the merge base, the `unproven` reason, `merge: wait`).
- Red first: `hooks/tests/test_unproven.py` (new), one case per row of the truth table above against `held`, and the `speaks_for` range of rows 8 and 10. Then `hooks/tests/test_forge_runs.py`, where `deep --answer` maps queued and in-progress runs to running, drops cancelled and skipped runs, and still prints the legacy word without `--answer`. Then `hooks/tests/test_merge_ready.py`, on a real repository: two merges past a green run with bound 1 gives `merge: wait` and an `unproven:` line naming the running run; a red gives `merge: owner` and `tier2:`; the bound comes from the merge base, not the head; no bound tier 2 row passes; `--deep` with `--tier2` is refused.

**2. The forge turns it on.** The templates, conformance and the upgrade's rewrites, the pages and the drain.

- Writes `skills/adopt/templates/.github/workflows/merge.yml`, `skills/adopt/templates/.github/workflows/deep.yml`, `skills/adopt/templates/quality/thresholds.toml`, `scripts/conformance.py` (the new markers, and the `Edit` fixes for the three older forms), `scripts/queue_notes.py` (`unproven` is mechanical), `skills/method/tiers.md` (tier 2's row and the rule), `skills/method/guards.md` (the setting and `merge: wait`), `skills/feature/SKILL.md` (step 6 on a forge waits for the named run), and `docs/gates.md`.
- Red first: `hooks/tests/test_conformance.py`, where a project on the 0.13.3 templates yields an `Edit` for each of the three, and applying them gives the new template's text. `hooks/tests/test_upgrade_writes.py`, where upgrading a project adopted on 0.13.3 rewrites all three and leaves conformance green, and a hand-edited `merge.yml` is owed with conformance still green. `hooks/tests/test_drain.py`, where a `merge: wait` close is retried and not held.

**3. The no-forge record.** The merge proves the tip, and the record and its guard.

- Writes `scripts/unproven.py` (the record's reader and writer), `scripts/merge.py` (prove the tip when held, record the adoption's `check-deep`, the new `next:` line), `skills/adopt/templates/guards/manifest.toml` (the record under `local-git`), `scripts/conformance.py` (the owner finding when the manifest lacks it), `skills/method/guards.md` and `skills/method/tiers.md` (the paragraph with no forge), and `docs/gates.md` (`merge.py`'s row).
- Red first: `hooks/tests/test_merge.py`, where a branch merged with the tip two merges past the last record line runs `check-deep` on the tip before the merge and appends one line with its commit and `speaks_for`; a red `check-deep` there refuses with `tier2` and moves nothing; a line that does not parse refuses with `tier2` and names the file; after the owner's hand merge past a red, the next merge proves the new tip first; an adoption's merge records its commit. `hooks/tests/test_conformance.py` for the owner finding.

### Consequences

- A red deep run blames one merge on a forge with the default bound, and at most one merge lands on top of a red before it is found.
- With a full queue, the rate of merges is set by the deep run's length. The project in #238, at 34 to 51 minutes, would take 1.2 to 1.8 merges an hour. #238 is what makes that faster.
- #307 can defer a proof to the deep run and stay safe.
- Every adopted project is held from its adoption, since `bite-all` is a tier 2 gate there.
- A `merge.yml` the upgrade cannot rewrite keeps today's judge until its owed entry is paid.

## For the owner

The direction is the owner's, from #316. These are the calls in it that no measurement settles. Each states the default this record takes.

- **The default bound.** 1. It costs about `60 / D` merges an hour with a full queue.
- **Finish rather than cancel.** A started run finishes, as #316 asks. Cancelling under the same bound would take about twice the merges an hour, and blame twice as many merges.
- **The hold from the adoption.** Every adopted project is held, since `bite-all` is tier 2 and `always`.
- **The owner's way past a deep run that never starts.** A hand merge, or turning the workflow back on. No agent path skips the wait.

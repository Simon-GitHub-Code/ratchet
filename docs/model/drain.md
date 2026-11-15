# The queue drain and its merge

`scripts/drain.py` drains the queue: `/ratchet:feature` with no ticket builds every agent-ready ticket, each in a work tree of its own, and merges them one at a time (ADR 0023). The script decides what needs no judgement and keeps what must outlive a session. `plan` says what each ticket is, `open` makes a ticket's work tree and branch only when the plan starts it, `update` rebases a built branch onto the default branch, `check` runs the publish check of ADR 0060 on a ticket's branch, `land` runs the merge with no forge and closes the ticket with what the judge printed, `close` removes a work tree and writes the ticket's note, `clean` removes leftover work trees, and `report` prints what merged, what waits and what is parked. `scripts/merge.py` is the merge `land` runs: it merges the branch in a scratch work tree, asks the judge and runs `check` on the result, and moves the default branch forward only when both are green.

The drain promises that an interrupted drain loses no work and repeats nothing (ADR 0023). Each command finishes from what git holds when it is run again: `update` abandons a rebase it left and starts over, `land` records a merge that landed, `clean` records the merge of a branch whose note does not yet say it, and every command first puts back what a killed `merge.py` left. One drain runs on a repository at a time: `clean --all` takes a lease and is refused while another drain is alive, and the report ends it. This page writes the drain down as a state machine, and says which of these promises the code keeps. For the publish check it says what the code must do: `drain.py` does not run the check yet, and the model holds the steps it is to take.

The model is `hooks/tests/drain_model.py`. The tests in `hooks/tests/test_drain_model.py` check each rule on every step the model reaches. They also take the model's steps on real repositories, where the real `drain.py` and `merge.py` must leave what the model says (`hooks/tests/drain_world.py`).

## Scope

The model has two graphs.

The main graph is one drain on one repository with no forge:

- three tickets, `a`, `b` and `c`, where `b` is blocked by `a`, under a cap of one; and `d` and `e`, the two parts `drain.py split` files when the agent splits `a` as too large, where `e` is blocked by `d` (ADR 0046);
- the drain's commands in any order, what the agent does in a work tree, a commit a role of a pipeline run makes on the branch, the publish check, the agent's split of `a`, what the owner does, an upgrade that makes the preflight ask the `Flag:` line, and a stale index lock git leaves;
- a kill of `update`, `close`, `land` or the `merge.py` it runs between two of their effects;
- each ticket playing one part. The agent and a role commit only in `a`, whose branch goes the whole way to merged, and the agent leaves work uncommitted only in `c`, the ticket the cap holds back. `b`, which waits on `a`, is only opened. A kill stops `update` and `land` only in `a`, and `close` only in `c`;
- the agent splits only `a`, in a clean tree before any commit, as the spec stage's estimate stops it. The parts are local tickets, committed on the default branch with the split. `d` and `e` are only opened, built, judged and landed. Once `a` is split no command names it, the owner's one edit of it is to close it, and the upgrade comes only before a split;
- the owner edits a ticket at most once, the agent leaves work uncommitted once, a role commits once, and the retry count of a ticket left waiting stops at three, past which nothing reads it;
- a role commits before its run returns, so before the check of that run. The skill runs `check` after a run returns, so only on a start no check has passed, of a ticket its note does not hold. A check that cannot read the present state, and one that finds only a rebase left unfinished, come only in `a`, on a branch with commits of its own. The agent commits before the check only in `a`: in `d` and `e` it keeps the skill's order;
- a project whose binding asks the `Flag:` line and that is not deployed, where `b` and `c` state their line and `a` does not. Before the upgrade that ships ADR 0061 the preflight asks no ticket for it, and after it the preflight refuses `a` until the owner lands its line. The owner's landing of the line is their one edit of a history;
- a judge that passes a branch only when its verdict record is its last commit and the branch holds the default tip; a `check` that is always green; and tickets no two of which change the same file, so nothing conflicts.

The lease graph is two drains on one repository and one ticket's work tree. Its first step says how the drains run: each in a session of its own, or both under one long-lived process. Then each drain takes the lease, opens the ticket, leaves a draft or starts a program in its tree, lands the ticket, reports, cleans, stops or dies, in any order. `land` is three steps: up to the `merge.py` it runs, the merge `merge.py` lands, and the close after it. `land` holds the drain's lock for its checks and again for its close, and not while `merge.py` runs, so every other command can run in between.

A landing is seen by three locks of the kernel's, each freed when its holder dies. `land` holds the ticket's landing lock all the way through. It also holds a private lock, in a file of a fresh name it passes to `merge.py` in `RATCHET_LANDING`. `merge.py` holds a merge lock of its own on the branch from its start to its end, and once it holds it, it probes the private lock: held, its `land` is alive and it goes on; free or gone, its `land` has stopped, and it merges nothing. Every command reads the landing lock and the merge lock. A kill can stop `land` in three places:

- `land` and its `merge.py` together before the merge, as a session's end or a shell's timeout ends both; or `land` alone after the merge and before its close;
- `land` alone before its `merge.py` has probed it. For that instant no lock is held, and every command acts as with no landing. The `merge.py` then finds its `land` gone and stops, having merged nothing;
- `land` alone once its `merge.py` has probed it. The merge lock is held, so every command of either drain sees a merge running. The `merge.py` then lands the merge or ends having landed nothing.

While the landing is alive, either drain's agent can also run `update`, `close --outcome parked` or a second `land` on the ticket. The landing drain's own agent does so when its shell gave up waiting on `land`. A second `land` in the instant before a killed `land`'s `merge.py` probes it starts a merge of its own, so two `merge.py` of one ticket run: the first stops at its probe, and the second lands under its live `land`, or meets the merge lock the first holds past its tries and stops, and its `land` closes nothing.

What it leaves out is listed [at the end](#left-out).

## State

| Part | Values |
| --- | --- |
| Each ticket's work tree | none; clean; uncommitted work; uncommitted work behind a stale index lock; a rebase under way, the tree detached at the default tip |
| Each ticket's branch | none; no commit of its own; commits, the verdict record not last; commits, the verdict record last; commits, the verdict record last and certifying a tree the branch no longer has |
| Whether the branch holds the default tip, and whether it is merged | yes or no |
| Whether the verdict record was written on the tip the branch holds now | yes or no. No file holds it. The model keeps it so that a rule can read it |
| Whether a role of a pipeline run committed on the branch | yes or no, until the branch merges. A role's brief forbids it. No file holds it: the model keeps it so that rules 11a and 11b can read it |
| Whether the branch moved from the start its note records | yes or no. It is what the publish check reads: the branch's head against the start's (ADR 0060). Once a check passed the start nothing reads it, until `update` records a new start |
| Whether `a` was split | no, and `d` and `e` are not filed; split, `a` open and held; or split, and the owner has closed `a` |
| The drain's note on the ticket | none; or opened, merged, waits or parked, or split. A note can hold the ticket, on the text it was written on, count retries, owe a report, and carry `update`'s mark that a rebase began or `close`'s mark that it has not yet removed the tree. The note of a ticket with a branch holds the start `open` or `update` recorded, and the mark `passed` once a check passed it |
| The ticket's text, and its stashes | the text the owner wrote first or after an edit; how many stashes hold its uncommitted work |
| What the preflight asks of `a`, the ticket with no `Flag:` line | nothing, before the upgrade; its `Flag:` line, which it lacks; or nothing more, once the owner has landed the line. Once `a`'s branch holds a commit no command reads it again, so the model keeps it as before the upgrade there |
| What a killed `merge.py` left | its scratch work tree; and the ticket whose fast-forward of the default branch was cut off half-way, its files in the default branch's work tree, one part-written, and its index locked |

The plan reads a ticket as `drain.plan` does: `merged` when its branch is, the ticket still to be marked resolved, `held` when its note holds it on the text it has now, `built` when its branch holds a commit the default branch does not, `building` when it has a work tree, `waiting` while its blocker can still merge, `refused` when it cannot or when the preflight refuses the ticket itself, and `buildable` otherwise. The preflight reads only a ticket with no tree and no commit of its own. A blocker is read by its open or closed state alone, as `drain.pending` reads it: a blocker that is closed, by its merge or by the owner, is met, and the note of a split blocker is not read. A split ticket stays open, and its note holds it.

From the start the main graph reaches 49,115 states and 682,505 steps. Before the publish check was a step it reached 33,246 states and 479,008 steps: the check adds the note's start and its mark, and whether the branch moved from the start, and the agent may commit in `a` before the check, which then parks it. Where the agent keeps the skill's order, the main graph reaches 40,356 states and 556,376 steps. Before a role's commit was a step it reached 12,863 states and 190,251 steps: the branch a role committed on repeats every state `a`'s branch reaches after a commit. With that step and before the upgrade and the `Flag:` line were steps, it reached 22,238 states and 332,594 steps. Before the split was a step it reached 24,312 states and 363,475 steps. The split adds 8,934 states, in which the parts are built while `b` waits and `c` goes on.

| Part of the lease graph | Values |
| --- | --- |
| How the drains run | not yet said; each in a session of its own; or both under one process |
| The lease | none, or held by drain 1 or drain 2 |
| Each drain | its session alive or not; draining or stopped; whether it holds its token, which `clean --all` prints on stderr and a later command passes back as `--drain` |
| The ticket's work tree | none, or opened by drain 1 or drain 2; a draft in it; a program running from it; a draft kept as a stash |
| A landing | none, or the drain whose `land` is past its checks and has not yet closed, with its merge landed or not yet. It is alive while `land` holds the lock on its ticket, which it takes before its checks |
| A `merge.py` whose `land` was killed past its probe | none, or the drain whose `land` it was. It holds the merge lock until it lands the merge or ends |
| A `merge.py` whose `land` was killed before its probe | none, or one still starting. It holds no lock, and it can only stop |
| The merge | not merged; merged and no note says so; a note no report has printed says so; printed |
| Whether the ticket's note holds it | yes or no. Only `close --outcome parked` writes a hold, in the instant before a killed `land`'s `merge.py` probes it, where no landing is seen. The tests read the note's hold back after every step |

From the start the lease graph reaches 4,583 states and 60,124 steps.

## Steps

| Step | What it does |
| --- | --- |
| `drain.py open` | makes the ticket's work tree and a branch at the default tip, when the plan starts it, and records the branch's head in the note as the start, with no mark |
| the agent commits; the agent commits the verdict record; the agent leaves work uncommitted | in the ticket's tree. The agent is the session that runs the pipeline, and it commits after the run returns. The skill says after the check too, and in `a` the model lets it commit before |
| `drain.py check` | the publish check, in the ticket's tree against the note's start; a branch with commits of its own and no tree gets one under the cap first. Green marks the start `passed`. A branch that moved is closed as parked with the reason that a role published, its work stashed, and held until its text changes |
| `drain.py check` cannot read the present state | closes the ticket as waiting, to be retried, the start left without the mark |
| `drain.py check` finds only a rebase left unfinished | nothing moved, so it marks the start `passed`, and closes the ticket as waiting with the conflict close's reason, to be retried |
| an upgrade makes the preflight ask the `Flag:` line before deployment | once, while `a`'s branch holds no commit of its own: the plugin ships ADR 0061. From then on the plan refuses `a`, which has no `Flag:` line, and so `b`, which waits on it |
| the owner lands the `Flag:` line of `a` | commits the line to `a`'s text on the default branch, which moves the default tip and changes its text as the owner's edit does. It is the owner's one edit of the history |
| a role of the pipeline run commits | in `a`'s clean tree, on a branch with no commit of its own or one `update` rebased for a rejudge, before the run returns and so on a start no check has passed. The branch then holds a commit, its verdict record not last |
| `drain.py update` | rebases the branch when the default branch has moved, first making the work tree of a built branch that has none, and after the rebase dropping a last commit that is the verdict record. On a branch with commits of its own and a start no check passed, it first runs the check as `check` does, once it has a tree: green marks the start `passed` before the rebase begins, and a branch that moved is parked. A rebase that moved the branch records its new head as the start, with no mark. On a landed branch it records the merge and does not rebase |
| the check of `drain.py update` cannot read the present state | `update` is refused, having made the tree it would check, and changes nothing else |
| `drain.py land` | refuses a start no check passed, after a held ticket and before `merge.py`. Otherwise it runs `merge.py`, and closes the ticket as merged, as waiting for a reason the drain resolves itself, or held for the owner |
| `drain.py close --outcome parked` | writes the note that holds the ticket, then removes the tree with its uncommitted work kept as a stash |
| `drain.py split` | files the parts `d` and `e`, closes `a` as `split` with the parts in its note's `children`, holds it on its text, and removes its tree. The parts of a local ticket are files the checkout does not yet hold, and the plan starts nothing until they are committed, so the step commits them too, which moves the default tip |
| the owner closes the ticket | sets the split ticket's status to done on the default branch, which moves the default tip and changes its text. It is the owner's one edit of the history |
| `drain.py clean`, `drain.py clean --all` | removes each tree of a merged branch, or every tree, after recording each merge no note says |
| `drain.py report` | records each merge no note says, then prints each close and merge not yet printed |
| the owner edits the ticket | commits a change to its text on the default branch, which moves the default tip |
| git leaves a stale index lock; the owner removes it | in `c`'s tree |
| a kill | stops `update` mid-rebase or after it; `close` before its discard or after its stash; `land` before its note, before its discard or after it; or the `merge.py` that `land` runs, before its fast-forward or in the middle of it |

Every command first puts back what a killed `merge.py` left, and so does a command that is then killed: it sweeps the scratch work tree and puts back a fast-forward cut off half-way. So no command reads a ticket's text while a cut-off fast-forward holds the merge's.

On the lease graph, each drain can: `clean --all`, with its token, or with `--take-over`; `open a`; let its agent leave a draft or start a program in its tree; `land a` up to the `merge.py` it runs; `report`, with its token or without; `clean`; stop with its session still open; or end its session. Under one process a session's end ends both drains. A program can end. A landing goes on by its own steps: its `merge.py` lands the merge, then `land` closes the ticket. A kill can stop `land` in the three places [above](#scope). A `merge.py` whose `land` was killed past its probe lands the merge, or is killed itself before it lands. One whose `land` was killed before its probe takes the merge lock, finds the private lock free, and stops. That is one step here: the instant it holds the merge lock before it stops is [left out](#left-out). When a second `land` runs in that instant, its `merge.py` may meet that merge lock past its tries; then both stop in one step, and the second `land` closes nothing.

While a landing is seen, either drain can also run `update a`, `close --outcome parked a` or `land a` on its ticket. Each is refused and changes nothing. While `land` holds the landing lock, `update` and `close` print ``<command>: a landing of `<branch>` is running in another command; nothing was done; wait for it to end, then run plan: the landing records the ticket's outcome itself`` and exit 3, and a second `land` prints its own line and exits 2. While only the merge lock is held, `update` and `close` print ``<command>: a merge of `<branch>` is running in another command; nothing was done; wait for it to end, then run plan`` and exit 3, and a second `land` prints ``land: a merge of `<branch>` is running in another command; run land again once it ends`` and exits 2. `report` and `clean` leave the ticket alone either way, and `clean --all` keeps its tree with ``kept: <tree>: `<branch>` has a landing running`` or ``has a merge running``. `open a` changes nothing there either: the plan does not start the ticket, whose branch has commits or has merged. The graph has `update` and `close` only while a landing is alive or in the instant before a killed `land`'s `merge.py` probes it. Outside those they are the main graph's steps.

## Rules

The promises are thirty-one rules, each about a step. The model numbers them, and the tests and this page use the numbers. The module names each `Rule` and its number, as `Rule6a`.

| Rule | Says | In Python | In TLC |
| --- | --- | --- | --- |
| 1 | No more tickets build at once than the cap. | holds | holds |
| 2 | Uncommitted work is never lost. | holds | holds |
| 3 | The default branch moves only to a judged merge. | holds | holds |
| 4 | A branch does not land on a review of an older base. | holds | holds |
| 5 | A merge is recorded and reported exactly once. | holds | holds |
| 5a | A report writes only what it prints, and a merge no note says. | holds | holds |
| 6 | A held ticket stays held until its text changes. | holds | holds |
| 6a | A ticket its note holds merges only once the owner edits it. | holds | holds |
| 7 | A ticket whose blocker waits on the owner is refused. | holds | holds |
| 7a | A ticket the plan refuses is never opened (ADR 0061). | holds | holds |
| 7b | Once the owner lands a refused ticket's `Flag:` line, neither it nor a ticket waiting on it is refused (ADR 0061). | holds | holds |
| 7c | A ticket that waits on a split ticket is never opened before every part of it has landed (ADR 0046). | holds | not checked: no run takes a split |
| 7d | A ticket that waits on a split ticket is not refused while every part can still land, and is buildable once every part has landed (ADR 0046). | holds | not checked: no run takes a split |
| 8 | A killed command recovers by running again or by `clean --all`. | holds | holds |
| 10 | `clean --all` leaves no queue work tree. | breaks in 5 steps | breaks in the work run, by the same 5 steps |
| 11a | A branch a role committed on is never landed (ADR 0060). | holds | holds |
| 11b | A branch a role committed on is never committed on after its run (ADR 0060). | breaks in 3 steps where the agent commits before the check; holds where it keeps the skill's order | breaks in the life run, by the same 3 steps |
| 9 | A second drain never removes a work tree a live first drain uses. | breaks in 4 steps | breaks in 4 steps, by the same run |
| 9a | A second drain removes it only where the docstring says the two look alike. | breaks in 7 steps | breaks in 7 steps, by the same run |
| 9b | A drain that has stopped never blocks the next. | breaks in 4 steps | breaks in 4 steps, by the same run |
| 9c | A drain that is gone never blocks the next. | holds | holds |
| 9d | A report ends no lease but its own drain's. | breaks in 3 steps | breaks in 3 steps, by the same run |
| 9e | A merge is printed by exactly one report across both drains. | holds | holds |
| 9f | No drain discards the tree of a landing another live drain runs. | holds | holds |
| 9g | A landing's close keeps a merge another command recorded meanwhile. | holds | holds |
| 9h | No command but the landing itself removes the tree or writes the note of a live landing. | holds | holds |
| 9i | A ticket whose note holds it is not merged by the drain. | holds | holds |
| 9j | A landing is seen until its `land` and every merge of it that can still land have ended. | holds | holds |
| 9k | A landing is not seen once its `land` and every merge of the branch have ended. | holds | holds |
| 9l | No close is written while a merge that can still land is alive, but by its own landing. | holds | holds |
| 9m | At most one merge of the ticket that can still land is alive at a time. | holds | holds |

Rules 1 to 8, 7a to 7d, 10, 11a and 11b are on the main graph, 9 to 9m on the lease graph. In Python the main rules are proved on the whole main graph, and the lease rules on the whole lease graph. TLC checks the main rules on three runs of the main graph, as [below](#the-rules-in-the-modules) says, and the lease rules on the whole lease graph.

In rules 7, 7c and 7d a ticket that waits on a split ticket waits on its parts: the method builds a split ticket as its parts, and it is done when they are (ADR 0046). A part can still land while the plan keeps it buildable, building, built or waiting, or once it has. Rule 7 reads the parts as the blockers: a ticket that waits on a held part is refused.

In 9f, 9h and 9l a live landing is a `land` that runs, or a `merge.py` it started that can still land. A `merge.py` whose `land` died before its probe can land nothing, so it keeps no landing alive. Rule 9g holds because no step reaches what it forbids: while a landing is alive no other command records its merge, which is rule 9h, so the landing's close never meets a merge recorded by another command. It stays as a rule so that a change that lets one in breaks it.

Three rules were worded again with the fix below, and two are new, each for a reason:

- 9i says "by the drain". The owner may still merge a held ticket by hand, which no step of the lease graph is.
- 9j said a landing is seen while its `land` or its `merge.py` is alive. A `merge.py` that has not passed its probe can land nothing once its `land` is dead, and it holds no lock then, so the rule is now about a merge that can still land. The code before the fix breaks it in 5 steps, at the kill of `land` alone.
- 9k said a landing is not seen once its `land` and its `merge.py` have ended. The merge lock is the branch's, not one `merge.py`'s, so the rule now says every merge of the branch.
- 9l and 9m are new. They say what the case of two `land`s across the instant must keep: no close is written for the ticket while a merge of it that can still land is alive, except by the landing that owns that merge, and the ticket is never merged twice. The code before the fix breaks 9l in 6 steps, by a `close --outcome parked` after the kill of `land` alone, and 9m in 6 steps, by a second `land` there.

Rule 11 said that a branch a role committed on is never committed on after its run, nor landed, and broke in 3 steps, as nothing checked the branch. With the check it is two rules, since the two halves rest on different things. Rule 11a, never landed, rests on the drain alone: `land` refuses a start no check passed, a check passes only a branch that has not moved from its start, and only `open` and `update` record a start, `update` after its own check. So it holds with the agent's commits free on a branch a role committed on. Rule 11b, never committed on, rests on the agent: the drain cannot stop a commit. It holds only where the agent keeps the feature skill's order, the check after the run returns and before its first commit, which is the one assumption the model makes of the agent. The tests prove 11b on every step of the main graph that keeps the order, and pin its break where the agent commits first. Beside the rules, the tests check that every merge on the main graph is of a branch whose start a check passed.

Rules 9i and 9m stand for "no ticket ends held and merged" and "no ticket ends merged twice". The lease graph has one merge, which a step lands only from not merged, so it cannot hold two; 9m forbids the two live merges that a second merge would need.

### Where a rule breaks

Rules 7c and 7d held once `preflight.blockers` read a split blocker, open or closed, as its parts, for `drain.pending` and the one-ticket preflight alike. Before, 7c broke in 4 steps (`drain.py open a`; `drain.py split a`; `the owner closes ticket a`; `drain.py open b`: the closed blocker was met and `b` started before its parts), and 7d in 2 (`drain.py open a`; `drain.py split a`: the held blocker made `drain.settled` refuse `b` for good). `test_a_ticket_waiting_on_a_split_ticket_the_owner_closed_waits_for_its_parts` and `test_a_ticket_waiting_on_a_split_ticket_waits_for_its_parts_then_starts` replay both runs.

Each break is pinned in the tests by the shortest run to it, and each run is replayed on a real repository, where the real scripts do what the model says. The docstring of `drain.py` accepts each one but 11b, which no script closes: it rests on the skill's order.


- 10, in 5 steps: `drain.py close --outcome parked a`; `drain.py open c`; `the agent leaves work uncommitted in c`; `git leaves a stale index lock in the tree of c`; `drain.py clean --all`. The docstring says: "A work tree whose uncommitted work git cannot stash (a stale lock, a merge left unfinished, a damaged index) is not removed". Rule 2 holds there: the work stays in the tree.
- 11b, in 3 steps: `drain.py open a`; `a role of the pipeline run commits in a`; `the agent commits the verdict record of a`. The agent records its verdict before it runs the check, which the skill's order forbids, and so commits on the role's commit. Rule 11a holds there: `land` refuses the branch, whose start no check passed, and the check parks it. `test_the_agent_that_commits_before_the_check_commits_on_a_branch_a_role_committed_on` replays the run. This was rule 11's run, and its `land` merged the role's commit: `test_land_refuses_a_branch_a_role_committed_on_whose_start_no_check_passed` replays that `land` and waits for the script change.
- 9, in 4 steps: `one long-lived process runs both drains`; `drain 1: drain.py clean --all`; `drain 1: drain.py open a`; `drain 2: drain.py clean --all`. The docstring says that "two drains run by one process on one repository, with drain 1 between commands and nothing running from its tree, cannot be told apart". Rule 9a, which allows only that, holds there.
- 9a, in 7 steps: `each drain runs in a session of its own`; `drain 1: drain.py clean --all`; `drain 1: drain.py open a`; `drain 1: drain.py land a, up to the merge.py it runs`; `drain 2: drain.py clean --all --take-over`; `drain 1: a kill stops its land and its merge.py before the merge`; `drain 2: drain.py clean --all`. The owner takes over while drain 1 is alive, and the docstring says that "`--take-over` replaces one whose drain is gone for good". It accepts what follows: "a landing that drain runs keeps its tree while it runs, and the next `clean --all` takes the tree once that landing has ended, its work all committed". The take-over leaves the tree of drain 1's live landing. Once that landing is killed, drain 2's next `clean --all` takes the tree. The kill came before the merge, so it records no merge.
- 9b, in 4 steps: `each drain runs in a session of its own`; `drain 1: drain.py clean --all`; `drain 1: stops, its session still open`; `drain 2: drain.py clean --all`. The docstring says: "A drain is alive while a process of its own is". A drain that stopped with its session open blocks the next until the owner says `--take-over`. Rule 9c, which asks only that a drain whose processes are all gone block nothing, holds.
- 9d, in 3 steps: `one long-lived process runs both drains`; `drain 1: drain.py clean --all`; `drain 2: drain.py report`. The docstring says that `report` "ends the lease of its own drain only (by its token, or when no other drain is alive)", and under one process no other drain is seen alive. In sessions of their own a report ends another drain's lease only when none of that drain's processes is alive.

### What the model found, and the code now does

The model found six defects, and the code was changed until none of them is a run of the model. The breaks that remain are the five above, which the docstring accepts. Each fix has a replay on a real repository:

1. A ticket parked while its fast-forward was cut off merged with no edit (rules 6a and 8). `close` read the ticket from the default branch's work tree, which the cut-off fast-forward left holding the merge's text, part-written, and held the ticket on that text. The next `land` put the text back, so the ticket was held no longer. Now every command first puts back what a killed `merge.py` left, so `close` holds the ticket on the owner's text and `land` refuses it (`test_a_close_after_a_cut_off_fast_forward_puts_it_back_first_and_holds_the_ticket_on_its_text`).
2. Under one process a second drain's report ended the first drain's lease, and with no lease `rival` looked for no program, so the second drain's `clean --all` ended the first drain's program and took its tree (rule 9a). Now `rival` sees a program running from a queue tree with a lease or with none, and that `clean --all` is refused (`test_under_one_process_a_program_refuses_clean_all_once_a_report_ended_the_lease`).
3. Under one process a second drain's `clean --all`, report or `clean` recorded the merge of a live landing and removed its tree (rule 9f). Now `clean --all` keeps the tree of a live landing (`drain.in_flight`), with `--take-over` or not, and report and `clean` leave a merge alone while its landing runs (`test_one_process_running_both_drains_leaves_a_live_landing_alone_and_it_is_printed_once`, `test_a_take_over_leaves_a_live_landing_and_takes_its_tree_once_the_landing_is_killed`).

The next three came once the lease graph let either drain's agent run `update` and `close` on the ticket of a live landing, before its merge lands and after. `update`, `close` and `split` did not read the landing's lock.

4. The other drain's `close --outcome parked` removed the tree of a landing whose `merge.py` still ran (rule 9f).
5. `close`, and `update` once the merge had landed, wrote the note and removed the tree of a live landing, from either drain (rule 9h). `update` recorded the merge with the reason "the merge landed, and the run that landed it stopped before it said so", which was false: the landing was still running.
6. A `close --outcome parked` while `merge.py` ran held the ticket, and `merge.py`, which reads no note, merged it anyway. The landing's close then wrote merged over the hold (rule 9i).

One guard fixed all three. While a landing of the ticket runs, `update`, `close` and `split` print that it runs, change nothing and exit 3 (`drain.act_on`). The replays are `test_a_close_as_parked_while_the_merge_runs_is_refused_and_the_landing_merges_it`, `test_another_drains_close_as_parked_while_the_merge_runs_is_refused_and_reported_once` and `test_an_update_and_a_close_after_the_merge_are_refused_and_the_landing_records_it`. Two more replays go past the lease graph. In `test_an_update_while_the_merge_runs_is_refused_and_the_landing_waits_as_behind` the default branch moves before the landing. Before the guard, `update` rebased the branch under the landing and dropped its verdict record, so the merge refused it and the landing held it for the owner. Now `update` is refused, the merge refuses the branch as behind, and the landing closes it as waiting, not held. The plan then calls it built and says to `update` it and judge it again. `test_a_split_while_the_merge_runs_is_refused_and_files_no_part` shows `split` refused with no part filed.

Two runs past the lease graph as it was then were fixed the same way, each replayed where a real landing is in flight. A second `land` of the ticket, from either drain, could run the merge again or close the ticket while the first was mid-merge. Now it is refused within about a second, changes nothing and exits 2, and the first landing records its merge once (`test_a_second_land_while_one_is_in_flight_is_refused_and_the_merge_printed_once`). That step is now on the lease graph. A `clean --all` under a live landing whose earlier close had been cut off before its discard finished that close and removed the tree the landing was using. Now it keeps the tree and the mark of a branch whose landing is alive (`test_clean_all_under_a_live_landing_keeps_the_tree_and_mark_a_killed_close_left`).

The last came once the lease graph let a kill stop `land` alone, its `merge.py` running on.

7. `land` started `merge.py` with no lock any command read, so a kill of `land` alone freed the landing lock while `merge.py` judged, checked and landed. Every command of either drain then met no landing, and four rules broke. A `close --outcome parked` held the ticket and removed its tree, and `merge.py`, which reads no note, merged it: the next report printed the ticket parked, and the one after printed it merged (rules 9h and 9i). The same drain's `clean --all`, or another drain's `clean --all --take-over`, removed the tree under the merge in flight (rules 9f and 9h). The landing was unseen from the kill on (rule 9j). A second `land` there started a second merge of the ticket, and an `update` rebased the branch under the merge in flight.

The fix holds three locks of the kernel's, none of them inherited by a child (ADR 0058). `land` holds the landing lock and a private lock, whose file it names to `merge.py` in `RATCHET_LANDING`; `merge.py` holds a merge lock on the branch for its whole run, and probes the private lock to learn whether its `land` is alive. `drain.landing_holder` reads the landing lock and the merge lock, so a landing is seen while its `land` or its `merge.py` past the probe is alive, and a `merge.py` before its probe stops once it finds its `land` gone. `merge.py` only reads the private lock file: it never creates, changes or removes it, and the file of a `land` that is gone stays until a `clean` sweeps it. A variable that names anything but a regular private lock file directly in the repository's git directory makes `merge.py` print `merge: RATCHET_LANDING names no landing of this repository; nothing was merged` and return 2, merging nothing; `hooks/tests/test_merge.py` tests it. On the code before the fix the replays below were each a break; now each is refused or left alone:

- `test_a_close_update_or_split_while_a_killed_lands_merge_runs_is_refused_and_it_lands_once`: `close --outcome parked` from either drain, `update` and `split` print the merge-only line and exit 3, changing nothing and filing no part. Once the merge lands, a report records it once and prints it once, and no note holds the ticket.
- `test_clean_all_while_a_killed_lands_merge_runs_keeps_its_tree_and_it_lands_once`, from the same drain and by `--take-over`: `clean --all` keeps the tree with ``has a merge running``. The report after the merge records it, removes the tree and prints it once.
- `test_a_second_land_while_a_killed_lands_merge_runs_is_refused_and_closes_nothing`, from either drain: `land` prints ``land: a merge of `<branch>` is running in another command; run land again once it ends``, exits 2 and changes nothing. One merge lands.
- `test_a_land_killed_before_its_merge_probes_it_merges_nothing_and_a_close_then_holds_it`: `land` is killed while its `merge.py` waits to start. No lock is held. Another drain's `close --outcome parked` holds the ticket, and a `clean` sweeps the private lock file. The `merge.py` then prints `merge: the land that started this merge has stopped; nothing was merged; run land again` and returns 2; nothing is merged. A later `land` is refused as held, and the report prints the ticket parked, not merged.
- `test_two_lands_across_the_instant_land_once_under_the_live_land_or_close_nothing`: a second `land` starts in that instant. In either order of the two `merge.py`, the first stops at its probe and the second lands under its live `land`, which records it. When the first holds the merge lock past the second's tries, the second prints ``merge: a merge of `<branch>` is already running; nothing was merged``, and its `land` prints ``land: `<command>` stopped (exit 2); run land again to finish it``, exits 2 and closes nothing; the next `land` lands it.
- `test_a_process_the_merges_check_or_shell_leaves_running_does_not_keep_the_landing_seen`: a process that the merge's `check`, or the shell of the merge entry point before `merge.py`, leaves running holds none of the three locks. Once `land` ends, or its `merge.py` past the probe ends, no landing is seen, and the merge is recorded and printed once.

The merge lock has a side effect. The owner's own run of the merge entry point on a ticket's branch holds it too, so the drain sees a merge running and refuses the ticket's commands with the merge-only lines. `test_an_owners_hand_merge_is_seen_as_a_merge_running_and_one_merge_lands` replays it three ways. With the hand merge first, `update`, `close` and `land` are refused and the next `land` records the owner's merge. With `land`'s `merge.py` past its probe first, the hand merge prints ``merge: a merge of `<branch>` is already running; nothing was merged`` and returns 2, and `land` closes its own merge. With the hand merge taking the lock while `land`'s `merge.py` waits to start, that `merge.py` meets the lock and stops, and `land` closes nothing.

### The rules in the modules

`exports()` writes six modules. Four carry the rules, each as a rule on a step. TLA+ cannot see a step that leaves every variable as it was, so a module holds each state once for each set of step rules the step into it broke. The rules are computed in Python, so TLC checks that a module carries them and finds the shortest runs to them. It does not prove them a second way.

The whole main graph is too large to export on every test run: at 12,863 states its module was 15 MB, and writing it took about 8 seconds. So TLC checks the main rules on three runs of it. Each run is the part of the main graph its start reaches by some of the model's own steps, so each of its states and steps is one of the graph, and a rule breaks on a step of it exactly where it breaks on that step of the graph.

- `DrainLanding`, the landing run: every step but those on `c`, a role's commit, a check that cannot read, the upgrade, the `Flag:` line and the steps no run takes, with the agent keeping the skill's order. Ticket `a` is built, checked, judged, landed or parked, with every kill of `update`, `land` and `merge.py`, and `b` waits on it. It reaches 442 states and 4,567 steps. No step rule breaks, so the module has as many.
- `DrainWork`, the work run: the steps on `c` and on the queue but the upgrade, and parking `a`, which lets the cap start `c`. The agent leaves work uncommitted, git locks the index, and kills stop `close`. It reaches 240 states and 2,337 steps. One step rule breaks, so the module has 270 states and 2,643 steps.
- `DrainTicketLife`, the life run: every step but those on `c`, the kills and the steps no run takes. Ticket `a` is opened, built by the agent or a role, checked, judged, landed or parked, the upgrade refuses it until the owner lands its `Flag:` line, and `b` waits on it. The agent may commit before the check. It reaches 419 states and 3,905 steps. One step rule breaks, rule 11b, so the module has 434 states and 4,048 steps.
- `DrainLease`: the whole lease graph, 4,583 states and 60,124 steps. Four step rules break, so the module has 5,207 states and 71,616 steps.

No run takes a split, a check that finds only a rebase left unfinished, or an `update` whose check cannot read, so that the life run stays a module the statechart tool draws whole. Only the search in the tests reaches them.

Together the three runs reach 948 of the main graph's 49,115 states and 9,539 of its 682,505 steps. TLC does not check a step where both `a` and `c` have gone further than that: `c` building while `a` is built, judged or landed, `b` opened while `c` is, or `update` and `land` on `c`. So rule 1, the cap, never meets two trees there. No run takes a split, so no module holds `d` or `e`, and rules 7c and 7d hold on each module only because nothing there is split. The search in the tests proves every rule on every state and step of the whole main graph, and gives each break the same shortest run as TLC, and the only run to the breaks of 7c and 7d.

The whole main graph was checked by TLC once, by hand, on the model before a role's commit was a step, when it had 12,863 states. `model_tla.export` wrote all of `drain_model.graph()` with the same step rules as one module of 15,244,560 bytes. No test writes that module, so this page names no SHA-256 for it. With every rule but 10, TLC reached 13,560 states and counts 200,583 states generated, and found no error. The module has more states than the graph's 12,863 because it holds a state once more where a step into it broke rule 10, as `DrainWork` does. Run with rule 10 alone, TLC breaks it by the same 5 steps as above. The run took 16 minutes on four workers and 6 GB of memory, so no test repeats it. The main graph has changed since, with rules 7a to 7d, 11a and 11b, the split and the check, and this run has not been repeated on it: run it again, with rules 7c, 7d and 11b each alone as well.

## The real scripts against the model

The tests hold the real `drain.py` and `merge.py` to the model in four ways:

- every pinned run above, and the steps of past defects on the way, are replayed on a real repository, which must read back as the model says after each step new to the run;
- each kind of kill is taken where it first leaves a state of its own, on a landed ticket and on one not landed: 10 runs, each followed by a report and the killed command run again;
- random runs of 8 steps take the model's steps on a real repository, and compare after each one;
- two real drains, as two sessions or under one process, take every decision the code makes on the lease and on a live landing, the refusals by either lock included: 78 decisions in 11 walks of 149 steps, with 17 landings. A `land` in flight runs as a process of its own, and its `merge.py` is held before its merge lock, at its check, or once done. A kill of `land` alone is SIGKILL to its pid. After each step the test reads the landing lock and the merge lock itself, and the merge lock must be held exactly where the model has a `merge.py` past its probe whose `land` is gone. Each pinned lease run is replayed too.

Where a real command and the model disagree, the test prints the run and what each says.

## Charts

The main rules were checked on `DrainLanding.tla`, SHA-256 `3190a15e22450e6a15ccf8f40570bd19b597852f1ea78615123f4e6fa94c9bdd`, `DrainWork.tla`, SHA-256 `16990b08c9f0af3febda912ed271b0b9643e50c6783616d855241756bc99b67b`, and `DrainTicketLife.tla`, SHA-256 `2a337c0d470c22ccf034e2a1ee8b4db47f8bec7aa2fd144abdee98767c617612`. On `DrainLanding` TLC reached 442 states. Run again with every rule, it counts 4,568 states generated: the start and 4,567 steps. Every main rule holds there. On `DrainWork` TLC reached 270 states, and run again with only the rules that hold, it counts 2,644 states generated: the start and 2,643 steps. Rule 10 breaks there, by the run above, and the others hold. On `DrainTicketLife` TLC reached 434 states, and run again with only the rules that hold, it counts 4,049 states generated: the start and 4,048 steps. Rule 11b breaks there, by the run above, and the others hold.

The lease rules were checked on `DrainLease.tla`, SHA-256 `d8dcc245916be657678b56ad5471ff8d97346db4904020e1b9b0b9fa5fbd46a6`. TLC reached 5,207 states. Run again with only the rules that hold, it counts 71,617 states generated: the start and 71,616 steps, as many as the module has. Rules 9c, 9e, 9f, 9g, 9h, 9i, 9j, 9k, 9l and 9m hold. Rules 9, 9a, 9b and 9d break, each by the run above. Once both drains' sessions have ended no step is left, so the module tells TLC that a state with no step out is no deadlock.

The charts of a kill are drawn from `DrainLanding.tla`, the chart of uncommitted work from `DrainWork.tla`, and the charts of a role's commit and of the upgrade from `DrainTicketLife.tla`. Four charts are drawn from summaries, each the graph seen through the few values the chart shows. `DrainLife.tla`, SHA-256 `f5a651f2ab3e6cfbb6ea159918dcdf7d337aeb5b6287bfb5bcf330954e3c457d`, is the life run, seen through what the plan and the note say of ticket `a`. `DrainLeaseShown.tla`, SHA-256 `4284a6774bcfbb061fe332b5cf5d0abc316a8d67b6a85f9225fa3d75d1baa6ba`, is the whole lease graph seen through how the drains run, the lease's holder and whether it drains, has stopped or is gone, the landing, the merge, and a `merge.py` whose `land` was killed before its probe or after. It leaves out the other drain's session and whether it drains, which no chart reads, so that it stays under the 500 states the statechart tool draws. A box groups states, and an arrow from a box leaves every state in it.

### One ticket's life

Drawn from `DrainLife.tla`, where TLC reached 346 states and 3,277 steps: 9 states and 22 arrows. It does not draw the 2,576 steps that stay in one state. A role's commit takes the same arrow as the agent's: to the drain both are a commit on the branch. A ticket is held when `close` parks it or `land` refuses it, and only the owner's edit frees it. `check`, and the check `update` runs, park a branch that moved from its start, and a check that cannot read holds the ticket once it has waited its retries. After the upgrade the preflight refuses a ticket with no `Flag:` line, and the owner's landing of the line frees it as an edit does. The upgrade does not reach a ticket that has a work tree, and `clean --all` then brings it to the refusal. A branch judged on an older tip goes back through `update`, and only a branch judged on the tip, whose start a check passed, lands.

```mermaid
---
title: "One ticket's life, from buildable to merged and reported"
---
stateDiagram-v2
  s0: Buildable
  s1: Opened, nothing committed
  s2: Held until its text changes
  s3: Refused for its Flag line
  s4: Committed, no verdict record last
  s5: Judged on the tip
  s6: Judged on an older tip
  s7: Merged, the next report prints it
  s8: Merged and reported
  [*] --> s0
  s0 --> s1: open
  s0 --> s2: close as parked or land refuses it
  s0 --> s3: an upgrade asks the Flag line
  s1 --> s0: clean --all
  s1 --> s3: clean --all
  s1 --> s4: the agent commits or a role commits
  s1 --> s2: close as parked or land refuses it
  s2 --> s0: the owner edits the ticket or the owner lands its Flag line
  s2 --> s3: the owner edits the ticket
  s3 --> s2: close as parked or land refuses it
  s3 --> s0: the owner lands its Flag line
  s4 --> s5: the agent commits the verdict record
  s4 --> s2: check or close as parked or update or land refuses it or the check cannot read
  s5 --> s2: check or close as parked or update or the check cannot read
  s5 --> s6: the owner edits the ticket
  s2 --> s4: the owner edits the ticket
  s4 --> s6: the agent commits the verdict record
  s2 --> s6: the owner edits the ticket
  s6 --> s2: check or close as parked or update or the check cannot read or land refuses it
  s5 --> s7: land
  s6 --> s4: update
  s7 --> s8: report
```

### A role's commit on the branch

Drawn from `DrainTicketLife.tla`, where TLC reached 434 states and 4,048 steps: 7 states and 13 arrows. It does not draw the 3,596 steps that stay in one state. A role commits before the run returns. The check after the run, or the one `update` runs, finds the branch moved from its start and parks it, and only the owner's edit frees it. A check of a branch no role committed on passes its start, and only then does `land` merge it. Where the agent records its verdict before the check, it commits on the role's commit, which is where rule 11b breaks; the check parks that branch too. `clean --all`, or a check that cannot read, removes the tree and leaves the start unpassed, and the next check makes the tree again and parks the branch.

```mermaid
---
title: "A role's commit on the branch, and the publish check of ADR 0060"
---
stateDiagram-v2
  state "Legend" as legend {
    lg0: Breaks Rule11b in some cases
    lg1: Path to the Rule11b bug
  }
  class lg0 mayBreak
  class lg1 bugPath
  s0: No role has committed
  s1: No role has committed, the check passed
  s2: A role committed, the run returned
  s3: A role's commit judged, unchecked
  s4: A role committed, its tree gone
  s5: Held, a role's commit on it
  s6: Merged
  [*] --> s0
  s0 --> s0: open
  s0 --> s1: check or update
  s0 --> s2: a role commits
  s2 --> s3: the agent commits the verdict record
  s2 --> s4: clean --all or the check cannot read
  s2 --> s5: check or close as parked or update or the check cannot read
  s1 --> s0: update or open
  s3 --> s4: clean --all or the check cannot read
  s3 --> s5: check or close as parked or update or the check cannot read
  s4 --> s5: check or close as parked or update or the check cannot read
  s5 --> s4: the owner edits the ticket
  s1 --> s6: land
  s0 --> s5: a role commits
  classDef mayBreak fill:#fff3e6,stroke:#e8730c,stroke-width:2.5px,color:#b85a06
  class s0 bugPath
  class s2 bugPath
  class s3 mayBreak
  class s3 bugPath
  class s5 mayBreak
```

### The upgrade and a ticket with no `Flag:` line

Drawn from `DrainTicketLife.tla`: 6 states and 16 arrows. It does not draw the 3,807 steps that stay in one state. The upgrade that ships ADR 0061 makes the preflight ask every ticket for its `Flag:` line, before deployment as after. The plan refuses `a`, which has none, and `b` with it, since `b` waits on a ticket the drain cannot merge (rule 7). `open` refuses both (rule 7a). The owner's landing of `a`'s line releases `a`, and `b` waits on it again (rule 7b). Once `a`'s branch holds a commit the preflight does not read it again, so an upgrade after that refuses nothing. The marks are rule 11b's, which this run also reaches.

```mermaid
---
title: "The upgrade, a ticket with no Flag line, and the ticket that waits on it"
---
stateDiagram-v2
  state "Legend" as legend {
    lg0: Breaks Rule11b in some cases
    lg1: Path to the Rule11b bug
  }
  class lg0 mayBreak
  class lg1 bugPath
  s0: Buildable, no line asked
  s1: Refused for its Flag line, b refused with it
  s2: Opened
  s3: Held until its text changes
  s4: Buildable, its line landed
  s5: Built, so the preflight reads it no more
  [*] --> s0
  s0 --> s1: an upgrade asks the Flag line
  s0 --> s2: open
  s0 --> s3: close as parked or land
  s1 --> s4: the owner lands its Flag line
  s1 --> s3: close as parked or land
  s2 --> s0: clean --all
  s2 --> s3: close as parked or land
  s2 --> s5: the agent commits or a role commits
  s3 --> s0: the owner edits the ticket
  s4 --> s2: open
  s4 --> s3: close as parked or land
  s2 --> s1: clean --all
  s3 --> s4: the owner lands its Flag line
  s3 --> s1: the owner edits the ticket
  s5 --> s5: the agent commits the verdict record
  s2 --> s4: clean --all
  classDef mayBreak fill:#fff3e6,stroke:#e8730c,stroke-width:2.5px,color:#b85a06
  class s0 bugPath
  class s2 bugPath
  class s5 mayBreak
  class s5 bugPath
```

### A kill of `update`

Drawn from `DrainLanding.tla`, where TLC reached 442 states and 4,567 steps: 3 states and 5 arrows. It does not draw the 4,202 steps that stay in one state. A rebase cut off leaves the tree detached, and the next `update`, `land`, `close` or `clean --all` abandons it. A kill after the rebase leaves the old verdict record last, certifying a tree the branch no longer has, and the next `update`, `land` or `close` finishes from there.

```mermaid
---
title: "A kill of update, and what finishes what it left"
---
stateDiagram-v2
  s0: No rebase left over
  s1: A rebase cut off, the tree detached
  s2: Rebased, the old verdict record still last
  [*] --> s0
  s0 --> s1: a kill stops update mid-rebase
  s1 --> s0: update or clean --all or close as parked or a kill stops update after the rebase or a kill of another command or land
  s0 --> s2: a kill stops update after the rebase
  s2 --> s0: update or a kill of another command or close as parked or land
  s1 --> s2: a kill stops update after the rebase
```

### A kill of `land` after `merge.py`

Drawn from `DrainLanding.tla`: 4 states and 8 arrows. It does not draw the 3,587 steps that stay in one state. Wherever `land` is cut off after the merge, a later command records the merge once and removes the tree.

```mermaid
---
title: "A kill of land after merge.py, and what finishes what it left"
---
stateDiagram-v2
  s0: Nothing of a landing left over
  s1: The note written, the tree still there
  s2: Merged, and no note says so
  s3: The tree gone, the note still marked
  [*] --> s0
  s0 --> s1: a kill stops land before its discard
  s1 --> s0: clean --all or close as parked or land or update or a kill of another command or a kill stops land after its discard or clean
  s0 --> s2: a kill stops land before its note
  s2 --> s0: a kill stops land after its discard or clean or clean --all or close as parked or land or update or report or a kill stops land before its discard
  s2 --> s1: a kill stops land before its discard
  s0 --> s3: a kill stops land after its discard or a kill stops land before its discard
  s1 --> s3: a kill stops land after its discard
  s3 --> s0: close as parked or clean --all or land or update or a kill of another command
```

### A kill of `merge.py`

Drawn from `DrainLanding.tla`: 3 states and 6 arrows. It does not draw the 3,782 steps that stay in one state. A kill before the fast-forward leaves the scratch work tree, and one in the middle of it leaves the default branch's work tree part-written. Every command, and every kill of another command, puts it back first.

```mermaid
---
title: "A kill of the merge.py that land runs, and what puts it back"
---
stateDiagram-v2
  s0: Nothing of merge.py left over
  s1: A fast-forward cut off half-way
  s2: The scratch tree of merge.py left
  [*] --> s0
  s0 --> s1: a kill stops merge.py mid fast-forward
  s0 --> s2: a kill stops merge.py before the fast-forward
  s1 --> s0: clean or open or report or update or a kill of another command or clean --all or close as parked or land
  s1 --> s2: a kill stops merge.py before the fast-forward
  s2 --> s0: clean or open or report or update or a kill of another command or clean --all or close as parked or land
  s2 --> s1: a kill stops merge.py mid fast-forward
```

### Uncommitted work in a ticket's tree

Drawn from `DrainWork.tla`, where TLC reached 270 states and 2,643 steps: 5 states and 15 arrows. It does not draw the 1,917 steps that stay in one state. Uncommitted work behind a stale lock is where rule 10 breaks: `clean --all` cannot stash it, so it leaves the tree with the work in it. The owner removes the lock, and the next `clean --all` or `close` keeps the work as a stash.

```mermaid
---
title: "Uncommitted work in a ticket's tree, and a kill of close"
---
stateDiagram-v2
  state "Legend" as legend {
    lg0: Breaks Rule10 in some cases
    lg1: Path to the Rule10 bug
  }
  class lg0 mayBreak
  class lg1 bugPath
  s0: No tree
  s1: A clean tree
  s2: Work uncommitted in its tree
  s3: Parked, its tree not yet removed
  s4: Work uncommitted behind a stale lock
  [*] --> s0
  s0 --> s0: close as parked
  s0 --> s1: open
  s1 --> s2: the agent leaves work uncommitted
  s1 --> s0: clean --all or close as parked
  s1 --> s3: a kill stops close before its discard
  s2 --> s4: git leaves a stale index lock
  s2 --> s0: clean --all or close as parked
  s2 --> s3: a kill stops close after the stash or a kill stops close before its discard
  s3 --> s0: clean --all or close as parked or land
  s3 --> s1: update
  s4 --> s2: the owner removes the lock
  s4 --> s4: clean --all
  s4 --> s3: a kill stops close before its discard
  s3 --> s2: update
  s3 --> s4: close as parked or land or update or clean --all
  classDef mayBreak fill:#fff3e6,stroke:#e8730c,stroke-width:2.5px,color:#b85a06
  class s0 bugPath
  class s1 bugPath
  class s2 bugPath
  class s4 mayBreak
  class s4 bugPath
```

### The lease

Drawn from `DrainLeaseShown.tla`, where TLC reached 241 states and 4,970 steps: 9 states and 18 arrows. It does not draw the 3,688 steps that stay in one state. The two drains are alike, so a step is named by its drain's part: the lease's holder, or the other drain. In sessions of their own, the other drain takes the lease from a holder that is gone, and from one that has stopped only with `--take-over`. Under one process it takes it from one that has stopped, and its report ends the lease of a holder still draining, which is where rule 9d breaks. The holder's report without its token keeps the lease while a program runs from its tree.

```mermaid
---
title: "The drain lease: who holds it, and when the next drain may take it"
---
stateDiagram-v2
  s0: Not started
  state "Each drain in a session of its own" as s1 {
    s2: No lease
    s3: Its holder drains
    s4: Its holder stopped, its session open
    s5: Its holder is gone
  }
  state "One process runs both drains" as s6 {
    s7: No lease
    s8: Its holder drains
    s9: Its holder stopped, its session open
    s10: Its holder is gone
  }
  [*] --> s0
  s0 --> s2: each drain starts in a session of its own
  s0 --> s7: one process starts both drains
  s2 --> s3: a drain's clean --all
  s7 --> s8: a drain's clean --all
  s3 --> s2: the holder's report
  s3 --> s4: the holder's report while a program runs or the holder stops
  s3 --> s5: the holder's session ends
  s8 --> s7: the holder's report or the other drain's report
  s8 --> s9: the holder's report while a program runs or the holder stops
  s8 --> s10: the process ends
  s4 --> s2: the holder's report
  s4 --> s3: the other drain's clean --all --take-over or the holder's clean --all
  s4 --> s5: the holder's session ends
  s5 --> s2: the other drain's report
  s5 --> s3: the other drain's clean --all
  s9 --> s7: the holder's report or the other drain's report
  s9 --> s8: the holder's clean --all or the other drain's clean --all
  s9 --> s10: the process ends
```

### A landing in flight

Drawn from `DrainLeaseShown.tla`: 7 states and 11 arrows. It does not draw the 4,146 steps that stay in one state. While `merge.py` runs, `land` holds no drain lock, so every other command can run, before the merge lands and after. `land` holds the lock on its own ticket (`ratchet-landing-` and a hash of the branch) from before its checks until it ends, and a `merge.py` past its probe holds its merge lock (`ratchet-merge-lock-` and the same hash) until it ends. By these two locks no other command records the merge or removes the tree while the landing is alive, even once its `land` is killed alone. `report`, `clean` and `clean --all` leave the ticket alone. `update`, `close` and a second `land` are refused and change nothing, so they stay in one state and the chart does not draw them. The landing's own close records the merge. After a kill a report, `clean`, `clean --all`, `update` or `close` records it, and after a kill before the merge the ticket is not landed. A `merge.py` whose `land` was killed before its probe lands nothing, so this chart draws it as not landed; the next chart draws it.

```mermaid
---
title: "A landing in flight, before its merge lands and after"
---
stateDiagram-v2
  s0: Not landed
  s1: Its land runs merge.py
  s2: Merged, its land not yet closed
  s3: Its land killed, its merge.py runs on
  s4: Merged, and no note says so
  s5: Recorded, not yet printed
  s6: Merged and printed
  [*] --> s0
  s0 --> s1: a drain's land
  s1 --> s0: a kill stops the land, or its session ends
  s1 --> s2: its merge.py merges it
  s1 --> s3: a kill stops the land alone past the probe
  s2 --> s4: a kill stops the land, or its session ends
  s2 --> s5: its land closes
  s3 --> s0: its merge.py is killed
  s3 --> s4: its merge.py merges it
  s4 --> s5: a drain's clean, clean --all, update or close as parked
  s4 --> s6: a drain's report
  s5 --> s6: a drain's report
```

### A `land` killed alone

Drawn from `DrainLeaseShown.tla`: 6 states and 15 arrows. It does not draw the 4,594 steps that stay in one state. A kill of `land` alone before its `merge.py` probes it holds no lock, so every command acts as with no landing; the `merge.py` then finds the private lock free and stops. A second `land` in that instant runs a merge of its own, which lands under it, or meets the merge lock of the first `merge.py` past its tries, so that both stop and the second `land` closes nothing. A kill of `land` alone past the probe leaves the merge lock held until its `merge.py` lands the merge or is killed. If that was the second `land`, the first `merge.py` may still be starting, so a kill of the second one's `merge.py` goes back to the first kill. Once the ticket is merged, the chart before this one goes on.

```mermaid
---
title: "A land killed alone, before its merge.py probes it and after"
---
stateDiagram-v2
  s0: Not landed
  s1: Its land runs merge.py
  s2: Merged
  s3: Killed before the probe, its merge.py starting
  s4: Killed past the probe, its merge.py runs on
  s5: A land runs merge.py, the killed land's merge.py starting
  [*] --> s0
  s0 --> s1: a drain's land
  s1 --> s0: a kill stops the land and its merge.py, or its session ends
  s1 --> s2: its merge.py merges it
  s1 --> s3: a kill stops the land alone before the probe
  s1 --> s4: a kill stops the land alone past the probe
  s3 --> s0: the killed land's merge.py finds it gone and stops
  s3 --> s5: a drain's land
  s4 --> s0: its merge.py is killed
  s4 --> s2: its merge.py merges it
  s5 --> s0: its merge.py meets the lock of the killed land's merge.py, and both stop
  s5 --> s1: the killed land's merge.py finds it gone and stops
  s5 --> s3: a kill stops the land and its merge.py, or its session ends
  s5 --> s2: its merge.py merges it
  s5 --> s4: a kill stops the land alone past the probe
  s4 --> s3: its merge.py is killed
```

### Where two drains break a rule

Drawn from `DrainLease.tla`, where the tool draws only the shortest run to each rule that breaks: 13 states and 14 arrows. The states say how the drains run, who holds the lease, and where drain 1's tree and landing are. The steps have the model's own names, as the tests give the runs, with a comma after the drain for the colon.

```mermaid
---
title: "Where two drains break a rule"
---
stateDiagram-v2
  state "Legend" as legend {
    lg0: Breaks Rule9 or Rule9a
    lg1: Breaks Rule9b or Rule9d in some cases
    lg2: Path to the Rule9 or Rule9a or Rule9b or Rule9d bug
  }
  class lg0 breaks
  class lg1 mayBreak
  class lg2 bugPath
  s0: Not started
  s1: Sessions of their own, no lease
  s2: One process, no lease
  s3: Sessions of their own, drain 1 holds the lease
  s4: One process, drain 1 holds the lease
  s5: Sessions of their own, drain 1 holds the lease, drain 1's tree open
  s6: Sessions of their own, drain 1 holds the lease and has stopped
  s7: One process, drain 1 holds the lease, drain 1's tree open
  s8: Sessions of their own, drain 1 holds the lease, drain 1's merge.py runs
  s9: One process, drain 2 holds the lease
  s10: Sessions of their own, drain 2 holds the lease, drain 1's merge.py runs
  s11: Sessions of their own, drain 2 holds the lease, drain 1's tree open
  s12: Sessions of their own, drain 2 holds the lease
  [*] --> s0
  s0 --> s1: each drain runs in a session of its own
  s0 --> s2: one long-lived process runs both drains
  s1 --> s3: drain 1, drain.py clean --all
  s2 --> s4: drain 1, drain.py clean --all
  s3 --> s5: drain 1, drain.py open a
  s3 --> s6: drain 1, stops, its session still open
  s4 --> s7: drain 1, drain.py open a
  s4 --> s2: drain 2, drain.py report
  s5 --> s8: drain 1, drain.py land a, up to the merge.py it runs
  s6 --> s6: drain 2, drain.py clean --all
  s7 --> s9: drain 2, drain.py clean --all
  s8 --> s10: drain 2, drain.py clean --all --take-over
  s10 --> s11: drain 1, a kill stops its land and its merge.py before the merge
  s11 --> s12: drain 2, drain.py clean --all
  classDef breaks fill:#ffecee,stroke:#e72e3b,stroke-width:2.5px,color:#cd2834
  classDef mayBreak fill:#fff3e6,stroke:#e8730c,stroke-width:2.5px,color:#b85a06
  class s0 bugPath
  class s1 bugPath
  class s2 mayBreak
  class s2 bugPath
  class s3 bugPath
  class s4 bugPath
  class s5 bugPath
  class s6 mayBreak
  class s6 bugPath
  class s7 bugPath
  class s8 bugPath
  class s9 breaks
  class s9 bugPath
  class s10 bugPath
  class s11 bugPath
  class s12 breaks
  class s12 bugPath
```

## Left out

- Forge mode. With a remote `land` is not used: the skill opens a pull request and the forge's merge job merges it. So a role's push and a role's pull request are left out too: the role's step here is a commit on the local branch, while ADR 0060's check also compares the remote branch and the open pull request.
- Which role commits, and more than one commit by roles in a run. The publish check runs once per run, not once per role, so one commit stands for any number.
- A ticket an older drain opened, whose note holds no `start`. `land` refuses it, and `check` changes nothing; the way out is `close --outcome waits`, and the next drain's `open` records a start.
- A run that errors, its check and its resume, and a check of a start a check already passed. A role commits here only before its run's check, which [above](#what-the-next-script-change-must-do) says the change must keep true.
- A check of a ticket its note holds, which no run reaches, and the way a rebase is left unfinished: here the check finds one after a rebase that did not conflict.
- More than two drains on the lease graph, and more than one ticket's work tree there.
- More than three tickets besides the parts, a cap above one, a ticket whose run errors, a conflict on rebase or merge, and a red `check`. The judge gives two reasons here: `behind`, which the drain resolves itself, and a verdict record that is not last, which it does not. Its other reasons act as the second.
- A split of `b` or `c`, of a ticket with commits of its own, into more than two parts, or refused and parked as too large; a part split again, which `split.py` refuses; and the parts' other steps: `update`, `close`, the owner's edit and the kills.
- An owner's edit of a split ticket, and a command on it. The edit of an open split ticket frees its hold, and the drain then builds it whole again: `queue_notes.split_into` reads an open ticket's parts only while the note's digest is its text, and a closed ticket's parts whatever its text. A split note with no parts is refused as malformed.
- The instant a local split's parts are written and not yet committed. The checkout then holds work uncommitted, and the plan starts nothing, `not started, the checkout is not fit`. Nothing in the feature skill commits them.
- A forge, where the parts are issues, committing them moves no tip, and closing the split ticket leaves its text, so its note's digest still matches.
- A kill at a point the model does not cut: in `open`, `report` or `clean`, and in `close` after its discard, which leaves what the whole `close` leaves but for the mark that only a retry reads. A kill in `merge.py` before its fast-forward, in the merge or in `check`, leaves its scratch work tree and nothing else, so the model takes one kill there.
- A command refused while a git command still runs in the default branch's work tree, where the cut-off fast-forward left its files, so they cannot be put back yet. The command exits 2, reads no ticket and writes no note. It still sweeps the scratch work tree a killed `merge.py` left. `hooks/tests/test_drain_safety.py` tests it (`test_a_command_is_refused_while_a_cut_off_fast_forward_cannot_be_put_back`).
- The lines ``clean: a landing of `<branch>` is running in another command, so it is left alone`` and ``clean: a merge of `<branch>` is running in another command, so it is left alone``, which `clean` prints for a live landing whose tree is gone. A landing on the lease graph always has its tree.
- A `clean --all` that meets a live landing whose earlier close was cut off before its discard. The lease graph has no mark of a discard under way, and the main graph has one drain. `test_clean_all_under_a_live_landing_keeps_the_tree_and_mark_a_killed_close_left` replays it.
- An `update` while the merge runs on a default branch that has moved. On the lease graph only the merge moves the default branch. `test_an_update_while_the_merge_runs_is_refused_and_the_landing_waits_as_behind` replays it.
- `close --outcome waits` and `close --outcome merged` while a landing runs. The lease graph has only `close --outcome parked` there. `hooks/tests/test_drain_safety.py` tests all three outcomes with the landing's lock held (`test_a_close_is_refused_while_a_landing_of_the_ticket_holds_its_lock`).
- The other ways a command refuses or stops: `update` on uncommitted changes, a tree over the cap, a pushed branch or a rebase git cannot abort; `land` when the merge entry point exits with neither 0 nor 1 for a reason other than the merge lock, where it closes nothing and says to run it again; `close --outcome merged` typed by hand on a branch that is not merged; the 30 seconds a command waits for the drain's lock before it is refused.
- The instant a `merge.py` whose `land` was killed before its probe holds the merge lock and has not yet stopped. The graph folds taking the lock, the probe and the stop into one step. In that instant a command is refused once with the merge-only line, and a second `land` once, as ADR 0058 accepts. The graph keeps its effect on a second `land`'s `merge.py`, which can meet that lock past its tries; `test_a_merge_that_meets_a_held_merge_lock_stops_and_its_land_closes_nothing` in `hooks/tests/test_drain_safety.py` tests a merge that meets it.
- The instant between the fast-forward of a `merge.py` past its probe and its exit, when the merge has landed and the merge lock is still held. The graph lands the merge and ends that `merge.py` in one step. There `report` prints ``report: a merge of `<branch>` is running in another command, so it is left alone``; `test_a_report_says_a_merge_whose_land_was_killed_alone_is_left_alone` in `hooks/tests/test_drain_safety.py` tests the line.
- A `clean` that removes a `land`'s private lock file between `land` creating it and locking it. The `merge.py` then finds the file gone and stops, having merged nothing, and `land` closes nothing and says to run it again. ADR 0058 accepts it. Nothing wrong lands.
- A session's end and a `merge.py` whose `land` was killed alone. The model takes that `merge.py` to outlive the session, as a process the session did not wait on can. A session's end that also ends it is the kill of `land` and its `merge.py` together.
- An owner's own run of the merge entry point on a ticket's branch, as a step of either graph. It holds the merge lock, so the drain sees it as a merge running; `test_an_owners_hand_merge_is_seen_as_a_merge_running_and_one_merge_lands` replays it.
- Two probes at one instant. A probe takes a lock exclusively for an instant. `update`, `close`, `split`, `report` and `clean` probe under the drain's lock, and `land`'s first probe does not, so `land`'s can meet one of theirs. Then that command reads a landing and refuses once with exit 3, or leaves the ticket alone once, or `land` reads a merge and refuses once with exit 2. It fails closed, and the next `plan` goes on.
- `split`, as a step of the lease graph. `test_a_split_while_the_merge_runs_is_refused_and_files_no_part` replays it while a landing runs, and `test_a_split_is_refused_while_a_landing_holds_its_lock_and_files_nothing` in `hooks/tests/test_drain_safety.py` tests it with the landing's lock held.
- The processes `discard` ends when they run from a tree it removes.
- A `merge.py` killed after it took its scratch lock and before it made the work tree, and one killed after the fast-forward and before it exits.
- A hold that ends because its cause landed: a path the run named, or a safeguard or review it owed. In the model only the owner's edit frees a held ticket.
- How a process is known to be alive. The scripts read `/proc`; where it is missing no other drain is seen.
- Whether the sweep of an empty merge scratch directory leaves what the model says. The replays do not compare it.
- A second drain between `settle`'s note and its discard. `settle` runs whole under the drain's lock, so no other drain's command runs there.
- `merge.py --records`, which lands the owner's records with no ticket and takes no merge lock.
- What the plan reads besides the tickets: the preflight but a missing `Flag:` line, a checkout not fit to build on, the agents a drain may start, and the report's text.
- A `Flag:` line on a ticket's spec, a split's parts, and a ticket on a forge's tracker. Here the owner lands the line on the ticket itself, so the landing changes its text and frees a hold. A line landed on the spec frees no hold of the ticket's.
- The merge judge's reasons of ADR 0061: `unflagged` on new surface before deployment, the key a yes names, and the full closure, under which a change that adds surface under the owner's no waits for the owner while an open labelled ticket reads a yes. The model abstracts the judge, as the record says. To the drain each is a reason it does not resolve itself, so `land` holds the ticket, and only the owner's edit frees it here.
- A project that has declared `deployed`, where the preflight asks the `Flag:` line already, before the upgrade.

The real scripts agree with the model on the runs above, the kills, the random runs and the lease walks. They are not compared on every step of either graph.

## What the next script change must do

The publish check of ADR 0060 is on the main graph, and not yet in `drain.py`. `scripts/publish_check.py` is on the default branch, and nothing calls it. The model says what the change must do:

- **The start.** `open` writes `publish_check.start_of(tree, branch, gh)` into the note as `start`, and prints it in its JSON, with `forge: null` where there is no forge. It writes no `passed`. `open` makes the branch at the default tip again, so it never keeps a start a run left.
- **`check <ticket>`.** It runs under the drain lock, and is refused like `update` while a landing runs. A branch with commits of its own and no work tree gets one under the cap, as `update` makes one: a session that dies after its run leaves the branch, and the next drain's `clean --all` removes the tree. Then it runs `publish_check.check` in the tree against the note's start. Green prints the check's green line, marks the start `passed`, and exits 0. A branch that moved closes the ticket as parked with the reason `a role published: <the line of each move>`, its work stashed as `<branch>: blocked run`, held until the ticket's text changes and never retried, and exits 1. Only a rebase left unfinished closes it as waiting with the conflict close's reason, marks the start `passed`, since that check read that nothing moved, and exits 3. A present state it cannot read closes it as waiting with the check's line, with no mark, and exits 3. That line must start with a code in `queue_notes.MECHANICAL`, or the close holds the ticket in place of retrying it. With no `start` in the note it says why, changes nothing and exits 2.
- **`update`.** On a branch with commits of its own whose start is not passed, once it has a tree and before it rebases, it runs the check as `check` does, and parks a branch that moved. On green it marks the start `passed` before the rebase begins. A rebase that moves the branch, and one that finishes what a killed `update` left, then record the new head as the start, with no mark; after a conflict the start is `start_of(..., rebase_onto=base)`. `moved: false` changes nothing. When its check cannot read the present state, `update` is refused with exit 3 and closes nothing.
- **`land`.** After it refuses a held ticket and before `merge.py`, it refuses a start not passed, and a note with no start, with exit 3 and "run `D check <ticket>` first".

The feature skill runs `check` after every run returns, before any close or commit, and its exit decides.

The model showed five of these, each by a rule that broke on a draft of it:

- `update` checks only a branch with commits of its own. A check of a branch with none marked its start, and a kill of `update` mid-rebase then left that mark on a branch whose tree `clean --all` removes, where neither the whole `update` nor none of it leaves one. Rule 8 broke by `drain.py open a`; `the owner edits ticket a`; `a kill mid-rebase stops drain.py update a`. A branch with no commit of its own holds none a role made, and its rebase records a new start.
- `update` marks the start `passed` before the rebase begins. Without the mark a run killed after the rebase leaves the branch rewritten under the old start, and the next `update` checks it and reads the rebase as a role's move.
- `update` is refused when its check cannot read. When it closed the ticket as waiting, as `check` does, it ended the hold of a parked ticket with no edit, and rule 6 broke by `drain.py open a`; `the agent commits a`; `drain.py check a`; `the owner edits ticket c`; `the check of drain.py update cannot read the present state of a`.
- `check` makes a tree only for a branch with commits of its own. When it made one for a branch with none, it opened a ticket the plan refuses, and rule 7a broke by `drain.py open a`; `drain.py check cannot read the present state of a`; `an upgrade makes the preflight ask the Flag: line before deployment`; `drain.py check a`. A branch with no commit and no tree is buildable, and `open` records a new start.
- The check after a run that errored must not mark the start. The skill runs it before a resume, and a role can commit in the resume. With a role free to commit after a check passed, rule 11a broke in 5 steps, the agent keeping the skill's order: `drain.py open a`; `drain.py check a`; `a role of the pipeline run commits in a`; `the agent commits the verdict record of a`; `drain.py land a`. The skill's check after the resume reads the role's commit, but a session that dies first leaves the mark for `land`. The model holds no resume, so the change must say how: `check` takes a flag that the run errored and then marks nothing, or the resume drops the mark. The form that fails safe marks only on a flag that says the run is over: a check that leaves the flag out marks nothing, and `land` then refuses and names the check. Nor may a check of a start already passed be skipped: it is what reads a resume's commit. The skill never runs one after its own commits.

These tests wait for the change. Each is marked as an expected failure, strict, so it turns red the day it passes, and the change removes the mark:

- `test_land_refuses_a_branch_a_role_committed_on_whose_start_no_check_passed`: rule 11b's run, then `land`, which today merges the role's commit;
- `test_check_records_the_start_and_marks_it_passed_in_the_note`: `open` prints the start and writes it in the note, and `check` marks it;
- `test_a_check_after_a_run_that_died_makes_the_tree_and_parks_a_branch_a_role_committed_on`: `check` after `clean --all`;
- `test_update_checks_a_start_no_check_passed_and_parks_a_branch_a_role_committed_on`: `update`'s own check;
- `test_a_check_that_cannot_read_leaves_the_start_unpassed_and_the_retrys_update_checks_it`: the close is retried, and its retry's `update` checks;
- `test_update_is_refused_when_its_check_cannot_read_the_present_state`.

Until the change, `drain_world.py` records the start where `open` and a moving `update` are to record it, and stands in for `check` where the ticket has a tree: it runs `publish_check.check` itself, and closes a branch that moved, or a rebase left unfinished, with the real `close`. So the check's own verdicts are held to the model now, by `test_the_check_parks_a_branch_a_role_committed_on_and_holds_it_until_its_text_changes`, `test_a_moved_update_records_a_new_start_and_its_rejudge_lands_once_checked_again` and `test_a_check_that_finds_only_an_unfinished_rebase_passes_the_start_and_the_ticket_waits`, and by every replay whose run takes `drain.py check`. The random runs leave out the steps the change adds, by `taken_today`. Once a note holds `start`, the world runs the real `check` and reads the mark from the note, and `taken_today` lets every step in.

Rules 7a and 7b hold on the code as it is: `drain.py` already refuses to open a ticket the preflight refuses, and lifts the refusal of a ticket that waits on one once that one is buildable again. What they need of a script change is the preflight's, which the model reads only as a refusal: ask a ticket of an asked type for its `Flag:` line before deployment as after (ADR 0061). The tests stand in for that preflight. After the upgrade step they run the real `drain.py` with the preflight's `operability_asked` answering the `Flag:` line alone.

## Changing the drain

Change the model first, then the code, then the page.

1. Change `hooks/tests/drain_model.py` so it says what the code should do. `hooks/tests/test_drain_model.py` says whether each rule still holds or breaks on its pinned run, and each pin changes only when the change says why.
2. Change `scripts/drain.py` or `scripts/merge.py`. The replays and the random runs say where the code does something else.
3. Draw the page again. The page test is red until the page names the SHA-256 of each current export.

To draw it again, from the `hooks` directory write the exports to a directory outside the checkout:

```sh
mkdir -p /tmp/drain-model && python3 -c 'import sys; from pathlib import Path; from tests import drain_model
for e in drain_model.exports():
    Path(sys.argv[1], e.name + ".tla").write_text(e.module)
    Path(sys.argv[1], e.name + ".cfg").write_text(e.config)' /tmp/drain-model
```

Then, as [the models' README](README.md#drawing-the-charts-again) says, run the `statechart` command once on each of `DrainLanding.tla`, `DrainWork.tla`, `DrainTicketLife.tla` and `DrainLease.tla` with no view for the rules, run TLC directly with only the rules that hold for the counts, and once for each rule that breaks with one worker. Draw each chart from its module and view in `docs/model/drain/`, as the chart's heading says. Put each `chart.mmd` in its block, the counts beside it, and each SHA-256 on the line that names it.

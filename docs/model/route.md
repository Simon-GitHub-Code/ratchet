# The route

`scripts/route.py` reads the checkout and names the one command the owner types next. [The route](../../skills/method/route.md) says what it names in each case. This page writes that down as a state machine: the states the route can tell apart, what it names in each, the steps that lead from one state to the next, and the rules the steps keep.

The model is `hooks/tests/route_model.py`. The tests in `hooks/tests/test_route_model.py` prove the rules on it, take each of its steps on a real repository, and ask the real route after each.

## Scope

The model covers one repository with:

- no forge: no `origin`, tickets in the local tracker, the route asked offline;
- one spec at a time, split into two tickets, the second blocked by the first;
- one step at a time: a step on a spec or a ticket starts from a clean default branch with no records branch waiting;
- one checkout: no other work tree holds the default branch;
- one upgrade that ships ADR 0061 to a project the `Flag:` line is asked of and that is not deployed, from a plugin that already asks the architecture. A part of a split and a ticket on a forge's tracker are out of it, since the model has neither.

What it leaves out is listed [at the end](#left-out).

## State

| Part | Values |
| --- | --- |
| Setup | nothing; an init that stopped before its questions; init done; the tracker set up; an adoption that stopped before it wrote the plugin pin; adopted |
| Commit hooks | installed, or not |
| Checked out | the default branch; a working branch; a working branch the default branch already holds; a `records/<time>` branch |
| Work tree | clean; records only; more than records |
| A records branch waits for the owner's merge | yes or no |
| The glossary and the design brief | written, or not |
| A spec not yet split | none; written; stating its conjecture |
| Each of the two tickets | none; needs triage; agent-ready; agent-ready with a conjecture; held by a blocked run's stash; parked by the drain for open questions, for guard edits, for an owed safeguard, or by a note older than the kinds of hold or than the base commit a guard hold needs; built on its branch; merged but open; resolved |
| The tickets inherit the spec's conjecture | yes or no |
| A safeguard is owed | yes or no |
| An architecture review is owed | yes or no |
| An upgrade left rows an agent fills | yes or no |
| The architecture | never asked, as before adoption and after an adoption by a plugin from before ADR 0049; owed since an upgrade; recorded |
| The `Flag:` line | waits for `deployed`, as before the upgrade that ships ADR 0061; missing from the spec in flight and its tickets, which state their conjecture; asked, the conjecture writing it on the spec |

From an empty directory the model reaches 2210 states and 5798 steps. Nine of the states are at rest: adopted, nothing unfinished, nothing owed, and every ticket resolved or none written. In three the architecture is recorded, in three it was never asked, and in three it is recorded and the `Flag:` line is asked. At rest the route names the next spec.

A working branch the default branch already holds has no commit of its own to merge. A merge leaves it checked out when it is cut off before it switches, when it cannot switch, or when it is made from another checkout. A merge keeps the branch it merged, so it can be checked out again. A build that stops on the branch it cut, before it wrote anything, leaves one too: the branch is the default branch's tip. A commit on it makes it a working branch with work to merge again. In 282 states it is checked out clean, and in 9 with code uncommitted.

The architecture takes three values because each opens other steps. `/ratchet:adopt` asks it and records it (ADR 0049), so it is recorded from the adoption on. An adoption by an older plugin asked nothing, and the route reads nothing of that: it says the same as where the architecture is recorded. They part at an upgrade, which owes the architecture where it was never asked and owes nothing more where it is recorded. While it is owed the route names `/ratchet:architecture`, and recording it is the only step that ends that. In 1554 states the architecture is recorded, in 466 it was never asked, and in 190 it is owed.

The `Flag:` line takes three values (ADR 0061). Before the upgrade that ships the decision, the line waits for `deployed`, and nothing the route names turns on it. The upgrade moves no ticket. Where a record in flight states its conjecture and no line, the line is missing: the spec states its conjecture, a ticket inherits it, or a ticket states its own and is not yet built. A labelled ticket then reads as one with no conjecture. The route names `/ratchet:conjecture <ticket>`, and no feature run and no drain starts on it. The release of a hold, and the agreement of a ticket that inherits from the spec, lead there too. A built ticket goes on to its merge, since no preflight reads it again. The conjecture writes the answer and the line on the spec, which each ticket reads, so one conjecture lands the line for both tickets. Once it is on the spec, or every ticket that lacked it is closed, the line is asked: the conjecture writes it on every later spec. In 1445 states the line waits, in 267 it is missing, and in 498 it is asked.

ADR 0061 has the upgrade send each stated ticket back to agent-ready and stop the tickets inheriting until the spec states the line. The model keeps each ticket's state and reads the line beside it instead. A ticket sent back to agent-ready forgets whether it states its own conjecture, and that decides whether it is stated once the line lands on the spec. The route names the same step either way.

The upgrade arrives only where the architecture is recorded. An upgrade from a plugin before ADR 0049 is the model's other upgrade, which arrives at rest and owes the architecture.

## What the route names

The route asks in this order, and the first case that applies is its answer. Each answer is a label. There are 28 labels; `safeguard` is named in two places.

| Label | When | What it names |
| --- | --- | --- |
| `init` | no setup, or an init that stopped | `/ratchet:init` |
| `tracker` | init done | `/mattpocock-skills:setup-matt-pocock-skills` |
| `adopt` | the tracker is set up, or an adoption stopped before the pin | `/ratchet:adopt` |
| `hooks` | the commit hooks are missing | the hooks install, which an agent runs |
| `finish-records` | a `records/<time>` branch is checked out | `just merge --records`, which finishes the stopped run |
| `land-records` | records alone are uncommitted on the default branch | `just merge --records` |
| `commit` | anything else is uncommitted | `just check`, then a commit on a branch |
| `switch` | a working branch the default branch already holds is checked out | `git switch <default>` |
| `merge` | a working branch is checked out | `just merge <branch>` |
| `merge-records` | a records branch waits | the owner's merge of it |
| `fill` | an upgrade left rows an agent writes from the repository | `/ratchet:fill` |
| `review` | an architecture review is owed | `/ratchet:architecture` |
| `safeguard` | a safeguard is owed | `/ratchet:safeguard` |
| `architecture` | an upgrade left the architecture owed | `/ratchet:architecture`, offering beside it the step it would otherwise name |
| `grill` | no glossary or design brief | `/mattpocock-skills:grill-with-docs` |
| `triage` | no agent-ready ticket, and an unblocked one needs triage | agree that ticket |
| `conjecture-spec` | no agent-ready ticket, and a spec with no conjecture | `/ratchet:conjecture` on the spec |
| `split` | no agent-ready ticket, and a spec with its conjecture | `/mattpocock-skills:to-tickets` |
| `specify` | no agent-ready ticket | `/mattpocock-skills:to-spec` |
| `resolve` | a merged ticket is still open | mark it resolved |
| `blocked` | every agent-ready ticket is blocked | finish the blocking ticket first |
| `blocked-run` | the first unblocked ticket is held by a blocked run | answer what the run asked, or land its guard edits |
| `answer` | ... parked by the drain for open questions | answer them on the ticket |
| `land-guard` | ... parked for guard edits | land the guard edits on the default branch |
| `safeguard` | ... parked for an owed safeguard | `/ratchet:safeguard` |
| `change-ticket` | ... parked by a note older than the kinds of hold or the base commit | change the ticket |
| `drain` | an unblocked ticket states a conjecture, and so does another, each with its `Flag:` line where it is asked | `/ratchet:feature` with no ticket |
| `build` | an unblocked ticket states a conjecture, and its `Flag:` line where it is asked | `/ratchet:feature <ticket>` |
| `conjecture` | any other case, a labelled ticket whose `Flag:` line is missing among them | `/ratchet:conjecture <ticket>` |

## Steps

42 steps change the state, and one of them can also leave it as it was. The owner's and an agent's steps each follow one label. The others are things that happen: a command that stops part-way, a run that blocks, a park, an edit.

| Step | Follows |
| --- | --- |
| you run `/ratchet:init` | `init` |
| init stops before its questions | |
| you set up the tracker | `tracker` |
| you run `/ratchet:adopt`, which records the architecture | `adopt` |
| an adoption by a plugin from before the architecture was asked | |
| the adoption stops part-way: on its branch before the pin, committed; or after the pin, uncommitted | |
| an agent installs the hooks | `hooks` |
| you write the glossary and the brief, uncommitted | `grill` |
| you write a spec | `specify` |
| you state the spec's conjecture | `conjecture-spec` |
| you split the spec into the two tickets | `split` |
| you agree a ticket | `triage` |
| you finish a ticket yourself | `blocked` |
| you state the ticket's conjecture, and the `Flag:` line on the spec where it is asked | `conjecture` |
| an agent builds the ticket on its branch | `build` |
| the build stops part-way: its code uncommitted on the branch, or on the branch it cut before it wrote anything | |
| the build run blocks and leaves its stash | |
| you answer the blocked run | `blocked-run` |
| the drain builds and merges a ticket | `drain` |
| the drain parks a ticket for open questions, for guard edits, or with an older note | |
| the drain merges the first ticket, which brings a new kind of code, and parks the second for the safeguard | |
| you answer on the ticket | `answer` |
| you land the guard edits | `land-guard` |
| you change the ticket | `change-ticket` |
| you mark the merged ticket resolved | `resolve` |
| a new kind of code arrives, and a safeguard is owed | |
| an upgrade leaves rows for an agent to fill, and the architecture owed where it was never asked | |
| an upgrade asks the `Flag:` line before deployment, on the default branch or beside a built ticket's branch | |
| an agent fills the rows | `fill` |
| a kind of code that reopens the architecture arrives, and a review is owed | |
| an agent lands the architecture review | `review` |
| an agent binds the safeguard | `safeguard` |
| you run `/ratchet:architecture`: the record and the parts on a branch, whose merge is yours | `architecture` |
| someone edits code | |
| an agent commits on a branch | `commit` |
| the branch merges, and the default branch is checked out; on a branch already merged it changes nothing | `merge` |
| the merge leaves the branch checked out: cut off before it switched, unable to switch, or made from another checkout | |
| someone checks out a merged branch, at rest | |
| an agent switches to the default branch | `switch` |
| an agent lands the records: merged, or on a branch that waits for you | `land-records`, `finish-records` |
| the landing run stops on its branch | |
| you merge the records branch | `merge-records` |

## Rules

A rule whose name starts `Real` is on what the real route names; the others are on what the pages say. Four are on the architecture an upgrade leaves owed, and the last five on the `Flag:` line (ADR 0061). Each rule on the pages holds in Python and in TLC, on every state. Two rules on the real route break, each in the 9 states of [the divergence](#divergences) and in no other.

| Rule | Says | On the model |
| --- | --- | --- |
| `AdviceCanBeFollowed` | The step the route names can be taken. | holds |
| `AdviceReachesRest` | Following the route's advice reaches a state with nothing left to finish. | holds |
| `FollowingAdviceLowersRank` | Each step the route names gets nearer to having nothing left to finish. | holds |
| `RealAdviceCanBeFollowed` | The step the real route names can be taken. | breaks in 9 states |
| `FollowingRealAdviceLowersRank` | Each step the real route names gets nearer to having nothing left to finish. | holds |
| `AdviceChangesTheState` | Taking the step the route names changes the state: its effect has not already happened. | holds |
| `RealAdviceChangesTheState` | Taking the step the real route names changes the state: its effect has not already happened. | holds |
| `AdviceNeverMergesAMergedBranch` | The route never names the merge of a branch the default branch already holds. | holds |
| `RealAdviceNeverMergesAMergedBranch` | The real route never names the merge of a branch the default branch already holds. | holds |
| `ArchitectureNamedExactlyWhenOwed` | The owed architecture is named exactly when the ledger owes it and nothing named before it is open. | holds |
| `OwedArchitectureNeverHidesTheWork` | Where the owed architecture is named, the step the route would otherwise name can be taken. | holds |
| `OnlyAnUpgradeOwesTheArchitecture` | Only an upgrade leaves the architecture owed; an adoption never does. | holds |
| `OnlyRecordingEndsTheOwedArchitecture` | Recording the architecture is the only step that ends it being owed. | holds |
| `FlagLineBeforeTheBuild` | Where a labelled ticket has no `Flag:` line, on it or on its spec, the route names no build and no drain. | holds |
| `RealFlagLineBeforeTheBuild` | Where a labelled ticket has no `Flag:` line, on it or on its spec, the real route names no build and no drain. | breaks in 9 states |
| `NoRunStartsWithoutTheFlagLine` | No feature run and no drain starts while a ticket's `Flag:` line is missing. | holds |
| `OnlyTheUpgradeLeavesTheFlagLineMissing` | Only the upgrade that asks the `Flag:` line before deployment leaves it missing. | holds |
| `TheUpgradeMovesNoTicket` | The upgrade that asks the `Flag:` line moves no ticket and no inheritance: a built ticket stays built. | holds |

The rank of a state is the longest run of advised steps from it to rest. The highest rank is 23.

On a branch the default branch already holds, the merge can still be run, and it changes nothing: the merge judge refuses the branch, as behind a merge commit that holds it, or as a change with nothing in it to judge. The model has that step, from each of the 282 states. So a route that named the merge there would name a step that can be taken, and `RealAdviceCanBeFollowed` would hold. That is how the defect this model once missed got past it: with no forge, after the merge left the checkout on the branch, the route named the same merge again, for ever. `RealAdviceChangesTheState` breaks there. TLA+ cannot see a step that leaves every variable as it was, so the rule is a fact of each state: no way of taking the step named leads back to the same state.

`ArchitectureNamedExactlyWhenOwed` names what comes before the architecture field by field, apart from the order the model asks in: the setup, the hooks, the checkout, a records branch, rows to fill, a review and a safeguard. So a change of that order breaks it. In 72 states the route names the owed architecture, and the step it would otherwise name is a build in 7 of them and the drain in 2.

The upgrade that asks the `Flag:` line arrives in 279 steps. In 198 it leaves the line missing, and 27 of those leave a built ticket on its branch, which goes on to its merge. In 10 of the 267 states where the line is missing the route names the conjecture. In the others something it names first comes before it: the checkout, a records branch, a fill, a review or a safeguard, a hold, triage, the split, a merged ticket to resolve, or a blocking ticket. Only two steps end a missing line: the conjecture, in 98 steps, and the owner finishing the last ticket that lacked it, in 11.

The tests also check that:

- following the advice never comes back to a state it passed, except through rest;
- every state reaches rest by some steps;
- each kind of hold is released by the one step the route names for it, and by no other: a ticket parked for guard edits stays parked when another ticket is agreed or the binding changes, and a note older than the kinds of hold, or a guard hold with no base commit, waits for a change to the ticket;
- on a real repository, the real route names what the model says after every one of the 5798 steps, each taken from its source state as the shortest path from an empty directory builds it; where the drain parks a ticket for the safeguard, it parks on what the real `triggers.py` printed for the ticket's change;
- on a real repository whose binding runs `merge.py`, the merge the route names, of the adoption's branch or of a working branch, leaves the default branch checked out and keeps the branch, and the route names what the model says after it; checked out again, the branch is named for the switch, a second merge of it changes nothing, and the switch goes back;
- on such a repository, a merge with a dirty tree, or with the default branch checked out elsewhere, leaves HEAD on the branch and its `next:` line names the switch first, and a merge run from the default branch leaves HEAD there; the route then names what the model says;
- on a real repository, a build that stops on the branch it cut is left by the switch and built again, and following the route from there never comes back to it;
- where the real route names the owed architecture, its `then:` names the step it names once the architecture is no longer owed, and its `why:` offers carrying on with it; that step is the one the model gives beneath;
- where the real route names a safeguard, its `then:` names the step it names once the rows are bound, which is the owed architecture exactly where the model owes one, and its `why:` still offers carrying on with the work;
- on a real repository, a commit on the default branch that a guard hold did not name leaves the ticket held, and landing the guard edits releases it;
- on a real repository, the architecture check finds nothing after `/ratchet:adopt`, and the real `upgrade.py --apply` then owes no architecture;
- on a real repository an older plugin adopted, the real upgrade owes exactly what the architecture check finds, the route names the fill, which the real upgrade settles once the rows are written, then `/ratchet:architecture`, then its branch's merge, and the check finds nothing after;
- the real upgrade also owes every other finding red at the merge base, which the route never names, and on the test's adoption, which writes the quality files the adopt templates hold, it leaves nothing red and exits 0, as every upgrade in the tests must;
- on a real repository that owes the architecture, a review and a safeguard that arrive beside it are each named before it;
- a drain note older than a blocked run's stash, or one with no base commit, does not release the run;
- on long runs of steps, a repository that loses its commit hooks is told to install them first, whatever else is true.

## Divergences

Where the pages make a promise that the rule they give for it does not keep, and the real route follows the rule, the model follows the promise and names the place as a divergence. The tests hold the route to the model with the divergences applied. Each one also has a test that expects the route to keep the promise and fails. That test turns red the day the route is fixed. Then the divergence is deleted from `route_model.py`.

There is one.

- `flag-line-waits-for-deployed`. [The records page](../../skills/method/records.md) says: "A line of `## Operability` is asked when the binding declares an application type its question is asked of". `scripts/feature_preflight.py` asks the `Flag:` line only once `deployed` is declared, so where the line is missing the real route reads each stated ticket as stated. ADR 0061 decides the page's way. The shortest run: `/ratchet:init`, the tracker, `/ratchet:adopt`, the hooks, the glossary and brief committed and merged, a spec written and landed, split and landed, the first ticket agreed and its own conjecture stated, then the upgrade that asks the line. The model names `/ratchet:conjecture` on the first ticket. The real route names its build. In the 9 states where the line is missing and the pages name the conjecture, the real route names a build or the drain. That step cannot be taken, since the preflight is to refuse the ticket, so `RealAdviceCanBeFollowed` and `RealFlagLineBeforeTheBuild` break there. The sweep holds the real route to the build it names today, and a strict expected failure replays the shortest run and expects the conjecture.

The model was written with another, a guard hold that outlived the edits it asked for. It was deleted when the drain's note began to record the paths a run named as its cause, and the default branch's commit when it parked. Landing those paths releases the hold.

## What the next script change must do

Each item below flips one pin. Delete the pin in the same change.

- `scripts/feature_preflight.py`, `operability_asked`: ask the `Flag:` line of every declared type the catalogue asks it of, whatever the life events, and delete `FLAG_EVENT`. The strict expected failure `test_the_route_names_what_the_pages_say_where_it_diverges[flag-line-waits-for-deployed]` then passes, which is red; delete the entry from `DIVERGENCES` in `route_model.py`. The world's tickets carry no `## Operability` block of their own, so the line-by-line reading ADR 0061 also asks of `operability_failure` changes nothing here; its own tests hold it.

## Charts

The rules were checked on `Route.tla`, SHA-256 `15402fc7e2e065d8c838323374dcc5ee57b10af419f7c2fb6306af6a4df8d306`. Run with the 16 rules that hold, TLC reached 2210 states, and its last line counts 5799 states generated: the start and 5798 steps. Nothing deadlocks. Run once for each of the two rules that break, with one worker, TLC stops at the shortest run of [the divergence](#divergences), 14 steps from an empty directory, the same for both.

That module is too large for the statechart tool to draw. The charts are drawn from `RouteShown.tla`, SHA-256 `370eb9d6d33e24f92b36f648c3e2c9c1a89f3aa8544903efc4b4972df2648d71`: the same graph seen through what the charts show, which is what the route names, what it would name beneath, the checkout, what is owed, and whether the `Flag:` line is missing. Each chart's report says that TLC reached 465 states and 1482 steps there, and that nothing deadlocks.

Each chart gives each of those states one label, and draws an arrow where a step changes the label. A step that keeps the label is not drawn. The summary carries no rule, so the charts have no legend. A state where the real route names something else says so in its label.

### The work

What the route names once nothing in the checkout is unfinished, no records branch waits and no rows to fill, safeguard, review or architecture is owed. Four labels share one state: the drain's three kinds of park and the park for a safeguard are "Release the ticket the drain parked". `build` and `drain` are "Build the agent-ready tickets". A state where the real route names something else says so after "yet the route names". One does: where a ticket's `Flag:` line is missing, the pages name its conjecture, and the real route names the build.

17 states and 38 arrows. It does not draw the 1049 steps that keep the label, which are most of the checkout's steps.

```mermaid
---
title: "What the route names once nothing is unfinished"
---
stateDiagram-v2
  s0: Run /ratchet:init
  s1: Set up the tracker
  s2: Run /ratchet:adopt
  s3: Install the commit hooks
  s4: Write the glossary and design brief
  s5: Write the next spec
  s6: State the spec's conjecture
  s7: Split the spec into tickets
  s8: Agree a ticket for agents
  s9: State the ticket's conjecture
  s10: Finish the blocking ticket first
  s11: Build the agent-ready tickets
  s12: The ticket is built on its branch
  s13: Answer what the blocked run asked
  s14: Mark the merged ticket resolved
  s15: Release the ticket the drain parked
  s16: State the ticket's conjecture, yet the route names: Build the agent-ready tickets
  [*] --> s0
  s0 --> s1: you run /ratchet:init
  s1 --> s2: you set up the tracker
  s2 --> s3: you run /ratchet:adopt or an older plugin's adoption or the adoption stops part-way
  s3 --> s4: an agent installs the hooks
  s4 --> s5: you write the glossary and brief
  s5 --> s6: you write a spec
  s6 --> s7: you state the spec's conjecture
  s6 --> s8: you split the spec
  s7 --> s8: you split the spec
  s8 --> s5: you finish a ticket yourself
  s8 --> s9: you agree a ticket
  s8 --> s10: you agree a ticket
  s8 --> s11: you agree a ticket
  s9 --> s11: you state the ticket's conjecture
  s10 --> s9: you agree a ticket or you finish a ticket yourself
  s10 --> s11: you finish a ticket yourself or you agree a ticket
  s11 --> s12: an agent builds the ticket or the build stops part-way
  s11 --> s13: the build run blocks
  s11 --> s14: the drain builds and merges a ticket or the drain merges one and parks the next for a safeguard
  s11 --> s15: the drain parks a ticket
  s11 --> s16: an upgrade asks the Flag: line before deployment
  s8 --> s16: you agree a ticket
  s11 --> s9: an upgrade asks the Flag: line before deployment
  s12 --> s14: the branch merges or the merge leaves the branch checked out
  s13 --> s11: you answer the blocked run
  s14 --> s8: you mark the ticket resolved
  s14 --> s9: you mark the ticket resolved
  s14 --> s5: you mark the ticket resolved
  s14 --> s11: you mark the ticket resolved
  s15 --> s11: you answer on the ticket or you land the guard edits or you change the ticket or an agent binds the safeguard
  s16 --> s11: you state the ticket's conjecture
  s10 --> s16: you finish a ticket yourself or you agree a ticket
  s9 --> s16: an agent fills the rows or an agent lands the architecture review or an agent binds the safeguard or an agent lands the records or you merge the records branch
  s13 --> s16: you answer the blocked run
  s15 --> s16: you answer on the ticket or you land the guard edits or you change the ticket or an agent binds the safeguard
  s14 --> s15: you mark the ticket resolved
  s13 --> s9: you answer the blocked run
  s15 --> s9: you answer on the ticket or you land the guard edits or you change the ticket or an agent binds the safeguard
```

### The checkout

What is unfinished in the checkout, which the route names before any work. A merged branch still checked out is left by a switch, never merged again. The merge of it, which changes nothing, keeps the label and is not drawn.

8 states and 18 arrows. It does not draw the 763 steps that keep the label: the setup, the tickets, and what is owed.

```mermaid
---
title: "What the route names first: unfinished work in the checkout"
---
stateDiagram-v2
  s0: Nothing unfinished
  s1: A clean branch to merge
  s2: Code uncommitted on a branch
  s3: Code uncommitted on the default branch
  s4: A merged branch still checked out
  s5: Records uncommitted
  s6: A records branch waits for your merge
  s7: A landing run stopped part-way
  [*] --> s0
  s0 --> s1: the adoption stops part-way or you run /ratchet:architecture or an agent builds the ticket
  s0 --> s2: the adoption stops part-way or the build stops part-way
  s1 --> s0: you run /ratchet:adopt or the branch merges
  s1 --> s2: the adoption stops part-way or someone edits code
  s2 --> s1: an agent commits on a branch
  s0 --> s3: you write the glossary and brief or someone edits code
  s3 --> s1: an agent commits on a branch
  s1 --> s4: the merge leaves the branch checked out
  s4 --> s0: an agent switches to the default branch
  s0 --> s4: someone checks out a merged branch or the build stops part-way
  s0 --> s5: you write a spec or you state the spec's conjecture or you split the spec or you mark the ticket resolved
  s4 --> s2: someone edits code
  s5 --> s0: an agent lands the records
  s5 --> s6: an agent lands the records
  s5 --> s7: the landing run stops
  s6 --> s0: you merge the records branch
  s7 --> s0: an agent lands the records
  s7 --> s6: an agent lands the records
```

### Fills, safeguards and reviews

Rows an upgrade left for an agent to fill are named first of these, and no feature or drain starts until they are written (ADR 0050); the model takes the feature run and the drain away while they are owed. A safeguard or an architecture review that is owed is named before the next feature, and after anything unfinished in the checkout. A review comes before a safeguard (ADR 0052): with both owed, landing the review leaves the safeguard named.

7 states and 13 arrows. It does not draw the 1113 steps that keep the label.

```mermaid
---
title: "Fills, safeguards and reviews: named before the next feature, never instead of it"
---
stateDiagram-v2
  s0: No fill, review or safeguard owed
  s1: Rows to fill: the route names it
  s2: Review owed: the route names it
  s3: Owed: the route names it
  s4: Review owed: unfinished work comes first
  s5: Rows to fill: unfinished work comes first
  s6: Owed: unfinished work comes first
  [*] --> s0
  s0 --> s1: an upgrade leaves rows for an agent to fill
  s0 --> s2: a kind that reopens the architecture arrives
  s0 --> s3: a new kind of code arrives or the drain merges one and parks the next for a safeguard
  s1 --> s0: an agent fills the rows
  s1 --> s5: you write a spec or you run /ratchet:architecture or you state the spec's conjecture or you split the spec
  s2 --> s0: an agent lands the architecture review
  s2 --> s4: you write a spec or you state the spec's conjecture or you split the spec or an agent builds the ticket or the build stops part-way or you mark the ticket resolved
  s3 --> s0: an agent binds the safeguard
  s3 --> s6: you write a spec or you state the spec's conjecture or you split the spec or an agent builds the ticket or the build stops part-way or you mark the ticket resolved or you run /ratchet:architecture
  s4 --> s2: an agent lands the records or you merge the records branch or the branch merges or an agent switches to the default branch
  s5 --> s1: an agent lands the records or the branch merges or you merge the records branch or an agent switches to the default branch
  s6 --> s3: an agent lands the records or you merge the records branch or the branch merges or an agent switches to the default branch
  s2 --> s3: an agent lands the architecture review
```

### The architecture

`/ratchet:adopt` records the architecture, and an adoption by an older plugin never asked it. An upgrade owes it where it was never asked (ADR 0049). The route names it after the rows to fill, a review and a safeguard, and offers beside it the step it would otherwise name, so it never hides a build or the drain. `/ratchet:architecture` leaves the record on a branch whose merge is the owner's: this chart draws that as recorded, and the checkout's chart draws the merge.

7 states and 15 arrows. It does not draw the 1389 steps that keep the label.

```mermaid
---
title: "The architecture: asked at adoption, owed after an upgrade, never instead of the work"
---
stateDiagram-v2
  s0: Not adopted yet
  s1: Recorded
  s2: Never asked: an older plugin adopted
  s3: Owed: the rows to fill come first
  s4: Owed: unfinished work comes first
  s5: Owed: the route names it
  s6: Owed: a safeguard comes first
  [*] --> s0
  s0 --> s1: you run /ratchet:adopt or the adoption stops part-way
  s0 --> s2: an older plugin's adoption
  s2 --> s3: an upgrade leaves rows for an agent to fill
  s3 --> s4: you write a spec or you state the spec's conjecture or you split the spec
  s3 --> s5: an agent fills the rows
  s3 --> s1: you run /ratchet:architecture
  s4 --> s3: an agent lands the records or you merge the records branch
  s5 --> s1: you run /ratchet:architecture
  s5 --> s4: you write a spec or you state the spec's conjecture or you split the spec or an agent builds the ticket or the build stops part-way or you mark the ticket resolved
  s4 --> s5: an agent lands the records or you merge the records branch or the branch merges or an agent switches to the default branch
  s5 --> s6: the drain merges one and parks the next for a safeguard
  s6 --> s5: an agent binds the safeguard
  s6 --> s4: you mark the ticket resolved
  s6 --> s1: you run /ratchet:architecture
  s4 --> s6: an agent lands the records or you merge the records branch
```

### The `Flag:` line

The upgrade that ships ADR 0061 leaves the line missing where a record in flight states its conjecture. The route then names the ticket's conjecture once nothing it names first is open, and that conjecture lands the line on the spec for both tickets. A held ticket is released first, and then needs its conjecture. A ticket the owner finishes themself closes without it. Where the line is missing, the real route names a build instead, as the two states that say "yet the real route names" show.

6 states and 17 arrows. It does not draw the 1363 steps that keep the label.

```mermaid
---
title: "The Flag: line: asked before deployment, stated before any build"
---
stateDiagram-v2
  s0: No Flag: line is missing
  s1: Missing: other work comes first
  s2: Missing: the route names the conjecture, yet the real route names: Build the ticket
  s3: Missing: the route names the conjecture
  s4: Missing: the route names the conjecture, yet the real route names: Build every ready ticket at once
  s5: Missing: a held ticket is released first
  [*] --> s0
  s0 --> s1: an upgrade asks the Flag: line before deployment
  s0 --> s3: an upgrade asks the Flag: line before deployment
  s0 --> s2: an upgrade asks the Flag: line before deployment
  s1 --> s0: you finish a ticket yourself or you state the ticket's conjecture
  s1 --> s2: you agree a ticket or you finish a ticket yourself or an agent fills the rows or an agent lands the architecture review or an agent binds the safeguard or an agent lands the records or you merge the records branch
  s0 --> s5: an upgrade asks the Flag: line before deployment
  s2 --> s0: you state the ticket's conjecture
  s2 --> s4: you agree a ticket
  s1 --> s3: you agree a ticket or an agent fills the rows or an agent lands the architecture review or an agent binds the safeguard
  s1 --> s4: you agree a ticket or an agent fills the rows or an agent lands the architecture review or an agent binds the safeguard
  s0 --> s4: an upgrade asks the Flag: line before deployment
  s3 --> s0: you state the ticket's conjecture
  s4 --> s0: you state the ticket's conjecture
  s5 --> s0: you state the ticket's conjecture
  s5 --> s2: you answer the blocked run or you answer on the ticket or you land the guard edits or you change the ticket
  s5 --> s4: you answer the blocked run or you answer on the ticket or you land the guard edits or you change the ticket
  s1 --> s5: an agent lands the architecture review or an agent binds the safeguard
```

## Left out

- A forge: `origin`, pull requests, issues on the forge. The route reads them online; the tests ask it offline.
- More than two tickets, more than one spec at a time, and blocking edges other than the second ticket on the first.
- A ticket too large for one change, which the agent splits itself, and the hold left when its split is refused.
- A red `check`. The model takes `just check` as green.
- The merge judge's reasons. A records landing has two outcomes, merged alone or waiting for the owner, and the model allows both whatever the records change.
- Which changes among the records are the owner's to merge. The merge judge decides it, and the judge's own tests test it. The model has only that a records branch waits for the owner or lands by an agent.
- The owner's ticket edits as records. Agreeing a ticket, stating its conjecture and finishing a ticket yourself are one step each. Only marking a ticket resolved goes through records uncommitted and a landing.
- A split that stops half-way, a grill that writes only part of its records, and a safeguard run that stops.
- The drain's own states: its work trees, the merge job, and retries of a mechanical wait.
- A drain note written after a blocked run's stash on the same ticket, and a note with more than one kind of hold. The other order is in the model: a ticket the drain parked, then changed by the owner, then held by a run that blocked. The note is older than the run, so the route judges the run as if the drain had written no note, and names it until the ticket changes again.
- A note a drain wrote with its kinds of hold but before it recorded the default branch's commit. Landing its guard edits does not release it, and the route names them again; a change to the ticket does.
- A kind that reopens the architecture and owes rows of its own as well, a declared life event that reopens it, and a ticket the drain parks while a review is owed. The model's arrival owes the review alone; `test_architecture_review.py` holds the route's order where rows are owed too, the review and then the safeguard, and the release of such a park.
- Rows to fill owed together with a safeguard or a review. The model's upgrade arrives at rest and leaves rows to fill alone; `test_route.py` holds the route's order where they are owed together.
- A review or a new kind that arrives while the architecture is owed. The model's arrivals come at rest; a test walks a review and a safeguard that arrive beside it on a real repository. A safeguard beside it is in the model, from the drain.
- An adoption by an older plugin that stops part-way, an upgrade that owes the architecture and no rows to fill, and an architecture recorded a second time to revisit it. An upgrade owes the architecture once: after it is recorded, an upgrade owes none.
- The `also:` lines: signs of a life event, and standing answers.
- Another work tree that holds the default branch. There the merge cannot switch, and both its `next:` line and the route name `git switch <default>`, which git refuses while that work tree holds it. A strict expected failure in the tests turns red the day the step named there can be taken.
- A branch the default branch holds, edited anywhere but at rest. What the route makes of a dirty tree there does not depend on the tickets or on what is owed.
- A project that is deployed, a library, or one whose flag row is bound `none`. The upgrade that asks the `Flag:` line changes nothing the route names there, as for any other upgrade.
- That upgrade before the glossary is written, while records or code are uncommitted or a records branch waits, or from a plugin before ADR 0049.
- A ticket whose own `## Operability` block lacks the `Flag:` line its spec states, which ADR 0061 reads line by line. The model's tickets carry no block of their own.
- Hooks lost after the setup. The model installs them once; the stateful test takes them away every seventh step and checks that the route names them first.

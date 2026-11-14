# The route

`scripts/route.py` reads the checkout and names the one command the owner types next. [The route](../../skills/method/route.md) says what it names in each case. This page writes that down as a state machine: the states the route can tell apart, what it names in each, the steps that lead from one state to the next, and the rules the steps keep.

The model is `hooks/tests/route_model.py`. The tests in `hooks/tests/test_route_model.py` prove the rules on it and ask the real route in each of its states.

## Scope

The model covers one repository with:

- no forge: no `origin`, tickets in the local tracker, the route asked offline;
- one spec at a time, split into two tickets, the second blocked by the first;
- one step at a time: a step on a spec or a ticket starts from a clean default branch with no records branch waiting.

What it leaves out is listed [at the end](#left-out).

## State

| Part | Values |
| --- | --- |
| Setup | nothing; an init that stopped before its questions; init done; the tracker set up; an adoption that stopped before it wrote the plugin pin; adopted |
| Commit hooks | installed, or not |
| Checked out | the default branch; a working branch; a `records/<time>` branch |
| Work tree | clean; records only; more than records |
| A records branch waits for the owner's merge | yes or no |
| The glossary and the design brief | written, or not |
| A spec not yet split | none; written; stating its conjecture |
| Each of the two tickets | none; needs triage; agent-ready; agent-ready with a conjecture; held by a blocked run's stash; parked by the drain for open questions, for guard edits, for an owed safeguard, or by a note older than the kinds of hold; parked for guard edits the owner has since landed; built on its branch; merged but open; resolved |
| The tickets inherit the spec's conjecture | yes or no |
| A safeguard is owed | yes or no |

From an empty directory the model reaches 294 states and 732 steps. Three of the states are at rest: adopted, nothing unfinished, nothing owed, and every ticket resolved or none written. At rest the route names the next spec.

## What the route names

The route asks in this order, and the first case that applies is its answer. Each answer is a label. There are 24 labels; `safeguard` is named in two places.

| Label | When | What it names |
| --- | --- | --- |
| `init` | no setup, or an init that stopped | `/ratchet:init` |
| `tracker` | init done | `/mattpocock-skills:setup-matt-pocock-skills` |
| `adopt` | the tracker is set up, or an adoption stopped before the pin | `/ratchet:adopt` |
| `hooks` | the commit hooks are missing | the hooks install, which an agent runs |
| `finish-records` | a `records/<time>` branch is checked out | `just merge --records`, which finishes the stopped run |
| `land-records` | records alone are uncommitted on the default branch | `just merge --records` |
| `commit` | anything else is uncommitted | `just check`, then a commit on a branch |
| `merge` | a working branch is checked out | `just merge <branch>` |
| `merge-records` | a records branch waits | the owner's merge of it |
| `safeguard` | a safeguard is owed | `/ratchet:safeguard` |
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
| `change-ticket` | ... parked by a note older than the kinds of hold | change the ticket |
| `drain` | an unblocked ticket states a conjecture, and so does another | `/ratchet:feature` with no ticket |
| `build` | an unblocked ticket states a conjecture | `/ratchet:feature <ticket>` |
| `conjecture` | any other case | `/ratchet:conjecture <ticket>` |

## Steps

32 steps change the state. The owner's and an agent's steps each follow one label. The others are things that happen: a command that stops part-way, a run that blocks, a park, an edit.

| Step | Follows |
| --- | --- |
| you run `/ratchet:init` | `init` |
| init stops before its questions | |
| you set up the tracker | `tracker` |
| you run `/ratchet:adopt` | `adopt` |
| the adoption stops part-way: on its branch before the pin, committed; or after the pin, uncommitted | |
| an agent installs the hooks | `hooks` |
| you write the glossary and the brief, uncommitted | `grill` |
| you write a spec | `specify` |
| you state the spec's conjecture | `conjecture-spec` |
| you split the spec into the two tickets | `split` |
| you agree a ticket | `triage` |
| you finish a ticket yourself | `blocked` |
| you state the ticket's conjecture | `conjecture` |
| an agent builds the ticket on its branch | `build` |
| the build stops part-way, its code uncommitted on the branch | |
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
| an agent binds the safeguard | `safeguard` |
| someone edits code | |
| an agent commits on a branch | `commit` |
| the branch merges | `merge` |
| an agent lands the records: merged, or on a branch that waits for you | `land-records`, `finish-records` |
| the landing run stops on its branch | |
| you merge the records branch | `merge-records` |

## Rules

The first three rules are on what the pages say. The last two are on what the real route names.

| Rule | Says | On the model |
| --- | --- | --- |
| `AdviceCanBeFollowed` | The step the route names can be taken. | holds |
| `AdviceReachesRest` | Following the route's advice reaches a state with nothing left to finish. | holds |
| `FollowingAdviceLowersRank` | Each step the route names gets nearer to having nothing left to finish. | holds |
| `RealAdviceCanBeFollowed` | The step the real route names can be taken. | breaks in 9 states, where the divergence below holds |
| `FollowingRealAdviceLowersRank` | Each step the real route names gets nearer to having nothing left to finish. | holds |

The rank of a state is the longest run of advised steps from it to rest. The highest rank is 21. Where the real route names a step that cannot be taken, there is no step to lower the rank, so the last rule holds there too.

The tests also check that:

- following the advice never comes back to a state it passed, except through rest;
- every state reaches rest by some steps;
- the real route names what the model says in every one of the 294 states;
- on long runs of steps, a repository that loses its commit hooks is told to install them first, whatever else is true.

## Divergences

Where the pages make a promise that the rule they give for it does not keep, and the real route follows the rule, the model follows the promise and names the place as a divergence. The tests hold the route to the model with the divergences applied. Each one also has a test that expects the route to keep the promise and fails. That test turns red the day the route is fixed. Then the divergence is deleted from `route_model.py`.

There is one.

### A guard hold outlives its edits

The pages say that for a ticket the drain parked, the route "names the step that releases the hold for each kind the note records", and that for guard edits the step is to land them on the default branch. They also give the rule: the note holds the ticket until the ticket's text changes. Landing the guard edits does not change the ticket's text, so the rule does not keep the promise. The route follows the rule: it names landing the edits again, and the drain still holds the ticket.

Replay, on an adopted repository with ticket 01 agent-ready with its conjecture:

1. The drain parks 01 for a guard edit. The route names `land-guard`: "land the guard edits the run of `.scratch/f1/issues/01-a1.md` named on main".
2. The owner commits the guard edit on the default branch. The route names the same line, and `drain plan` lists 01 as held.

Rule broken: `RealAdviceCanBeFollowed`: there is nothing left to land. Past this state the model moves on by the step the pages name, the build.

## Charts

The charts were drawn from the export with SHA-256 `4f20a0f095b2ba8f8533257dfae714d99e819b7c8b1c9ffaf0230e0560347a87`.

Each chart gives each state of the model one label, and draws an arrow where a step changes the label. A step that keeps the label is not drawn, except on the path to a broken rule. The legend of each chart marks the states where `RealAdviceCanBeFollowed` breaks, in all or some of the model's states behind them, and the shortest path to one from an empty directory: 15 steps, through the divergence. Each chart's report says that TLC reached 294 states and 732 steps, that `RealAdviceCanBeFollowed` breaks, and that the other four rules hold and nothing deadlocks.

### The work

What the route names once nothing in the checkout is unfinished, no records branch waits and no safeguard is owed. Four labels share one state: the drain's three kinds of park and the park for a safeguard are "Release the ticket the drain parked". `build` and `drain` are "Build the agent-ready tickets". A state where the real route names something else says so after "yet the route names".

17 states and 35 arrows. It does not draw the 358 steps that keep the label, which are most of the checkout's steps.

```mermaid
---
title: "What the route names once nothing is unfinished"
---
stateDiagram-v2
  state "Legend" as legend {
    lg0: Breaks RealAdviceCanBeFollowed
    lg1: Path to the RealAdviceCanBeFollowed bug
  }
  class lg0 breaks
  class lg1 bugPath
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
  s16: Build the agent-ready tickets, yet the route names: Release the ticket the drain parked
  [*] --> s0
  s0 --> s1: you run /ratchet:init
  s1 --> s2: you set up the tracker
  s2 --> s3: you run /ratchet:adopt or the adoption stops part-way
  s3 --> s4: an agent installs the hooks
  s4 --> s5: you write the glossary and brief
  s5 --> s5: an agent commits on a branch or the branch merges
  s5 --> s6: you write a spec
  s6 --> s6: an agent lands the records
  s6 --> s7: you state the spec's conjecture
  s6 --> s8: you split the spec
  s8 --> s8: an agent lands the records
  s7 --> s8: you split the spec
  s8 --> s9: you agree a ticket
  s8 --> s10: you agree a ticket
  s9 --> s11: you state the ticket's conjecture
  s10 --> s9: you agree a ticket or you finish a ticket yourself
  s8 --> s5: you finish a ticket yourself
  s8 --> s11: you agree a ticket
  s11 --> s12: an agent builds the ticket or the build stops part-way
  s11 --> s13: the build run blocks
  s11 --> s14: the drain builds and merges a ticket or the drain merges one and parks the next for a safeguard
  s11 --> s15: the drain parks a ticket
  s10 --> s11: you finish a ticket yourself or you agree a ticket
  s12 --> s14: the branch merges
  s13 --> s11: you answer the blocked run
  s14 --> s8: you mark the ticket resolved
  s15 --> s11: you answer on the ticket or you change the ticket or an agent binds the safeguard or you land the guard edits
  s15 --> s16: you land the guard edits
  s14 --> s9: you mark the ticket resolved
  s14 --> s5: you mark the ticket resolved
  s16 --> s12: an agent builds the ticket or the build stops part-way
  s16 --> s14: the drain builds and merges a ticket
  s14 --> s11: you mark the ticket resolved
  s14 --> s15: you mark the ticket resolved
  s11 --> s16: an agent binds the safeguard
  classDef breaks fill:#ffecee,stroke:#e72e3b,stroke-width:2.5px,color:#cd2834
  class s0 bugPath
  class s1 bugPath
  class s2 bugPath
  class s3 bugPath
  class s4 bugPath
  class s5 bugPath
  class s6 bugPath
  class s8 bugPath
  class s9 bugPath
  class s11 bugPath
  class s15 bugPath
  class s16 breaks
  class s16 bugPath
```

### The checkout

What is unfinished in the checkout, which the route names before any work.

7 states and 15 arrows. It does not draw the 400 steps that keep the label: the setup, the tickets and the safeguards.

```mermaid
---
title: "What the route names first: unfinished work in the checkout"
---
stateDiagram-v2
  state "Legend" as legend {
    lg0: Breaks RealAdviceCanBeFollowed in some cases
    lg1: Path to the RealAdviceCanBeFollowed bug
  }
  class lg0 mayBreak
  class lg1 bugPath
  s0: Nothing unfinished
  s1: A clean branch to merge
  s2: Code uncommitted on a branch
  s3: Code uncommitted on the default branch
  s4: Records uncommitted
  s5: A records branch waits for your merge
  s6: A landing run stopped part-way
  [*] --> s0
  s0 --> s0: the work moves on
  s0 --> s1: the adoption stops part-way or an agent builds the ticket
  s0 --> s2: the adoption stops part-way or the build stops part-way
  s1 --> s0: you run /ratchet:adopt or the branch merges
  s1 --> s2: the adoption stops part-way or someone edits code
  s0 --> s3: you write the glossary and brief or someone edits code
  s2 --> s1: an agent commits on a branch
  s3 --> s1: an agent commits on a branch
  s0 --> s4: you write a spec or you state the spec's conjecture or you split the spec or you mark the ticket resolved
  s4 --> s0: an agent lands the records
  s4 --> s5: an agent lands the records
  s4 --> s6: the landing run stops
  s5 --> s0: you merge the records branch
  s6 --> s0: an agent lands the records
  s6 --> s5: an agent lands the records
  classDef mayBreak fill:#fff3e6,stroke:#e8730c,stroke-width:2.5px,color:#b85a06
  class s0 mayBreak
  class s0 bugPath
  class s1 bugPath
  class s3 bugPath
  class s4 bugPath
```

### Safeguards

A safeguard that is owed is named before the next feature, and after anything unfinished in the checkout.

3 states and 5 arrows. It does not draw the 528 steps that keep the label.

```mermaid
---
title: "Safeguards: named before the next feature, never instead of it"
---
stateDiagram-v2
  state "Legend" as legend {
    lg0: Breaks RealAdviceCanBeFollowed in some cases
    lg1: Path to the RealAdviceCanBeFollowed bug
  }
  class lg0 mayBreak
  class lg1 bugPath
  s0: None owed
  s1: Owed: the route names it
  s2: Owed: unfinished work comes first
  [*] --> s0
  s0 --> s0: the work moves on
  s0 --> s1: a new kind of code arrives or the drain merges one and parks the next for a safeguard
  s1 --> s0: an agent binds the safeguard
  s1 --> s2: you write a spec or you mark the ticket resolved or you state the spec's conjecture or you split the spec or an agent builds the ticket or the build stops part-way
  s2 --> s1: an agent lands the records or you merge the records branch or the branch merges
  classDef mayBreak fill:#fff3e6,stroke:#e8730c,stroke-width:2.5px,color:#b85a06
  class s0 mayBreak
  class s0 bugPath
```

## Left out

- A forge: `origin`, pull requests, issues on the forge. The route reads them online; the tests ask it offline.
- More than two tickets, more than one spec at a time, and blocking edges other than the second ticket on the first.
- A ticket too large for one change, which the agent splits itself, and the hold left when its split is refused.
- A red `check`. The model takes `just check` as green.
- The merge judge's reasons. A records landing has two outcomes, merged alone or waiting for the owner, and the model allows both whatever the records change.
- The owner's ticket edits as records. Agreeing a ticket, stating its conjecture and finishing a ticket yourself are one step each. Only marking a ticket resolved goes through records uncommitted and a landing.
- A split that stops half-way, a grill that writes only part of its records, and a safeguard run that stops.
- The drain's own states: its work trees, the merge job, and retries of a mechanical wait.
- A blocked run's stash and a drain note on the same ticket, and a note with more than one kind of hold.
- The `also:` lines: signs of a life event, and standing answers.
- Hooks lost after the setup. The model installs them once; the stateful test takes them away every seventh step and checks that the route names them first.

# The route

`scripts/route.py` reads the checkout and names the one command the owner types next. [The route](../../skills/method/route.md) says what it names in each case. This page writes that down as a state machine: the states the route can tell apart, what it names in each, the steps that lead from one state to the next, and the rules the steps keep.

The model is `hooks/tests/route_model.py`. The tests in `hooks/tests/test_route_model.py` prove the rules on it, take each of its steps on a real repository, and ask the real route after each.

## Scope

The model covers one repository with:

- no forge: no `origin`, tickets in the local tracker, the route asked offline;
- one spec at a time, split into two tickets, the second blocked by the first;
- one step at a time: a step on a spec or a ticket starts from a clean default branch with no records branch waiting;
- one checkout: no other work tree holds the default branch;
- one upgrade that ships ADR 0061 to a project the `Flag:` line is asked of and that is not deployed, from a plugin that already asks the architecture. A part of a split and a ticket on a forge's tracker are out of it, since the model has neither;
- one kind of edit of the criteria a stated conjecture was drafted against: a ticket's `## Acceptance criteria` where it states its own conjecture, or the spec's `## User Stories` where its tickets inherit it, made on a clean default branch;
- one steering unit at a time (ADR 0064): the local spec and its two tickets under `.scratch/<slug>/`, which the step that resolves its last ticket deletes, and one unit a plugin from before ADR 0064 left, which the upgrade that ships it finds at rest or beside a due prune pass. The pipeline's feature spec under `docs/features/<slug>/` is out of it, since the model's build writes none;
- one formal model (ADR 0062), whose binding section the first change to the code it mirrors brings with it, and one kind its safeguard can find a defect in, state machines, whose row is `formal verification of state`.
- one prune pass of each kind (ADR 0065): the comments the adoption leaves owed in the ledger, and the pages, once enough merges have landed and a page cites a path changed after it. Each pass merges its own branch, as `/ratchet:prune` does with no forge.

What it leaves out is listed [at the end](#left-out).

## State

| Part | Values |
| --- | --- |
| Setup | nothing; an init that stopped before its questions; init done; the tracker set up; an adoption that stopped before it wrote the plugin pin; one that stopped after the pin, before its proof; adopted |
| Commit hooks | installed, or not |
| Checked out | the default branch; a working branch; a working branch the default branch already holds; the `records/<time>` branch a stopped `just merge --records` run left |
| Work tree | clean; records only; more than records |
| A records branch waits for the owner's merge: a local branch the default branch does not hold, whose change holds only records, that no run owns and no work tree has checked out | yes or no |
| The glossary and the design brief | written, or not |
| A spec not yet split | none; written; stating its conjecture |
| Each of the two tickets | none; needs triage; agent-ready; agent-ready with a conjecture; agent-ready with a stale conjecture, one whose criteria changed after it was written; held by a blocked run's stash; parked by the drain for open questions, for guard edits, for an owed safeguard, by a note older than the kinds of hold or than the base commit a guard hold needs, or, the defect ticket, for the row it owes itself; built on its branch; merged but open; resolved |
| The tickets inherit the spec's conjecture | yes or no |
| A safeguard is owed | yes or no |
| An architecture review is owed | yes or no |
| An upgrade left rows an agent fills | yes or no |
| The architecture | never asked, as before adoption and after an adoption by a plugin from before ADR 0049; owed since an upgrade; recorded |
| The `Flag:` line | not yet asked, as before the upgrade that ships ADR 0061, whose plugin asked it only once `deployed` was declared; missing from the spec in flight and its tickets, which state their conjecture; asked, the conjecture writing it on the spec |
| The owner's `## Answers` lines | none; on the unit; carried to the brief by a records change not yet on the default branch; dropped, a unit deleted without them in the brief |
| An expired unit is left in the tree | yes or no |
| The formal model's upkeep | in sync, or no model; behind; a round's change waiting for the owner's merge, the model in sync beside it or behind again; a round's ticket open, the bug a round found, stating its conjecture or none; behind again once that ticket is fixed |
| A defect ticket, filed by the safeguard where binding a formal row found the code breaks the model | none; open, its row owed; fixed, the kept model still to bind |
| Comments owed in the ledger, which a prune pass of comments works through | yes or no |
| A prune pass of the pages is due: enough merges since the last pass, and a page that cites a path changed after it | yes or no |

A waiting records branch is known by what it holds, not by its name. A feature run owns a `feature/` branch, and the drain owns a branch its note names. `just merge --records` names its branch `records/<time>`, and an agent that commits tickets on a branch of its own names it as it likes. The tests leave it on a branch an agent named in about half the states where one waits, and on a `records/<time>` branch in the others.

From an empty directory the model reaches 3232 states and 8501 steps. Nine of the states are at rest: adopted, nothing unfinished, nothing owed, and every ticket resolved or none written. In three the architecture is recorded, in three it was never asked, and in three it is recorded and the `Flag:` line is asked. At rest the route names the next spec.

`/ratchet:adopt` writes the plugin pin in its step 11, since every tier runs the plugin-pin gate first, and proves the adoption in step 12. So it can stop in two places: before the pin, with the templates committed on its branch, and after it, before its proof, with its work uncommitted there. The pages name `/ratchet:adopt` to resume either. Resumed on its branch it merges, as before; resumed off it, it proves the adoption and changes nothing else. Where it stopped after the pin, the model takes only that resume and the step the real route names, so the run it shows is that of an owner who follows today's route. In 29 states the adoption stopped after its pin, and they are not at rest.

A working branch the default branch already holds has no commit of its own to merge. A merge leaves it checked out when it is cut off before it switches, when it cannot switch, or when it is made from another checkout. A merge keeps the branch it merged, so it can be checked out again. A build that stops on the branch it cut, before it wrote anything, leaves one too: the branch is the default branch's tip. A commit on it makes it a working branch with work to merge again. In 342 states it is checked out clean, and in 9 with code uncommitted.

The architecture takes three values because each opens other steps. `/ratchet:adopt` asks it and records it (ADR 0049), so it is recorded from the adoption on. An adoption by an older plugin asked nothing, and the route reads nothing of that: it says the same as where the architecture is recorded. They part at an upgrade, which owes the architecture where it was never asked and owes nothing more where it is recorded. While it is owed the route names `/ratchet:architecture`, and recording it is the only step that ends that. In 2396 states the architecture is recorded, in 598 it was never asked, and in 238 it is owed.

The `Flag:` line takes three values (ADR 0061). Before the upgrade that ships the decision, the plugin asked the line only once `deployed` was declared, so in a project not yet deployed it waits, and nothing the route names turns on it. The upgrade moves no ticket. Where a record in flight states its conjecture and no line, the line is missing: the spec states its conjecture, a ticket inherits it, or a ticket states its own and is not yet built. A spec not yet split then reads as one with no conjecture, and the route names `/ratchet:conjecture` on the spec. A labelled ticket reads the same way. The route names `/ratchet:conjecture <ticket>`, and no feature run and no drain starts on it. The release of a hold, and the agreement of a ticket that inherits from the spec, lead there too. A built ticket goes on to its merge, since no preflight reads it again. The conjecture writes the answer and the line on the spec, which each ticket reads, so one conjecture lands the line for both tickets. Once it is on the spec, or every ticket that lacked it is closed, the line is asked: the conjecture writes it on every later spec. In 2100 states the line waits, in 368 it is missing, and in 764 it is asked.

ADR 0061 has the upgrade send each stated ticket back to agent-ready and stop the tickets inheriting until the spec states the line. The model keeps each ticket's state and reads the line beside it instead. A ticket sent back to agent-ready forgets whether it states its own conjecture, and that decides whether it is stated once the line lands on the spec. The route names the same step either way.

The upgrade arrives only where the architecture is recorded. An upgrade from a plugin before ADR 0049 is the model's other upgrade, which arrives at rest, or where nothing but a prune pass is open, and owes the architecture.

A conjecture goes stale when the owner or an agent edits the criteria it was drafted against, after it was written. [The conjecture skill](../../skills/conjecture/SKILL.md) ends `## Conjecture` with a `- Criteria:` line, the criteria heading and a digest of that section, so one command tells whether they changed. A ticket that states its own conjecture is checked against its own `## Acceptance criteria`. A ticket that inherits the spec's is checked against the spec's `## User Stories`, so an edit of the stories makes both tickets stale at once, and a ticket agreed while the spec is stale is agreed stale. The edit moves nothing else. A stale ticket reads as one with no conjecture: the route names `/ratchet:conjecture <ticket>` where it would otherwise build it, and no feature run and no drain starts on it. The redraft, the same command, is the only step that clears it: it writes the `- Criteria:` line again on the body that holds the conjecture, for both tickets where they inherit it. A conjecture with no `- Criteria:` line is current, so a stated ticket is never stale until the edit. In 546 states a conjecture is stale: in 170 it is inherited, in 46 both tickets are stale, and in 101 the `Flag:` line is missing as well. The edit is 336 steps, 98 of them on the spec's stories. The route names the conjecture of the stale ticket in 43 states. In 5 more it names the conjecture of the other ticket first, and in 3 the build of the other ticket, whose conjecture is current. In the others something it names first comes before it: the checkout, a records branch, a fill, a review, a safeguard or the architecture, a merged ticket to resolve, a blocking ticket, or a hold.

A spec and its tickets are a steering unit ([ADR 0064](../adr/0064-a-document-has-a-kind-and-a-steering-document-leaves-with-its-feature.md)). The unit expires when its last ticket is resolved, and the step that resolves that ticket deletes it: the owner's mark of the merged ticket, or the owner finishing the ticket themself. Where the ticket it names is the unit's last open one, the route's `resolve` line puts `documents.py expire` in the same command as the mark, so the step is one paste. Its `blocked` line never does. The ticket it names to finish blocks an agent-ready ticket, which is open, so the finish leaves a ticket open that names it. In the model that ticket is of the same unit: in each of the 19 states where the route names `blocked`, the first ticket needs triage and the second is agent-ready. The real route reads another unit the same way, since a unit has not expired while an open ticket elsewhere names one of its files as its blocker (ADR 0064). The owner finishing the last open ticket is a step the route does not name: there it names the triage of that ticket, or something owed before it. `expire` deletes the unit and appends each of its `## Answers` lines, dated, to the brief's `## In the owner's words`. Every spec the world writes holds an answer, so that deletion always edits the brief. That edit is a records change (ADR 0064): the merge judge takes, in a records change, lines added to `## In the owner's words` that are each, its date set aside, an `## Answers` line of a file the same change deletes, and no other edit of the brief. So the mark of the last ticket leaves records uncommitted on the default branch, as any mark does, and the route names their landing. The owner who finishes the last ticket commits the deletion with it. In 57 steps the mark expires the unit, and in 60 the owner finishing a ticket does. No other step expires one: the drain's merge leaves its ticket open, as before. The defect ticket's unit holds no spec, so its mark carries only what the owner wrote on the ticket: the `Flag:` answer its conjecture writes there where the line is asked, or an answer to a hold. That is 3 of the 57 marks, 2 of which carry answers. In 92 states the carried answers wait to land: uncommitted on the default branch in 36, on the branch of a landing run that stopped in 20, and on a records branch that waits for the owner in 36. Only the landing ends it, by an agent or by the owner's merge. The real route reads the deletion and the carried lines as records in each of them, as the pages do.

A unit left in the tree after it expired comes from a plugin from before ADR 0064, which deleted nothing. The upgrade that ships the decision finds one at rest or beside a due prune pass, once a feature is finished, in 14 steps. The route then names its deletion, `expire`, after the owed architecture, the formal model's upkeep and a due prune pass, and before the glossary and any spec or ticket. The world's older unit holds no answer, so its deletion edits no brief: it leaves the unit's files deleted, records alone uncommitted, and the route names their landing. While a unit is left the model takes no new spec. In 14 states one is left: the route names its deletion in 6, and a prune pass before it in 8, and so does the real route.

The deleter is what keeps the unit's rules, and the tests walk the model twice more without it. Where the step that makes the unit expired leaves it in the tree, the model reaches 3391 states and 8810 steps, and `NoStepLeavesTheUnitItExpires` breaks in 117 steps: the 57 marks and the 60 times the owner finishes a ticket. Its shortest run is 13 steps: `/ratchet:init`, the tracker, `/ratchet:adopt`, the hooks, the glossary and brief committed and merged, a spec written and landed, split and landed, and the owner finishing both tickets. Every other rule holds there, since the route names the deletion before any work and following it reaches rest. Where the step deletes the unit and drops its answers, the model reaches 3413 states and 8929 steps, and `TheOwnersAnswersOutliveTheUnit` breaks in 275 states, first after the same 13 steps. `ExpiredUnitBeforeTheWork` breaks without the deleter in no run, and the real route keeps it.

The formal model (ADR 0062) adds two parts. Its upkeep reads three signs: the binding's `- Synced:` line against the default branch, an unmerged `formal/upkeep-` branch, and an open ticket whose `Formal model:` line says `round`. It is behind when code the model mirrors landed after the commit that line names. The safeguard's round then either changes the model, on a `formal/upkeep-` branch that waits for the owner's merge since it touches guard paths, or finds a bug, which it files as a ticket and keeps its update on a `formal/kept-` branch. A ticket open comes before a round waiting, and a round waiting before behind. Behind is never red: where it is the first thing open the route names `/ratchet:safeguard` and offers beside it, in `then:`, the step it would otherwise name, as it does for the owed architecture. So do a round waiting and a round's ticket. A round may file its ticket with no conjecture. The route's step for a round's ticket is `/ratchet:feature`, which the preflight refuses without one, so there the route leaves the ticket to the ordinary chain: it names `/ratchet:conjecture <ticket>`, as for any agent-ready ticket that states none, and once it is stated, the fix. The model's upkeep arrives at rest, or beside a due prune pass, where the architecture is recorded, and under it the model takes only the step the route names, the work beside it, and more mirrored code. Mirrored code that lands while a round waits leaves it waiting and behind again, and its merge leads to behind. The fix of a round's ticket leads to behind, since the fix is mirrored code; the round after it changes the model and waits, and finds no second bug. In 52 states the model is behind, in 52 a round waits, in 52 a round waits and the model is behind again, in 4 a round's ticket is open, in 4 a round's ticket states no conjecture, and in 46 the model is behind after the fix. The route names the round's ticket in 4 states, its conjecture in 4, the round's merge in 56 and a round in 50. In the others unfinished work in the checkout comes first.

Binding a formal row can find a defect: the code breaks the model the safeguard wrote. The safeguard then leaves the row owed as `later:`, keeps the model on a `formal/kept-` branch, and files a defect ticket whose `Formal model:` line names the row. The route's safeguard step passes over a row a defect ticket names, so where that row is the only one owed the route names the ticket's work instead: its conjecture, its build, the merge, the mark. The drain can park it for the row it owes itself, since its change adds an instance of the kind the row waits on; the real `triggers.py` prints that for the parked change, and the route names a change to the ticket to meet it. Once the ticket is closed the safeguard is named again, and binding the row from the kept model is green. The model's safeguard finds a defect only where the architecture is recorded and the row is the only thing owed, and finds one once. In 38 states a defect ticket is open, and in 8 it is fixed with its row still to bind. The route names the safeguard in each of those 8 where nothing comes before it.

The prune pass (ADR 0065) adds two parts. Comments are owed where the ledger holds an entry of the rule `comments`; in the model the adoption leaves them, as an adoption that records the comments it found does, in one of its two outcomes. A pass of the pages is due where at least `- Prune after:` merges, 10 by default, have landed on the default branch since the last pass, and a page of a kind the pass prunes cites a tracked file that changed after the page's last commit. The model folds the count and the stale citation into one part: one step, merges that leave a page stale, makes the pass due, and it arrives where nothing but a prune is open. A due pass is never red. Where it is the first thing open the route names `/ratchet:prune comments` or `/ratchet:prune pages`, the comments first, and offers beside it, in `then:`, the step it would otherwise name. It comes after the review, a safeguard, the owed architecture and the formal model's upkeep, and before the expired unit, the glossary and every ticket. Under a due pass the model takes the pass, the work beside it, and the arrivals: a new kind, a kind that reopens the architecture, an upgrade, mirrored code, an older unit, and more merges. The pass of the comments deletes them on its branch, settles the ledger, and merges. The pass of the pages either cuts a page, a `docs(prune):` commit merged to the default branch, or cuts nothing and records the default branch's tip in `.ratchet/prune-pass` with `documents.py passed`; either starts the count again. In 68 states comments are owed, in 156 a pass of the pages is due, and in 32 both. The route names the comments in 25 states, 12 of them with the pages due too, and the pages in 42. The step it offers beside them is the next spec in 13, the triage of a ticket in 10, a build in 10, a mark in 10, the expired unit's deletion in 8, the spec's conjecture in 5, its split in 5, the blocking ticket in 5 and the glossary in 1. In the others something it names first comes before it: the hooks, the checkout, a records branch, a fill, a review, a safeguard, the architecture or the formal model's upkeep. The real route names the same as the pages in every state where a pass is due.

## What the route names

The route asks in this order, and the first case that applies is its answer. Each answer is a label. There are 34 labels; `safeguard`, `change-ticket` and `conjecture` are each named in two places.

| Label | When | What it names |
| --- | --- | --- |
| `init` | no setup, or an init that stopped | `/ratchet:init` |
| `tracker` | init done | `/mattpocock-skills:setup-matt-pocock-skills` |
| `adopt` | the tracker is set up, or an adoption stopped, before the pin or after it, before its proof | `/ratchet:adopt` |
| `hooks` | the commit hooks are missing | the hooks install, which an agent runs |
| `finish-records` | a `records/<time>` branch is checked out | `just merge --records`, which finishes the stopped run |
| `land-records` | records alone are uncommitted on the default branch, the answers `expire` carries to the brief among them | `just merge --records` |
| `commit` | anything else is uncommitted | `just check`, then a commit on a branch |
| `switch` | a working branch the default branch already holds is checked out | `git switch <default>` |
| `merge` | a working branch is checked out | `just merge <branch>` |
| `merge-records` | a records branch waits, whatever its name | the owner's merge of it |
| `fill` | an upgrade left rows an agent writes from the repository | `/ratchet:fill` |
| `review` | an architecture review is owed | `/ratchet:architecture` |
| `safeguard` | a safeguard is owed, of a row no defect ticket names | `/ratchet:safeguard` |
| `architecture` | an upgrade left the architecture owed | `/ratchet:architecture`, offering beside it the step it would otherwise name |
| `formal-ticket` | a ticket a formal round filed is open, and states its conjecture | `/ratchet:feature <ticket>`, offering beside it the step it would otherwise name |
| `formal-round` | a round's change waits on its `formal/upkeep-` branch | the owner's merge of it, offering beside it the step it would otherwise name |
| `formal-upkeep` | the formal model is behind the code it mirrors | `/ratchet:safeguard`, offering beside it the step it would otherwise name |
| `prune-comments` | the ledger owes comments | `/ratchet:prune comments`, offering beside it the step it would otherwise name |
| `prune-pages` | a pass of the pages is due | `/ratchet:prune pages`, offering beside it the step it would otherwise name |
| `expire` | an expired unit is left in the tree | `documents.py expire` on the default branch, whose deletion lands as records |
| `grill` | no glossary or design brief | `/mattpocock-skills:grill-with-docs` |
| `conjecture` | a ticket a formal round filed states no conjecture | `/ratchet:conjecture <ticket>` |
| `triage` | no agent-ready ticket, and an unblocked one needs triage | agree that ticket |
| `conjecture-spec` | no agent-ready ticket, and a spec with no conjecture, or with no `Flag:` line where it is asked | `/ratchet:conjecture` on the spec |
| `split` | no agent-ready ticket, and a spec with its conjecture, and its `Flag:` line where it is asked | `/mattpocock-skills:to-tickets` |
| `specify` | no agent-ready ticket | `/mattpocock-skills:to-spec` |
| `resolve` | a merged ticket is still open | mark it resolved, and where it is the unit's last open ticket, `documents.py expire` in the same command |
| `blocked` | every agent-ready ticket is blocked | finish the blocking ticket first, which never closes its unit |
| `blocked-run` | the first unblocked ticket is held by a blocked run | answer what the run asked, or land its guard edits |
| `answer` | ... parked by the drain for open questions | answer them on the ticket |
| `land-guard` | ... parked for guard edits | land the guard edits on the default branch |
| `safeguard` | ... parked for an owed safeguard | `/ratchet:safeguard` |
| `change-ticket` | ... parked by a note older than the kinds of hold or the base commit | change the ticket |
| `change-ticket` | ... the defect ticket, parked for the row it owes itself | change the ticket to meet what the drain parked it for |
| `drain` | an unblocked ticket states a current conjecture, and so does another, each with its `Flag:` line where it is asked | `/ratchet:feature` with no ticket |
| `build` | an unblocked ticket states a current conjecture, and its `Flag:` line where it is asked | `/ratchet:feature <ticket>` |
| `conjecture` | any other case, a labelled ticket whose `Flag:` line is missing or whose conjecture is stale among them | `/ratchet:conjecture <ticket>` |

## Steps

52 steps change the state, and one of them can also leave it as it was. The owner's and an agent's steps each follow one label. The others are things that happen: a command that stops part-way, a run that blocks, a park, an edit.

| Step | Follows |
| --- | --- |
| you run `/ratchet:init` | `init` |
| init stops before its questions | |
| you set up the tracker | `tracker` |
| you run `/ratchet:adopt`, which records the architecture and may leave comments owed; resumed after its pin, off its branch, it proves the adoption alone | `adopt` |
| an adoption by a plugin from before the architecture was asked | |
| the adoption stops part-way: on its branch before the pin, committed; or after the pin, before its proof, uncommitted | |
| an agent installs the hooks | `hooks` |
| you write the glossary and the brief, uncommitted | `grill` |
| you write a spec | `specify` |
| you state the spec's conjecture, and its `Flag:` line where it is asked | `conjecture-spec` |
| you split the spec into the two tickets | `split` |
| you agree a ticket | `triage` |
| you finish a ticket yourself; the finish of the last one, a step the route never names, deletes the unit with it | `blocked` |
| you state the ticket's conjecture, a round's ticket's among them, or draft a stale one again, and the `Flag:` line on the spec where it is asked | `conjecture` |
| you or an agent edit the criteria a stated conjecture was drafted against: the ticket's, or the spec's it inherits | |
| an agent builds the ticket on its branch | `build` |
| the build stops part-way: its code uncommitted on the branch, or on the branch it cut before it wrote anything | |
| the build run blocks and leaves its stash | |
| you answer the blocked run | `blocked-run` |
| the drain builds and merges a ticket | `drain` |
| the drain parks a ticket for open questions, for guard edits, or with an older note; the defect ticket, for the row it owes itself | |
| the drain merges the first ticket, which brings a new kind of code, and parks the second for the safeguard | |
| you answer on the ticket | `answer` |
| you land the guard edits | `land-guard` |
| you change the ticket, the defect ticket's among them | `change-ticket` |
| you mark the merged ticket resolved; the last one deletes the unit and carries its answers to the brief, records uncommitted | `resolve` |
| a new kind of code arrives, and a safeguard is owed | |
| an upgrade leaves rows for an agent to fill, and the architecture owed where it was never asked | |
| an upgrade asks the `Flag:` line before deployment, on the default branch or beside a built ticket's branch | |
| an agent fills the rows | `fill` |
| a kind of code that reopens the architecture arrives, and a review is owed | |
| an agent lands the architecture review | `review` |
| an agent binds the safeguard; or binding a formal row finds a defect, and the safeguard leaves the row owed and files the defect ticket | `safeguard` |
| you run `/ratchet:architecture`: the record and the parts on a branch, whose merge is yours | `architecture` |
| an upgrade finds a unit a plugin from before ADR 0064 left, at rest | |
| an agent runs `documents.py expire`: the unit deleted, uncommitted | `expire` |
| someone edits code | |
| an agent commits on a branch | `commit` |
| the branch merges, and the default branch is checked out; on a branch already merged it changes nothing | `merge` |
| the merge leaves the branch checked out: cut off before it switched, unable to switch, or made from another checkout | |
| someone checks out a merged branch, at rest | |
| an agent switches to the default branch | `switch` |
| an agent lands the records: merged, or on a branch that waits for you | `land-records`, `finish-records` |
| the landing run stops on its branch | |
| you merge the records branch | `merge-records` |
| code the formal model mirrors lands, at rest or while a round waits | |
| an agent runs the formal model's round: its change waits for your merge, or it files a ticket for the bug it found, with its conjecture or with none | `formal-upkeep` |
| you merge the round | `formal-round` |
| an agent fixes the bug the round found, and the fix merges | `formal-ticket` |
| merges land past the count since the last prune pass, and leave a page stale | |
| an agent prunes the comments on a branch, and the branch merges | `prune-comments` |
| an agent prunes the pages: a `docs(prune):` commit merged, or nothing cut and the pass recorded | `prune-pages` |

## Rules

A rule whose name starts `Real` is on what the real route names; the others are on what the pages say. Four are on the architecture an upgrade leaves owed, five on the `Flag:` line (ADR 0061), two on the adoption's proof, five on a stale conjecture, four on the steering unit (ADR 0064), five on the formal model (ADR 0062), and the last three on the prune pass (ADR 0065). Each rule on the pages holds in Python and in TLC, on every state. Four rules on the real route break, each only in states of [a divergence](#divergences) that names it. The other six on the real route hold: it names what the pages name everywhere but where an adoption stopped after its pin or a conjecture is stale.

| Rule | Says | On the model |
| --- | --- | --- |
| `AdviceCanBeFollowed` | The step the route names can be taken. | holds |
| `AdviceReachesRest` | Following the route's advice reaches a state with nothing left to finish. | holds |
| `FollowingAdviceLowersRank` | Each step the route names gets nearer to having nothing left to finish. | holds |
| `RealAdviceCanBeFollowed` | The step the real route names can be taken. | breaks in 33 states |
| `FollowingRealAdviceLowersRank` | Each step the real route names gets nearer to having nothing left to finish. | breaks in 4 states |
| `AdviceChangesTheState` | Taking the step the route names changes the state: its effect has not already happened. | holds |
| `RealAdviceChangesTheState` | Taking the step the real route names changes the state: its effect has not already happened. | holds |
| `AdviceNeverMergesAMergedBranch` | The route never names the merge of a branch the default branch already holds. | holds |
| `RealAdviceNeverMergesAMergedBranch` | The real route never names the merge of a branch the default branch already holds. | holds |
| `ArchitectureNamedExactlyWhenOwed` | The owed architecture is named exactly when the ledger owes it and nothing named before it is open. | holds |
| `OwedArchitectureNeverHidesTheWork` | Where the owed architecture is named, the step the route would otherwise name can be taken. | holds |
| `OnlyAnUpgradeOwesTheArchitecture` | Only an upgrade leaves the architecture owed; an adoption never does. | holds |
| `OnlyRecordingEndsTheOwedArchitecture` | Recording the architecture is the only step that ends it being owed. | holds |
| `FlagLineBeforeTheBuild` | Where a labelled ticket has no `Flag:` line, on it or on its spec, the route names no build and no drain. | holds |
| `RealFlagLineBeforeTheBuild` | Where a labelled ticket has no `Flag:` line, on it or on its spec, the real route names no build and no drain. | holds |
| `NoRunStartsWithoutTheFlagLine` | No feature run and no drain starts while a ticket's `Flag:` line is missing. | holds |
| `OnlyTheUpgradeLeavesTheFlagLineMissing` | Only the upgrade that asks the `Flag:` line before deployment leaves it missing. | holds |
| `TheUpgradeMovesNoTicket` | The upgrade that asks the `Flag:` line moves no ticket and no inheritance: a built ticket stays built. | holds |
| `UnprovedAdoptionNeverBuilds` | A repository whose adoption stopped before its proof is never told to build. | holds |
| `RealUnprovedAdoptionNeverBuilds` | The real route never tells a repository whose adoption stopped before its proof to build. | breaks in 2 states |
| `StaleConjectureBeforeTheBuild` | Where a ticket's conjecture is stale, the route names no drain, and no build but of a ticket whose conjecture is current. | holds |
| `RealStaleConjectureBeforeTheBuild` | Where a ticket's conjecture is stale, the real route names no drain, and no build but of a ticket whose conjecture is current. | breaks in 36 states |
| `NoRunStartsOnAStaleConjecture` | No feature run and no drain starts on a ticket whose conjecture is stale. | holds |
| `OnlyTheRedraftClearsAStaleConjecture` | Only `/ratchet:conjecture` ends a stale conjecture, and it leaves the ticket stated. | holds |
| `TheCriteriaEditMovesNothingElse` | An edit of the criteria makes a stated conjecture stale and changes nothing else. | holds |
| `NoStepLeavesTheUnitItExpires` | A step that makes the unit expired deletes it: only the upgrade that ships ADR 0064 finds one left in the tree. | holds |
| `ExpiredUnitBeforeTheWork` | Where an expired unit is left in the tree, the route names no spec, ticket, build or drain. | holds |
| `RealExpiredUnitBeforeTheWork` | Where an expired unit is left in the tree, the real route names no spec, ticket, build or drain. | holds |
| `TheOwnersAnswersOutliveTheUnit` | No unit is deleted with the owner's `## Answers` lines left out of the brief. | holds |
| `DefectTicketBeforeTheSafeguard` | Where a defect ticket is open, the route never names the safeguard: it is never offered the row the ticket names. | holds |
| `RealDefectTicketBeforeTheSafeguard` | Where a defect ticket is open, the real route never names the safeguard. | holds |
| `RoundBeforeBehind` | Where a round's ticket is open, or a round waits for the owner's merge, the route never names the round that behind asks for. | holds |
| `RealRoundBeforeBehind` | Where a round's ticket is open, or a round waits for the owner's merge, the real route never names the round that behind asks for. | holds |
| `UpkeepNeverHidesTheWork` | Where the route names a round or its merge, the step it would otherwise name can be taken: behind is never red. | holds |
| `PruneNamedExactlyWhenDue` | A prune pass is named exactly when one is due and nothing named before it is open: the review, a safeguard, the owed architecture and the formal model's upkeep come first, and the expired unit, the glossary and every ticket after it. | holds |
| `PruneNeverHidesTheWork` | Where a prune pass is named, the step the route would otherwise name can be taken: a due pass is never red. | holds |
| `CommentsBeforePages` | Where comments are owed, the route never names the pages pass: the comments come first. | holds |

The rank of a state is the longest run of advised steps from it to rest. The highest rank is 23.

On a branch the default branch already holds, the merge can still be run, and it changes nothing: the merge judge refuses the branch, as behind a merge commit that holds it, or as a change with nothing in it to judge. The model has that step, from each of the 342 states. So a route that named the merge there would name a step that can be taken, and `RealAdviceCanBeFollowed` would hold. That is how the defect this model once missed got past it: with no forge, after the merge left the checkout on the branch, the route named the same merge again, for ever. `RealAdviceChangesTheState` breaks there. TLA+ cannot see a step that leaves every variable as it was, so the rule is a fact of each state: no way of taking the step named leads back to the same state.

`ArchitectureNamedExactlyWhenOwed` names what comes before the architecture field by field, apart from the order the model asks in: the setup, the hooks, the checkout, a records branch, rows to fill, a review and a safeguard of a row no defect ticket names. So a change of that order breaks it. In 94 states the route names the owed architecture, and the step it would otherwise name is a build in 8 of them and the drain in 2.

Each rule on the formal model is kept by one step of the model, and the tests walk the model once without each, from an empty directory, and pin the shortest run that breaks it and every rule that breaks with it. Without the safeguard's pass over the defect's row, the model reaches 3232 states and `DefectTicketBeforeTheSafeguard` breaks in 26 of them, as does `AdviceCanBeFollowed`, since binding the row is red while the ticket is open, and `AdviceReachesRest` in 54. The shortest run is 15 steps: `/ratchet:init`, the tracker, `/ratchet:adopt`, the hooks, the glossary and brief committed and merged, a spec written and landed, split and landed, the owner finishing both tickets, a new kind of code, and the safeguard finding a defect. Read as an owed safeguard, the drain's park of the defect ticket breaks the same three rules in 3 states, first after 17 steps: those 15, the ticket's conjecture, and the drain's park. With behind asked before a round's ticket, the model reaches 3186 states and `RoundBeforeBehind` breaks in 36, `AdviceCanBeFollowed` in 36, `AdviceReachesRest` in 68 and `UpkeepNeverHidesTheWork` in 4, first after 15 steps: the 13 to rest, mirrored code, and the round that files a ticket. With behind made red, so that no feature step is taken while it holds, the model reaches 3210 states and `UpkeepNeverHidesTheWork` alone breaks, in 8, first after 21 steps: the 13 to rest, mirrored code, a spec written and landed, its conjecture stated and landed, split and landed, and a ticket agreed, whose build behind then hides. With a round's ticket that states no conjecture named for its fix, the model reaches 3232 states and `AdviceCanBeFollowed` breaks in 4 of them, since the preflight refuses the run, and `AdviceReachesRest` in 20, first after 15 steps: the 13 to rest, mirrored code, and the round that files a ticket with no conjecture.

So is each rule on the prune pass. With the prune asked before the review, the model reaches 3449 states and `PruneNamedExactlyWhenDue` breaks in 123 of them, and `PruneNeverHidesTheWork` in 6, first after 15 steps: the 13 to rest, merges that leave a page stale, and a new kind of code, whose safeguard the pass is then named before. With the prune asked after the tickets, the model reaches 3514 states and `PruneNamedExactlyWhenDue` breaks in 67, and `AdviceReachesRest` in 478, since at rest the route names the next spec and never the pass, first after 4 steps: `/ratchet:init`, the tracker, the adoption that leaves comments owed, and the hooks. With the work beside a due pass taken away, so that no other step is taken while it is named, the model reaches 3051 states and `PruneNeverHidesTheWork` alone breaks, in 10, first after the same 4 steps. With the pages asked before the comments, the model reaches 3232 states and `CommentsBeforePages` alone breaks, in 12, first after 8 steps: those 4, the glossary and brief committed and merged, and merges that leave a page stale.

The upgrade that asks the `Flag:` line arrives in 358 steps, and in 275 it leaves the line missing. 30 of the 358 arrive beside a built ticket's branch, and 12 of those leave the line missing; the built ticket goes on to its merge. In 23 of the 368 states where the line is missing the route names a conjecture: the ticket's in 22, and the spec's in 1. In the others something it names first comes before it: the checkout, a records branch, a fill, a review or a safeguard, a hold, triage, a merged ticket to resolve, or a blocking ticket. Three steps end a missing line: the ticket's conjecture, in 192 steps, the spec's conjecture, in 4, and the owner finishing the last ticket that lacked it, in 11.

The tests also check that:

- following the advice never comes back to a state it passed, except through rest;
- every state reaches rest by some steps;
- each kind of hold is released by the one step the route names for it, and by no other: a ticket parked for guard edits stays parked when another ticket is agreed or the binding changes, and a note older than the kinds of hold, or a guard hold with no base commit, waits for a change to the ticket;
- the edit makes a conjecture stale on a ticket and on the spec, both tickets at once there; from every state where the route names the conjecture and a conjecture is stale, the redraft clears one; and where the stale ticket is the one the route would otherwise build, the route names the conjecture while the real route names a build or the drain;
- on a real repository, every ticket the world writes has `## Acceptance criteria` and every spec `## User Stories`, and each conjecture it writes ends with a `- Criteria:` line whose digest is what the conjecture skill's own command prints, read from the skill page; the edit adds a criterion, and the redraft writes the line again;
- on a real repository, the real route names what the model says after every one of the 8501 steps, each taken from its source state as the shortest path from an empty directory builds it; where the drain parks a ticket for the safeguard, it parks on what the real `triggers.py` printed for the ticket's change; where it names `resolve` or `blocked`, its command names `documents.py expire` exactly where the step it names expires the unit;
- on a real repository whose binding runs `merge.py`, the merge the route names, of the adoption's branch before its proof or of a working branch, leaves the default branch checked out and keeps the branch, and the route names what the model says after it; checked out again, the branch is named for the switch, a second merge of it changes nothing, and the switch goes back;
- on such a repository, a merge with a dirty tree, or with the default branch checked out elsewhere, leaves HEAD on the branch and its `next:` line names the switch first, and a merge run from the default branch leaves HEAD there; the route then names what the model says;
- on a real repository, a build that stops on the branch it cut is left by the switch and built again, and following the route from there never comes back to it;
- the walk leaves records waiting on branches of both kinds of name, and on a real repository, records waiting on a branch an agent named are named for their merge, those of a spec and those of the two tickets;
- where the real route names the owed architecture, its `then:` names the step it names once the architecture is no longer owed, and its `why:` offers carrying on with it; that step is the one the model gives beneath, read as the real route reads the state;
- where the real route names a safeguard, its `then:` names the step it names once the rows are bound, which is the owed architecture exactly where the model owes one, and its `why:` still offers carrying on with the work;
- where the real route names a prune pass, its `then:` names the step it names once no pass is due, which is the one the model gives beneath, read as the real route reads the state, and its `why:` offers carrying on with it, since nothing is red for it;
- where the real route names a round's ticket, a round's merge or a round, its `then:` names the step it names once the formal model is in sync, which is the one the model gives beneath, and its `why:` offers carrying on with it, since nothing is red for it;
- on a real repository, the first mirrored code brings the formal model and the binding's `## Formal verification` section, synced to the default branch's tip; a round that changes the model writes the `- Synced:` line on a `formal/upkeep-` branch the owner merges, and a round that finds a bug keeps its update on a `formal/kept-` branch and files a ticket with the `Formal model: round` line, with its conjecture or with none, which is named for its conjecture before its fix; the defect ticket names `formal verification of state`, the row the binding leaves `later:`, and the drain parks it on what the real `triggers.py` printed for its change;
- each rule on the formal model, and each on the prune pass, breaks without the step that keeps it, with the rules and the shortest run above;
- a pass is due in states where each of the hooks, the checkout, a records branch, a fill, a review, a safeguard, the architecture and the formal model's upkeep is named first, and where the route names a pass it offers beside it, somewhere, each of the next spec, the triage, a build, a mark, the expired unit, the glossary and the blocking ticket; only the adoption leaves comments owed, only merges make a pass of the pages due, and only each pass ends its own;
- on a real repository, the adoption that leaves comments owed writes one into `quality/owed.toml` beside a commented line, the pass of the comments deletes the comment and settles the entry, merges that leave a page stale are ten merged changes of a file `README.md` cites, and the walk takes passes of the pages that cut a line of `README.md` in a `docs(prune):` commit and passes that cut nothing and run `documents.py passed`;
- on a real repository, a commit on the default branch that a guard hold did not name leaves the ticket held, and landing the guard edits releases it;
- on a real repository, the architecture check finds nothing after `/ratchet:adopt`, and the real `upgrade.py --apply` then owes no architecture;
- on a real repository an older plugin adopted, the real upgrade owes exactly what the architecture check finds, the route names the fill, which the real upgrade settles once the rows are written, then `/ratchet:architecture`, then its branch's merge, and the check finds nothing after;
- the real upgrade also owes every other finding red at the merge base, which the route never names, and on the test's adoption, which writes the quality files the adopt templates hold, it leaves nothing red and exits 0, as every upgrade in the tests must;
- on a real repository that owes the architecture, a review and a safeguard that arrive beside it are each named before it;
- a drain note older than a blocked run's stash, or one with no base commit, does not release the run;
- on long runs of steps, a repository that loses its commit hooks is told to install them first, whatever else is true;
- the steps that expire the unit are the mark of the last ticket, which leaves records uncommitted with the answers carried, and the owner finishing the last ticket, which commits; the route names the landing after the mark, and only the landing, by an agent or by the owner's merge, ends the carry; a left unit is named for its deletion, which leaves records alone;
- where the route names `blocked`, no unit is left and the finish it names never expires the unit, though the owner's finish expires it elsewhere;
- without the deleter, `NoStepLeavesTheUnitItExpires` breaks and no other of the unit's rules does, and without the answers it carries, `TheOwnersAnswersOutliveTheUnit` does; each first breaks after the 13 steps above;
- on a real repository, the step that resolves the last ticket deletes `.scratch/<slug>/` and appends each of its `## Answers` lines to the brief, as `documents.py expire` will, and the sweep asks the real route after it;
- on a real repository, after the mark of the last ticket the tree holds the deletion of the unit's files and an edit of the brief that removes nothing and adds only lines that are each, the date set aside, an `## Answers` line of a deleted file.

## The `also:` lines

After `next:`, `why:` and `then:` the route prints its `also:` lines: the upgrade, a standing answer, a sign of a life event. ADR 0061 adds one more: each switch past its removal date, as `flags.py --expired` lists it, with no forge and before `/ratchet:safeguard` binds the row `no flag past its removal date`. It outranks no step.

An `also:` line needs no state of the model. The model's label is the `next:` line alone, and an `also:` line changes no step: it is read from the registry and the date, and nothing the owner or an agent does next turns on it. So the model leaves the lines out, and two tests on the real route hold the new one instead:

- `test_a_switch_past_its_removal_date_outranks_no_step` walks from an empty directory through a whole feature. At each stop where the default branch is checked out clean, the route on a copy whose default branch declares a switch past its date prints the same `next:`, `why:`, `then:` and stage as without it. This holds today.
- `test_the_route_names_each_switch_past_its_removal_date` takes the shortest run, `/ratchet:init`, the tracker, `/ratchet:adopt` and the hooks, then commits that switch on the default branch, and expects an `also:` line naming it. `scripts/route.py` prints one such line for each switch past its date, so this holds.

## Divergences

Where the pages make a promise that the rule they give for it does not keep, and the real route follows the rule, the model follows the promise and names the place as a divergence. The tests hold the route to the model with the divergences applied. Each one also has a test that expects the route to keep the promise and fails. That test turns red the day the route is fixed. Then the divergence is deleted from `route_model.py`.

The real route reads a state through every divergence that holds there, as the script does. No state of the model holds both: where an adoption stopped after its pin the model takes no edit of the criteria, so no conjecture there is stale.

There are two.

- `adoption-proved-by-its-pin`. [The adopt skill](../../skills/adopt/SKILL.md) says of its proof: "Done when `check` is green in a fresh worktree of the commit, `conformance` and `check-deep` exit 0, no rehearsal worktree is left, and the rehearsal passed, or its second round ended with the edits it still needs named." `scripts/route.py`, `adoption_step`, reads a binding with no plugin pin as an adoption that stopped, and one with a pin as adopted. The pin is written in step 11 and the proof run in step 12, so where the adoption stopped between them the real route reads it as finished. The shortest run: `/ratchet:init`, the tracker, the adoption stopped after its pin, the hooks, its work committed and merged as the route names, the glossary and brief committed and merged, a spec written and landed, its conjecture stated and landed, split and landed, the first ticket agreed. The model names `/ratchet:adopt`. The real route names the ticket's build. In the 29 states where the adoption stopped after its pin, the real route names what it names once the adoption is done. In 2 that is a build, so `RealUnprovedAdoptionNeverBuilds` breaks. In 4 it is the commit or the merge of the adoption's own branch, or the next spec, before the first feature and after it, each a step that leads further from rest, so `FollowingRealAdviceLowersRank` breaks. The sweep holds the real route to what it names today, and a strict expected failure replays the shortest run and expects `/ratchet:adopt`.
- `stale-conjecture-read-as-stated`. [The records page](../../skills/method/records.md) says of a conjecture gone stale: "When the criteria change after it, `/ratchet:conjecture <ticket>` finds another digest, drafts the three sections again, keeps every line under `## Answers`, asks only the forks the change opened, and prints what changed in the conjecture." No script reads the `- Criteria:` line. `scripts/feature_preflight.py`, `conjecture_failure`, asks only that `## Conjecture` states something, and `scripts/route.py`, `unstated`, asks the preflight. So the real route reads a stale ticket as stated, and the rule the route page gives, a build for one ticket that states a conjecture and the drain for more, sends it to a build. The shortest run, 14 steps: `/ratchet:init`, the tracker, `/ratchet:adopt`, the hooks, the glossary and brief committed and merged, a spec written and landed, split and landed, the first ticket agreed and its own conjecture stated, then an edit of its acceptance criteria. The model names `/ratchet:conjecture` on the first ticket. The real route names its build. In 36 states a stale conjecture is where the real route names a build or the drain. In 33 of them the pages name the conjecture, and the step the real route names is a run no preflight should start, so `RealAdviceCanBeFollowed` breaks there. In all 36 `RealStaleConjectureBeforeTheBuild` breaks: 24 builds of the stale ticket and 12 drains, 3 of them where the pages name the build of the other ticket, whose conjecture is current, and the drain would build the stale one after it. The same reading reaches the step the route offers beside the owed architecture: in 11 of the 94 states that name it, the real route's `then:` offers a build or the drain where the pages offer the conjecture or the build of the other ticket. The sweep holds the real route to what it names today, and a strict expected failure replays the shortest run and expects the conjecture.

Four more were deleted. `flag-line-waits-for-deployed` went when the preflight began to ask the `Flag:` line of every declared type the catalogue asks it of, whatever the life events. The model was written with another, a guard hold that outlived the edits it asked for. It went when the drain's note began to record the paths a run named as its cause, and the default branch's commit when it parked. Landing those paths releases the hold. `expired-unit-unread` went when `scripts/route.py` began to name `documents.py expire` where an expired unit is left, at the stage `expire`. `carried-answers-read-as-code` went when the merge judge began to take, in a records change, the lines `expire` carries to the brief beside the files it deletes, and to read a ticket as closed where the same change deletes its whole unit, so the mark of the last ticket and its deletion land as records.

## What the next script change must do

Each item below flips the pins it names. Delete each pin in the same change.

- The adoption's proof, decided by the project's owner as recommended here. `skills/adopt/SKILL.md`: the last step writes one binding line, `- Adopted: <plugin version>`, once the proof is green. `scripts/route.py`, `adoption_step`: a binding with no such line is an adoption still running, and the route names `/ratchet:adopt` to resume it. `scripts/upgrade.py` writes the line for a binding that already has a pin, so an adoption finished before the change is not sent back. `scripts/conformance.py` requires the line once the binding exists. The world's resume in `route_world.py` then writes the line as the skill does. The strict expected failure `test_the_route_names_what_the_pages_say_where_it_diverges[adoption-proved-by-its-pin]` passes, which is red; delete the entry from `DIVERGENCES`. The model has none of this until the scripts do.
- A stale conjecture, decided by the project's owner: the route and the preflight read the digest. `scripts/feature_preflight.py` refuses it, and `scripts/route.py` follows, since it reads a ticket as stated only where the preflight finds its conjecture. The strict expected failure `test_the_route_names_what_the_pages_say_where_it_diverges[stale-conjecture-read-as-stated]` then passes, which is red; delete the entry from `DIVERGENCES`. What the change must do:
  - The route reason. Where the first free ticket nothing holds has a stale conjecture, the route names `/ratchet:conjecture <ticket>`, at the stage `conjecture`, and its `why:` says that the ticket's criteria changed after its conjecture was written. A stale ticket is not counted among the tickets that state a conjecture, so it is neither built nor drained, and the other ticket is built alone where its conjecture is current.
  - The preflight refusal. `conjecture_failure` refuses a ticket whose conjecture is stale, naming the ticket, the heading its `- Criteria:` line names, and `/ratchet:conjecture <ticket>` as the command that drafts it again. The drain reads the same refusal, so it starts no run on it.
  - The digest. Read the line's heading and digest, and compute the digest of that section as the conjecture skill's command does: the lines from the heading to the next `## ` heading, carriage returns dropped, blank lines dropped, as git hashes a blob. The world computes its fixtures with that command, read from the skill page, so a script that computes another digest reads every world ticket as stale and the sweep goes red at once.
  - The line is not a statement. `states_a_conjecture` judges the section without its `- Criteria:` line, so a `## Conjecture` that holds that line alone states no conjecture. Today it counts as stated: `test_a_conjecture_that_holds_only_its_criteria_line_states_none` is a strict expected failure that passes, which is red, when this is done; take its mark away.
  - No line, no staleness. A conjecture with no `- Criteria:` line is current, as the skill says, so a ticket written before the line was added, or by hand, is not sent back.
  - Inheritance. A ticket with no `## Conjecture` of its own takes its parent spec's, and is checked against the digest of the spec's `## User Stories`, the heading the spec's line names. The ticket's own `## Acceptance criteria` are not read for it, so an edit of them is not noticed. That is a known gap of the skill, and this change does not close it.
  - Neither heading. A body with neither `## Acceptance criteria` nor `## User Stories` gets no line from the skill, so its conjecture is current. A line that names a heading the body no longer has is stale: the skill's command then prints the digest of an empty section, which is not the one the line holds.
  - The model has all of this but the two cases of the last item, which are argued here and not modelled: its tickets and its spec always have their heading, and the line always names it.

## Charts

The rules were checked on `Route.tla`, SHA-256 `f7346e586db2f20b90fc54d87a347be2dd4175c30739fbca75e65f3644ad6cee`. Run with the 33 rules that hold, TLC reached 3232 states, and its last line counts 8502 states generated: the start and 8501 steps. Nothing deadlocks. Run once for each of the Four rules that break, with one worker, TLC stops at the shortest run that breaks it. For `RealAdviceCanBeFollowed` and `RealStaleConjectureBeforeTheBuild` that is the shortest run of the stale conjecture's divergence, 14 steps from an empty directory, the last of them the edit of the criteria. For `RealUnprovedAdoptionNeverBuilds` it is the shortest run of the adoption's, 16 steps. `FollowingRealAdviceLowersRank` breaks after 5: `/ratchet:init`, the tracker, the adoption stopped after its pin, the hooks, and the commit the real route names on the adoption's branch, where the pages name `/ratchet:adopt`.

Each of the two walks without the deleter was exported the same way and run with its one rule and one worker. TLC stops at the 13 steps the tests pin, the owner finishing the second ticket last. So was each of the four walks without the step that keeps a rule on the prune pass, run with that rule: TLC stops at the 15, 4, 4 and 8 steps the tests pin.

That module is too large for the statechart tool to draw. The charts are drawn from `RouteShown.tla`, SHA-256 `b248235f695e4beb87fcb0d9881f71a10c842a1f094b05be80adeff5a4b00819`: the same graph seen through what the charts show, which is what the route names, what it would name beneath, the checkout, what is owed, whether the `Flag:` line is missing, the formal model's upkeep as the route reads it, and whether a defect ticket is open. It shows the four kinds of park as one release beneath, and the build and the drain as one build, so that it stays within what the tool draws. Each chart's report says that TLC reached 489 states and 1724 steps there, and that nothing deadlocks.

A stale conjecture has no chart of its own, and neither has the prune pass. Seen with whether a conjecture is stale, the summary has 650 states, past the 500 the statechart tool draws. The work chart shows it: the edit of the criteria leads from "Build the agent-ready tickets" to the conjecture, which the real route reads as the build. Seen with whether comments are owed and a pass of the pages is due, the summary has 549 states. The work chart shows the pass where the route names it: the hooks installed after an adoption that leaves comments owed lead to the comments, merges that leave a page stale lead to the pages, and each pass leads back to the work beside it. A state where a pass is due and unfinished work comes first shows the work beside the pass, so the landing that finishes it leads to the pass.

An expired unit needs no state of the summary of its own: where one is left the route names `expire`, which no other state names. The work chart shows the older unit's arrival and its deletion. The checkout's chart shows the mark of the last ticket leaving records uncommitted, and the records branch their landing can leave waiting, each read by the real route as the pages read it.

Each chart gives each of those states one label, and draws an arrow where a step changes the label. A step that keeps the label is not drawn. The summary carries no rule, so the charts have no legend. A state where the real route names something else says so in its label.

### The work

What the route names once nothing in the checkout is unfinished, no records branch waits and no rows to fill, safeguard, review or architecture is owed. Where the route names the formal model's upkeep, this chart shows the work it offers beside it. Where it names a prune pass, this chart shows the pass, and the work beside it leads on from there. Four labels share one state: the drain's three kinds of park and the park for a safeguard are "Release the ticket the drain parked", and so is the defect ticket's park for its own row. `build` and `drain` are "Build the agent-ready tickets". A state where the real route names something else says so after "yet the route names". Two do. Where an adoption stopped after its pin, the pages name `/ratchet:adopt` and the real route names what follows it, whatever the checkout and the tickets hold; that is one state here, and the resume leaves it for wherever the work stands. Where a ticket's conjecture is stale, the pages name its conjecture, and the real route names the build. Where the pages name the build of one ticket and the real route the drain, which would build a stale ticket after it, the chart shows the build alone, since both are "Build the agent-ready tickets".

21 states and 84 arrows. It does not draw the 1137 steps that keep the label, which are most of the checkout's steps. A round that files a ticket leads from the next spec to a build, or to the ticket's conjecture where it states none, and the safeguard that files a defect ticket leads to its conjecture.

```mermaid
---
title: "What the route names once nothing is unfinished"
---
stateDiagram-v2
  s0: Run /ratchet:init
  s1: Set up the tracker
  s2: Run /ratchet:adopt
  s3: Install the commit hooks
  s4: Run /ratchet:adopt, yet the route names what follows it
  s5: Write the glossary and design brief
  s6: Prune the owed comments
  s7: Write the next spec
  s8: State the spec's conjecture
  s9: Prune the pages
  s10: Split the spec into tickets
  s11: Agree a ticket for agents
  s12: Finish the blocking ticket first
  s13: Build the agent-ready tickets
  s14: The ticket is built on its branch
  s15: Mark the merged ticket resolved
  s16: Delete the expired unit
  s17: State the ticket's conjecture
  s18: State the ticket's conjecture, yet the route names: Build the agent-ready tickets
  s19: Answer what the blocked run asked
  s20: Release the ticket the drain parked
  [*] --> s0
  s0 --> s1: you run /ratchet:init
  s1 --> s2: you set up the tracker
  s2 --> s3: you run /ratchet:adopt or an older plugin's adoption
  s2 --> s4: the adoption stops part-way
  s3 --> s5: an agent installs the hooks
  s3 --> s6: an agent installs the hooks
  s4 --> s3: you run /ratchet:adopt
  s5 --> s7: you write the glossary and brief
  s4 --> s5: you run /ratchet:adopt
  s6 --> s5: an agent prunes the comments
  s6 --> s7: an agent deletes the expired unit or you mark the ticket resolved or you write the glossary and brief or an agent prunes the comments or an upgrade leaves rows for an agent to fill or a kind that reopens the architecture arrives or code the formal model mirrors lands or a new kind of code arrives
  s6 --> s8: you write a spec or an agent prunes the comments
  s6 --> s9: an agent prunes the comments
  s6 --> s10: you state the spec's conjecture or an agent prunes the comments
  s6 --> s11: you mark the ticket resolved or you split the spec or an agent prunes the comments
  s6 --> s12: an agent prunes the comments
  s6 --> s13: an agent prunes the comments
  s6 --> s14: an agent builds the ticket
  s6 --> s15: an agent prunes the comments
  s6 --> s16: an agent prunes the comments
  s7 --> s6: an agent lands the records or an agent fills the rows or an agent lands the architecture review or an agent binds the safeguard or you merge the round or you merge the records branch or the branch merges
  s7 --> s9: an agent lands the records or merges leave a page stale or an agent fills the rows or an agent lands the architecture review or an agent binds the safeguard or you merge the round or you merge the records branch or the branch merges
  s4 --> s7: you run /ratchet:adopt
  s7 --> s8: you write a spec
  s7 --> s16: an upgrade finds a unit an older plugin left
  s8 --> s6: an agent lands the records or you merge the records branch
  s8 --> s9: an agent lands the records or you merge the records branch
  s9 --> s7: an agent deletes the expired unit or you mark the ticket resolved or an agent prunes the pages or an upgrade leaves rows for an agent to fill or a kind that reopens the architecture arrives or code the formal model mirrors lands or a new kind of code arrives
  s9 --> s8: you write a spec or an agent prunes the pages
  s9 --> s10: you state the spec's conjecture or an agent prunes the pages
  s9 --> s11: you mark the ticket resolved or you split the spec or an agent prunes the pages
  s9 --> s12: an agent prunes the pages
  s9 --> s13: an agent prunes the pages
  s9 --> s14: an agent builds the ticket
  s9 --> s15: an agent prunes the pages
  s9 --> s16: an agent prunes the pages
  s8 --> s10: you state the spec's conjecture
  s8 --> s11: you split the spec
  s10 --> s6: an agent lands the records or you merge the records branch
  s10 --> s9: an agent lands the records or you merge the records branch
  s11 --> s6: an agent lands the records or you merge the records branch
  s11 --> s9: an agent lands the records or you merge the records branch
  s10 --> s11: you split the spec
  s10 --> s8: an upgrade asks the Flag: line before deployment
  s11 --> s7: you finish a ticket yourself
  s11 --> s12: you agree a ticket
  s11 --> s13: you agree a ticket
  s11 --> s17: you agree a ticket
  s12 --> s13: you finish a ticket yourself or you agree a ticket
  s12 --> s18: you agree a ticket or you finish a ticket yourself
  s12 --> s17: you agree a ticket or you finish a ticket yourself
  s13 --> s14: an agent builds the ticket or the build stops part-way
  s13 --> s15: the drain builds and merges a ticket or the drain merges one and parks the next for a safeguard
  s13 --> s18: you edit the criteria
  s13 --> s19: the build run blocks
  s13 --> s20: the drain parks a ticket
  s13 --> s17: an upgrade asks the Flag: line before deployment or you edit the criteria
  s14 --> s6: the branch merges
  s14 --> s9: the branch merges
  s14 --> s15: the branch merges or the merge leaves the branch checked out
  s15 --> s7: you mark the ticket resolved
  s15 --> s11: you mark the ticket resolved
  s15 --> s17: you mark the ticket resolved
  s15 --> s13: you mark the ticket resolved
  s16 --> s7: an agent deletes the expired unit
  s7 --> s13: an agent runs the formal model's round
  s7 --> s17: an agent runs the formal model's round or an agent binds the safeguard
  s13 --> s7: an agent fixes the bug the round found
  s17 --> s13: you state the ticket's conjecture
  s18 --> s17: an upgrade asks the Flag: line before deployment
  s18 --> s13: you state the ticket's conjecture
  s17 --> s18: an agent lands the records or you state the ticket's conjecture or you merge the records branch or an agent binds the safeguard or an agent fills the rows or an agent lands the architecture review or the branch merges or an agent switches to the default branch
  s19 --> s13: you answer the blocked run
  s20 --> s13: you answer on the ticket or you land the guard edits or you change the ticket or an agent binds the safeguard
  s19 --> s17: you answer the blocked run
  s20 --> s17: you answer on the ticket or you land the guard edits or you change the ticket or an agent binds the safeguard
  s4 --> s8: you run /ratchet:adopt
  s4 --> s10: you run /ratchet:adopt
  s4 --> s11: you run /ratchet:adopt
  s15 --> s20: you mark the ticket resolved
  s4 --> s13: you run /ratchet:adopt
  s4 --> s12: you run /ratchet:adopt
  s4 --> s14: you run /ratchet:adopt
  s4 --> s15: you run /ratchet:adopt
```

### The checkout

What is unfinished in the checkout, which the route names before any work. A merged branch still checked out is left by a switch, never merged again. The merge of it, which changes nothing, keeps the label and is not drawn. The real route names the same as the pages in every state here, the records the mark of the last ticket leaves among them.

8 states and 19 arrows. It does not draw the 901 steps that keep the label: the setup, the tickets, and what is owed.

```mermaid
---
title: "What the route names first: unfinished work in the checkout"
---
stateDiagram-v2
  s0: Nothing unfinished
  s1: A clean branch to merge
  s2: Code uncommitted on a branch
  s3: Records uncommitted
  s4: Code uncommitted on the default branch
  s5: A records branch waits for your merge
  s6: A landing run stopped part-way
  s7: A merged branch still checked out
  [*] --> s0
  s0 --> s1: the adoption stops part-way or an agent builds the ticket or you run /ratchet:architecture
  s0 --> s2: the adoption stops part-way or the build stops part-way
  s1 --> s0: you run /ratchet:adopt or the branch merges
  s1 --> s2: the adoption stops part-way or someone edits code
  s2 --> s0: you run /ratchet:adopt
  s0 --> s4: you write the glossary and brief or someone edits code
  s2 --> s1: an agent commits on a branch
  s0 --> s3: an agent deletes the expired unit or you mark the ticket resolved or you write a spec or you state the spec's conjecture or you split the spec
  s3 --> s0: an agent lands the records
  s3 --> s5: an agent lands the records
  s3 --> s6: the landing run stops
  s4 --> s1: an agent commits on a branch
  s0 --> s7: someone checks out a merged branch or the build stops part-way
  s1 --> s7: the merge leaves the branch checked out
  s5 --> s0: you merge the records branch
  s6 --> s0: an agent lands the records
  s6 --> s5: an agent lands the records
  s7 --> s0: an agent switches to the default branch
  s7 --> s2: someone edits code
```

### Fills, safeguards and reviews

Rows an upgrade left for an agent to fill are named first of these, and no feature or drain starts until they are written (ADR 0050); the model takes the feature run and the drain away while they are owed. A safeguard or an architecture review that is owed is named before the next feature, and after anything unfinished in the checkout. A review comes before a safeguard (ADR 0052): with both owed, landing the review leaves the safeguard named. A safeguard whose only row a defect ticket names waits on that ticket, whose work the route names until it is marked resolved.

8 states and 15 arrows. It does not draw the 1344 steps that keep the label.

```mermaid
---
title: "Fills, safeguards and reviews: named before the next feature, never instead of it"
---
stateDiagram-v2
  s0: No fill, review or safeguard owed
  s1: Rows to fill: the route names it
  s2: Review owed: the route names it
  s3: Owed: the route names it
  s4: Rows to fill: unfinished work comes first
  s5: Review owed: unfinished work comes first
  s6: Owed: unfinished work comes first
  s7: Owed: the defect ticket comes first
  [*] --> s0
  s0 --> s1: an upgrade leaves rows for an agent to fill
  s0 --> s2: a kind that reopens the architecture arrives
  s0 --> s3: a new kind of code arrives or the drain merges one and parks the next for a safeguard
  s1 --> s0: an agent fills the rows
  s1 --> s4: you write a spec or you state the spec's conjecture or you split the spec or you run /ratchet:architecture
  s2 --> s0: an agent lands the architecture review
  s2 --> s5: you write a spec or you state the spec's conjecture or you split the spec or an agent builds the ticket or the build stops part-way or you mark the ticket resolved
  s3 --> s0: an agent binds the safeguard
  s3 --> s6: you write a spec or you mark the ticket resolved or you state the spec's conjecture or you split the spec or an agent builds the ticket or the build stops part-way or you run /ratchet:architecture
  s3 --> s7: an agent binds the safeguard
  s4 --> s1: an agent lands the records or you merge the records branch or the branch merges or an agent switches to the default branch
  s5 --> s2: an agent lands the records or you merge the records branch or the branch merges or an agent switches to the default branch
  s6 --> s3: an agent lands the records or you merge the records branch or the branch merges or an agent switches to the default branch
  s7 --> s6: you mark the ticket resolved
  s2 --> s3: an agent lands the architecture review
```

### The architecture

`/ratchet:adopt` records the architecture, and an adoption by an older plugin never asked it. An upgrade owes it where it was never asked (ADR 0049). The route names it after the rows to fill, a review and a safeguard, and offers beside it the step it would otherwise name, so it never hides a build or the drain. `/ratchet:architecture` leaves the record on a branch whose merge is the owner's: this chart draws that as recorded, and the checkout's chart draws the merge.

7 states and 15 arrows. It does not draw the 1619 steps that keep the label.

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
  s0 --> s1: you run /ratchet:adopt
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

The upgrade that ships ADR 0061 leaves the line missing where a record in flight states its conjecture. The route then names a conjecture once nothing it names first is open: the spec's, where the spec is not yet split, and the ticket's otherwise. That conjecture lands the line on the spec for both tickets. A held ticket is released first, and then needs its conjecture. A ticket the owner finishes themself closes without it. The real route names the same in every state here.

5 states and 13 arrows. It does not draw the 1621 steps that keep the label.

```mermaid
---
title: "The Flag: line: asked before deployment, stated before any build"
---
stateDiagram-v2
  s0: No Flag: line is missing
  s1: Missing: other work comes first
  s2: Missing: the route names the spec's conjecture
  s3: Missing: the route names the ticket's conjecture
  s4: Missing: a held ticket is released first
  [*] --> s0
  s0 --> s2: an upgrade asks the Flag: line before deployment
  s0 --> s1: an upgrade asks the Flag: line before deployment
  s0 --> s3: an upgrade asks the Flag: line before deployment
  s1 --> s0: you state the ticket's conjecture or you finish a ticket yourself or you state the spec's conjecture
  s2 --> s0: you state the spec's conjecture
  s2 --> s1: you split the spec
  s1 --> s3: you agree a ticket or you finish a ticket yourself or an agent lands the records or you merge the records branch or an agent binds the safeguard or an agent fills the rows or an agent lands the architecture review
  s0 --> s4: an upgrade asks the Flag: line before deployment
  s3 --> s0: you state the ticket's conjecture
  s4 --> s0: you state the ticket's conjecture
  s4 --> s3: you answer the blocked run or you answer on the ticket or you land the guard edits or you change the ticket
  s1 --> s2: an agent fills the rows or an agent lands the architecture review or an agent binds the safeguard
  s1 --> s4: an agent binds the safeguard or an agent lands the architecture review
```

### The formal model

Mirrored code that lands puts the model behind, and the route then offers a round beside the work, never instead of it. A round either waits for the owner's merge, which leads back to in sync, or behind where more mirrored code landed meanwhile, or files a ticket for the bug it found, whose fix is mirrored code and leads to behind. A ticket filed with no conjecture is named for its conjecture first. Unfinished work in the checkout comes first of each. Binding a formal row that finds a defect leads to a defect ticket, which the safeguard's row waits on until it is marked resolved. The real route names the same as the pages in every state here.

8 states and 14 arrows. It does not draw the 1662 steps that keep the label.

```mermaid
---
title: "The formal model: behind is offered beside the work, a round and its ticket come first"
---
stateDiagram-v2
  s0: In sync, or no model
  s1: Behind: the route offers a round beside the work
  s2: Behind: unfinished work comes first
  s3: A round waits for your merge
  s4: A round's ticket: the route names it
  s5: A round's ticket states no conjecture: the route names its conjecture
  s6: A defect ticket: the safeguard's row waits on it
  s7: A round waits: unfinished work comes first
  [*] --> s0
  s0 --> s1: code the formal model mirrors lands
  s1 --> s2: you write a spec or you state the spec's conjecture or you split the spec or an agent builds the ticket or you mark the ticket resolved
  s1 --> s3: an agent runs the formal model's round
  s1 --> s4: an agent runs the formal model's round
  s1 --> s5: an agent runs the formal model's round
  s0 --> s6: an agent binds the safeguard
  s2 --> s1: an agent lands the records or you merge the records branch or the branch merges
  s3 --> s0: you merge the round
  s3 --> s1: you merge the round
  s3 --> s7: you write a spec or you state the spec's conjecture or you split the spec or an agent builds the ticket or you mark the ticket resolved
  s4 --> s1: an agent fixes the bug the round found
  s5 --> s4: you state the ticket's conjecture
  s7 --> s3: an agent lands the records or you merge the records branch or the branch merges
  s6 --> s0: you mark the ticket resolved
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
- Rows to fill owed together with a safeguard or a review. The model's upgrade arrives at rest, or beside a due prune pass, and leaves rows to fill alone; `test_route.py` holds the route's order where they are owed together.
- A review or a new kind that arrives while the architecture is owed. The model's arrivals come at rest, or beside a due prune pass; a test walks a review and a safeguard that arrive beside it on a real repository. A safeguard beside it is in the model, from the drain.
- An adoption by an older plugin that stops part-way, an upgrade that owes the architecture and no rows to fill, and an architecture recorded a second time to revisit it. An upgrade owes the architecture once: after it is recorded, an upgrade owes none.
- The `also:` lines, for the reason [above](#the-also-lines).
- Another work tree that holds the default branch. There the merge cannot switch, and both its `next:` line and the route name `git switch <default>`, which git refuses while that work tree holds it. A strict expected failure in the tests turns red the day the step named there can be taken.
- A branch the default branch holds, edited anywhere but at rest. What the route makes of a dirty tree there does not depend on the tickets or on what is owed.
- A project that is deployed, a library, or one whose flag row is bound `none`. The upgrade that asks the `Flag:` line changes nothing the route names there, as for any other upgrade.
- Any step but the resume and the real route's from an adoption stopped after its pin: an edit, an upgrade, a hold. The model shows where today's route leads from there, and nothing else.
- That upgrade before the glossary is written, while records or code are uncommitted or a records branch waits, or from a plugin before ADR 0049.
- A ticket whose own `## Operability` block lacks the `Flag:` line its spec states, which ADR 0061 reads line by line. The model's tickets carry no block of their own.
- An edit of the criteria of a ticket that is held, built, merged or still in triage; an edit of the spec's stories before any ticket of it is agreed, or while a ticket that inherits from it is held; an edit that puts the criteria back as they were; and an edit made while the checkout is unfinished. The model's edit comes on a clean default branch and makes a stated conjecture stale.
- An edit of the own `## Acceptance criteria` of a ticket that inherits its conjecture. The skill reads only the spec's stories for it, so that edit is not noticed and leaves it current. That is a known gap of the skill, which this page states and does not fix, and the model has no step for it.
- Hooks lost after the setup. The model installs them once; the stateful test takes them away every seventh step and checks that the route names them first.
- The pipeline's feature spec under `docs/features/<slug>/` and its verdict record, the `expire` commit a feature run makes before that record, and the judge-again run that reads a deleted spec from the parent of the commit that deleted it. The model's build writes no spec. The drain model takes these.
- Tickets of one unit built at once and merged in any order, a ticket that waits and is merged by hand later, and an update of a branch across a deletion, whose conflict resolves to the deletion. The model builds one ticket at a time, the second blocked by the first. The drain and merge models take these.
- The merge judge's check of expiry, and which deletions are the owner's to merge: a spent ticket's is not. A ticket is spent when it is closed at the merge base and its build left its record there, so the change that marks the last ticket and deletes its unit, which the judge reads as records, still waits for the owner: the ticket it marks is labelled and open at the merge base. The model's records landing waits or lands whatever it holds.
- A change that renames a file of a unit or deletes part of one, which the judge reads as deleting a record, and one that moves a whole unit, which it reads as the unit's deletion beside new tickets. The model's only deletion is of the whole unit, by `expire` or with the step that resolves its last ticket.
- A unit with no answer that its own feature expires, and an older unit with answers. Every spec the world writes holds one, and the older unit holds none.
- The change description as a marked commit (ADR 0064). It changes the words of the merge step, and no state.
- The formal model's upkeep where the architecture is not recorded, or that arrives anywhere but at rest or beside a due prune pass; more than one round's ticket or kept branch at once; a round's change closed unmerged; a round's ticket the drain parks or the owner finishes; and a second bug found by the round after a fix.
- A pass of the comments that leaves some owed, as one the change-size ceiling cuts short does, and comments owed by anything but the adoption. The model's pass clears the ledger in one batch.
- The count of merges and the stale citation apart: merges past the count with no page stale, or a page stale before the count. The model's one step brings both, and each pass of the pages ends both.
- A pass whose branch waits for the owner's merge or whose merge is cut off, and a pass on a forge's pull request. The model's pass merges its own branch.
- A pass that comes due while the checkout is unfinished, a records branch waits, or rows, a review, a safeguard, the architecture or the formal model's upkeep are owed. The model's merges arrive where nothing but a prune is open.
- What the pass cuts, the kinds of page it prunes, and the gates on it, `cited` and `stale` among them. `test_documents.py` and `test_prune.py` hold them.
- A defect in more than one kind, a defect found where something else is owed too, the safeguard finding a defect twice, an upgrade that asks the `Flag:` line while a defect ticket is open, and a fix that binds the row in its own change. An upgrade that adds a formal row whose kind has fired, which the upgrade writes `later:` so that it is owed at once (`dropped_row`), is out of it too: the model's upgrades come before any formal row's kind.

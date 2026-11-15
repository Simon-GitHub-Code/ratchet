---
status: accepted
---
# Before a project is deployed, the owner is asked whether new behaviour arrives switched off

Amends ADR 0028, "What waits for a life event": its first bullet for a change whose ticket's `Flag:` line is not the owner's no, and its second for the flag registry. Amends ADR 0031: a labelled ticket and the spec it inherits from change only by the owner's merge in every change, not only in records alone, save a build's close of its own ticket. Amends ADR 0046: a part carries its ticket's inherited sections word for word. Adds one question to the registry ADR 0034 keeps. Decided by the owner, 2026-11-15.

Today a flag is asked of a change only once the project is `deployed`. Before that, the method page says a flag is the owner's choice, but no skill asks them. The preflight waits for `deployed` before it asks a ticket for its `Flag:` line, and the merge judge lets new surface with no flag through with a note. Meanwhile the spec role is told to put every new behaviour behind a flag, so an agent makes the choice the page leaves to the owner. The recommendation put to the owner was to ship no flag before deployment. The owner chose instead to be asked, each time a change adds behaviour a user could meet, while the project is not `deployed`.

## Decision

**The conjecture asks, once per behaviour.** `/ratchet:conjecture` asks in step 3, in the one message that already holds the forks, when all of these hold:

- the binding declares an application type the method asks `changed flags off and on` of, so a library, or a type the catalogue lacks, is never asked;
- that question's row is not bound `none`. A row that applies is bound `none` only with the ADR that accepts having no switch, so the owner has already answered for every behaviour, and the `Flag:` line cites that ADR. A row that says `later: code:flag` is asked: it says that until the first switch exists, and the question comes before it;
- the project is not `deployed`, read as the merge judge reads it: a binding with no `- Life events:` line counts as deployed. Every role this decision touches reads it that way;
- the drafted conjecture adds behaviour a user could meet. A fix, a refactoring or a change with nothing to turn off is not asked, and its line says `none` and why;
- no `## Answers` line on the ticket or its spec answers it.

The conjecture writes the answer on the spec when the ticket has one, and on the ticket only when it has none. Every ticket of the spec, and every part split from one, inherits it, and a rerun finds it and asks nothing. The conjecture is where the owner answers product questions. The feature run starts when they step away, so a question asked there would stop the build until they return.

**The question.** Key `conjecture.flag`, kind `product`, asked by the conjecture skill alone:

> \<what this adds, as someone using it would notice it\>: should it stay hidden until you switch it on, or appear as soon as it is finished?

The options are "Appear as soon as it is finished" and "Stay hidden until I switch it on". Appearing costs nothing later, and once the project is deployed the behaviour has no switch to turn it off. Staying hidden costs a switch the owner turns on later by merging that change, and a date by which they do it. The first hidden behaviour in a project also brings one setup step, which the next command names. "Appear" comes first and is recommended, because only the owner can reach the project yet. "Stay hidden" comes first instead when the behaviour is built over more than one ticket, because the owner would otherwise meet it half-built between them.

**The answer and the line sit together.** The conjecture writes the question and the answer as one line under `## Answers`, and the `Flag:` line under `## Operability`, on the same record. The line takes one of three forms:

- a yes names the switch: `` `<key>`, off until the owner switches it on ``;
- a no: `none: the owner chose to have it appear as soon as it is finished, while the project is not deployed`;
- a change not asked: `none` and why, or the ADR of a row bound `none`.

**Each line is read from the ticket, else from its parent.** One reader in the preflight takes each asked `## Operability` line from the ticket's own block, and a line the ticket lacks from its parent's block. The preflight, the merge judge and the report the roles receive all use it. The preflight asks every ticket of an asked type for its `Flag:` line, before deployment as after, whatever its row's `later:` says. A `none` with no reason is refused in any markup: bare, in backticks, in bold, or after a list dash. Reading line by line passes no ticket that lacks a stated line. The only ticket it newly passes is one whose own block omits a line its spec states, and the preflight already reads that spec whole, and ADR 0031 holds it still while the ticket carries the label.

**A split carries the answer and the link.** A part inherits `## Answers` with `## Conjecture` and `## Operability`. When its ticket names its spec on a `Parent:` line, the part gets that line as the ticket wrote it. The part is filed beside the ticket, so the ref resolves to the same file. The preflight normalises the parent ref it resolves, so `../spec.md` becomes a path `git show` reads. The merge judge accepts a part's agent-ready label only when its inherited sections and its `Parent:` line are its ticket's, word for word, as the tip holds the ticket. So a part cannot name another spec or carry another line.

**The roles read the parent and the life events.** The preflight's report gains `parent`, normalised, and `deployed`. The spec role, the ticket check and the review read the parent's `## Conjecture`, `## Operability` and `## Answers` beside the ticket, as the base branch or the tracker holds them, and nothing else of the parent. A rejudge runs no preflight. The first run's result carries `parent` and `deployed`, and the drain passes them back in `args.rejudge` with the slug. The pipeline states `deployed` in each of the three prompts, so no role reads the binding for it. The ticket check reads the `Flag:` line that applies, with its answer, as a promise about what the product shows. Before deployment, a spec that names no switch against a yes is `bent`, and so is one that names a switch against a no. Once the project is deployed, a no is void. The spec role adds a switch, and the check accepts it.

**The merge judge reads the owner's line, and never an agent's field.**

- *Agreement in every change.* `agreement` applies to every change, whether it carries a verdict record or not. A labelled ticket and the spec it inherits from change only by the owner's merge, with one exception. With no forge, the build's own commit sets the local ticket's `Status:` line to `resolved`. So a change that carries a verdict record may change the found ticket, and only that ticket, in one way: its `Status:` line goes from the label to `resolved`, the closed status the feature skill writes, and every other line stays as the merge base holds it. That close is not counted as a change of whether a ticket waiting on it may start. A change that finds no ticket gets no exception.
- *Finding the ticket.* The judge takes the labelled local tickets it already reads, those labelled at the merge base or the tip. Among them, it looks for the one whose slug, computed from its title as the preflight computes it, is the slug of the verdict record's path. The pipeline names its spec and its record by that slug. With exactly one, the judge reads that ticket's `Flag:` line through the preflight's reader, at the merge base, or at the tip when only the tip labels it. Both merge paths ask for a branch that holds the tip, so at an agent's merge the two are the same commit. With none or more than one, no ticket is found. A ticket the change itself adds is labelled at neither, so it is never found. A part is found once its split has landed. A ticket on a forge's tracker is never found, since the judge reads commits only. The record's `ticket` field is not read.
- *New surface before deployment.* In an asked project that is not deployed, `unflagged` is let through with today's note only for a change whose ticket is found and whose line is the owner's no, or when the row is bound `none`. Every other change meets `unflagged` as it would after deployment: a yes, a plain `none` beside surface a door lists, or no ticket found.
- *A yes names a key the registry must hold.* When the found ticket's line is a yes, `flags.toml` at the head must declare that key with a `remove-by` date. It must also be off, unless the merge base already had it on. Off means its `default` is exactly false. A key with no `default`, a registry that is gone or does not parse at the head, and a key declared twice all fail closed, whether or not the change touches the registry. The test that new surface sits behind a switch that is off (`dark_flag`) is asked of the ticket's key alone: the change declares that key off, or a line it adds names that key while it is off. Another off flag does not pass it. Otherwise the owner merges, whatever the life events. This holds for behaviour no door lists, since it reads the ticket and the registry.
- *The flag registry's doors hold from the first commit* in an asked project. A switch turned on by default, made permanent, or removed while off goes to the owner, before deployment as after. A library's registry keeps waiting for a life event.

The judge still cannot see three things, and `guards.md` names them. It cannot see whether the behaviour actually sits behind the switch, which the review holds. It cannot see surface no door lists, such as a screen no snapshot records. And it cannot see a verdict record that an agent wrote under another ticket's slug, since the record is the agent's. Because the close is bound to the found ticket, such a change can close only the other ticket, unbuilt, and leaves its own open and labelled. That is a trace in the tracker after the merge, not a stop. The owner chose the full closure as well; see Settled by the owner.

**The owner's text is held from the label.** The conjecture lands its records through `just merge --records` on a branch of their own, before the owner applies the agent-ready label, and no guard group holds `.scratch/`. So between that merge and the label, an agent's change can rewrite a yes to the no sentence, as it can any unlabelled ticket's text today, a limit this decision leaves as it is.

**A yes holds the change to the rules after deployment.** The switch is declared in the flag registry, off, with a reason that cites the owner's answer and a `remove-by` date, never `permanent`. The spec role picks the date and lists it under Assumptions, so the owner sees it in the hand-over. The behaviour is read through the flag seam and tested with the switch off. The change that brings the project's first switch is free (ADR 0030): no off-and-on gate is bound yet, and the review holds it to the flag-off test its spec names. The route then names `/ratchet:safeguard`, which binds the gates every later switch meets. The removal date is checked only once `/ratchet:safeguard` binds the row `no flag past its removal date`, which says `later: code:flag` until then. Even then only a forge's merge job reads tier 2: when the date passes, tier 2 on the default branch turns red, and every merge goes to the owner. With no forge, `merge.py` never passes tier 2 to the judge, so nothing reminds the owner.

**A no is a record, not a gap.** The spec's feature flag line says `none` and cites the owner's answer. A reviewer who finds no switch beside that line opens no finding. Before deployment the judge lets new surface through with its note.

**Nothing changes once the project is `deployed`.** No question is asked. The `Flag:` line names a switch, or says `none` and why. New surface with no switch that is off goes to the owner, as ADR 0025 and ADR 0028 decided.

**No ticket is spared at the upgrade.** In an asked project that is not deployed, a labelled ticket with no `Flag:` line on it or its spec turns its preflight red when this decision ships. Sparing it would loosen a gate.

**The answer is not logged.** "Each time" is the owner's rule, so nothing offers to stop asking. The answer log keeps whether the recommendation was taken, not what was chosen, and the recommendation here turns with the number of tickets.

## Refutation criterion

This is wrong if the question settles nothing a default would not. Count the `Flag:` lines in the two asked forms on the tickets and specs of the owner's own projects. Leave out the first-hour evaluations, whose owner is a model. If the first ten all give the same answer, the question is a toll, and it goes back to the owner with that count.

It is also wrong if any of these is seen:

- The owner is asked twice for one behaviour: two `conjecture.flag` lines under `## Answers` on one spec, or on a ticket and its spec. A part's copy under its `Split from:` line is not an asking.
- Behaviour the owner asked to keep hidden can be reached on the default branch while its switch is off. Or its switch is turned on, made permanent or removed in a merge that a drain report under `.ratchet/queue/` lists as one the judge passed. History alone does not say who merged, so run the judge again on the merge's two parents.
- A no is read as a defect: a dispute, a fix round or an unresolved finding title in the verdict record about a missing switch, on a change whose line is the owner's no.
- A feature is judged as a fix to avoid the question. The judge reports `unflagged` before deployment on a change whose ticket's line is a plain `none`. Two of these among the first ten such changes refute it.

## Considered options

- **No switch before deployment, stated in the hand-over.** This was the recommendation, and the owner turned it down. They want the call on each behaviour made by them, before it is built.
- **Leave the pages as they are.** Rejected. The page gives the choice to the owner, and the spec role makes it alone.
- **A switch for every new behaviour from the first commit.** Rejected. A young project would pay for a switch on each behaviour nobody else meets (ADR 0028).
- **Ask in the feature run or the feature skill.** Rejected. The run stops on a question while the owner is away, and the conjecture already asks product questions in one message.
- **Ask once per ticket.** Rejected. The tickets of one spec are one behaviour built in slices.
- **Copy the spec's `Flag:` line onto each ticket whose own block lacks it.** Rejected. Reading line by line takes the same owner-held text from the spec. The copy would cost an owner merge for each labelled local ticket and each part, with nothing new in it, and a ticket's text change releases a drain hold, so the copy would restart a ticket parked for open questions.
- **Find the ticket from the verdict record's `ticket` field.** Rejected. An agent writes it, and a bare number does not say whether it names an issue or a local file.
- **Let the merge judge read the spec role's flag line.** Rejected. That line is an agent's (ADR 0025).
- **Spare tickets labelled before the upgrade.** Rejected. It loosens the preflight.
- **Keep the flag registry's doors waiting for deployment.** Rejected. Before deployment, a switch in an asked project exists to keep behaviour hidden until the owner says otherwise.

---

## For the builders

### What changes where

Pages:

- `skills/method/SKILL.md`. The branch for behaviour a user could meet gains its half before deployment: follow the `Flag:` line. The judge entries in the glossary and in item 7 follow the judge rules above.
- `skills/method/records.md`. `## Operability` is read line by line, the ticket and then its parent. The `Flag:` line is asked whatever the life events and its row's `later:`, and it takes the three forms. `## Answers` is read from the parent too. "Questions, assumptions and standing answers" says `conjecture.flag` is not logged.
- `skills/method/guards.md`. Under "The merge judge", these bullets change: the one-way door bullet (the registry's doors hold from the first commit in an asked project), the new-surface bullet (what waits before `deployed`, and the key check), and the no-clean-approval bullet (agreement in every change). "What the judge cannot know" names the three things above and drops "a labelled ticket ... meet only the guard rules". "What only the owner does" follows.
- `skills/method/tiers.md`, "What else waits for a life event". The door line excepts an asked project's flag registry. The new-surface line excepts every change whose ticket's line is not the owner's no.
- `skills/method/pipeline.md`. The Sees column of the spec, ticket check and review rows gains the parent's three sections and `deployed`. The spec's feature flag line follows the ticket's `Flag:` line, and a no is void once deployed. The rejudge paragraph says what `args.rejudge` carries.
- `skills/method/questions.toml`. Add `conjecture.flag`, skills `["conjecture"]`, kind `product`.
- `skills/conjecture/SKILL.md`. Step 1: a record states its operability when every asked line is stated, line by line, and an answer under `## Answers` is never asked again. A part with no parent link is read through the ticket its `Split from:` names, and gets that ticket's link. Step 2: the three forms and when the question is asked. Step 3: the ask, word for word, with the recommendation rule. Step 4: write the answer and the line on the spec when there is one. Step 5: the count of answers includes it.
- `skills/adopt/templates/docs/process/binding.md`, `## Feature flags`. The placeholder's "New behaviour ships behind a flag that defaults to off" holds once the project is deployed, and before then as the owner answered for each behaviour.
- `skills/adopt/templates/.github/pull_request_template.md`. The Operability line may cite the owner's no.

Workflow:

- `workflows/pipeline.js`. A sibling of `ticketHint()` names the parent as the base branch or the tracker holds it. It goes into the spec role's inputs, into the ticket check's list (now "these three"), and into the review's inputs. Each of the three prompts states `deployed`. The spec role's "Feature flag" line follows the ticket's `Flag:` line, and gives a yes a `remove-by` it lists under Assumptions. `PROMISE_READINGS` names the `Flag:` line that applies, with its answer, as a promise. Review axis B gains that the flag spec.md names is the ticket's, and that a spec `none` against a yes is a blocker. The rejudge reads `parent` and `deployed` from `args.rejudge`.

Scripts:

- `scripts/feature_preflight.py`. The `Flag:` line is asked by type alone. Remove `FLAG_EVENT`, and correct the module docstring and `operability_asked`'s. The operability reader works line by line. `unfilled` strips markup before it compares. `Ticket.parent` is normalised. `Report` gains `parent` and `deployed`, read with `declared_or_all`.
- `scripts/merge_ready.py`. `agreement` runs for every change, and lets a change with a verdict record move only its found ticket's `Status:` line from the label to `resolved`. `ticket_of(change, slug)` finds the ticket from `labelled(change)` and `slug_of`. `unflagged_reasons` takes the rule before deployment and the key check, which reads the registry at the head itself and asks `dark_flag` of the ticket's key. `flag_reasons` leaves `waiting` when the binding declares an asked type and the row is not bound `none`. That is the preflight's test, not `flags_asked`, which keeps failing closed for unknown types after deployment. `part_of_a_split` compares the inherited sections and the `Parent:` line. Correct the docstring.
- `scripts/split.py`. `INHERITED` gains `"## Answers"`. `child_body` writes the ticket's `Parent:` line as the ticket wrote it, never `Ticket.parent`, which is already joined to the folder.
- `scripts/route.py` and `scripts/questions.py`. No change. `unstated` reads the preflight, so a ticket with no `Flag:` line routes to the conjecture.

Tests:

- `hooks/tests/test_feature_preflight.py`. Replace `test_the_flag_line_waits_for_the_deployed_event`. The line is asked before and after `deployed`, with `row("changed flags off and on", "later: code:flag")`, and never of a library or an unknown type. A ticket's own block that lacks `Flag:` takes its spec's line. A markup-wrapped `none` is refused. `Parent: ../spec.md` resolves to a normalised path. The `adopted` helper defaults to a deployed binding, so each case names its life events.
- `hooks/tests/test_merge_ready.py`. `test_new_surface_and_doors_wait_until_an_event_is_declared` turns red as written: its registry change is a door in an asked project and stays a note for a library. Add these: a change with a verdict record that edits a labelled ticket's spec is the owner's; the ticket is found by slug, and a forged `ticket` field changes nothing; a slug that matches no labelled ticket brings `unflagged` forward; the owner's no lets it through with a note; a yes whose key is missing, on, permanent, without a `default` or declared twice, or whose surface sits behind another off flag, is the owner's; a build that resolves its own ticket passes, and one that resolves another ticket or edits another line of its own is the owner's; a part whose `Parent:` line differs from its ticket's keeps no label.
- `hooks/tests/test_pipeline.py`. The pin at `:935` becomes "these three". The three prompts carry the parent and `deployed`, and the rejudge's review does too.
- `hooks/tests/test_route.py`, `test_split_script.py` and `test_owner_questions.py`. A labelled ticket with no `Flag:` line routes to the conjecture. A part carries `## Answers` and its ticket's `Parent:` line. The question registry holds the new key.

Models, which come before the scripts change:

- Route model (`hooks/tests/route_model.py`, `route_world.py`, `docs/model/route.md`). A labelled ticket with no `Flag:` line is `READY` with advice `conjecture`. A new environment step is the upgrade. It needs a world field for "asked and not deployed" and moves stated tickets back to `READY`. It also sets `inherits` to false until the spec states the line. The release moves that send a ticket to `STATED` send it to `READY` while the line is missing. Built tickets cross the upgrade unchanged. The scope says that parts and forge tickets are out of it.
- Drain model (`hooks/tests/drain_model.py`, `docs/model/drain.md`). The model gains a preflight refusal reachable by the upgrade alone, and the step "the owner lands the `Flag:` line", within its bound of one owner edit per ticket. It also gains two rules: a refused ticket is never opened, and the cascade lifts once the line lands. The new judge rules need nothing, since the model abstracts the judge.
- Pipeline model and safeguard model. Nothing changes. A ticket missing the line is `Preflight.RED`, and the first switch arrives free like any kind (`docs/model/safeguard.md:3`).

Evaluations: a first-hour run on an asked type meets one more question per spec. Check whether an expected round of questions counts them.

### Consequences

- **Upgrade.** In an asked project that is not deployed, the next drain refuses each labelled ticket with no `Flag:` line on it or its spec. Every ticket that waits on it is refused too, though those carry their own line. The owner learns this from the drain report. The route names `/ratchet:conjecture` once nothing else is ready. On a local tracker, it costs one question and one owner merge per spec in flight, and nothing per ticket. On a forge's tracker, it is an issue edit. A branch that already has commits is not preflighted again. The judge finds no line on its ticket and sends any surface a door lists to the owner.
- **A forge project before deployment.** Every change that adds surface a door lists goes to the owner, whatever the answer, because the judge cannot read an issue. On a forge, the ticket's line still decides what the spec role builds, and an issue edit can change it without asking. This decision widens that gap, which the roles hold for surface no door lists.
- **A plain `none` beside new surface**, before deployment, is the owner's merge. This is the backstop for a feature judged as a fix.
- **Bindings already written.** Adoption writes the Feature flags section in one line while its rows are `later:`. Only where `/ratchet:safeguard` filled it from the placeholder does it carry "New behaviour ships behind a flag that defaults to off", and changing that is the owner's merge.
- **Switches agents made before this decision.** Once the registry's doors hold, turning one on is the owner's merge, once per switch.

### What already contradicts the decision

- `workflows/pipeline.js`: the spec role requires a switch for every behaviour, whatever the life events. Its three ticket readers are given the ticket alone, and the rejudge has no parent.
- `skills/adopt/templates/docs/process/binding.md:254`: the placeholder requires a flag without condition.
- `scripts/feature_preflight.py`: `FLAG_EVENT` holds the line back until deployment, which the method page never said. The operability block is read whole, and `Ticket.parent` is not normalised.
- `scripts/merge_ready.py`: the registry's doors wait for an event, and `agreement` runs only for records alone.
- `scripts/split.py`: carries neither `## Answers` nor a `Parent:` line.
- `skills/conjecture/SKILL.md` step 1 stops with "nothing to write" when any `## Operability` exists.

## Settled by the owner, 2026-11-15

These were the open points of the review. The owner chose each one below, and each is part of the decision.

- **A verdict record under another ticket's slug is closed, not only traced.** Before deployment, `unflagged` passes on a no only while no open ticket labelled at the merge base or the tip reads a yes. Otherwise an agent could commit a record under the slug of a labelled ticket whose line is a no (`merge_ready.py record` takes the slug from its stdin, and the judge checks only that it matches the record's folder) and ship behaviour the owner asked to keep hidden. Cost accepted: a merge of the owner's for every change that adds surface under a no while any yes is in flight, in any spec. The close bound to the found ticket stays as well, so a forgery also leaves its trace.
- **A ticket on a forge's tracker goes to the owner.** The judge finds no ticket it can read at the merge base, and before deployment sends surface a door lists to the owner. Cost accepted: a merge of the owner's for each such change, whatever they answered.
- **The flag registry holds its doors from the first commit in projects asked the flag question.** A library's registry keeps waiting for a life event.
- **Moving a removal date later is the owner's.** Every flag in an asked project gets a door, and `flag_doors` compares `remove-by`. Cost accepted: a merge of the owner's each time a date moves.
- **The route names each switch past its removal date.** It lists what `flags.py --expired` lists, with no forge and before `/ratchet:safeguard`, as an `also:` line that outranks no step. Cost accepted: one line in every route while any switch is past its date.

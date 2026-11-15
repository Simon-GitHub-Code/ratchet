---
name: architecture
description: Choose how an adopted repository is built, or review that choice when the project's shape changes.
disable-model-invocation: true
---

# The architecture

Choose how an adopted repository is built, record it and declare its parts, or review that choice when the project's shape has changed. Load the `ratchet:method` skill first, then read its [architecture.md](../method/architecture.md).

- **Review:** run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/triggers.py" --root . --reviews` in one Bash call. When it lists an owed review, and the owner did not ask to revisit the choice, follow [Review the architecture](#review-the-architecture).
- **Choose:** for a repository adopted before the architecture was asked, which the route sends here while `quality/owed.toml` lists an `architecture` entry, and for an owner who wants to revisit the choice, follow [Choose the architecture](#choose-the-architecture). Adoption does this at its step 3 on a new repository.
- **Both are owed:** review first. The review reads what is here as the decision, and its record states it in full. The choice follows as its own change.

## Choose the architecture

### What the owner is asked when choosing

The architecture and its tools, in one round, as [architecture.md](../method/architecture.md#the-round-of-questions) says.

1. **Ask** `architecture.system`: "How should it be built? Each choice says what it is made of, where it runs, where your things are kept, and what changing it later would cost." The options are the systems, recommended first, and "You decide", which takes the recommendation.
2. **Ask** `architecture.tools`: "Which tools should check the work? Each set names one tool for each kind of check, for the design I recommend." On a repository already adopted, the options are the tools bound now, first; a second set only where the code shows a reason to change one; and "You decide", which takes the first.

Skip a question whose choice the owner's words already make. Assume everything else and list it under **Assumed** in the hand-over.

### Steps to choose

1. **Check the ground.**
   - Start on the default branch with a clean tree. Make the branch `chore/architecture`.
   - Read the binding, `AGENTS.md` (its `## Layers` section, if any), `docs/adr/` (an earlier architecture record, if any) and the tree.
   - Learn from them what it is written in, how it starts, where it keeps data, what its parts are and how they depend on each other, and which tool each bound gate runs.

   Done when you can describe what is here as an option.

2. **Draft and ask.**
   - Draft the options and their tool sets as [architecture.md](../method/architecture.md) says. Look each tool up in its current documentation.
   - Ask the round above.

   Done when both questions are answered or left to you.

3. **Write the record.** Write it under the next free number in `docs/adr/`, with these sections:
   - `## Decision`, with its `- Chosen by:` line;
   - `## Considered options`: at least two alternatives, each offered or set aside, with why not;
   - `## Reversal cost`;
   - `## Refutation criterion`.

   When an earlier architecture record exists, the new one says it supersedes it, which the owner merges.

   Done when `conformance` names no `architecture` finding against the record.

4. **Declare it in the binding.**
   - Fill the binding's `## Architecture` section: `- Decision:` with the record's path, and one row per part, inner to outer, with the globs of its files and the parts it may depend on, or `nothing`.
   - Move the rows of an `AGENTS.md` `## Layers` section here and delete that section, and point the Triggers table's `layer` row at the binding's table.
   - **The parts differ from what the `dependency direction` gate and its fitness test hold:** change both in this change, and show the gate bite with `just bite-one imports`.

   Done when `conformance` names no `architecture` finding and `just check` is green.

5. **Settle the ledger and open the change.**
   - Delete each `[[owed]]` entry of `quality/owed.toml` whose `rule` is `architecture`, with the Edit tool. `conformance` is red on an entry whose finding is gone, and a ledger only shrinks between upgrades, so dropping them loosens nothing.
   - Run `conformance` again and see it green on them.
   - Commit with the subject `chore(method): record the architecture`.
   - Push and open the pull request with every template section filled, the options and the answers under its summary.
   - With no forge, write the change description at `.scratch/changes/chore/architecture.md` and run `just merge chore/architecture`.
   - **The owner chose a system other than what is here:** build none of it in this change. Name the tickets it needs for `/mattpocock-skills:to-spec`: moving each part, and replacing each tool as *Overturning an assumption* in the records page says.

   A new record and the binding are guard paths, so the merge is the owner's, and the judge says so.

   Done when `quality/owed.toml` holds no `architecture` entry, `conformance` is green on the deleted entries, the change is open or handed to `just merge`, and, for another system, its tickets are named.

6. **Hand over.**
   - Under **You chose**: the system and the tools, one plain line each with what changing it later costs, or "I took my recommendation, as you left it to me".
   - Then the **Assumed** block for anything else you chose, and the number of questions the owner answered.
   - The last lines are the route: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .` in one Bash call and print its lines as they are.

   Done when the route's lines are the last you print.

## Review the architecture

A review is owed when a kind of code that changes the project's shape first arrives, or the owner declares a life event (`deployed`, `irreplaceable-data` or `depended-on`). The catalogue's `[architecture]` table in [application-types.toml](../method/application-types.toml) names each trigger and the question its arrival reopens.

### What the owner is asked when reviewing

At most one message, and only when the review finds that the architecture should change.

- **Assume** `architecture.holds`: the existing decision still fits the new shape. Record it (step 4) and list it under **Assumed** (step 6).
- When it does not fit, put the ways to change it to the owner in one message, every trigger this review covers in it, so the owner can accept the recommendation at once:

  **Ask** `architecture.change`: "<what the project now does that the way it is built did not plan for, and two or three ways forward, each with what someone using it would notice, what it costs, and how hard it is to undo, the recommended one first>" All in one call of the harness's question tool where it takes them, or one message with them numbered.

  Each option is a whole way of building it, as [architecture.md](../method/architecture.md) says an option is, told in the owner's words:
  - what the user notices: speed, what is kept and where, what happens when something is down, who can reach it;
  - what it costs to run;
  - what undoing it later means: what is rewritten, roughly how much work, and whether anything a user keeps is at risk.

  Use no method term and no tool name: the agent chooses tools afterwards, as an assumption. Take the answer as given.

### Steps to review

1. **Check the ground.**
   - Start on the default branch with a clean tree. Make the branch `docs/architecture-<what>`, where `<what>` is the kinds or the events in a word or two.
   - Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/triggers.py" --root . --reviews` in one Bash call. It lists each owed review: its trigger, the kind or event, how many instances, and what the arrival reopens.
   - When it lists nothing, say so, print the route as step 6 does, and stop.

   Done when you hold the list.

2. **Read what was decided.**
   - **The binding has an `## Architecture` section:** the decision is the ADR its `- Decision:` line names, and every ADR that superseded it or reviewed it since, which name their trigger on a `- Architecture review:` line.
   - **The binding has no such section** (a repository adopted before the architecture was asked): the ADRs that decided how it is built (the stack, where it runs, where its data lives, how its parts talk), `AGENTS.md` and the binding are the decision as it stands. Your record states it in full.
   - Then read the declared parts, inner to outer, with what each may depend on: the binding's `## Architecture` table, or `AGENTS.md`'s `## Layers` where that table is not there yet.
   - Then read the design brief: who uses the product, where, and what is out of scope.

   Done when you can say the decision in three sentences and name each part's job.

3. **Read the code as it now is.**
   - Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/triggers.py" --root . --count`. It lists the instances of each kind.
   - For each owed trigger, open every instance and say which part it sits in, what it reaches and what reaches it.
   - Use `mattpocock-skills:codebase-design` on the boundary the new kind sits at.
   - **For an event:** read what the event changes for the people it brings: who reaches the project and from where, what must survive, which surfaces are now promises.
   - Answer the question the catalogue gives the trigger, from the code and the records.

   Done when each reopened question has an answer with the files that show it.

4. **Decide: holds or changes.** The decision **holds** when all of these are true:
   - every new instance sits in a part whose job covers it;
   - the declared direction still describes every dependency;
   - the answer to each reopened question follows from the existing decision (the stored data is read and written by the one part the decision gives that job; the outside system is reached through one part that decides what happens when it fails).

   It **changes** when any of these is true:
   - the new kind has no part whose job covers it;
   - covering it would break the direction;
   - what the product now needs (others reaching it, data that cannot be recreated, people relying on it) contradicts where it runs, where its data lives or how its parts talk.

   Then write the record for the case:
   - **Holds.** Write the review under the next free number in `docs/adr/`:
     - a title that states, as a sentence, that the decision holds;
     - `- Outcome: holds`;
     - a `## Decision` that names the decision it confirms and says, per trigger, why it covers the arrival, with the files that show it;
     - a `## Refutation criterion`: what would show it no longer holds and when to look (the next arrival of the kind outside its part, a failure of the outside system that reaches the user, a second store).
   - **Changes.**
     1. Prepare two or three whole options, each with its trade-offs, what it costs to run, its reversal cost and the parts it would declare, and the one you recommend and why.
     2. Ask `architecture.change` as *What the owner is asked when reviewing* says, in one message.
     3. Write the superseding record as [architecture.md](../method/architecture.md) says the record is written, with `- Outcome: changes`; a `## Decision` with the option chosen, its `- Chosen by:` line and the owner's answer in his words; a `## Considered options` section with every option, its trade-offs and its reversal cost, one list item each; a `## Reversal cost`; and a `## Refutation criterion`.
     4. Name the decision it supersedes in its text. In the same change, the earlier record's status becomes `superseded` with `superseded-by:`. The guard asks the owner for that edit, which is his merge in any case.
     5. Point the binding's `- Decision:` line at it. Update the declared parts to the chosen option: a part added for the new kind, what it may depend on, and the `dependency direction` gate's config, so that the code as it now stands passes.
     6. List the code that has to move to fit in the record, as the first work under the new decision. This change moves none of it.
   - **Either way**, the record names every trigger it covers on one line, `- Architecture review:` followed by the keys separated by commas, such as `` `code:stored-format`, `life:deployed` ``, and the day it was decided on another, `- Decided: YYYY-MM-DD`. The `triggers` gate counts it only with those lines, the `- Outcome:` line, a `## Decision` and a `## Refutation criterion` that say something, and, when it changes the architecture, two or more options under `## Considered options`.

   Done when `triggers.py --reviews` prints `[]` and, when the decision changes, the earlier record is `superseded` and the binding's `- Decision:` line and parts follow the new one.

5. **Prove it and open the change.**
   - Run `conformance` and `check`, both to green. Fix a changed part that the `dependency direction` gate finds broken in the parts you declared, never by loosening the gate.
   - Commit with the subject `docs(adr): architecture review for <what>`, at most 72 characters.
   - Push and open the pull request with every template section filled. Under Criticism, write what in the code the decision does not yet explain.
   - With no forge, write the change description at `.scratch/changes/<branch>.md` and run `just merge <branch>`.

   The change brings an ADR, so the merge judge leaves the merge to the owner, and says so. The review counts once it is on the default branch: a record written in the change that adds another instance does not.

   Done when `conformance` and `check` are green and the change is open or handed to `just merge`.

6. **Hand over.** Write for the owner, in plain words with no method term:
   - what arrived, what the review found, and what now holds: the decision kept, or the option the owner chose and what follows from it;
   - **The decision holds:** list it under **Assumed**, one line per trigger: what arrived, what you concluded and why in a clause, what changing it later costs, and that saying "rethink how it is built for <the thing that arrived>" reopens it.

   The last lines are the route: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .` in one Bash call and print its lines as they are. When safeguards are owed, it names `/ratchet:safeguard` next.

   Done when the route's lines are the last you print.

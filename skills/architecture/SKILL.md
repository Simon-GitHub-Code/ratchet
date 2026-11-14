---
name: architecture
description: "Choose, record and declare how an adopted repository is built: propose two or three whole systems (keep what is here first), from which the owner picks one, with the tools that check it, in a single round; write the architecture decision record and the binding's Architecture section, and open one change. The route names it when an upgrade left the architecture owed; the owner types it again to revisit the choice."
disable-model-invocation: true
---

# Choose the architecture

Load the `ratchet:method` skill first, then read its [architecture.md](../method/architecture.md): it says what an option states, how fit is judged, what the record holds and what the binding declares (ADR 0049). Adoption does this at its step 3 on a new repository. This skill does it for a repository adopted before that, which the route sends here while `quality/owed.toml` lists an `architecture` entry, and for an owner who wants to revisit the choice.

## What the owner is asked

The architecture and its tools, in one round, recommendation first, in plain words with no method term. On a repository with code the first option is "keep what is here", described from the code; another is offered only when the code shows a reason, and a rewrite never by default.

1. **Ask** `architecture.system`: "How should it be built? Each choice says what it is made of, where it runs, where your things are kept, and what changing it later would cost." The options are the systems, recommended first, and "You decide", which takes the recommendation.
2. **Ask** `architecture.tools`: "Which tools should check the work? Each set names one tool for each kind of check, for the design I recommend." On a repository already adopted the first set is the tools bound now; a second only where the code shows a reason to change one, and "You decide", which takes the first. When the owner picks another system, that system's own set holds and this answer is set aside.

A choice the owner's words already make is not asked. Everything else is assumed and listed under **Assumed** at the end.

## Steps

1. **Check the ground.** On the default branch with a clean tree, make the branch `chore/architecture`. Read the binding, `AGENTS.md` (its `## Layers` section, if any), `docs/adr/` (an earlier architecture record, if any) and the tree: what it is written in, how it starts, where it keeps data, what its parts are and how they depend on each other, and which tool each bound gate runs. Done when you can describe what is here as an option.

2. **Draft and ask.** Draft the options and their tool sets as [architecture.md](../method/architecture.md) says, each tool looked up in its current documentation, and ask the round above. Done when both are answered or left to you.

3. **Write the record.** Under the next free number in `docs/adr/`, with `## Decision` (and its `- Chosen by:` line), `## Considered options` (at least two alternatives, each offered or set aside, with why not), `## Reversal cost` and `## Refutation criterion`. When an earlier architecture record exists, the new one says it supersedes it, which the owner merges. Done when `conformance` names no `architecture` finding against the record.

4. **Declare it in the binding.** Fill the binding's `## Architecture` section: `- Decision:` with the record's path, and one row per part, inner to outer, with the globs of its files and the parts it may depend on, or `nothing`. Move the rows of an `AGENTS.md` `## Layers` section here and delete that section, and point the Triggers table's `layer` row at the binding's table. When the parts differ from what the `dependency direction` gate and its fitness test hold, change both in this change, and show the gate bite with `just bite-one imports`. Done when `conformance` names no `architecture` finding and `just check` is green.

5. **Settle the ledger and open the change.** Delete each `[[owed]]` entry of `quality/owed.toml` whose `rule` is `architecture`, with the Edit tool: `conformance` is red on an entry whose finding is gone, and a ledger only shrinks between upgrades, so dropping them loosens nothing. Run `conformance` again; it is green on them. Commit with the subject `chore(method): record the architecture`, push and open the pull request with every template section filled, the options and the answers under its summary. With no forge, write the change description at `.scratch/changes/chore/architecture.md` and run `just merge chore/architecture`. A new record and the binding are guard paths, so the merge is the owner's, and the judge says so. A chosen system other than what is here is not built in this change: name the tickets it needs (moving each part, replacing each tool as *Overturning an assumption* in the records page says) for `/mattpocock-skills:to-spec`.

6. **Hand over.** Under **You chose**, the system and the tools, one plain line each with what changing it later costs, or "I took my recommendation, as you left it to me". Then the **Assumed** block for anything else you chose, and the number of questions the owner answered. The last lines are the route: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .` in one Bash call and print its lines as they are.

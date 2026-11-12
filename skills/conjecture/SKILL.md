---
name: conjecture
description: Draft the method's `## Conjecture` section for one ticket (the approach, what would refute it, the alternatives and the criticism that killed each) from the conversation that produced the ticket, and write it to the ticket. Asks the owner only where the record leaves two different products open.
disable-model-invocation: true
---

# The conjecture of a ticket

`/ratchet:conjecture <ticket>`: the ticket is a number on the tracker (`4` or `#4`) or the path of a local ticket file. The feature preflight refuses a ticket without a stated conjecture, on the ticket or on the spec its `Parent:` line names. This skill writes it, once, from what the owner already decided.

## Steps

1. **Read the ticket.** `gh issue view <n> --json number,title,body,comments` in one Bash call, or read the file. Read its parent spec too: the one a `Parent:` line or a `## Parent` section names, or, for a local ticket under `<feature>/issues/`, the `<feature>/spec.md` beside that directory, which the preflight reads as its parent. Stop and say so when the ticket already states a conjecture, or inherits one from its parent: nothing to write.

2. **Draft from the record, not from imagination.** The conversation that produced the ticket (`grill-with-docs`, `to-spec`, `to-tickets`) already chose an approach and killed the alternatives; the ADRs and the glossary hold the rest. Draft four parts, each a sentence or two:
   - *Approach*: the one way this ticket will be built, stated so it can be shown wrong.
   - *Refuter*: the observation that would show the approach wrong (a failing scenario, a measurement, a user who cannot do the job).
   - *Alternatives*: each alternative that was considered, and the criticism that killed it.
   - *Scope of the claim*: what this conjecture does not cover, when the ticket's Out of scope does not already say it.

3. **One conjecture for the whole spec, and no dialog to confirm it.** When the ticket's siblings share a parent spec, draft once for the spec and its tickets inherit it. The draft restates what the owner already decided, so do not ask them to accept it: write it, and print it in the reply, where they read it and counter what is wrong. An alternative the owner then names goes in with its criticism; an approach they change replaces the draft's.

   Ask only at a fork the record leaves open: two approaches that would give a user of the product different things (what they see, what they can do, who may do it, what becomes of something that is theirs), with nothing in the conversation, the design brief or an ADR choosing between them. Put that one question through the question tool, in the user's words, the approach the record leans to first with `(recommended)` and the reason. Then record the answer in one Bash call: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/questions.py" record --skill conjecture --key "conjecture.<slug>" --kind direction --recommended <yes|no> --root .`. A choice between two ways to build the same product is yours: take the one the record supports and name the other under Alternatives.

4. **Write it.** Append `## Conjecture` with the agreed text to the ticket body: `gh issue edit <n> --body-file <file>` with the full body in one Bash call (no label flag; the agent-ready label is the owner's act and the guard asks on it), or, on a local tracker, edit the file and commit it alone (`git commit -m "docs(tracker): the conjecture of <ticket>" -- <file>`), since the preflight refuses a dirty tree and the ticket file is a record, not work in progress. When the ticket's tickets share one spec, write it on the spec and say that its tickets inherit it.

5. **Hand over.** Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/feature_preflight.py" <ticket> --root .` in one Bash call and report its verdict. Red on the label: say that the ticket needs the agent-ready label and that applying it is the owner's keystroke. Green: the last line is the next command, `/ratchet:feature <ticket>` (or `/pstack:poteto-mode <ticket>` when the owner stays).

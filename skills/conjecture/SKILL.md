---
name: conjecture
description: Draft the method's `## Conjecture` section for one ticket (the approach, what would refute it, the alternatives and the criticism that killed each) from the conversation that produced the ticket, confirm it with the owner in one dialog, and write it to the ticket.
disable-model-invocation: true
---

# The conjecture of a ticket

`/ratchet:conjecture <ticket>`: the ticket is a number on the tracker (`4` or `#4`) or the path of a local ticket file. The feature preflight refuses a ticket without a stated conjecture, on the ticket or on the spec its `Parent:` line names. This skill writes it, once, from what the owner already decided.

## Steps

1. **Read the ticket.** `gh issue view <n> --json number,title,body,comments` in one Bash call, or read the file. Read its parent spec too when the body has a `Parent:` line. Stop and say so when the ticket already states a conjecture, or inherits one from its parent: nothing to write.

2. **Draft from the record, not from imagination.** The conversation that produced the ticket (`grill-with-docs`, `to-spec`, `to-tickets`) already chose an approach and killed the alternatives; the ADRs and the glossary hold the rest. Draft four parts, each a sentence or two:
   - *Approach*: the one way this ticket will be built, stated so it can be shown wrong.
   - *Refuter*: the observation that would show the approach wrong (a failing scenario, a measurement, a user who cannot do the job).
   - *Alternatives*: each alternative that was considered, and the criticism that killed it.
   - *Scope of the claim*: what this conjecture does not cover, when the ticket's Out of scope does not already say it.

3. **One dialog.** Show the draft through the question tool: accept as drafted (recommended), or edit it. An alternative the owner names that the draft lacks goes in with its criticism; an approach the owner changes replaces the draft's.

4. **Write it.** Append `## Conjecture` with the agreed text to the ticket body: `gh issue edit <n> --body-file <file>` with the full body in one Bash call (no label flag; the agent-ready label is the owner's act and the guard asks on it), or edit the local file. When the ticket's tickets share one spec, write it on the spec and say that its tickets inherit it.

5. **Hand over.** Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/feature_preflight.py" <ticket> --root .` in one Bash call and report its verdict. Red on the label: say that the ticket needs the agent-ready label and that applying it is the owner's keystroke. Green: the last line is the next command, `/ratchet:feature <ticket>` (or `/pstack:poteto-mode <ticket>` when the owner stays).

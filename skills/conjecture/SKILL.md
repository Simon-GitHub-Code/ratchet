---
name: conjecture
description: Draft the method's `## Conjecture` section for one ticket (the approach, what would refute it, the alternatives and the criticism that killed each) from the conversation that produced the ticket, and write it to the ticket with the `## Operability` block the preflight asks. Asks the owner only where the record leaves two different products open.
disable-model-invocation: true
---

# The conjecture of a ticket

`/ratchet:conjecture <ticket>`: the ticket is a number on the tracker (`4` or `#4`) or the path of a local ticket file. The argument may also be a spec (a tracker issue or a local `spec.md`) that `to-tickets` has not split yet: the route sends a spec that states no conjecture or operability here first, because `to-tickets` writes neither, and the tickets it then makes inherit both from the spec. The feature preflight refuses a ticket without a stated conjecture, and in an adopted repository without the `## Operability` lines its application types are asked, on the ticket or on the spec its `Parent:` line names. This skill writes both, once, from what the owner already decided.

## Steps

1. **Read the ticket.** `gh issue view <n> --json number,title,body,comments` in one Bash call, or read the file. Read its parent spec too: the one a `Parent:` line or a `## Parent` section names, or, for a local ticket under `<feature>/issues/`, the `<feature>/spec.md` beside that directory, which the preflight reads as its parent. Stop and say so when the ticket, or its parent, already states both: nothing to write.

2. **Draft from the record, not from imagination.** The conversation that produced the ticket (`grill-with-docs`, `to-spec`, `to-tickets`) already chose an approach and killed the alternatives; the ADRs and the glossary hold the rest. Draft four parts, each a sentence or two:
   - *Approach*: the one way this ticket will be built, stated so it can be shown wrong.
   - *Refuter*: the observation that would show the approach wrong (a failing scenario, a measurement, a user who cannot do the job).
   - *Alternatives*: each alternative that was considered, and the criticism that killed it.
   - *Scope of the claim*: what this conjecture does not cover, when the ticket's Out of scope does not already say it.

   Then the `## Operability` block: one line per concern the binding's application types are asked (the preflight of step 5 names any it still wants), each from the ticket and the binding's Feature flags, Telemetry and Limits sections, or `none` and why.
   - `- Flag:` the flag the behaviour ships behind.
   - `- Signal:` the span, metric or log that shows it working.
   - `- Load:` the route or input the load gates gain.
   - `- Security:` the surface it adds: routes, inputs, who may call.

3. **One conjecture for the whole spec, and no dialog to confirm it.** When the ticket's siblings share a parent spec, draft both once for the spec and its tickets inherit them. The draft restates what the owner already decided, so do not ask them to accept it: write it, and print it in the reply, where they read it and counter what is wrong. An alternative the owner then names goes in with its criticism; an approach they change replaces the draft's.

   Ask only at a fork the record leaves open: [the fork test](../method/pipeline.md#the-fork-test), the one the pipeline's Spec role applies, says the options are different products, and nothing in the conversation, the design brief or an ADR chooses between them. Its examples say what is yours to assume: a status code, where a check lives, a limit with one safe value. Put that one question through the question tool, in the user's words, the approach the record leans to first with `(recommended)` and the reason. Then record the answer in one Bash call: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/questions.py" record --skill conjecture --key "conjecture.<slug>" --kind direction --recommended <yes|no> --root .`. A choice between two ways to build the same product is yours: take the one the record supports and name the other under Alternatives.

4. **Write it.** Append `## Conjecture` and `## Operability` with the agreed text to the ticket body: `gh issue edit <n> --body-file <file>` with the full body in one Bash call (no label flag; the agent-ready label is the owner's act and the guard asks on it), or, on a local tracker, edit the file and land it, since the preflight refuses a dirty tree and the ticket file is a record, not work in progress. With no forge, run the `merge` entry point with `--records` (`just merge --records`) in one Bash call: it commits the records alone on a branch of their own and merges it when the merge judge and `check` pass, and the judge asks no verdict record of records alone (ADR 0031). A ticket that already carries the agent-ready label, and the spec such a ticket inherits from, change only by the owner's merge: the run prints `merge: owner` and their command, which you relay as it is. With a forge, commit the file on a branch of its own and open the pull request; its merge job merges it. Never commit on the default branch itself. When the ticket's tickets share one spec, write them on the spec and say that its tickets inherit them.

5. **Hand over.** Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/feature_preflight.py" <ticket> --root .` in one Bash call and report its verdict. Red on the label: say that the ticket needs the agent-ready label and that applying it is the owner's keystroke. Green: the last line is the next command, `/ratchet:feature <ticket>` (or `/pstack:poteto-mode <ticket>` when the owner stays).

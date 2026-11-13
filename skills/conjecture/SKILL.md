---
name: conjecture
description: Draft the method's `## Conjecture` section for one ticket (the approach, what would refute it, the alternatives and the criticism that killed each, and what each thing the owner said became) from the conversation that produced the ticket, and write it to the ticket with the `## Operability` block the preflight asks. Asks the owner each product choice that nothing they said settles, one at a time, and makes the spec follow what they did say.
disable-model-invocation: true
---

# The conjecture of a ticket

`/ratchet:conjecture <ticket>`: the ticket is a number on the tracker (`4` or `#4`) or the path of a local ticket file. The argument may also be a spec (a tracker issue or a local `spec.md`) that `to-tickets` has not split yet: the route sends a spec that states no conjecture or operability here first, because `to-tickets` writes neither, and the tickets it then makes inherit both from the spec. The feature preflight refuses a ticket without a stated conjecture, and in an adopted repository without the `## Operability` lines its application types are asked, on the ticket or on the spec its `Parent:` line names. This skill writes both, once, from what the owner already decided.

## Steps

1. **Read the ticket.** `gh issue view <n> --json number,title,body,comments` in one Bash call, or read the file. Read its parent spec too: the one a `Parent:` line or a `## Parent` section names, or, for a local ticket under `<feature>/issues/`, the `<feature>/spec.md` beside that directory, which the preflight reads as its parent. Stop and say so when the ticket, or its parent, already states both: nothing to write.

2. **Draft from the record, not from imagination.** The conversation that produced the ticket (`grill-with-docs`, `to-spec`, `to-tickets`) already chose an approach and killed the alternatives; the ADRs and the glossary hold the rest. That is how it will be built. What it does for the user is another matter: a behaviour the spec chose is the owner's only where their own words settle it (step 3). Draft five parts, the first four a sentence or two each:
   - *Approach*: the one way this ticket will be built, stated so it can be shown wrong.
   - *Refuter*: the observation that would show the approach wrong (a failing scenario, a measurement, a user who cannot do the job).
   - *Alternatives*: each alternative that was considered, and the criticism that killed it.
   - *Scope of the claim*: what this conjecture does not cover, when the ticket's Out of scope does not already say it.
   - *What you asked for*: a table with one row per thing the owner said that this ticket or spec touches, so the owner sees that nothing was dropped and nothing invented. Take each one from where the record keeps the owner's words: `## In the owner's words` in `docs/design-brief.md`, the ticket's or spec's `## Answers`, and what `AGENTS.md`, `PRODUCT.md` or the spec quotes from them. A user story an agent wrote is not the owner's words unless it quotes them. The columns are *You said* (their words, quoted, short), *Became* and *Where*. *Became* is one of **built** (the behaviour a person will see, in their words), **assumed** (the answer taken where the rule lets you assume it, what changing it later costs, and what they can say to change it) or **left out** (why: waits for a later ticket, out of the first part, or not planned, with the reason). *Where* names the acceptance criterion, user story, ticket or record that holds it; a **built** row with no criterion or story to name is not built, so find the criterion or mark the row for what it is. A spec criterion that contradicts something the owner said is a defect: correct the criterion to follow their words, and say so in the row (**built**, corrected: the spec had said otherwise). Then one row per behaviour in the spec that nothing the owner said asked for, each put to the fork test in step 3. One the test gives the owner is `(left open)` until it is asked; once answered, *You said* quotes the answer and the row is **built**. A `(left open)` row is never marked **added** or **assumed**, and the table you write holds none. One the test gives you is written `(not asked)` and marked **assumed**, with what changing it later costs. **added** is only for an addition the user never sees, keeps or can do anything differently by (a title's markup shown as plain text, a secret kept out of the logs), written `(not asked)` with its reason; there should be few. An addition that changes what the user sees, keeps or can do is not **added**: it goes through the fork test like any other behaviour, and when the test gives it to the owner it is asked. For example:

   ```markdown
   | You said | Became | Where |
   | --- | --- | --- |
   | "Keep it, out of sight" | **built**: a link marked read stays saved and is never deleted | Story 18 |
   | "Treat as one" | **built**, corrected: the spec had counted only identical addresses as the same | Story 13a, `## Answers` |
   | "Refuse it and leave the list as it is" | **built**: Undo after pasting a saved address again is refused | Story 14, `## Answers` |
   | "articles I found on my phone" | **left out**: saving from the phone waits for a later ticket | Out of Scope |
   | (not asked) newest saved at the top | **assumed**: changing it later is a small change of its own and nothing you saved changes; say "oldest first" | Story 4 |
   | (not asked) a title's markup shown as plain text | **added**: a hostile page cannot run anything in your list | Story 8 |
   ```

   The third row was a fork: whether Undo works after a re-paste decides what the user can do, so it was asked and quotes the answer, where a careless table would have written `(left open)` and **added**.

   Then the `## Operability` block: one line per concern the binding's application types are asked (the preflight of step 5 names any it still wants), each from the ticket and the binding's Feature flags, Telemetry and Limits sections, or `none` and why.
   - `- Flag:` the flag the behaviour ships behind.
   - `- Signal:` the span, metric or log that shows it working.
   - `- Load:` the route or input the load gates gain.
   - `- Security:` the surface it adds: routes, inputs, who may call.

3. **One conjecture for the whole spec, and no dialog to confirm it.** When the ticket's siblings share a parent spec, draft both once for the spec and its tickets inherit them. The draft restates what the owner already decided, so do not ask them to accept it: write it, and print it in the reply, where they read it and counter what is wrong. An alternative the owner then names goes in with its criticism; an approach they change replaces the draft's.

   Put each behaviour the spec chose that nothing the owner said settles, and each addition the user would see, keep or do something differently by, to [the fork test](../method/pipeline.md#the-fork-test). What the spec, an earlier conjecture or an **Assumed** line chose does not settle it: an agent wrote those. Ask each such fork, every one, one at a time, before you write anything:

   - **Ask** `conjecture.fork`: "<one choice the fork test gives to the owner and nothing they said settles, asked as someone using it would put it>" The answer the spec leans to comes first with `(recommended)` and the reason.

   Write each answer under `## Answers` on the spec or ticket, the question and the answer in one line each, change the criterion to follow it, and write its row with the answer quoted under *You said*, marked **built**. A fork the test gives to you (order, wording, layout, timing, a limit, how it is built) is written `(not asked)` and **assumed**: take the one the record supports, name the other under Alternatives where it is a way to build it, and state what changing it later costs. Never write that no question was needed while a row says `(left open)` and is not **assumed** by the rule.

4. **Write it.** Append `## Conjecture` and `## Operability` with the agreed text to the ticket body: `gh issue edit <n> --body-file <file>` with the full body in one Bash call (no label flag; the agent-ready label is the owner's act and the guard asks on it), or, on a local tracker, edit the file and commit it alone (`git commit -m "docs(tracker): the conjecture of <ticket>" -- <file>`), since the preflight refuses a dirty tree and the ticket file is a record, not work in progress. When the ticket's tickets share one spec, write them on the spec and say that its tickets inherit them.

5. **Hand over.** Print the conjecture with *What you asked for* whole: it is where the owner sees what each thing they said became, and its **assumed** rows are the reply's **Assumed** block, each with what changing it later costs. Then say how many questions the owner answered. Then run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/feature_preflight.py" <ticket> --root .` in one Bash call and report its verdict. Red on the label: say that the ticket needs the agent-ready label and that applying it is the owner's keystroke. Green: the last line is the next command, `/ratchet:feature <ticket>` (or `/pstack:poteto-mode <ticket>` when the owner stays).

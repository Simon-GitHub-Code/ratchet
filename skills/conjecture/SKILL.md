---
name: conjecture
description: Draft a ticket's conjecture, size and operability from the conversation that produced it, and write them to the ticket. Asks the owner, in one message, each product choice nothing they said settles, and makes the spec follow what they did say.
disable-model-invocation: true
---

# The conjecture of a ticket

Write a ticket's `## Conjecture`, `## Size` and `## Operability` once, from what the owner already decided.

`/ratchet:conjecture <ticket>`: the ticket is a number on the tracker (`4` or `#4`) or the path of a local ticket file. The argument may also be a spec (a tracker issue or a local `spec.md`) that `to-tickets` has not split yet. The route sends a spec that states no conjecture or operability here first, because `to-tickets` writes neither, and the tickets it then makes inherit both from the spec.

## Steps

1. **Read the ticket.**
   - Run `gh issue view <n> --json number,title,body,comments` in one Bash call, or read the file.
   - Read its parent spec too: the one a `Parent:` line or a `## Parent` section names, or, for a local ticket under `<feature>/issues/`, the `<feature>/spec.md` beside that directory, which the preflight reads as its parent.
   - **The ticket, or its parent, already states both a conjecture and its operability:** say so and stop.

   Done when you hold the ticket, and its parent spec when it has one.

2. **Draft from the record.** The conversation that produced the ticket (`grill-with-docs`, `to-spec`, `to-tickets`) already chose an approach and killed the alternatives, and the ADRs and the glossary hold the rest. That is how it will be built, so draft from that record, not from imagination. What it does for the user is another matter: a behaviour the spec chose is the owner's only where their own words settle it (step 3).
   - **The ticket's siblings share a parent spec:** draft both once, for the spec. Its tickets inherit them.

   Draft five parts. Each of the first four is a sentence or two.
   - *Approach*: the one way this ticket will be built, stated so it can be shown wrong.
   - *Refuter*: the observation that would show the approach wrong (a failing scenario, a measurement, a user who cannot do the job).
   - *Alternatives*: each alternative that was considered, and the criticism that killed it.
   - *Scope of the claim*: what this conjecture does not cover, when the ticket's Out of scope does not already say it.
   - *What you asked for*: the table below.

   **The *What you asked for* table.** It has one row per thing the owner said that this ticket or spec touches, so the owner sees that nothing was dropped and nothing invented.
   - Take each thing they said from where the record keeps the owner's words: `## In the owner's words` in `docs/design-brief.md`, the ticket's or spec's `## Answers`, and what `AGENTS.md`, `PRODUCT.md` or the spec quotes from them. A user story an agent wrote is not the owner's words unless it quotes them.
   - The columns are *You said* (their words, quoted, short), *Became* and *Where*.
   - *Became* is one of these words. **built** is the behaviour a person will see, in their words. **assumed** is the answer taken where the rule lets you assume it, what changing it later costs, and what they can say to change it. **left out** says why: it waits for a later ticket, it is out of the first part, or it is not planned, with the reason.
   - *Where* names the acceptance criterion, user story, ticket or record that holds it: a **built** row with no criterion or story to name is not built, so find the criterion or mark the row for what it is.
   - A spec criterion that contradicts something the owner said is a defect. When you find one, correct the criterion to follow their words, and say so in the row (**built**, corrected: the spec had said otherwise).

   Then add one row per behaviour in the spec that nothing the owner said asked for, and put each to the fork test in step 3.
   - **A fork the test gives the owner:** the row is `(left open)` until it is asked. Once answered, *You said* quotes the answer and the row is **built**. A `(left open)` row is never marked **added** or **assumed**, and the table you write holds none.
   - **A fork the test gives you:** it is written `(not asked)` and marked **assumed**, with what changing it later costs.
   - **added** is only for an addition the user never sees, keeps or can do anything differently by (a title's markup shown as plain text, a secret kept out of the logs). Write it `(not asked)` with its reason. There should be few.
   - An addition that changes what the user sees, keeps or can do is not **added**. It goes through the fork test like any other behaviour, and when the test gives it to the owner it is asked.

   For example:

   ```markdown
   | You said | Became | Where |
   | --- | --- | --- |
   | "Keep it, out of sight" | **built**: a link marked read stays saved and is never deleted | Story 18 |
   | "Treat as one" | **built**, corrected: the spec had counted only identical addresses as the same | Story 13a, `## Answers` |
   | "Refuse it and leave the list as it is" | **built**: Undo after pasting a saved address again is refused | Story 14, `## Answers` |
   | "articles I found on my phone" | **left out**: saving from the phone waits for a later ticket | Out of Scope |
   | (not asked) newest saved at the top | **assumed**: changing it later is a small ticket of its own, and nothing you saved changes; say "oldest first" | Story 4 |
   | (not asked) a title's markup shown as plain text | **added**: a hostile page cannot run anything in your list | Story 8 |
   ```

   The third row was a fork: whether Undo works after a re-paste decides what the user can do, so it was asked and quotes the answer, where a careless table would have written `(left open)` and **added**.

   **The `## Size` block.** The change-size ceiling counts product code, and a ticket that cannot fit it should be known before anything is built.
   - Estimate, from the approach and the code it touches, the product lines and files the ticket adds or changes. Tests are not counted.
   - Write the estimate as two lines: `- Product lines: <N>` and `- Product files: <M>`.
   - The ceilings are `product_lines` and `product_files` of `[ceilings.change]` in `quality/thresholds.toml`. The feature preflight refuses a ticket whose stated size passes either.
   - Be honest and not hopeful: the spec stage estimates the whole change again and stops it when it is larger.
   - **The estimate passes a ceiling:** the ticket is not written as one. Say so in the reply, with the parts it divides into, each a vertical slice a person could use and each under the ceilings. `/mattpocock-skills:to-tickets` slices a spec by what a person can use and not by its size, so ask it for slices under the ceilings by naming both numbers in its argument.

   **The `## Operability` block.** Write one line per concern the binding's application types are asked; the preflight of step 5 names any it still wants. Take each line from the ticket and the binding's Feature flags, Telemetry and Limits sections, or write `none` and why.
   - `- Flag:` the flag the behaviour ships behind.
   - `- Signal:` the span, metric or log that shows it working.
   - `- Load:` the route or input the load gates gain.
   - `- Security:` the surface it adds: routes, inputs, who may call.

   Done when the draft holds the five parts, a table row for every thing the owner said that the ticket or spec touches and for every behaviour of the spec nothing they said asked for, the two size lines and an operability line for every concern asked, and, when the estimate passes a ceiling, the reply says so with the parts it divides into, and `/mattpocock-skills:to-tickets` is asked for slices under the ceilings with both numbers in its argument.

3. **One conjecture for the whole spec, and no dialog to confirm it.**
   - **The ticket's siblings share a parent spec:** the conjecture is the spec's, drafted once in step 2.
   - The draft restates what the owner already decided, so do not ask them to accept it. Step 4 writes it, and step 5 prints it in the reply, where they read it and counter what is wrong.

   **Ask the forks.**
   - Put to [the fork test](../method/pipeline.md#the-fork-test) each behaviour the spec chose that nothing the owner said settles, and each addition the user would see, keep or do something differently by.
   - What the spec, an earlier conjecture or an **Assumed** line chose does not settle it: an agent wrote those.
   - Ask every such fork before you write anything, all in one message, each with the answer the spec leans to first, so the owner can accept them all at once.
   - A fork already answered at the spec step has its answer under `## Answers` and is not asked again. The conjecture's questions join the same message as the spec's when they are known at that point. Otherwise, as here, they form one message of their own.

   - **Ask** `conjecture.fork`: "<one choice the fork test gives to the owner and nothing they said settles, asked as someone using it would put it>" The answer the spec leans to comes first with `(recommended)` and the reason.

   **Write each fork.**
   - **The owner answered it:** write the answer under `## Answers` on the spec or ticket, the question and the answer in one line each. Change the criterion to follow it. Write its row with the answer quoted under *You said*, marked **built**.
   - **A fork the test gives to you** (order, wording, layout, timing, a limit, how it is built): it is written `(not asked)` and **assumed**. Take the one the record supports, name the other under Alternatives where it is a way to build it, and state what changing it later costs.
   - Never write that no question was needed while a row says `(left open)` and is not **assumed** by the rule.

   Done when every fork the owner was asked has its line under `## Answers` and a **built** row, every other fork's row is `(not asked)` and **assumed**, and no row says `(left open)`.

4. **Write it.** Append `## Conjecture`, `## Size` and `## Operability` with the agreed text to the ticket body. When the ticket's tickets share one spec, write them on the spec, and say that its tickets inherit them.
   - **The tracker is on the forge:** run `gh issue edit <n> --body-file <file>` with the full body in one Bash call. Pass no label flag: the agent-ready label is the owner's act, and the guard asks on it.
   - **The tracker is local:** edit the file and land it. The preflight refuses a dirty tree, and the ticket file is a record, not work in progress.
     - **The repository has no forge:** run the `merge` entry point with `--records` (`just merge --records`) in one Bash call. It commits the records alone on a branch of their own and merges it when the merge judge and `check` pass. The judge asks no verdict record of records alone.
     - A ticket that already carries the agent-ready label, and the spec such a ticket inherits from, change only by the owner's merge: the run prints `merge: owner` and their command, which you relay as it is.
     - **The repository has a forge:** commit the file on a branch of its own and open the pull request. Its merge job merges it.
     - Never commit on the default branch itself.

   Done when the three sections are in the issue body of the ticket, or of the spec its tickets share, or in that ticket's or spec's file, merged, waiting on the owner's merge, or in an open pull request, and, when they are on the spec, you have said that its tickets inherit them.

5. **Hand over.**
   - Print the conjecture with *What you asked for* whole. It is where the owner sees what each thing they said became.
   - Its **assumed** rows are the reply's **Assumed** block, each with what changing it later costs.
   - Say how many questions the owner answered.
   - Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/feature_preflight.py" <ticket> --root .` in one Bash call and report its verdict.
   - **Red on the label:** say that the ticket needs the agent-ready label and that applying it is the owner's keystroke.
   - **Green:** the last line is the next command, `/ratchet:feature <ticket>` (or `/pstack:poteto-mode <ticket>` when the owner stays).
   - **The owner counters the conjecture:** an alternative they name goes in with its criticism. An approach they change replaces the draft's.

   Done when the reply holds the conjecture with its whole *What you asked for* table, its **Assumed** block, the count of answered questions and the preflight's verdict, with, when the verdict is red on the label, that applying the label is the owner's keystroke, and, when it is green, the next command last; and when the owner counters it, the conjecture holds the alternative they named with its criticism, or the approach they changed.

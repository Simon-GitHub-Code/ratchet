---
name: conjecture
description: Draft a ticket's conjecture, size and operability from the conversation that produced it, and write them to the ticket, or draft them again when its criteria changed since. Asks the owner, in one message, each product choice nothing they said settles, and makes the spec follow what they did say.
disable-model-invocation: true
---

# The conjecture of a ticket

Write a ticket's `## Conjecture`, `## Size` and `## Operability` from what the owner already decided, and draft them again when the ticket's criteria change after they were written.

`/ratchet:conjecture <ticket>`: the ticket is a number on the tracker (`4` or `#4`) or the path of a local ticket file. The argument may also be a spec (a tracker issue or a local `spec.md`) that `to-tickets` has not split yet. The route sends a spec that states no conjecture or operability here first, because `to-tickets` writes neither, and the tickets it then makes inherit both from the spec.

## Steps

1. **Read the ticket.**
   - Run `gh issue view <n> --json number,title,body,comments` in one Bash call, or read the file.
   - Read its parent spec too: the one a `Parent:` line or a `## Parent` section names, or, for a local ticket under `<feature>/issues/`, the `<feature>/spec.md` beside that directory.
   - **A part that names no parent** (it has a `Split from:` line, and no `Parent:` line or `## Parent` section): read the ticket its `Split from:` names, and that ticket's parent spec. Read each section the part lacks from that ticket, else from that spec. Step 4 gives the part that ticket's `Parent:` line.
   - **What states the operability:** a record states its operability when every line the `## Operability` block of step 2 asks is stated, each read from the ticket's own `## Operability`, else from its parent's.
   - A question with its answer under `## Answers` on the ticket or its parent is never asked again.
   - **The ticket, or its parent, states a conjecture:** check that it is current. Run the digest command of [The criteria digest](#the-criteria-digest) on the body that holds the conjecture, with the heading its `- Criteria:` line names.
     - **The command prints another digest than the line names, or exits 3:** the criteria changed after the conjecture was written. Redraft it: run steps 2 to 5, each as its *redraft* case says. The case below does not apply.
     - **It prints the same digest, or the conjecture has no `- Criteria:` line:** the conjecture is current, and the case below decides.
     - **It exits any other way:** stop, report the command and its output, and redraft nothing.
   - **The ticket, or its parent, already states a current conjecture and its operability, and the ticket states no `## Size`:** it was written before sizes were. Write the size alone: estimate it by the `## Size` block of step 2, write that block as step 4 says, and hand over as step 5 says, with the two size lines in place of the conjecture. Ask nothing, and draft nothing else.
   - **The ticket, or its parent, already states a current conjecture and not its operability:** write the missing operability lines alone, and, on a ticket that states no `## Size`, the size with them. Draft each line by the `## Operability` block of step 2. Ask `conjecture.flag` as step 3 says, when step 2 says it is asked, and no fork. Write the lines as step 4 says, and hand over as step 5 says, with the lines you wrote in place of the conjecture. Draft nothing else.
   - **The ticket, or its parent, already states a current conjecture and its operability, and the ticket states its `## Size`, or the argument is a spec:** there is nothing to write. Say so and stop.

   Done when you hold the ticket, and its parent spec when it has one; when a conjecture is stated, you know from the digest whether it is current or is to be redrafted; when only operability lines or the size are missing, they are written and handed over; and, when there is nothing to write, you have said so and stopped.

2. **Draft from the record.** The conversation that produced the ticket (`grill-with-docs`, `to-spec`, `to-tickets`) already chose an approach and killed the alternatives, and the ADRs and the glossary hold the rest. That is how it will be built, so draft from that record, not from imagination.
   - **The ticket's siblings share a parent spec:** draft the conjecture and the operability once, for the spec. Its tickets inherit them.
   - **A redraft** (step 1): draft from the record as it stands now, the changed criteria and every line under `## Answers` included. The old conjecture is an agent's draft: keep what the record still supports, and change what the changed criteria change. A changed criterion that contradicts the latest line under `## Answers` is not corrected to that line: step 3 asks its fork again, since the owner may have made the change. Estimate the size again, by the `## Size` block below.

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
   - **A ticket:** the block goes on the ticket itself, even when its siblings share a parent spec, since the preflight reads `## Size` only from the ticket's own body.
   - **A spec that has not been split:** write no `## Size`. Make the estimate all the same, for the ceiling case below.
   - The ceilings are `product_lines` and `product_files` of `[ceilings.change]` in `quality/thresholds.toml`.
   - Be honest and not hopeful: the spec stage estimates the whole change again and stops it when it is larger.
   - **The estimate passes a ceiling:** write it as it is, and the ticket with it. The feature run splits a ticket whose `## Size` passes a ceiling before it builds, by size and without a question, as ADR 0046 says.
   - **A spec that has not been split, whose estimate passes a ceiling:** `/mattpocock-skills:to-tickets` slices a spec by what a person can use and not by its size, so name in the reply the argument that asks it for slices under both ceilings, with both numbers in it.

   **The `## Operability` block.** Write one line per concern the binding's application types are asked. Take each line from the ticket and the binding's Feature flags, Telemetry and Limits sections, or write `none` and why.
   - `- Flag:` the flag the behaviour ships behind. It takes one of three forms:
     - **The owner chose to keep it hidden:** `` `<key>`, off until the owner switches it on ``.
     - **The owner chose to have it appear:** `none: the owner chose to have it appear as soon as it is finished, while the project is not deployed`.
     - **Not asked, and not answered:** the flag the behaviour ships behind, or `none` and why, or the ADR of a `changed flags off and on` row bound `none`.
   - `conjecture.flag` is asked when all of these hold:
     - the binding declares an application type that `application-types.toml` asks `changed flags off and on` of, so a library, or a type the catalogue lacks, is not asked;
     - the binding's `changed flags off and on` row is not bound `none`, and a row that says `later: code:flag` is asked;
     - the binding's `- Life events:` line does not declare `deployed`, and a binding with no `- Life events:` line counts as deployed;
     - the conjecture adds behaviour a user could meet, so a fix, a refactoring or a change with nothing to turn off is not asked, and its line says `none` and why;
     - no line under `## Answers` on the ticket or its parent answers it.
   - `- Signal:` the span, metric or log that shows it working.
   - `- Load:` the route or input the load gates gain.
   - `- Security:` the surface it adds: routes, inputs, who may call.

   Done when the draft holds the five parts, a table row for every thing the owner said that the ticket or spec touches and for every behaviour of the spec nothing they said asked for, the two size lines for a ticket (none for a spec that has not been split) and an operability line for every concern asked. When the estimate of a spec that has not been split passes a ceiling, the reply also names the `/mattpocock-skills:to-tickets` argument.

3. **One conjecture for the whole spec, and no dialog to confirm it.**
   - **The ticket's siblings share a parent spec:** the conjecture is the spec's, drafted once in step 2.
   - The draft restates what the owner already decided, so do not ask them to accept it. Step 4 writes it. It is printed once, in the final reply of step 5, where the owner reads it and counters what is wrong.

   **Ask the forks.**
   - Put to [the fork test](../method/pipeline.md#the-fork-test) each behaviour the spec chose that nothing the owner said settles, and each addition the user would see, keep or do something differently by.
   - What the spec, an earlier conjecture or an **Assumed** line chose does not settle it: an agent wrote those.
   - Ask every such fork before you write anything, all in one message, each with the answer the spec leans to first, so the owner can accept them all at once.
   - A fork already answered at the spec step has its answer under `## Answers` and is not asked again. The conjecture's questions join the same message as the spec's when they are known at that point. Otherwise, as here, they form one message of their own.
   - **A redraft:** ask only the forks the changed criteria opened: a behaviour they now hold that nothing the owner said settles, and a fork whose latest line under `## Answers` they now contradict. Every other line under `## Answers` stays as it is, and its fork is not asked again.

   - **Ask** `conjecture.fork`: "<one choice the fork test gives to the owner and nothing they said settles, asked as someone using it would put it>" The answer the spec leans to comes first with `(recommended)` and the reason.

   **Whether new behaviour arrives switched off.** When step 2 says `conjecture.flag` is asked, put it in the same message as the forks, once for the spec when the ticket's siblings share one.
   - **Ask** `conjecture.flag`: "<what this adds, as someone using it would notice it>: should it stay hidden until you switch it on, or appear as soon as it is finished?"
   - The options are "Appear as soon as it is finished" and "Stay hidden until I switch it on". Give each its cost:
     - Appearing costs nothing later, and once the project is deployed the behaviour has no switch to turn it off.
     - Staying hidden costs a switch the owner turns on later by merging that change, and a date by which they do it. The first hidden behaviour in a project also brings one setup step, which the next command names.
   - "Appear as soon as it is finished" comes first with `(recommended)`, because only the owner can reach the project yet.
   - **The behaviour is built over more than one ticket:** "Stay hidden until I switch it on" comes first with `(recommended)` instead, because the owner would otherwise meet it half-built between them.

   **Write each fork.**
   - **The owner answered it:** write the answer under `## Answers` on the spec or ticket, the question and the answer in one line each. Change the criterion to follow it. Write its row with the answer quoted under *You said*, marked **built**.
   - **A fork the test gives to you** (order, wording, layout, timing, a limit, how it is built): it is written `(not asked)` and **assumed**. Take the one the record supports, name the other under Alternatives where it is a way to build it, and state what changing it later costs.
   - **A redraft:** write a reopened fork's answer below its earlier line, dated. The earlier line stays.
   - **The owner answered `conjecture.flag`:** write the question and the answer as one line under `## Answers`, and the `Flag:` line in the form step 2 gives that answer under `## Operability`, both on the record step 4 names. Write its row with the answer quoted under *You said*, marked **built**, and the `Flag:` line under *Where*.
   - Never write that no question was needed while a row says `(left open)`.

   Done when every fork the owner was asked has its line under `## Answers` and a **built** row, every other fork's row is `(not asked)` and **assumed**, and no row says `(left open)`; and, when `conjecture.flag` was asked, its answer has its line under `## Answers`, its `Flag:` line and its **built** row.

4. **Write it.** Append `## Conjecture`, `## Size` and `## Operability` with the agreed text to the ticket body.
   - End the `## Conjecture` you write with its `- Criteria:` line, by [The criteria digest](#the-criteria-digest), run on the body as you will write it.
   - **A redraft:** replace each section this run drafted, where it stands, instead of appending it. `## Answers` keeps every earlier line. It lands as below, as the first write did.
   - **The ticket's siblings share a parent spec:** write the conjecture and the operability on the spec, and say that its tickets inherit them. `## Size` goes on the ticket.
   - **The argument is a spec that has not been split:** append `## Conjecture` and `## Operability` only.
   - **`conjecture.flag` was answered, or step 1 found operability lines missing:** write the answer's line under `## Answers` and each such line under `## Operability` on the spec when the ticket has one, and on the ticket only when it has none. Add a line to the section the record already holds, or append the section when it holds none.
   - **A part that names no parent** (step 1): when the ticket its `Split from:` names has a `Parent:` line, write that line on the part as the ticket wrote it.
   - **The tracker is on the forge:** run `gh issue edit <n> --body-file <file>` with the full body in one Bash call, one call per issue you write to. Pass no label flag: the agent-ready label is the owner's act.
   - **The tracker is local:** edit the file and land it. The preflight refuses a dirty tree, and the ticket file is a record, not work in progress.
     - **The repository has no forge:** run the `merge` entry point with `--records` (`just merge --records`) in one Bash call.
     - **The repository has a forge:** commit the file on a branch of its own and open the pull request. Its merge job merges it.
     - A ticket that already carries the agent-ready label, and the spec such a ticket inherits from, change only by the owner's merge, with or without a forge.
       - **No forge:** the run prints `merge: owner` and their command, which you relay as it is.
       - **A forge:** the merge job leaves the pull request to the owner and comments why on it. Hand over without waiting for that job, as step 5 says.
     - Never commit on the default branch itself.

   Done when each section you appended or replaced is where this step puts it, `## Conjecture` ending with its `- Criteria:` line when the body has a criteria heading, in the issue body or in the file, merged, waiting on the owner's merge, or in an open pull request. When they are on the spec, you have said that its tickets inherit them. When the merge is the owner's, you have relayed why, or, with a forge, you have gone on to step 5 without waiting for the merge job.

5. **Hand over.**
   - **This run redrafted a conjecture** (step 1): before the conjecture, say that the criteria changed since it was written, and print what changed in it. Give each part, table row, size line and operability line that the redraft changed, dropped or added, the old words under *Was* and the new under *Now*.
   - Print the conjecture with *What you asked for* whole. It is where the owner sees what each thing they said became.
   - Its **assumed** rows are the reply's **Assumed** block, each with what changing it later costs.
   - Say how many questions the owner answered, `conjecture.flag` included.
   - **The merge job will leave the pull request to the owner** (step 4, a forge): say that it will, with a comment on the pull request saying why.
   - Run `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/feature_preflight.py" <ticket> --root .` in one Bash call and report its verdict.
   - **Red on the label:** say that the ticket needs the agent-ready label and that applying it is the owner's keystroke.
   - **Red on `size`:** say that the ticket's size passes a ceiling and that `/ratchet:feature` splits it by size before it builds, asking the owner nothing.
   - **Green, or red on `size` alone:** the last line is the next command, `/ratchet:feature <ticket>` (or `/pstack:poteto-mode <ticket>` when the owner stays).
   - **The owner counters the conjecture:** an alternative they name goes in with its criticism. An approach they change replaces the draft's. Write the changed conjecture again as step 4 says, and print it again.

   Done when the reply holds the conjecture with its whole *What you asked for* table, its **Assumed** block and the count of answered questions, after a redraft preceded by each change under *Was* and *Now*; and the preflight's verdict, with, when the verdict is red on the label, that applying the label is the owner's keystroke, when it is red on `size`, that the feature run splits the ticket, and, when it is green or red on `size` alone, the next command last. When the merge job will leave the pull request to the owner, the reply says so. When the owner counters it, the conjecture holds the alternative they named with its criticism, or the approach they changed, written again as step 4 says and printed again.

## The criteria digest

The `- Criteria:` line records which criteria a conjecture was drafted against, so that step 1 tells from one command, and not by judgement, whether they changed since. A forge issue and a local file are checked the same way, since both hold the body as text.

- The line is the last of `## Conjecture`: the criteria heading, then the digest.

  ```markdown
  - Criteria: `## Acceptance criteria` `<digest>`
  ```

- The criteria heading is `## Acceptance criteria` on a ticket and `## User Stories` on a spec, spelled as the body spells it. A body with neither gets no line.
- The digest is what this command prints:
  - **The body is a file** (a local ticket or spec, or the file step 4 passes to `gh issue edit`): `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/criteria_digest.py" '<heading>' <file>`
  - **The body is a forge issue's:** `gh issue view <n> --json body --jq .body | python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/criteria_digest.py" '<heading>'`
- No line of the body is the heading: the command prints no digest and exits 3, and step 4 writes no line. Any other exit without a digest means it could not run.

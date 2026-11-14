---
status: accepted
---
# The architecture and the tools are asked at adoption, recorded in a decision record, and declared in the binding

ADR 0034 sent the owner only product questions and assumed the rest, "tools, and how it is built" included: `init.stack` and `adopt.tools` became assumptions, and adoption wrote the layers into `AGENTS.md` from its own reading. An audit of the method found the enforcement of an architecture strong (declared layers held by a dependency-direction tool and a fitness test, hard limits, decisions judged in review) and the choice of one weak. The stack was assumed although ADR 0034's own test assumes only what a later change reverses without loss, and a stack changed after adoption has no supported step. Nothing proposed alternatives, recorded why they lost, or said what would show the choice wrong. The owner's principle is that getting a project's foundations right, asking him, and enforcing them deterministically is a large part of what Ratchet is for. He overrides ADR 0034 for the architecture and the tools, and for nothing else.

## Decision

1. **Adoption proposes whole systems, and the owner picks.** At step 3 of `/ratchet:adopt` the agent drafts two or three options, each a whole system: the languages and runtime, where it runs, where its data is kept, how its parts talk, and its parts (modules, layers and the seams between them); each with its trade-offs, what changing it later costs, and the tools its checks would use, named by concept (formatter, linter, type checker, test runner, property-testing library, mutation tool, dependency-direction checker, and the rest the bound rows need). It recommends one. What an option states and how fit is judged are in the method's `architecture.md`, in concepts: the plugin names no stack, as ADR 0042 holds, so every concrete option comes from the agent's own knowledge, checked against current documentation, and from the owner's stated constraints.

2. **One round of questions carries the product, the architecture and the tools.** `architecture.system` (the whole-system option) and `architecture.tools` (the tools for the recommended option, with any concept where two tools are close) join the product questions of step 3, recommendation first, through the harness's question tool, in as few consecutive calls as it takes and with nothing done between them. An answer of "you decide", or none, takes the recommendation, and the record says so. When the owner picks another system, that option's own tools hold and the tools answer is set aside, and the record says that too. `questions.toml` registers both with the kind `architecture`, which only keys under `architecture.` may carry; their words have no method term, as a product question's have none. `init.stack` and `adopt.tools` are no longer assumptions: init leaves the stack to adoption and records only what the owner said about where it is used, and `/ratchet:safeguard` takes the tool the architecture record names for a concept, assuming one only where none is named (`safeguard.tools` stays).

3. **A repository with code keeps what is there.** The first option is "keep what is here", described from the code. Another is offered only when the code shows a reason (a part that cannot do what the owner asked, a runtime past its support). A rewrite is never proposed by default.

4. **The decision is a record of its own.** The agent writes it under the next free number in `docs/adr/`, with these sections, which are the fields the gate reads:
   - `## Decision`: the system chosen, its parts, and a line `- Chosen by:` that says whether the owner picked it or the recommendation was taken because the owner left it to the agent.
   - `## Considered options`: at least two alternatives as list items, each with why it was not chosen. Options the owner was shown and options the agent set aside before asking both count; each says which it was.
   - `## Reversal cost`: what changing the choice later costs, part by part: what changes, roughly how much work, and whether anything built is redone.
   - `## Refutation criterion`: the observation that would show the choice wrong.

5. **The binding declares it.** The binding's `## Architecture` section holds `- Decision:` with the record's path in backticks, and a table with one row per part, inner to outer: `| Part | Path | May depend on |`, where `Path` is the globs of the part's files in backticks and `May depend on` names other parts of the table or `nothing`. This table replaces the `## Layers` section of `AGENTS.md`. The `layer` kind of the catalogue is a declared part of this table, so the trigger of `dependency direction` fires at adoption and its gate holds the table from the first feature. The binding is a guard path, read at the merge base where it decides anything.

6. **The gate is structural.** `conformance` (its `architecture` check) is red when the binding has no `## Architecture` section, when `- Decision:` is unfilled or names no file, when that record lacks `- Chosen by:`, lists fewer than two alternatives under `## Considered options`, or has an empty `## Reversal cost` or `## Refutation criterion` (the last is the ADR check every record already has), and when the parts table declares no part, has an unfilled cell, gives a path outside backticks, or lets a part depend on one the table does not declare. It reads sections, list items and cells, never the quality of the prose.

7. **An adopted repository owes it, and the route makes it next.** An upgrade adds the template's section, and what is left (the record, the parts) is an agent's item that ADR 0037 records in `quality/owed.toml` under the rule `architecture`, never a sudden red. While the ledger holds such an entry, the route names `/ratchet:architecture` before the next build, offering the step it would otherwise name, as it does for a safeguard. That skill drafts the options from the code, asks the same round (keep what is here, first), writes the record and the section, and moves an `AGENTS.md` `## Layers` table into the binding. The owner types it again to revisit the architecture, which supersedes the record by a new one.

Everything else in ADR 0034 stands: product questions in one message, the **Assumed** block for every other choice, preferences typed after a command, and the registry with its test.

The files and names the pipeline's design pass reads: the binding's `## Architecture` section, its `- Decision:` line and its `| Part | Path | May depend on |` table; the record that line names, with `## Decision`, `- Chosen by:`, `## Considered options`, `## Reversal cost` and `## Refutation criterion`.

Amends ADR 0034, decisions 1, 2 and 4, for the architecture and the tools. Supersedes ADR 0017's and ADR 0034's "the tools question is an assumption" for adoption.

## Refutation criterion

This is wrong if, in the first five adoptions after it, the owner overturns the system he picked within its first three features, or says the options he was shown were not real choices (one plainly fit, the others padding). It is wrong if an owner who answered "you decide" says afterwards he did not want the question. It is wrong if a conforming record is later found to have chosen a system whose reversal cost it understated by a part it did not name, which the first superseding architecture record shows. And it is wrong if adoptions take more than ten minutes longer for the drafting, measured by the first-hour harness against its runs before this.

## Known residue

The gate reads structure, not judgement: a record with two thin alternatives passes, as does a `- Chosen by:` line an agent wrote without asking. The merge judge leaves a new record to the owner, which is where that is read. A project with one part declares it, and its `dependency direction` gate has no direction to hold until a second part arrives. Tools for rows bound later than adoption are named in the record only when the agent can foresee them; otherwise `/ratchet:safeguard` assumes them as before. Rebinding every gate for another stack after adoption still has no step of its own: `/ratchet:architecture` records the new decision, and each tool is replaced as *Overturning an assumption* says for a tool.

## Considered options

- **Keep the stack and tools assumed, and list them under Assumed.** Rejected. A stack changed after adoption has no supported step, so ADR 0034's own test puts it among what cannot be reversed without loss.
- **Ask at init, before adoption.** Rejected. The tools depend on the rows adoption binds, so the tools would be a second round, and init's questions are about the product.
- **Ask one question per tool concept.** Rejected. Six or more questions an owner who is not an engineer cannot judge one by one; a whole system with its tools, and a second question only where two tools are close, is a choice he can make.
- **A review that judges the record's prose.** Rejected for the gate. Prose quality is a review's, and a gate that reads it is a gate an agent argues with.
- **Keep the layers in `AGENTS.md`.** Rejected. Both are guard paths, but the binding is the record the gates read at the merge base and the one the Triggers table already points the gates at; a declaration a gate checks belongs beside the others it checks.
- **Red for every adopted repository at once.** Rejected, as ADR 0037 rejects it for every new check: the owed ledger carries it, and the route makes it next.

Decided 2026-11-14.

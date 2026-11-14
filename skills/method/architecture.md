# The architecture

How a project's foundations are chosen with the owner, recorded, and held (ADR 0049). The owner is asked about the architecture and the tools once, at adoption, in the same round as the product questions; `/ratchet:architecture` asks again when an adopted repository owes the record or the owner wants to revisit it. Every other choice of how it is built stays an assumption ([records.md](records.md#asking-the-owner)).

The plugin names no language, runtime, framework, store, host or tool, here or anywhere (ADR 0042). Every concrete option comes from your own knowledge, checked now against current documentation, and from what the owner has said: the purpose, who uses it, where it is used, what must not be lost. A name you remember is a conjecture until you have looked it up.

## What an option states

An option is a whole system, not a list of parts. Each states, in a line or two each:

- **Languages and runtime**: what the code is written in and what runs it.
- **Where it runs**: the owner's machine, a device, a host, a browser, and who reaches it there.
- **Where its data is kept**: in memory, in files, in a store of its own or someone else's; and how it is backed up once it matters.
- **How its parts talk**: calls in one process, requests over a network, messages, files; and which of those cross a boundary the project does not control.
- **Its parts**: the modules or layers, inner to outer, what each owns, and which may depend on which. The innermost holds the rules of the product and depends on nothing that does I/O; the outer parts adapt it to the world. Each seam between them is named, so a test can stand in for what is outside.
- **Its tools**, by concept: the formatter, the linter, the type checker, the test runner and the property-testing library, the mutation tool, the dependency-direction checker, the coverage tool, and any other the rows bound now need. One concrete tool for each, current and maintained, preferring one tool for several concepts and the one the package manager locks.
- **Trade-offs**: what it is good at for this product and what it costs: speed of building, running cost, what the owner must look after, what it makes hard.
- **Reversal cost**: what changing it later costs, part by part: what changes, roughly how much work, and whether anything built is redone. A store's format and where the data lives usually cost the most; a formatter the least.

## How fit is judged

Recommend the option that does the job with the fewest moving parts the owner must keep alive, judged against what the owner said, in this order:

1. **It does what the purpose needs**, where the owner said it is used: one device or many, alone or shared, online or not.
2. **What cannot be got back is safe**: data the owner or a user could not recreate lives where it survives a crash and a mistake.
3. **It is the plainest that does that.** A server, a store, a second language or a network hop each earn their place by a sentence of the purpose; none is there for a future the owner has not described.
4. **Every gate can judge it**: each tool concept has a current tool for this stack, and the parts can be checked for direction.
5. **It can change**: between two that fit, the one whose reversal cost is lower.

Offer two or three options that a person could really choose between: each one fits, and they differ in what the owner gives up. An option that does not fit is not offered to fill the count; set it aside and record it with why.

## A repository with code

The first option is **keep what is here**, described from the code: its languages, where it runs, its store, how its parts talk and the parts the tree already has, with the tools already configured. Offer another only when the code shows a reason (a part that cannot do what the owner asked, a runtime past its support, a store that loses what must not be lost), and say what in the code shows it. Never propose a rewrite by default. Where the tree has no clear parts, propose the parts its code already falls into, not new ones it would have to be moved into.

## The round of questions

The skill that asks puts `architecture.system` and `architecture.tools` in the same round as its product questions, recommendation first, in plain words with no method term: what the person will notice (it works on their phone too; their list survives the laptop dying; it costs a few a month to keep online) before what it is made of. The tools question names a concrete tool per concept for the recommended system, and offers a second set only where two tools are close for a concept that matters. An answer of "you decide", or no answer, takes the recommendation. When the owner picks another system, that option's tools hold and the tools answer is set aside. Both are written in the record.

## The record

Write it under the next free number in `docs/adr/`, in the format of [the records page](records.md#architecture-decision-records-docsadrnnnn-slugmd), with these sections. The `conformance` gate reads them by heading and field; how well they argue is the merge's to judge.

```markdown
---
status: accepted
---
# <the system in one line: what it is written in, where it runs, where its data lives>

## Decision

<The system chosen and why it fits, in the terms of "What an option states", with its parts and
the tools by concept.>

- Chosen by: <the owner | the recommendation, because the owner left it to the agent>

## Considered options

- **<an alternative>**: <offered to the owner, or set aside before asking>. <Why it was not chosen.>
- **<another alternative>**: <offered, or set aside>. <Why not.>

## Reversal cost

<Part by part: what changing it later costs, roughly how much work, and whether anything built is
redone.>

## Refutation criterion

<The observation that would show this choice wrong: a need of the purpose it cannot meet, a cost
it was chosen to avoid that appears, a part that keeps being crossed.>
```

At least two alternatives, each a list item under `## Considered options`. With code, "keep what is here" is usually the decision, and the alternatives are what you weighed and set aside, each with what in the code ruled it out.

## The binding

The binding's `## Architecture` section names the record on its `- Decision:` line and declares the parts in its table, inner to outer, each with the globs of its files and the parts it may depend on, or `nothing`. That table is what the `dependency direction` gate and its fitness test hold, what the Triggers table's `layer` row counts, and what a review reads before it judges where code was put. Declare every part the record names, at least one. A part with no file yet is declared anyway: its first file lands where the table says.

## Changing it later

The owner types `/ratchet:architecture` to revisit it. A new record supersedes the old one, the binding's section follows it, and what the change costs is the old record's reversal cost made real: the code is moved in tickets of its own, and each tool that changes is replaced as *Overturning an assumption* says for a tool ([records.md](records.md#overturning-an-assumption)).

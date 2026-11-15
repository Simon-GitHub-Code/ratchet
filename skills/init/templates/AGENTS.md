# <project>

Four plugins build software here, and each owns one question. `CLAUDE.md` is a symlink to this file.

- **How is the work done?** `mattpocock-skills`.
- **In what style, with which levers?** `pstack`. Only the owner starts a pstack skill, by typing `/pstack:<name>`, since the Skill tool refuses these to an agent; an agent does not load or imitate one to start it. Once a pstack skill runs, it reaches the others by reading their files, which is pstack's own design and is followed in full.
- **How is anything a person sees designed?** `impeccable`: every user interface, web or native, goes through it. Any agent whose work touches a surface loads `impeccable:impeccable` itself and runs the impeccable skills; none of them waits for the owner. `PRODUCT.md` and `DESIGN.md` are records: the design standards a review judges against.
- **What must be true before a merge?** `ratchet`: load the `ratchet:method` skill before any work; it is the contract this file assumes. Its route says which command the owner types next at every stage; `/ratchet:next` prints it. This file holds only what the method leaves to the project.

## Asking the owner

The owner is asked a product question and nothing else, whichever plugin's skill is running. A product question is a choice that decides what becomes of something of the user's (kept, deleted, merged, duplicated, overwritten, shared), who may see or do it, or what the user can do at all, where nothing the owner said or answered settles it. A choice an agent wrote down earlier (a spec, a conjecture, an **Assumed** line) does not settle it. Where the owner's words do settle it, follow them: a spec that contradicts them is wrong.

- Put every product question open at a step to the owner in one message, each with your recommended answer first and why, so the owner can accept them all at once. Ask every one that is open.
- Write each in the user's words, with no method term.
- Put product questions through the harness's question tool, all in one call where it takes them, with two to four options each. With no question tool, number them in one message.
- The one exception to the product question rule is the architecture: what the system is written in, where it runs, where its data is kept, its parts and the tools that check it are chosen by the owner from whole options, recommendation first, at `/ratchet:adopt` and whenever they type `/ratchet:architecture`. The decision record the binding's `## Architecture` section names holds the choice.
- While a spec is written, put each behaviour it chooses to the fork test there and then. The forks the conjecture would ask join the same message as the spec's when they are known at that point, and `/ratchet:conjecture` asks only those still open, in one message of its own.

Assume, and list under **Assumed**, anything a later change reverses without loss to the user: order, wording, layout, timing, limits, and, within the architecture the owner chose, tools and how it is built. A companion plugin's choices are assumed the same way: a tracker or labels already recorded here, where tests reach the code, how to split work into tickets, a draft to confirm, whether to go on. List each at the end of your reply, one line each in plain words: what you chose and why in a clause, what changing it later costs (what changes, roughly how much work, and whether anything built is redone), and what the owner can say to change it. The owner overturns any line in their own words; the agent who hears it follows *Overturning an assumption* in the `ratchet:method` skill's records page.

## What this is

<One paragraph: purpose, who it is for, the one sentence that says what the system's job is.>

## Agent skills

<Written by `/mattpocock-skills:setup-matt-pocock-skills`, which the owner types after init: the issue tracker, the triage labels and the domain-doc layout, each pointing at its file under docs/agents/. It updates this section in place. Delete the section if that setup was declined.>

## Read before any design work

- `docs/design-brief.md`: scope, slice one, what is out.
- `GLOSSARY.md`: the vocabulary. Its _Avoid_ lists are banned identifiers where a fitness test can see them.
- `docs/adr/`: decisions with lasting consequences, each with its refutation criterion. ADR 0001 adopts the method.
- `docs/process/binding.md`: the architecture (the parts, the paths each owns and which may depend on which), the runner, the forge, the tool behind each gate, the absolute paths and the limits. Code goes in the part its `## Architecture` table gives it.

## Non-negotiables

<Each one a sentence with the ADR that holds it and the gate that enforces it.>

## Stack rules the method leaves open

<Language- and framework-specific rules: the contract mechanism, the property-testing library, generated-code locations, the formatter for a critical value type.>

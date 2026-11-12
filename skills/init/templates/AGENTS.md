# <project>

Four plugins build software here, and each owns one question. `CLAUDE.md` is a symlink to this file.

- **How is the work done?** `mattpocock-skills`: `tdd` for the red-green loop and the seams under test, `codebase-design` for module and seam vocabulary, `domain-modeling` for the glossary and ADRs, `code-review` for the two-axis review, `diagnosing-bugs` for a bug, `grilling` for a design interview (its answers go to `domain-modeling`), `pr` for the pull request body, `research` for a question against sources.
- **In what style, with which levers?** `pstack`: `poteto-mode` routes a task; `architect` before code that crosses a boundary; `interrogate` to break a change; `arena` to race candidates; `correct` to make a repeated mistake impossible; `create-formal-verification-skill` where tests reach least. The owner presses the first key (`/pstack:<name>`), since the Skill tool refuses these to an agent; once a pstack skill runs, it reaches the others by reading their files, which is pstack's own design and is followed in full.
- **How is anything a person sees designed?** `impeccable`: every user interface, web or native, goes through it. `shape` before building a surface, `init` for `PRODUCT.md`, `document` for `DESIGN.md`, `critique` and `audit` to evaluate, `polish` before shipping. Any agent whose work touches a surface loads `impeccable:impeccable` itself and runs these; none of them waits for the owner. `PRODUCT.md` and `DESIGN.md` are records: the design standards a review judges against.
- **What must be true before a merge?** `ratchet`: load the `ratchet:method` skill before any work. It is the contract this file assumes: tiered deterministic gates, a ratchet that only rises, guard paths the harness protects, disputes, records and the feature pipeline. Its route says which command the owner types next at every stage; `/ratchet:next` prints it. This file holds only what the method leaves to the project.

## Asking the owner

Every question to the owner goes through the harness's question tool, whichever plugin's skill is asking: one decision per question, two to four options, the recommended one first with its reason. Never a list of questions as free text. With no question tool, one question per message with numbered options.

## What this is

<One paragraph: purpose, who it is for, the one sentence that says what the system's job is.>

## Agent skills

<Written by `/mattpocock-skills:setup-matt-pocock-skills`, which the owner types after init: the issue tracker, the triage labels and the domain-doc layout, each pointing at its file under docs/agents/. It updates this section in place. Delete the section if that setup was declined.>

## Read before any design work

- `docs/design-brief.md`: scope, slice one, what is out.
- `GLOSSARY.md`: the vocabulary; its _Avoid_ lists are banned identifiers where a fitness test can see them.
- `docs/adr/`: decisions with lasting consequences, each with its refutation criterion. ADR 0001 adopts the method.
- `docs/process/binding.md`: the runner, the forge, the tool behind each gate, the absolute paths and the limits.

## Layers

<Delete this section if the project declares no layers. Otherwise one row per layer, inner to outer; a tool checks the direction.>

| Layer | Path | May import |
| --- | --- | --- |
| <name> | `<path>` | <layers> |

## Non-negotiables

<Each one a sentence with the ADR that holds it and the gate that enforces it.>

## Stack rules the method leaves open

<Language- and framework-specific rules: the contract mechanism, the property-testing library, generated-code locations, the formatter for a critical value type.>

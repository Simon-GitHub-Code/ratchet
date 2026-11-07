# <project>

Three plugins build software here, and each owns one question. `CLAUDE.md` is a symlink to this file.

- **How is the work done?** `mattpocock-skills`: `tdd` for the red-green loop and the seams under test, `codebase-design` for module and seam vocabulary, `domain-modeling` for the glossary and ADRs, `code-review` for the two-axis review, `diagnosing-bugs` for a bug, `grill-with-docs` for a design interview, `pr` for the pull request body, `research` for a question against sources.
- **In what style, with which levers?** `pstack`: `poteto-mode` routes a task; `architect` before code that crosses a boundary; `interrogate` to break a change; `arena` to race candidates; `correct` to make a repeated mistake impossible; `create-formal-verification-skill` where tests reach least.
- **What must be true before a merge?** `ratchet`: load the `ratchet:method` skill before any work. It is the contract this file assumes: tiered deterministic gates, a ratchet that only rises, guard paths the harness protects, disputes, records and the feature pipeline. This file holds only what the method leaves to the project.

## What this is

<One paragraph: purpose, who it is for, the one sentence that says what the system's job is.>

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

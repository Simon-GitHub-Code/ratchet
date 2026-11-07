# <project>

This repository follows the Ratchet method. Before any work, load the `ratchet:method` skill: it is the contract this file assumes, and this file holds only what the method leaves to the project. The method's text lives in the `ratchet` plugin (`skills/method/SKILL.md`); other agents read it at <repository URL of the plugin>.

## What this is

<One paragraph: purpose, who it is for, the one sentence that says what the system's job is.>

## Read before any design work

- `docs/design-brief.md`: scope, slice one, what is out.
- `GLOSSARY.md`: the vocabulary; its _Avoid_ lists are banned identifiers.
- `docs/adr/`: decisions with lasting consequences. ADR 0001 adopts the method.
- `docs/process/binding.md`: which tool runs each gate, and the recipe for each tier.

## Layers

| Layer | Path | May import |
| --- | --- | --- |
| core | `<path>` | stdlib and the contract library only |
| application | `<path>` | core |
| adapters | `<path>` | core, application |
| interfaces | `<path>` | all above |

## Non-negotiables

<Each one a sentence with the ADR that holds it and the gate that enforces it. Examples: a representation rule for a critical value; an access policy a port encodes; a dynamic-typing escape hatch that is banned.>

## Stack rules the method leaves open

<Language- and framework-specific rules: the contract library, the property-testing library, the construct used for ports and values, generated-code locations, the formatter for a critical value type.>

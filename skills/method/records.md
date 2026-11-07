# Records

Records are the standards a review judges against. Each has a fixed shape so a machine can check its presence and a reader can find its content.

## Design brief: `docs/design-brief.md`

Dated, and the agreed result of an interview. Lasting decisions go to ADRs and vocabulary to the glossary; the brief points at them.

```markdown
# <Project>

Agreed on <YYYY-MM-DD>.

## Purpose
## Shape
## Domain
## Engineering
## Slice one: definition of done
Numbered, checkable, end to end, ending with "every gate tier green".
## Explicitly out of slice one
```

An unlisted capability is out by default. A listed exclusion needs a document change to come in.

## Glossary: `GLOSSARY.md`

One concept, one name, its synonyms listed out loud and banned. A fitness test scans every identifier defined in the pure core and the application layer, splits at word boundaries, and fails on any *Avoid* term as consecutive whole words. The check cannot see meaning, so the whole word is banned; collisions are allow-listed with a `Why:` and an ADR.

```markdown
## Language

### <cluster>

**Term**:
One to three sentences, with the boundary stated ("never", "only").
_Avoid_: synonym, synonym (qualifier)
```

## Architecture decision records: `docs/adr/NNNN-<slug>.md`

Numbered without gaps. The title states the decision as a sentence, never a topic.

```markdown
---
status: accepted
---
# <The decision, as a sentence>

<Context, then "We decided ...", then the consequence. Date any fact that will age.>

## Refutation criterion
<What observation or measurement would show this decision wrong, and when to look.>

## Considered options
- <option>: rejected because ... / deferred until ... / ruled out by the owner.
```

- `status` is `proposed`, `accepted`, `superseded` or `rejected`. A superseded ADR gains `superseded-by: NNNN`; the new one says what it supersedes, in full or in part.
- A change that contradicts an ADR needs the superseding ADR first.
- Every machine-enforced rule cites its ADR: a policy constant in a fitness test (`FORBIDDEN_*`, `REQUIRED_*`, `ALLOWED_*`) carries an `ADR NNNN` comment, and a fitness test checks the number exists.
- A rejection is a decision too (`status: accepted`, the title states the negative): scope, claims checked against evidence, the experiment run through our own gates with numbers, revisit triggers, and the seam that keeps the option cheap, enforced by a rule. Link the research document.

## Feature brief: `docs/features/<slug>/brief.md`

Short; the spec role expands it.

```markdown
# <Feature title>

## Problem
What is wrong or missing, for whom, why now. One paragraph.

## Conjecture about the solution
The one approach expected to work, stated so it can be shown wrong. The alternatives and why they lose.

## Out of scope
Anything not listed is fair game for the spec role to ask about.

## Glossary terms
Exact names. Propose new terms here, before code.

## Acceptance sketch
Three to six "Given ... when ... then ..." bullets, each naming the layer it lives in.
```

## Dispute: `docs/disputes/NNNN-<slug>.md`

The template and lifecycle are in [guards.md](guards.md).

## Research: `docs/research/<topic>.md`

Sources with URLs, the strongest counter-argument, the cheapest experiment with its pre-declared win condition, and the costs to budget if adopted anyway.

# Records

Records are the standards a review judges against. Their formats come from `mattpocock-skills:domain-modeling` where it has one; the method adds a section or a check and names it here. The conformance gate checks presence and shape.

## Design brief: `docs/design-brief.md`

The agreed result of an interview. Run `mattpocock-skills:grill-with-docs`: it writes ADRs and glossary entries as decisions crystallise. Record the outcome here, dated, pointing at them.

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

## Glossary: `GLOSSARY.md`, or `GLOSSARY-MAP.md` over several contexts

The format is domain-modeling's `GLOSSARY-FORMAT.md`: one concept, one name, the synonyms under _Avoid_. The method adds enforcement where the stack allows it: a fitness test scans every identifier defined on the absolute and declared paths, splits at word boundaries, and fails on any _Avoid_ term as consecutive whole words. The check cannot see meaning, so the whole word is banned; a collision is allow-listed with a `Why:` and an ADR.

## Architecture decision records: `docs/adr/NNNN-<slug>.md`

The format is domain-modeling's `ADR-FORMAT.md`, and its three-part test for when to write one (hard to reverse, surprising without context, a real trade-off) holds. The method adds:

- A `## Refutation criterion` section: the observation or measurement that would show the decision wrong, and when to look. The conformance gate requires it and rejects an empty or placeholder body.
- Numbering without gaps. The title states the decision as a sentence.
- A change that contradicts an ADR needs the superseding ADR first: `status: superseded` with `superseded-by: NNNN`, or domain-modeling's `superseded by ADR-NNNN`.
- Every machine-enforced rule cites its ADR: a policy constant in a fitness test carries an `ADR NNNN` comment, and a fitness test checks the number exists.
- A rejection is a decision too. The title states the negative; the body gives the scope, the claims checked against evidence, the experiment run through the gates with its numbers, the revisit triggers, and the seam that keeps the option cheap. Link the document `mattpocock-skills:research` wrote.

## Feature brief: `docs/features/<slug>/brief.md`

Short; the spec role expands it.

```markdown
# <Feature title>

## Problem
What is wrong or missing, for whom, why now. One paragraph.

## Conjecture about the solution
The one approach expected to work, stated so it can be shown wrong. The alternatives and why they lose. The conformance gate rejects an empty or placeholder body.

## Out of scope
Anything not listed is fair game for the spec role to ask about.

## Glossary terms
Exact names. Propose new terms here, before code.

## Acceptance sketch
Three to six "Given ... when ... then ..." bullets, each naming where it lives.
```

## Dispute: `docs/disputes/NNNN-<slug>.md`

The template and lifecycle are in [guards.md](guards.md).

## Pull request

The body follows the repository's template at the path the binding names. `mattpocock-skills:pr` supplies Summary, Evidence and Merge Danger; the method adds Conjecture (the claim, its refuter, and for a fix the alternative explanation and the check that eliminated it), Criticism, Guard paths, Decisions and Gates, and the conformance gate checks the template carries them. Every section is filled: an empty section is an open criticism of the pull request.

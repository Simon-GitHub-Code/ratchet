# Records

Records are the standards a review judges against. Their formats come from `mattpocock-skills:domain-modeling` where it has one; the method adds a section or a check and names it here. The conformance gate checks presence and shape.

## Design brief: `docs/design-brief.md`

The agreed result of an interview. The owner types `/mattpocock-skills:grill-with-docs` (an agent calls `grilling` then `domain-modeling`, which is the same two steps): it writes ADRs and glossary entries as decisions crystallise. Record the outcome here, dated, pointing at them.

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

An agent adds to the glossary and does not change it. A new term with its _Avoid_ list, and a new _Avoid_ item on an existing term, pass the guard; a changed definition, a renamed or removed term and a shortened _Avoid_ list ask the owner ([guards.md](guards.md), ADR 0014). The check reads lines, not meaning: before you add a term, read the glossary for the concept under another word, and add the word to that term's _Avoid_ list instead of defining it twice.

## Architecture decision records: `docs/adr/NNNN-<slug>.md`

The format is domain-modeling's `ADR-FORMAT.md`, and its three-part test for when to write one (hard to reverse, surprising without context, a real trade-off) holds. The method adds:

- A `## Refutation criterion` section: the observation or measurement that would show the decision wrong, and when to look. The conformance gate requires it and rejects an empty or placeholder body.
- Numbering without gaps. The title states the decision as a sentence.
- A change that contradicts an ADR needs the superseding ADR first: `status: superseded` with `superseded-by: NNNN`, or domain-modeling's `superseded by ADR-NNNN`.
- Every machine-enforced rule cites its ADR: a policy constant in a fitness test carries an `ADR NNNN` comment, and a fitness test checks the number exists.
- An agent writes a new ADR, under the next free number, and edits no ADR the default branch holds. The guard passes a new record and asks the owner for an edit of an existing one, for a record that speaks of superseding another, and for one carrying a `Relaxes:` line ([ratchet.md](ratchet.md)). A new ADR that contradicts an old one without superseding it passes the guard and is a finding for the review, so read the titles in `docs/adr/` first.
- A rejection is a decision too. The title states the negative; the body gives the scope, the claims checked against evidence, the experiment run through the gates with its numbers, the revisit triggers, and the seam that keeps the option cheap. Link the document `mattpocock-skills:research` wrote.

## Product and design: `PRODUCT.md`, `DESIGN.md`

`impeccable` owns both: `init` writes the product context, `document` derives the design system from the shipped code, and a redesign replaces it. They are guard paths in the decisions group, so a change to the design system is the owner's. A review of a user interface judges against `DESIGN.md`.

## Feature input: a tracker ticket

A feature starts as the ticket the owner's `/mattpocock-skills:to-spec`, `to-tickets` or `triage` writes: on the tracker (`gh issue view <n>`), or a local file under `.scratch/<slug>/issues/` where there is no tracker. Its sections are those skills' (What to build, Acceptance criteria, Blocked by; or Problem Statement, Solution, User Stories, Implementation Decisions, Testing Decisions, Out of Scope). The method adds one section and reads one label:

```markdown
## Conjecture
The one approach expected to work, stated so it can be shown wrong: what would refute it. The alternatives considered and the criticism that kills each.
```

The agent-ready label (`ready-for-agent` in mattpocock-skills, spelled as `docs/agents/triage-labels.md` maps it; a local ticket carries it on its `Status:` line) is the owner's agreement that the ticket, with its conjecture, is ready to build. The skill the owner typed applies it; the guard asks when an agent tries to. `/ratchet:conjecture <ticket>` drafts the section from the conversation that produced the ticket and writes it after one dialog. `scripts/feature_preflight.py` refuses a ticket that is closed, has no stated conjecture or lacks the label, and names the skill that produces each missing piece; `/ratchet:feature <ticket>` runs it before any agent exists, and the pipeline runs it again as its first stage. The spec role writes `docs/features/<slug>/spec.md` from the ticket, and adds what the ticket need not carry: the telemetry that shows each criterion working in production, the feature flag that gates the new behaviour (or why there is none), and the assumptions it made where the ticket was silent, each with its refuter.

## Dispute: `docs/disputes/NNNN-<slug>.md`

The template and lifecycle are in [guards.md](guards.md).

## Pull request

The body follows the repository's template at the path the binding names. `mattpocock-skills:pr` supplies Summary, Evidence and Merge Danger; the method adds Conjecture (the claim, its refuter, the assumptions the spec made where the ticket was silent, and for a fix the alternative explanation and the check that eliminated it), Operability (the flag and its default, the signals added, what the security and load gates ran), Criticism, Guard paths, Decisions (each ADR and each glossary term or _Avoid_ item the change added, as `guard-diff` lists them under `additions:`, with new dependencies and why) and Gates, and the conformance gate checks the template carries them. Every section is filled: an empty section is an open criticism of the pull request.

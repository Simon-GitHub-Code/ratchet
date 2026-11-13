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

An agent adds to the glossary and does not change it. A new term with its _Avoid_ list, and a new _Avoid_ item on an existing term, pass the guard; the guard asks the owner about a changed definition, a renamed or removed term and a shortened _Avoid_ list ([guards.md](guards.md), ADR 0014). The check reads lines, not meaning: before you add a term, read the glossary for the concept under another word, and add the word to that term's _Avoid_ list instead of defining it twice.

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

A feature starts as the ticket the owner's `/mattpocock-skills:to-spec`, `to-tickets` or `triage` writes: on the tracker (`gh issue view <n>`), or a local file under `.scratch/<slug>/issues/` where there is no tracker. Its sections are those skills' (What to build, Acceptance criteria, Blocked by; or Problem Statement, Solution, User Stories, Implementation Decisions, Testing Decisions, Out of Scope). The method adds two sections and reads one label:

```markdown
## Conjecture
The one approach expected to work, stated so it can be shown wrong: what would refute it. The alternatives considered and the criticism that kills each.

| You said | Became | Where |
| --- | --- | --- |
| "one list I can come back to in the evening" | built: saved links show newest first | criterion 2 |
| "maybe a cheap host later" | left out: runs on your computer for now | design brief, out-list |

## Operability
- Flag: the flag the behaviour ships behind
- Signal: the span, metric or log that shows it working
- Load: the route or input the load gates gain
- Security: the surface it adds: routes, inputs, who may call
```

A line of `## Operability` is asked when the binding declares an application type its question is asked of (`application-types.toml`), so a library's ticket carries none; a line that does not apply says `none` and why. A ticket inherits both sections from its parent spec.

The agent-ready label (`ready-for-agent` in mattpocock-skills, spelled as `docs/agents/triage-labels.md` maps it; a local ticket carries it on its `Status:` line) is the owner's agreement that the ticket, with its conjecture, is ready to build. The skill the owner typed applies it; the guard asks when an agent tries to, once for a call, so one call that gives every ticket of a spec the label is one ask, which names each. `/ratchet:conjecture <ticket>` drafts both sections from the conversation that produced the ticket, writes it and prints it; it asks only where the record leaves two different products open. `scripts/feature_preflight.py` refuses a ticket that is closed, has no stated conjecture, leaves out an operability line it is asked or lacks the label, and names the skill that produces each missing piece; `/ratchet:feature <ticket>` runs it before any agent exists, and the pipeline runs it again as its first stage. The spec role writes `docs/features/<slug>/spec.md` from the ticket, starting from its `## Operability` lines, and adds what the ticket need not carry: the telemetry that shows each criterion working in production, the feature flag that gates the new behaviour (or why there is none), and the assumptions it made where the ticket was silent, each with its refuter.

The owner's answers to a run's open questions are a section of the ticket too, written by the skill that asked:

```markdown
## Answers
- Does an expired link say so, or send the visitor home? A page that says it expired.
```

The spec role reads each line as decided. A changed ticket is also what releases one the drain parked.

## Questions, assumptions and standing answers

A skill asks the owner only a product question: what the thing should do, for whom, what matters, or a direction with real consequences that no experiment settles (ADR 0034). [questions.toml](questions.toml) lists each one a Ratchet skill asks, its key and its words, and each question a default now answers instead; a test holds the skills to it. Every other choice is an assumption: the agent takes the default, acts, and ends its reply with an **Assumed** block, one plain line each, what it chose, why, and what the owner can say to change it. A conjecture carries the same account for each thing the owner said, in its *You said* table.

The pipeline's open questions are product questions of two sorts (ADR 0022): `direction`, where the options are different products, and `irreversible`, where a wrong guess cannot be taken back. The feature skill logs each answer as one line of `.ratchet/questions.tsv`, written by `scripts/questions.py record`: the date, the skill, the question's key, its kind and whether the recommended option was taken. The file is one clone's and no commit carries it. A question about one ticket carries its slug (`feature.<slug>.1`).

When a key's last three answers all took the recommendation the route prints an `also:` line that offers a standing answer. The owner says yes, and the key goes into `AGENTS.md`, a guard path, so the edit asks them once:

```markdown
## Standing answers
- `feature.short-links.2`: keep what the visitor sent.
```

A skill that finds a question's key there does not ask it, takes the answer, and says so in its hand-over. A `direction` key is never proposed.

## Dispute: `docs/disputes/NNNN-<slug>.md`

The template and lifecycle are in [guards.md](guards.md).

## Incident record: `docs/incidents/NNNN-<slug>.md`

What went wrong for real, and the gate that now catches it. An incident nobody turned into a gate happens again. The question `incidents tighten a gate` is on the floor, so it holds from the first commit and judges nothing until the first record exists (ADR 0028).

An agent writes it. When the owner reports an incident (something broke for them or for a user, data was lost, a deploy went wrong) the agent they told proposes one and writes it with them under the next free number. It is blameless: it names what the system allowed, never who did it.

```markdown
# NNNN: <one-line title>

- Date: <YYYY-MM-DD>

## What happened
What was seen, in order, with times where they matter.

## Impact
Who met it, for how long, and what was lost or wrong.

## Cause
The first place the system went wrong, and what let it.

## The gate that now catches it
Bite: <name of a [[bite]] in quality/bites.toml>
The fault that bite plants reproduces this incident, and the gate it names goes red on it.

## Follow-ups
Each with the change or ticket that carries it.
```

The `Bite:` line names a bite of `quality/bites.toml`, and the gate that answers `incidents tighten a gate` is red on a record whose line names no bite the registry holds. The bite and the gate it proves land with the record: an existing gate given the fault, or a new row bound. A follow-up that cannot be a gate (a runbook, a conversation with a user) is listed and the record still names a bite. The records are not guard paths; the registry they point into is.

## Pull request

The body follows the repository's template at the path the binding names. `mattpocock-skills:pr` supplies Summary, Evidence and Merge Danger; the method adds Conjecture (the claim, its refuter, the assumptions the spec made where the ticket was silent, and for a fix the alternative explanation and the check that eliminated it), Operability (the flag and its default, the signals added, what the security and load gates ran), Criticism, Guard paths, Decisions (each ADR and each glossary term or _Avoid_ item the change added, as `guard-diff` lists them under `additions:`, with new dependencies and why) and Gates, and the conformance gate checks the template carries them. Every section is filled: an empty section is an open criticism of the pull request.

## Verdict record: `docs/features/<slug>/verdict.json`

What the pipeline concluded, for the merge judge to read: the slug, the ticket, the forced verdict and what forced it, each unresolved blocker or major finding by title, each open question, each assumption, and the tree the review saw. The orchestrator writes it from the result's `verdictRecord` with the `merge-ready` entry point's `record` form, which adds the tree, and commits it alone, as the last commit of the change. The tree is the head's with the record set aside, so the record cannot name itself and a commit after it shows. An agent writes this record and an agent can forge it; [guards.md](guards.md) says what the judge reads that does not depend on it.

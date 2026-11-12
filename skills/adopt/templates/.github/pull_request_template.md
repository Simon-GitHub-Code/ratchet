## Summary

<diagram, diff-sketch, or tree>

## Evidence

- **Before:** <screenshot/output/failing test run>
  **After:** <screenshot/output/passing test run>

## Merge Danger

**Door:** <one-way or two-way>

**Blast Radius:** <one-word description>

## Conjecture
What this change claims, and the observation that would show it wrong. Each assumption made where the ticket was silent, with its refuter: the owner counters one here if it is wrong. For a fix: the alternative explanation considered, and the check that eliminated it.

## Operability
The feature flag that gates the new behaviour and its default, or why there is none. The telemetry added: the span, metric or log that shows this working in production. What the security and load gates ran against it.

## Criticism
Critic findings and how each was resolved; review issues; disputes raised and their status. Links to the ticket and the spec where they exist.

## Guard paths
Output of the guard-diff entry point, and for each path the upheld dispute, the dependency the spec named and why, the additions to a record, or the owner's hand. The merge judge (`merge-ready`) reads the same paths and the one-way doors: this pull request merges itself when the judge passes it, and otherwise carries one comment saying why it waits for the owner.

## Decisions
Each ADR added, by number and title, and each glossary term or Avoid item added, as guard-diff lists them under `additions:`. ADRs superseded, new dependencies and why. Or "none".

## Gates
The tail of the check entry point:

```
```

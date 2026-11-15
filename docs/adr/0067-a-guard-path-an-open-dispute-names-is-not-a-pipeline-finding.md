---
status: accepted
---
# A guard path an open dispute names is not a pipeline finding

Amends the severity list of `workflows/pipeline.js` (`SEVERITY`, `:113-115`) that the critic and the review share. Decided by the owner, 2026-11-15, on issue #152.

**The case.** A ticket whose gate must tighten in the same change as its code can never reach `approve`. The tightened gate cannot land first: on the default branch it would be red, because the code that makes it pass is in the feature. So the change always touches a guard path, with a gate dispute record open for it. The pipeline's check was green, and the critic and the review each filed "guard path changed: <path>" as a major finding for the justfile and the binding. Major findings force `request_changes` (`workflows/pipeline.js:1656-1658`), so the ticket was parked before the owner saw it.

**It was counted twice.** The script already lets a guard path pass that a dispute record covers (`coveredGuardPaths`, `:1488`, and `undisputed`, `:1654`). The merge judge already sends the change to the owner: a dispute the change adds or edits, and an open one that names a path it touches, is a `dispute` reason (`scripts/merge_ready.py:727-735`, ADR 0021). Only the agents' grade turned the same fact into a finding that forces the verdict.

## Decision

- **A changed guard path that an open gate dispute in the change names exactly is not a finding** of the critic or the review. Neither files it.
- **The verdict stands on the rest.** A change whose only guard paths are covered that way can reach `approve`.
- **The merge stays the owner's.** The merge judge leaves it to the owner as today, through its `dispute` and `guard` reasons. Nothing in the judge changes.
- **An uncovered guard path is unchanged:** a blocker on the standards axis. A dispute that names only the gate covers no path.

## Consequences

- The owner decides a gate change once, at the merge, with the run's work in front of them.
- The pipeline cannot tell a sound tightening from a loosening dressed as one. The judge's `dispute` reason holds, so the owner reads every such change.
- `SEVERITY`'s Guard paths line, the critic's step 7 and the review's axis C drop the "major when covered" grade. A test holds both prompts to it.

## Refutation criterion

A merged change whose covered guard path loosened a gate, which the owner merged without seeing the loosening because the run reached `approve`.

## Considered options

- **Keep it a finding, graded so that it does not force `request_changes` on its own.** Rejected: a grade that changes nothing is noise in the report, and it keeps a second asker for one decision.
- **Build such a ticket in two changes, with the route naming the order.** Rejected: the tightened gate is red on the default branch until the code lands, so no order works.

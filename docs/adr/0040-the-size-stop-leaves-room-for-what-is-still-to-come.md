---
status: accepted
---
# The size stop leaves room for what is still to come

A first-hour eval run parked its first ticket as too large at 1,067 changed lines of the 1,500-line ceiling (ADR 0032), "with the review still to come". The check was green, no question was open and no guard path was edited. The stop is the pipeline's early one: a change over two thirds of the ceiling ends `blocked`, because "the critic's tests and the fixes are still to come". Since ADR 0028 the critic and the fix loop run only once the binding declares `depended-on`. In that run neither ran, so the margin reserved room for work that was never going to happen, and a ticket that fit the ceiling was sent back to be split.

## Decision

**The room kept under the ceiling is what the stages still to come can add.** While the critic runs, that is the critic's tests and the fixes, about two fifths of the change: the early stop stays at two thirds of the ceiling. When the critic does not run, only the review is still to come. It writes no code, and what it leaves behind (the verdict record, the spec, the disputes) is under the records `change_size.py` does not count. So the margin is nothing: the stop is the ceiling itself, `lines > ceiling`, and a change of exactly the ceiling builds on.

**One rule in four places.** The spec role's own check on its tests, the post-implement check, the spec's whole-change estimate (which already left out the critic's share) and the stop's wording all read the same `critic` flag the preflight sets; the spec role's prompt names two thirds only when the critic runs. The drain parks whatever the pipeline returns, so it needs no number of its own.

The ceiling itself, 40 files and 1,500 lines, is unchanged.

## Refutation criterion

This is wrong if a change the pipeline built without a critic, at more than two thirds of the ceiling, ends red on `change-size` after the review: then the review or the orchestrator adds counted lines, and the margin must return for them. Check it on the first ten tickets built before `depended-on` that measure above two thirds of the ceiling.

## Considered options

- **A smaller fixed margin for the review.** Rejected: the review adds no counted line, so any number would be a guess that parks tickets that fit.
- **Drop the early stop altogether.** Rejected: with the critic, its tests were two fifths of the first changes built, and the stop saves the agents that would run on a change that cannot end green.
- **Count the critic's share as zero in the estimate only.** Rejected: that is the part that already followed the flag; the post-implement check did not, which is the fault.

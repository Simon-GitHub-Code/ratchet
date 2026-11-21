---
status: accepted
---
# A change is one pull request against the default branch

Settles issue #304. Read with ADR 0024 and ADR 0069. Amends `skills/method/SKILL.md`, the steps of a change.

**What was asked.** An adopted project delivered one piece of work as five pull requests, each on the branch below it. The gates that compare a change with its base take the merge-base with the default branch. So each pull request above the first was measured as the whole stack below it plus itself. `change-size` was red on a change that fitted, the proof ran every gate for every pull request, and the merge job left each one to the owner. Deleting a merged lower branch from the command line closed the pull request above it, which could then not be reopened or retargeted. Issue #304 asks for a stated position.

## Decision

**1. Stacked pull requests are not supported.** A change is one pull request against the default branch. The next change opens after the one below has merged. No gate changes how it picks its base.

**2. Supporting them was declined.** Each gate that measures a change's size and reach would take its base from the forge, per pull request, and a second full comparison against the default branch would run when the stack lands. That conflicts with ADR 0024, which counts a run only for what it tested. A run against the branch below tested a merge with that branch and its `ci.yml`, so it counts for nothing the default branch receives, and each pull request of the stack would be judged twice, the second time only once it had already landed below.

**3. The merge judge says what to do next.** On a pull request that targets another branch, the `target` note now tells the reader to merge the pull request below first, and to change this one's target to the default branch on the pull request's page before the one below merges, which keeps it open. It says not to delete the lower branch before then, since the merge job deletes the branch of each pull request it merges, and a pull request whose target branch is gone is closed. The verdict and the exit code are unchanged: the pull request is the owner's, and once retargeted it stays the owner's (ADR 0024).

## What a project sees

The method's steps for a change say from the start that a change is one pull request against the default branch, and why. A pull request opened on top of another still gets `merge: owner`, now with the next step in the note.

## Evidence

That a retargeted pull request stays open, and its comment is edited to `retargeted`, was observed on a sandbox (ADR 0024). That deleting the target branch closes the pull request above it, and that it cannot then be reopened or retargeted, was observed in the adopted project (issue #304), not on the sandbox.

## Refutation criterion

This is wrong if adopted projects keep opening stacked pull requests after reading the steps, so that the cost of one at a time, a rebase and a new run between merges, is paid often and lands as collisions on the default branch. Count the pull requests with a `target` note in the first ten adopted projects: more than one in twenty would reopen the second option.

## Considered options

- **Take the base from the forge per pull request, and compare in full when the stack lands.** Declined, for point 2.
- **Say nothing, and let the judge's note stand as it was.** Rejected. The note named the problem and not the next step, and the step a person would guess, deleting the lower branch, cost them the pull request.

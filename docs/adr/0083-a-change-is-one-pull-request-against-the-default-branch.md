---
status: accepted
---
# A change is one pull request against the default branch

Settles issue #304. Read with ADR 0024 and ADR 0069. Amends `skills/method/SKILL.md`, the steps of a change.

**What was asked.** An adopted project delivered one piece of work as five pull requests, each on the branch below it. The gates that compare a change with its base take the merge-base with the default branch. So each pull request above the first was measured as the whole stack below it plus itself. `change-size` was red on a change that fitted, the proof ran every gate for every pull request, and the merge job left each one to the owner. The issue reports that deleting a merged lower branch from the command line closed the pull request above it, which could then not be reopened or retargeted. Issue #304 asks for a stated position.

## Decision

**1. Stacked pull requests are not supported.** A change is one pull request against the default branch. The next change opens after the one below has merged. No gate changes how it picks its base.

**2. Supporting them was declined.** Each gate that measures a change's size and reach would take its base from the forge, per pull request, and a second full comparison against the default branch would run when the stack lands. That conflicts with ADR 0024, which counts a run only for what it tested. A run against the branch below tested a merge with that branch and its `ci.yml`, so it counts for nothing the default branch receives, and each pull request of the stack would be judged twice, the second time only once it had already landed below.

**3. The merge job keeps a merged branch while an open pull request targets it.** After a merge it asks the forge for an open pull request whose target is the merged branch. If there is one, the branch stays and the job's log says which pull request targets it. If the forge does not answer, the branch stays too, since deleting is the side that can do harm. Otherwise it is deleted as before. The plugin's merge must not close someone's pull request as a side effect. What the forge does to a pull request whose target branch is deleted is not known here: ADR 0024 inferred that the forge retargets it, and issue #304 reports one closure after a deletion from the command line. Neither was observed on the sandbox, and with this rule neither matters to the merge job.

**4. The merge judge says what to do next, and only what is true.** On a pull request that targets another branch, the `target` note says that the method judges a change against the default branch, that changing the target to the default branch on the pull request's page keeps it open (observed, ADR 0024), that the owner then merges it, that the change of target starts no ci run while a new commit does, and that a closed one comes back as a new pull request from its branch. The note gives one step and no order, and names no other pull request's branch. When another open pull request from the same branch already targets the default branch, the retarget step is not offered: the note names that pull request and leaves this one to the owner. The `retargeted` note now says the owner merges and how to get a new run, and `unnamed` says how a run that names it starts and that the merge job then judges it again. Verdicts, exit codes and doubt codes are unchanged: the pull request is the owner's, and once retargeted it stays the owner's (ADR 0024).

## What a project sees

The method's steps for a change say from the start that a change is one pull request against the default branch, and why. A pull request opened on top of another still gets `merge: owner`, now with the step that is true of it. A merged branch that an open pull request targets stays on the forge; the merge job does not come back to it, so the owner deletes it once nothing targets it. The new merge step reaches a project through `/ratchet:adopt`'s template. An upgrade does not rewrite `merge.yml` and adds no finding for this step, so an adopted project keeps its present merge job, and stays green, until it takes the template's step.

## Evidence

That a retargeted pull request stays open, its comment is edited to `retargeted`, and the change starts no run, was observed on a sandbox (ADR 0024). That deleting the target branch closes the pull request above it was reported once in the adopted project (issue #304), not observed on the sandbox. The keep-the-branch rule is tested against the stand-in for the forge's client that the merge job's other tests use, not on a forge.

## Refutation criterion

This is wrong if adopted projects keep opening stacked pull requests after reading the steps, so that the cost of one at a time, a rebase and a new run between merges, is paid often and lands as collisions on the default branch. Count the pull requests with a `target` note in the first ten adopted projects: more than one in twenty would reopen the second option. The merge-job rule is wrong if a pull request is closed after the merge job merged the branch it targets, or if kept branches pile up because the owner never learns they stay: one run on a forge with a stacked pair, the lower one merged by the job, shows the first.

## Considered options

- **Take the base from the forge per pull request, and compare in full when the stack lands.** Declined, for point 2.
- **Say nothing, and let the judge's note stand as it was.** Rejected. The note named the problem and not the next step, and the step a person would guess, deleting the lower branch, cost them the pull request.
- **Warn in the note and leave the merge job deleting every branch it merges.** Rejected. The upper pull request gets its note in the same run that merges the lower one, seconds before the deletion or not at all, so the warning arrives too late.

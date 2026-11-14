---
status: accepted
---
# A young project gets one fix pass on a review blocker

Amends ADR 0028 (where the fix loop runs) and ADR 0036 (the most agents a run spawns before `depended-on`).

ADR 0028 ran the critic and its fix loop only once the project is `depended-on`. A project people do not yet rely on is built by spec, the ticket check, implement and review, and a review that refuses parks the ticket.

In a first-hour eval on a young project, the independent review found one real blocker in the first feature. No fix loop ran, so the ticket was parked. The owner merged an edit to the ticket and the whole feature, spec included, was built again from scratch: about 19 minutes and an extra paste for one finding a fix agent could have closed from the code as it stood.

## Decision

**A review blocker gets one fix pass in a project that is not yet `depended-on`.** A `depended-on` project keeps its critic and fix loop as ADR 0028 has them, and a blocker its review finds after that loop still parks the ticket. When the project is not `depended-on` and the review lists a blocker or major issue in the change, the script runs the fix round once: the same fresh fix agent the `depended-on` loop uses, given the review's blocking issues, then the review again. The pass keys on the review's blocking issues, not on its verdict: the script forces `request_changes` on such issues whatever the review said, so a review that approves and still lists one gets the pass too. A second review that approves goes on as approve. One that does not leaves the ticket parked as before; there is no second pass.

The pass is the loop's own code, parameterised by the number of rounds: one for a project that is not `depended-on`, two (`MAX_FIX_ITERATIONS`) for one that is. The size read after a fix round (ADR 0055) applies to it.

**What is not fixable by a pass.** A refusal that says the change lost a promise of the ticket, or left an input case undecided, goes back to the ticket and the spec, not to a fix agent. A too-large stop and a question for the owner end the run before the review. None of them gets a pass.

**The critic stays as it was.** It runs only when the project is `depended-on`. The fix round needs findings, and the review's issues carry the same severity and title the round reads, so it runs without the critic.

**What it costs.** A run that is not `depended-on` spawns at most nine agents, not seven: the fix agent and the second review. A run whose first review approves spawns none of them. `AGENTS_AT_MOST` declares the number and the drain reads it.

Decided 2026-11-14.

## Refutation criterion

This is wrong if fix passes on young projects mostly end in a second refusal, so that the pass only adds an agent and a review to a ticket that is parked anyway: the log of a run says whether the second review approved.

## Considered options

- Run the full loop, with the critic, for every project: rejected, ADR 0028's cost argument stands for the critic, an adversary on every ticket of a project nobody relies on.
- Leave the ticket parked and make the owner's edit cheaper: rejected, the cost was the rebuild of a feature for one finding the code could be fixed for.
- More than one pass: rejected, a second refusal after a fix is a sign the ticket or spec is wrong, which is the owner's.

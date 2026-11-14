---
status: accepted
---
# The agent splits a ticket that is too large, and its parts inherit the label

Amends ADR 0008 (only the owner applies the agent-ready label) and ADR 0031 (what merges without the owner).

A ticket that stops as too large (the preflight's `## Size`, the spec stage's estimate, or the measured stop) was handed back to the owner to split. Splitting by size decides nothing about the product: the same promises are built, in smaller pieces. Asking the owner for it is a question with one answer, and it stalled the drain until the owner came back.

## Decision

**The agent splits it, and the parts carry the agreement.** A part names its ticket with `Split from: <ref>` (`#N`, or the ticket file). The label guard lets an agent write the agent-ready label on a part only when the ticket it names carries the label now, as the default branch (a local ticket) or the tracker (an issue) shows it, never as the part's own text says. The merge judge's label rule reads the same. The agreement reaches one level: a part of a part goes back to the owner.

**The scope check is deterministic, and one script holds it.** A ticket's promises are its acceptance criteria, the `- [ ]` lines (ADR 0036 reads promises the same way). Every promise of a part must appear in the parent verbatim, and together the parts carry each promise of the parent exactly once. Prose outside the criteria is free. A split that adds, drops, rewords or doubles a promise is refused, and the refusal names the line. The rule lives in `ratchet_guards.splits`, which the guard, the merge judge and `scripts/split.py` all read, so they agree.

**The parent is closed as `split` and its text is never edited.** The drain's `split` action files the parts, each waiting on the one before (the first on what the parent waited on), writes the outcome `split` with links to the parts in the parent's note, and keeps draining. A ticket that waited on the parent waits on its parts. The parent's text stays as the owner agreed it (ADR 0031). A plan the check refuses parks the ticket as too large, which is the owner's, as before.

**What the guard does not cover.** The guard sees a direct Edit or Write to a local ticket. A patch or shell write of the label still asks, as before. The sanctioned path is `drain.py split`, which also files tracker parts itself with `gh issue create`.

## Refutation criterion

Wrong if a split the check passes changes what the owner agreed: a part builds something the parent did not promise, or a promise is lost, in any of the next ten splits. Wrong too if more than three in ten splits are refused by the check on a line the owner would have accepted, since the check is then too strict for the agent to use. Wrong if a part of a part reaches the label without the owner.

## Considered options

- **Keep asking the owner.** Rejected: one answer, one decision-free question, and the drain stops until it is given.
- **Let the agent judge whether the parts match.** Rejected: the check is the guarantee, so it is a script, not an opinion.
- **Edit the parent to list its parts.** Rejected: ADR 0031 keeps the owner's text as the owner wrote it.

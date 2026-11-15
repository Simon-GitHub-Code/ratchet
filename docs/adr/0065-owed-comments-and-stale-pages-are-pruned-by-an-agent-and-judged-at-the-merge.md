---
status: proposed
---
# Owed comments and stale pages are pruned by an agent and judged at the merge

Whether a page says only what the code says, and what a comment's constraint becomes, are judgements. The method decides what a script can and lets an agent propose the rest, through the merge judge (ADR 0021). The owed comments are ADR 0063's. The prunable kinds are ADR 0064's.

## Decision

**One skill, `/ratchet:prune`, with two subjects.**

- **`comments`** works off the owed `comments` entries, file by file, one batch under the change-size ceiling (`scripts/change_size.py:5-7`). Narration of what the code does is deleted. A comment that narrates what a test checks is deleted, and the test is renamed when its name does not say it. An assertion message is added only where the failure output would not say what broke. A comment that claims a constraint is first encoded, then deleted: a behaviour as a test named for it, a shape as a type, a repository-wide rule as a lint rule, a reason for a choice as a decision record. Decision records are collected into a change of their own, whose merge is the owner's (`scripts/merge.py:30-32`), so the code changes merge without the owner. The batch drops its ledger entries in the same change.
- **`pages`** may cut or delete text only in pages of a kind marked `pass`. It never touches a guard path. It never cuts a protected section, an instruction (how to install, run, deploy or reach someone) or a reason. Each cut is listed in the change description with the path and the symbol that carries it. `documents.py cited` is red when a cited path is missing at the head or does not hold the symbol as a whole word.

**Judged like any change.** An independent review agent reads each removed passage against its encoding or its citation. It approves only when nothing is lost that no file now states. Its verdict goes into a verdict record, so the merge judge passes the change without the owner.

**The route names it.** The route asks the review, the safeguards and the owed architecture before the ticket step (`scripts/route.py:823-828`, `:935-953`). After the owed architecture and before the ticket step, owed `comments` entries name `/ratchet:prune comments`. Otherwise a due prune pass names `/ratchet:prune pages`. Each offers the step it would otherwise name, as the owed architecture does, and neither is red.

**When a prune pass is due.** Two conditions, both decided from git.

- At least N merges have landed on the default branch's first-parent line since the last pass. The last pass is the last commit there typed and scoped `docs(prune)`, or the commit that a pass which cut nothing records in the clone's state directory. N is set by the binding's `- Prune after:` line, ten by default.
- Some page of a prunable kind cites a tracked path, as a backticked path or a relative link that resolves, that changed after the page's own last change.

**No gating backstop.** `reports` prints the prunable lines per kind, and that number informs only.

## Consequences

A pass costs one review agent and no owner's merge, except for the decision records a comment batch writes.

## Refutation criterion

This is wrong if any of these is seen:

- A pass waits on the owner more than one time in ten.
- An agent later re-asks, or rebuilds wrongly, something only a cut passage said, as shown in an overturned assumption or an incident.
- The owed comments do not shrink across five passes.

## Considered options

- **A pass after every merge.** Rejected. It costs a review per feature, and most merges leave every page true.
- **The pipeline's review prunes in each feature.** Rejected. It widens the change and the reviewer's axis, and pages go stale across features, not within one.
- **A ratchet on prunable lines.** Rejected. It blocks a readme line for a new command and pushes text into kinds marked `never`.
- **The owner reviews every pass.** Rejected. Pruning is reversible, and git holds every cut.
- **Clean-up tickets built by the drain.** Rejected. The agent-ready label is the owner's agreement (ADR 0008).
- **A reason in an assertion message for every deleted test comment.** Rejected. Prose moved into strings is what ADR 0063's refutation criterion watches for.

## Open for the owner

- **1. Prune after ten merges.** Default taken overnight: ten, set per project by `- Prune after:`. Recommendation: accept, and revisit after the first five passes. Cost: a page can stay stale for up to ten merges, and the second condition keeps a pass from running when nothing it cites changed.

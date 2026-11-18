---
status: accepted
---
# The change-size gate does not count the owed ledger

Amends ADR 0045: the owed ledger, `quality/owed.toml`, is not counted, beside the records and the lockfiles.

A real project on 0.13.2 could not merge its `/ratchet:fill` change. That change binds the comments lister, and `just comments --record` writes every comment the project already has into the ledger as owed (ADR 0063): 1,917 entries and 11,493 ledger lines. The tier 1 `change-size` gate counted the ledger as product code, 11,937 lines against a ceiling of 1,500, on a change of about 450 lines without it. At about six ledger lines an entry, a project with a few hundred comments hits it. The change cannot be split, since only the change that binds the lister may record.

## Decision

**The ledger is not counted, in either direction.** `change_size.py` lists `quality/owed.toml` with the method's records. Its added and its deleted lines are both left out of the files and the lines the ceilings bound.

**Its growth is held elsewhere.** `ratchet-lowered` (`lowered.owed_grown`, through `owed.grown`) is red on an entry the change adds, save where ADR 0037 and ADR 0063 allow one: a pin move, the change that first binds the lister, and a rename. The ledger is a guard path, and the merge judge passes it without the owner only when entries go and the pin does not move (`merge_ready.more_than_shrunk`). A size ceiling on the same lines adds no bound these lack.

**Its shrinking is a tightening.** An entry that goes is a debt paid (ADR 0037). Counting the lines that pay it would make a change look larger for doing what the method asks.

## Consequences

A `/ratchet:prune comments` batch is sized by the comments it deletes and what encodes them (ADR 0065), not by the ledger it drops. An owed comment holds about six ledger lines on the project above (11,493 lines for 1,917 entries). Counting its removal would make each comment cost about seven lines where it costs one, and a batch under the ceiling would hold about a seventh as many comments.

## Refutation criterion

This is wrong if a change grows the ledger past what `ratchet-lowered` and the merge judge refuse, or if a change whose ledger lines are left out turns out too large for the review to judge, as shown in a verdict or an incident.

## Considered options

- **Count the ledger's removals only.** Rejected. The lines that grow the ledger are already held, and the lines that shrink it are the ones not worth bounding. It would shrink prune batches about sevenfold.
- **Raise the ceiling for the change that binds the lister.** Rejected. The gate would need to know which change binds the lister, which `ratchet-lowered` already knows, and the prune batches would still be counted.
- **Record the comments over several changes.** Rejected. ADR 0063 lets only the change that binds the lister record, so the ledger cannot grow later without a pin move.

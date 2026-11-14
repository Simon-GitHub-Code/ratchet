---
status: accepted
---
# A relaxation record binds the one change that adds it

`ratchet-lowered` passed a loosening when any ADR in the head tree carried `Relaxes: <key> from <old> to <new>`. Two paths broke the ratchet's promise that a threshold only tightens and every loosening carries its own record. First, a record outlived its step: one branch lowered `gate.floor` from 2 to 1 with a record and landed, another raised it back, and a third lowered it again with no record and was green, "relaxed by" the first branch's ADR. Shorter: a record that landed alone let a different branch's loosening pass, red on its own branch and green on the merge result, which is what the merge judge reads. Second, a key removed with a record and later written back looser was green, since a key absent from the merge base counted as new.

## Decision

**A record excuses a loosening only in the change that adds it.** The gate reads a `Relaxes:` line only when the same ADR file at the merge base does not hold it (a line count, so moving a line within a file gains nothing). The merge base is the default branch's tip for a branch, and for the merge judge's merge result too, so a record already on the default branch is never spendable and a record added by one branch never covers a step another branch makes, whichever lands first. A record's `<old>` and `<new>` already had to equal the base's and the head's value, and still do.

**A key the merge base lacks is judged against the value it last held.** One `git log` limited to `quality/thresholds.toml` walks back from the merge base to the newest version that held the key, and that value is the `before` for it. Removing a key and writing it back looser is then a loosening that needs its own record (`from 2 to 1`); writing it back at or above the old value is green; a key the file never held is new. The walk stops once every re-added key is found, and runs only when head has keys the base lacks. History it cannot read, as in a shallow clone, leaves the key new, so CI must check out full history for this gate.

**A key may be forgotten only through named steps.** A retype chain 2, then `true`, then 1 is allowed when each step is its own change with its own record (`from 2 to true`, then `from true to 1`). The same chain in one change, or with either record missing, is red, because the move from the base's value to the head's is the one that needs naming. The rename of a key by value (ADR 0045) is judged before this applies, so it is unchanged.

## Refutation criterion

This is wrong if a loosening, or a re-added key looser than the value it last held, is green with no `Relaxes:` line added by the same change naming exactly the base's and head's values, or if a change that adds the right record for its own loosening is red.

## Considered options

- **Keep reading the whole tree and retire records by hand.** Rejected: nothing retires them, which was the fault.
- **Read records from the diff of the range.** Rejected: uncommitted and untracked files would need separate handling. Comparing each ADR's lines with the base's covers committed and uncommitted work alike.
- **Remember removed keys in a ledger.** Rejected: the thresholds file's history already holds them, and a ledger is another file to forge.

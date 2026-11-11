---
status: accepted
---
# A survivor is identified by its place in its unit, and a register entry excuses one mutant

The mutation gate keyed a stryker survivor on `<file>:<mutator>:<replacement>` plus the hash of its enclosing unit, and kept the live set as a set. Two survivors of one mutator at different positions in one function therefore had the same key. One register entry excused both, and a second survivor added to the function later was accepted with no entry of its own. A minimal stryker report reproduced it: two `ConditionalExpression` mutants with the replacement `true` in one function, one entry, gate green. The replacement was also cut at 60 characters, which made distinct mutants collide the same way.

We decided three things. First, the identity of a stryker survivor ends in its place inside its unit, `<file>:<mutator>:<replacement>@<start line>:<start column>-<end line>:<end column>`, with lines counted from the first line of the enclosing top-level declaration (the unit the function hash already covers). An edit outside the unit moves no line offset and leaves the entry valid. An edit inside the unit changes the hash, so the entry is reviewed again either way, which is what the hash is for. The mutated text and an occurrence index was the alternative; it loses the position the owner reads and renumbers on any edit, and gains nothing the hash does not already give. Second, entries and live survivors match one to one: each entry excuses at most one survivor, a survivor with no entry of its own is named with the entry to paste, and an entry nothing claims is stale. A register with fewer entries than live survivors is red whatever the keys look like, for both tools; mutmut names are already unique per mutant, and the same matching keeps a listing repeated twice from passing on one entry. Third, a mutant that timed out is not a verdict on a registered survivor. Both tools count a timeout as a kill, which is right for a mutant that hangs the suite and wrong for a survivor the run was too slow to finish, so a register entry whose mutant timed out is reported as undecided, with the instruction to rerun it with less load or, if it times out unloaded, to remove the entry. It no longer reads as stale.

Registers written before this carry identities without a position. Such an entry cannot say which mutant it excuses, so it is never accepted: it is red, the message says it has the old identity, names the live mutants it matched by file, mutator, replacement and function hash, and the unregistered survivors above it carry the new entries to paste. The owner replaces each old entry by one new entry per mutant they judge equivalent, with a reason of their own, as the register is a guard path. The cost is one review of the register in each adopted repository that uses stryker, the first time the gate runs after the upgrade. Decided 2026-11-11.

## Refutation criterion

This is wrong if an unrelated edit outside a function invalidates entries for it: change a file above a registered function and run the gate, and if an entry goes stale, the position is not relative to the unit. It is also wrong if two survivors can still share an entry: a stryker report whose mutants share a file, mutator, replacement and span but differ otherwise, or a tool that numbers mutants so that two distinct ones print one name, shows the key is still too coarse.

## Considered options

- The mutated text plus an occurrence index among equal mutants in the unit: rejected, it shows the owner no position and renumbers when any equal mutant moves.
- Absolute line numbers: rejected, any edit above the function invalidates every entry below it.
- A count per key (one entry excuses `n` survivors): rejected, it is the original hole with a number on it, and the reason covers none of the survivors in particular.
- Accept an old entry when exactly one live mutant matches its old key: rejected, the old entry was written when it may have excused several, and the register is the owner's to re-review, not the gate's to reinterpret.
- Treat every timeout as a survivor: rejected, a mutant that hangs the suite is usually a kill, and both tools say so.

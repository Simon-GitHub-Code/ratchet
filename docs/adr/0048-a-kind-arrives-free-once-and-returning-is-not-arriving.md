---
status: accepted
---
# A kind arrives free once, and returning is not arriving

ADR 0028 and ADR 0030 make the change that brings a kind free: the count goes from none at the merge base to any number, the row becomes owed, and while it is owed a change that adds another instance is red. "None at the merge base" was the whole test, so an owed kind could grow without limit and never be bound. A model of the gate (4,158 states), replayed on a real repository, found three landings that touch nothing of the binding:

1. add one instance: a note that the kind has arrived, green.
2. remove every instance: green.
3. add two instances: a second note that it has arrived, green.

The row is still `later:`. The kind arrived twice, free both times, and the run repeats forever. It has the shape ADR 0047 closed for threshold keys: removal followed by a return is read as new.

## Decision

**An arrival is free only if no commit on the default line up to the merge base ever held an instance of the kind.** When head adds instances to a kind with none at the merge base, the gate looks for the newest first-parent commit up to the base whose tree held one. If it finds one the kind is returning, it is owed, and adding instances is red (the message names that commit). If it finds none the arrival is free as before, however many instances and files it brings.

**Ever, not "since the row was last bound".** A bound row is never judged (nothing unbinds, ADR 0028), so for a row that is still `later:` there is no binding event to measure from; the two readings differ only for a row added to the table after the code existed, and there the code was already present and the stricter reading is right.

**One path-limited read, stopped at the first hit (as ADR 0047's `forgotten`).** It runs only for a kind that has none at the base and gains instances at head, which is rare. One first-parent `git log --name-only` lists the commits; only a commit that touched a path the kind finds has its tree searched, with the merge base's table. An instance on the default line was introduced by a commit that touched its path, so no instance is missed; one added and removed inside a merged branch never reached the default line and does not count. A repository without the history, as a shallow clone, finds nothing, so every arrival reads as free: CI must fetch full history for this gate.

**Unchanged.** A bound row takes instances. A declared life event is never red. Moving an instance is green. A branch green at its fork still cannot add to an owed kind by landing late, since the merge base, not the fork, is the reference. Binding the row in the change that returns is green.

## Refutation criterion

This is wrong if a kind that never held an instance on the default line is red on its first arrival, or a kind that held one, is back to none, and still owes an unbound row can gain instances in a green change. It is also wrong if reading the history makes the gate slower than the change-size gate on a repository of ten thousand commits where a kind arrives for the first time.

## Considered options

- **A durable arrival record in the binding or a ledger.** Rejected: another file to forge or forget, when the history already holds the fact.
- **Count instances over every commit of the range.** Rejected: it reads each tree, where the log of names lets most commits be skipped.
- **Measure from the row's last binding.** Rejected above: no such event exists for a row still `later:`.

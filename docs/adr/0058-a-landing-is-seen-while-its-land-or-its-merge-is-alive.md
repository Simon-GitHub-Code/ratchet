---
status: accepted
---
# A landing is seen while its `land` or its merge is alive

`drain.py land` held a lock of the kernel's on the ticket's branch and ran the binding's `merge` entry point under it. Killed alone, `land` freed the lock while the merge it started ran on, so `update`, `close`, `split`, `clean` and a second `land` saw no landing. A `close --outcome parked` was merged over, and `clean --all` removed the tree under the live merge, which then landed with no note. A model of the drain found both.

## Decision

Three locks of the kernel's, none inherited by a child. Each is freed when its holder dies, however it dies.

- **The landing lock**, the ticket's, as before: a `land` holds it from before its merge until it ends. It keeps two `land`s of one ticket apart, and it is what the ticket commands probe.
- **The merge lock**, held by `merge.py` itself for its whole run, in the repository's common git directory, at a path both `drain.py` and `merge.py` take from `merge.merge_lock_path`. It is the first act after the arguments, and it is skipped for `--records`. A second merge of one branch waits about a second, then prints that a merge of the branch is already running, merges nothing and exits 2. A merge run by hand holds it too, so the drain sees the owner's hand merge.
- **A private lock**, one per `land`, in a file of a fresh random name beside the landing lock. `land` takes it before it starts the merge, keeps it until after its own close, and gives the merge its path in `RATCHET_LANDING`, a variable that carries only that path. Nothing else locks it. The merge, holding its own lock, tries it: if it cannot take it, its `land` is alive and it goes on; if it can, or the file is gone, the `land` has stopped, so the merge prints that, merges nothing and exits 2. The merge only reads the file: it never removes, creates or changes what the variable names. A variable naming anything but a regular private lock file (the `ratchet-landed-by-` prefix) directly in the repository's common git directory, a symlink included, is refused before any probe, with one line, nothing merged and exit 2; the merge cannot be made to delete a file by setting a variable. With no variable (a hand merge) there is no probe.

A landing is seen while either the landing lock or the merge lock is held. `update`, `close` and `split` refuse with exit 3 in both cases, with a line for each: the landing's names that it records the ticket's outcome; the merge's, held with no `land` alive, says a report or an update records it afterwards. `land` and `clean` follow the same rule. `clean` is the only housekeeping: it removes any private lock file whose lock is free, only regular files with the prefix directly in that directory and never a symlink, since a `land` killed leaves one that nobody removes.

`land` closes nothing on a merge exit other than 0 or 1.

## Considered options

- **Make the merge a child that dies with `land`.** Rejected: a merge cut off part-way is what `recover` repairs, and killing `land` alone is the case to survive, not to prevent.
- **One lock handed down to the merge by descriptor.** Rejected: an inherited descriptor is held by every process the merge's `check` leaves running, so the landing would stay seen after `merge.py` ended.
- **Trust the landing lock alone and refuse a second `land` by a pid file.** Rejected: a pid names no lifetime, and a pid file outlives its process.
- **Probe the merge lock from `land` only.** Rejected: it would not cover the hand merge, which has no `land`.

## Consequences

A second `land` that meets an orphaned merge in its last instant starts a merge that finds the merge lock held: it is refused once, by the merge, with "run land again". A `clean` may remove a private lock file in the instant between `land` creating it and locking it; the merge then finds the file gone, stops, and `land` is run again.

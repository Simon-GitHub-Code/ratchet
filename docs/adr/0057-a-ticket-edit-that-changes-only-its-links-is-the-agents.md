---
status: accepted
---
# A ticket edit that changes only its links is the agent's

Amends ADR 0031 (a labelled ticket stays still). Decided by the owner, 2026-11-14.

A skill rewrote the `Parent:` line of two agent-ready tickets from one path of the spec to another path of the same file. ADR 0031 sends every change to a labelled ticket to the owner, so a path fix cost the owner a merge. The rule exists because a ticket the owner agreed to is frozen: what it says to build must not move under the label. A link that still names the same file moves nothing.

## Decision

**A change to a labelled ticket that touches only its link lines, each still naming the same file, merges as records do.** `link_only` in `scripts/merge_ready.py` holds when all of these are true:

- The ticket is text at the merge base and at the head, and the change keeps it. A ticket the change renames, moves or deletes is not an edit of it.
- The two texts have the same number of lines, and each line that differs is a `Parent:` or `Blocked by:` line in both, as `feature_preflight` reads those lines (its own patterns). A line added or removed, a section heading, a criterion, the title, the status or the label is another difference, and the ticket stays the owner's.
- On each changed line, the text around the `.md` refs is identical and the refs are as many. A link added or removed is another difference.
- Each ref that changed names, from the folder of the ticket, one regular file in the head's tree, and the old and new refs name the same one. The judge walks the path component by component in the commit's tree, never the work tree. A missing file, a directory, an absolute path, a path that climbs out of the repository, and a symbolic link at any component make it uncertain, and uncertain is the owner's.

The judge does not trust its own pattern for how a line reads. It parses both texts with the preflight's `Ticket`, the parser the drain and the route use, and the edit passes only when `parent` and `blockers` give the same number of refs and each pair is either two numbers with the same text, or two paths that lead to the same regular file. A path read as a number on one side is the owner's.

Every other rule of ADR 0031 still applies to the rest of the change. The spec the ticket inherits from, a ticket it is blocked by and every other path are judged by their own rules, and the label count, the number and the close rules still read the ticket. A link edit frees nothing but the ticket's own link lines. When it holds, the judge gives no reason for that ticket; the other reasons it prints are unchanged.

## A parent line reads a path before a number

The preflight's `PARENT_LINE` tried `#?\d+` before the `.md` form, so `Parent: 02-b.md` read as the number `02`, which `reads` turns into every local ticket numbered 2, or issue 2 on a forge. A path read as a number is a defect, so the pattern now tries the `.md` form first, as `BLOCKER_REF` does. This changes how a parent line whose file name starts with digits is read: it is a path. No fallback keeps the old reading.

## What an agent could forge

An agent can write any link text. The judge reads where the old and the new text lead, in the commit it merges, and lets only a pair that leads to one file pass. Changing a link to another file, or to a file that is a link, is a change of what the ticket reads, and stays the owner's. A change of the file a link names is a change of that file, judged as ADR 0031 judges it.

## Refutation criterion

This is wrong if a link-only merge changes what a labelled ticket builds or whether it may start. A test in `hooks/tests/test_merge_ready.py` for each way found goes in before the rule changes. It is also wrong if the owner still merges path fixes by hand: a form of equivalent link the rule reads as different belongs in the tests.

## Known residue

- A ref is read from the folder of the ticket, as the preflight reads it. A `Parent:` path written from the root of the repository names, to the preflight, a file that is not there; changing it to a path that works is a change of what the ticket reads, and the owner's.
- A ref that is a number or a tracker issue is compared as text: any change to it is the owner's.
- The links in a `## Parent` or `## Blocked by` section are not link lines here; a change to them is the owner's.

## Considered options

- Let any change to a link line pass: rejected. `Parent:` selects the spec the conjecture is read from, so a link to another spec changes what is built.
- Compare the link text with its spelling normalised: rejected. Text that looks equal can lead elsewhere through a link on the way, so the files are compared.
- Trust the line-level pattern alone: rejected. A reviewer showed a rewrite of `./02-b.md` to `02-b.md` that named one file but read as a path and then as a number.
- Resolve in the work tree: rejected. The judge reads commits, and the work tree may hold what the merge does not.

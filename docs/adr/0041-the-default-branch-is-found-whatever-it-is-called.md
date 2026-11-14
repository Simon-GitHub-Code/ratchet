---
status: accepted
---
# The default branch is found whatever it is called

In a repository with no remote whose default branch is called something other than `main` or `master` (`trunk`, `develop`), every path guard was off. `git reset --hard`, `git branch -f`, `git update-ref`, `git merge`, `git push . x:trunk` and an edit of `guards/manifest.toml` were all allowed, where on `main` each is refused or asks. Two things shared the fault. The committed manifest is read from "the default branch", and `ratchet_guards.committed` found that only through `origin/HEAD`, `origin/main`, `origin/master`, `main` and `master`: finding none, it read no manifest, so the guard took adoption never to have happened. And every ref-move refusal and every "is this a default branch" test (ADR 0016, ADR 0021) compared a branch name with the fixed pair `main` and `master`, so a default branch of another name was never protected even where the manifest was found, `origin/HEAD` naming it included.

## Decision

**When none of the fixed refs exists, the default branch is the first branch the repository's main work tree was on**, read from that work tree's `HEAD` reflog: the oldest `checkout: moving from <branch>` line, else the branch it is on. The first reading of this decision took the branch the main work tree has checked out now. That was wrong: an agent can commit a weakened manifest on a branch in a linked work tree, remove that work tree and switch the main one to the branch (or detach `HEAD` onto it), and the weakened manifest then governs, though no ref moved. Moving `HEAD` adds a later reflog line and changes the current branch; it never changes the first line. An agent-made orphan or rewritten branch comes later in the reflog, so it cannot win. The fixed candidates keep their order and win when present, so no repository judged before is judged differently.

Two moves of `HEAD` leave no `checkout` line, so they are refused on a default branch: `git checkout --orphan` and `git switch --orphan`, and `git symbolic-ref HEAD <ref>`. `git reflog expire` and `git reflog delete` are refused anywhere.

**The default branches are a set, and every rule reads the set.** `main` and `master` stay in it; the branch the manifest is read from joins it (`GitRepoState.default_branches`, which already did so for where an edit lands). `on_main`, the refusals that move or create a ref (`refmoves`), the push and merge rules, and a switch to a name only the shell or git can resolve all take the set from `Guard.defaults`, so `git branch -f trunk`, `git push . x:trunk` and `git merge` on `trunk` are refused as on `main`. A `git` aimed at a repository the guard cannot read is still judged against `main` and `master` alone.

**Renaming the default branch no longer switches the guards off.** `git branch -m main gone` left no branch the guard recognised; the reflog and the main work tree's branch still lead to `gone`. The `refmoves` refusal of that rename stays as it was.

## The reflog covers only the window before conformance is first run

The reflog is a file, and git forgets it (`gc.reflogExpire`, 90 days by default). The refusals above cover the git commands that move `HEAD` unrecorded or erase the log. They do not cover a write to the reflog or to the work tree's `HEAD` file by a tool that is not git, nor the expiry. Once the log is lost the fallback reads the branch the main work tree is on, and the weakened-manifest route above is open again.

So the reflog anchor is a bridge and not a place to stay. **Conformance is red while the default branch is found only through the reflog** (`check_default_branch`, raised for the owner with `ratchet_guards.committed.anchored_on_reflog`). Its repair line asks for one step: add the forge remote, or name the default branch `main` or `master`. The guards then read the branch from git's own refs, which no log expiry touches. Until the owner takes it, the guards in such a repository are as strong as the reflog and no stronger: the anchor covers the window between the repository adopting the method and the first conformance run, which every merge and every upgrade runs.

## Refutation criterion

This is wrong if a repository with no remote and a default branch of another name has its path guards off while the owner's main work tree has only ever been on that branch, or if a branch the owner never made the default is read as one.

## Considered options

- **The branch the main work tree is on now.** Rejected: `HEAD` moves, see above.
- **Every local branch whose history added the manifest.** Rejected: an adoption branch holds the manifest before the owner merges it, and the guard would read its unreviewed text, which ADR 0005 forbids. An agent can also make such a branch.
- **The branch with the oldest or the fewest commits.** Rejected: an orphan branch an agent creates beats both.
- **Refuse every unknown default and ask the owner to name it.** Rejected for now: it needs a place the owner names it, which an agent could not edit, and a new record; the reflog needs neither.

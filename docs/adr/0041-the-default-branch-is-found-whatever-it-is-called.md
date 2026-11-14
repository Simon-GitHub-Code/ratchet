---
status: accepted
---
# The default branch is found whatever it is called

In a repository with no remote whose default branch is called something other than `main` or `master` (`trunk`, `develop`), every path guard was off. `git reset --hard`, `git branch -f`, `git update-ref`, `git merge`, `git push . x:trunk` and an edit of `guards/manifest.toml` were all allowed, where on `main` each is refused or asks. Two things shared the fault. The committed manifest is read from "the default branch", and `ratchet_guards.committed` found that only through `origin/HEAD`, `origin/main`, `origin/master`, `main` and `master`: finding none, it read no manifest, so the guard took adoption never to have happened. And every ref-move refusal and every "is this a default branch" test (ADR 0016, ADR 0021) compared a branch name with the fixed pair `main` and `master`, so a default branch of another name was never protected even where the manifest was found, `origin/HEAD` naming it included.

## Decision

**When none of the fixed refs exists, the default branch is the one the repository's main work tree has checked out**, the first entry of `git worktree list --porcelain`. Git keeps it in the main work tree's `HEAD`, outside `refs/`. A ref an agent creates, renames or deletes does not change it, and the other candidates an agent could make, any local branch holding a manifest, would let an agent's own branch or an adoption branch stand in for the default one. The fixed candidates keep their order and win when present, so no repository that was judged before is judged differently.

**The default branches are a set, and every rule reads the set.** `main` and `master` stay in it; the branch the manifest is read from joins it (`GitRepoState.default_branches`, which already did so for where an edit lands). `on_main`, the refusals that move or create a ref (`refmoves`), the push and merge rules, and a switch to a name only the shell or git can resolve all take the set from `Guard.defaults`, so `git branch -f trunk`, `git push . x:trunk` and `git merge` on `trunk` are refused as on `main`. A `git` aimed at a repository the guard cannot read is still judged against `main` and `master` alone.

**Renaming the default branch no longer switches the guards off.** `git branch -m main gone` left no branch the guard recognised; the main work tree's branch is now `gone`, so it is still read. The `refmoves` refusal of that rename stays as it was.

## Refutation criterion

This is wrong if a repository with no remote and a default branch of another name has its path guards off while the owner's main work tree is on that branch, or if the guards read a branch that is not the default one for as long as the owner works in the main work tree on a feature branch: then the main work tree is the wrong anchor, and the owner must name the default branch in the manifest or the binding instead. The second is the known cost: with no `origin/HEAD` and no `main` or `master`, the guard cannot tell the owner's feature branch in the main work tree from the default one.

## Considered options

- **Every local branch whose history added the manifest.** Rejected: an adoption branch holds the manifest before the owner merges it, and the guard would read its unreviewed text, which ADR 0005 forbids. An agent can also make such a branch.
- **The branch with the oldest or the fewest commits.** Rejected: an orphan branch an agent creates beats both.
- **Refuse every unknown default and ask the owner to name it.** Rejected for now: it needs a place the owner names it, which an agent could not edit, and a new record; the main work tree needs neither.

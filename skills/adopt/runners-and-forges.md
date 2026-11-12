# Another runner, another forge, or no forge

The templates are the `just` and GitHub examples. Step 6 of the adopt skill sends you here when the repository is something else. The entry points, their names and what each must do stay the same in every case.

## A runner other than `just`

Delete the `justfile`, create the same entry points in the repository's runner, and rewrite the binding's Entry points table with its commands. Keep the two hook recipes' behaviour: `pre-commit` and `commit-msg` answer `RATCHET_HOOK_PROBE` before any gate runs, or `hooks_installed.py` cannot tell whether a commit would run tier 0. Keep the three service gates on a `boot` and a `ready` value, and hand each long gate its shard from `SHARD` and `SHARDS`. Keep one list of gates per tier in that runner, run each tier's gates at once through `scripts/parallel.py` (`--runner` takes the runner's command prefix), and have the CI build its matrices from those lists, never from a copy.

## A forge other than GitHub

Move the pull request template to the path the forge reads and name it on the Forge section's template line. Replace `.github/workflows/` with the forge's CI, running the same entry points in the same shape: tier 0 first, one job per gate, the long gates as one job per shard of the count the binding's `- Shards:` line holds, superseded runs cancelled, caches keyed on the lockfiles. Schedule `weekly` there once a week, and have a red run open or update one issue. Put those paths in the manifest's `ci` group.

The `merge` workflow has no counterpart in another forge's templates. Where the forge's CI can run a job when the `ci` pipeline of a merge request finishes, write one that does what `merge.yml` does: check out the default branch and the plugin at the commit ADR 0001 names, run `merge_ready.py` on each merge request whose pipeline is green, merge the ones it passes at the commit it judged, and comment its reasons on the rest. Until that job exists, the owner merges every change, and the hand-over says so; the binding's Forge section names which of the two holds.

## No forge

No `origin`: the owner kept the repository local at init. Delete `.github/`, move the pull request template to `docs/process/change-template.md`, and name it in the Forge section as the change template. A change is then a branch whose description is written from that template to `.scratch/changes/<branch>.md` (the branch's `/` written as `-`), and `just merge <branch>` merges it: the merge judge reads the branch, `check` runs on the merge result, and the default branch moves only when both pass. A change the judge leaves to the owner, they merge with `git merge --no-ff`. Tier 0 runs from the commit hook, tier 1 as `check` before each merge, tier 2 as `check-deep` on the default branch after it. Nothing schedules the weekly gates, so the machine does: a cron line or a systemd timer runs `just weekly` in the repository once a week and writes its output where the owner reads it, and a red run becomes a ticket.

Write that as an ADR with its refuter (a merge with `check` red or unrun; tier 2 unrun for a week; the weekly gates unrun for a month) and the rejected options. Read "push" and "pull request" in the adopt skill's later steps that way: nothing is pushed, and the CI step has no workflow to pin.

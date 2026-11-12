---
status: accepted
---
# A change the gates can judge merges without the owner

Every pull request waited for the owner to merge it. ADR 0013 named this as the largest remaining count of owner actions, and the owner said what it costs: approving every pull request is the tiresome part, and the point of deterministic checks is that they make most approvals unnecessary. A merge that always needs a person also teaches the person to approve without reading, which is worse than no approval.

We decided that a change merges itself when the gates are green, the review approves, no blocker or major finding is open, no guard path changed and it goes through no one-way door. Everything else waits for the owner, with the reasons in writing. A script decides which, never an agent: anything an agent can set is something an agent can forge, so the decision is read from commits by code the change cannot alter.

The judge is `scripts/merge_ready.py`, behind the `merge-ready` entry point. It prints `merge: agent` or `merge: owner` and one line per reason, each with a code a later script can branch on. It reads the manifest at the tip of the default branch and every other file at the merge base and at the head, through git. It never reads a work tree and never runs anything the change holds. The owner merges when:

- `guard`: a guard path changed. Two changes pass: an addition to a record (ADR 0014), and `quality/thresholds.toml` when every number moved the tight way and its shape is unchanged, which is what the `ratchet` entry point does after each feature. A path that matches a guard pattern only when letter case is ignored counts as a guard path.
- `door`: the change goes through a one-way door. The manifest lists them as `[[door]]` tables, which guard nothing: the hook never asks on one, so an agent builds a migration and the owner merges it. `on = "change"` is any change (migrations, schema, infrastructure). `on = "rewrite"` is a change to or deletion of a file the default branch holds (fixtures of stored data; a new fixture passes). `on = "removal"` is a deletion or a change that does more than add (contract artefacts and public-surface snapshots). The flag registry is a door in every repository: a flag that turns on by default, becomes permanent, or leaves the registry while off.
- `dispute`: the change adds or edits a dispute, or a dispute not yet decided names a path the change touches.
- `record`: the verdict record `docs/features/<slug>/verdict.json` is missing, is not an approval, lists an unresolved blocker or major finding or an open question, or certifies another tree. Minor findings do not hold a merge.
- `target`, `behind`, `tier2`: the pull request targets a branch other than the default; the branch does not hold the default branch's tip, so its gates ran on a merge with an older one; tier 2 is red on the default branch.
- `refs`, `manifest`: the judge could not read what it judges. It fails closed.

On a forge, `merge.yml` runs the judge. It is triggered by `workflow_run` when `ci` completes, so the workflow file, the judge (the plugin at its pinned commit) and the manifest all come from the default branch. The job runs only for a successful `pull_request` run of `.github/workflows/ci.yml` from a branch of the same repository. It checks out the default branch, fetches each head commit, and never checks out or runs a pull request's code. It takes every open pull request of the repository whose newest `ci` run on its head commit succeeded, not only the one that woke it, and for each in turn, with the default branch fetched again first, asks the judge and writes one comment, found by a marker and edited in place on later runs. When the judge says `agent` it merges through the API with the head commit named, so a branch that moved since is refused, and with a merge commit, which is what the route reads a finished feature from. The workflow token's push starts no workflow, so the job starts tier 2 on the default branch itself. Only this job holds write permissions. The `guard-changes-present` job leaves `ci.yml`: it failed the run on any guard path, an added glossary term included, and the judge now says the same thing with the reasons.

With no forge, `scripts/merge.py <branch>` (the `merge` entry point) merges the branch into the default branch in a scratch work tree, asks the judge about the branch, runs the binding's `check` on the merge result, and moves the default branch only when both pass. It refuses in a repository that has an `origin`.

The hook makes the judge the one agent path. `gh pr merge` and the merge API already asked the owner. On the default branch, `git merge`, `git cherry-pick` and a `git pull` that names another repository or branch now ask too (rule `merge-default`). The sync with the remote's own default branch still passes, and so does the entry point. Decided 2026-11-11.

Which runs the merge job counts, a `ci` run for a pull request and a `deep` run for tier 2, is ADR 0024's to say: a run counts only for what it tested.

## What an agent could forge

The verdict record is written by an agent. An agent that skips the pipeline can write a record that says it ran. The record therefore proves less than the other reasons: it catches a commit made after the review, a non-approval, and a record copied from another change, and it is how an honest orchestrator hands the pipeline's conclusion to the judge. The `guard`, `door`, `dispute`, `target`, `behind` and `tier2` reasons do not depend on it. They are read from the diff, the forge and the default branch, and hold against a forged record.

The review's independence rests on the pipeline's sight lines, and an agent runs the pipeline. The judge cannot see whether the reviewer was independent.

The gates are only as strong as what `ci` runs. A pull request's `ci` run uses the pull request's own workflow file, recipes, tool configs and tests. The first three are guard paths, so a change that weakens them goes to the owner. Tests are not: a change that weakens its own tests meets the test-count, coverage and mutation gates and nothing else.

The agent's `gh` runs with the owner's token, and this decision does not rely on branch protection. What stops an agent pressing the merge button is the hook, with the residue ADR 0016 lists.

## What the forge showed

The first version of this decision was checked on a private sandbox repository on 2026-11-12, one pull request per case. Observed: `workflow_run` runs the default branch's `merge.yml` and judge, so a pull request that rewrote both to always pass still got `merge: owner`, with the `guard` reasons; `path`, `head_sha`, `head_branch` and `head_repository` are populated as the `if:` reads them; the job's own `permissions` block was enough to comment, merge, delete the branch and dispatch `deep.yml`; the token's merge started no `ci` run on the default branch; a merge call that names a head commit other than the pull request's is refused with 409 and "Head branch was modified"; a second run edits the comment in place. A `merge` run is also started, and skipped by its `if:`, for every `ci` run on a push to the default branch.

Two things differed from what was inferred. First, of four runs queued together GitHub ran one, cancelled two and ran the newest: the group keeps one waiting run, and the pull requests of the cancelled runs got no verdict for their head. A pull request that would have passed would have waited for its next push with no word said. The job now judges every green pull request in each run, so a cancelled run loses nothing. Second, a matrix that `fromJSON` expands to no values fails the run before the job exists. A `*_sharded` list in the `justfile` that is empty gave an empty long matrix, so the long jobs of `ci.yml` and `deep.yml` now skip when it is `[]`.

Not observed: a repository whose default workflow permissions are read-only (the sandbox's setting could not be read), a token refused the merge, and the loop over every green pull request on the forge. That loop is tested against a stand-in for `gh` and awaits its first run on the default branch, since `workflow_run` runs only the version the default branch holds. That loop has since run on the forge, as the section "What the forge showed" of ADR 0024 records.

## Refutation criterion

This is wrong if a change merges itself that the owner would have stopped. Read the first twenty merges the judge passes: one the owner reverts, or says they would have refused, names a reason the judge lacks, and it becomes a test in `hooks/tests/test_merge_ready.py` before the judge gains the reason. Two such merges in twenty mean the rule is too loose to stand. It is also wrong if it saves nothing: if more than half of the pipeline's approved changes still end `merge: owner`, count the reasons, and a reason the owner waves through unread each time is either not a reason or needs a narrower reading. The standing attempts are that test file, the three `merge-ready` scenarios of `scripts/bite.py`, and the `merge-default` rows of the hook corpus; a forged input that passes goes there first.

Known residue. A flag whose default is set in code or configuration outside `flags.toml` is not seen. A required key added to a JSON contract reads as an addition. The judge reads tier 2's state on a forge only: with no forge nothing records it, and `merge.py` names `check-deep` as the next command. With no forge, a commit made directly on the default branch never meets the judge (ADR 0016 lets it pass, since it moves the branch forward and its content meets the path rules). A script file run by path, or `git worktree add <path> main` followed by a merge in that work tree on the same command line, is out of the hook's reach. A branch behind the default branch must be brought up to date and its record written again: the drain of ADR 0023 does that for the tickets it builds, with a second review, and a branch built alone is still brought up to date by hand. Between the judge's read and the merge call the owner may merge something else; the forge then merges onto the newer tip without a second run of the gates.

## Considered options

- Let the orchestrator run `gh pr merge` when a hook check passes: rejected, the decision would sit in the session that wants the merge, and the hook would have to trust arguments an agent supplies.
- The forge's own auto-merge with branch protection and required checks: rejected as the mechanism, the method cannot rely on protection being there: an agent cannot set it up (the hook denies every protection call), and the judge must also work with no forge. A repository whose owner has set protection keeps it; the judge does not need it.
- Make the judge a required status check on the pull request's own run: rejected, a `pull_request` run uses the pull request's workflow file, so the change would carry its own judge.
- `pull_request_target`: rejected, it gives a token with write access to a run that can be led into checking out the pull request's code. `workflow_run` after `ci` keeps the two apart.
- Keep the human on every merge: rejected by the owner, for the cost above.
- A "hard to reverse" field in an ADR's front matter: rejected, the change would declare its own door, and every ADR is a lasting decision by definition. Doors are paths the owner lists.
- Exempt a dependency the spec named, as the pipeline does (ADR 0011): rejected for the merge, the spec is an agent's, so "the spec named it" is not something the judge can check. The judge prints the dependency note and the owner merges.
- One-way doors as guard groups: rejected, a guard asks on every edit, and the owner wants to read a migration once, at the merge, not approve each keystroke of it.
- Squash merges: rejected, the route recognises a finished feature by its merge commit.

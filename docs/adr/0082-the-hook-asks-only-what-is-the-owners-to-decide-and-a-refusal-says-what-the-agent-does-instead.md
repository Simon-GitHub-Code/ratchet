---
status: accepted
---
# The hook asks only what is the owner's to decide, and a refusal says what the agent does instead

Read with ADR 0021, ADR 0033 and ADR 0034. Amends ADR 0021 where the hook asks the owner about a merge an agent types, and `skills/method/guards.md` where it lists what the hook asks.

The guard hook asks the owner in six cases: a merge of a pull request, a merge onto the default branch, the agent-ready label, a write of a CI workflow, a write of an agent-instruction file, and a tool call it cannot read. The owner approves most prompts without reading them, so a prompt guards nothing unless the owner's attention is warranted, and each one stops an agent that runs unattended. A deny does not stop it: the agent reads the way out and goes on. The owner reviewed the six and kept the ones that are theirs to decide. The guards stay deterministic, generic and out of an agent's reach.

This record is built in four pull requests ([For the builders](#for-the-builders)). The first, the merges, is built. Where a sentence below describes what a later one builds, it says so.

## Decision

**A merge an agent types is denied once the project is adopted and its way out exists.** Built with the merges. Adopted means what turns the path rules on: `guards/manifest.toml` committed on the default branch, read from git as the hook reads it for every path. The way out exists when the `justfile` committed on the default branch, read the same way, defines both the `merge-ready` and the `merge` recipe, recognised by the reader conformance uses. A project adopted before those recipes shipped and never upgraded lacks them, and there a merge asks in the unadopted words. Removing a recipe from the default branch takes a merge, and what it leaves is that ask, the answer before the merges were built, so an agent cannot weaken the guard by it: a recipe removed only in the work tree or on another branch changes nothing. Where both hold, the hook denies `gh pr merge`, a write to the forge's pull-request merge or branch-merge API, the merge and auto-merge mutations (rule `merge-pr`), and, on the default branch, `git merge`, `pull`, `cherry-pick`, `am` and `read-tree` (rule `merge-default`). A change lands through the merge job on a forge, or with no forge through the `merge` entry point, and each merges what the merge judge passes. The deny names them, and `merge-ready --head <branch>`, which prints each reason a change waits. A merge the judge leaves to the owner is the owner's to make, in the forge's interface or their own terminal, and the agent reports the judge's reasons. A merge tried out locally runs on a detached `HEAD` or a scratch branch, which the deny names too. The hook does not run the judge: the judge needs the forge's checks and runs, which the merge job reads a few seconds after `ci`, and a hook would have to make those network calls inside its own run and refuse whenever the network is slow.

**Before adoption a merge an agent types still asks the owner, in the same words.** Built with the merges, as rules `merge-pr-unadopted` and `merge-default-unadopted`. The hook runs in every repository of a person who has the plugin installed. Where nothing has been adopted there is no judge, no merge job and no `merge` entry point, so a deny would have no way out the agent can take, and it would change a project that never chose the method. Adoption is read in the directory the command runs in, so a forge merge named for another repository (`gh -R`) from a directory that is not adopted asks.

**A sync of the default branch passes, in either kind of project.** Built with the merges. A merge or a pull on the default branch whose every operand is the default branch's own remote-tracking ref, named or as its upstream, gets no verdict, with or without `--ff-only`, as `git pull` from the default remote already did. It brings in only what the forge merged, and the merge commit stays local, since the hook denies a push to the default branch. An operand git reads as anything else is a merge: `FETCH_HEAD`, another remote's branch of the same name, a local branch or a tag named like the tracking ref, an upstream moved to another branch, or a second operand beside the tracking ref. So is a sync on a line that may first change what a name points to, and one whose `git` carries a `-c` setting, whether it names the remote and branch or not. That last case is new: before the merges were built `git -c branch.main.merge=refs/heads/<feature> pull` passed as a sync, and git merged the other branch. The deny offers `git pull --ff-only` only where the default branch tracks a remote.

**A CI workflow will be asked about at the push, not at the edit.** Decided; not yet built (pull request 4). Until then the hook asks before each write of a workflow it reads, as before. The forge runs a branch's own workflows with the repository's secrets once a commit reaches it, and no merge is judged before that. So the hook will ask when a push sends a workflow tree that differs from the default branch's, and show the files and commits that change it. Once the owner lets a tree through, no later push of that same tree will ask. A push whose source the hook cannot resolve will ask. A write of workflow contents through the forge's API, and a write of trees or commits through its git data API, will be denied, and the agent commits and pushes instead. A workflow edit on a branch will get no verdict: the merge judge sends it to the owner, and on the default branch the edit is denied as now.

**An agent-instruction file will be asked about once per file per session.** Decided; not yet built (pull request 2). Until then every write the hook reads asks. The hook is never told the owner's answer, so it will record the files a prompt named and mark them approved only when the harness reports that the call ran, which a rejected call never does. A harness that passes no session, or reports no such event, will still ask on every write. The approvals will live beside the guard's log, and the hook will refuse every write of them it can read.

**A command line the guard cannot read to its end will be denied, and the deny will say how to restate it.** Decided; not yet built (pull request 3). Until then it asks. That covers an unclosed quote or substitution, more than 64 possible directories and scripts nested more than eight deep. The way out is to restate or split the command, or to write the script to a file and run it by path. When the hook gets a time limit of its own, a call it cannot read within that limit will be denied the same way, with running it again on its own as the first way out. The ask will stay only where the guard itself failed: input that is not the harness's, an internal error, or a guard that cannot load. A deny there would lock the session.

**The agent-ready label still asks.** Unchanged. It is the owner's agreement that a ticket is ready to build.

## What a project sees

With the merges built, in an adopted project whose default branch defines the `merge-ready` and `merge` recipes, an agent that types a merge is refused and told what merges by itself and how to see why a change waits; the two merge prompts are gone. A sync merge of the default branch with its own remote-tracking ref no longer asks, in any project. In a project that has not adopted the method, or whose default branch lacks either recipe, a merge an agent types asks as before, with the same words. No permission rule changes.

Once the later pull requests are built, command lines with an unclosed quote or seven `;`-chained `cd`s will be refused with a way to restate them, where now they ask. A forge API write of a workflow will be refused on every branch. A push that changes a workflow will ask once per tree, and the prompts at a workflow edit and at a repeated write of an approved instruction file will be gone.

## Refutation criterion

This is wrong if any of these shows:

- an agent lands a change on the default branch of an adopted project without the judge or the owner;
- a merge that passed as a sync brought in anything the default branch's own remote-tracking ref did not hold;
- once pull request 4 is built, a workflow tree reaches the forge from a call the hook read, with no prompt and not already approved;
- once pull request 2 is built, an instruction file is written without a prompt in a session whose first prompt for it was rejected;
- once the time limit denies, the guard's log shows that deny repeating on short command lines, so that an agent cannot get a call through by running it again.

## Considered options

- **Deny a merge an agent types in every repository, adopted or not.** Rejected. Before adoption nothing judges a merge and nothing merges by itself, so the deny would name no way out the agent can take, and it would change repositories that never chose the method.
- **The hook runs the merge judge and allows what it passes.** Rejected. It needs forge calls inside the hook's run, it repeats what the merge job does moments later, and it would have to pin the head it judged to close a race the job already closes.
- **Ask on a merge the judge holds for the owner.** Not taken. That prompt is the warranted kind, but the hook cannot tell it apart without running the judge, and an owner who wants the merge makes it in one click.
- **One rule whose decision depends on the adoption.** Not taken. Two rules with their own names keep the rule table a plain list of decisions, so a log line says what the owner was shown.
- **Compare a push with what the forge holds, read over the network.** Rejected. It is exact, but it puts a network call and a possible credential prompt in the hook, and the remote-tracking ref of a branch, which an agent can move, cannot stand in for it.
- **Count the instruction-file prompt, not the approval.** Rejected. A rejected first prompt would approve every later write of that file.
- **Keep asking when the hook runs out of time.** Rejected. An ask stops an unattended agent, and running the call again or splitting it is a way out the agent can take alone.

## For the builders

### The pull requests

Each pull request carries its tests red before its code and its section of `skills/method/guards.md`.

**1. Merges.** Built. `hooks/ratchet_guards/verdict.py` (the two deny rows and the two unadopted ask rows), `hooks/ratchet_guards/context.py` (`Guard.merging`, the one place the adoption and the two recipes are read for a merge), `hooks/ratchet_guards/gh.py`, `hooks/ratchet_guards/merges.py` (the sync with or without `--ff-only`, and no sync with a `-c` setting), `skills/method/guards.md`, `skills/method/route.md`, `docs/harness/opencode.md`, and tests.

**2. Instruction files once per session.** To be built. An approval store under the repository's git directory, locked like the guard's log, written from a post-tool event matched to the prompt's call. Before it merges, a live session confirms that the post-tool event carries the same call id as the prompt.

**3. Unreadable shapes deny.** To be built, after the hook's time limit lands. The shape cases and the time limit take rules of their own with the ways out above, and `unreadable` keeps only the guard's own failures.

**4. Workflows at the push.** To be built, after 2 and 3. The push compares each source's workflow tree with the default branch's remote-tracking ref, which an agent cannot move, and the forge API writes of workflows, trees and commits are denied.

## For the owner

- Whether the push of an upgrade's own plugin pin change may go without the workflow prompt is open; meanwhile it asks, once pull request 4 is built.
- Whether subagents share a session's instruction-file approvals is open; meanwhile they share them, once pull request 2 is built.

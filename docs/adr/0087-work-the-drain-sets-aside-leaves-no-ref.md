---
status: accepted
---
# Work the drain sets aside leaves no ref

Settles issue #328. Read with ADR 0023, whose point 9 says an interrupted drain loses no work. Amends `skills/feature/SKILL.md`, `skills/method/pipeline.md`, `skills/method/route.md` and `docs/model/drain.md` where they said the drain stashes.

**What was asked.** When the drain closes a ticket, or removes the work tree of an interrupted run, it set aside what the tree held uncommitted as a git stash. A stash is a commit under `refs/stash`. In an adopted project, a gate that reads the repository's history judged every reachable commit, so one parked ticket's uncommitted test, which held a made-up value shaped like a live key, turned that gate red for every later ticket, on a commit that was on none of their branches. The plugin cannot know which refs or paths a project's gates read, so the fix is that the plugin keeps none of its own state in the repository's refs.

## Decision

**1. The drain sets work aside as a file, not a stash.** The file is `<git common directory>/ratchet-kept/<branch>/<n>-<time>-<digest>.kept`, with each `/` of the branch written as `-` and `n` one more than the branch's last file, so the files sort in the order they were written. Its first line is JSON: the commit the work was on (`base`), the tree of the index, the tree of the tracked files as they were in the work tree, and that tree with the untracked files that are not ignored, and the length of each of three patches. The three patches follow, each from one of those trees to the next, written by `git diff-tree --binary --full-index`. Together they hold staged and unstaged changes, untracked files, binary files, file modes, and any file name. A nested repository that is not tracked is left out, as a stash leaves it out. The digest is of the file's bytes, so setting the same work aside twice writes one file. Nothing reachable from any ref holds the work. The trees and blobs git writes to build the file are unreachable, so git's collection removes them after its grace period.

**2. Nothing leaves the work tree before the file is proved.** The steps, and what holds the work after each:

| Step | What holds the work after it |
| --- | --- |
| 1. The note says the ticket is closing (`discarding`), as before | the work tree |
| 2. The drain takes the tree's `index.lock`, reads the index and the tree into a scratch index, and writes the file under a `.partial` name, then renames it | the work tree, and the file once it is renamed |
| 3. It reads the file back, applies the patches to `base` in a scratch index, and compares each tree with the one it read | the work tree and the file |
| 4. It puts the work tree back to `HEAD` (`reset --hard`, then `clean -d`, which leaves ignored files) | the file |
| 5. It ends the programs that still run from the tree, and sets aside what they wrote since, by steps 2 to 4 | the file, and a second file if they wrote anything |
| 6. It removes the work tree and the note's `discarding` | the file |

If any part of steps 2 and 3 fails, such as a held `index.lock`, a merge left unfinished, a file that cannot be written or read back, or a proof that does not give the same trees, the drain deletes the file it wrote, leaves the work tree as it is, and prints `kept: the work tree stays at <tree>: <why>`. A kill between any two steps leaves the work in the tree, in the file, or both. The next drain command that finishes the close, or `clean --all`, sets aside what the tree still holds: the same content gives the same file, so a rerun converges on one file and no tree.

**3. One command puts it back.** `drain.py restore <branch>` takes the newest file of that branch, refuses a checkout that holds uncommitted work of its own, switches the checkout to the branch when the branch is still at `base` and to `base` otherwise, applies the patches (the first with `--index`), and deletes the file. Each line the drain prints about set-aside work names the file and this command: `kept: what the work tree of <branch> held uncommitted is set aside at <file>: drain.py restore <branch> puts it back in the checkout`.

**4. The drain's old stashes move.** On each `clean` and `restore`, the drain reads the stash list. A stash whose message is the drain's (`On <branch>: <branch>: <word> run`, or `On (no branch): …` for a detached tree, with one of the drain's words) and whose branch has a note the drain wrote when it opened the ticket is set aside as a file by steps 2 and 3, and only then dropped. Every other stash is left alone. A stash with the drain's message and no such note, or one whose file cannot be proved, stays, and the drain prints one line naming it and `git stash show -p --include-untracked <commit>`. A second run finds nothing to move.

**5. What stays a stash.** The split's landing keeps its check that the commit hooks did not move `refs/stash` (`harmed`): that stash is the owner's, in the owner's checkout, and the check protects it. A `/ratchet:feature` run that is not approved is still stashed by the orchestrator as its skill says, and the route still holds that ticket on its `blocked` stash. That is the same class of fault, and it is a follow-up, since moving it needs a command for the orchestrator and a new reader in the route.

## What a project sees

A parked, waiting, split, merged or interrupted ticket no longer adds a commit to `refs/stash`, and a gate that reads every reachable commit judges the next ticket on its own commits. The `kept:` line says where the work went and how to get it back. The route and the plan hold a parked ticket by its note, as before, so nothing a project sees about holds changes.

## What an upgrade does

The first `clean` after the upgrade moves the stashes the drain made into files, and the gate that read them goes green on the next run. A stash the drain cannot prove is its own stays and is named on every `clean` until the owner deals with it.

## Known limit

A gate that reads files under the git directory, rather than the history, still sees the kept files. A project with such a gate excludes `ratchet-kept/` under the git directory from it, or restores and removes the kept work it no longer needs. The file holds patches against `base`: if that commit is no longer in the repository, `restore` says the file does not apply and leaves it, and `git apply` onto another commit is the way back.

## Evidence

`hooks/tests/test_drain_kept.py` closes a ticket with each outcome and walks every object reachable from every ref and reflog for a marker of the uncommitted work: none holds it, and a stand-in gate that fails on any reachable commit holding it is green in the next ticket's tree. A restore gives back the same index tree, the same files and the same executable bits, for staged, unstaged and untracked files, a binary file, a file name with a space and one with a newline. A file that cannot be written or read back, or whose proof fails, leaves the work in its tree with the `kept: the work tree stays at` line, and a later `clean --all` sets it aside. A close killed between each pair of steps reruns to one file and no tree. Of three user stashes with messages like the drain's, none is touched and two are named, and the drain's own stash moves once. The drain's model is unchanged: its stash is the set-aside copy, and step 4 is why a kill after it leaves a clean tree, as the model says.

## Refutation criterion

This is wrong if a drain loses uncommitted work that a stash would have kept, or if `restore` gives back a tree other than the one the drain set aside. It is also wrong if an adopted project's gates go red on the kept files often enough that the known limit costs more than the stash did: count the projects that have to exclude `ratchet-kept/` in the first ten that upgrade.

## Considered options

- **Tell the history gate to scan only the change's own commits (`<base>..HEAD`) in tier 0.** Declined as the fix. The plugin names no gate's tool and cannot know which refs a project's gates read, and any other ref the plugin wrote would hit the same gate.
- **Run the gate over the stash at `close` and warn.** Rejected. It names the harm and leaves it in place for every later ticket.
- **A stash commit kept in a ref of the plugin's own, such as `refs/ratchet/kept`.** Rejected. A gate that reads every ref reads it too.
- **Keep the objects as a pack file.** Rejected. Patches can be read and applied with git alone, and the proof that applies them to `base` tests the file itself, not objects the repository already holds.

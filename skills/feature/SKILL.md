---
name: feature
description: Build features from agent-ready tracker tickets with the Ratchet pipeline. With a ticket it builds that one; with no ticket it builds every agent-ready ticket at once, each in a work tree of its own, merges what the merge judge passes and ends in one report. The preflight is a script and nothing is spawned while it is red.
disable-model-invocation: true
---

# Build features from tickets

`/ratchet:feature <ticket>` builds one ticket: a number on the tracker (`4` or `#4`) or the path of a local ticket file (`.scratch/<slug>/issues/02-<slug>.md`). `/ratchet:feature` with no ticket, or `/ratchet:feature all`, builds every agent-ready ticket: [the drain](#the-drain-every-agent-ready-ticket). Load the `ratchet:method` skill first; [pipeline.md](../method/pipeline.md) says what the four roles do and [records.md](../method/records.md) what the ticket carries.

The input is the ticket the owner's `/mattpocock-skills:to-spec`, `to-tickets` or `triage` wrote, with the method's `## Conjecture` section, and the agent-ready label the owner applies (`ready-for-agent` in mattpocock-skills, spelled as `docs/agents/triage-labels.md` maps it). The label is the owner's agreement; nothing below infers it or applies it (ADR 0008).

Stay in the turn until every workflow you start has returned: in a headless session (`claude -p`) the session ends with your turn and takes a running workflow with it.

## One ticket

1. **Preflight, before any agent exists.** One Bash call: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/feature_preflight.py" "<ticket>" --root .`. It prints JSON (`ok`, `failures`, `number`, `title`, `slug`, `branch`, `base`, `label`) and exits 1 when red; `base` is the default branch, read from the checkout and the binding. Red: report `verdict: blocked` and, for each failure, its `check`, `detail` and `produce`, the skill or act that supplies the missing piece. The `hooks` failure's `produce` is the owner's command, which the guard refuses from an agent: report it, do not run it. Spawn nothing, create nothing, label nothing. Stop.

2. **The branch.** Green: `git switch <branch>` when `git rev-parse --verify --quiet <branch>` finds it, else `git switch -c <branch> <base>`. Run the preflight again with `--branch <branch>`; it must stay green.

3. **The pipeline.** Call the Workflow tool (`name: "ratchet:pipeline"` where the harness lists plugin workflows by name):

   ```
   scriptPath: "${CLAUDE_PLUGIN_ROOT}/workflows/pipeline.js"
   args: { "ref": "<ticket>", "branch": "<branch>", "base": "<base>", "preflight": "${CLAUDE_PLUGIN_ROOT}/scripts/feature_preflight.py" }
   ```

   It runs the same preflight as its first stage, then Spec, Implement, Critique, the fix loop and Review. It never commits, pushes or opens a pull request.

4. **A result that is not approved.** A `verdict` other than `approve`, an open `unresolvedBlockingFindings` or a red `checkOutput` tail commits nothing. Clear what the agents left with `git stash push -u -m "<branch>: <verdict> run"`, then `git switch <base>`, and name the stash in the hand-over; a `blocked` stash holds the ticket in the route until the default branch moves past the run. Then:
   - `openQuestions`: put them to the owner as [Questions for the owner](#questions-for-the-owner) says. When every one is answered and there is no guard edit, build the ticket again from step 1 in the same turn.
   - `guardEdits`: list them. The owner lands them on the default branch, or builds the ticket with `/pstack:poteto-mode` while present.
   - A dispute: relay it to the owner.

5. **Commit an approved change,** in this order ([pipeline.md](../method/pipeline.md), the orchestrator). Run `ratchet` when it is bound, and commit `filesChanged` with the raised `quality/` files, the message naming the slug. Write the ADR the result suggests under the next free number. With no forge, write the change description at `.scratch/changes/<branch>.md` and set the local ticket's `Status:` line to `resolved` in the same commit. Last and alone, the verdict record: the result's `verdictRecord` on stdin of `just merge-ready record`, its file committed.

6. **Publish.** With a forge: push and open the pull request with every section of the template filled. Its Conjecture section lists each of the result's `assumptions` with its refuter, Guard paths each of its `dependencies` with its reason beside the versions in `dependencyNotes`, and Decisions every addition in `recordNotes`. The `merge` workflow merges it once `ci` is green and the judge passes it. With no forge: `just merge <branch>` judges the branch, runs `check` on the merge result and merges it, or prints why the merge is the owner's.

7. **Hand over.** Say what the merge judge says (`just merge-ready`: `merge: agent`, or `merge: owner` with its reasons), and never merge by hand. When the merge is the owner's, name the passes the change warrants before they merge, from [route.md](../method/route.md) stage 8. When the change built a surface and the repository has `PRODUCT.md` but no `DESIGN.md`, say that an agent runs `impeccable document` once this merges, in a session the owner attends, since the write of `DESIGN.md`, a guard path, asks the owner. The last lines are the route: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .` in one Bash call and print its lines.

## The drain: every agent-ready ticket

`/ratchet:feature` or `/ratchet:feature all`, with `--concurrency N` when the owner wants another cap than three. The owner types it once and steps away. Each ticket is built by its own pipeline in a git work tree of its own, the tickets that do not wait on each other at the same time; each approved change is brought up to date, judged and merged one at a time; a ticket that cannot go on is parked and the others continue. Nothing asks the owner anything until the report is printed. Never merge by hand, never rebase by hand, never answer a question for the owner.

`scripts/drain.py` decides the order, the cap, each ticket's state and the report (ADR 0023). Run every command below from the repository's own checkout, on the default branch. `D` stands for `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/drain.py"`.

1. **Clear what an interrupted drain left.** `D clean --all --root .` removes every queue work tree, keeps what one held uncommitted as the stash `<branch>: interrupted run`, and prints a line for each.

2. **The plan.** `D plan --root .` (with `--concurrency N` when given) prints JSON: `base`, `forge`, `blocked`, `start`, `agents_at_most`, and per ticket its `state`, `branch`, `worktree`, `why` and `waits_on`. When `blocked` is not empty the checkout is not fit to build on: report each line, which ends with what fixes it, and stop. Otherwise say in one or two sentences what the drain will do and go on without asking: how many tickets it builds and how many at once, which are refused or held and why, and the cost, at most `agents_at_most` agents (nine per ticket, two more per ticket judged again).

3. **Start the pipelines.** For each ticket in `start`, `D open <ticket> --root .`: exit 0 prints its `branch`, `worktree` and `base`; exit 1 means the plan does not start it. Then call the Workflow tool once per ticket opened, every call in one message so the runs are concurrent:

   ```
   scriptPath: "${CLAUDE_PLUGIN_ROOT}/workflows/pipeline.js"
   args: { "ref": "<ticket>", "branch": "<branch>", "base": "<base>", "root": "<worktree>", "preflight": "${CLAUDE_PLUGIN_ROOT}/scripts/feature_preflight.py" }
   ```

   Create no branch and no work tree by hand: `open` is the gate on the order and the cap.

4. **Settle each result, one ticket at a time,** in the order they arrive, finishing one before the next: two changes that passed apart can fail together. Each sub-step runs in the ticket's work tree.

   1. **Not approved.** A `verdict` other than `approve` parks the ticket: write the result to a file made with `mktemp` and run `D close <ticket> --outcome parked --result <file> --root .`. It removes the work tree, keeps what the agents left as the stash `<branch>: blocked run`, and holds the ticket until the ticket changes. Keep the result's `openQuestions` for step 7.
   2. **Commit.** `approve`: commit the result's `filesChanged` with a message naming the slug.
   3. **Bring it up to date.** `D update <ticket> --root .`. Exit 0 with `"moved": false`: go to step 4.5. Exit 0 with `"moved": true`: run `check` and note whether it is red. Exit 2: the rebase stopped on the `conflicts` it prints, and the branch is as it was. Exit 1: close the ticket with `--outcome waits --reason "<the line it printed>"`.
   4. **Judge it again.** After a rebase or a conflict the first review no longer describes the change:

      ```
      scriptPath: "${CLAUDE_PLUGIN_ROOT}/workflows/pipeline.js"
      args: { "ref": "<ticket>", "branch": "<branch>", "base": "<base>", "root": "<worktree>", "rejudge": { "slug": "<slug>", "specPath": "<specPath>", "conflicts": [<the conflicts>], "checkRed": <true when check was red>, "dependencies": <the first result's dependencies>, "assumptions": <the first result's assumptions> } }
      ```

      One agent finishes the rebase and gets `check` green, only when there was a conflict or a red `check`; then the reviewer judges the change as it stands. Not `approve`: park as in step 4.1. Otherwise commit its `filesChanged`, if any, and use its `verdictRecord` in place of the first.
   5. **The records.** As step 5 of the one-ticket path: `ratchet` and the raised `quality/` files, the suggested ADR, with no forge the change description and the local ticket's `Status:` line, then the verdict record from the latest result, alone and last.
   6. **Land it.** With no forge: `D land <ticket> --root .` runs the binding's `merge` entry point, prints the judge's lines, removes the work tree and records merged or waiting with every reason; exit 2 means it could not ask, so report what it printed and leave the ticket. With a forge: push, open the pull request as step 6 of the one-ticket path says, and wait for both workflows: `gh pr checks <n> --watch` for `ci`, then the `merge` run that follows it (`gh run list --workflow merge.yml --limit 1`, then `gh run watch <id>`). When `gh pr view <n> --json state` says `MERGED`, run `git pull --ff-only` on the default branch and `D close <ticket> --outcome merged --root .`. Otherwise `D close <ticket> --outcome waits --root .`, with one `--reason` per line of the judge's comment on the pull request.

   A ticket the plan lists as `built` was committed by an earlier drain and has no run: start it at step 4.6. `update` refuses its branch, which ends in its verdict record and may be pushed. With no forge the merge runs `check` on the merge result, so a branch behind the default branch is judged as merged; with a forge the merge job leaves a branch that is behind to the owner, and the ticket waits with that reason.

5. **Start what a merge released.** After each ticket is closed, run `D plan --root .` again and start every ticket now in `start` as in step 3.

6. **The report.** When no run is in progress and the plan starts nothing more, `D report --root .` writes `.ratchet/queue/<date>-<time>.md` and prints it. Print it to the owner whole, adding only the stash names of parked tickets. `git worktree list` then lists no queue work tree.

7. **The questions, then the route.** After the report, put the parked tickets' open questions to the owner as [Questions for the owner](#questions-for-the-owner) says; an answer written to a ticket releases it. When an answer released a ticket, say so and run the drain again from step 1 in the same turn: it builds only what was released. Otherwise the last lines are the route: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .` in one Bash call, its lines printed as they are.

## Questions for the owner

A question reaches the owner only when its answer shapes the product (`direction`) or cannot be taken back (`irreversible`), as ADR 0022 says. The spec role's result marks each one, with two to four options, the recommended one first, and a sentence on why. Never ask the owner to confirm what you would do anyway, and never ask whether to go on.

1. Ask through the question tool, one question at a time, in the words of a user of the product. The recommended option is first and its label ends with `(recommended)`; the description gives the reason.
2. Write each answer on the ticket under `## Answers`, the question and the answer in one line each. A local ticket: edit the file and commit it alone (`git commit -m "docs(tracker): the owner's answers on <ticket>" -- <file>`). A tracker ticket: `gh issue edit <n> --body-file <file>` with the full body, no label flag.
3. Record how the owner answered, one Bash call per answer: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/questions.py" record --skill feature --key "feature.<slug>.<n>" --kind <direction|irreversible> --recommended <yes|no> --root .`, where `<n>` is the question's place in the result. The log is `.ratchet/questions.tsv`, which no commit carries.

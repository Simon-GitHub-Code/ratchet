---
name: feature
description: Build features from agent-ready tracker tickets with the Ratchet pipeline. With a ticket it builds that one; with no ticket it builds every agent-ready ticket at once, each in a work tree of its own, merges what the merge judge passes and ends in one report. The preflight is a script and nothing is spawned while it is red.
disable-model-invocation: true
---

# Build features from tickets

`/ratchet:feature <ticket>` builds one ticket: a number on the tracker (`4` or `#4`) or the path of a local ticket file (`.scratch/<slug>/issues/02-<slug>.md`). `/ratchet:feature` with no ticket, or `/ratchet:feature all`, builds every agent-ready ticket: [the drain](#the-drain-every-agent-ready-ticket) below. Load the `ratchet:method` skill first; [pipeline.md](../method/pipeline.md) says what the four roles do and [records.md](../method/records.md) what the ticket carries.

The feature's input is the ticket the owner's `/mattpocock-skills:to-spec`, `to-tickets` or `triage` wrote, plus one section the method adds, `## Conjecture`, and one label the owner applies: the agent-ready label (`ready-for-agent` in mattpocock-skills, spelled as `docs/agents/triage-labels.md` maps it). The label is the owner's agreement; nothing below infers it.

## One ticket

1. **Preflight, before any agent exists.** One Bash call:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/feature_preflight.py" "<ticket>" --root . --base main
   ```

   It prints JSON (`ok`, `failures`, `number`, `title`, `slug`, `branch`, `label`) and exits 1 when red. Red: report `verdict: blocked` and, for each failure, its `check`, `detail` and `produce` (the skill or action that supplies the missing piece: `to-spec`, `to-tickets` or `triage` for the ticket, the owner for the Conjecture and the label, the blocking tickets for `blocked`, a commit or stash for the tree, the owner for `hooks`: the failure's `produce` is the one command that installs the commit hooks, which the guard refuses from an agent, so report it and do not run it). Spawn nothing, create nothing, label nothing: applying the agent-ready label is the owner's act and the guard asks on it. Stop.

2. **The branch.** Green: the preflight names `<branch>` (`feature/<number>-<slug>`). A rerun finds it already made, so check first with `git rev-parse --verify --quiet <branch>`: `git switch <branch>` when it exists, `git switch -c <branch> <base>` when it does not. Run the preflight once more with `--branch <branch>`; it must stay green.

3. **The pipeline.** Call the Workflow tool with the plugin's script and the arguments the preflight produced:

   ```
   scriptPath: "${CLAUDE_PLUGIN_ROOT}/workflows/pipeline.js"
   args: { "ref": "<ticket>", "branch": "<branch>", "base": "main", "preflight": "${CLAUDE_PLUGIN_ROOT}/scripts/feature_preflight.py" }
   ```

   (`name: "ratchet:pipeline"` where the harness lists plugin workflows by name.) The workflow runs the same preflight as its first stage, through one low-effort agent, so the pipeline cannot be entered around this skill; then Spec, Implement, Critique, the fix loop and Review. It never commits, pushes or opens a pull request. Stay in the turn until the workflow's result arrives: in a headless session (`claude -p`) the session ends with your turn and takes the running workflow with it, which killed two trial runs midway.

4. **The result.** Follow the orchestrator section of [pipeline.md](../method/pipeline.md): `verdict`, `unresolvedBlockingFindings`, the `checkOutput` tail, every dispute to the owner, then `ratchet` when it is bound (the raised thresholds go in the same commit; the orchestrator's step 3 in pipeline.md says why), commit `filesChanged`, write the verdict record (the result's `verdictRecord` on stdin of `just merge-ready record`) and commit it alone and last, push, and open the pull request with every section of the template filled, the Conjecture section listing each of the result's `assumptions` with its refuter, the Guard paths section naming each of the result's `dependencies` with its reason beside the versions in `dependencyNotes`, the Decisions section listing every addition in `recordNotes` (each glossary term, *Avoid* item and ADR the run added) (with no forge, the change description at `.scratch/changes/<branch>.md` instead, and the local ticket's `Status:` line set to `resolved` in the same commit: the local form of `Closes #N`, so merging the branch resolves the ticket; the description is committed before the verdict record, nothing is pushed, and `just merge <branch>` judges the branch and merges it or prints why the merge is the owner's). A `blocked` verdict with `guardEdits` lists the guard-path changes the ticket needs: the owner lands them on the default branch, or builds the ticket with `/pstack:poteto-mode` while present. A run that is not approved commits nothing, so clear what its agents left before you hand over: `git stash push -u -m "<branch>: <verdict> run"`, then `git switch <base>`. The rerun's preflight needs a clean tree, the route then reads the true state instead of a dirty branch, and the stash keeps the draft spec and tests for the owner to read; name it in the hand-over. The `blocked` stash's name is what `/ratchet:next` reads: it holds the ticket until the default branch moves past the run.

   A `blocked` verdict with `openQuestions` is the owner's to answer, and each one is a question about the product or about something that cannot be taken back: the spec role assumes everything else. After the stash, put them to the owner as [Questions for the owner](#questions-for-the-owner) says. When every question is answered, build the ticket again from step 1 in the same turn: the owner has answered, and asking them to type the command again would be a confirmation.

5. **Hand over.** When the change built a surface and the repository has `PRODUCT.md` but no `DESIGN.md`, say that an agent runs `impeccable document` to write it once this merges, in a session the owner attends, since the write of `DESIGN.md`, a guard path, asks the owner. It is not an owner keystroke. The last lines are the route: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .` in one Bash call and print its lines. With the pull request open they say the merge job decides. Say what the merge judge says now (`just merge-ready`: `merge: agent`, or `merge: owner` with its reasons), and never merge by hand. When the merge is the owner's, say also which of the owner's own passes the change warrants before they merge, from [route.md](../method/route.md) stage 8: `/pstack:interrogate` for a contested design, `/pstack:blast-radius` for a change with reach, `impeccable audit` or `polish` for a surface.

## The drain: every agent-ready ticket

`/ratchet:feature` or `/ratchet:feature all`, with `--concurrency N` when the owner wants another cap than three. The owner types it once and steps away. Each ticket is built by its own run of the pipeline in a git work tree of its own, the tickets that do not wait on each other at the same time. Each change that the pipeline approves is brought up to date, judged and merged one at a time. A ticket that cannot go on is parked and the others continue. Nothing below asks the owner anything until the report is printed.

`scripts/drain.py` decides the order, the cap, what a ticket's state is and what the report says; ADR 0024 says why. Run every command below from the repository's own checkout, on the default branch. `D` stands for `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/drain.py"`.

1. **Clear what an interrupted drain left.** `D clean --all --root .` removes every queue work tree and prints one line for each. One drain runs on a repository at a time, so a work tree found now is left over; what it held uncommitted is kept as a stash named `<branch>: interrupted run`, and its ticket is built again. A branch that already holds commits is not built again: step 4 asks the judge about it.

2. **The plan.** `D plan --root .` (with `--concurrency N` when given) prints JSON: `base`, `forge`, `blocked`, `start`, `agents_at_most`, and per ticket its `state`, `branch`, `worktree`, `why` and `waits_on`. When `blocked` is not empty the checkout is not fit to build on (uncommitted changes, another branch checked out, missing commit hooks): report each line, which ends with what produces the fix, and stop. Otherwise say in one or two sentences what the drain will do, and go on without asking: how many tickets it builds and how many at once, how many are refused or held and why (their `why` lines), and the cost, which is at most `agents_at_most` agents: nine for each ticket, and two more for each ticket that is judged again because another merged first.

3. **Start the pipelines.** For each ticket in `start`, `D open <ticket> --root .`. Exit 0 prints the ticket's `branch`, `worktree` and `base`; exit 1 means the plan does not start it, so do not. Then call the Workflow tool once for each ticket opened, all the calls in one message so that the runs are concurrent:

   ```
   scriptPath: "${CLAUDE_PLUGIN_ROOT}/workflows/pipeline.js"
   args: { "ref": "<ticket>", "branch": "<branch>", "base": "<base>", "root": "<worktree>", "preflight": "${CLAUDE_PLUGIN_ROOT}/scripts/feature_preflight.py" }
   ```

   Create no branch and no work tree by hand: `open` is the gate on the order and the cap, and a work tree it did not make is not in the plan. Stay in the turn until every run has returned.

4. **Settle each result, one ticket at a time.** Take the results in the order they arrive, and finish one ticket before the next: two changes that passed their gates apart can fail them together, so each is judged on top of what merged before it. A ticket the plan lists as `built` (an earlier drain committed it and it did not merge) has no run: go straight to step 4.6 for it. For the ticket's result, in its work tree:

   1. **Not approved.** A `verdict` other than `approve` parks the ticket. Write the result to a file under the session's scratch directory and run `D close <ticket> --outcome parked --result <file> --root .`: it removes the work tree, keeps what the agents left as the stash `<branch>: blocked run`, and holds the ticket until the ticket changes. Keep the result's `openQuestions` for step 6. Ask nothing now.
   2. **Commit the change.** `verdict` is `approve`: commit the result's `filesChanged` on the branch, in the work tree, with a message naming the slug.
   3. **Bring it up to date.** `D update <ticket> --root .` rebases the branch onto the default branch when another change merged since the branch was made. Exit 0 with `"moved": false`: nothing merged meanwhile, go to step 4.5. Exit 0 with `"moved": true`: run `check` in the work tree and note whether it is red. Exit 2: the rebase stopped on the `conflicts` it prints, and the branch is as it was. Exit 1: it prints why the branch cannot be moved; close the ticket with `--outcome waits --reason "<that line>"`.
   4. **Judge it again.** After a rebase or a conflict the first review no longer describes the change, so run the pipeline's rejudge:

      ```
      scriptPath: "${CLAUDE_PLUGIN_ROOT}/workflows/pipeline.js"
      args: { "ref": "<ticket>", "branch": "<branch>", "base": "<base>", "root": "<worktree>", "rejudge": { "slug": "<slug>", "specPath": "<specPath>", "conflicts": [<the conflicts>], "checkRed": <true when check was red>, "dependencies": <the first result's dependencies>, "assumptions": <the first result's assumptions> } }
      ```

      It runs one agent that finishes the rebase and gets `check` green again, only when there was a conflict or a red `check`, and then the reviewer on the change as it now stands. A result that is not `approve` parks the ticket as in step 4.1. Otherwise commit its `filesChanged`, when there are any, and use its `verdictRecord` in place of the first.
   5. **The records of the change.** As the orchestrator section of [pipeline.md](../method/pipeline.md) says, in the work tree: `ratchet` when it is bound, and the raised `quality/` files committed; the ADR, when the result suggests one, under the next free number; the pull request body, or with no forge the change description with the local ticket's `Status:` line set to `resolved`. Then the verdict record from the latest result, committed alone and last.
   6. **Land it.** With no forge: `D land <ticket> --root .` runs the binding's `merge` entry point, which asks the merge judge, runs `check` on the merge result and merges or does not. It prints the judge's lines, removes the work tree and records the outcome: merged, or waiting for the owner with every reason. Exit 2 means it could not ask; report what it printed and leave the ticket. With a forge: push the branch, open the pull request with every section of the template filled, and watch its checks (`gh pr checks <n> --watch`). The repository's merge job merges it when the judge passes it. Merged: `git pull --ff-only` on the default branch, then `D close <ticket> --outcome merged --root .`. Not merged: `D close <ticket> --outcome waits --root .` with one `--reason` for each line of the judge's comment on the pull request.
   7. **Start what the merge released.** After each ticket is closed, run `D plan --root .` again and start every ticket now in `start` as in step 3: a ticket that waited on the one just merged, or one the cap held back.

   Never merge by hand, never rebase by hand and never answer a question for the owner. A dispute in a result is the owner's: it parks the ticket with the dispute as a reason.

5. **The report.** When no run is in progress and the plan starts nothing more, `D report --root .` writes `.ratchet/queue/<date>-<time>.md` and prints it. Print it to the owner whole: what merged, what waits for them and why, what is parked and why, what was not built, every assumption with its refuter, every glossary term and ADR the changes added, and the time each ticket took. Check `git worktree list`: it lists no queue work tree. Add nothing the report does not say, except the stash names of parked tickets.

6. **The questions, then the route.** The report is the first thing the owner reads. After it, put the open questions of the parked tickets to the owner as [Questions for the owner](#questions-for-the-owner) says. An answer written to a ticket releases it. When the answers released at least one ticket, run the drain again from step 1 in the same turn, and say that you are doing so: it builds only what was released. Otherwise the last lines are the route: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .` in one Bash call, its lines printed as they are.

## Questions for the owner

A question reaches the owner only when its answer shapes the product (`direction`) or cannot be taken back (`irreversible`). The spec role's result marks each one, with two to four options, the recommended one first, and a sentence on why. Never ask the owner to confirm what you would do anyway, and never ask whether to go on.

1. Ask through the question tool, one question at a time. The recommended option is first and its label ends with `(recommended)`; the description under the question gives the reason. Keep the question in the words of a user of the product.
2. Write each answer on the ticket under `## Answers`, as the question and the answer in one line each, so the next spec agent reads a decision and not a discussion. A local ticket: edit the file and commit it alone (`git commit -m "docs(tracker): the owner's answers on <ticket>" -- <file>`). A ticket on the tracker: `gh issue edit <n> --body-file <file>` with the full body, no label flag.
3. Record how the owner answered, one Bash call per answer:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/questions.py" record --skill feature --key "feature.<slug>.<n>" --kind <direction|irreversible> --recommended <yes|no> --root .
   ```

   `<n>` is the question's place in the result. The log is `.ratchet/questions.tsv`, which no commit carries.

## Why the skill reads the way it does

- **The preflight is a script, not an agent.** Issue 6: the workflow's slash form passes no arguments, and its preconditions were checked by the spec agent after it had spawned. A script decides before the first agent exists, and the workflow's first stage runs the same script, so both entrances agree.
- **The ticket is mattpocock-skills' and the Conjecture is the method's.** Issue 4: a second feature-brief format duplicated `to-spec`, `to-tickets` and `triage`. The method adds the one section those skills lack, the conjecture with its refuter and the alternatives killed, and reads everything else as those skills wrote it.
- **The label is the agreement.** Issue 5: "the owner has agreed the seams" was assumed. The agent-ready label is a thing the owner does, the preflight refuses a ticket without it, and the guard asks when an agent tries to apply it.
- **The drain's order is a script's exit code.** ADR 0024: an agent that decides which ticket may start, or that a branch is current enough to merge, decides it differently on a long night. `drain.py open` refuses a ticket the plan does not start, `update` leaves a conflicting branch as it was, and `land` closes a ticket with the judge's own lines.
- **A question is asked after the work, not during it.** ADR 0023: one parked ticket must not stop its siblings, and the owner answers better with the report in front of them. What they are asked is what only they can decide.

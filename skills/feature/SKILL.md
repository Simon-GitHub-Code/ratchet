---
name: feature
description: Build features from agent-ready tracker tickets with the Ratchet pipeline. With a ticket it builds that one; with no ticket it builds every agent-ready ticket at once, each in a work tree of its own, merges what the merge judge passes and ends in one report. The preflight is a script and nothing is spawned while it is red.
disable-model-invocation: true
---

# Build features from tickets

`/ratchet:feature <ticket>` builds one ticket: a number on the tracker (`4` or `#4`) or the path of a local ticket file (`.scratch/<slug>/issues/02-<slug>.md`). `/ratchet:feature` with no ticket, or `/ratchet:feature all`, builds every agent-ready ticket: [the drain](#the-drain-every-agent-ready-ticket). Load the `ratchet:method` skill first; [pipeline.md](../method/pipeline.md) says what the roles do (spec, the ticket check, implement and review always; the critic and its fix loop once the project is depended-on) and [records.md](../method/records.md) what the ticket carries.

The input is the ticket the owner's `/mattpocock-skills:to-spec`, `to-tickets` or `triage` wrote, with the method's `## Conjecture` and `## Operability` sections, and the agent-ready label the owner applies (`ready-for-agent` in mattpocock-skills, spelled as `docs/agents/triage-labels.md` maps it). The label is the owner's agreement; nothing below infers it or applies it (ADR 0008).

Stay in the turn until every workflow you start has returned: in a headless session (`claude -p`) the session ends with your turn and takes a running workflow with it.

## One ticket

1. **Preflight, before any agent exists.** One Bash call: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/feature_preflight.py" "<ticket>" --root .`. It prints JSON (`ok`, `failures`, `number`, `title`, `slug`, `branch`, `base`, `label`, `critic`, `critic_reason`) and exits 1 when red; `base` is the default branch, read from the checkout and the binding. Red on `size` alone (the ticket's own `## Size` passes a product ceiling): split it as [Too large](#too-large-the-agent-splits-it) says, then build the first part that is free. Otherwise red: report `verdict: blocked` and, for each failure, its `check`, `detail` and `produce`, the skill or act that supplies the missing piece. A `hooks` failure whose `produce` does not start `the owner:` is yours to repair: run the command it names in one Bash call, then the preflight again. One that does is a change of git config, which the guard refuses from an agent: report it, do not run it. Spawn nothing, create nothing, label nothing. Stop.

2. **The branch.** Green: `git switch <branch>` when `git rev-parse --verify --quiet <branch>` finds it, else `git switch -c <branch> <base>`. Run the preflight again with `--branch <branch>`; it must stay green.

3. **The pipeline.** Call the Workflow tool (`name: "ratchet:pipeline"` where the harness lists plugin workflows by name):

   ```
   scriptPath: "${CLAUDE_PLUGIN_ROOT}/workflows/pipeline.js"
   args: { "ref": "<ticket>", "branch": "<branch>", "base": "<base>", "preflight": "${CLAUDE_PLUGIN_ROOT}/scripts/feature_preflight.py" }
   ```

   It runs the same preflight as its first stage, which is where it reads `critic` (the arguments carry only the script's path): Spec, the ticket check (with one amend of the spec when it finds a gap; a promise the plan still leaves out is built as the ticket says it, and an input case still undecided with the safe answer, each in the result's `assumptions`, and neither asks the owner anything), Implement, then Critique and the fix loop only when `critic` is true (the binding declares the `depended-on` life event, ADR 0028), then Review. When it did not run the critic, the result's `critic` says so and why; put that line in the pull request. It never commits, pushes or opens a pull request.

   When the run errors instead of returning (an agent died), resume it once; if it errors again, tell the owner which stage died and stop: the drain closes such a ticket for a retry, a single ticket has no queue to retry it.

4. **A result that is not approved.** A `verdict` other than `approve`, an open `unresolvedBlockingFindings` or a red `checkOutput` tail commits nothing. Clear what the agents left with `git stash push -u -m "<branch>: <verdict> run"`, then `git switch <base>`, and name the stash in the hand-over; a `blocked` stash holds the ticket in the route until the default branch moves past the run. Then:
   - `openQuestions`: ask each as `feature.open-question`, as [Questions for the owner](#questions-for-the-owner) says. When every one is answered and there is no guard edit, build the ticket again from step 1 in the same turn.
   - `guardEdits`: list them. The owner lands them on the default branch, or builds the ticket with `/pstack:poteto-mode` while present.
   - `tooLarge`: split it yourself and go on, as [Too large](#too-large-the-agent-splits-it) says. Splitting by size is no product decision, so the owner is told afterwards, in the hand-over, and asked nothing.
   - A dispute: relay it to the owner.
   - `ticketFindings`: the build lost a promise of the ticket, or left a case of an input undecided, that the plan had kept. The owner has nothing to decide and the ticket nothing to change. Build the ticket again once, from step 1 in the same turn, with `"ticketFindings": <the result's ticketFindings>` added to the pipeline's args, so the spec role writes the next spec with them in hand. If that run is refused for the same reason, report the findings to the owner.
   - A `triggers:` red line in `checkOutput`: the ticket adds another instance of a kind whose safeguard is owed (ADR 0028), or whose architecture review is owed (`triggers: architecture review owed:`, ADR 0052). Tell the owner to type `/ratchet:safeguard`, or `/ratchet:architecture` for a review, then build the ticket again; nothing in the ticket needs to change.

5. **Commit an approved change,** in this order ([pipeline.md](../method/pipeline.md), the orchestrator). Run `ratchet` when it is bound, and commit everything the work tree holds (`git add -A`: the roles' `filesChanged` can miss the spec and a record they added) with the raised `quality/` files, the message naming the slug. Write the ADR the result suggests under the next free number. With no forge, write the change description at `.scratch/changes/<branch>.md` and set the local ticket's `Status:` line to `resolved` in the same commit. Last and alone, the verdict record: the result's `verdictRecord` on stdin of `just merge-ready record`, its file committed.

6. **Publish.** With a forge: push and open the pull request with every section of the template filled. Its Conjecture section lists each of the result's `assumptions` with its refuter, Guard paths each of its `dependencies` with its reason beside the versions in `dependencyNotes`, and Decisions every addition in `recordNotes`, a new term by its name only: a glossary's _Avoid_ words quoted there fail the prose gate. The `merge` workflow merges it once `ci` is green and the judge passes it. With no forge: `just merge <branch>` judges the branch, runs `check` on the merge result and merges it, or prints why the merge is the owner's.

7. **Hand over.** List the result's `assumptions` under **Assumed**, one plain line each with what changing it later costs and what the owner can say to change it, as the method's rule on asking describes. Say what the merge judge says (`just merge-ready`: `merge: agent`, or `merge: owner` with its reasons), and never merge by hand. When the merge is the owner's, name the passes the change warrants before they merge, from [route.md](../method/route.md) stage 8. When the change built a surface and the repository has `PRODUCT.md` but no `DESIGN.md`, say that an agent runs `impeccable document` on a branch once this merges, and that the owner merges that change, since `DESIGN.md` is a guard path. The last lines are the route: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .` in one Bash call and print its lines.

## The drain: every agent-ready ticket

`/ratchet:feature` or `/ratchet:feature all`, with `--concurrency N` when the owner wants another cap than three. The owner types it once and steps away. Each ticket is built by its own pipeline in a git work tree of its own, the tickets that do not wait on each other at the same time; each approved change is brought up to date, judged and merged one at a time; a ticket that cannot go on is parked and the others continue. Nothing asks the owner anything until the report is printed. Never merge by hand, never rebase by hand, never answer a question for the owner.

`scripts/drain.py` decides the order, the cap, each ticket's state and the report (ADR 0023). Run every command below from the repository's own checkout, on the default branch. `D` stands for `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/drain.py"`.

1. **Clear what an interrupted drain left.** `D clean --all --root .` removes every queue work tree, keeps what one held uncommitted as the stash `<branch>: interrupted run`, and prints a line for each. A work tree whose uncommitted work git cannot stash stays, with a `left:` line saying why. It prints this drain's token on stderr (`drain: <token>`): keep it, and pass `--drain <token>` to `clean --all` and to `report` when you resume this drain. Exit 2 with `another drain is running`: another session's drain holds the repository, so stop and report it; `--take-over` is for the owner to say, when that drain is gone for good.

2. **The plan.** `D plan --root .` (with `--concurrency N` when given) prints JSON: `base`, `forge`, `blocked`, `start`, `agents_at_most`, and per ticket its `state`, `branch`, `worktree`, `why` and `waits_on`. When `blocked` is not empty the checkout is not fit to build on, and each buildable ticket's `why` says so: a `hooks:` line that does not end in an `the owner:` command is yours to repair as in the single-ticket preflight, then plan again; otherwise report each line, which ends with what fixes it, and stop. Otherwise say in one or two sentences what the drain will do and go on without asking: how many tickets it builds and how many at once, which are refused or held and why, and the cost, at most `agents_at_most` agents.

3. **Start the pipelines.** For each ticket in `start`, `D open <ticket> --root .`: exit 0 prints its `branch`, `worktree` and `base`; exit 1 means the plan does not start it. Then call the Workflow tool once per ticket opened, every call in one message so the runs are concurrent:

   ```
   scriptPath: "${CLAUDE_PLUGIN_ROOT}/workflows/pipeline.js"
   args: { "ref": "<ticket>", "branch": "<branch>", "base": "<base>", "root": "<worktree>", "preflight": "${CLAUDE_PLUGIN_ROOT}/scripts/feature_preflight.py" }
   ```

   Create no branch and no work tree by hand: `open` is the gate on the order and the cap.

   **A run that errors.** An agent that dies ends the run in a thrown error ("resume the run to retry") and no result (ADR 0023). Resume that run once, the same Workflow call with its resume. If it errors again, the ticket is not parked and the drain does not stop: `D close <ticket> --outcome waits --reason "errored: the pipeline run errored twice: <the error's first line>" --root .`. The code `errored` is one the drain resolves itself like `behind`, so the ticket is built again by the next drain, from the start, and only after it has errored `RETRIES` times in a row (three) is it held for the owner, with a reason that says the run kept erroring (ADR 0055). A run that returns a result, even a refused one, never takes this path. The same holds for the rejudge of step 4.4: resume it once, then close the ticket the same way.

4. **Settle each result, one ticket at a time,** in the order they arrive, finishing one before the next: two changes that passed apart can fail together. Each sub-step runs in the ticket's work tree.

   1. **Not approved.** A result with `tooLarge` is split, not parked: [Too large](#too-large-the-agent-splits-it); the parts join the plan and the drain goes on. Any other `verdict` than `approve` parks the ticket: write the result to a file made with `mktemp` and run `D close <ticket> --outcome parked --result <file> --root .`. It removes the work tree, keeps what the agents left as the stash `<branch>: blocked run`, and holds the ticket until the ticket changes. A `triggers:` red in the result's `checkOutput` adds a reason that names `/ratchet:safeguard`, or `/ratchet:architecture` for an owed review, and that ticket is held only until the safeguard is bound or the review has landed. Keep the result's `openQuestions` for step 7.
   2. **Commit.** `approve`: commit everything the work tree holds (`git add -A`) with the subject `<type>(<scope>): <the slug, hyphens as spaces>`, the type `feat` for a ticket that adds behaviour and `fix` for one that repairs it. The subject is at most 72 characters (tier 0 refuses more): when it is longer, drop whole words from the end of the slug until it fits, never cutting a word or leaving a trailing `and`, `a`, `the`, `of` or `to`, and put the ticket's full title and `Closes <ref>` in the body.
   3. **Bring it up to date.** `D update <ticket> --root .`. Exit 0 with `"moved": false`: go to step 4.5. Exit 0 with `"moved": true`: run `check` and note whether it is red. Exit 2: the rebase stopped on the `conflicts` it prints, and the branch is as it was. Exit 1: close the ticket with `--outcome waits --reason "<the line it printed>"`.
   4. **Judge it again.** After a rebase or a conflict the first review no longer describes the change:

      ```
      scriptPath: "${CLAUDE_PLUGIN_ROOT}/workflows/pipeline.js"
      args: { "ref": "<ticket>", "branch": "<branch>", "base": "<base>", "root": "<worktree>", "rejudge": { "slug": "<slug>", "specPath": "<specPath>", "conflicts": [<the conflicts>], "checkRed": <true when check was red>, "dependencies": <the first result's dependencies>, "assumptions": <the first result's assumptions>, "settled": <the first result's settled> } }
      ```

      One agent finishes the rebase and gets `check` green, only when there was a conflict or a red `check`; then the reviewer judges the change as it stands, held to the readings the first run's ticket check settled (`settled`), so a case the first run assumed is not read as open again. Not `approve`: park as in step 4.1. Otherwise commit its `filesChanged`, if any, and use its `verdictRecord` in place of the first.
   5. **The records.** As step 5 of the one-ticket path: `ratchet` and the raised `quality/` files, the suggested ADR, with no forge the change description and the local ticket's `Status:` line, then the verdict record from the latest result, alone and last.
   6. **Land it.** With no forge: `D land <ticket> --root .` runs the binding's `merge` entry point, prints the judge's lines, removes the work tree and records merged or waiting with every reason; exit 2 means it could not ask, so report what it printed and leave the ticket. With a forge: push, open the pull request as step 6 of the one-ticket path says, and wait for both workflows: `gh pr checks <n> --watch` for `ci`, then the `merge` run that follows it (`gh run list --workflow merge.yml --limit 1`, then `gh run watch <id>`). When `gh pr view <n> --json state` says `MERGED`, run `git pull --ff-only` on the default branch and `D close <ticket> --outcome merged --root .`. Otherwise `D close <ticket> --outcome waits --root .`, with one `--reason` per line of the judge's comment on the pull request.

   A ticket the plan lists as `built` was committed by an earlier drain and has no run: start it at step 4.3 when its `why` says it is behind the default branch (`update` makes its work tree again, rebases it and drops the verdict record the move outdated; then step 4.4 judges it again and 4.5 writes the new record), otherwise at step 4.6. The merge refuses a branch behind the default branch with the `behind` reason, so no verdict lands on a tree it was not given on. A ticket the plan lists as `held` was parked or left to the owner for a decision and is not landed again until its text changes or a path its run named as the cause changes on the default branch. A ticket that waited only for a reason the drain resolves itself (`behind`, `moved`, `conflict`, `gates`, `refs`, `errored`) stays `built` and is retried, at most three times in a row; then it is held with a reason that says so.

5. **Start what a merge released.** After each ticket is closed, run `D plan --root .` again and start every ticket now in `start` as in step 3.

6. **The report.** When no run is in progress and the plan starts nothing more, `D report --drain <token> --root .` writes `.ratchet/queue/<date>-<time>.md` and prints it. Print it to the owner whole, adding only the stash names of parked tickets. `git worktree list` then lists no queue work tree.

7. **The questions, then the route.** After the report, ask every parked ticket's open questions as `feature.open-question`, all in one message, as [Questions for the owner](#questions-for-the-owner) says; an answer written to a ticket releases it. When an answer released a ticket, say so and run the drain again from step 1 in the same turn: it builds only what was released. Otherwise the last lines are the route: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .` in one Bash call, its lines printed as they are.

## Too large: the agent splits it

A ticket that passes the product ceilings (the preflight's `size` failure, the spec stage's estimate, or the measured stop; ADR 0045) is split by size by you, never by the owner and never with a question (ADR 0046). The parts are tickets in the project's tracker, so they are built like any other.

1. **The plan.** Write a JSON file made with `mktemp`: `{"parts": [{"title": "...", "summary": "...", "promises": ["..."]}]}`. Start from `tooLarge.proposedSplit` (each part's `title` and `promises`; `summary` from its `criteria`) or, with none, divide the ticket yourself so each part is a slice a person could use and fits both ceilings. A part's `promises` are the ticket's own `- [ ]` lines, copied verbatim without the checkbox. Every such line of the ticket goes in exactly one part; add none, drop none, reword none. A part waits on the one before, unless a part gives `after` as a list of part indexes.
2. **File it.** In the repository's own checkout: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/drain.py" split <ticket> --plan <file> --root .`. It checks the plan, files the parts with the agent-ready label the parent holds now, closes the parent as `split` with its text unedited, and prints the parts. Exit 1 means the plan was refused, naming the line: fix the plan and run it again, once. A ticket that is itself a part is not split again: it is parked as too large and the report names it, which is the owner's.
3. **Go on.** The parts are tickets the plan now lists, so the drain builds them in order. The hand-over says the ticket was split, into what, and why.

## Questions for the owner

A question reaches the owner only when it is a product question: its answer shapes the product (`direction`) or cannot be taken back (`irreversible`), as ADRs 0022 and 0034 say. The spec role's result marks each one, with two to four options, the recommended one first, and a sentence on why. Never ask the owner to confirm what you would do anyway, and never ask whether to go on.

1. **Ask** `feature.open-question`: "<the question the build stopped on, in the words of someone using the product>" All of them in one message, each as the spec role wrote it, so the owner can accept them all at once. The recommended option is first and its label ends with `(recommended)`; the description gives the reason.
2. Write each answer on the ticket under `## Answers`, the question and the answer in one line each. A local ticket: write them at the end of the file and land it from the default branch: with no forge `just merge --records` (the `merge` entry point) commits it on a branch of its own, and with a forge it goes on a branch of its own and through a pull request. Never commit on the default branch itself. A labelled ticket changes only by the owner's merge (ADR 0031), so the run prints `merge: owner` and the owner's command; relay both as they are. A tracker ticket: `gh issue edit <n> --body-file <file>` with the full body, no label flag.
3. Record how the owner answered, one Bash call per answer: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/questions.py" record --skill feature --key "feature.<slug>.<n>" --kind <direction|irreversible> --recommended <yes|no> --root .`, where `<n>` is the question's place in the result. The log is `.ratchet/questions.tsv`, which no commit carries.

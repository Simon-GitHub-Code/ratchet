---
name: feature
description: Build agent-ready tickets with the Ratchet pipeline, the one named or every one at once in work trees of their own, merged as the merge judge passes each and ending in one report. A preflight script runs first, and nothing is spawned while it is red.
disable-model-invocation: true
---

# Build features from tickets

Load the `ratchet:method` skill first. [pipeline.md](../method/pipeline.md) says what the roles do, and [records.md](../method/records.md) what the ticket carries.

Pick the mode from what the owner typed:

- **A ticket**, `/ratchet:feature <ticket>`: a number on the tracker (`4` or `#4`) or the path of a local ticket file (`.scratch/<slug>/issues/02-<slug>.md`). Follow [One ticket](#one-ticket).
- **No ticket**, `/ratchet:feature` or `/ratchet:feature all`: build every agent-ready ticket. Follow [the drain](#the-drain-every-agent-ready-ticket).

## Both modes

- **The input.** A ticket the owner's `/mattpocock-skills:to-spec`, `to-tickets` or `triage` wrote, with the method's `## Conjecture` and `## Operability` sections. It carries the agent-ready label the owner applies (`ready-for-agent` in mattpocock-skills, spelled as `docs/agents/triage-labels.md` maps it). The label is the owner's agreement: nothing here infers it or applies it.
- **The Workflow call.** Call the Workflow tool by the workflow's name, `ratchet:pipeline`, in every Workflow call on this page. A harness that lists no plugin workflow by name takes `scriptPath: "${CLAUDE_PLUGIN_ROOT}/workflows/pipeline.js"` instead, once you have read that file: a fresh install refuses a script path the session has not read.
- **The turn.** Stay in the turn until every workflow you start has returned. In a headless session (`claude -p`) the session ends with your turn and takes a running workflow with it.

## One ticket

1. **Preflight, before any agent exists.**
   - Run, in one Bash call: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/feature_preflight.py" "<ticket>" --root .`.
   - It prints JSON (`ok`, `failures`, `number`, `title`, `slug`, `branch`, `base`, `label`, `critic`, `critic_reason`) and exits 1 when red. `base` is the default branch, read from the checkout and the binding.
   - **Red on `size` alone** (the ticket's own `## Size` passes a product ceiling): split it as [Too large](#too-large-the-agent-splits-it) says, then build the first part that is free.
   - **A `hooks` failure whose `produce` does not start `the owner:`** is yours to repair. Run the command it names in one Bash call, then the preflight again.
   - **A `hooks` failure whose `produce` starts `the owner:`** is a change of git config, which the guard refuses from an agent. Report it with the rest below; do not run it.
   - **Still red after your own repair,** on anything but `size` alone, an owner's `hooks` failure included: report `verdict: blocked` and, for each failure, its `check`, `detail` and `produce` (the skill or act that supplies the missing piece). Spawn nothing, create nothing, label nothing. Stop.

   Done when the preflight is green, or you have reported every failure and stopped.

2. **The branch.**
   - When `git rev-parse --verify --quiet <branch>` finds the branch, `git switch <branch>`. Otherwise `git switch -c <branch> <base>`.
   - Run the preflight again with `--branch <branch>`, its report written to a file made with `mktemp`, outside the work tree. It must stay green. The report also holds `start`, the state of the branch and the forge that the publish check of step 3 compares with.
   - **Red with `--branch`:** report `verdict: blocked` and, for each failure, its `check`, `detail` and `produce`. Spawn nothing. Stop.

   Done when you are on the branch and the preflight with `--branch` is green, or you have reported every failure and stopped.

3. **The pipeline.** Call the Workflow tool:

   ```
   name: "ratchet:pipeline"
   args: { "ref": "<ticket>", "branch": "<branch>", "base": "<base>", "preflight": "${CLAUDE_PLUGIN_ROOT}/scripts/feature_preflight.py" }
   ```

   The run is the one [pipeline.md](../method/pipeline.md) describes. It never commits, pushes or opens a pull request. The critic runs only when `critic` is true (the binding declares the `depended-on` life event).

   **The publish check, each time the run returns or errors,** before anything is committed and before a resume: write the run's result, as JSON, to `<the report file of step 2>.result.json`, then run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/publish_check.py" <the report file of step 2> --root .` in one Bash call. A run that errored has no result: write none and add `--before-resume`.
   - **Exit 0:** nothing moved. Go on.
   - **Exit 1:** a role committed, pushed or opened a pull request, or you committed before this check, or a finding the run treated as owed quotes what its record does not hold. Run `git stash push -u -m "<branch>: blocked run"`, then `git switch <base>`. Tell the owner what the check printed, and stop. Never commit on that branch.
   - **Exit 2 or 3:** the start, the result or the present state cannot be read, and none of them is a pass. Report what it printed and stop.

   **The run errors instead of returning** (an agent died): resume it once. If it errors again, tell the owner which stage died and stop.

   Done when the workflow has returned a result and the publish check is green, or you have stopped as the check or a second error says.

4. **A result that is not approved.** A `verdict` other than `approve`, an open `unresolvedBlockingFindings` or a red `checkOutput` tail commits nothing. An approved result goes to step 5.
   - Clear what the agents left with `git stash push -u -m "<branch>: <verdict> run"`, then `git switch <base>`.
   - Name the stash in the hand-over. A `blocked` stash holds the ticket in the route until the default branch moves past the run.

   Then, for each of these the result holds:
   - **`openQuestions`:** ask each as `feature.open-question`, as [Questions for the owner](#questions-for-the-owner) says. When every one is answered and there is no guard edit, build the ticket again from step 1 in the same turn.
   - **`guardEdits`:** list them. The owner lands them on the default branch, or builds the ticket with `/pstack:poteto-mode` while present.
   - **`tooLarge`:** split it yourself and go on, as [Too large](#too-large-the-agent-splits-it) says. Splitting by size is no product decision, so the owner is asked nothing and is told afterwards, in the hand-over.
   - **A dispute:** relay it to the owner.
   - **`ticketFindings`:** the build lost a promise of the ticket, or left a case of an input undecided, that the plan had kept. The owner has nothing to decide and the ticket nothing to change. Build the ticket again once, from step 1 in the same turn, with `"ticketFindings": <the result's ticketFindings>` added to the pipeline's args, so the spec role writes the next spec with them in hand. If that run is refused for the same reason, report the findings to the owner.
   - **A `triggers:` red line in `checkOutput`:** the ticket adds another instance of a kind whose safeguard is owed, or whose architecture review is owed (`triggers: architecture review owed:`). Tell the owner to type `/ratchet:safeguard`, or `/ratchet:architecture` for a review, then build the ticket again. Nothing in the ticket needs to change.

   Done when the stash is made, you are on `<base>`, and every field above that the result holds has had its case done.

5. **Commit an approved change,** in the order [The records of an approved change](#the-records-of-an-approved-change) gives, with this result's records.

   Done when the verdict record is the branch's last commit and holds nothing else.

6. **Publish.**
   - **With a forge:** push, and open the pull request as [The pull request](#the-pull-request) says. The `merge` workflow merges it once `ci` is green and the judge passes it.
   - **With no forge:** `just merge <branch>` judges the branch, runs `check` on the merge result and merges it, or prints why the merge is the owner's.

   Done when the pull request is open with every section filled, or `just merge` has printed its lines.

7. **Hand over.**
   - List the result's `assumptions` under **Assumed**, one plain line each with what changing it later costs and what the owner can say to change it, as the method's rule on asking describes.
   - Say what the merge judge says (`just merge-ready`: `merge: agent`, or `merge: owner` with its reasons). The judge decides the merge: never merge by hand.
   - When the merge is the owner's, name the passes the change warrants before they merge, from [route.md](../method/route.md) stage 8.
   - When the change built a surface and the repository has `PRODUCT.md` but no `DESIGN.md`, say that an agent runs `impeccable document` on a branch once this merges, and that the owner merges that change, since `DESIGN.md` is a guard path.
   - The last lines are the route: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .` in one Bash call and print its lines.

   Done when the hand-over holds each line above that applies and ends with the route's lines.

## The drain: every agent-ready ticket

`/ratchet:feature` or `/ratchet:feature all`, with `--concurrency N` when the owner wants another cap than three. The owner types it once and steps away, so nothing asks the owner anything until the report is printed.

- `scripts/drain.py` decides the order, the cap, each ticket's state and the report.
- The merge judge merges and `D update` rebases: never merge by hand and never rebase by hand. Open questions go to the owner at step 7: never answer a question for the owner.

Run every command below from the repository's own checkout, on the default branch. `D` stands for `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/drain.py"`.

1. **Clear what an interrupted drain left.**
   - Run `D clean --all --root .`. It removes every queue work tree, keeps what one held uncommitted as the stash `<branch>: interrupted run`, and prints a line for each.
   - A work tree whose uncommitted work git cannot stash stays, with a `left:` line saying why.
   - It prints this drain's token on stderr (`drain: <token>`). Keep it. When you resume this drain, pass `--drain <token>` to `clean --all` and to `report`.
   - **Exit 2 with `another drain is running`:** another session's drain holds the repository. Stop and report it. `--take-over` is for the owner to say, when that drain is gone for good.

   Done when you hold the token, or you have reported the other drain and stopped.

2. **The plan.**
   - Run `D plan --root .`, with `--concurrency N` when given. It prints JSON: `base`, `forge`, `blocked`, `start`, `agents_at_most`, and per ticket its `state`, `branch`, `worktree`, `why` and `waits_on`.
   - **`blocked` is not empty:** the checkout is not fit to build on, and each buildable ticket's `why` says so. A `hooks:` line that does not end in an `the owner:` command is yours to repair: run the command it names in one Bash call, then plan again. Otherwise report each line, which ends with what fixes it, and stop.
   - **`blocked` is empty:** say in one or two sentences what the drain will do, and go on without asking. Say how many tickets it builds and how many at once, which are refused or held and why, and the cost: at most `agents_at_most` agents.

   Done when `blocked` is empty and you have said what the drain will do, or you have reported each line and stopped.

3. **Start the pipelines.**
   - For each ticket in `start`, run `D open <ticket> --root .`. Exit 0 prints its `branch`, `worktree` and `base`. Exit 1 means the plan does not start it.
   - `open` is the gate on the order and the cap, so make every branch and work tree through it, none by hand.
   - Call the Workflow tool once per ticket opened, every call in one message so the runs are concurrent:

   ```
   name: "ratchet:pipeline"
   args: { "ref": "<ticket>", "branch": "<branch>", "base": "<base>", "root": "<worktree>", "preflight": "${CLAUDE_PLUGIN_ROOT}/scripts/feature_preflight.py" }
   ```

   **A run that errors.** An agent that dies ends the run in a thrown error ("resume the run to retry") and no result.
   - First run `D check <ticket> --root .`, with no `--final`, since a role can still commit in the resume. On any exit but 0, settle it as [The publish check](#the-publish-check) says, and do not resume.
   - Resume that run once: the same Workflow call with its resume.
   - If it errors again, the ticket is not parked and the drain does not stop. Run `D close <ticket> --outcome waits --reason "errored: the pipeline run errored twice: <the error's first line>" --root .`.
   - The next drain builds the ticket again, from the start.
   - A run that returns a result, even a refused one, never takes this path.
   - The same holds for the rejudge of step 4.4: resume it once, then close the ticket the same way.

   Done when every ticket in `start` is opened and its run started, or `open` refused it.

4. **Settle each result, one ticket at a time,** in the order they arrive, finishing one before the next: two changes that passed apart can fail together. Each sub-step runs in the ticket's work tree.

   1. **Not approved.**
      - **A result with `tooLarge`** is split as [Too large](#too-large-the-agent-splits-it) says, or parked there when its split is refused. The parts join the plan and the drain goes on.
      - **Any other `verdict` than `approve`** parks the ticket. Write the result to a file made with `mktemp` and run `D close <ticket> --outcome parked --result <file> --root .`. It removes the work tree, keeps what the agents left as the stash `<branch>: blocked run`, and holds the ticket until the ticket changes.
      - A `triggers:` red in the result's `checkOutput` adds a reason that names `/ratchet:safeguard`, or `/ratchet:architecture` for an owed review. That ticket is held only until the safeguard is bound or the review has landed.
      - Keep the result's `openQuestions` for step 7.
   2. **Commit.** On `approve`:
      - First run `D check <ticket> --final --root .`. On any exit but 0, settle it as [The publish check](#the-publish-check) says, and commit nothing.
      - Commit everything the work tree holds (`git add -A`) with the subject `<type>(<scope>): <the slug, hyphens as spaces>`. The type is `feat` for a ticket that adds behaviour and `fix` for one that repairs it.
      - The subject is at most 72 characters (tier 0 refuses more). When it is longer, drop whole words from the end of the slug until it fits, never cutting a word or leaving a trailing `and`, `a`, `the`, `of` or `to`, and put the ticket's full title in the body.
      - The body holds `Closes <ref>`, whether or not the subject was shortened.
   3. **Bring it up to date.** Run `D update <ticket> --root .`. When it prints JSON, the JSON decides, whatever `drain:` or `merge:` line came before it.
      - **Exit 0 with `"moved": false`:** go to step 4.5.
      - **Exit 0 with `"moved": true`:** run `check` and note whether it is red. Then step 4.4.
      - **Exit 2 with JSON:** the rebase stopped on the `conflicts` it prints, and the branch is as it was. Then step 4.4.
      - **Exit 1 with `update: <ref> is merged and is not rebased`:** the ticket has landed. Do not close it. Go on at step 5 (`D plan`); the report lists it under Merged.
      - **Any other exit 1** (`update: <ref> is not rebased: <why>`): close the ticket with `--outcome waits --reason "<the line it printed>"`.
   4. **Judge it again.** After a rebase or a conflict the first review no longer describes the change:

      ```
      name: "ratchet:pipeline"
      args: { "ref": "<ticket>", "branch": "<branch>", "base": "<base>", "root": "<worktree>", "rejudge": { "slug": "<slug>", "specPath": "<specPath>", "conflicts": [<the conflicts>], "checkRed": <true when check was red>, "dependencies": <the first result's dependencies>, "assumptions": <the first result's assumptions>, "settled": <the first result's settled> } }
      ```

      - One agent finishes the rebase and gets `check` green, only when there was a conflict or a red `check`.
      - Then the reviewer judges the change as it stands, held to the readings the first run's ticket check settled (`settled`), so a case the first run assumed is not read as open again.
      - **Not `approve`:** park as in step 4.1.
      - **Otherwise:** run `D check <ticket> --final --root .` first, since the rebase recorded a new start that `land` refuses until a final check passes it. On any exit but 0, settle it as [The publish check](#the-publish-check) says. On 0, commit its `filesChanged`, if any, and use its `verdictRecord` in place of the first.
   5. **The records.** Make them as [The records of an approved change](#the-records-of-an-approved-change) says, with the verdict record from the latest result.
   6. **Land it.**
      - **With no forge:** `D land <ticket> --root .` runs the binding's `merge` entry point, prints the judge's lines, removes the work tree and records merged or waiting with every reason. Exit 2 means it could not ask: report what it printed and leave the ticket.
      - **With a forge:** push, and open the pull request as [The pull request](#the-pull-request) says. Wait for both workflows: `gh pr checks <n> --watch` for `ci`, then the `merge` run that follows it (`gh run list --workflow merge.yml --limit 1`, then `gh run watch <id>`).
        - When `gh pr view <n> --json state` says `MERGED`, run `git pull --ff-only` on the default branch and `D close <ticket> --outcome merged --root .`.
        - Otherwise run `D close <ticket> --outcome waits --root .`, with one `--reason` per line of the judge's comment on the pull request.

   **Exit 3 from `update`, `close` or `split`:** a landing of that ticket is still running (for example a `land` the shell gave up waiting on), or only its merge is (its line says "a merge"). Leave the ticket: do not close, reconcile or split it. Wait for that landing to end, and go on at step 5 (`D plan`). The landing records the outcome itself; with only a merge running, a `report` or an `update` records it afterwards.

   **Exit 2 from `update`, `close` or `split` with a `drain:` or `merge:` line and no JSON:** the command refused before any work on the ticket. Report the line, leave the ticket as it is, and go on at step 5 (`D plan`). The line says why, for example: no such ticket, or a number more than one local ticket starts with; a fast-forward that was cut off and cannot be put back yet; the drain lock, not free in time.

   **A close refused with `<ref> merged, and a merge is not unsaid by closing it as <outcome>`:** the close has recorded that merge and removed a work tree its landing left. The ticket has landed: do not close it again, and go on at step 5 (`D plan`).

   **A ticket the plan lists as `built`** was committed by an earlier drain and has no run.
   - When its `why` says it is behind the default branch, start it at step 4.3. `update` makes its work tree again, rebases it and drops the verdict record the move outdated. Then step 4.4 judges it again and 4.5 writes the new record.
   - Otherwise start it at step 4.6.

   Done when the ticket is closed, split, landed as `update` said, or left as step 4.6, exit 3 or a refusal with exit 2 says.

5. **Start what a merge released.** After each ticket is closed, run `D plan --root .` again and start every ticket now in `start` as in step 3.

   Done when every ticket the new plan starts has its run started.

6. **The report.**
   - When no run is in progress and the plan starts nothing more, run `D report --drain <token> --root .`. It writes `.ratchet/queue/<date>-<time>.md` and prints it.
   - Print it to the owner whole, adding only the stash names of parked tickets.
   - `git worktree list` then lists no queue work tree but one a `left:` or `kept:` line named. That one stays as the script left it.

   Done when the report is printed whole, with the stash names of parked tickets added.

7. **The questions, then the route.**
   - After the report, ask every parked ticket's open questions as `feature.open-question`, all in one message, as [Questions for the owner](#questions-for-the-owner) says. An answer written to a ticket releases it.
   - **An answer released a ticket:** say so and run the drain again from step 1 in the same turn.
   - **Otherwise:** the last lines are the route. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .` in one Bash call and print its lines as they are.

   Done when the drain runs again for what was released, or the route's lines are the last lines you print.

### The publish check

`D check <ticket>` compares the ticket's branch and the forge with the start that `open`, or a rebasing `update`, recorded in the ticket's note (ADR 0060). Only `--final` marks the start passed, and `land` refuses a start that no final check passed. Pass `--final` only on the check just before a commit. The guard enforces this order: a commit in a queue work tree, by `git commit`, `am`, `cherry-pick` or `revert`, is refused until the start has passed its final check.

- **Exit 0:** nothing moved. Go on.
- **Exit 1:** a role committed, pushed or opened a pull request, or the orchestrator committed before the final check. The check has parked the ticket, kept what was uncommitted as a stash, and holds the ticket until its text changes. Commit nothing and land nothing. Go on at step 5 (`D plan`), and the report names it. When the line is `check: <ref> is merged and is not checked`, the ticket has landed instead: go on at step 5.
- **Exit 2:** the note holds no start, which `open` records. Report the line, leave the ticket, and go on at step 5.
- **Exit 3:** either the check found a rebase left unfinished or a state it cannot read, and has closed the ticket as waiting for a later drain to retry; or it refused before checking, since the ticket is held or its branch has no commit of its own and no work tree. Report the line and go on at step 5.

### Tickets the drain holds or retries

- **A ticket the plan lists as `held`** was parked or left to the owner for a decision. It is not landed again until its text changes or a path its run named as the cause changes on the default branch.
- **A ticket that waited only for a reason the drain resolves itself** (`behind`, `moved`, `conflict`, `gates`, `refs`, `errored`) stays `built` and is retried, at most `RETRIES` times in a row (three). Then it is held with a reason that says so.

## The records of an approved change

Both modes make these commits in this order ([pipeline.md](../method/pipeline.md), the orchestrator).

1. The change and the raised `quality/` files:
   - **One ticket:** run `ratchet` when it is bound, then commit everything the work tree holds (`git add -A`: the roles' `filesChanged` can miss the spec and a record they added) with the raised `quality/` files, the message naming the slug.
   - **The drain:** run `ratchet` when it is bound, and commit the raised `quality/` files.
2. Write the ADR the result suggests under the next free number.
3. **With no forge:** write the change description at `.scratch/changes/<branch>.md` (the branch's `/` written as `-`) and set the local ticket's `Status:` line to `resolved` in the same commit.
4. Last and alone, the verdict record: the result's `verdictRecord` on stdin of `just merge-ready record`, its file committed.

## The pull request

With a forge, open the pull request with every section of the template filled.

- **Conjecture:** each of the result's `assumptions` with its refuter.
- **Guard paths:** each of its `dependencies` with its reason, beside the versions in `dependencyNotes`.
- **Decisions:** every addition in `recordNotes`. Name a new term by its name only: a glossary's _Avoid_ words quoted there fail the prose gate.

When the critic did not run, the pull request says so and why, from the result's `critic`, in both modes.

## Too large: the agent splits it

A ticket that passes the product ceilings (the preflight's `size` failure, the spec stage's estimate, or the measured stop) is split by size by you, never by the owner and never with a question. The parts are tickets in the project's tracker, so they are built like any other.

1. **The plan.** Write a JSON file made with `mktemp`: `{"parts": [{"title": "...", "summary": "...", "promises": ["..."]}]}`.
   - **`tooLarge.proposedSplit` is there:** start from it. Take each part's `title` and `promises`, and its `summary` from its `criteria`.
   - **None is there:** divide the ticket yourself, so each part is a slice a person could use and fits both ceilings.
   - A part's `promises` are the ticket's own `- [ ]` lines, copied verbatim without the checkbox. Every such line of the ticket goes in exactly one part: add none, drop none, reword none.
   - A part waits on the one before, unless a part gives `after` as a list of part indexes.

   Done when every `- [ ]` line of the ticket is in exactly one part, word for word.

2. **Check the plan.** In the repository's own checkout, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/split.py" check <ticket> --plan <file> --root .`. It judges the plan as `split` will and files nothing.
   - **Exit 0:** go to step 3.
   - **Exit 1 or 2:** it prints `split: refused: <line>` for each line it refuses, or `split: the plan does not read: <why>`. Fix the plan once and check it again.
   - **Refused a second time:** do not run the plan. Park the ticket as too large, which is the owner's. Write the pipeline's result to a file made with `mktemp`, or `{"tooLarge": true}` when the preflight's `size` failure sent you here, and run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/drain.py" close <ticket> --outcome parked --result <file> --root .` with one `--reason "too large, and the split was refused: <line>"` per line the second check refused.
   - **The refusal says the ticket is itself a part:** a part is not split again, and the check refuses every plan for it. Park it as too large at once, as the bullet above says, with that line as the reason. The report names it, which is the owner's.

   Done when the check has passed the plan, or the ticket is parked as too large: after a second refusal, or at once for a part.

3. **File it.** In the repository's own checkout, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/drain.py" split <ticket> --plan <file> --root .`, only with a plan the check passed.
   - It checks the plan again, files the parts with the agent-ready label the parent holds now, closes the parent as `split` with its text unedited, and prints the parts.
   - **Exit 1:** it refused the plan and has already parked the ticket as too large: it prints `closed: <ref>: parked`, and the report gives the reason. The ticket is the owner's. Do not run it again.

   Done when the parts are printed, or `split` has parked the ticket as too large.

4. **Go on.** The parts are tickets the plan now lists, so the drain builds them in order. The hand-over says the ticket was split, into what, and why.

   Done when you hold the split's parts and its reason for the hand-over.

## Questions for the owner

A question reaches the owner only when it is a product question: its answer shapes the product (`direction`) or cannot be taken back (`irreversible`). The spec role's result marks each one, with two to four options, the recommended one first, and a sentence on why. Never ask the owner to confirm what you would do anyway, and never ask whether to go on.

1. **Ask** `feature.open-question`: "<the question the build stopped on, in the words of someone using the product>" All of them in one message, each as the spec role wrote it, so the owner can accept them all at once. The recommended option is first and its label ends with `(recommended)`; the description gives the reason.
2. **Write each answer on the ticket** under `## Answers`, the question and the answer in one line each.
   - **A local ticket:** write them at the end of the file and land it from the default branch. With no forge, `just merge --records` (the `merge` entry point) commits it on a branch of its own. With a forge it goes on a branch of its own and through a pull request. Never commit on the default branch itself.
     - **A ticket that already carries the agent-ready label, or the spec such a ticket inherits from,** changes only by the owner's merge, with a forge or without (only a ticket's link paths may change alone). With no forge, the run prints `merge: owner` and the owner's command: relay both as they are. With a forge, the merge job leaves the pull request to the owner and comments why: relay that comment as it is.
   - **A tracker ticket:** `gh issue edit <n> --body-file <file>` with the full body, no label flag.
3. **Record how the owner answered,** one Bash call per answer: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/questions.py" record --skill feature --key "feature.<slug>.<n>" --kind <direction|irreversible> --recommended <yes|no> --root .`, where `<n>` is the question's place in the result. The log is `.ratchet/questions.tsv`, which no commit carries.

   Done when every answer is on its ticket and has its line in the log.

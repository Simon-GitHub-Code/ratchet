---
name: feature
description: Build one feature from an agent-ready tracker ticket with the Ratchet pipeline. Runs the feature preflight first, deterministically, and spawns nothing while it is red; creates the working branch; then runs the four-role pipeline workflow with the arguments it needs.
disable-model-invocation: true
---

# Build a feature from a ticket

`/ratchet:feature <ticket>`: the ticket is a number on the tracker (`4` or `#4`) or the path of a local ticket file (`.scratch/<slug>/issues/02-<slug>.md`). Load the `ratchet:method` skill first; [pipeline.md](../method/pipeline.md) says what the four roles do and [records.md](../method/records.md) what the ticket carries.

The feature's input is the ticket the owner's `/mattpocock-skills:to-spec`, `to-tickets` or `triage` wrote, plus one section the method adds, `## Conjecture`, and one label the owner applies: the agent-ready label (`ready-for-agent` in mattpocock-skills, spelled as `docs/agents/triage-labels.md` maps it). The label is the owner's agreement; nothing below infers it.

## Steps

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

4. **The result.** Follow the orchestrator section of [pipeline.md](../method/pipeline.md): `verdict`, `unresolvedBlockingFindings`, the `checkOutput` tail, every dispute to the owner, then `ratchet` when it is bound (the raised thresholds go in the same commit; the orchestrator's step 3 in pipeline.md says why), commit `filesChanged`, write the verdict record (the result's `verdictRecord` on stdin of `just merge-ready record`) and commit it alone and last, push, and open the pull request with every section of the template filled, the Conjecture section listing each of the result's `assumptions` with its refuter, the Guard paths section naming each of the result's `dependencies` with its reason beside the versions in `dependencyNotes`, the Decisions section listing every addition in `recordNotes` (each glossary term, *Avoid* item and ADR the run added) (with no forge, the change description at `.scratch/changes/<branch>.md` instead, and the local ticket's `Status:` line set to `resolved` in the same commit: the local form of `Closes #N`, so merging the branch resolves the ticket; the description is committed before the verdict record, nothing is pushed, and `just merge <branch>` judges the branch and merges it or prints why the merge is the owner's). A `blocked` verdict with `openQuestions` goes back to the owner on the ticket (a comment or an edit); re-run `/ratchet:feature <ticket>` when the ticket answers them. A `blocked` verdict with `guardEdits` lists the guard-path changes the ticket needs: the owner lands them on the default branch, or builds the ticket with `/pstack:poteto-mode` while present. A run that is not approved commits nothing, so clear what its agents left before you hand over: `git stash push -u -m "<branch>: <verdict> run"`, then `git switch <base>`. The rerun's preflight needs a clean tree, the route then reads the true state instead of a dirty branch, and the stash keeps the draft spec and tests for the owner to read; name it in the hand-over. The `blocked` stash's name is what `/ratchet:next` reads: it holds the ticket until the default branch moves past the run.

5. **Hand over.** When the change built a surface and the repository has `PRODUCT.md` but no `DESIGN.md`, say that an agent runs `impeccable document` to write it once this merges, in a session the owner attends, since the write of `DESIGN.md`, a guard path, asks the owner. It is not an owner keystroke. The last lines are the route: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .` in one Bash call and print its three lines. With the pull request open they say the merge job decides. Say what the merge judge says now (`just merge-ready`: `merge: agent`, or `merge: owner` with its reasons), and never merge by hand. When the merge is the owner's, say also which of the owner's own passes the change warrants before they merge, from [route.md](../method/route.md) stage 8: `/pstack:interrogate` for a contested design, `/pstack:blast-radius` for a change with reach, `impeccable audit` or `polish` for a surface.

## Why the skill reads the way it does

- **The preflight is a script, not an agent.** Issue 6: the workflow's slash form passes no arguments, and its preconditions were checked by the spec agent after it had spawned. A script decides before the first agent exists, and the workflow's first stage runs the same script, so both entrances agree.
- **The ticket is mattpocock-skills' and the Conjecture is the method's.** Issue 4: a second feature-brief format duplicated `to-spec`, `to-tickets` and `triage`. The method adds the one section those skills lack, the conjecture with its refuter and the alternatives killed, and reads everything else as those skills wrote it.
- **The label is the agreement.** Issue 5: "the owner has agreed the seams" was assumed. The agent-ready label is a thing the owner does, the preflight refuses a ticket without it, and the guard asks when an agent tries to apply it.

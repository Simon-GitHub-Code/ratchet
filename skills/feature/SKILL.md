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

   It prints JSON (`ok`, `failures`, `number`, `title`, `slug`, `branch`, `label`) and exits 1 when red. Red: report `verdict: blocked` and, for each failure, its `check`, `detail` and `produce` (the skill or action that supplies the missing piece: `to-spec`, `to-tickets` or `triage` for the ticket, the owner for the Conjecture and the label, the blocking tickets for `blocked`, a commit or stash for the tree). Spawn nothing, create nothing, label nothing: applying the agent-ready label is the owner's act and the guard asks on it. Stop.

2. **The branch.** Green: `git switch -c <branch> <base>` where `<branch>` is the preflight's (`feature/<number>-<slug>`); `git switch <branch>` when it already exists. Run the preflight once more with `--branch <branch>`; it must stay green.

3. **The pipeline.** Call the Workflow tool with the plugin's script and the arguments the preflight produced:

   ```
   scriptPath: "${CLAUDE_PLUGIN_ROOT}/workflows/pipeline.js"
   args: { "ref": "<ticket>", "branch": "<branch>", "base": "main", "preflight": "${CLAUDE_PLUGIN_ROOT}/scripts/feature_preflight.py" }
   ```

   (`name: "ratchet:pipeline"` where the harness lists plugin workflows by name.) The workflow runs the same preflight as its first stage, through one low-effort agent, so the pipeline cannot be entered around this skill; then Spec, Implement, Critique, the fix loop and Review. It never commits, pushes or opens a pull request.

4. **The result.** Follow the orchestrator section of [pipeline.md](../method/pipeline.md): `verdict`, `unresolvedBlockingFindings`, the `checkOutput` tail, every dispute to the owner, then commit `filesChanged`, push, and open the pull request with every section of the template filled (with no forge, the change description at `.scratch/changes/<branch>.md` instead; nothing is pushed). A `blocked` verdict with `openQuestions` goes back to the owner on the ticket (a comment or an edit); re-run `/ratchet:feature <ticket>` when the ticket answers them. A run that is not approved commits nothing, so clear what its agents left before you hand over: `git stash push -u -m "<branch>: <verdict> run"`, then `git switch <base>`. The rerun's preflight needs a clean tree, the route then reads the true state instead of a dirty branch, and the stash keeps the draft spec and tests for the owner to read; name it in the hand-over.

5. **Hand over.** The last lines are the route: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .` in one Bash call and print its three lines. With the pull request open they say to wait for the review; say also which of the owner's own passes the change warrants before they merge, from [route.md](../method/route.md) stage 8: `/pstack:interrogate` for a contested design, `/pstack:blast-radius` for a change with reach, `impeccable audit` or `polish` for a surface.

## Why the skill reads the way it does

- **The preflight is a script, not an agent.** Issue 6: the workflow's slash form passes no arguments, and its preconditions were checked by the spec agent after it had spawned. A script decides before the first agent exists, and the workflow's first stage runs the same script, so both entrances agree.
- **The ticket is mattpocock-skills' and the Conjecture is the method's.** Issue 4: a second feature-brief format duplicated `to-spec`, `to-tickets` and `triage`. The method adds the one section those skills lack, the conjecture with its refuter and the alternatives killed, and reads everything else as those skills wrote it.
- **The label is the agreement.** Issue 5: "the owner has agreed the seams" was assumed. The agent-ready label is a thing the owner does, the preflight refuses a ticket without it, and the guard asks when an agent tries to apply it.

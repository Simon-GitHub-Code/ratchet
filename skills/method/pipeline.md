# The feature pipeline

One agent grading its own work is confirmation, not criticism. A feature is therefore built by four roles with fixed sight lines, run by the `pipeline` workflow the plugin ships (`workflows/pipeline.js`), which the `/ratchet:feature <ticket>` skill invokes once its preflight is green. The script is a pure function of its arguments and the agents' structured results, so it resumes cleanly. Each role does its work with the other plugins' skills; the method fixes what a role sees, what it returns and when it is done.

## Roles

| Role | Works with | Sees | Never sees | Done when |
| --- | --- | --- | --- | --- |
| **Spec** | `mattpocock-skills:tdd`: what a good test is, the seams under test | the ticket with its comments, the glossary, the design brief, the relevant ADRs, code only as far as placing the feature needs | | `docs/features/<slug>/spec.md` holds numbered Given/When/Then criteria, glossary terms, placement with reasons, the seams under test, a test plan mapped to criteria, out of scope; the failing tests exist and each fails for the right reason, its failure content quoted in the result and in the spec; `check` is red only on the new tests. Open questions stay open. |
| **Implement** | `mattpocock-skills:tdd`: one slice at a time; `impeccable` (`shape`, then `polish`) for any user interface | the spec and its tests, the paths named | the ticket, the spec author's reasoning | every listed test passes and `check` exits 0. A test that cannot be made to pass is left red. |
| **Critic** | the method's rule of evidence, as the one adversary (`/pstack:interrogate` is the owner's to type on the branch afterwards, never the critic's to imitate); `impeccable` `audit` and `critique` for any user interface | the spec, its tests, the changed files, the fitness tests, the fuzz harness | the implementer's notes, the ticket | every finding carries the command and output that proves it, and `attempted` lists every command run, so "nothing found" carries its severity. Property and edge-case tests written; changed-code mutation run; fuzzing run; the fitness tests sabotaged in a scratch copy and seen to bite; vocabulary and contracts checked; `guard-diff` run. Tests that expose real defects stay. |
| **Review** | `mattpocock-skills:code-review`: its Spec and Standards axes and smell baseline | the spec, the uncommitted diff, `AGENTS.md`, the glossary, `docs/adr/`, the binding, `docs/disputes/`, `DESIGN.md`, generated contract artefacts | the implementer's notes, the critic's findings, the ticket | axis A, the spec: criteria narrowed, tests weakened, behaviour added; axis B, the standards; axis C, guard paths. Approve only with no blocker or major issue, no undisputed guard change, and `check` exit 0. |

A skill that spawns sub-agents of its own (`code-review`) is followed in-process: the pipeline fixes the agent count, so the role is the single reviewer its skill describes. The roles call model-invoked skills only (`tdd`, `code-review`, `impeccable`, `diagnosing-bugs`); a user-invoked one is the owner's keystroke and the harness refuses it to an agent. [route.md](route.md)

Severity: *blocker* is a wrong result, data loss, a layer violation or a broken non-negotiable from `AGENTS.md`; *major* is a missing criterion or a test that does not test; *minor* is everything else.

## The loop

0. Preflight. `scripts/feature_preflight.py` on the ticket: open, `## Conjecture` stated, the agent-ready label present, the tree clean, `HEAD` on the branch. The skill runs it before the workflow and spawns nothing while it is red; the workflow runs it again as its first stage through one low-effort agent that types the command and relays its JSON. Red stops the run with `verdict: "blocked"` and, per failure, what produces the missing piece.
1. Spec. A non-empty `openQuestions` stops the run with `verdict: "blocked"`; the orchestrator asks the owner, who answers on the ticket, and reruns.
2. Implement.
3. Critique.
4. While blocker or major findings remain, up to two rounds: a *fresh* fix agent (it has not seen the previous implementer's reasoning), then re-critique by title plus a search for new breakage.
5. Review.
6. The script forces `request_changes` when blocking findings are unresolved, when the last implement or fix reported `checkPasses: false`, when the spec's tests did not fail for the right reason or a test file has no quoted failure, when the critic found nothing and attempted nothing, or when a guard path changed without a dispute naming it. `verdictForcedBy` says which.

At most nine agents run, the preflight's included. Agents leave every change uncommitted; the orchestrator commits and publishes. An agent blocked by a guard path writes a dispute and returns it.

## The orchestrator

The orchestrator is the `/ratchet:feature` skill's session: it runs the preflight, creates the branch, runs the workflow, reads its result and the spec, and nothing else: reading the implementation would anchor it on one agent's reasoning and defeat the independence the pipeline exists for. If the result is not enough to decide, add the missing field to the schema.

1. Check `verdict === "approve"`, empty `unresolvedBlockingFindings`, and a green tail in `checkOutput`. Otherwise stash what the agents left (`git stash push -u`), switch back to the base branch, and rerun `/ratchet:feature <ticket>` after the ticket has been adjusted, or stop and report.
2. Relay every dispute to the owner, through the interactive question dialog where one exists. The owner decides; any gate change is the owner's.
3. Commit `filesChanged` on the branch with a message naming the slug, push.
4. Open the pull request with every section of the repository's template filled ([records.md](records.md)). With no forge (the binding's Forge section names a change template instead of a pull request template), nothing is pushed: the filled template is `.scratch/changes/<branch>.md` (the branch's `/` written as `-`), committed on the branch, and the owner merges. If an ADR is suggested, it goes in the same pull request, which means an interactive session with the owner present, since `docs/adr/` is a guard path.
5. Wait. A human merges.

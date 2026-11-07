# The feature pipeline

One agent grading its own work is confirmation, not criticism. A feature is therefore built by four roles with fixed sight lines, run by the `feature` workflow the plugin ships (`/ratchet:feature`, `workflows/feature.js`). The script is a pure function of its arguments and the agents' structured results, so it resumes cleanly. Each role does its work with the other plugins' skills; the method fixes what a role sees, what it returns and when it is done.

## Roles

| Role | Works with | Sees | Never sees | Done when |
| --- | --- | --- | --- | --- |
| **Spec** | `mattpocock-skills:tdd`: what a good test is, the seams under test | the brief, the glossary, the design brief, the relevant ADRs, code only as far as placing the feature needs | | `docs/features/<slug>/spec.md` holds numbered Given/When/Then criteria, glossary terms, placement with reasons, the seams under test, a test plan mapped to criteria, out of scope; the failing tests exist and each fails for the right reason, its failure content quoted in the result and in the spec; `check` is red only on the new tests. Open questions stay open. |
| **Implement** | `mattpocock-skills:tdd`: one slice at a time | the spec and its tests, the paths named | the brief, the spec author's reasoning | every listed test passes and `check` exits 0. A test that cannot be made to pass is left red. |
| **Critic** | `pstack:interrogate`: its brief, as the one adversary | the spec, its tests, the changed files, the fitness tests, the fuzz harness | the implementer's notes, the brief | every finding carries the command and output that proves it, and `attempted` lists every command run, so "nothing found" carries its severity. Property and edge-case tests written; changed-code mutation run; fuzzing run; the fitness tests sabotaged in a scratch copy and seen to bite; vocabulary and contracts checked; `guard-diff` run. Tests that expose real defects stay. |
| **Review** | `mattpocock-skills:code-review`: its Spec and Standards axes and smell baseline | the spec, the uncommitted diff, `AGENTS.md`, the glossary, `docs/adr/`, the binding, `docs/disputes/`, generated contract artefacts | the implementer's notes, the critic's findings, the brief | axis A, the spec: criteria narrowed, tests weakened, behaviour added; axis B, the standards; axis C, guard paths. Approve only with no blocker or major issue, no undisputed guard change, and `check` exit 0. |

A skill that spawns sub-agents of its own (`interrogate`, `code-review`) is followed in-process: the pipeline fixes the agent count, so the role is the single adversary or reviewer its skill describes.

Severity: *blocker* is a wrong result, data loss, a layer violation or a broken non-negotiable from `AGENTS.md`; *major* is a missing criterion or a test that does not test; *minor* is everything else.

## The loop

1. Spec. A non-empty `openQuestions` stops the run with `verdict: "blocked"`; the orchestrator asks the owner, edits the brief, and reruns.
2. Implement.
3. Critique.
4. While blocker or major findings remain, up to two rounds: a *fresh* fix agent (it has not seen the previous implementer's reasoning), then re-critique by title plus a search for new breakage.
5. Review.
6. The script forces `request_changes` when blocking findings are unresolved, when the last implement or fix reported `checkPasses: false`, when the spec's tests did not fail for the right reason or a test file has no quoted failure, when the critic found nothing and attempted nothing, or when a guard path changed without a dispute naming it. `verdictForcedBy` says which.

At most eight agents run. Agents leave every change uncommitted; the orchestrator commits and publishes. An agent blocked by a guard path writes a dispute and returns it.

## The orchestrator

The orchestrator writes the brief, creates the branch, runs the workflow, reads its result and the spec, and nothing else: reading the implementation would anchor it on one agent's reasoning and defeat the independence the pipeline exists for. If the result is not enough to decide, add the missing field to the schema.

1. Check `verdict === "approve"`, empty `unresolvedBlockingFindings`, and a green tail in `checkOutput`. Otherwise rerun with the same arguments after adjusting the brief, or stop and report.
2. Relay every dispute to the owner, through the interactive question dialog where one exists. The owner decides; any gate change is the owner's.
3. Commit `filesChanged` on the branch with a message naming the slug, push.
4. Open the pull request with every section of the repository's template filled ([records.md](records.md)). If an ADR is suggested, it goes in the same pull request, which means an interactive session with the owner present, since `docs/adr/` is a guard path.
5. Wait. A human merges.

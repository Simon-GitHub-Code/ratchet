# The feature pipeline

One agent grading its own work is confirmation, not criticism. A feature is therefore built by four roles with fixed sight lines, run by the `feature` workflow the method's plugin ships (`/ratchet:feature`, template in `workflows/feature.js`). The script is a pure function of its arguments and the agents' structured results, so it resumes cleanly.

## Roles

| Role | Sees | Never sees | Returns | Done when |
| --- | --- | --- | --- | --- |
| **Spec** | the brief, the glossary, the design brief, the relevant ADRs, code only as far as placing the feature needs | | `slug, specPath, testFiles, layersTouched, openQuestions, failingForRightReason, checkOutput` | `docs/features/<slug>/spec.md` holds numbered Given/When/Then criteria, glossary terms, layer placement with reasons, a test plan mapped to criteria, out of scope; the failing tests exist and each fails *for the right reason* (missing behaviour, never an import, syntax or fixture error); `just check` is red only on the new tests. Open questions stay open. |
| **Implement** | the spec and its tests, the layers named | the brief, the spec author's reasoning | `filesChanged, allNewTestsPass, checkPasses, notes, disputes, checkOutput` | every listed test passes and `just check` exits 0. A test that cannot be made to pass is left red, never weakened. |
| **Critic** | the spec, its tests, the changed files, the fitness tests, the fuzz harness | the implementer's notes, the brief | `findings[{severity, title, evidence, suggestedTest}], testsAdded, mutationSummary, fuzzSummary, fitnessSabotageConfirmed, guardFilesChanged, checkOutput` | its job is to break the change; "nothing found" needs evidence. Property and edge-case tests written; changed-code mutation run; fuzzing run; the fitness tests sabotaged in a scratch copy and seen to bite; contracts and vocabulary checked; `just guard-diff` run. Tests that expose real defects stay. |
| **Review** | the spec, the uncommitted diff, `CLAUDE.md`, `GLOSSARY.md`, `docs/adr/`, the binding, `docs/disputes/`, generated contract artefacts | the implementer's notes, the critic's findings, the brief | `verdict, issues[{axis, severity, title, location, evidence}], adrSuggested, guardChanges, disputeCoverage, checkPasses, checkOutput` | axis A, the spec: criteria narrowed, tests weakened, behaviour added; axis B, the standards; axis C, guard paths. Approve only with no blocker or major issue, no undisputed guard change, and check exit 0. |

Severity: *blocker* is a wrong result, data loss, a layer violation or a broken non-negotiable from `CLAUDE.md`; *major* is a missing criterion or a test that does not test; *minor* is everything else.

## The loop

1. Spec. A non-empty `openQuestions` stops the run with `verdict: "blocked"`; the orchestrator asks the owner, edits the brief, and reruns.
2. Implement.
3. Critique.
4. While blocker or major findings remain, up to two rounds: a *fresh* fix agent (it has not seen the previous implementer's reasoning and does not look for it), then re-critique by title plus a search for new breakage.
5. Review.
6. The script forces `request_changes` when blocking findings are unresolved, when the last implement or fix reported `checkPasses: false`, when the spec's tests did not fail for the right reason, or when a guard path changed without a dispute naming it. `verdictForcedBy` says which.

At most eight agents run. No agent commits, pushes, stashes or resets. No agent edits a guard path; one that is blocked writes a dispute and returns it.

## The orchestrator

The orchestrator writes the brief, creates the branch, runs the workflow, reads its result and the spec, and nothing else. Reading the implementation would anchor it on one agent's reasoning and defeat the independence the pipeline exists for. If the result is not enough to decide, add the missing field to the schema.

1. Check `verdict === "approve"`, empty `unresolvedBlockingFindings`, and a green tail in `checkOutput`. Otherwise rerun with the same arguments after adjusting the brief, or stop and report.
2. Relay every dispute to the owner, through the interactive question dialog where one exists. The owner decides; any gate change is the owner's.
3. Commit `filesChanged` on the branch with a message naming the slug, push.
4. Open the pull request with every section of the template filled. If an ADR is suggested, it goes in the same pull request, which means an interactive session with the owner present, since `docs/adr/` is a guard path.
5. Wait. A human merges.

## The pull request template

Every section is filled; an empty section is a criticism of the pull request.

```markdown
## Conjecture
What this change claims, and the criterion that would show it wrong.

## Brief and spec
Links to docs/features/<slug>/brief.md and spec.md; the acceptance criteria in one list.

## Criticism
Critic findings and how each was resolved; review issues; disputes raised and their status.

## Guard paths
Output of `just guard-diff`, and for each path the upheld dispute or the owner's hand.

## Dependencies
Each new dependency and why it is a good decision.

## Decisions
ADRs written or superseded, or "none".

## Gates
The tail of `just check`.
```

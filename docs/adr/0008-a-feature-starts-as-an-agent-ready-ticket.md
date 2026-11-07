---
status: accepted
---
# A feature starts as an agent-ready ticket, checked before the first agent spawns

The feature pipeline took a brief in the method's own format at `docs/features/<slug>/brief.md`, which duplicated what `mattpocock-skills:to-spec`, `to-tickets` and `triage` already write, assumed the owner had agreed it without any act that showed agreement, and could not be started from its slash form, which passes no arguments; its preconditions were checked by the spec agent after it had spawned. We decided that the feature's input is the tracker ticket those skills write (an issue number, or a local ticket file where there is no tracker), that the method adds exactly one section to it, `## Conjecture` (the approach, what would refute it, the alternatives and the criticism that kills each), that the owner's agreement is the agent-ready label (the `ready-for-agent` role as `docs/agents/triage-labels.md` spells it, or the `Status:` line of a local ticket), which the guard asks on when an agent applies it; that `scripts/feature_preflight.py` decides deterministically whether a ticket is open, carries a stated conjecture and the label, and whether the tree is clean and on the branch, printing per failure the skill or act that produces the missing piece; that `/ratchet:feature <ticket>` is a skill that runs the preflight before any agent exists, creates `feature/<number>-<slug>`, and invokes the `pipeline` workflow with its arguments; and that the workflow runs the same preflight as its own first stage through one low-effort agent, because a Workflow script cannot read a file or run a command. The spec role keeps writing `docs/features/<slug>/spec.md` with Given/When/Then criteria and the failing tests; `implement-spec` is not used, since one `/ratchet:feature` run builds one ticket. Decided 2026-11-07.

## Refutation criterion

The decision is wrong if, in the first ten features built with it, the preflight passes a ticket the owner had not agreed to build, or refuses one the owner had, more than once; or if the workflow's own preflight stage ever disagrees with the skill's run on the same ticket and checkout. Check after the tenth feature.

## Considered options

- Keep the brief and add a `Status: agreed` line: rejected, the brief still duplicates the mattpocock-skills formats, and a line any agent can write is not the owner's act.
- Check the preconditions inside the workflow only: rejected, a Workflow script has no file or shell access, so the check would run in an agent, after the spawn the check exists to prevent, and the slash form of a workflow passes no arguments at all.
- Check them in the skill only: rejected, the workflow stays invokable by hand with hand-written arguments; one low-effort agent typing the same command closes that door.
- Mirror the ask on the agent-ready label into the generated permission rules: rejected, those rules match a command prefix and cannot read the label name; a rule on `gh issue edit` would ask on every edit. The hook holds the finer line, as it does for `gh label create`.

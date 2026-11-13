---
status: accepted
---
# A safeguard arrives with the first thing it guards, and the first one is free

ADR 0012 asked telemetry, feature flags, security testing and load testing of every repository from the first commit, and adoption bound every gate against a walking skeleton built for the purpose. ADR 0020 made a gate with nothing to run red. Together they ask a hobby project, on the day it is a single file, for a span exporter, a flag seam, a load smoke, a scanner and a fault for each, before the owner has written the idea they sat down to write. The owner put the principle that replaces this on 2026-11-13: safeguards are added by judgement, per project, at the stage they earn their keep, and the method must never block the flow of someone's creativity. The method is for hobby and solo projects first and large ones less often.

Asking everything late is no answer either. Some safeguards cost little to add at the first instance of what they guard and a great deal at the fortieth: a timeout on the first outbound call is minutes, on forty after an outage a week. Others are worthless before an event in the project's life: a restore drill before there is data worth restoring tests nothing.

## Decision

Every question the method asks carries a **trigger**, in `skills/method/application-types.toml`. A question is **asked** of a repository once its trigger has fired. Before that its row is bound `later: <trigger>` and judges nothing.

- `always`: the floor. These questions need no setup and run in seconds: format, lint, strict types, secrets, commit subjects, guards, the method's own gates, the tests that exist. They hold from the first commit.
- `code:<kind>`: the first **instance** of a kind of code: an entry point, an outbound call, a listener, a configuration read, a stored format, a user interface. The binding's `## Triggers` table says what marks an instance in this stack (paths and an optional pattern). A shipped script counts instances at the merge base and at head. No owner is needed, and the count is deterministic.
- `life:<event>`: something that happens to the project, not to its code: `deployed` (others can reach it), `irreplaceable-data` (it holds data the owner could not recreate), `depended-on` (people outside rely on it). A machine cannot see these reliably, so the owner declares them on the binding's `- Life events:` line. The route proposes one when it sees a sign of it.

**The first one is free.** The change that brings the first instance of a kind is never red for it. The gate reports that the kind has arrived and which rows it makes **owed**. The route then makes binding them the next step: `/ratchet:safeguard`, where an agent chooses the tool, writes the recipe and the fault, and binds the row. A later change that adds another instance while a row is still owed is red. Retrofitting then costs one instance, and the owner never stops mid-idea to build a safeguard. A declared life event makes its rows owed in the same way, and is never red: the route puts binding them first.

**A gate once asked keeps ADR 0020.** A bound gate with nothing to run is red. A row only becomes bound after its trigger has fired, so the set it judges already has a member.

**What waits for a life event.** Some behaviour of the merge judge and the pipeline protects people other than the owner, so it starts when they exist:

- New surface with no flag that is off goes to the owner only once the project is `deployed`. Before that, nobody meets the surface.
- A one-way door goes to the owner only once the project is `deployed` or holds `irreplaceable-data`. Before that, nothing is lost by walking back through it.
- The pipeline's critic and its fix loop run only once the project is `depended-on`. Before that, a ticket is built by spec, implement and review. The merge rule is unchanged: a finding the review opens is still open.

Guard paths, disputes and the ratchet hold from the first commit. They protect the method itself, and they cost nothing until something touches them.

**Read from the merge base.** Life events and the Triggers table are read at the merge base when they decide whether a change waits for the owner, so a change cannot loosen its own judge. Both live in the binding, which is a guard path.

## The SRE questions this adds

| Question | Trigger | Applies to | Tier |
| --- | --- | --- | --- |
| rolls back safely | `code:stored-format` | service, app, cli, job | 1: the merge base's build runs against the data the head's migrations leave |
| survives failing dependencies | `code:outbound-call` | service, web-ui, app, job | 1: every outbound call has a timeout, and a test meets the dependency erroring, slow and gone |
| refuses bad configuration | `code:config-read` | service, app, cli, job | 1: with a required setting missing, it exits non-zero before it is ready and names the setting |
| shuts down gracefully | `code:listener` | service, job | 1: signalled while serving, it finishes the work in flight and exits within the limit |
| incidents tighten a gate | `always` | all | 1: each record under `docs/incidents/` names a fault in `quality/bites.toml` that reproduces it |
| backup restores | `life:irreplaceable-data` | service, app, job | weekly: the latest backup restores into scratch and the deliverable boots on it |
| production answers | `life:deployed` | service, web-ui | weekly and after a deploy: the live deployment answers its real routes |

Service-level objectives and error budgets were considered and left out: they need traffic most of these projects do not have, and the owner chose not to carry them.

## Refutation criterion

This is wrong if safeguards arrive too late to be cheap, or the triggers become paperwork. Look at the first three repositories adopted under it, after twenty merged changes each. It is refuted if a safeguard bound after a trigger fired took more than one change to retrofit, because instances had piled up while it was owed. It is also refuted if an owner reports a gate red on work they were exploring, or if a Triggers row matches nothing in a stack that has the kind (a detector that never fires). And it is refuted if a `deployed` or `depended-on` project is found still not declaring the event a month after the route first proposed it.

## Considered options

- **A stage the owner sets (sketch, live, has users, scaling) with each question at a stage.** Rejected as the only trigger. It misses the code-shaped safeguards, which must arrive at the first instance whatever the stage, and a forgotten stage line leaves everything late.
- **Signals alone, with no owner declaration.** Rejected for life events. A deploy file is evidence of intent, not of users, and a safeguard switching on unannounced is the surprise the principle forbids. Kept for code, where an instance is a fact.
- **Red at the first instance.** Rejected. It blocks exactly the change where the owner is creating something new. One instance is what retrofitting then costs.
- **Advisory only, never red.** Rejected. A warning nobody acts on lets instances pile up, which is the retrofit cost this exists to bound (ADR 0025's reasoning on unread warnings).
- **Keep ADR 0012's every row from the first commit.** Rejected by the owner's principle. Rows already bound in an adopted repository stay bound: the ratchet does not loosen, and nothing here unbinds a gate.

Supersedes, in part: ADR 0012's "from the first commit" and its walking skeleton at adoption, and ADR 0020's red gate with nothing to run, before its trigger fires. The rest of both stands.

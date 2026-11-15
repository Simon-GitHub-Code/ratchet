---
name: method
description: The Ratchet method says what must be true before a merge. Load when AGENTS.md names it, a gate goes red, an edit or command is denied, a threshold, guard path or dispute is in play, a binding or tier is being written, or a feature ticket is agent-ready.
---

# The Ratchet method

Every change is a **conjecture** that **stands** only while no **criticism** of it is **open**. The **gates** are criticism made deterministic: they run in **tiers**, they only **ratchet** tighter, and every exception to them is a written **record**.

## The four plugins

Four plugins compose, and each owns one question.

- `mattpocock-skills`: how the work is done. `tdd`, `codebase-design`, `domain-modeling`, `code-review`, `diagnosing-bugs`, `grilling`, `pr`, `research`.
- `pstack`: in what style and with which levers. `poteto-mode`, `architect`, `interrogate`, `arena`, `correct`, the formal verification skills.
- `impeccable`: how anything a person sees is designed. Every user interface goes through it.
- This method: what must be true before a merge, and which of those skills comes next at each stage (the **route**).

## Words

Use these words in code, tests, records and prose.

### Gates

- **gate**: one deterministic check answering one question the method asks. It is *red* or *green*, nothing in between.
- **red**, **green**: a test or gate's observable state. A test that was never red proves nothing.
- **bite**: a gate shown to go red on a planted fault, and on an empty collection when it judges one. `quality/bites.toml` holds the faults. A gate nobody has seen fail is a gate nobody can trust.
- **tier**: when a gate runs. Tier 0 on every commit, tier 1 on every pull request, tier 2 on the default branch and nightly.
- **entry point**: one named command per tier and per method gate, in whatever runner the binding names.
- **binding**: `docs/process/binding.md`, the repository's table mapping each question to the tool that answers it, or to `none` with a reason. It names the runner and the forge too. It also declares the repository's **application types** (`service`, `web-ui`, `app`, `cli`, `job`, `library`), which decide the questions that apply and never the tool.
- **ratchet**: a recorded threshold that only rises. The `ratchet` entry point raises it to the measured value.
- **ceiling**: a recorded wall-clock limit per tier or gate, in seconds, that only falls. The `budget` entry point measures against it.
- **absolute path**: code held at 100% coverage with no exclusion reaching it: the gate tools, and whatever the binding declares pure.

### Triggers and what is owed

- **trigger**: when a question starts to be asked of a repository. `always` is the floor, `code:<kind>` is the first instance of a kind of code, and `life:<event>` is a life event the owner declares. A row whose trigger has not fired is bound `later: <trigger>` and judges nothing. Read [tiers.md](tiers.md#triggers) for each question's trigger and which triggers reopen the architecture.
- **the floor**: the questions asked from the first commit. They need no setup and run in seconds: format, lint, strict types, secrets, commit subjects, the guards, the method's own gates and the tests that exist.
- **instance**: one occurrence of a kind of code: a route, command, job handler or screen a user or caller reaches (the `entry-point` kind), an outbound call, a listener, a configuration read, a stored format, a user interface. The binding's `## Triggers` table says what marks one in this stack, and `scripts/triggers.py` counts them at the merge base and at head.
- **life event**: something that happens to the project, not to its code: `deployed` (others can reach it), `irreplaceable-data` (it holds data the owner could not recreate), `depended-on` (people outside rely on it). The owner declares it on the binding's `- Life events:` line. The route proposes one when it sees a sign of it, and never declares it.
- **owed**: a row whose trigger has fired and which is still bound `later:`. Until its trigger fires, a `later:` row is not owed.
  - **The first one is free**: the change that brings a kind is never red for it, however many files it touches.
  - A later change that adds another instance while a row is still owed is red.
  - A declared life event makes its rows owed too. The event itself is never red.
  - An **architecture review** is owed the same way when a kind that changes the project's shape first arrives, or a life event is declared, until a decision record names that trigger (`/ratchet:architecture`).

### Guards and merging

- **owner**: the human. Decides disputes, changes gates by hand, and merges every change the merge judge does not pass.
- **guard path**: a file that defines, runs or records a gate, listed in `guards/manifest.toml`.
  - An agent changes one on a branch, and the merge judge sends the change to the owner.
  - The hook denies each change it reads to one that would land on the default branch. That denial is about where the commit lands: the way out is a branch.
  - Edit-time control stays for exactly the files whose effect comes before the merge. On every branch, two kinds get a verdict at edit time:
    - a file that acts at once, a CI workflow or an agent-instruction file, guard path or not: the hook asks the owner before each write it reads;
    - a locked file: the hook denies each change it reads.
  - The hook checks ordinary writes. It is not a boundary against an agent that sets out to get round it.
- **merge judge**: the shipped script that decides who merges a change.
  - It passes one whose gates are green, whose review approves with no finding open, and that changes no guard path.
  - Records alone (a spec, a ticket, a change description, a glossary term, each in the shape a skill writes it) need no review unless they do one of these:
    - give a ticket the agent-ready label;
    - change a labelled ticket (other than the paths of its links, to the same files) or what it reads;
    - close a ticket;
    - bring an ADR, which the owner agrees to.
  - The adoption is judged by the gates it brings.
  - Once the project is `deployed` it also leaves to the owner a change that goes through a one-way door or adds surface without a flag. Once it holds `irreplaceable-data`, it leaves a one-way door to the owner.
  - Read [guards.md](guards.md) for how the judge decides each case.
- **one-way door**: a change that cannot be taken back once it ships: a migration, stored data rewritten, a contract line removed, a flag turned on for everyone. The manifest lists the paths. Before others can reach the project or it holds data the owner could not recreate, nothing is lost by walking back through one.
- **dispute**: the record an agent writes when it believes a gate, not the code, is wrong. The owner decides.
- **record**: the design brief, the glossary, an ADR, a feature brief, a dispute, an incident record, and for a user interface `PRODUCT.md` and `DESIGN.md`. Records are the standards a review judges against.
- **route**: which skill comes next at each stage. Most skills of the four plugins are the owner's keystrokes, which the harness refuses to an agent, so the route names the one to type and `/ratchet:next` prints it. Read [route.md](route.md) for the stages, or when a skill is refused to you.

### The merge rule

- **conjecture**, **alternative**, **refuter**, **criticism**, **open**, **stands**: the merge rule's six words. Read [epistemology.md](epistemology.md) for where each is written down.

## The contract

The method fixes the contract. A repository's `AGENTS.md` holds only what the method leaves to the project: its purpose, its declared paths, its non-negotiables. A project may add rules and tighten limits. Loosening one is a method change and needs a superseding ADR in the project.

Every repository on the method keeps all eleven. The conformance gate checks the artefacts; the owner reads for the rest.

1. **One set of entry points.** The binding's table is the only list of gates: one row per question, the application types deciding which rows apply, each row's trigger deciding when it is asked, and the repository choosing each tool once the row is owed. CI runs the same entry points. A red `check` names the one that failed. A gate once asked is red with nothing to run. Every gate the repository bound to a tool carries a fault in `quality/bites.toml` that shows it red. Read [tiers.md](tiers.md#application-types) for the rows each application type applies.
2. **Three tiers, two aggregates.** `check` is tier 0 plus tier 1 and runs before every pull request. `check-deep` adds tier 2 and runs on the default branch and nightly. A red tier 2 there is the next piece of work.
   - A question that applies and has no tool in the stack is bound to `none` with a reason and an ADR. The row is green, and a standing criticism of the binding.
   - A question the application types leave out is bound `none: not a <type>`, and one whose trigger has not fired is bound `later: <trigger>`. Both rows stay.

   Read [tiers.md](tiers.md) before you choose a tier for a question.
3. **The ratchet.** Thresholds live in `quality/thresholds.toml` and only rise, checked against the merge base. Absolute paths need 100%. Counts that could be gamed (suppressions, test counts) are computed from both trees on every run, never stored. Each gate config the `tool-config` group of the guard manifest lists is snapshotted, so loosening it shows as a reviewed diff, and a config the group does not list is neither guarded nor recorded. Mutation testing gates on zero unexplained survivors: kill the mutant, or register it with a reason tied to the function's hash. Read [ratchet.md](ratchet.md) before you change a threshold, a listed config or the mutant register.
4. **Guards.** Guard paths are enforced by the harness, which the model cannot override, and surfaced in the diff for whatever slips past. A denial is the dispute signal. Read [guards.md](guards.md) for the manifest, the hook and the diff check.
5. **Disputes.** When a gate blocks and you believe the gate is wrong, stop on that gate and write `docs/disputes/NNNN-<slug>.md`. The owner upholds or rejects it. An upheld dispute that relaxes a rule also needs an ADR. Read [guards.md](guards.md#disputes) for the format before you write one.
6. **Records.** Each repository keeps:
   - a dated design brief with a first slice and an explicit out-list;
   - a glossary whose *Avoid* lists are banned identifiers;
   - one ADR per lasting decision, each with its refutation criterion, superseded in writing;
   - a feature brief per feature;
   - an incident record per incident, naming the bite that now catches it.

   Every machine-enforced rule cites the ADR that justifies it. Formats come from `domain-modeling`; the method adds its sections. Read [records.md](records.md) before you write a record.
7. **The pipeline.** A feature is built by roles with fixed sight lines, each working with the skill the role names: spec, a ticket check, implement and review. Once the project is `depended-on`, a critic and its fix loop run between implement and review. The orchestrator reads only the ticket, the structured result and the spec, and opens the pull request when the gates are green. The merge judge then decides who merges it. Read [pipeline.md](pipeline.md) when you work in a role.
8. **Verifiable shape.** Logic sits where every verifier reaches it. The architecture is chosen with the owner at adoption from whole options, and recorded in a decision record with its alternatives, reversal cost and refuter. Its parts are declared in the binding and checked by a tool and a fitness test. Read [architecture.md](architecture.md) before an architecture choice. Limits are hard: split the unit. A red test stays red. Suppressions and policy constants cite their rule and their ADR. Read [code.md](code.md) before you shape code the gates judge.
9. **Speed.** Fast local runs and fast CI are asked from the first commit, by concept:
   - the suite on every core;
   - one pipeline that runs the same locally and in CI;
   - the long gates sharded over a count the binding holds (1 on day one);
   - everything cached;
   - only what the change can affect run.

   The binding's Speed section says how this stack meets each, and the conformance gate checks what a machine can. Each tier has a ceiling in seconds that only falls, measured by the `budget` entry point and set at adoption from the first run. A tier over its ceiling on the default branch is the next piece of work. Read [tiers.md](tiers.md#speed) when a tier is slow.
10. **The merge rule.** A change is merged when no criticism of it is open. A test is a refuter made executable, a gate is a refuter made deterministic, a review finding is a criticism, a dispute is a counter. A criticism is answered by revision or by a counter, in writing. Before you write a counter, read [epistemology.md](epistemology.md) for where it goes.
11. **Operable by the time it matters.** A safeguard arrives with the first thing it guards, and the first one is free. The floor holds from the first commit.
    - A question about a kind of code is asked once the first instance of that kind exists: a span and a load smoke with the first code of the `entry-point` kind, a timeout and a failing-dependency test with the first outbound call, a refusal of bad configuration with the first setting read, a graceful shutdown with the first listener.
    - A question about the project's life is asked once the owner declares the event: the scans and a check that production answers once it is `deployed`, a restore drill once it holds `irreplaceable-data`, load and performance budgets once it is `depended-on`.

    The change that brings a kind is never red for it, however many files it touches. A later one that adds another while a safeguard is owed is red. Each safeguard is split across the tiers so the pull request pays only for the cheap part. Three gates ship with the method for any service, whatever it is written in: it boots and answers, it holds under a load smoke, and its telemetry arrives. The binding names the seams and what marks an instance; the conformance gate keeps the rows. Read [tiers.md](tiers.md#triggers) when a trigger fires.

## The stance

Work as a critical rationalist. Every claim about the software, yours included, is a conjecture held because it has survived the criticism aimed at it, never because it was justified. A test is a refuter made executable and a gate is a refuter made deterministic. Green means unrefuted so far, never verified, and you say so.

- Before you claim anything, write the refuter: the observation that would show the claim wrong. Then look. "Holds unless X; I checked X by Y" replaces "I'm fairly confident".
- Before you fix anything, name the alternative explanation and the check that eliminates it. A fix with no alternative on the table has refuted nothing. "Flaky", "timing" and "cache" without a mechanism fit any symptom and explain none.
- A red gate, a finding, a review comment and a tool warning are each a criticism. Answer it by revising the work or by a counter in writing. A criticism you think trivial gets a one-line counter the critic can answer. Dismissal, reweighting and silence leave it open.
- Decide between options by elimination: find the criticism that kills each, keep what survives, record the kills. Confidence and scores are not reasons.
- Report a green run by what it attempted and what it did not. Report "nothing found" with what you ran. Nothing found refutes only the attempts that would have hit.
- Expect your best work to be wrong somewhere and say where you would look first. A rule of the method you think wrong is criticised in writing, as a dispute, never in action.
- The owner is asked a product question and nothing else, whichever skill is running, a companion plugin's included.
  - A product question is a choice that decides what becomes of something of the user's (kept, deleted, merged, duplicated, overwritten, shared), who may see or do it, or what the user can do at all, where nothing the owner said or answered settles it. A choice an agent wrote down earlier (a spec, a conjecture, an **Assumed** line) does not settle it. Where the owner's words do settle it, follow them.
  - Put every product question open at a step to the owner in one message, each with your recommended answer first and why, so the owner can accept them all at once. Write each in the user's words with no method term (tier, binding, guard path, gate, conformance). Put the product questions through the harness's question tool, all in one call where it takes them, with two to four options each, or in one message with the questions numbered. `questions.toml` lists each product question a Ratchet skill asks.
  - The one exception to the product question rule is the architecture. The owner chooses the system and the tools that check it at adoption and at `/ratchet:architecture`, in the same round as that step's product questions. That skill asks again only when an arrival reopens the architecture and the review finds it no longer fits.
  - Assume, and list under **Assumed**, anything a later change reverses without loss to the user: order, wording, layout, timing, limits, and, within the architecture the owner chose, tools and how it is built (a tracker, labels, where tests reach the code, a ticket split, a draft to confirm, whether to go on). One plain line each at the end of your reply: what you chose and why in a clause, what changing it later costs (what changes, roughly how much work, and whether anything built is redone), and what the owner can say to change it. When the owner overturns a line, follow *Overturning an assumption* on the records page of this skill.

The words behind this stance are on the merge-rule page of the `ratchet:method` skill.

## Working

### Start of a session

Read `AGENTS.md`, `docs/process/binding.md` (its `## Architecture` section first, and the record it names), the glossary and the titles in `docs/adr/`. The session hook's last line is the route's next step. `/ratchet:next` prints it again, with the tracker read. Read [route.md](route.md) for the stage that step belongs to.

Done when you can name the project's parts and what each may depend on, its declared paths, its non-negotiables, its runner and the entry point for each tier.

### A change

1. Branch from the default branch.
2. Work the loop as `mattpocock-skills:tdd` describes: the test goes red for the right reason, then green, then refactor.
3. Run `check`.
4. Hand the change over:
   - **With a forge:** open one pull request with every section of the template filled and the tail of `check` pasted.
   - **With no forge:** write the change description at `.scratch/changes/<branch>.md` with every section of the template filled and the tail of `check` pasted, then run `just merge <branch>`.

Leave the merge to the merge judge: the merge job, or with no forge the `merge` entry point, merges a change the judge passes, and the owner merges the rest. Never merge by hand.

Done when the pull request is open and green, or, with no forge, the change description is written and `just merge <branch>` has run.

### A feature

1. The owner types the ticket into being with `mattpocock-skills`: `/mattpocock-skills:wayfinder` when the effort is too big for one session, `/mattpocock-skills:grill-with-docs` to decide, `/mattpocock-skills:to-spec` and `to-tickets` to write it, `/mattpocock-skills:triage` for an issue that arrived.
   - The agent-ready label these commands apply is the agreement.
2. The owner types `/ratchet:conjecture <ticket>`, for the `## Conjecture` that [records.md](records.md) describes.
3. The owner types `/ratchet:feature <ticket>` when they step away, or `/pstack:poteto-mode <ticket>` when they stay. The preflight refuses anything less before an agent exists.

Read [route.md](route.md) for the stages around these and [pipeline.md](pipeline.md) for what each role does once the feature runs.

### A decision with lasting consequences

A part of the architecture, a dependency, a representation, a tool, a rule. Write the ADR first, under the next free number, with the options considered and the criterion that would show the decision wrong. A new ADR is yours to write. A change that contradicts an ADR needs a superseding ADR first. Superseding or editing an ADR is the owner's to merge: on a branch the guard lets the edit through, and on the default branch it denies it. Read [records.md](records.md) for the ADR's sections.

### Branches you will meet

- **A gate goes red:** fix the code. Split the unit, write the test, narrow the change.
- **You believe the gate is wrong:** write a dispute, report it, and continue with the work the gate leaves open. Read [guards.md](guards.md#disputes) for the format before you write it.
- **An edit or command is denied, or asks for approval you cannot give:** that is the dispute signal. Stop on that gate there. A denied Bash call runs no part of itself, so keep a command the guard may refuse (a forge CLI call, a push, anything near a guard path) in a tool call of its own. An unrelated write chained with it is lost with it.
- **The change brings the first of a kind of code** (the first route, outbound call, setting read, listener or migration), in one file or several: it is not red for it. Say in the pull request which rows it made owed, and whether it reopened the architecture. The route makes `/ratchet:architecture` the next step when it did, then `/ratchet:safeguard`, where an agent chooses the tool, writes the recipe and the fault, and binds the row.
- **A gate is red because the change adds another instance while a row is still owed:** bind the row first, in a change of its own, or take the instance out of this change. Never bind the row `none` to get past it.
- **You see a sign of a life event** (a deploy file, a migrations directory, a ticket about users): tell the owner. Declaring it is theirs.
- **The change adds behaviour a user could meet, in a project that is `deployed`:** declare its flag in `flags.toml` with `default = false`, read it through the flag seam, and test the behaviour with the flag off. Before the project is deployed nobody meets it, and a flag is the owner's choice. A fix or a refactoring ships with no flag and says so.
- **The change adds or alters code of the `entry-point` kind** (a route, a job, a command) **once `telemetry emitted` is bound:** register it through the telemetry seam, and write the test that reads its span from the in-memory exporter.
- **The owner reports an incident:** write the incident record with them, and the fault in `quality/bites.toml` that reproduces it. Read [records.md](records.md#incident-record-docsincidentsnnnn-slugmd) for the record's sections.
- **A mutant survives:** kill it with a test, or register it as equivalent with a reason of twenty characters or more.
- **A measured value is above its threshold:** run `ratchet`, and commit the result with the change that earned it.
- **A tier runs over its ceiling:** make it faster in the same change (cache, split, raise the binding's shard count, run less), never by quarantining a test or raising the ceiling. Raising it is an ADR.
- **The change touches a user interface:** load `impeccable:impeccable` yourself. Use its `shape` before you build the surface and its `polish` before you open the pull request. `DESIGN.md` is the standard the review judges it against.
- **You need a word the glossary lacks:** read the glossary for the concept under another name first. Then add the entry with its *Avoid* list, through `domain-modeling`, before using it in code. The guard lets an added term and an added *Avoid* item through. If it denies the edit, your edit changed or removed something that was there: make it on a branch, and its merge is the owner's. Read [records.md](records.md) for the glossary's shape when you add the entry.
- **You need a dependency:** that is a design decision. Say why in the pull request body, or in an ADR when it shapes the architecture.
- **A tool reports a finding you think is wrong:** it is a criticism. Answer it in writing (a dispute, an ADR, a register entry with a reason) or fix the code.
- **A bug:** `mattpocock-skills:diagnosing-bugs` builds the loop. The method asks that the diagnosis name an alternative explanation before the fix and that the fix ship with the test that was red.

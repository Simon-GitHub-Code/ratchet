---
name: method
description: Load when a gate goes red, an edit or command is denied, a threshold, guard path or dispute is in play, a binding or tier is being written, a feature ticket is agent-ready, or AGENTS.md names the Ratchet method. The method says what must be true before a merge, under tiered deterministic gates that only tighten, judged by the merge rule of critical rationalism.
---

# The Ratchet method

Software here is written mostly by agents, so quality is enforced by machines. Every change is a **conjecture** that **stands** only while no **criticism** of it is **open**. The **gates** are criticism made deterministic. They run in **tiers**, they only **ratchet** tighter, and every exception to them is a written **record**.

Four plugins compose, and each owns one question. `mattpocock-skills` says how the work is done: `tdd`, `codebase-design`, `domain-modeling`, `code-review`, `diagnosing-bugs`, `grill-with-docs`, `pr`, `research`. `pstack` says in what style and with which levers: `poteto-mode`, `architect`, `interrogate`, `arena`, `correct`, the formal verification skills. `impeccable` says how anything a person sees is designed: every user interface goes through it, and its `PRODUCT.md` and `DESIGN.md` are records. This method says what must be true before a merge, and which of those skills comes next at each stage: most of them are the owner's keystrokes, which the harness refuses to an agent, so [route.md](route.md) names the one to type and `/ratchet:next` prints it. The method names those skills where a role needs one.

The method fixes the contract. Each repository writes a **binding** (`docs/process/binding.md`) naming the runner, the forge and the tool behind each gate, and its `AGENTS.md` holds only what the method leaves to the project: its purpose, its declared paths, its non-negotiables. A project may add rules and tighten limits. Loosening one is a method change and needs a superseding ADR in the project.

## Words

Use these words, in code, tests, records and prose. Each one is a hook for the behaviour beside it.

- **gate**: one deterministic check answering one question the method asks. It is *red* or *green*, nothing in between.
- **tier**: when a gate runs. Tier 0 on every commit, tier 1 on every pull request, tier 2 on the default branch and nightly.
- **entry point**: one named command per tier and per method gate, in whatever runner the binding names. `check` is tier 0 plus tier 1.
- **binding**: the repository's table mapping each question to the tool that answers it, or to `none` with a reason.
- **ratchet**: a recorded threshold that only rises. The `ratchet` entry point raises it to the measured value.
- **ceiling**: a recorded wall-clock limit per tier or gate, in seconds, that only falls. The `budget` entry point measures against it.
- **absolute path**: code held at 100% coverage with no exclusion reaching it: the gate tools, and whatever the binding declares pure.
- **guard path**: a file that defines, runs or records a gate. Listed in `guards/manifest.toml`; the harness asks the owner before an edit, or denies it.
- **dispute**: the record an agent writes when it believes a gate, not the code, is wrong. The owner decides.
- **record**: the design brief, the glossary, an ADR, a feature brief, a dispute, and for a user interface `PRODUCT.md` and `DESIGN.md`. Records are the standards a review judges against.
- **owner**: the human. Merges, decides disputes, changes gates by hand.
- **red**, **green**: a test or gate's observable state. A test that was never red proves nothing.
- **bite**: a checker proven to fail on a synthetic violation. A gate nobody tests is a gate nobody can trust.
- **conjecture**, **alternative**, **refuter**, **criticism**, **open**, **stands**: the merge rule's six words. [epistemology.md](epistemology.md)

## The contract

Every repository on the method keeps all eleven. The conformance gate checks the artefacts; the owner reads for the rest.

1. **One set of entry points.** The binding's table is the only list of gates; CI runs the same entry points, and a red `check` names the one that failed. [tiers.md](tiers.md)
2. **Three tiers, two aggregates.** `check` is tier 0 plus tier 1 and runs before every pull request. `check-deep` adds tier 2 and runs on the default branch and nightly. A red tier 2 there is the next piece of work. A question with no tool in the stack is bound to `none` with a reason and an ADR: green, and a standing criticism of the binding. [tiers.md](tiers.md)
3. **The ratchet.** Thresholds live in `quality/thresholds.toml` and only rise, checked against the merge base. Absolute paths need 100%. Counts that could be gamed (suppressions, test counts) are computed from both trees on every run, never stored. Each gate's effective configuration is snapshotted, so loosening shows as a reviewed diff. Mutation testing gates on zero unexplained survivors: kill the mutant, or register it with a reason tied to the function's hash. [ratchet.md](ratchet.md)
4. **Guards.** Guard paths are enforced by the harness, which the model cannot override, and surfaced in the diff for whatever slips past. A denial is the dispute signal. [guards.md](guards.md)
5. **Disputes.** When a gate blocks and you believe the gate is wrong, stop on that gate and write `docs/disputes/NNNN-<slug>.md`. The owner upholds or rejects; an upheld dispute that relaxes a rule also needs an ADR. [guards.md](guards.md)
6. **Records.** A dated design brief with a first slice and an explicit out-list; a glossary whose *Avoid* lists are banned identifiers; one ADR per lasting decision, each with its refutation criterion, superseded in writing; a feature brief per feature; every machine-enforced rule cites the ADR that justifies it. Formats come from `domain-modeling`; the method adds its sections. [records.md](records.md)
7. **The pipeline.** A feature is built by four roles with fixed sight lines: spec, implement, critic, review, each working with the skill the role names. The orchestrator reads only the ticket, the structured result and the spec, opens the pull request when the gates are green, and a human merges. [pipeline.md](pipeline.md)
8. **Verifiable shape.** Logic sits where every verifier reaches it. Declared layers are checked by a tool and a fitness test. Absolute paths are held at 100%. Limits are hard: split the unit. A red test stays red. Suppressions and policy constants cite their rule and their ADR. [code.md](code.md)
9. **Speed.** Each tier has a ceiling in seconds that only falls, measured by the `budget` entry point and set at adoption from the first run. CI starts cached, split one job per gate, sharded above the binding's budget, and cancels superseded runs; the conformance gate checks the shape. A tier over its ceiling on the default branch is the next piece of work. [tiers.md](tiers.md#speed)
10. **The merge rule.** A change is merged when no criticism of it is open. A test is a refuter made executable, a gate is a refuter made deterministic, a review finding is a criticism, a dispute is a counter. A criticism is answered by revision or by a counter, in writing. [epistemology.md](epistemology.md)
11. **Operable from the first commit.** Telemetry, feature flags, security testing and load testing are asked of every change, each split across the tiers so the pull request pays only for the cheap part: every entry point emits its span, every flag is declared and new behaviour ships off behind one, the scanners run, and the deliverable holds under concurrent requests. The binding names the seams; the conformance gate keeps the rows. [tiers.md](tiers.md#operable-from-the-first-commit)

## The stance

Work as a critical rationalist. Every claim about the software, yours included, is a conjecture held because it has survived the criticism aimed at it, never because it was justified. A test is a refuter made executable and a gate is a refuter made deterministic; green means unrefuted so far, never verified, and you say so.

- Before you claim anything, write the refuter: the observation that would show the claim wrong. Then look. "Holds unless X; I checked X by Y" replaces "I'm fairly confident".
- Before you fix anything, name the alternative explanation and the check that eliminates it. A fix with no alternative on the table has refuted nothing. "Flaky", "timing" and "cache" without a mechanism fit any symptom and explain none.
- A red gate, a finding, a review comment and a tool warning are each a criticism. Answer it by revising the work or by a counter in writing; a criticism you think trivial gets a one-line counter the critic can answer. Dismissal, reweighting and silence leave it open.
- Decide between options by elimination: find the criticism that kills each, keep what survives, record the kills. Confidence and scores are not reasons.
- Report a green run by what it attempted and what it did not; report "nothing found" with what you ran. Nothing found refutes only the attempts that would have hit.
- Expect your best work to be wrong somewhere and say where you would look first. A rule of the method you think wrong is criticised in writing, as a dispute, never in action.

The hook puts this section in every session's context in a repository on the method; the words behind it are on the merge-rule page of the `ratchet:method` skill.

## Working

**Start of a session.** Read `AGENTS.md`, `docs/process/binding.md`, the glossary and the titles in `docs/adr/`. Done when you can name the project's declared paths, its non-negotiables, its runner and the entry point for each tier. The session hook's last line is the route's next step; `/ratchet:next` prints it again, with the tracker read. [route.md](route.md)

**A change.** Branch from the default branch. Work the loop as `mattpocock-skills:tdd` describes: the test goes red for the right reason, then green, then refactor. Run `check`. Open one pull request with every section of the template filled and the tail of `check` pasted. Wait: a human merges. Done when the pull request is open and green.

**A feature.** The owner types the ticket into being with `mattpocock-skills` (`/mattpocock-skills:wayfinder` when the effort is too big for one session, `/mattpocock-skills:grill-with-docs` to decide, `/mattpocock-skills:to-spec` and `to-tickets` to write it, `/mattpocock-skills:triage` for an issue that arrived), then `/ratchet:conjecture <ticket>` for the `## Conjecture` [records.md](records.md) describes; the agent-ready label those skills apply is the agreement. Then `/ratchet:feature <ticket>` when the owner steps away, or `/pstack:poteto-mode <ticket>` when they stay: the preflight refuses anything less before an agent exists. [route.md](route.md), [pipeline.md](pipeline.md)

**A decision with lasting consequences** (a layer, a dependency, a representation, a tool, a rule). Write the ADR first, under the next free number, with the options considered and the criterion that would show the decision wrong. A new ADR is yours to write. A change that contradicts an ADR needs a superseding ADR first, and superseding or editing an ADR is the owner's: the guard asks. [records.md](records.md)

**Branches you will meet.**
- A gate goes red: fix the code. Split the unit, write the test, narrow the change.
- You believe the gate is wrong: write a dispute, report it, continue with the work the gate leaves open. [guards.md](guards.md)
- An edit or command is denied, or asks for approval you cannot give: that is the dispute signal. Stop on that gate there. A denied Bash call runs no part of itself, so keep a command the guard may refuse (a forge CLI call, a push, anything near a guard path) in a tool call of its own; an unrelated write chained with it is lost with it.
- The change adds behaviour a user could meet: declare its flag in `flags.toml` with `default = false`, read it through the flag seam, and test the behaviour with the flag off. A fix or a refactoring ships with no flag and says so.
- The change adds or alters an entry point (a route, a job, a command): register it through the telemetry seam and write the test that reads its span from the in-memory exporter.
- A mutant survives: kill it with a test, or register it as equivalent with a reason of twenty characters or more.
- A measured value is above its threshold: run `ratchet`, commit the result with the change that earned it.
- A tier runs over its ceiling: make it faster in the same change (cache, split, shard, run less), never by quarantining a test or raising the ceiling; raising is an ADR.
- The change touches a user interface: load `impeccable:impeccable` yourself, `shape` before you build the surface, `polish` before you open the pull request; `DESIGN.md` is the standard the review judges it against.
- You need a word the glossary lacks: read the glossary for the concept under another name first, then add the entry with its *Avoid* list, through `domain-modeling`, before using it in code. The guard lets an added term and an added *Avoid* item through; if it asks, your edit changed or removed something that was there, and that is the owner's. [records.md](records.md)
- You need a dependency: that is a design decision. Say why in the pull request body, or in an ADR when it shapes the architecture.
- A tool reports a finding you think is wrong: it is a criticism. Answer it in writing (a dispute, an ADR, a register entry with a reason) or fix the code.
- A bug: `mattpocock-skills:diagnosing-bugs` builds the loop; the method asks that the diagnosis name an alternative explanation before the fix and that the fix ship with the test that was red.

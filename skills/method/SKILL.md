---
name: method
description: The Ratchet method, the founding contract for building and verifying software with agents under tiered deterministic gates and critical rationalism. Load at the start of any work in a repository whose CLAUDE.md names it; when a gate goes red; when an edit or command is denied; before a design decision, a new dependency or a new term; before writing a test, a review or a bug diagnosis; and before running the feature pipeline.
---

# The Ratchet method

Software here is written mostly by agents, so quality is enforced by machines and never trusted. Every claim about the software is a **conjecture**: held because it has survived **criticism**, carried together with the criticism that would refute it. The **gates** are that criticism made deterministic. They run in **tiers**, they only **ratchet** tighter, and every exception to them is a written **record**.

The method fixes the contract. Each repository writes a **binding** (`docs/process/binding.md`) naming the tools behind each gate, and its `CLAUDE.md` holds only what the method leaves to the project: its purpose, its layers, its non-negotiables. A project may add rules and tighten limits. Loosening one is a method change and needs a superseding ADR in the project.

## Words

Use these words, in code, tests, records and prose. Each one is a hook for the behaviour beside it.

- **gate**: one deterministic check with one recipe name. It is *red* or *green*, nothing in between.
- **tier**: when a gate runs. Tier 0 on every commit, tier 1 on every pull request, tier 2 on main and nightly.
- **binding**: the repository's table mapping each gate the method requires to the tool that runs it.
- **ratchet**: a recorded threshold that only rises. `just ratchet` raises it to the measured value.
- **absolute layer**: code held at 100% coverage with no exclusion reaching it: the pure core and the gate tools themselves.
- **guard path**: a file that defines, runs or records a gate. Listed in `guards/manifest.toml`; the harness asks the owner before an edit, or denies it.
- **dispute**: the record an agent writes when it believes a gate, not the code, is wrong. The owner decides.
- **record**: the design brief, the glossary, an ADR, a feature brief, a dispute. Records are the standards a review judges against.
- **owner**: the human. Merges, decides disputes, changes gates by hand.
- **red**, **green**: a test or gate's observable state. A test that was never red proves nothing.
- **bite**: a checker proven to fail on a synthetic violation. A gate nobody tests is a gate nobody can trust.
- **pure core**: the innermost layer: no I/O, no clock, no randomness, no environment, immutable values, every public function contracted.
- **port**, **adapter**, **fake**: an interface declared by the layer that needs it, one implementation per external system, one test double per port.
- **conjecture**, **attempted refutation**, **pending criticism**, **unrefuted so far**, **rival**, **crucial test**, **error correction**: the stance. See [epistemology.md](epistemology.md).

## The contract

Every repository on the method keeps all ten. The conformance gate checks the artefacts; the owner reads for the rest.

1. **One command contract.** The root `justfile` is the only list of gates; `just` lists every recipe, CI runs the same recipes, and a red `just check` names the one recipe that failed. [tiers.md](tiers.md)
2. **Three tiers, two aggregates.** `just check` is tier 0 plus tier 1 and runs before every pull request. `just check-deep` adds tier 2 and runs on main and nightly. A red tier 2 on main is the next piece of work. [tiers.md](tiers.md)
3. **The ratchet.** Thresholds live in `quality/thresholds.toml` and only rise, checked against the merge base. Absolute layers need 100%. Counts that could be gamed (suppressions, test counts) are computed from both trees on every run, never stored. Each gate's effective configuration is snapshotted, so loosening shows as a reviewed diff. Mutation testing gates on zero unexplained survivors: kill the mutant, or register it with a reason tied to the function's hash. [ratchet.md](ratchet.md)
4. **Guards.** Guard paths are enforced by the harness, which the model cannot override, and surfaced in the diff for whatever slips past. A denial is a stop sign, not a puzzle. [guards.md](guards.md)
5. **Disputes.** When a gate blocks and you believe the gate is wrong, stop and write `docs/disputes/NNNN-<slug>.md`. The owner upholds or rejects; an upheld dispute that relaxes a rule also needs an ADR. [guards.md](guards.md)
6. **Records.** A dated design brief with a first slice and an explicit out-list; a glossary whose *Avoid* lists are banned identifiers; one ADR per lasting decision, superseded in writing; a feature brief per feature; every machine-enforced rule cites the ADR that justifies it. [records.md](records.md)
7. **The pipeline.** A feature is built by four roles with fixed sight lines: spec, implement, critic, review. The orchestrator reads only the brief, the structured result and the spec, opens the pull request when the gates are green, and a human merges. [pipeline.md](pipeline.md)
8. **Code shape.** Dependencies point inward and a tool checks it. The core is pure and contracted. Every port has a fake; ambient inputs arrive as values. Every value type is immutable, has a generator and a property test. Logic sits where every verifier can reach it. Size and complexity limits are hard: split the function. Comments say why, cite an ADR, or direct a tool. [code.md](code.md)
9. **Formal specs.** A state machine whose rules are cheap to get wrong and expensive to discover late is specified and model-checked before it is coded; the code names its spec and its tests mirror it. [formal.md](formal.md)
10. **The stance.** Fallible all the way down: a test is an attempted refutation, green means unrefuted so far, a review is criticism, and a pending criticism blocks. A criticism is addressed by revision, counter-criticism or accepting defeat, never by dismissal or reweighting. Decide between rivals by eliminating the ones a criticism kills, never by weighing confidence. [epistemology.md](epistemology.md)

## Working

**Start of a session.** Read `CLAUDE.md`, `docs/process/binding.md`, `GLOSSARY.md` and the titles in `docs/adr/`. Run `just` to see the recipes. Done when you can name the project's layers, its non-negotiables and the recipe for each tier.

**A change.** Branch from main. Write the test; watch it go red for the right reason (missing behaviour, never an import or fixture error); make it green; refactor. Run `just check`. Open one pull request with every section of the template filled and the tail of `just check` pasted. Wait: a human merges. Done when the pull request is open and green.

**A feature.** Write `docs/features/<slug>/brief.md` as [records.md](records.md) describes, then run the `feature` workflow the plugin ships (`/ratchet:feature`). [pipeline.md](pipeline.md)

**A decision with lasting consequences** (a layer, a dependency, a representation, a tool, a rule). Write the ADR first, with the options considered and the criterion that would show the decision wrong. A change that contradicts an ADR needs a superseding ADR first. [records.md](records.md)

**Branches you will meet.**
- A gate goes red: fix the code. Split the function, write the test, narrow the change.
- You believe the gate is wrong: write a dispute, report it, continue with work the gate does not block. [guards.md](guards.md)
- An edit or command is denied, or asks for approval you cannot give: that is the dispute signal. Stop there.
- A mutant survives: kill it with a test, or register it as equivalent with a reason of twenty characters or more.
- A measured value is above its threshold: `just ratchet`, commit the result with the change that earned it.
- You need a word the glossary lacks: add the entry, with its *Avoid* list, before using it in code.
- You need a dependency: that is a design decision. Say why in the pull request body, or in an ADR when it shapes the architecture.
- A tool reports a finding you think is wrong: it is a criticism. Answer it in writing (a dispute, an ADR, a register entry with a reason) or fix the code. Silence is not an answer.

## Reference

- [epistemology.md](epistemology.md): the stance, the directives and the vocabulary of critical rationalism as it applies to building software.
- [tiers.md](tiers.md): what each tier contains, the gate catalogue, time budgets, what sits outside the tiers.
- [ratchet.md](ratchet.md): thresholds, absolute layers, computed counts, snapshots, the mutation register.
- [guards.md](guards.md): the manifest, the hook's decisions, the diff check, owner override, and the dispute procedure with its template.
- [pipeline.md](pipeline.md): the four roles, their sight lines and schemas, the fix loop, the orchestrator's steps, the pull request template.
- [records.md](records.md): formats for the design brief, glossary, ADRs, feature briefs and disputes, and the rejection ADR pattern.
- [code.md](code.md): the technology-neutral code rules and their default limits.
- [formal.md](formal.md): when and how a design gets a formal spec.

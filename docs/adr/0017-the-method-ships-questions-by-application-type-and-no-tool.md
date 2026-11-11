---
status: accepted
---
# The method ships questions by application type, and no tool for any stack

The plugin shipped two stack presets under `skills/adopt/presets/`, `python-uv` and `typescript-pnpm`: for each, the recipes, the binding's Tool cells, the manifest paths, the thresholds, the CI steps, the tool configuration and a fitness-test scaffold. Adopt merged the matching preset into the templates and confirmed it with the owner. Three things were observed.

- A preset was read as known good and was not. The two trial projects of 2026-11-11 were adopted from these presets and ended with every bound gate green, and ADR 0012 records what that green hid: a performance row that was a micro-benchmark which could not fail in one stack and was red for want of a benchmark in the other, and an "image scan" over the source tree of a service that ships as a container.
- A preset covers the stacks someone wrote one for. The Rust trial of 2026-11-08 (ADR 0011) had none and went through an interview; every other stack would have too.
- A tool named in the plugin ages at the plugin's pace. A trial adoption for this record chose `cargo nextest --partition count:m/n` from memory and found, on looking it up, that the tool had deprecated that mode nine months earlier.

The owner's statement of the product is that the plugin enforces the concepts, and the stack that enforces them is the repository's.

We decided four things.

1. **The method ships the questions by application type.** `skills/method/application-types.toml` names six types (`service`, `web-ui`, `app`, `cli`, `job`, `library`) and, for the twelve questions that are not asked of every type, the types each is asked of. A binding declares its types on one line, `- Application types: service, web-ui`. A row that applies names a tool, or `none` with a reason and an ADR the repository holds. A row that applies to none of the declared types says `none: not a <type>`, naming a type the question is asked of, and needs no ADR. A row is never dropped. The conformance gate is red on a missing or unknown type, an empty Tool cell, a row that applies bound to `none` without its reason and ADR, and a dropped row. A type decides which questions apply and never which tool answers one.
2. **The presets are deleted, and nothing replaces them.** The adopting agent recognises the stack, chooses a tool for each row that applies, checks the choice against current documentation where the session can reach it, and takes every version from the package registry at install time. The owner is asked one question about tools: accept the proposal, or name the ones they want.
3. **What ships is what works on any stack.** The templates stay stack-neutral. The method's own gates stay bound in them, and the three service gates of `scripts/with_service.py`, `scripts/load_smoke.py` and `scripts/telemetry_sink.py` are bound for `smoke`, `load-smoke` and `telemetry-e2e` with a boot command and a URL to fill.
4. **The guarantee moves from the recipe to the repository.** A preset's promise was that its recipes were good. The promise now is that the adopted repository has shown each of its gates going red on a fault and on an empty collection: its `bite` entry point. `application-types.toml` marks the questions whose gate judges a collection, for that proof to read. What the presets taught is kept as rules any recipe must keep, in `skills/method/tiers.md`.

Two earlier records spoke of presets, and are read as follows; their decisions stand. In ADR 0011, "every preset puts the dependency manifest and its lockfile in the `tool-config` group" is now a line of the adopt skill, and the refuter's "a test-count gate in every preset" is the `test count did not fall` row every binding already carries. In ADR 0012, "OpenTelemetry is the default the presets name" is now the example the method's tier tables name. Decided 2026-11-11.

## Refutation criterion

This is wrong if adoption without presets binds weaker gates or costs the owner more. Adopt a Python repository on uv and a TypeScript one on pnpm afresh, and compare each row with the preset this record deleted (the tree at commit `50c291d`): if either binds a row to `none`, or to a tool with a narrower rule set, where the preset bound a tool, in two or more rows, the agent's choice is worse than a maintained recipe and presets return as maintained artefacts. It is also wrong if adoption asks the owner more than the two dialogs it asked before, or if two adoptions on one stack on the same day bind tools so different that a reader cannot move between the repositories. And the six types are wrong on the first adoption that has to write an ADR to say a row does not apply to what it plainly is not: that row's types are narrowed, or a type is added.

## Considered options

- Keep the presets and add more: rejected. Each one is a tool matrix to keep current against registries the plugin does not watch, the trials showed their gates passing vacuously, and a repository on any other stack gets nothing.
- Demote the presets to examples: rejected. An example in the plugin is read by an agent as the answer, ages exactly as a preset does, and still covers two stacks.
- Ship gates per stack, as code the plugin tests: rejected. That is the same matrix with more to maintain. What the plugin ships as code is what needs no stack: the method's own gates and the three service gates, which boot a deliverable by any command.
- No types, every question asked of everything, each `none` with its ADR: rejected. A library would cite an ADR on eleven rows to say it is not a service, which is the paperwork ADR 0012's refuter warns of.
- Types that choose the tools: rejected. A type says what the software is, which changes rarely; a tool is a choice within a stack, which changes yearly.

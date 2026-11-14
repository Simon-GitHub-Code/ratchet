---
status: accepted
---
# The plugin names no stack tool, and the binding holds each stack fact

Three of the plugin's gates carried a list of tools. `scripts/installs.py` knew four package managers, their lockfiles and their frozen installs. `ratchet_guards/dependencies.py` read five lockfile formats and three manifest formats. `scripts/suppressions.py` held twelve directive spellings for a handful of languages. Each list was right on the day it was written and wrong for the next stack. Each also steered the agent that adopts the method: an agent reading a list of examples binds toward the examples.

ADR 0017 already decided that the method ships questions by application type and no tool. This decision carries that rule to the facts a gate needs about a stack, not only the tool that answers a question.

## Decision

**No script, hook, template or skill of the plugin names a language, a package manager, a lockfile, a linter, a directive spelling or any other tool whose choice depends on the stack under test.** Python as the plugin's own language and `just` as its task runner are the plugin's, and stay. Formats that are not tied to a language (Cobertura, LCOV, JUnit XML, TOML, JSON, YAML) may be named. This holds for comments, docstrings, error messages and templates as well: an example in the text an agent reads is a default.

**A per-stack fact is a row or a section of the project's binding, written at adoption by the project's own agent from what it knows of the tools it chose and what the human decided.** Three sections carry the facts the gates needed: `## Install` (ADR 0041), `## Dependencies` (ADR 0042) and `## Suppressions` below. The adopt skill says to fill each in plain method terms, with no example, and the template holds placeholders, not defaults.

**A gate is red when the stack needs a row that is missing or malformed.** A deterministic check, never the agent's care, holds each section: the section exists, every row is filled, every pattern parses, and the section agrees with the justfile or the Gates table it describes. The agent cannot skip a row, and the plugin cannot say which tool to use.

**`## Suppressions` is a table of kinds.** Each row has a name, an extended regular expression and a rule: `rises` (the count of matching lines may not rise against the merge base) or `never` (no line may match, as for a marker that narrows a suite). The catalogue marks the questions a directive can silence (`directives = true` in `skills/method/application-types.toml`); a second table says, for each of those questions whose Gates row is bound to a tool, which kinds silence it or `none` with a reason. That is the deterministic signal for a missing kind: a bound lint, type, coverage, mutation or test question with no line is red, and no tool is named to decide it. The merge base's table judges a change, so a change cannot loosen its own judge, and the binding file itself is not searched, since it quotes every pattern. A pattern that the search command rejects is red, because it would count nothing and say nothing.

**The upgrade writes what the project's own tree states and owes the rest.** An existing adoption gets the three sections as findings (ADR 0029, ADR 0037). The upgrade writes a section only from facts the tree itself states, and otherwise records an owed item for the agent that names the check which does not run meanwhile.

## Known residue

The plugin outside the three gates still names tools: the mutation survivor reader (`scripts/mutation_survivors.py`, per-tool converters), the coverage reader's docstring, `scripts/project_bite.py`'s examples, the shell and interpreter classifiers of the guard hook, and `scripts/prose.py`'s extension list. The coverage reader's docstring and exclusion pattern (`scripts/coverage_groups.py`), the bite proof's docstring examples, the `with_service.py` docstring, the planted fixture of a mutation report in `scripts/bite.py`, and the parallel and shard paragraphs of `skills/method/tiers.md` name tools too. They are not moved here. The guard hook's lists are the security boundary of a harness, not a fact about the stack under test, and moving them into a row the agent writes would let a project loosen them.

## Refutation criterion

This is wrong if a stack adopts the method and one of the three sections, filled as the template asks, makes a gate judge worse than the list it replaced did. The open attempt is a directive an agent leaves out of the table: bind a lint tool, list one kind, add a second kind of directive in the code, and if the gate stays green then a row-by-row signal does not hold the table to the tool's whole directive set, and the catalogue needs a check the agent runs against the tool's own documentation. It is also wrong if a project's agent, handed a template with no examples, writes a table that is wrong more often than the old list was, which the first ten adoptions after this one show in their `suppressions` and `guard-diff` runs.

## Considered options

- **Keep the lists and add more stacks.** Rejected: it is the failure this ADR names, and it steers every adopter toward the listed tools.
- **Ship the lists as data files the binding overrides.** Rejected: a default is still an example in the agent's context, and a project that never overrides inherits a stack it does not use.
- **Ask the agent in prose and check nothing.** Rejected: ADR 0039 showed that a rule an agent can satisfy by care alone is skipped. Each section has a gate that is red without it.
- **Detect the stack in the plugin from files that name it.** Rejected: that is the same knowledge, moved into a detector.

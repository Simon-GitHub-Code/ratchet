---
name: fill
description: Write the rows an upgrade left owed that an agent can write from the repository alone, with nothing asked of the owner.
disable-model-invocation: true
---

# Fill what the upgrade owes

Write the binding rows an upgrade left owed that the repository alone answers. Load the `ratchet:method` skill first.

The owner is asked nothing: every row is yours to propose and write, from your knowledge of the tools the repository uses and from the tree.

## Steps

1. **List it.**
   - Run `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/owed.py" --fill-first`.
   - Run `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/conformance.py"` for the same findings with the fix each one names.

   Done when you hold every owed line and the fix conformance names for it.

2. **Branch.** On the default branch with a clean tree, make the branch `chore/fill-owed`.

   Done when `chore/fill-owed` is checked out.

3. **Read the repository, then write each row.** For each section the list names, read the template's text for it in `${CLAUDE_PLUGIN_ROOT}/skills/adopt/templates/docs/process/binding.md`. Then write its rows in the binding from what the tree and its tools show:
   - **Life event signs:** the files that mark a deployment or a store of records.
   - **Triggers:** a row of `## Triggers` for each kind the `trigger_kinds` finding names, with this stack's Paths and a Pattern where a path is not enough, or `none: <reason>` for a kind this project cannot have. Prove each with `triggers.py --count`, as [adopt step 4](../adopt/SKILL.md) says.
   - **Image pins:** the files that name a container image, and the pattern that finds the reference.
   - **Suppressions:** the directives the bound tools read, including the kinds that silence the coverage question.
   - **Comments:** the Comments table, one row per kind of tracked file other than Markdown, `listed` or `none: <reason>`, and the `comments-list` recipe as the section says. Every directive the files carry goes into Suppressions, `kept` where it silences no gate. Then run `just comments --record`: this change binds the lister, so it reads every file a `listed` row matches and records what it finds as owed. No later change may record.
   - **Documents:** one row of `## Documents` per kind of tracked Markdown file that no kind matches yet, `never` or `pass`. Give the `depended-on` row of `## Life event signs` the forge's files a maintainer writes for strangers.
   - **Install:** whether an install comes first.
   - **Dependencies:** how dependencies are read.
   - **Suites:** the suites table under `## Tests`, one row per suite: the gate that runs it whole, the globs of its test files, and the recipe that runs given files of it, or `none: <reason>`. In the same pass, bind the Gates row `changed tests run` to `just tests-changed` and add `tests-changed` to the justfile's `tier1_gates`. A Gates row bound before its table exists turns tier 1 red.
   - **A mutation recipe that passes a tool's own report:** rewrite the recipe so it converts that report to the neutral format that `${CLAUDE_PLUGIN_ROOT}/scripts/mutation_survivors.py --help` prints. Write the register's entries again for the new identities. Where the mutation tool lets a recipe order each mutant's tests and stops at the first failure, run likely killers (such as sibling unit tests) first and quickest first, while keeping test isolation intact so verdicts do not change.
   - **Static security analysis scope:** state in the binding whether the gate reads product code or every file and why (when narrowed to product code, record what is given up and which gates still read test files, such as secrets scan, typed lint, and import rules), and keep exclusions visible in the recipe command rather than in a hidden ignore file that silently replaces defaults.
   - **Speed profile commands:** record any slow gate's profiling command under `## Speed` `- Profile:` as `gate: command` in backticks using the tool's own timing flag.

   Name no tool in a page the plugin ships. The binding is the project's.

   Done when every section the list names has its rows, and every mutation recipe it names converts to the neutral format.

4. **Prove it.**
   - Run `conformance.py` until the findings you filled are gone.
   - Run each gate whose section you wrote (`pins`, `suppressions`, `coverage`, the mutation gate) on the tree, by its entry point: the command in the Entry point column of the gate's row in the binding, such as `just pins`. A row that finds nothing in a tree that has the thing is wrong.

   Done when `conformance.py` prints none of the findings you filled, and each gate you ran finds what the tree has.

5. **Settle the ledger.** Run `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/upgrade.py" --apply`.

   Done when `owed.py --fill-first` prints nothing.

6. **Land it.**
   - Commit the change on the branch.
   - With a forge, push and open the pull request. When a change that moves the plugin pin lands on the default branch while it is open, move the ledger onto the new pin, or `ratchet-lowered` is red on every `comments` entry. Fetch and merge `origin/<default>` in. Where `quality/owed.toml` conflicts, take the default branch's with `git checkout origin/<default> -- quality/owed.toml`. Run `just comments --record` again, so the `comments` entries are owed since the new pin. Run step 5's `upgrade.py --apply` again, to drop the entries this change already settles.
   - With no forge, write the change description at `.scratch/changes/<branch>.md` (the branch's `/` written as `-`) and run `just merge <branch>`.

   The binding is a guard path, so the merge judge leaves the merge to the owner, who reads one change.

   Done when the change is committed on `chore/fill-owed` and its pull request is open, or, with no forge, its change description is written and `just merge <branch>` has run.

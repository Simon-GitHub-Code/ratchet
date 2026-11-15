---
name: fill
description: Write the rows an upgrade left owed that an agent can write from the repository alone, with nothing asked of the owner.
disable-model-invocation: true
---

# Fill what the upgrade owes

Write the binding rows an upgrade left owed that the repository alone answers. The route names this step right after an upgrade, and no feature and no drain starts until it is done. Load the `ratchet:method` skill first.

An upgrade adds sections to the binding whose rows state what this project's own tree and tools say, and records each as owed. The owner is asked nothing: every row is yours to propose and write, from your knowledge of the tools the repository uses and from the tree.

## Steps

1. **List it.**
   - Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/owed.py" --fill-first`. Each line is the check, the file and what is missing.
   - Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/conformance.py"` for the same findings with the fix each one names.

   Done when you hold every owed line and the fix conformance names for it.

2. **Branch.** On the default branch with a clean tree, make the branch `chore/fill-owed`.

   Done when `chore/fill-owed` is checked out.

3. **Read the repository, then write each row.** For each section the list names, read the template's text for it in `${CLAUDE_PLUGIN_ROOT}/skills/adopt/templates/docs/process/binding.md`. Then write its rows in the binding from what the tree and its tools show:
   - **Life event signs:** the files that mark a deployment or a store of records.
   - **Image pins:** the files that name a container image, and the pattern that finds the reference.
   - **Suppressions:** the directives the bound tools read, including the kinds that silence the coverage question.
   - **Install:** whether an install comes first.
   - **Dependencies:** how dependencies are read.
   - **A mutation recipe that passes a tool's own report:** rewrite the recipe so it converts that report to the neutral format that `${CLAUDE_PLUGIN_ROOT}/scripts/mutation_survivors.py` documents. Write the register's entries again for the new identities.

   Name no tool in a page the plugin ships. The binding is the project's.

   Done when every section the list names has its rows, and every mutation recipe it names converts to the neutral format.

4. **Prove it.**
   - Run `conformance.py` until the findings you filled are gone.
   - Run each gate whose section you wrote (`pins`, `suppressions`, `coverage`, the mutation gate) on the tree, by its entry point: the command in the Entry point column of the gate's row in the binding, such as `just pins`. A row that finds nothing in a tree that has the thing is wrong.

   Done when `conformance.py` prints none of the findings you filled, and each gate you ran finds what the tree has.

5. **Settle the ledger.** Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/upgrade.py" --apply`. It drops the entries whose findings are gone and writes nothing else owed.

   Done when `owed.py --fill-first` prints nothing.

6. **Land it.**
   - Commit the change on the branch.
   - With a forge, push and open the pull request.
   - With no forge, write the change description at `.scratch/changes/<branch>.md` (the branch's `/` written as `-`) and run `just merge <branch>`.

   The binding is a guard path, so the merge judge leaves the merge to the owner, who reads one change.

   Done when the change is committed on `chore/fill-owed` and its pull request is open, or, with no forge, its change description is written and `just merge <branch>` has run.

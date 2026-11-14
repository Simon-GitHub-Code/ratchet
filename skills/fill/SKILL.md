---
name: fill
description: "Write the rows an upgrade left owed that an agent can write from the repository alone: the binding's Life event signs, Image pins, Suppressions, Install and Dependencies sections and a mutation recipe converted to the neutral format. Reads the tree and its tools, proposes each row, writes it, and drops the entries it settled. Nothing is asked of the owner."
disable-model-invocation: true
---

# Fill what the upgrade owes

Load the `ratchet:method` skill first. An upgrade of the plugin adds sections to the binding whose rows state what this project's own tree and tools say, and records each as owed (ADR 0037, ADR 0050). The route names this step right after an upgrade, and no feature and no drain starts until it is done. The owner is asked nothing (ADR 0034): every row is yours to propose and write, from your knowledge of the tools the repository uses and from the tree.

## Steps

1. **List it.** Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/owed.py" --fill-first`. Each line is the check, the file and what is missing. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/conformance.py"` for the same findings with the fix each one names.
2. **Branch.** On the default branch with a clean tree, make the branch `chore/fill-owed`.
3. **Read the repository, then write each row.** For each section the list names, read the template's text for it in `skills/adopt/templates/docs/process/binding.md` and write the rows in the binding, from what the tree and its tools show: the files that mark a deployment or a store of records (Life event signs), the files that name a container image and the pattern that finds the reference (Image pins), the directives the bound tools read (Suppressions, including the kinds that silence the coverage question), whether an install comes first (Install), how dependencies are read (Dependencies). A mutation recipe that passes a tool's own report is rewritten to convert it to the neutral format that `scripts/mutation_survivors.py` documents, and the register's entries are written again for the new identities. Name no tool in a page the plugin ships; the binding is the project's.
4. **Prove it.** Run `conformance.py` until the findings you filled are gone, and each gate whose section you wrote (`pins`, `suppressions`, `coverage`, the mutation gate) on the tree. A row that finds nothing in a tree that has the thing is wrong.
5. **Settle the ledger.** Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/upgrade.py" --apply`: it drops the entries whose findings are gone and writes nothing else owed.
6. **Land it.** Commit the change on the branch and hand it to the merge as any change to a guard path. The binding is one: the merge judge leaves the merge to the owner, who reads one change.

Done when `owed.py --fill-first` prints nothing.

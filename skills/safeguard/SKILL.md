---
name: safeguard
description: Bind the safeguards this repository now owes. Given a life event, declare it first.
disable-model-invocation: true
---

# Bind what is owed

Bind every **owed** row in one change, so the owner's next change is never the one that stalls. Load the `ratchet:method` skill first: it defines owed, bound and bite.

`/ratchet:safeguard <event>` (`deployed`, `irreplaceable-data` or `depended-on`) declares that life event, then binds what it owes with the rest.

The owner is asked nothing. A tool the architecture record names was their choice at adoption. Every other choice is yours: the tool, how its recipe is written, where a check lives, what its fault plants.

- **Assume** `safeguard.tools`: the tool step 2 chooses for each row, with the one you passed over. Step 6 lists it.

## Steps

1. **Find what is owed.**
   - Start on the default branch with a clean tree. Make the branch `chore/safeguard-<what>`, where `<what>` is the kinds or the event in a word or two.
   - Given an event, write it on the binding's `- Life events:` line with the Edit tool, replacing `none`. The binding is a guard path, so the owner's merge of this change is the declaration.
   - Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/triggers.py" --root . --json` in one Bash call. It lists each owed row: the question, its trigger, the kind or event, and how many instances.
   - When it lists nothing, say so, print the route as step 6 does, and stop.

   Done when you hold the list.

2. **Choose a tool for each row.**
   - **The architecture record names a tool for the row's concept:** take it. The owner chose it, so it is not an assumption.
   - **The row is a shipped gate** (`smoke`, `load-smoke`, `telemetry-e2e`, `config-refusal`, `graceful-stop`, `prod-probe`): fill its `bind:` words; it needs no tool. The first entry point of a service also fills `boot`, `ready`, `service` and `flags_on` as [adopt step 9](../adopt/SKILL.md) says, with its table for the other types.
   - **Any other row:** choose as [adopt step 5](../adopt/SKILL.md) says. What is there wins. Look each tool up now, in its current documentation. Take its version from the registry, through the package manager. Prefer a tool that already answers another row.

   Print one table: each row, its trigger and instances, the tool, and where you checked it.

   Done when every row has a tool, or `none` with the reason this project will never have the property.

3. **Bind each row.** Read [what a recipe must get right](../method/tiers.md#what-a-recipe-must-get-right) first. Then, for each row, in this order:
   1. **Install.** Install the tool through the package manager and add what it writes to `.gitignore`. A tool adoption provisioned, such as the test runner the binding's Tests section names, is already installed: write that runner into the recipe (`{{test_runner}}` with its shard flags).
   2. **Recipe.** Replace the recipe's `@echo "later: <trigger>"` body in the `justfile` with the real command. The recipe is red when it finds nothing to run; the trigger fired, so what it judges has a member.
   3. **Binding.** Change the row's Tool cell in `docs/process/binding.md` from `later: <trigger>` to the tool, and fill its threshold source. A row whose Telemetry or Feature flags section was one line says how it is met now.
   4. **Bite.** Add the row's `[[bite]]` to `quality/bites.toml`: the smallest fault the gate exists to catch, and the words it prints for it. Add a second bite that empties a collection, and a `proof = "<name>"` bite where the question requires one. A fault inside a file other changes also edit (a manifest, a lockfile) is a `replace`, not a `patch`. Run `just bite-one <gate>` and see it red with the fault and green without. Any verdict but `bites` is a defect of the recipe or the fault: fix that, and leave `expect` as it is.
   5. **Guard.** Add the tool's config path to the `tool-config` paths of `guards/manifest.toml`. The hook and the merge judge read the manifest as the default branch holds it, so the path is guarded once the owner merges this change. `.claude/settings.json` needs no change.

   The `justfile`, the binding, `quality/` and the manifest are guard paths: on this branch they take no prompt, and the merge judge sends the change to the owner.

   Done when no owed row is `later:` and each bound gate bites.

4. **Record the choice.** Write an ADR under the next free number, one line per row: the question, the tool and its version, where you checked it, and the alternative you passed over. A new ADR passes the guard without asking.

   Done when the ADR exists and `triggers.py --json` prints `[]`.

5. **Prove it and open the change.**
   - Run `conformance` and `check`, both to green.
   - Commit with the subject `chore(method): safeguard <what>`, at most 72 characters; the `commits` gate accepts it.
   - Push and open the pull request with every template section filled: under Gates the table of step 2 and each `just bite-one` verdict, under Criticism every tool you could not check.
   - With no forge, write the change description at `.scratch/changes/<branch>.md` and run `just merge <branch>`.

   The change touches guard paths, so the merge judge leaves the merge to the owner, and says so.

6. **Hand over.** Write for the owner, in plain words with no method term:
   - which rows are bound and by what, and that the next instance of each kind is now judged by its gate;
   - under **Assumed**, one line per tool you chose: why it, the one you passed over, what changing it later costs (what changes, roughly how much work, whether anything built is redone), and what the owner can say to change it;
   - when a tool has config, name the path you added to the manifest.

   An **Assumed** line carries the real names in place of the brackets, such as ``Security checks on new code: <the tool you chose>, current and it reads this language; <the one you passed over> was the other choice. Changing later: a change of its own that an agent makes while you are away (the tool swapped, its settings written, the check rewired and shown to catch a planted mistake again, a decision record). It touches protected files: the list of checks, the list of planted mistakes, the tool's settings and their saved copy, the record of which tool runs each check, and the package list and its lock file where the tool is installed through them. The agent puts the new tool's settings file on the protected list in place of the old one, and the merge is yours. Nothing built is redone; the code changes only where the new tool objects to it. Say "use another" to switch.``

   The last lines are the route: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .` in one Bash call and print its lines as they are.

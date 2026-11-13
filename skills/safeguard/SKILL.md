---
name: safeguard
description: "Bind the safeguards a repository now owes: the Gates rows still `later:` whose kind of code has arrived or whose life event is declared. Chooses a current tool for each, writes the recipe and a fault that shows it bite, and opens one change. With a life event as the argument, declares it first."
disable-model-invocation: true
---

# Bind what is owed

Load the `ratchet:method` skill first. A question is asked once its trigger fires (ADR 0028): the change that brings a kind of code is free, however many files it touches (ADR 0030), and a later one that adds another instance while a row it fires is still `later:` is red. The route names `/ratchet:safeguard` when a row is owed. This skill binds those rows, so the owner's next change is never the one that stalls.

`/ratchet:safeguard` binds every owed row. `/ratchet:safeguard <event>` (`deployed`, `irreplaceable-data` or `depended-on`) declares that life event first, then binds what it owes with the rest.

## What the owner is asked

One question, through the question tool, for every row at once: accept the proposed tools, or name others for any row. It is `tooling`; record it in one Bash call: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/questions.py" record --skill safeguard --key safeguard.tools --kind tooling --recommended <yes|no> --root .`. A key `## Standing answers` in `AGENTS.md` lists is not asked. Nothing else is asked: how a recipe is written, where a check lives and what its fault plants are yours, and go in the hand-over.

## Steps

1. **Check the ground.** On the default branch with a clean tree, make the branch `chore/safeguard-<what>`, where `<what>` is the kinds or the event in a word or two. With an event as the argument, write it on the binding's `- Life events:` line (replacing `none`), with the Edit tool. The binding is a guard path, so the owner merges this change, and their merge is the declaration. Then run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/triggers.py" --root . --json` in one Bash call. It lists each owed row: the question, its trigger, the kind or event, and how many instances. Nothing owed: say so, print the route and stop. Done when you hold the list.

2. **Choose a tool for each row.** As adoption does ([adopt step 5](../adopt/SKILL.md)): what is there wins; look each tool up now, in its current documentation, and take versions from the registry through the package manager, never from memory and never from this plugin (ADR 0017); prefer a tool that already answers another row. A shipped gate (`smoke`, `load-smoke`, `telemetry-e2e`, `config-refusal`, `graceful-stop`, `prod-probe`) needs only its `bind:` words filled; the first entry point of a service also fills `boot`, `ready`, `service` and `flags_on` as [adopt step 9](../adopt/SKILL.md) says, with its table for the other types. Print one table: each row, its trigger and instances, the tool, and where you checked it. Done when every row has a tool, or `none` with a reason for a property this project will never have.

3. **Ask the one question**, then act on it with no further question. A named tool that cannot answer its row: bind your proposal and say why in the hand-over.

4. **Bind each row.** Read [what a recipe must get right](../method/tiers.md#what-a-recipe-must-get-right) first. For each row, in this order:
   - Install the tool through the package manager, and add what it writes to `.gitignore`.
   - Replace the recipe's `@echo "later: <trigger>"` body in the `justfile` with the real command. A gate with nothing to run is red, and the trigger fired, so what it judges has a member.
   - Change the row's Tool cell in `docs/process/binding.md` from `later: <trigger>` to the tool, and fill its threshold source. A row whose Telemetry or Feature flags section was one line says how it is met now.
   - Add its `[[bite]]` to `quality/bites.toml`: the smallest fault the gate exists to catch and the words it prints for it, a second bite that empties a collection, and a `proof = "<name>"` bite where the question requires one. Run `just bite-one <gate>` and see it red with the fault and green without. A verdict other than `bites` is a defect of the recipe or the fault: fix it, never loosen `expect`.
   - Add the tool's config path to the `tool-config` paths of `guards/manifest.toml`. The hook and the merge judge read the manifest as the default branch holds it, so the path is guarded once the owner merges this change (ADR 0033). `.claude/settings.json` needs no change.

   The `justfile`, the binding, `quality/` and the manifest are guard paths: on this branch they take no prompt, and the merge judge sends the change to the owner. Done when no owed row is `later:` and each bound gate bites.

5. **Record the choice.** Write an ADR under the next free number, one line per row: the question, the tool and its version, where you checked it, and the alternative you passed over. A new ADR passes the guard without asking. Done when the ADR exists and `triggers.py --json` prints `[]`.

6. **Prove it and open the change.** Run `conformance` and `check`; both green. Commit with the subject `chore(method): safeguard <what>` (the `commits` gate accepts it; at most 72 characters), push and open the pull request with every template section filled: the table of step 2 under Gates, each `just bite-one` verdict, and under Criticism every tool you could not check and every answer you could not act on. With no forge, write the change description at `.scratch/changes/<branch>.md` and run `just merge <branch>`. The change touches guard paths, so the merge judge leaves the merge to the owner, and says so.

7. **Hand over.** Say which rows are bound and by what, and that the next instance of each kind is now judged by its gate. When a tool has config, name the path you added to the manifest. The last lines are the route: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .` in one Bash call and print its lines as they are.

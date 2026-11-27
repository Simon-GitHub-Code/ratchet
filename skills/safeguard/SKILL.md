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
   - Run `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/triggers.py" --root . --json` in one Bash call.
   - When it lists nothing and the route's line said the formal model is behind, run [a round of upkeep](#a-round-of-the-formal-models-upkeep) instead of these steps. When it lists nothing else, say so, print the route as step 6 does, and stop.

   Done when you hold the list.

2. **Choose a tool for each row.**
   - **The architecture record names a tool for the row's concept:** take it. The owner chose it, so it is not an assumption.
   - **The row is a shipped gate** (`smoke`, `load-smoke`, `telemetry-e2e`, `config-refusal`, `graceful-stop`, `prod-probe`): fill its `bind:` words; it needs no tool. The first entry point of a service also fills `boot`, `ready`, `service` and `flags_on` as [adopt step 9](../adopt/SKILL.md) says, with its table for the other types.
   - **A `formal verification of <kind>` row:** load `pstack:create-formal-verification-skill`. Ask it to model the area the kind's instances are in, name the instances `triggers.py` listed, and give it one requirement: **the check's one command catches a break planted in that code, with the model and its properties unchanged.** A model of the design alone does not meet it. A verifier run directly on the code does, and so does trace validation — tests emit real execution traces from the code and `just formal` validates every trace against the formal model so any code divergence fails the check. For `code:state-machine`, match state-transition constructs in `## Triggers` rather than only file names so new lifecycles are caught wherever they live. The skill chooses the tool. List the target and the tool under **Assumed**.
     - When no check here can read this code, bind the row `none` with a record that says so, and bind the design model the skill wrote under `formal verification`, without the proof.
   - **Any other row:** choose as [adopt step 5](../adopt/SKILL.md) says. What is there wins. Look each tool up now, in its current documentation. Take its version from the registry, through the package manager, never from memory and never from this plugin. Prefer a tool that already answers another row.

   Print one table: each row, its trigger and instances, the tool, and where you checked it.

   Done when every row has a tool, or `none` with the reason this project will never have the property.

3. **Bind each row.** Read [what a recipe must get right](../method/tiers.md#what-a-recipe-must-get-right) first. Then, for each row, in this order:
   1. **Install.** Install the tool through the package manager and add what it writes to `.gitignore`. A tool adoption provisioned, such as the test runner the binding's Tests section names, is already installed: write that runner into the recipe (`{{test_runner}}` with its shard flags), and install nothing.
   2. **Recipe.** Replace the recipe's `@echo "later: <trigger>"` body in the `justfile` with the real command. The recipe is red when it finds nothing to run; the trigger fired, so what it judges has a member.
   3. **Binding.** Change the row's Tool cell in `docs/process/binding.md` from `later: <trigger>` to the tool, and fill its threshold source. A row whose Telemetry or Feature flags section was one line says how it is met now. When binding the first build of a kind (a compiled entry point, a bundle, or generated code) or adding a gate that reads the build's output (`smoke`, `load-smoke`, `config-refusal`, `graceful-stop`, `e2e`), measure cold and warm build times, test once in a fresh work tree whether copying the build cache stays warm, fill or update `- Build:` under `## Speed` (naming gate, cache paths, cold and warm seconds, reuse policy, and consumer gates), and update CI build caches to key on toolchain version, lockfile, and gate (ending in `-${{ github.sha }}` with `restore-keys:` prefix, never hashing product source).
   4. **Bite.** Add the row's `[[bite]]` to `quality/bites.toml`: the smallest fault the gate exists to catch, and the words it prints for it. Narrow a whole-tree build gate's bite with `args` to one entry point by default. Add a second bite that empties a collection, and a `proof = "<name>"` bite for every proof the question lists in `application-types.toml` (`[proofs]`). For `accessibility`, include computed-style assertions under emulated forced-colors mode and emulated reduced-motion preference, and prove both with `proof = "forced-colors"` and `proof = "reduced-motion"` bites. For `authorization and tenant isolation`, prove both `proof = "unprotected-route"` and `proof = "cross-tenant-read"`; for `sensitive data kept out of logs`, prove `proof = "secret-logged"`; for each `high-consequence` gate, plant every named proof (`ambient-nondeterminism`, `replay-drift`, `imprecise-or-untyped-quantity`, `oracle-divergence`, `duplicate-action`, `concurrent-race`, `stale-input-accepted`, `bound-bypassed`, `schema-integrity`). A fault inside a file other changes also edit (a manifest, a lockfile) is a `replace`, not a `patch`. Run `just bite-one <gate>` and see it red with the fault and green without. Any verdict but `bites` is a defect of the recipe or the fault: fix it, never loosen `expect`.
   5. **Guard.** Add the tool's config path to the `tool-config` paths of `guards/manifest.toml`, and for `formal verification` the project-local verification skill's directory to the `formal` paths. The hook and the merge judge read the manifest as the default branch holds it, so the path is guarded once the owner merges this change. `.claude/settings.json` needs no change.

   6. **A formal model.** Add the model, its harness and the skill to the `formal` group of `guards/manifest.toml`. Fill the binding's `## Formal verification`: `- Model:` and `- Mirrors:` with their globs, and `- Synced:` with the default-branch commit this branch forks from. The row's `code-departs-from-model` bite plants its fault only in files that match both a `- Mirrors:` glob and the kind's Triggers Paths, and in no `- Model:` file. Then run `triggers.py --json` again: the model is the first `formal-model` instance, so `formal verification` is owed now. Bind it to `just formal` in this change.

   The `justfile`, the binding, `quality/` and the manifest are guard paths: on this branch they take no prompt, and the merge judge sends the change to the owner.

   Done when no owed row is `later:`, except a row a defect ticket of this run names, and each bound gate bites.

   **A defect at binding.** When the create skill reports a property the code breaks, the gate would be red, and this run changes no product code. Leave that row `later:` and bind the rest. Push the model to `formal/kept-<kind>`, which never merges. File a ticket that names the row, carries the counterexample in the code's words, and includes a `## Acceptance criteria` section with `- [ ]` items for fixing the broken property, merging `formal/kept-<kind>`, and binding the row (matching `/ratchet:conjecture`'s criteria digest check), with the line ``Formal model: defect `<row>`, kept on `formal/kept-<kind>` ``, and under it: "A fix that adds an instance of this kind merges `formal/kept-<kind>` and binds the row in the same change."

4. **Record the choice.** Write an ADR under the next free number, one line per row: the question, the tool and its version, where you checked it, and the alternative you passed over. A new ADR passes the guard without asking.

   Done when the ADR exists and `triggers.py --json` prints `[]`, or only rows a defect ticket of this run names.

5. **Prove it and open the change.**
   - Run `conformance` and `check`, both to green.
   - Commit with the subject `chore(method): safeguard <what>`, at most 72 characters.
   - Push and open the pull request with every template section filled: under Gates the table of step 2 and each `just bite-one` verdict, under Criticism every tool you could not check.
   - With no forge, write the change description at `.scratch/changes/<branch>.md` (the branch's `/` written as `-`) and run `just merge <branch>`.

   The change touches guard paths, so the merge judge leaves the merge to the owner, and says so.

6. **Hand over.** Write for the owner, in plain words with no method term:
   - which rows are bound and by what, and that the next instance of each kind is now judged by its gate;
   - under **Assumed**, one line per tool you chose: why it, the one you passed over, what changing it later costs (what changes, roughly how much work, whether anything built is redone), and what the owner can say to change it;
   - when a tool has config, name the path you added to the manifest.

   An **Assumed** line carries the real names in place of the brackets, such as ``Security checks on new code: <the tool you chose>, current and it reads this language; <the one you passed over> was the other choice. Changing later: a change of its own that an agent makes while you are away (the tool swapped, its settings written, the check rewired and shown to catch a planted mistake again, a decision record). It touches protected files: the list of checks, the list of planted mistakes, the tool's settings and their saved copy, the record of which tool runs each check, and the package list and its lock file where the tool is installed through them. The agent puts the new tool's settings file on the protected list in place of the old one, and the merge is yours. Nothing built is redone; the code changes only where the new tool objects to it. Say "use another" to switch.``

   The last lines are the route: run `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .` in one Bash call and print its lines as they are.

## A round of the formal model's upkeep

A round checks the model against the code as it is now.

1. **Branch.** Note the default branch's head commit, `<commit>`. Make the branch `formal/upkeep-<commit>` from it. When a `formal/kept-upkeep-` branch exists from a ticket since closed, start from its update.
2. **Run the round.** Load `pstack:maintain-formal-verification-skill`, and act on its outcome:
   - **In sync, or updated:** set `- Synced:` to `<commit>`, commit the change to the model if there is one, and open the change as step 5 does. Every round edits guard paths, so it is the owner's merge.
   - **A bug in the code:** push the update to `formal/kept-upkeep-<commit>` and leave `- Synced:` as it is. File a ticket that carries the counterexample in the code's words and includes a `## Acceptance criteria` section with `- [ ]` items for the fix, with the line ``Formal model: round, kept on `formal/kept-upkeep-<commit>` ``.
   - **Blocked:** leave `- Synced:` as it is. File a ticket that carries the reason and includes a `## Acceptance criteria` section with `- [ ]` items for unblocking the round, with the line `Formal model: round`.
3. **Hand over** as step 6 does: say what the round found, in plain words, and what waits for the owner, the merge or the ticket.

Done when the change is open or the ticket is filed.

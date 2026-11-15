---
name: next
description: Say which command the owner types next, decided from the checkout and the tracker, with the reason and the step after it.
disable-model-invocation: true
---

# What next

`/ratchet:next` names the command the owner types next. It is one Bash call, then relay.

## Steps

1. **Run the route.** Run both commands in one Bash call:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/owed.py" --root .
   ```

   - `route.py` prints three lines: `next:` the command, `why:` the state of the checkout or tracker that decides it, `then:` the step after. It reads the tracker through `gh` when there is one, and local tickets under `.scratch/` otherwise. `also:` lines may follow the three.
   - `owed.py` prints one line when the project owes something since a Ratchet upgrade, and nothing otherwise. The line starts with the count, such as "69 things this project owes since the upgrade to 0.12.0", says that nothing fixes them by itself, and names the checks that do not run until they are fixed.

   Done when you hold the route's three lines.

2. **Run only the two commands that are an agent's.** Every command the route names is the owner's keystroke: leave it for them to type, and do not run it. The agent-callable stage (a dirty tree: run `check`, commit) is still the owner's to start. Two exceptions are yours: run the command, then run step 1 again.
   - **`next:` names the `hooks-install` entry point or `hooks_install.py`:** run it. It installs the repository's own commit hooks and changes nothing else.
   - **`next:` names the `merge` entry point** (`just merge <branch>`, or `just merge --records` for records the owner's conversation left on the default branch or a run of it that stopped on its `records/<time>` branch): run it. It merges only what the merge judge and `check` pass. Otherwise it prints `merge: owner` with the reasons and the owner's command: relay those as they are. That line is the merge's answer, not a failure, so the command is not run again.
   - **The command fails, or the route names it again:** run it once more, then step 1 again. When it fails a second time, or the route still names it, stop there and relay what the command printed, as it is.

   Done when the latest `next:` line names neither exception, or you have run its command twice and relayed what it printed.

3. **Report the route.** The reply runs in this order: the route's three lines, the one sentence on what the command will produce, each `also:` line with what it needs (step 4), then the owed line (step 5).
   - Print the three lines as they are.
   - Add one sentence on what the command will produce, taken from the stage's row in [route.md](../method/route.md).
   - **`next:` offers two commands for stage 7:** say which fits. `/ratchet:feature` fits when the owner is stepping away, `/pstack:poteto-mode` when they stay at the keyboard. `/ratchet:feature` with no ticket is the drain: it builds every agent-ready ticket, and the route names it when more than one states a conjecture.
   - **`next:` names `/ratchet:safeguard` or `/ratchet:architecture`:** the line offers the step it would otherwise name. Say both, and that carrying on is free until another instance of the owed kind is added.

   Done when the owner has the three lines and the sentence, and, in either case above, the choice spelled out.

4. **Relay each `also:` line.**
   - **It says the plugin in use is newer than the one the binding pins:** relay it as it is, and leave the command it names for the owner to type. Then add one sentence: the upgrade moves what the line names, writes the workflows, files and recipes the newer version supplies and the rows and sections it adds, and may leave owed what the newer version asks that it cannot write, which its report counts when it runs. Take that sentence from the line and from the opening of `${CLAUDE_PLUGIN_ROOT}/scripts/upgrade.py`, and give no count of what is owed: only the upgrade's run knows it.
   - **It offers a standing answer** (the owner has answered one question with the recommended option three times running): relay it as it is. It changes nothing. When the owner says yes, add the key and the answer to `## Standing answers` in `AGENTS.md` ([records.md](../method/records.md)) on a branch of its own. Agents follow `AGENTS.md` from the moment it is written, so the write asks the owner once, and the owner merges the change.
   - **It names a sign of a life event:** it is a product question. Ask it in plain words, with the sign it names:
     - **Ask** `event.deployed`: "Is it now running somewhere that people other than you can reach?"
     - **Ask** `event.irreplaceable-data`: "Does it now hold anything that could not be got back if it were lost?"
     - **Ask** `event.depended-on`: "Do people other than you now rely on it?"

     **On yes:** the next command is `/ratchet:safeguard <event>`, which they type. **On not yet:** run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root . --decline <event>` in one Bash call. The line stays away until a new sign appears.

   Done when each `also:` line is relayed or asked, and the upgrade's, if there is one, has its sentence.

5. **Relay what is owed.** When `owed.py` printed a line, relay it as it is. None of what it counts stops a merge, so it never changes the command the route names.
   - **The owner asks you to work through what is owed:** do it on one branch and in one change. An agent edits a guard path there with no prompt, and the owner decides once at the merge. Run `upgrade.py --apply` after the fixes, so the ledger drops the entries they settled.

   Done when the owed line, if there is one, is in the reply word for word.

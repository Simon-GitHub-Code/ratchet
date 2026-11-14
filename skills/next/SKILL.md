---
name: next
description: Say which command the owner types next on this repository, decided from the checkout and the tracker by the method's route script, with the reason and the step after it.
disable-model-invocation: true
---

# What next

`/ratchet:next`: one Bash call, then relay.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/owed.py" --root .
```

The second command prints one line when the project owes something since a Ratchet upgrade, and nothing otherwise (ADR 0037). The line starts with the count, such as "69 things this project owes since the upgrade to 0.12.0", says that nothing fixes them by itself, and names the checks that do not run until they are fixed. Relay that line as it is, after the route's lines. None of what it counts stops a merge, so it never changes the command the route names. When the owner asks you to work through what is owed, do it on one branch and in one change, since an agent edits a guard path there with no prompt (ADR 0033) and the owner decides once at the merge; run `upgrade.py --apply` after the fixes so the ledger drops the entries they settled.

It prints three lines: `next:` the command, `why:` the state of the checkout or tracker that decides it, `then:` the step after. It reads the tracker through `gh` when there is one; local tickets under `.scratch/` otherwise. Report the three lines as they are, then one sentence on what the command will produce, taken from the stage's row in [route.md](../method/route.md). An `also:` line may follow them: the owner has answered one question with the recommended option three times running, and the line offers to make that a standing answer. Relay it as it is. It changes nothing; when the owner says yes, add the key and the answer to `## Standing answers` in `AGENTS.md` ([records.md](../method/records.md)) on a branch of its own: agents follow `AGENTS.md` from the moment it is written, so the write asks the owner once, and the owner merges the change. An `also:` line that names a sign of a life event is a product question, asked in plain words with the sign it names:

- **Ask** `event.deployed`: "Is it now running somewhere that people other than you can reach?"
- **Ask** `event.irreplaceable-data`: "Does it now hold anything that could not be got back if it were lost?"
- **Ask** `event.depended-on`: "Do people other than you now rely on it?"

On yes, the next command is `/ratchet:safeguard <event>`, which they type. On not yet, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root . --decline <event>` in one Bash call: the line stays away until a new sign appears. A `next:` line naming `/ratchet:safeguard` or `/ratchet:architecture` offers the step it would otherwise name; say both, and that carrying on is free until another instance of the owed kind is added. Do not run the command: every command it names is the owner's keystroke, and the agent-callable stage (a dirty tree: run `check`, commit) is still the owner's to start. Two exceptions, each run and then the route again. A `next:` line that names the `hooks-install` entry point or `hooks_install.py` installs the repository's own commit hooks and changes nothing else. A `next:` line that names the `merge` entry point (`just merge <branch>`, or `just merge --records` for records the owner's conversation left on the default branch or a run of it that stopped on its `records/<time>` branch) merges only what the merge judge and `check` pass, and otherwise prints `merge: owner` with the reasons and the owner's command, which you relay as it is (ADR 0031).

When the `next:` line offers two commands for stage 7, say which fits: `/ratchet:feature` when the owner is stepping away, `/pstack:poteto-mode` when they stay at the keyboard. `/ratchet:feature` with no ticket is the drain: it builds every agent-ready ticket, and the route names it when more than one states a conjecture.

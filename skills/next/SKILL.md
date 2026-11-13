---
name: next
description: Say which command the owner types next on this repository, decided from the checkout and the tracker by the method's route script, with the reason and the step after it.
disable-model-invocation: true
---

# What next

`/ratchet:next`: one Bash call, then relay.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .
```

It prints three lines: `next:` the command, `why:` the state of the checkout or tracker that decides it, `then:` the step after. It reads the tracker through `gh` when there is one; local tickets under `.scratch/` otherwise. Report the three lines as they are, then one sentence on what the command will produce, taken from the stage's row in [route.md](../method/route.md). An `also:` line may follow them: the owner has answered one question with the recommended option three times running, and the line offers to make that a standing answer. Relay it as it is. It changes nothing; when the owner says yes, add the key and the answer to `## Standing answers` in `AGENTS.md` ([records.md](../method/records.md)) on a branch of its own: agents follow `AGENTS.md` from the moment it is written, so the write asks the owner once, and the owner merges the change. An `also:` line that names a sign of a life event asks the owner one question through the question tool: is the project that now (`/ratchet:safeguard <event>`, which they type), or not yet. On not yet, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root . --decline <event>` in one Bash call: the line stays away until a new sign appears. A `next:` line naming `/ratchet:safeguard` offers the step it would otherwise name; say both, and that carrying on is free until another instance of the owed kind is added. Do not run the command: every command it names is the owner's keystroke, and the agent-callable stage (a dirty tree: run `check`, commit) is still the owner's to start. One exception: a `next:` line that names the `hooks-install` entry point or `hooks_install.py` installs the repository's own commit hooks and changes nothing else, so run it and then the route again.

When the `next:` line offers two commands for stage 7, say which fits: `/ratchet:feature` when the owner is stepping away, `/pstack:poteto-mode` when they stay at the keyboard. `/ratchet:feature` with no ticket is the drain: it builds every agent-ready ticket, and the route names it when more than one states a conjecture.

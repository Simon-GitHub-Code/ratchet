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

It prints three lines: `next:` the command, `why:` the state of the checkout or tracker that decides it, `then:` the step after. It reads the tracker through `gh` when there is one; local tickets under `.scratch/` otherwise. Report the three lines as they are, then one sentence on what the command will produce, taken from the stage's row in [route.md](../method/route.md). Do not run the command: every command it names is the owner's keystroke, and the one agent-callable stage (a dirty tree: run `check`, commit) is still the owner's to start.

When the `next:` line offers two commands for stage 7, say which fits: `/ratchet:feature` when the owner is stepping away, `/pstack:poteto-mode` when they stay at the keyboard.

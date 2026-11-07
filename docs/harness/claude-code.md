# Ratchet in Claude Code

The home harness. Everything in the plugin is written for it; the other harness pages map from here.

## Install

```bash
claude plugin marketplace add claude-plugins-official   # mattpocock-skills
claude plugin marketplace add cursor/plugins            # pstack
claude plugin marketplace add pbakaus/impeccable        # impeccable
claude plugin marketplace add Simon-GitHub-Code/ratchet
claude plugin install ratchet@ratchet
```

The ratchet marketplace entry declares `mattpocock-skills`, `pstack` and `impeccable` as dependencies, so the last line installs all four once the three marketplaces are known. `claude plugin list` shows each with its marketplace and version.

## The guard

`hooks/hooks.json` registers `hooks/guard.py` on PreToolUse for `Edit|Write|MultiEdit|NotebookEdit|Bash|apply_patch` and `hooks/session.py` on SessionStart and SubagentStart. Nothing to wire.

## Names

| In the skills | Here |
| --- | --- |
| `${CLAUDE_SKILL_DIR}` | set by Claude Code to the skill's folder |
| `disable-model-invocation: true` | the Skill tool refuses it to an agent, in the main session and in every subagent, with the instruction not to reproduce it another way; the command is the owner's keystroke and the route names it (`skills/method/route.md`). pstack's own adapter lets a pstack skill read another pstack skill's file; that holds between pstack skills only |
| the question tool | `AskUserQuestion`, up to four questions per dialog |
| subagents | `Agent`; `pstack:poteto-agent` and friends by name |
| the plugin cache | `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/` |
| `/ratchet:init`, `/ratchet:adopt`, `/ratchet:feature`, `/ratchet:conjecture`, `/ratchet:next` | as written |

## What is Claude Code only

`workflows/pipeline.js` runs on the Workflow tool. The pipeline's roles and sight lines are in `skills/method/pipeline.md` for any harness with subagents.

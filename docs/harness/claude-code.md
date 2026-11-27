# Ratchet in Claude Code

Claude Code is the home harness. Everything in the plugin is written for it, and the other harness pages map from here.

## Install

```bash
claude plugin marketplace add mattpocock/skills             # mattpocock-skills
claude plugin marketplace add michael-denyer/pstack-claude  # pstack, ported for Claude Code
claude plugin marketplace add pbakaus/impeccable            # impeccable
claude plugin marketplace add Simon-GitHub-Code/ratchet
claude plugin install ratchet@ratchet
```

The ratchet marketplace entry declares `mattpocock-skills`, `pstack` and `impeccable` as dependencies. So once the three marketplaces are known, the last line installs all four. The entry names the latest release's tag, so the plugin installs from that release, never from the default branch (ADR 0068). `claude plugin list` shows each with its marketplace and version.

## The guard

Nothing to wire. `hooks/hooks.json` registers `hooks/guard.py` on PreToolUse for `Edit|Write|MultiEdit|NotebookEdit|Bash|apply_patch|run_terminal_command|search_replace|hashline_edit|write`, and `hooks/session.py` on SessionStart and SubagentStart.

## Names

| In the skills | Here |
| --- | --- |
| `RATCHET_PLUGIN`, the plugin an adopted repository's gates run from | unset: the session hook records the installed copy's folder in `.git/ratchet-plugin`, and the template `justfile` reads it |
| `${CLAUDE_SKILL_DIR}` | set by Claude Code to the skill's folder |
| `${CLAUDE_PLUGIN_ROOT}` | set by Claude Code to the plugin's install folder. `hooks/hooks.json` and the script commands in the skills rely on it |
| `disable-model-invocation: true` | the Skill tool refuses it to an agent, in the main session and in every subagent, with the instruction not to reproduce it another way. The command is the owner's keystroke, and the route names it (`skills/method/route.md`). pstack's own adapter lets a pstack skill read another pstack skill's file. That holds between pstack skills only |
| the question tool | `AskUserQuestion`, up to four questions per dialog |
| subagents | `Agent`; a plugin's agent types by their full name |
| the plugin cache | `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/` |
| `/ratchet:init`, `/ratchet:adopt`, `/ratchet:feature`, `/ratchet:conjecture`, `/ratchet:safeguard`, `/ratchet:architecture`, `/ratchet:next` | as written |

## What is Claude Code only

`workflows/pipeline.js` runs on the Workflow tool. On any harness with subagents, the pipeline's roles and sight lines are in `skills/method/pipeline.md`.

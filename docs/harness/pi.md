# Ratchet in pi

pi installs packages from git; a package is a directory with `extensions/` and `skills/`, or a `package.json` with a `pi` key naming them. This repository is one: its `package.json` points pi at `skills/` and at the guard extension.

## Install

```bash
pi install git:github.com/Simon-GitHub-Code/ratchet
npx skills@latest add mattpocock/skills -a pi      # include setup-matt-pocock-skills
pi install git:github.com/michael-denyer/pstack-claude   # a community port of pstack; pstack itself ships no pi package
```

`pi list` shows installed packages. impeccable ships no pi package; its skill can be added through skills.sh when listed there, and otherwise is unavailable here.

## The guard

`adapters/pi/ratchet-guard.ts` is a pi extension. On `tool_call` it maps pi's `bash`, `edit` and `write` to the guard's `Bash`, `Edit` and `Write`, runs `hooks/guard.py` with the Claude-shaped JSON and the session's `cwd`, and returns `{ block: true, reason }` on `deny`. On `ask` it uses `ctx.ui.confirm` when the session has a UI and blocks when it has none, since nobody can approve. On `before_agent_start` it appends `hooks/session.py`'s output to the system prompt, which is the stance in every session, as in Claude Code.

## Names

| In the skills | Here |
| --- | --- |
| `${CLAUDE_SKILL_DIR}` | the skill folder inside the installed package under `~/.pi/agent/` |
| `disable-model-invocation: true` | pi has no such flag; the skill is listed, and the agent reads it as a document |
| the question tool | `ctx.ui.confirm` and `ctx.ui.select` in extensions; the model asks in plain text |
| subagents | the `pi-subagents` package when installed; otherwise the pipeline is run by hand from `pipeline.md` |
| `claude plugin list` | `pi list` |
| `/ratchet:init` | the skill by name |

## Verified

On this machine: pi 1.3.0 with mattpocock's skills under `~/.pi/agent/skills/` and the pstack port installed as a git package. From its docs: `tool_call` can block with `{ block, reason }`, `ctx.ui.confirm` exists, `ctx.cwd` is the working directory, and a package declares extensions and skills under `pi` in `package.json`. Not yet run: the extension itself in a pi session. Its system-prompt injection uses the `before_agent_start` event and is the least certain part.

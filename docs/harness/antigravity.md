# Ratchet in Antigravity

Antigravity's CLI (`agy`) installs a Claude Code plugin from its git URL and converts the plugin's hooks to its own `hooks.json`. Its hook JSON is not Claude Code's, so the guard and the session hook translate (below); no adapter file is needed.

## Install

```bash
agy plugin install https://github.com/Simon-GitHub-Code/ratchet
agy plugin list
```

`agy plugin list` should show `ratchet` as enabled. The plugin lands in `~/.gemini/config/plugins/ratchet/`, with its hooks in `hooks.json` there. Antigravity has no `SubagentStart` event, so the install skips that hook: subagents do not get the stance. Antigravity has no dependency field, so `/ratchet:init` installs `mattpocock-skills`, `pstack` and `impeccable` with the owner's consent.

## The guard

The install maps the matcher to Antigravity's tools: `run_command`, `write_to_file`, `write_file`, `replace_file_content`, `edit_file`, `multi_replace_file_content` and `notebook_edit`. Hooks run with the plugin folder as their working directory; the agent's directory is `cwd` in the input.

The input is `{"toolCall": {"name": ..., "args": {...}}, "cwd": ...}` with CamelCase arguments. `hooks/ratchet_guards/hook.py` turns it into Claude's shape: `run_command` is `Bash` with `CommandLine` as the command and its own `Cwd` as the directory; `write_to_file` and `write_file` are `Write`; `replace_file_content` and `edit_file` are `Edit`; `multi_replace_file_content` is `MultiEdit`. The file is `TargetFile`.

The answer is Antigravity's:

| Ratchet | Antigravity |
| --- | --- |
| deny | `{"decision": "deny", "reason": ...}` |
| ask | `{"decision": "force_ask", "reason": ...}` |
| no rule fires | `{}` |

`ask` becomes `force_ask` because Antigravity's `ask` respects a cached "Always Allow", which would let a later guard-path edit through without the owner. No rule gives `{}`: no decision, so Antigravity's own permission rules decide. The guard never answers `allow`, which would skip them.

The session hook answers `SessionStart` with `{"injectSteps": [{"systemMessage": {"systemMessage": ...}}]}`.

`~/.gemini/config/plugins/**`, `~/.gemini/config/hooks.json` and `~/.gemini/config/config.json` (which enables plugins) are locked, and `agy plugin uninstall` and `agy plugin disable` are denied.

## Names

| In the skills | Here |
| --- | --- |
| `${CLAUDE_SKILL_DIR}` | the skill folder under `~/.gemini/config/plugins/<plugin>/skills/<name>/` |
| `disable-model-invocation: true` | read the `SKILL.md` and follow it |
| the question tool | ask in plain text |
| `claude plugin list` | `agy plugin list` |

## Verified

On this machine, with agy 1.5.6:

- `agy plugin install` of this repository staged the `PreToolUse` and `SessionStart` hooks shown above and skipped `SubagentStart`.
- Antigravity-shaped input fed to `hooks/guard.py` and `hooks/session.py` gave the answers in the table.

Not yet run: a live `agy` session. The hooks guide calls `decision` required and does not say what an empty answer does; the agy 1.5.6 changelog says an empty decision is handled instead of failing. Check once that `{}` leaves an ordinary edit to Antigravity's own prompt and does not block it.

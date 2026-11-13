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
| no rule fires | `{"decision": "ask"}` |

`ask` becomes `force_ask` because Antigravity's `ask` respects a cached "Always Allow", which would let a later merge, agent-ready label, or write of a CI workflow or an agent-instruction file through without the owner. A guard path change on a branch gets no verdict from the guard (ADR 0033), so it meets Antigravity's normal prompt, as any edit does.

Antigravity has no answer for "no opinion". A missing or empty `decision` is a deny: under 0.9.0 and 0.9.1 the guard answered `{}` when no rule fired, and agy 1.5.6 refused every command and edit, `git status` included, with `tool call denied by pre-tool hook:` and an empty reason. `allow` would auto-approve past Antigravity's own permission prompt. So when no rule fires the guard answers a plain `ask`: Antigravity's normal prompt, which honours a cached "Always Allow" as it would without the guard.

The session hook answers `SessionStart` with `{"injectSteps": [{"systemMessage": {"systemMessage": ...}}]}`.

`~/.gemini/config/plugins/**`, `~/.gemini/config/hooks.json` and `~/.gemini/config/config.json` (which enables plugins) are locked, and `agy plugin uninstall` and `agy plugin disable` are denied.

## Names

| In the skills | Here |
| --- | --- |
| `RATCHET_PLUGIN`, the plugin an adopted repository's gates run from | export it as `~/.gemini/config/plugins/ratchet`, the folder the install made |
| `${CLAUDE_SKILL_DIR}` | the skill folder under `~/.gemini/config/plugins/<plugin>/skills/<name>/` |
| `${CLAUDE_PLUGIN_ROOT}` | the plugin folder, `~/.gemini/config/plugins/ratchet`. Hooks run with it as their working directory; whether Antigravity sets this variable in the shell of a skill's command is not verified. Export `CLAUDE_PLUGIN_ROOT="$HOME/.gemini/config/plugins/ratchet"` before starting a session, so the commands in the skills resolve |
| `disable-model-invocation: true` | read the `SKILL.md` and follow it |
| the question tool | ask in plain text |
| `claude plugin list` | `agy plugin list` |

## Verified

agy 1.5.6:

- `agy plugin install` of this repository staged the `PreToolUse` and `SessionStart` hooks shown above and skipped `SubagentStart`.
- Antigravity-shaped input fed to `hooks/guard.py` and `hooks/session.py` gave the answers in the table.
- A live agy 1.5.6 session under 0.9.1 showed that `{}` is a deny (above). The `decision` field's schema in the agy binary is required, one of `allow`, `deny`, `ask`, `force_ask` and `deny_unless_prior_grant`, and `allow` maps to an internal auto-approve.

Not yet run: a live `agy` session under 0.9.2, to confirm that an ordinary command now reaches Antigravity's normal prompt.

# Ratchet in Codex

Codex installs plugins from git marketplaces that carry `.claude-plugin/plugin.json`, and its hooks use the Claude Code event names and decision JSON.

## Install

```bash
codex plugin marketplace add Simon-GitHub-Code/ratchet
codex plugin add ratchet@ratchet
codex plugin marketplace add mattpocock/skills && codex plugin add mattpocock-skills@mattpocock
codex plugin marketplace add cursor/plugins && codex plugin add pstack@cursor-plugins
codex plugin marketplace add pbakaus/impeccable && codex plugin add impeccable@impeccable
```

`~/.codex/config.toml` lists installed plugins under `[plugins."name@marketplace"]`. Hooks need `[features] hooks = true`, and Codex asks the owner to trust each hook source once.

## The guard

Codex's plugin manifest reader resolves a `hooks` path, so the plugin's `hooks/hooks.json` should load as it does in Claude Code. Its PreToolUse payload carries `tool_name`, `tool_input` and `cwd`; shell tools are matched as `Bash` with `{ "command": ... }`, and file edits arrive as `apply_patch` (matcher aliases `Write` and `Edit`), whose patch headers the guard reads for the paths. The reply is `hookSpecificOutput.permissionDecision` `deny` or `ask` with a reason.

If the plugin's hooks do not load, the owner runs, in their own shell:

```bash
python3 <ratchet>/scripts/adapter.py codex --apply
```

which merges PreToolUse and SessionStart entries pointing at the installed plugin into `~/.codex/hooks.json`. That file is a locked guard path, so only the owner writes it.

## Names

| In the skills | Here |
| --- | --- |
| `${CLAUDE_SKILL_DIR}` | the skill folder under `~/.codex/plugins/cache/<marketplace>/<plugin>/<version>/skills/<name>/` |
| `disable-model-invocation: true` | read the `SKILL.md` and follow it |
| the question tool | Codex's question tool when present; otherwise ask in plain text |
| subagents | `spawn_agent` (hook alias `Agent`) |
| `claude plugin list` | `codex plugin list`, or the `[plugins]` tables in `~/.codex/config.toml` |
| `/ratchet:init` | the skill by name |

## Verified

On this machine: Codex 0.164 has pstack and mattpocock-skills installed from Claude-shaped manifests, `~/.codex/hooks.json` with a Claude-shaped SessionStart entry and trust hashes. From its source: PreToolUse with `permissionDecision` deny/ask, the `Bash` and `apply_patch` names, and a `hooks` path in the plugin manifest. Not yet run: the plugin's hooks loading from a Codex install, and the corpus through its hook runner.

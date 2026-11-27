# Ratchet in Codex

Codex installs plugins from git marketplaces that carry `.claude-plugin/plugin.json`. Its hooks use the Claude Code event names and decision JSON.

## Install

```bash
codex plugin marketplace add Simon-GitHub-Code/ratchet
codex plugin add ratchet@ratchet
codex plugin marketplace add mattpocock/skills && codex plugin add mattpocock-skills@mattpocock
codex plugin marketplace add michael-denyer/pstack-claude && codex plugin add pstack@pstack-claude
codex plugin marketplace add pbakaus/impeccable && codex plugin add impeccable@impeccable
```

`~/.codex/config.toml` lists installed plugins under `[plugins."name@marketplace"]`. Hooks need `[features] hooks = true`, which the owner adds by hand, since it switches the guard on. Once on, the guard denies an agent's edit of that file. Codex asks the owner to trust each hook source once.

## The guard

Codex's plugin manifest reader loads the installed plugin's `hooks/hooks.json` natively from `~/.codex/plugins/cache/<marketplace>/ratchet/<version>/` with `${CLAUDE_PLUGIN_ROOT}` set, as in Claude Code.

- Its PreToolUse payload carries `tool_name`, `tool_input` and `cwd`.
- Shell tools are matched as `Bash` with `{ "command": ... }`.
- File edits arrive as `apply_patch` (matcher aliases `Write` and `Edit`). The guard reads its patch headers for the paths.
- The reply is `hookSpecificOutput.permissionDecision` `deny` or `ask` with a reason.

Do not keep legacy Ratchet entries in `~/.codex/hooks.json` once Codex loads `ratchet@ratchet:hooks/hooks.json` natively: both fire on every tool call, and an entry pointing at `~/.codex/.tmp/marketplaces/ratchet` runs from the moving `main` checkout instead of the pinned release. Remove legacy entries from your own shell:

```bash
python3 <ratchet>/scripts/adapter.py codex --remove
```

**Only on older Codex builds where plugin hooks do not load:** the owner runs, in their own shell, pointing at the versioned cache copy (never `~/.codex/.tmp/marketplaces/ratchet`):

```bash
python3 <ratchet>/scripts/adapter.py codex --apply --plugin-root ~/.codex/plugins/cache/ratchet/ratchet/<version>
```

It merges PreToolUse and SessionStart entries into `~/.codex/hooks.json`. That file is a locked guard path, so only the owner writes it.

## Names

| In the skills | Here |
| --- | --- |
| `RATCHET_PLUGIN`, the plugin an adopted repository's gates run from | unset: the session hook, once it has run in the repository, records the installed copy's folder in `.git/ratchet-plugin`, and the template `justfile` reads it (and falls back to `~/.codex/plugins/cache/ratchet/ratchet/*/`). Until then, export it as `~/.codex/plugins/cache/<marketplace>/ratchet/<version>/` |
| `${CLAUDE_SKILL_DIR}` | the skill folder under `~/.codex/plugins/cache/<marketplace>/<plugin>/<version>/skills/<name>/` |
| `${CLAUDE_PLUGIN_ROOT}` | set by Codex for plugin hooks to `~/.codex/plugins/cache/<marketplace>/ratchet/<version>/`. For shell commands run outside hooks, export it to that folder if a skill reads it |
| `disable-model-invocation: true` | read the `SKILL.md` and follow it |
| the question tool | Codex's question tool when present. Otherwise ask in plain text |
| subagents | `spawn_agent` (hook alias `Agent`) |
| `claude plugin list` | `codex plugin list`, or the `[plugins]` tables in `~/.codex/config.toml` |
| `/ratchet:init` | the skill by name |

## Verified

Codex 0.164:

- It installs pstack and mattpocock-skills from Claude-shaped manifests.
- Its `~/.codex/hooks.json` takes a Claude-shaped SessionStart entry with trust hashes.

Codex 0.171:

- Loads `hooks/hooks.json` natively from `~/.codex/plugins/cache/<marketplace>/<plugin>/<version>/` with `${CLAUDE_PLUGIN_ROOT}` set and records trust hashes in `~/.codex/config.toml`.
- Default workspace-write sandbox mounts `.git` read-only and restricts network access, so `git` writes and forge CLI calls prompt for escalation unless the owner widens the sandbox profile.

From its source: PreToolUse with `permissionDecision` deny/ask, the `Bash` and `apply_patch` names, and a `hooks` path in the plugin manifest.

Not yet run: the full guard corpus through Codex's hook runner.

# Ratchet in Grok

Grok installs Claude Code plugins as they are and speaks the same hook JSON, so the plugin needs no adapter here.

## Install

```bash
grok plugin install Simon-GitHub-Code/ratchet --trust
grok plugin install "michael-denyer/pstack-claude#plugins/pstack" --trust
grok plugin install mattpocock/skills --trust
grok plugin install "pbakaus/impeccable#plugin" --trust
```

`grok plugin list` shows what is installed. Grok has no dependency field, so `/ratchet:init` installs what is missing with the owner's consent. `--trust` activates a plugin's hooks; without it Grok shows the source and stops.

## The guard

Grok loads a plugin's `hooks/hooks.json`, fires `PreToolUse` with `tool_name`, `tool_input` and `cwd` on stdin, and honours `hookSpecificOutput.permissionDecision` of `deny` and `ask` with `permissionDecisionReason` (its hooks guide, "Reading a Hook's Reply"). Grok also reads `~/.claude/settings.json` hooks and `.claude/skills/`, so a repository adopted under Claude Code carries its permission rules here too.

## Names

| In the skills | Here |
| --- | --- |
| `${CLAUDE_SKILL_DIR}` | the skill folder under `~/.grok/installed-plugins/<plugin>/skills/<name>/` |
| `disable-model-invocation: true` | read the `SKILL.md` and follow it |
| the question tool | Grok's question tool when the session has one; otherwise ask in plain text |
| subagents | Grok subagents; `plugin-name:agent-name` for plugin agents |
| the plugin cache | `~/.grok/installed-plugins/<id>/`; `~/.grok/installed-plugins/registry.json` maps names to folders |
| `claude plugin list` | `grok plugin list` |
| `/ratchet:init` | `/ratchet:init` (plugin commands keep their prefix) |

## Verified

On this machine: Grok 1.0.70 installed pstack's Claude Code tree and mattpocock-skills from their Claude marketplaces, lists `PreToolUse` as a deny-capable event and loads plugin `hooks/hooks.json`. Not yet run: an adopted repository's corpus through Grok's hook runner.

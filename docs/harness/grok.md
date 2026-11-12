# Ratchet in Grok

Grok installs Claude Code plugins as they are and speaks the same hook JSON. It does not use Claude's tool names in that JSON, so the guard translates them (below); no adapter file is needed.

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

Grok's matcher accepts Claude's names (`Bash` matches its `run_terminal_command`), but the hook input carries Grok's own: `run_terminal_command` with `command`, `search_replace` and `write` with `file_path`, `hashline_edit`. The Cursor preset uses `Shell`, `StrReplace`, `Write` and `EditNotebook`, with the file in `path`. `hooks/ratchet_guards/hook.py` judges each as the Claude tool it stands for, and `hooks/hooks.json` lists Grok's names in the matcher as well.

## Names

| In the skills | Here |
| --- | --- |
| `${CLAUDE_SKILL_DIR}` | the skill folder under `~/.grok/installed-plugins/<plugin>/skills/<name>/` |
| `${CLAUDE_PLUGIN_ROOT}` | the plugin folder under `~/.grok/installed-plugins/`. Grok expands it in hook commands: the guard's command in `hooks/hooks.json` uses it, and the guard denied calls (see Verified). Whether it is set in the shell of a skill's Bash call is not verified. Export it to the plugin folder before starting a session, so the commands in the skills resolve |
| `disable-model-invocation: true` | read the `SKILL.md` and follow it |
| the question tool | Grok's question tool when the session has one; otherwise ask in plain text |
| subagents | Grok subagents; `plugin-name:agent-name` for plugin agents |
| the plugin cache | `~/.grok/installed-plugins/<id>/`; `~/.grok/installed-plugins/registry.json` maps names to folders |
| `claude plugin list` | `grok plugin list` |
| `/ratchet:init` | `/ratchet:init` (plugin commands keep their prefix) |

## Verified

- Grok 1.0.70 installed pstack's Claude Code tree and mattpocock-skills from their Claude marketplaces, lists `PreToolUse` as a deny-capable event and loads plugin `hooks/hooks.json`.
- Grok 1.0.77: the `PreToolUse` input was captured for a shell call, a new file, a replacement and an overwrite, and the tool names above come from it.
- `grok -p --always-approve` ran `git push --force`. A guard that did not know Grok's tool names let it through. The current guard denied it, and it also denied a `search_replace` and a `write` on the plugin's own locked files, which stayed unchanged.

Not yet run: an adopted repository's corpus through Grok's hook runner.

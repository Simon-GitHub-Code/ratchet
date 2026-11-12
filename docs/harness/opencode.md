# Ratchet in OpenCode

OpenCode reads `AGENTS.md`, discovers skills from `.opencode/skills/`, `~/.config/opencode/skills/` and the Claude-compatible `.claude/skills/` paths, and loads TypeScript plugins from `.opencode/plugins/` and `~/.config/opencode/plugins/`.

## Install

```bash
git clone https://github.com/Simon-GitHub-Code/ratchet ~/.config/opencode/ratchet
python3 ~/.config/opencode/ratchet/scripts/adapter.py opencode --apply   # the owner, in their own shell
npx skills@latest add Simon-GitHub-Code/ratchet -a opencode
npx skills@latest add mattpocock/skills -a opencode
```

The adapter writes `~/.config/opencode/plugins/ratchet-guard.ts` with the checkout's path filled in. pstack and impeccable ship no OpenCode package; pstack's skills can be copied as files, with its Cursor tool names unmapped.

## The guard

`adapters/opencode/ratchet-guard.ts` is an OpenCode plugin. Its `tool.execute.before` handler maps `bash`, `edit` and `write` to `Bash`, `Edit` and `Write`, runs `hooks/guard.py` with the Claude-shaped JSON and the project directory, and throws on `deny`. OpenCode has no way for a plugin to hand a call to the owner, so `ask` also throws, with the reason and "run it yourself". A guard that cannot run throws too: fail closed, as in Claude Code.

## Names

| In the skills | Here |
| --- | --- |
| `RATCHET_PLUGIN`, the plugin an adopted repository's gates run from | export it as `~/.config/opencode/ratchet`, the clone the install made |
| `${CLAUDE_SKILL_DIR}` | the skill folder under the skills directory the installer chose |
| `${CLAUDE_PLUGIN_ROOT}` | the clone, `~/.config/opencode/ratchet` in the install above. OpenCode is not known to set this variable, and the guard plugin takes its path from the adapter, not from it. Export `CLAUDE_PLUGIN_ROOT="$HOME/.config/opencode/ratchet"` before starting a session, so the commands in the skills resolve. This mapping is unverified |
| `disable-model-invocation: true` | no such flag; the agent reads the skill as a document |
| the question tool | none for plugins; the model asks in plain text |
| subagents | OpenCode agents; otherwise the pipeline by hand from `pipeline.md` |
| `claude plugin list` | the `plugin` array in `opencode.json` and the plugin directories |

## Verified

Not yet run against a live OpenCode. From its docs: the plugin locations, `tool.execute.before(input, output)` with `input.tool` and `output.args`, throwing to block, and `filePath` and `command` as the file and shell arguments. Everything on this page is to be checked on first use.

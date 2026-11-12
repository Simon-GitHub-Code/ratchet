# Ratchet in pi

pi installs packages from git; a package is a directory with `extensions/` and `skills/`, or a `package.json` with a `pi` key naming them. This repository is one: its `package.json` points pi at `skills/` and at the guard extension.

## Install

```bash
pi install git:github.com/michael-denyer/pstack-claude   # the pstack port; pstack itself ships no pi package
pi install git:github.com/Simon-GitHub-Code/ratchet
pi install git:github.com/mattpocock/skills
pi install git:github.com/pbakaus/impeccable
```

`pi list` shows installed packages. Two packages need a line in `~/.pi/agent/settings.json` after installing:

- **mattpocock/skills** has no `pi` manifest, so pi loads every folder under `skills/`, drafts included, and two names (`tdd`, `teach`) collide with pstack's. pi skill names are not namespaced per plugin, so the first one loaded wins and the other is a warning. Narrow the package to the skills its Claude plugin ships (the `skills` list in its `.claude-plugin/plugin.json`), minus `tdd` and `teach`, with the object form: `{ "source": "git:github.com/mattpocock/skills", "skills": ["skills/engineering/ask-matt", ...] }`.
- **impeccable** keeps its pi build in `.pi/skills/`, which pi does not look in for a package, so the install loads nothing. Add the folder to pi's own list: `"skills": ["~/.pi/agent/git/github.com/pbakaus/impeccable/.pi/skills"]`.

pi reads frontmatter with a strict YAML parser: a `description` holding `: ` must be quoted, or the skill is skipped with "Nested mappings are not allowed in compact mappings".

## The guard

`adapters/pi/ratchet-guard.ts` is a pi extension. On `tool_call` it maps pi's `bash`, `edit` and `write` to the guard's `Bash`, `Edit` and `Write`, runs `hooks/guard.py` with the Claude-shaped JSON and the session's `cwd`, and returns `{ block: true, reason }` on `deny`. On `ask` it uses `ctx.ui.confirm` when the session has a UI and blocks when it has none, since nobody can approve. On `before_agent_start` it appends `hooks/session.py`'s output to the system prompt, which is the stance in every session, as in Claude Code. Commands in that output are written Claude Code's way (`/ratchet:init`); the extension rewrites them to pi's `/skill:init` before appending.

## Names

| In the skills | Here |
| --- | --- |
| `${CLAUDE_SKILL_DIR}` | the skill folder inside the installed package under `~/.pi/agent/` |
| `${CLAUDE_PLUGIN_ROOT}` | the installed package, `~/.pi/agent/git/github.com/Simon-GitHub-Code/ratchet`. The guard extension works out its own root from its file location and sets no variable, and pi is not known to set one. Export `CLAUDE_PLUGIN_ROOT="$HOME/.pi/agent/git/github.com/Simon-GitHub-Code/ratchet"` before starting a session, so the commands in the skills resolve. The path is inferred from where pi puts the impeccable package; this mapping is unverified |
| `disable-model-invocation: true` | honoured: the skill is left out of the model's list and runs only as the owner's `/skill:<name>` |
| the question tool | `ctx.ui.confirm` and `ctx.ui.select` in extensions; pstack's pi extension adds a question tool |
| subagents | pstack's pi extension adds a subagent tool; otherwise the pipeline is run by hand from `pipeline.md` |
| `claude plugin list` | `pi list` |
| `/ratchet:init`, `/mattpocock-skills:setup-matt-pocock-skills` | `/skill:init`, `/skill:setup-matt-pocock-skills` |

## Verified

pi 1.5.2, with the four packages above installed and filtered as described:

- all six ratchet skills load, with no collisions;
- the extension loads beside pstack's;
- in `pi -p` sessions the extension blocked `git push --force` with the guard's reason, and let `git log` through;
- the session text reached the model, which opened with the "nothing checks the code" notice.

Not yet run: an `ask` decision with a UI (`ctx.ui.confirm`).

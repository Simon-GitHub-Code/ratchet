# ratchet

The Ratchet method: what must be true before a merge, when software is built by agents. Tiered deterministic gates that only tighten, guard paths the harness protects, disputes, records, a four-role feature pipeline, and the merge rule of critical rationalism. Packaged as one plugin that runs in Claude Code, Grok, Codex, Antigravity, pi and OpenCode, so every repository reads one copy of the method and keeps only its own binding.

It composes with three other plugins and competes with none. `mattpocock-skills` says how the work is done (`tdd`, `codebase-design`, `domain-modeling`, `code-review`, `diagnosing-bugs`, `grill-with-docs`, `pr`, `research`). `pstack` says in what style and with which levers (`poteto-mode`, `architect`, `interrogate`, `arena`, `correct`, the formal verification skills). `impeccable` says how anything a person sees is designed, and its `PRODUCT.md` and `DESIGN.md` are records. Ratchet's roles call those skills by name; its records use their formats and add a section each.

## What is in the plugin

| Path | What it is |
| --- | --- |
| `skills/method/SKILL.md` | the founding document: the words, the nine commitments, how work runs |
| `skills/method/*.md` | the reference behind each commitment: the merge rule, tiers as questions, the ratchet, guards and disputes, the pipeline, records, verifiable shape |
| `skills/init/` | `/ratchet:init`: the bare bones for any repository: `git init` where needed, an `AGENTS.md` naming the four plugins with `CLAUDE.md` linked to it, the other three plugins' one-time setup, the first commit, the remote and the triage labels |
| `skills/adopt/` | `/ratchet:adopt`: steps and templates that bind the method to a repository's runner, forge and stack |
| `skills/feature/` | `/ratchet:feature <ticket>`: the feature preflight on an agent-ready ticket, the branch, then the pipeline |
| `skills/conjecture/` | `/ratchet:conjecture <ticket>`: the method's one addition to a ticket, drafted from the conversation that produced it |
| `skills/next/` | `/ratchet:next`: the command the owner types next, decided from the checkout and the tracker |
| `scripts/route.py` | the route engine behind `/ratchet:next`, every hand-over and the session hook's last line |
| `workflows/pipeline.js` | the four-role pipeline (preflight, spec, implement, critic, review) the feature skill runs |
| `scripts/feature_preflight.py` | what must be true of a ticket and the checkout before the pipeline's first agent spawns |
| `hooks/` | the PreToolUse guard hook, the SessionStart and SubagentStart hook that puts the stance in every agent's context on an adopted checkout and names a bare one, and their `ratchet_guards` package (`guard-diff` lives here too) |
| `scripts/conformance.py` | tier 1 gate: the repository still carries the method's artefacts; `--self-test` shows it bites |
| `scripts/lowered.py` | tier 0 gate: no threshold lowered and no ceiling raised against the merge base |
| `scripts/budget.py` | the speed gate: a tier or gate against its ceiling in seconds; `--set` records the first ceiling |
| `scripts/adapter.py` | wires the guard into Codex or OpenCode when the harness does not load the plugin's hooks |
| `adapters/` | the pi extension and the OpenCode plugin that translate a tool call into the guard's JSON |
| `docs/harness/` | one page per harness: install commands, how the guard is wired, the names mapped, what was verified |
| `docs/adr/` | the plugin's own decisions; 0004 is the five-harness decision |
| `docs/research/` | the sources behind the stance |

## Install

In Claude Code:

```bash
claude plugin marketplace add mattpocock/skills
claude plugin marketplace add michael-denyer/pstack-claude
claude plugin marketplace add pbakaus/impeccable
claude plugin marketplace add Simon-GitHub-Code/ratchet
claude plugin install ratchet@ratchet
```

The marketplace entry declares `mattpocock-skills`, `pstack` and `impeccable` as dependencies, so the last line installs all four once their marketplaces are known. pstack comes from `michael-denyer/pstack-claude`, the port with Cursor's tool names already translated for Claude Code and the other harnesses, not from `cursor/plugins`, the Cursor original. Grok, Codex, Antigravity, pi and OpenCode each have a page under `docs/harness/` with their commands; none has a dependency field, so `/ratchet:init` installs what is missing with the owner's consent.

In Antigravity's CLI:

```bash
agy plugin install https://github.com/Simon-GitHub-Code/ratchet
agy plugin list
```

`agy plugin list` should show `ratchet` enabled. Antigravity has no `SubagentStart` event, so that hook is skipped and subagents do not get the stance; the guard and the `SessionStart` hook run. See `docs/harness/antigravity.md`.

Updates arrive when `version` in `.claude-plugin/plugin.json` rises. To develop the plugin, add the checkout as a local marketplace instead (`claude plugin marketplace add /path/to/ratchet`): it is read in place, so edits apply at the next session or `/reload-plugins`.

## Use in a repository

Run `/ratchet:init` in any repository for the bare bones. Run `/ratchet:adopt` when the owner wants the gates: it copies the templates, interviews the owner for the binding, and leaves the entry points red until every gate is bound to a tool or accepted as `none` with a reason. The `justfile` and `.github/` templates are the `just` and GitHub examples; any runner and any forge can carry the entry points.

## Develop the plugin

The guard hook locks the installed plugin's root with or without a manifest, and a directory marketplace is read in place, so an agent cannot edit this checkout while the plugin is enabled. Disable it for the session (`claude plugin disable ratchet`, then `/reload-plugins`) and re-enable it afterwards, or install the plugin from a separate clone.

`just check` here runs the hook's tests, lint and types, the conformance self-test, a parse of the workflow and `claude plugin validate`. The method says gates are themselves gated; this is where that applies to the method.

## Change the method

A change to a commitment is a change to every repository on the method. Record it as an ADR here (`docs/adr/`), raise `version`, and let each repository's ADR 0001 say which version it adopted.

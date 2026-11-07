# ratchet

The Ratchet method: what must be true before a merge, when software is built by agents. Tiered deterministic gates that only tighten, guard paths the harness protects, disputes, records, a four-role feature pipeline, and the merge rule of critical rationalism. Packaged as a Claude Code plugin so every repository reads one copy of the method and keeps only its own binding.

It composes with three other plugins and competes with none. `mattpocock-skills` says how the work is done (`tdd`, `codebase-design`, `domain-modeling`, `code-review`, `diagnosing-bugs`, `grill-with-docs`, `pr`, `research`). `pstack` says in what style and with which levers (`poteto-mode`, `architect`, `interrogate`, `arena`, `correct`, the formal verification skills). `impeccable` says how anything a person sees is designed, and its `PRODUCT.md` and `DESIGN.md` are records. Ratchet's roles call those skills by name; its records use their formats and add a section each.

## What is in the plugin

| Path | What it is |
| --- | --- |
| `skills/method/SKILL.md` | the founding document: the words, the nine commitments, how work runs |
| `skills/method/*.md` | the reference behind each commitment: the merge rule, tiers as questions, the ratchet, guards and disputes, the pipeline, records, verifiable shape |
| `skills/init/` | `/ratchet:init`: the bare bones for any repository, an `AGENTS.md` naming the four plugins with `CLAUDE.md` linked to it |
| `skills/adopt/` | `/ratchet:adopt`: steps and templates that bind the method to a repository's runner, forge and stack |
| `workflows/feature.js` | `/ratchet:feature`: the four-role pipeline (spec, implement, critic, review) |
| `hooks/` | the PreToolUse guard hook, the SessionStart and SubagentStart hook that puts the stance in every agent's context on an adopted checkout and names a bare one, and their `ratchet_guards` package (`guard-diff` lives here too) |
| `scripts/conformance.py` | tier 1 gate: the repository still carries the method's artefacts; `--self-test` shows it bites |
| `scripts/lowered.py` | tier 0 gate: no threshold lowered against the merge base |
| `docs/research/` | the sources behind the stance |

## Install

```bash
claude plugin marketplace add /home/simon/ratchet   # or owner/repo once pushed
claude plugin install ratchet@ratchet
```

A local marketplace is read in place: edits apply at the next session or `/reload-plugins`. Other machines take the GitHub form and get updates when `version` in `.claude-plugin/plugin.json` rises.

## Use in a repository

Run `/ratchet:init` in any repository for the bare bones. Run `/ratchet:adopt` when the owner wants the gates: it copies the templates, interviews the owner for the binding, and leaves the entry points red until every gate is bound to a tool or accepted as `none` with a reason. The `justfile` and `.github/` templates are the `just` and GitHub examples; any runner and any forge can carry the entry points.

## Develop the plugin

The guard hook locks the installed plugin's root with or without a manifest, and a directory marketplace is read in place, so an agent cannot edit this checkout while the plugin is enabled. Disable it for the session (`claude plugin disable ratchet`, then `/reload-plugins`) and re-enable it afterwards, or install the plugin from a separate clone.

`just check` here runs the hook's tests, lint and types, the conformance self-test, a parse of the workflow and `claude plugin validate`. The method says gates are themselves gated; this is where that applies to the method.

## Change the method

A change to a commitment is a change to every repository on the method. Record it as an ADR here (`docs/adr/`), raise `version`, and let each repository's ADR 0001 say which version it adopted.

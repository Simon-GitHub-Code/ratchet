# ratchet

The Ratchet method: how software is built and verified here, by agents, under tiered deterministic gates that only tighten, guarded by the harness, and judged by critical rationalism. Packaged as a Claude Code plugin so every repository reads one copy of the method and keeps only its own binding.

## What is in the plugin

| Path | What it is |
| --- | --- |
| `skills/method/SKILL.md` | the founding document: the vocabulary, the ten commitments, how work runs |
| `skills/method/*.md` | the reference behind each commitment: epistemology, tiers, ratchet, guards and disputes, pipeline, records, code shape, formal specs |
| `skills/adopt/` | `/ratchet:adopt`: steps and templates that bring a repository onto the method |
| `workflows/feature.js` | `/ratchet:feature`: the four-role pipeline (spec, implement, critic, review) |
| `hooks/` | the PreToolUse guard hook and its `ratchet_guards` package (`guard-diff` lives here too) |
| `scripts/conformance.py` | tier 1 gate: the repository still carries the method's artefacts; `--self-test` shows it bites |
| `scripts/lowered.py` | tier 0 gate: no threshold lowered against the merge base |
| `docs/research/` | the sources behind the stance |

## Install

```bash
claude plugin marketplace add /home/simon/ratchet   # or owner/repo once pushed
claude plugin install ratchet@ratchet
```

A local marketplace is read in place: edits apply at the next session or `/reload-plugins`. Other machines take the GitHub form and get updates when `version` in `.claude-plugin/plugin.json` rises.

## Adopt in a repository

Run `/ratchet:adopt` in the repository. It copies the templates, interviews the owner for `CLAUDE.md`, the binding, the brief and the glossary, and leaves the `justfile` red until every gate is bound. The repository's `CLAUDE.md` then opens with one stanza telling agents to load `ratchet:method`.

## Keep the plugin honest

`just check` in this repository runs the hook's tests, lint and types, the conformance self-test, a parse of the workflow and `claude plugin validate`. The method says gates are themselves gated; this is where that applies to the method.

## Change the method

A change to a commitment is a change to every repository on the method. Record it as an ADR here (`docs/adr/`), raise `version`, and let each repository's ADR 0001 say which version it adopted.

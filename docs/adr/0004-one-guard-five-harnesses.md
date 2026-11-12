---
status: accepted
---
# The method runs in five harnesses through one guard, portable skills and a thin adapter each

The owner builds with Claude Code, Grok, Codex, pi and OpenCode, and wants the method, with its guard, in all of them. Version 0.4.0 was a Claude Code plugin: its skills, its PreToolUse hook and its `feature` workflow. We decided to keep one plugin and make three parts of it portable by construction, with a thin adapter where a harness differs. The skills stay in the Agent Skills `SKILL.md` format every harness discovers, and `docs/harness/<name>.md` maps the two Claude-only details (`${CLAUDE_SKILL_DIR}` and `disable-model-invocation`) plus the tool names a skill uses. The guard stays one standard-library Python program reading Claude Code's PreToolUse JSON on stdin and writing a decision on stdout; Grok and Codex speak that schema and load a plugin's `hooks/hooks.json`, so they need no adapter, while pi and OpenCode get a small TypeScript extension or plugin that translates their tool-call event into that JSON and blocks on `deny`. The plugin is also a pi package (root `package.json` with a `pi` key) and declares its three companion plugins as marketplace dependencies where a harness has the concept (Claude Code). `AGENTS.md` is read by all five. Decided 2026-11-07.

The workflow named below as `workflows/feature.js` is now `workflows/pipeline.js`; ADR 0009 has the `/ratchet:feature` skill run it.

Antigravity is a sixth harness since 0.9.0, on the same guard: it converts the plugin's own hooks on install and needs no adapter file, and `docs/harness/antigravity.md` says how the guard answers it.

## Refutation criterion

The decision is wrong if a harness needs a change to the guard's rules, rather than to its adapter, to refuse the same command the others refuse; or if the adapters drift so that a command denied in one harness runs in another. Check with the corpus in `hooks/tests/test_corpus.py` run through each adapter after the first adoption in each harness, and again after each harness's next major release.

## Considered options

- Stay Claude Code only and tell the owner to run the other harnesses without gates: rejected, the method's claim is that quality is enforced by machines wherever the agent runs.
- One port per harness, each with its own rules: rejected, five rule sets drift, and a drift between them is a hole an agent finds first.
- One guard with adapters (chosen): the rules live once; an adapter is forty lines that marshal an event and act on a decision, and the corpus is the conformance test for every adapter.
- The feature pipeline on every harness: deferred. `workflows/feature.js` is Claude Code's Workflow tool; the roles and sight lines are the method's, and a harness with subagents can run them by hand from `pipeline.md`, but the script does not port and the method says so.
- Formal dependencies: Claude Code's `dependencies` on the marketplace entry, cross-marketplace allowed by `allowCrossMarketplaceDependenciesOn`; the other harnesses have no such field, so `/ratchet:init` installs what is missing with the owner's consent, from each harness's own commands.

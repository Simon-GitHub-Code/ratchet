---
status: accepted
---
# The method is one Claude Code plugin, and each repository keeps only its binding

The owner wants one founding document for every piece of software they build, technology- and domain-agnostic, that any agent reads before working. We decided to package it as a Claude Code plugin named `ratchet`, in its own private repository that is also its own marketplace. The founding document is a model-invoked skill (`ratchet:method`) whose description carries the triggers (session start, a red gate, a denial, a design decision, a review, the pipeline) and whose reference files are reached by pointers from it. A repository on the method opens its `CLAUDE.md` with one stanza telling agents to load the skill, and holds only what the method leaves open: purpose, layers, non-negotiables, stack rules. The tool-neutral enforcement travels with the plugin: the PreToolUse guard hook, the `feature` workflow, the conformance gate and the threshold-lowered gate. Tool-specific gates stay in each repository's binding. Decided 2026-11-07.

Three details here were changed later; the packaging stands. ADR 0002 has `init` write the stanza to `AGENTS.md`, with `CLAUDE.md` a link to it. The `feature` workflow became `workflows/pipeline.js`, which the `/ratchet:feature` skill runs (ADR 0009). The plugin is to be published, so its repository will not stay private.

## Refutation criterion

The packaging is wrong if an agent in an adopting repository starts work without loading the skill in more than an occasional session, or if a method change takes more than one commit here plus a version bump to reach every repository. Check after the first two adoptions.

## Considered options

- A global `~/.claude/CLAUDE.md` holding the method: rejected, it loads in every session including non-software ones, and a thorough method would cost its full length on every turn.
- A `CLAUDE.md` or `.claude/rules/` copied into each repository: rejected, the copies drift and no gate checks them; the owner has already seen this with hand-written instructions.
- An `AGENTS.md` convention for cross-tool reach: deferred. The skill is plain Markdown in a git repository, so other tools read it by URL, and `CLAUDE.md`'s stanza names that URL.
- A plugin without the hook and workflow, documentation only: rejected, the guards are what make "enforced by machines, never trusted" true in a new repository on day one.

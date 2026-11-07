---
status: accepted
---
# The method composes with mattpocock-skills and pstack, and binds to any runner, forge and stack

Version 0.1.0 restated what two installed plugins already do (the test loop, design vocabulary, ADR and glossary formats, review axes, diagnosis, formal specs), with formats that conflicted with theirs, and its conformance gate required a `justfile`, a GitHub pull request template and its own ADR front matter. We decided that the method owns one question, what must be true before a merge, and reaches `mattpocock-skills` and `pstack` by name for the rest: the pipeline's roles work with `tdd`, `interrogate` and `code-review`; records use `domain-modeling`'s formats plus the method's sections (the refutation criterion, the pull request's Conjecture, Criticism, Guard paths, Decisions and Gates); formal verification is `pstack`'s project-local skill, held as a guard path. The gate catalogue became questions per tier, answered by a tool or by `none` with a reason and an ADR, which is green. The conformance gate reads the runner, the forge and the default branch from the binding. The epistemology shrank to the merge rule and six words shared with `conjecture:lexicon`. A new `init` skill lays down `AGENTS.md`, naming the three plugins, with `CLAUDE.md` linked to it. Decided 2026-11-07.

## Refutation criterion

The composition is wrong if an adopting repository on a runner other than `just` or a forge other than GitHub cannot reach a green `check` without editing the plugin, or if a role of the pipeline produces worse findings after delegating to the other plugins' skills than the inlined instructions did. Check after the first adoption on a non-`just`, non-GitHub repository, and after the first ten pipeline runs.

## Considered options

- Keep the inlined rules and accept the overlap: rejected, two vocabularies for one concept and two formats for one record are a variance bug the method exists to remove.
- Depend on the other plugins only by mention, with fallbacks inlined for when they are absent: rejected, the fallback is the duplication again; `init` and `adopt` check the plugins are installed instead.
- Drop the pipeline in favour of `pstack`'s playbooks: deferred; the sight lines, the schemas and the forced verdict are the method's, and nothing else ships them.

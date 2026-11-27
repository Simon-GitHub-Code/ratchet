---
status: accepted
---
# Fail-closed pages lane for non-code changes

Builds on ADR 0028 ([`0028-triggers-earn-safeguards.md`](0028-triggers-earn-safeguards.md)), ADR 0064 ([`0064-every-markdown-file-has-a-kind.md`](0064-every-markdown-file-has-a-kind.md)), and ADR 0069 ([`0069-tool-config-bites-prove-only-the-gates-that-read-it.md`](0069-tool-config-bites-prove-only-the-gates-that-read-it.md)).

**What was asked.** Pure documentation changes (such as `/ratchet:prune pages`, design briefs, or architectural blueprints) still ran every whole-tree code, runtime, and container gate in tier 1 — live server boots, graceful-stop load probes, container builds, artefact scans, and full test suites — even when no code, config, manifest, or guard file changed. Adopted repositories need a fast, stack-agnostic, fail-closed pages lane that skips code and runtime tier 1 gates on pure documentation changes without weakening assurance or allowing a branch to weaken its own gate selection.

## Decision

**1. Classify pages-only changes fail-closed from the merge base (`scripts/pages_lane.py`).**

A change enters the `pages` lane only when every condition holds; any doubt falls back to `full`:

- Neither `RATCHET_BITING`, `RATCHET_FULL`, nor `--full` is set.
- In CI, `GITHUB_EVENT_NAME` is absent or equals `pull_request` (pushes to the default branch, schedules, and manual dispatches always run `full`).
- No tier 2 gate is in the run (`check-deep` and `tier2` always run `full`).
- A non-adoption merge base exists, and both `docs/process/binding.md` and `guards/manifest.toml` are read from the merge base (never from the working tree, so a branch cannot widen its own pages lane).
- `## Speed` at the merge base does not disable the lane (`- Pages lane: none: <reason>`).
- The change touches at least one file (tracked diff against the merge base plus untracked non-ignored files), and every changed path is a Markdown file (`.md`, `.markdown`) or a method-written `docs/features/*/verdict.json` record.
- No changed path matches a guard group in the merge base's `guards/manifest.toml`, any glob in the merge base's `## What each gate reads`, `## Triggers`, `## Dependencies`, `## Image pins`, `## Test paths`, `## Formal verification` (`- Model:` or `- Mirrors:`), or an optional `- Pages exclude:` bullet in `## Speed`.

**2. Skip only known code, runtime, container, and dependency tier 1 gates.**

- Every tier 0 gate still runs (`prose`, `secrets`, `commits`, `fmt-check`, `lint`, `comments`, `suppressions`, `pins`, `flags`, `limits`, `markers`, `snapshots-check`, `ratchet-lowered`).
- Every method, conformance, and documentation tier 1 gate still runs (`change-size`, `guard-diff`, `settings-check`, `conformance`, `triggers`, `incidents`, `doctests`, `doc-coverage`, `bite`), as does any project-specific tier 1 gate outside the known code/runtime set.
- `scripts/parallel.py` and `scripts/ci_jobs.py` skip only the explicitly listed code, runtime, container, and dependency tier 1 gates, printing one explicit `pages-lane:` summary line naming the file count and skipped gate count.

**3. Never let a pages-lane pull request skip full default-branch CI (`scripts/already_proven.py`).**

- `scripts/already_proven.py record` writes `"lane": "pages"` into `proven.json` when the pages lane is active.
- `scripts/already_proven.py judge` refuses to skip default-branch push CI when `proven.json` records `"lane": "pages"`, so every merge onto the default branch still runs the full tier 1 suite.

## Refutation criterion

This decision is wrong, and must be revised, if any of these is observed:

- A change touching any non-Markdown, non-verdict file, any guard path, any trigger path, any gate-read path, or any `- Pages exclude:` path enters the `pages` lane.
- A branch edits its own `guards/manifest.toml` or `docs/process/binding.md` to widen the `pages` lane and skips code gates on that branch.
- A `pages`-lane run skips a tier 0 gate, a method/conformance/documentation tier 1 gate, a custom tier 1 gate, or any tier 2 gate.
- A `pages`-lane pull request causes `scripts/already_proven.py judge` to skip the subsequent default-branch push CI run.
- `RATCHET_BITING=1`, `RATCHET_FULL=1`, or `--full` enters the `pages` lane.

## Considered options

- **Expand `## What each gate reads` with per-gate source globs for every tool.** Rejected: maintaining accurate transitive input globs for dozens of language and runtime tools is brittle and prone to silent under-scoping.
- **Skip `tier0` as well on documentation changes.** Rejected: tier 0 holds `prose`, `secrets`, `commits`, `fmt-check`, `lint`, and `comments`, which directly judge Markdown pages and are already fast.

## Consequences

- `scripts/pages_lane.py` provides the shared fail-closed classifier and gate filter.
- `scripts/parallel.py`, `scripts/ci_jobs.py`, and `scripts/already_proven.py` honor the pages lane automatically with zero required changes to existing `justfile` or `binding.md` files.
- `skills/method/tiers.md` and `skills/adopt/templates/docs/process/binding.md` document the lane and optional `- Pages lane:` / `- Pages exclude:` controls under `## Speed`.

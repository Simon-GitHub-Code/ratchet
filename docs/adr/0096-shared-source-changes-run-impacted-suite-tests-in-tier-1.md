---
status: accepted
---
# Shared-source changes run impacted suite tests in tier 1

Builds on ADR 0073 (`changed_tests.py` and the `### Suites` table under `## Tests`).

Previously, `scripts/changed_tests.py` ran only test files that themselves changed against the merge base. When a pull request edits shared shell, layout, router, or middleware source files without editing any end-to-end test file, tier 1 runs zero end-to-end tests. Cross-route regressions then land on the default branch, stalling merges and autonomous drains until tier 2 catches them after the merge.

## Decision

**1. Optional `### Impact triggers` table under `## Tests`.** A binding may add a `### Impact triggers` subsection under `## Tests` in `docs/process/binding.md` with columns `Sources | Suite | Tests`:

- `Sources`: comma-separated backticked globs matching shared source files, `none: <reason>` when no shared source needs impact selection, or a `<...>` placeholder while unfilled.
- `Suite`: a suite name declared in the `### Suites` table.
- `Tests`: comma-separated backticked globs matching test files of that suite to run when any changed file matches `Sources`.

**2. Impact selection semantics in `scripts/changed_tests.py`.**

- `scripts/suites.py` parses `### Impact triggers` with `parse_impacts(binding, known)`. Bindings without `## Tests`, without an `Impact triggers` table, or with placeholder `<...>` or valid `none: <reason>` rows return `([], [])` so existing bindings behave identically to before. Real rows validate non-empty backticked `Sources` globs, a `Suite` name present in `known`, and non-empty backticked `Tests` globs.
- When `impacts` is non-empty, `changed_tests.py` lists existing tracked and untracked non-ignored files once. When `impacts` is empty, it skips that listing so projects without triggers pay zero extra process overhead.
- Any changed file matching a row's `Sources` globs selects every existing tracked test file matching that row's `Tests` globs for that `Suite`, provided that suite is in scope (including tier-2 suites when `changed_tests.py` runs in tier 1) and belongs to that declared `Suite` in `### Suites`.
- Selected impacted files merge with directly changed test files for each suite in scope, deduplicated and sorted, and run through the suite's `Run files` recipe (or report with its `none: <reason>` when the suite cannot run by file).

## Refutation criterion

This decision is wrong, and must be revised, if any of these is observed:

- A pull request modifies a source file matched by `Sources` without modifying test files, and tier 1 `changed_tests.py` runs zero tests for that row's in-scope `Suite`.
- A binding without `### Impact triggers` changes exit status, selected files, or subprocess calls under `changed_tests.py`.
- A pull request edits an unrelated source file matched by no `Sources` glob, and `changed_tests.py` runs impacted tests anyway.
- An impacted file matched by both `Sources` selection and direct test changes runs twice in one `Run files` invocation.

## Considered options

- **Run the whole tier-2 suite in tier 1 on every pull request.** Rejected: whole end-to-end suites often exceed tier-1 time ceilings, and unrelated pull requests should not pay that wait.
- **Infer impacted tests from an import graph or coverage map.** Rejected (consistent with ADR 0073): static or dynamic maps vary by language and toolchain, whereas explicit globs in `docs/process/binding.md` keep the method independent of any stack.
- **Require `### Impact triggers` in every binding.** Rejected: projects without shared shell or layout code across later-tier suites need no extra configuration.

## Consequences

- `scripts/suites.py` adds `Impact`, `IMPACT_COLUMNS`, `parse_impacts`, and `impacted`.
- `scripts/changed_tests.py` parses impact triggers, gathers existing files only when triggers are declared, validates suite ownership of impacted files, and merges impacted files into the scoped run.
- Projects with shared shell, layout, router, or middleware files can catch cross-route regressions in tier 1 before merge without moving entire tier-2 suites into tier 1.

---
status: accepted
---
# A dependency change is not a guard change

Every preset puts the dependency manifest and its lockfile (`pyproject.toml` and `uv.lock`, `package.json` and `pnpm-lock.yaml`) in the `tool-config` group, because gate configuration lives in them, and the `snapshots` gate copies each one into `quality/snapshots/`. So a feature that adds one library changed three guard paths. In the Rust trial of 2026-11-08 the first owner-away `/ratchet:feature` stopped at the spec stage on `proptest`, a dev-dependency the adoption interview had already agreed, and landing it ahead on the default branch is not open to a stack whose unused-dependency gate (`knip`, `deptry`, `cargo machete`) turns the default branch red. And a guard report that flags every dependency pull request teaches the owner to skim it.

We decided that `guard-diff` lists a guard path apart, with each package's old and new version, when its change only changes dependencies: both sides parse, and they are equal once the dependency tables (`project.dependencies`, `optional-dependencies`, `dependency-groups`; Cargo's dependency tables at the top, under `target` and under `workspace`; npm's four) and the package's own version are set aside. A lockfile holds nothing else. A snapshot is judged as the file it copies. Anything else stays a guard change: another key, a parse failure, a file created or deleted, `[tool.uv.sources]`, an entry pointed at a direct source (git, a path, a URL), or a locked version that falls or cannot be read. The last two came from writing this record's refutation criterion first: a gate tool rolled back through the lockfile, or swapped for a fork through a dependency table, would otherwise have passed as dependency-only. The hook still asks on an `Edit` of the manifest; pipeline agents add a dependency with the package manager and the spec names it. Decided 2026-11-09.

## Refutation criterion

This is wrong if a loosening of a gate reaches a merge as a dependency-only change. The rollback and the fork are covered by tests in `hooks/tests/test_dependencies.py`. The open attempt is an added test-runner plugin that deselects tests or swallows failures: seed one in a trial repository, and if it is listed only as dependency-only and no gate goes red (a test count, a coverage group), the classification is too wide, and additions need an allow-list of package names or a test-count gate in every preset.

## Considered options

- Keep the manifests guarded and send dependency tickets to an owner-present `/pstack:poteto-mode` run: rejected, it makes the most common feature change unbuildable with the owner away, and the flagged pull requests wear out the guard report.
- Guard the manifest section by section in the hook: rejected, permission rules match whole paths, so the settings layer would still ask, and `uv add` writes the file where no hook sees it.
- Move gate configuration out of the manifests into dedicated files and drop the manifests from the guard: rejected, Rust lint levels live in `Cargo.toml`'s `[lints]`, and which file wins when both exist differs per tool.

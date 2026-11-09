# Binding

## Runner

`just`. The `justfile` is the command contract; every tool runs on the system `python3`, so there is nothing to install.

## Gates

| Question | Tool |
| --- | --- |
| unit tests | `python3 -m unittest discover -s tests` |
| lint, types, coverage, mutation | none: a small pure-Python codebase with no third-party toolchain yet; ADR 0001 accepts the gap |

`check` is tier 0 plus tier 1 and is the only entry point today.

# Verifiable shape

How code is designed and tested belongs to the other plugins: `mattpocock-skills:codebase-design` for modules, seams and adapters, `mattpocock-skills:tdd` for the loop and the seams under test, `/pstack:architect`, which the owner types, before code crosses a boundary. The method adds only the rules that exist so the gates can see the code. Each one has a gate in the binding.

- **Logic where every verifier reaches.** A decorated, wrapped or generated function is a docstring plus one call to a plain function with the same parameters. Logic inside a wrapper escapes the mutation tool while the coverage report still calls it covered. A fitness test enforces the shape.
- **Declared layers are checked.** A project that declares layers in `AGENTS.md` has a tool checking the dependency direction and a fitness test checking it again, so a violation cannot hide behind import-time side effects. A path declared pure has no I/O, clock, randomness or environment, and is an absolute path.
- **Absolute paths.** The gate tools, and every path the binding declares pure, are held at 100% line and branch coverage by the coverage tool itself, with no exclusion reaching them.
- **Limits are hard.** Size and complexity limits live in the lint config and a fitness test, tighter on absolute paths; the binding holds the numbers. Split the unit rather than raise a limit.
- **A red test stays red.** A test that cannot pass becomes a finding. A test that must change carries, in the pull request, the criticism of the test that justifies the change.
- **Suppressions and policy constants are cited.** A tool directive names its rule and a reason. A constant that enforces a rule in a fitness test names the ADR that holds it, and a fitness test checks that the number exists.
- **Generated artefacts are committed**, and a gate regenerates them and diffs.
- **A dependency is a decision**, stated in the pull request, or in an ADR when it shapes the architecture. Tool versions come from lockfiles; a binary outside them is pinned by version and checksum.
- **One reviewed change at a time, and the merge judge decides who merges it.** A change the gates can judge merges itself; a guard path, a one-way door or an open finding waits for the owner. The binding names the branch flow and the commit convention; a hook enforces the convention where there is one.

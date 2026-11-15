---
status: accepted
---
# A stack that compiles builds once and reuses its cache

Amends ADR 0019 (what the binding's Speed section asks) and says how the bite proof and the rehearsal meet it.

First-hour eval runs on harness `fa9ee31` (plugin 0.11.1), one persona at a time on empty repositories, measured adoption by the kind of work each command did:

- `rust-shortener`, a stack that compiles: 42.3 minutes, against 34.7 for `go-todo-cli`, a stack that compiles quickly. Practice runs of a sample feature took 8.7 minutes against 3.6: the first round went red on dead code and lint, so a second, cold round followed, and its one `check` took 3.2 minutes. Planting a fault per gate took 7.1 minutes against 3.0, since each gate's fault meant a rebuild. Toolchain and tool installs took 4.6 minutes against none: four tools were compiled from source, and one twice after the toolchain version changed. The agent split the program into three separately compiled packages from the start, so every build compiled three. The machine was otherwise idle; its load rose only during the run's own gate runs.
- `kotlin-recipes`, a stack whose builds are slow: adoption was still unfinished when the 90-minute run ended, after 86.7 minutes. 37.9 of them were spent in commands that called the build tool directly, before counting the gates that build through it. Outside work used more than a quarter of the machine in 26% of its samples, so the 86.7 is an upper bound; the shape is not load, since the quiet 40 minutes in its middle went the same way.

The method named no cure, and the plugin names no stack (ADR 0042), so the cure is stated as concepts.

## Decision

**A stack that compiles compiles each unit once and reuses what it built.** Its binding keeps one content-addressed compiler cache outside the work tree: it stores each compiled unit under a key of its inputs (sources, flags, toolchain) and copies the result out on a hit, so a planted fault is a miss and never a stale hit. Every gate, scratch copy and worktree reuses it, and CI restores it. It is never a shared build output directory: each work tree, scratch copy and gate keeps its own build output inside itself, so parallel lanes never judge one another's builds. The bite registry's `setup` installs and builds, and points that cache outside the tree. `setup` runs in every scratch copy: the first copy runs it alone and fills the cache, the later copies' builds are cache hits, and each planted fault then rebuilds incrementally in its own copy. Each tool is installed as the publisher's prebuilt release where one exists, and not built from source, at a pinned version with the recipe checking the publisher's checksum; the `pins` gate does not read a download's checksum, so the recipe owns that check. The toolchain is pinned before the first build. The architecture round states that each separately compiled unit adds build time to every gate, and that the imports gate can hold a layer rule inside one unit.

**The rehearsal stays a fresh checkout.** The rehearsal may reuse only that content-addressed cache, outside the tree, the one CI restores, and it is not "a cache carried in". It holds compiled units keyed on their inputs, and the rehearsal's build output stays its own. The cache cannot supply a missing config or tool; a config the adoption owes still fails the rehearsal. The two-round limit and the planted faults are unchanged.

**A recipe still builds what it runs.** An incremental build from a cache is a build.

Decided 2026-11-15.

## Refutation criterion

This is wrong if a compiled stack's rehearsal or bite proof, run with the shared cache, still takes most of its time compiling, or if a rehearsal passes on the cache and the first real checkout then fails on a missing config or tool.

## Considered options

- A fixed rule per stack, naming each stack's cache and install route: rejected, it breaks the stack-agnostic principle (ADR 0042) and goes stale with each tool.
- Drop the cold rehearsal for a compiled stack: rejected, it loses its proof that the first feature finds every config it needs.
- A shared build output directory across copies: rejected, parallel lanes would judge each other's builds, a clean tree's or another lane's planted fault, and gates that share an output cannot run at once.

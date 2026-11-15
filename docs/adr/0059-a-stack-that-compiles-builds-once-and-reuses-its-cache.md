---
status: accepted
---
# A stack that compiles builds once and reuses its cache

Amends ADR 0019 (what the binding's Speed section asks) and says how the bite proof and the rehearsal meet it.

A first-hour eval adopted the method on a fresh project of a stack that compiles, and on one that compiles quickly. The machine was idle. Adoption took 42 minutes on the first and 31 on the second, and the gap is compile time, by the kind of work each command did:

- Practice runs of a sample feature: 8.7 minutes against 1.1. The first round went red on dead code and lint, so a second, cold round followed, and its one `check` took 3.2 minutes.
- Planting a fault per gate: 7.1 minutes against 3.9. Each gate's fault meant a rebuild.
- Toolchain and tool installs: 4.6 minutes against 2.0. Four tools were compiled from source, and one twice after the toolchain version changed.
- The agent split the program into three separately compiled packages from the start, so every build compiled three.

The method named no cure, and the plugin names no stack (ADR 0042), so the cure is stated as concepts.

## Decision

**A stack that compiles builds once and reuses what it built.** Its binding keeps one content-addressed compiler cache outside the work tree: it stores each compiled unit under a key of its inputs (sources, flags, toolchain) and copies the result out on a hit, so a planted fault is a miss and never a stale hit. Every gate, scratch copy and worktree reuses it, and CI restores it. It is never a shared build output directory: each work tree, scratch copy and gate keeps its own build output inside itself, so parallel lanes never judge one another's builds. The bite registry's `setup` installs and builds, and points that cache outside the tree: the first scratch copy is set up alone, so the cache is filled once, and each fault then rebuilds incrementally. Each tool is installed as the publisher's prebuilt release where one exists, and not built from source, at a pinned version with the recipe checking the publisher's checksum; the `pins` gate does not read a download's checksum, so the recipe owns that check. The toolchain is pinned before the first build. The architecture round states that each separately compiled unit adds build time to every gate, and that the imports gate can hold a layer rule inside one unit.

**The rehearsal stays a fresh checkout.** The rehearsal may reuse only that content-addressed cache, outside the tree, the one CI restores, and it is not "a cache carried in". It holds compiled units keyed on their inputs, and the rehearsal's build output stays its own. The cache cannot supply a missing config or tool; a config the adoption owes still fails the rehearsal. The two-round limit and the planted faults are unchanged.

**A recipe still builds what it runs.** An incremental build from a cache is a build.

Decided 2026-11-15.

## Refutation criterion

This is wrong if a compiled stack's rehearsal or bite proof, run with the shared cache, still takes most of its time compiling, or if a rehearsal passes on the cache and the first real checkout then fails on a missing config or tool.

## Considered options

- A fixed rule per stack, naming each stack's cache and install route: rejected, it breaks the stack-agnostic principle (ADR 0042) and goes stale with each tool.
- Drop the cold rehearsal for a compiled stack: rejected, it loses its proof that the first feature finds every config it needs.
- A shared build output directory across copies: rejected, parallel lanes would judge each other's builds, a clean tree's or another lane's planted fault, and gates that share an output cannot run at once.

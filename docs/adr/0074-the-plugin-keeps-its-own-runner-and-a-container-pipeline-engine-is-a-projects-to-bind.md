---
status: accepted
---
# The plugin keeps its own runner, and a container pipeline engine is a project's to bind

Settles the owner's question of 2026-11-16, whether Ratchet should adopt Dagger. Read with ADR 0071 and ADR 0073, which answer the same slow run from inside the plugin.

**Today** the plugin runs a tier's gates with its own runner: one recipe per gate, a script that runs a tier's gates at once, and the same entry points on a developer's machine and in CI. One run on a small ticket took three and a half hours, and part of that was work a cache would have saved: `check` proved an unchanged tree three times, the product was built about forty times outside `check`, most of them for a later-tier suite or a role's own probe, and three critique rounds each rebuilt the merge base to compare against it.

Dagger is an engine that runs each step in a container and caches a step by the content of its inputs. ADR 0071 declined to reuse a report from one run to the next because what a gate reads is wider than the tree. An engine that makes a gate's inputs complete would answer that. So the question deserved evidence, and a delegate read Dagger's documentation, releases and open issues on 2026-11-16.

Any answer must keep four things:

- **The plugin names no stack tool** where an agent reads it (ADR 0042), and each project binds its own tools.
- **A red CI job is reproduced by one local command.**
- **Every project can run its gates**, on a developer's machine, inside an agent session under the guard, and on a forge's hosted runners.
- **A green gate means the gate ran on this tree**, or on inputs that are provably the same.

## Decision

**1. The plugin does not require a container pipeline engine, and does not ship support for one.** Its own runner stays. The work that makes a `check` cheaper goes ahead inside it: gates that each use every core do not run beside each other (#210), a check runs the suite once (ADR 0071), mutation is incremental on a pull request (#212), and a check runs the test files a change touches (ADR 0073).

**2. A project may bind an engine of its own.** A project that binds an engine puts it under the entry points (`tiers.md`, "One pipeline, the same everywhere"): CI still calls the entry point, and the entry point calls the engine, so a red job is still one command. The proof that gates can fail and a gate that runs twice to find instability are never answered from the engine's cache. The project owns the engine's version, its image and its cache.

**3. This is decided on what was true on 2026-11-16, and those facts are the conditions to reopen it.**

- The engine runs as a privileged, rootful container and cannot run rootless. A machine with a rootless runtime only cannot start it. Access to the engine is equal to root on the machine, outside what the guard sees.
- On a forge's hosted runner the engine's cache is lost after each job. A cache shared between jobs, or between a developer's machine and CI, comes from the vendor's paid service.
- Its 1.0 line was in beta, with defects in the cache opened in the month before: a selection of checks by filter did not cache from one session to the next, the cache key depended on file permission bits, and an unclean stop lost the whole cache. A gate run through its own entry point is a selection by filter.
- On a warm local engine it would remove the repeated `check` on an unchanged tree, the repeated product builds and the merge-base rebuilds. It would not change gates slowing each other inside one `check`, and it has no bearing on a fix loop that does not converge (ADR 0072).
- A gate that reads history needs its base passed as an input. The proof that gates can fail, and a gate that runs twice to find instability, must not be replayed from a cache.

## Sources, read 2026-11-16

- The engine cannot run rootless: the engine's operator manual, https://github.com/dagger/dagger/blob/main/core/docs/d7yxc-operator_manual.md
- The cache is lost after each job on an ephemeral runner: the 1.0 self-hosting guide, https://raw.githubusercontent.com/dagger/dagger/main/docs/versioned_docs/version-1.0-beta/self-hosting/index.mdx
- The 1.0 line in beta: https://docs.dagger.io/install and the post of 2026-10-23 at https://dagger.io/blog
- A selection by filter does not cache from one session to the next: https://github.com/dagger/dagger/issues/15044
- The cache key depends on permission bits: https://github.com/dagger/dagger/issues/15034
- An unclean stop loses the cache: https://github.com/dagger/dagger/issues/14956

## Refutation criterion

This is wrong, and the question reopens, when all of these hold:

- The engine's 1.0 is released and the three cache defects above are closed.
- A spike on one real adopted project passes: a rerun on an unchanged tree reports every gate as cached and takes under a tenth of the plain run; a change to a test file alone leaves the product build cached; a change to a lockfile runs everything again; a change to the host's environment changes nothing; and the cache survives a restart of the engine.
- The plugin's own runner, after the work of point 1, still leaves a run spending more than a tenth of its time on gates that re-prove an unchanged tree. ADR 0073's time line measures that.

It is also wrong if a project that binds an engine under point 2 cannot keep the one-command rule, since then the binding's line promises what it cannot hold.

## Considered options

- **The engine as the required runner of every adopted project.** A project that cannot run a privileged container would have no gates at all: a sandboxed agent, a restricted CI, a machine with a rootless runtime. Every project would carry a beta's defects and a version lock between the command line tool and the engine.
- **The engine as a runner the plugin supports beside its own.** A generic module, binding fields for the image and the engine's version, new guard paths, and changes to the proof and the budget so a replay is not read as a run. Medium work in the plugin and high work for each project, for a cache that does not persist on hosted runners. Worth building only after the spike passes.
- **The engine for the plugin's own CI only.** The slow run was measured in an adopted project, not here. It buys nothing against it.
- **A hermetic build system with a remote cache.** It answers ADR 0071's objection more strongly, since every input is declared. The cost falls on each project, which must describe its whole toolchain in that system's terms. A project may bind one under point 2.
- **Reuse a `check` result by the tree's content, in the plugin's own runner.** ADR 0071 declined it: without a sandbox, nothing proves the inputs are the same. ADR 0073 left it to a later record, for a runner that can make a gate's inputs complete. This record finds no such runner today, and point 3 says what would be one.

## Consequences

- The slow run is answered inside the plugin: fewer runs (ADR 0072 and ADR 0073) and cheaper runs (#210, ADR 0071, #212). No project needs a container runtime to run its gates.
- A repeated `check` on an unchanged tree still runs. Its count is in every run's result, so the cost of this decision is measured.
- The evidence is dated. A reader who reopens the question reads the product as it is then, against the conditions above.

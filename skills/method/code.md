# Code shape

Agent-written code drifts unless constrained by deterministic checks, so every rule here has a gate behind it in the binding. Rules are stated for any language; the binding names the construct.

## Layers

Inner to outer: **core** (domain), **application**, **adapters**, **interfaces**. Each layer imports only inward. A dependency tool checks it and a fitness test checks it again, so a violation cannot hide behind import-time side effects.

- **Core** is pure: no I/O, no clock, no randomness, no identifiers minted, no environment, no concurrency, no mutable module state. Values are immutable. Every public function carries a contract (pre- and post-conditions) that runtime checks, symbolic checking and property tests all read. Time is a value the core receives.
- **Application** holds use cases and talks outward only through **ports** it declares: structural interfaces, in the layer that needs them. A port can encode policy in its shape: a read-only port has no write method, ever.
- **Adapters** implement one port each for one external system and ship a **fake** in the tests. Tests of application code use the fake, never the adapter. Clock, randomness, environment and settings live here, injected through ports.
- **Interfaces** (HTTP, CLI, UI) are thin: no business logic. A UI shares nothing with the service except a generated contract, committed and checked by regeneration.
- A third-party SDK is confined to one module. A numeric or performance kernel sits behind an explicit seam so it can be replaced.

## Values

Every value type in the core is immutable, exact in representation (integers and rationals for quantities that must add up; a currency or unit carried with the amount), and has: a contract on its public surface, a shared generator registered where critics can reuse it, and a property test of its laws (identity, associativity, commutativity, round trips, conservation, ordering), preferably against an independent oracle. Wrong states are unrepresentable rather than validated.

## Logic where every verifier reaches

A decorated or wrapped function is a docstring plus one call to an undecorated core function with the same parameters. Logic inside a wrapper escapes the mutation tool while the score still reports it covered; a post-condition that restates the body is exactly the kind of test mutation testing exists to doubt. A fitness test enforces the shape.

## Tests

- Test first. Write the test, watch it go red for the right reason, make it green, refactor. A test that was never red proves nothing.
- Property tests state laws, not examples, with deterministic seeds and the default example count or more.
- Fitness tests work from the syntax tree, never import the modules they judge, and carry a synthetic must-fail case: the checker itself must bite.
- A test is never weakened to pass. A test that cannot pass stays red and becomes a finding.
- Contract tests property-test an interface against its published schema.

## Size and complexity

Hard limits, held in the lint config and a fitness test, tighter in the core. Split the function rather than raise a limit. Defaults, core / elsewhere:

| Measure | Core | Elsewhere |
| --- | --- | --- |
| cognitive complexity | 8 | 15 |
| cyclomatic complexity | 6 | 10 |
| nesting depth | 2 | 3 |
| function lines | 30 | 50 |
| file lines | 400 | 400 |

A metric the tools do not agree on is pinned by a golden corpus in the repository.

## Comments and docstrings

A comment has one of three forms: `Why: <reason>`, `ADR NNNN: <reason>` naming an ADR that exists, or a tool directive naming its rule then `-- <reason>`. Reasons are ten characters or more. At most `ceil(SLOC / 20)` non-directive comments per file. Comment text that parses as code fails the gate: delete dead code, git keeps it. Docstrings are required in the core and application layers and written elsewhere only when they say something the signature does not.

## Dependencies and generated artefacts

A new dependency is a design decision stated in the pull request, or an ADR when it shapes the architecture. Tool versions come from the lockfiles; binaries outside them are pinned by version and checksum. Generated artefacts (API clients, schemas, snapshots) are committed and a gate regenerates them and diffs.

## Commits

Conventional commits, `type(scope): summary`, enforced by a hook. One pull request per change, trunk-based, branched from main. Attribution trailers come from the harness in that session, never copied from history.

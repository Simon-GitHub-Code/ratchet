# ADR 0102: High-consequence assurance questions and trace validation

- Status: accepted
- Decided: 2026-11-27

## Context

ADR 0101 introduced `- Stakes: standard | high-consequence` so repositories whose defects carry severe financial, safety, medical, custody, or regulatory consequences can tighten triggers and merge rules without burdening hobby or solo projects.

Beyond earlier mutation testing, earlier rollback checks, and human review on absolute paths, high-consequence systems fail catastrophically from six generic failure classes that standard suites rarely enforce deterministically:

1. Ambient non-determinism (wall-clock reads, unseeded randomness, nondeterministic map iteration) that breaks deterministic replay of state transitions.
2. Imprecise binary floating-point or untyped numeric representations used for exact quantities (monetary amounts, physical dosages, inventory counts, rates, or units).
3. Silent formula or algorithm bugs that unit tests written by the same author mirror; independent differential oracles (naive reference models or dual implementations) catch semantic divergence.
4. Duplicate side effects on retry and race conditions under concurrent updates against persisted state.
5. Stale external data or missing input bounds causing fail-open decisions when an external dependency degrades or returns out-of-range values.
6. Corrupted persistent state where schema constraints, double-entry balances, or state invariants are only checked in application code rather than enforced at the storage boundary.

In addition, under `high-consequence` stakes, redacting credentials and sensitive fields from logs and telemetry matters from the first entry point rather than waiting for `code:access-control` to arrive. Finally, when binding formal verification, a formal model stays true to the code most reliably when tests emit execution traces validated against the model and when `code:state-machine` matches state-transition constructs rather than only file names.

## Decision

1. **New code kind.** Add `exact-quantity` under `[kinds]` in `skills/method/application-types.toml` for values that must keep exact precision or carry units rather than bare floating-point numbers. Because only a `stakes = "high-consequence"` question uses `code:exact-quantity`, standard repositories are never asked to add it to `## Triggers`.
2. **Six universal high-consequence questions.** Add six Tier 1 `[[question]]` entries marked `stakes = "high-consequence"` to `skills/method/application-types.toml`, `skills/adopt/templates/docs/process/binding.md`, and `skills/adopt/templates/justfile`, together with nine named `[proofs]`:
   - `execution determinism and replay` (`just determinism`, `trigger = "code:source"`, `collection = true`, proofs `ambient-nondeterminism`, `replay-drift`)
   - `exact-value and unit safety` (`just exact-values`, `trigger = "code:exact-quantity"`, proof `imprecise-or-untyped-quantity`)
   - `differential oracle agreement` (`just oracle`, `trigger = "code:source"`, `collection = true`, proof `oracle-divergence`)
   - `idempotency and concurrent write safety` (`just idempotency`, `applies-to = ["service", "app", "job"]`, `trigger = "code:stored-format"`, `collection = true`, proofs `duplicate-action`, `concurrent-race`)
   - `fail-closed on stale or out-of-bounds input` (`just fail-closed`, `applies-to = ["service", "app", "cli", "job"]`, `trigger = "code:outbound-call"`, `collection = true`, proofs `stale-input-accepted`, `bound-bypassed`)
   - `schema and ledger integrity` (`just schema-integrity`, `applies-to = ["service", "app", "job"]`, `trigger = "code:stored-format"`, `collection = true`, proof `schema-integrity`)
3. **Promoted log-redaction trigger.** Add `high-consequence-trigger = "code:entry-point"` to `sensitive data kept out of logs` so high-consequence repositories bind runtime log redaction on their first entry point.
4. **Zero noise for standard repositories.**
   - Existing repositories with `- Stakes: standard` (or no `- Stakes:` line) may omit the six `high-consequence` rows completely without conformance errors or upgrade debt.
   - Newly adopted repositories starting from the templates with `- Stakes: standard` may write `none: standard stakes` (or `none: not high-consequence`) in those six rows without an ADR, mirroring `none: not a <type>`. When `- Stakes:` is switched to `high-consequence`, `none: standard stakes` is rejected and every row must be bound, `later: <trigger>`, `none: not a <type>`, or accepted by an ADR.
5. **Formal trace-validation and state-machine trigger guidance.** Document in `skills/method/tiers.md` and `skills/safeguard/SKILL.md` that formal verification gates (`just formal`) may validate recorded execution traces emitted by real tests against the formal model to satisfy `code-departs-from-model`, and advise matching `code:state-machine` on state-transition constructs rather than only filenames.

## Considered options

- **Domain-specific financial or safety gates.** Rejected because Ratchet must remain completely generic and stack-agnostic; every question above applies equally to finance, healthcare, robotics, logistics, custody, and infrastructure control planes.
- **Require all six questions on every repository.** Rejected because hobby and solo projects should not carry differential oracle, replay, or ledger-integrity gates before they need them.
- **Keep the six questions only as prose advice in skills.** Rejected because prose advice without catalogue questions, named proofs, and conformance enforcement drifts silently and cannot prove that each gate bites.

## Consequences

- High-consequence repositories get deterministic, bite-proven Tier 1 gates for determinism/replay, exact values/units, differential oracles, idempotency/concurrency, fail-closed bounds/staleness, and storage-boundary integrity, plus early log redaction.
- Standard repositories experience zero new upgrade obligations and can keep template rows cleanly marked `none: standard stakes` without writing ADRs.

## Refutation criterion

Wrong if standard repositories gain new conformance findings or upgrade debt from this change, or if high-consequence repositories cannot express these six assurance properties generically across arbitrary stacks without naming domain- or stack-specific tools in the method.

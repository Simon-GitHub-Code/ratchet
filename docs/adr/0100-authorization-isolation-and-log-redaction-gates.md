---
status: accepted
---
# Authorization isolation and log redaction gates

Builds on ADR 0025 ([`0025-a-first-class-concern-is-held-by-a-check-that-names-no-tool.md`](0025-a-first-class-concern-is-held-by-a-check-that-names-no-tool.md)) and ADR 0028 ([`0028-triggers-earn-safeguards.md`](0028-triggers-earn-safeguards.md)).

**What was asked.** `skills/method/application-types.toml` already defines the `access-control` kind and reopens the architecture when it arrives, but asks no deterministic gate question of it. Two classes of security regression therefore slip through Tier 1 checks:

- **Missing authorization or cross-tenant leakage.** Static security scanners do not catch an entry point registered without an explicit authorization policy, or a query or cache key that lets one actor or tenant read or modify another's data.
- **Runtime secret and sensitive-payload leakage.** Git-tree secret scanners check committed files, not structured logs, error responses or telemetry spans emitted at runtime.

## Decision

**1. Bind `authorization and tenant isolation` to `code:access-control`.**

- `skills/method/application-types.toml` adds the Tier 1 question `authorization and tenant isolation` (`applies-to = ["service", "web-ui", "app", "job"]`, `trigger = "code:access-control"`, `collection = true`, `proofs = ["unprotected-route", "cross-tenant-read"]`), bound to `just authz-check` in the binding and justfile templates.
- `unprotected-route` proves the gate fails when an entry point is registered with no explicit authorization policy.
- `cross-tenant-read` proves the gate fails when one actor or tenant reads or modifies another actor's or tenant's record.

**2. Bind `sensitive data kept out of logs` to `code:entry-point and code:access-control`.**

- `skills/method/application-types.toml` adds the Tier 1 question `sensitive data kept out of logs` (`applies-to = ["service", "web-ui", "app", "cli", "job"]`, `trigger = "code:entry-point and code:access-control"`, `collection = true`, `proofs = ["secret-logged"]`), bound to `just log-redaction` in the binding and justfile templates.
- `secret-logged` proves the gate fails when a credential, token or sensitive field is emitted to runtime logs, error responses or telemetry.

**3. Smooth upgrade for existing bindings.**

- Repositories with no `access-control` instances upgrade automatically: `scripts/upgrade.py` adds both rows as `later:` (or `none: not a ...` for a library) with no blocking finding.
- Repositories that already hold `access-control` instances owe the new rows in `quality/owed.toml` on upgrade until `/ratchet:safeguard` binds them.

## Refutation criterion

This decision is wrong, and must be revised, if any of these is observed:

- A bound `authorization and tenant isolation` gate lacks a `proof = "unprotected-route"` or `proof = "cross-tenant-read"` `[[bite]]` in `quality/bites.toml` and conformance passes green.
- A bound `sensitive data kept out of logs` gate lacks a `proof = "secret-logged"` `[[bite]]` in `quality/bites.toml` and conformance passes green.
- Either question left `later:` (before its trigger fires) or `none: not a ...` (on a library) fails conformance for missing proof bites.
- Adding a second `access-control` instance while `authorization and tenant isolation` is still `later: code:access-control` passes `scripts/triggers.py` green.

## Considered options

- **Fold authorization and log redaction into `static security analysis` (`just sast`).** Rejected: SAST inspects syntax patterns, not whether a live router enforces default-deny authorization, whether a multi-tenant store isolates Tenant A from Tenant B, or whether runtime logs emit unredacted credentials.
- **Trigger `sensitive data kept out of logs` on `code:entry-point` alone for every project.** Rejected: hobby and single-user tools with no access control or sensitive state would pay the setup cost prematurely; compound `code:entry-point and code:access-control` matches Ratchet's trigger discipline while firing automatically once auth arrives.

## Consequences

- `skills/method/application-types.toml` adds three `[proofs]` entries (`unprotected-route`, `cross-tenant-read`, `secret-logged`) and two Tier 1 questions.
- `skills/adopt/templates/docs/process/binding.md` and `skills/adopt/templates/justfile` add `authz-check` and `log-redaction` to Tier 1.
- `skills/method/tiers.md` and `skills/safeguard/SKILL.md` document both questions and their named proof bites.

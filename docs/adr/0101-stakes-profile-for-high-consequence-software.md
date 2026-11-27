---
status: accepted
---
# Stakes profile for high-consequence software

Builds on ADR 0028 ([`0028-triggers-earn-safeguards.md`](0028-triggers-earn-safeguards.md)) and ADR 0031 ([`0031-records-alone-and-the-adoption-merge-without-the-owner.md`](0031-records-alone-and-the-adoption-merge-without-the-owner.md)).

**What was asked.** Ratchet's trigger and merge defaults assume hobby and solo projects first: mutation testing waits for `life:depended-on`, migration rollback waits for `life:deployed and code:stored-format`, one-way doors and altered recorded outputs pass unattended before life events are declared, and product code outside `guards/manifest.toml` auto-merges once CI is green. Software whose mistakes carry severe financial, safety, medical, custody or regulatory consequences cannot wait for live users before turning on core assurance or human review of critical logic.

## Decision

**1. Declare `- Stakes:` in `docs/process/binding.md`.**

- A binding declares `- Stakes: standard` (default when absent, preserving full compatibility with existing repositories) or `- Stakes: high-consequence` beside `- Application types:` and `- Life events:`.
- `scripts/application_types.py` validates `- Stakes:` when present and exposes `stakes_of(binding)`. Unknown values fail `conformance`.

**2. Promote triggers and unlock high-consequence questions in the catalogue.**

- `skills/method/application-types.toml` supports two optional fields on `[[question]]`:
  - `high-consequence-trigger`: replaces `trigger` when `- Stakes: high-consequence` is declared. `changed-code mutation` and `full mutation` promote from `life:depended-on` to `code:source`; `rolls back safely` promotes from `life:deployed and code:stored-format` to `code:stored-format`.
  - `stakes = "high-consequence"`: marks questions required only under `- Stakes: high-consequence`, so `standard` repositories carry no extra boilerplate rows.
- `scripts/conformance.py` uses `stakes_of(binding)` when checking required rows, `later:` triggers, and required `## Triggers` kinds.

**3. Require owner review on high-consequence critical paths (`scripts/merge_ready.py`).**

- `scripts/merge_ready.py` reads `- Stakes:` at the merge base and default-branch tip (so a branch cannot lower its own stakes).
- Under `high-consequence`:
  - Any change touching a path matched by `## Absolute paths` in `docs/process/binding.md` or an `absolute = true` coverage group in `quality/thresholds.toml` returns `stakes` and routes to `merge: owner` for human code review.
  - One-way doors and altered recorded outputs (`expect-output`) are never waived before life events are declared.

**4. Surface stakes during adoption and ticket operability.**

- `skills/adopt/SKILL.md` asks `adopt.stakes` once during adoption, writes `- Stakes:`, and under `high-consequence` instructs the adopting agent to bind language type-escape bans and package install-script hardening as stack rules and gates.
- `skills/conjecture/SKILL.md` adds `- Determinism:`, `- Idempotency:`, and `- Bounds & fail-closed:` to `## Operability` when `- Stakes: high-consequence` is declared.

## Refutation criterion

This decision is wrong, and must be revised, if any of these is observed:

- An existing repository without a `- Stakes:` line fails conformance or changes its required triggers or merge behavior.
- A repository declaring `- Stakes: high-consequence` accepts `later: life:depended-on` on `changed-code mutation` or `full mutation`, or `later: life:deployed and code:stored-format` on `rolls back safely`.
- A pull request in a `high-consequence` repository edits a path under `## Absolute paths` (or an `absolute = true` coverage group), triggers a one-way door, or alters a recorded output, and `scripts/merge_ready.py` returns `merge: agent`.
- A branch edits `- Stakes: high-consequence` to `standard` on itself and bypasses owner review for that change.

## Considered options

- **Require every project to enable mutation testing and human review on absolute paths from commit one.** Rejected: contradicts ADR 0028 and imposes heavy friction on hobby, exploratory, and solo repositories.
- **Encode domain-specific rules (such as financial trading checks) directly in Ratchet.** Rejected: Ratchet is stack- and domain-neutral; consequence severity is orthogonal to domain and applies equally to financial, safety, medical, custody, and infrastructure systems.

## Consequences

- `scripts/application_types.py`, `scripts/conformance.py`, and `scripts/merge_ready.py` honor `- Stakes: standard | high-consequence`.
- `skills/adopt/templates/docs/process/binding.md`, `skills/adopt/SKILL.md`, `skills/conjecture/SKILL.md`, `skills/method/application-types.toml`, `skills/method/tiers.md`, and `skills/method/guards.md` document and wire the profile.
- Standard repositories experience zero behavioral change and zero upgrade churn.

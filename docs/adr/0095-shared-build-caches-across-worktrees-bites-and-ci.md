---
status: accepted
---
# Shared build caches across work trees, bites, and CI

Settles issue #358. Builds on ADR 0019 (speed from the first commit), ADR 0071 (shared report producers), ADR 0078 (slow unnarrowed bites), and ADR 0088 (measured loop budgets). Distinguishes explicitly from #301.

An adopted project with a compiled server ran a 180 s cold build where a warm incremental build took 30 s. None of the extra 150 s came from product changes.

- **Fresh work trees.** The pipeline, the drain, and measurement work trees started without the checkout's build cache, paying the full 180 s cold build on every agent run.
- **Whole-tree build bites.** Planting one compile error and running the whole-tree build gate cost 180.4 s. Narrowing the bite with `args` to one entry point cut it to 4.4 s, dropping total project bite time from 257 s to 57 s across 16 faults. `project_bite.py` already logged a note, yet nothing brought it into the route.
- **CI caches.** Cache keys that ended in commit hashes or omitted input prefixes missed exact matches across commits, or risked restoring stale caches from other lockfile or toolchain versions.

What #301 proposed was caching a gate's green verdict by a plugin-computed digest of product inputs, which opened stale greens. Here the plugin never hashes product source and never skips a gate verdict. Every gate still runs its build tool, and the build tool itself checks every input for staleness against its own incremental cache.

## Decision

1. **The binding names the build under `## Speed` (PR B1).** An optional `- Build:` bullet records the build gate, its cache paths (or `none` when non-incremental), measured cold and warm seconds, its reuse policy (`moved`, `shared `ENV=dir``, or `none: <reason>`), and the consumer gates that read its build output (`smoke`, `load-smoke`, `graceful-stop`, `config-refusal`, `e2e`, or `none`). `scripts/build_cache.py` parses and validates the declaration; `scripts/conformance.py` accepts bindings without `- Build:` or with the `<...>` placeholder, and rejects malformed lines or unknown gate names. Adoption measures cold and warm times and tests moved-cache survival once; `/ratchet:safeguard` measures them again when the first build of a kind arrives.

2. **Fresh work trees and bite copies start warm (PR B2).** When the plugin creates a work tree or bite copy, `scripts/build_cache.py` seeds `moved` cache paths from the source checkout before the first run, or sets the declared `shared` environment variable outside the work tree. Tools that support neither keep `reuse none: <reason>` and report their cold time honestly.

3. **Build once per run in `parallel.py` (PR B3).** Gates listed as `consumers` of the `- Build:` declaration depend on the build gate in `parallel.py`, so the build gate runs once before its consumers rather than forcing projects into serial groups or letting each consumer rebuild from scratch.

4. **Narrowed bite by default, and actionable route findings (PR B1, amending ADR 0078).** The bite of a whole-tree build gate passes `args` targeting one entry point by default. When `.ratchet/timing.jsonl` records a slow unnarrowed bite (no `args`, no `whole` reason, over `[ceilings.seconds].tier0`), `scripts/route.py` surfaces it as a concrete `also:` proposal naming the gate, bite, elapsed seconds, `tier0` ceiling, and the cure (`args` or `whole = "<reason>"`), rather than leaving it only in `conformance` output.

5. **Cold-build detection and reporting (PR B3).** After a run, a build that takes near its recorded cold time where a warm cache was expected is reported with its cause (`fresh tree`, `no cache`, or `cache rejected`), extending #298 from CI cache restores to local and agent runs.

6. **CI build caches key on inputs, not product source (PR B1).** CI build cache steps key on toolchain version, lockfile, and `matrix.gate` (plus `matrix.shard` when sharded), followed by a per-run suffix (`-${{ github.sha }}` or `-${{ github.run_id }}`) on saving steps so `actions/cache` writes updated incremental outputs on every commit without false-failing `bare_cache_finding`. `restore-keys` lists the input prefix (`<prefix>-<toolchain>-<lockfile>-`) before the gate fallback (`<prefix>-<gate>-`), restoring the newest matching near hit across commits. Workflow steps using `actions/cache/restore` alone are exempt from the per-run suffix check because they never save. Product source is never hashed into any CI cache key.

## Refutation criterion

This decision is wrong, and must be revised, if any of these is observed:

- A moved or shared build cache returns a stale build output after source, lockfile, or toolchain inputs change, causing a gate to pass on broken code.
- A project with a whole-tree build gate has a slow unnarrowed bite recorded in `.ratchet/timing.jsonl` and `route.py` prints no proposal naming the gate, bite, time, and cure.
- `conformance.py` rejects an existing binding without `- Build:` or an `actions/cache/restore` step keyed by toolchain version, lockfile, and gate.

## Considered options

- **Hash product source in CI or plugin verdict caches (#301 style).** Rejected. A plugin-computed input digest misses implicit build inputs (environment variables, code generation, tool internals) and creates stale greens. Reusing the build tool's own incremental cache leaves staleness detection to the compiler or build tool.
- **Require `- Build:` on every project.** Rejected. Interpreted or pure-script repositories without a build step need no build cache specification; keeping `- Build:` optional with a `<...>` placeholder avoids upgrade churn and false conformance failures.
- **Turn slow unnarrowed bites red in `conformance.py`.** Rejected for the reasons in ADR 0078: a single measured time varies under host load. Surfacing the slow bite in `route.py` makes it visible and actionable without load-induced merge flakes.

## Consequences

- `skills/adopt/templates/docs/process/binding.md` gains an optional `- Build:` line under `## Speed`.
- `scripts/build_cache.py` provides frozen domain types (`BuildSpec`, `ReusePolicy`, `ReuseKind`), pure parsing (`parse_build_spec`, `parse_build_line`), and validation (`validate_build_spec`).
- `scripts/conformance.py` validates filled `- Build:` declarations and exempts `actions/cache/restore` steps from `bare_cache_finding`.
- `scripts/project_bite.py` exposes `SlowBite` and `recorded_slow_bites`, and `scripts/route.py` surfaces slow unnarrowed bites as actionable `also:` proposals.
- `skills/adopt/SKILL.md`, `skills/safeguard/SKILL.md`, and `skills/method/tiers.md` document cold and warm build measurement, moved-cache checks, narrowed build bites, and input-keyed CI build caches.

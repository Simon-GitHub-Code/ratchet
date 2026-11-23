---
status: accepted
---
# Pre-merge Tier-2 budget projection and sharding warning

Builds on ADR 0020 ([`0020-every-bound-gate-is-shown-to-bite-by-a-fault-the-repository-keeps.md`](0020-every-bound-gate-is-shown-to-bite-by-a-fault-the-repository-keeps.md)), ADR 0078 ([`0078-a-slow-fault-whose-bite-passes-no-args-is-named-from-the-run-and-never-red.md`](0078-a-slow-fault-whose-bite-passes-no-args-is-named-from-the-run-and-never-red.md)), and ADR 0090 ([`0090-ci-runs-tier-1-as-a-few-jobs.md`](0090-ci-runs-tier-1-as-a-few-jobs.md)).

Tier-2 gates (such as `bite-all`, full `mutation`, and `e2e`) run only after merge on the default branch. As a codebase grows, a Tier-2 gate can creep past its time ceiling (for example, an unsharded `bite-all` growing from 10 minutes to 44 minutes), breaking the default branch post-merge with no pre-merge warning.

## Decision

**1. Ship `scripts/tier2_projection.py` to project bound Tier-2 gate runtimes pre-merge.**

- Read bound Tier-2 gate names from `docs/process/binding.md` (`row.tier == "2"` where the row names a tool or shipped gate rather than `none:` or `later:`).
- Resolve each gate's effective ceiling from `quality/thresholds.toml` via `[ceilings.seconds].<gate>`, falling back to `[ceilings.seconds].tier2` (skipping gates with no effective ceiling).
- For normal Tier-2 gates, project runtime as the median of up to the last 5 recorded green runs (`exit == 0`) in `.ratchet/timing.jsonl` (ignoring missing files, corrupt lines, and non-green runs).
- For `bite-all`, project the maximum of (a) the recent median of recorded `bite-all` green runs and (b) the sum of recorded per-fault durations (`runrecord.facts(timing).faults`) divided by concurrency (`min(MAX_LANES, fault_count)`) and divided by the `- Shards:` count from `docs/process/binding.md` (`## Speed`, default `1`) when `bite-all` is listed in `tier2_sharded` in `justfile`.

**2. Warn at 80% ceiling share with a concrete sharding recommendation.**

- Whenever a Tier-2 gate's projected runtime reaches `>= 80%` (`WARN_SHARE = 0.80`) of its effective ceiling, emit a structured `Projection` warning sorted by highest ceiling share descending.
- Compute `recommended_shards = max(current_shards + 1, math.ceil(unsharded_seconds / (TARGET_SHARE * ceiling)))` using `TARGET_SHARE = 0.60`, where `current_shards` is `- Shards:` when the gate is already in `tier2_sharded` and `1` otherwise, and `unsharded_seconds = projected * current_shards`.
- State concretely whether to add the gate to `tier2_sharded` in `justfile` or raise `- Shards:` in `docs/process/binding.md`.

**3. Expose library and CLI entry points.**

- Expose `projections(root: Path) -> list[Projection]` and `proposals(root: Path) -> list[str]` for route integration.
- Provide `scripts/tier2_projection.py [--root DIR] [--json] [--strict]`, exiting `0` in advisory mode by default and `1` under `--strict` when any projection warning fires.

## Refutation criterion

This decision is wrong, and must be revised, if any of these is observed:

- A bound Tier-2 gate has recent green runtimes or recorded per-fault durations whose projection is at or above `80%` of its effective ceiling, and `scripts/tier2_projection.py --strict` exits `0` or omits its sharding recommendation.
- A Tier-2 gate projected below `80%` of its effective ceiling causes `scripts/tier2_projection.py --strict` to exit `1` or emit a warning.
- Missing `.ratchet/timing.jsonl`, missing `docs/process/binding.md`, missing `quality/thresholds.toml`, corrupt JSON lines, or non-green (`exit != 0`) records cause `scripts/tier2_projection.py` to raise an unhandled error or inflate a projection.
- Adding `bite-all` to `tier2_sharded` with `- Shards: N` (`N > 1`) does not divide the fault-sum projection by `N`, or `recommended_shards` fails to bring `unsharded_seconds / recommended_shards` at or below `60%` of the ceiling.

## Considered options

- **Warn only after a post-merge Tier-2 run times out or goes red.** Rejected: post-merge failures already block the default branch and stall subsequent merges; pre-merge projection from existing timing and fault facts warns before a ceiling breach lands.
- **Run full Tier-2 gates on every pull request.** Rejected: Tier-2 gates exist because their full runtimes exceed pull-request budgets.
- **Project `bite-all` from `bite-all` green runs alone without per-fault summation.** Rejected: new faults added across pull requests accumulate per-fault durations in `.ratchet/timing.jsonl` before the next post-merge `bite-all` run finishes.

## Consequences

- `scripts/tier2_projection.py` provides `Projection`, `projections`, `proposals`, and the `--root`, `--json`, and `--strict` CLI flags using only the standard library and existing plugin helpers.
- `scripts/bite.py` gains a scenario proving `tier2_projection.py --strict` exits `0` under `80%` and `1` at or above `80%`.
- Downstream route tooling can call `tier2_projection.proposals(root)` to surface sharding recommendations ahead of post-merge Tier-2 failures.

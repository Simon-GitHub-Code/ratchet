---
status: accepted
---
# A default-branch push skips CI only when a PR run settled every gate

Settles issue #297. Builds on ADR 0080 (point 3: an unchanged tree does not run `check` again when an earlier green run covered it) and ADR 0024 (`scripts/forge_runs.py` reads the forge's workflow runs).

**What was asked.** When `merge.yml` merges a pull request onto the default branch as a merge commit whose tree equals the pull request head's tree (`HEAD^{tree} == HEAD^2^{tree}`), the push workflow `ci.yml` ran every tier 0 and tier 1 gate again on the exact tree and base commit that the pull request run had already passed. Issue #297 asks when a default-branch push may skip `ci.yml` without loosening any gate.

## Decision

**1. A default-branch push skips `tier0`, `shape`, `tier1` and `tier1-long` only when `scripts/already_proven.py` proves six invariants hold.** On a `push` event, the `proven` job (with `permissions: contents: read` and `actions: read`) runs `already_proven.py select` and `already_proven.py judge`. `tier0` and `shape` depend on `proven` and run unless `needs.proven.outputs.skip == 'true'`. Because `tier1` and `tier1-long` depend on `shape`, skipping `shape` skips every tier 1 job.

**2. Which gates an identical tree settles.** Deterministic tier 0 and tier 1 gates whose inputs are the commit tree and the base commit (`HEAD^1`) are settled by the pull request run when `HEAD^{tree} == HEAD^2^{tree}` and `HEAD^1` matches the recorded base. Gates that read network state (dependency advisories such as `audit`, and external scanner rule feeds) depend on time as well as the tree, so their result is settled only inside the age window below.

**3. The six invariants.**

1. **Same tree and same base `HEAD^1`.** On `pull_request`, `tier0` runs `already_proven.py record` and uploads `.ratchet/proven.json` (`schema: 1`, `base: HEAD^1`, `head: HEAD^2`, `tree: HEAD^{tree}`). On `push`, both `select` and `judge` require `HEAD` to have two parents (`HEAD^1`, `HEAD^2`) with `HEAD^{tree} == HEAD^2^{tree}`, and `judge` requires `proven.json` to match `HEAD^1`, `HEAD^2` and `HEAD^{tree}` exactly. A moved default branch (`HEAD^1 != proven.base`), a rebase, or a squash merge runs every gate.
2. **Time ceilings checked from timing artifacts.** Pull request runs pass `--over report`, so a slow gate or tier does not turn a pull request run red, whereas default-branch pushes pass `--over block`. `judge` reads every `timing-*.jsonl` artifact, requires valid JSON objects with `exit == 0`, `over` not true, `status` not `red` or `failed`, re-checks `budget.held` against `quality/thresholds.toml` (holding standalone gates and group members to `tier1` and group job wall clock to its own key per ADR 0090), and confirms a passing record for `tier0` and every expected `tier1` and `tier1_long` shard from `ci_jobs`.
3. **Age limit for network-reading gates.** Both `select` and `judge` take the earlier of `created_at` and `run_started_at` (so queue delay cannot stretch the window) and require `-60 <= now - timestamp <= max_age` (default 3600 seconds, one hour). Older runs fall back to running everything.
4. **Re-runs never skip.** Both `select` and `judge` require `attempt == 1`. Manually re-running a default-branch `ci.yml` workflow (`GITHUB_RUN_ATTEMPT > 1`) always runs `tier0`, `shape`, `tier1` and `tier1-long`.
5. **Every error falls back to running everything.** Missing artifacts, GitHub API failures, corrupt JSON or JSONL, unreadable thresholds, invalid `justfile` job lists, or unexpected exceptions leave `skip=false` and exit `0` so the `proven` job never blocks CI and `tier0`/`shape` run in full.
6. **Skipped push runs count as `green` for `forge_runs.py tier1`.** When `proven` outputs `skip=true`, `tier0`, `shape`, `tier1` and `tier1-long` are skipped and GitHub Actions marks the `ci.yml` push run `completed` with conclusion `success`. `forge_runs.py tier1` (ADR 0024, ADR 0069) reads that conclusion as `green` on the default branch.

## Refutation criterion

This decision is wrong, and the skip must be disabled or tightened, if any of these is observed:

- A default-branch push skips `ci.yml` when `HEAD^1` differs from the base commit the pull request run tested, or when `HEAD^{tree}` differs from the tree the pull request run tested.
- A default-branch push skips `ci.yml` after a pull request run whose `budget.py` timing exceeded a ceiling in `quality/thresholds.toml`, whose timing artifact is missing a gate or shard, or whose run started more than `max_age` seconds earlier.
- A default-branch `ci.yml` workflow fails because `proven` exited non-zero on a transient `gh` or artifact error instead of falling back to `skip=false`.

## Considered options

- **Always run `ci.yml` on every default-branch push.** Rejected. It re-runs identical deterministic checks and setup minutes on the exact tree and base that the pull request run passed moments earlier.
- **Skip using GitHub's `head_sha` or `pull_requests` metadata alone without `proven.json` and timing artifacts.** Rejected. `head_sha` does not identify the tested base `HEAD^1` or tree `HEAD^{tree}`, and `--over report` on pull requests hides ceiling failures from the run conclusion.
- **Keep `actions: read` at the workflow top level.** Rejected. Granting `actions: read` only to the `proven` job keeps `tier0`, `shape`, `tier1` and `tier1-long` at `contents: read`.

## Consequences

- Clean fast-forward merge commits that land within an hour of a green, within-ceiling pull request run skip `tier0`, `shape`, `tier1` and `tier1-long` on `push`.
- Projects using this template upload one extra artifact (`proven`, containing `.ratchet/proven.json`) on `pull_request` `tier0` runs.
- Any error in `proven` falls back to a full run (`skip=false`) with zero extra risk of a false green.
